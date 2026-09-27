"""Run the deterministic C0 pace-policy experiment.

The fixture changes only ``pace`` for the four primary scenarios. Results are
rule-engine observations over Mock data, not measurements of human comfort.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from agents.activity_agent import ActivityAgent
from agents.weather_agent import WeatherAgent
from models.schemas import (
    Activity,
    ActivityAttributeStatus,
    ActivityEnvironment,
    ActivityIntensity,
    ActivityPlanningMode,
    ActivitySearchRequest,
    DataSource,
    TravelPace,
    UserPreferences,
    WeatherScenario,
    WeatherSensitivity,
)
from orchestrator.pipeline import TravelPlanningPipeline
from tools.weather_provider import MockWeatherProvider


ROOT = Path(__file__).resolve().parent
DEFAULT_SCENARIOS = ROOT / "pace_planning_scenarios.json"
DEFAULT_OUTPUT = ROOT / "results" / "pace_planning_results.json"


class FixedPaceActivityProvider:
    """Independent fixed fixture; returned records are always deep copies."""

    def __init__(self, source: dict[str, Any], variant: str = "standard") -> None:
        self.source_version = source["activity_dataset_version"]
        templates = source["candidate_templates"]
        if variant == "missing_evening":
            templates = [item for item in templates if item["time_slot"] != "evening"]
        elif variant == "high_only":
            templates = [item for item in templates if item["intensity"] == "high"]
        self.templates = [dict(item) for item in templates]
        self.unknown_only = variant == "unknown_only"

    async def search(self, request: ActivitySearchRequest) -> list[Activity]:
        candidates: list[Activity] = []
        for item in self.templates:
            intensity = (
                ActivityIntensity.UNKNOWN
                if self.unknown_only
                else ActivityIntensity(item["intensity"])
            )
            status = (
                ActivityAttributeStatus.UNKNOWN
                if self.unknown_only
                else ActivityAttributeStatus.AVAILABLE
            )
            candidates.append(Activity(
                candidate_id=item["candidate_id"],
                source=DataSource.MOCK,
                source_version=self.source_version,
                name=item["name"],
                category="历史",
                location=request.city,
                available_start_date=request.start_date,
                available_end_date=request.end_date,
                duration_hours=item["duration_hours"],
                duration_status=status,
                intensity=intensity,
                intensity_status=status,
                pace_attribute_source=(None if self.unknown_only else DataSource.MOCK),
                price=item["price"],
                rating=item["rating"],
                description="C0 固定 Mock 候选；时长和强度不是实测数据。",
                time_slot=item["time_slot"],
                environment=ActivityEnvironment(item["environment"]),
                weather_sensitivity=WeatherSensitivity(item["weather_sensitivity"]),
            ))
        return [item.model_copy(deep=True) for item in candidates]


def _preferences(source: dict[str, Any], scenario: dict[str, Any]) -> UserPreferences:
    payload = dict(source["base_preferences"])
    payload["budget"] = scenario["budget"]
    payload["pace"] = scenario["pace"]
    return UserPreferences.model_validate(payload)


def _matches_interest(activity: Activity, interests: list[str]) -> bool:
    haystack = f"{activity.name} {activity.category} {activity.description}".lower()
    return any(item.lower() in haystack for item in interests)


async def _run_one(source: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any]:
    preferences = _preferences(source, scenario)
    activity_agent = ActivityAgent(
        provider=FixedPaceActivityProvider(source, scenario["fixture_variant"]),
        require_weather_context=True,
        planning_mode=ActivityPlanningMode.WEATHER_AWARE,
    )
    pipeline = TravelPlanningPipeline(
        weather_agent=WeatherAgent(
            provider=MockWeatherProvider(source["weather_source_version"]),
            scenario=WeatherScenario(scenario["weather"]),
        ),
        activity_agent=activity_agent,
    )
    state = await pipeline.run(preferences)
    result = state.activity_result
    activities = [
        activity
        for day in (result.day_plans if result else [])
        for activity in day.activities
    ]
    rests = [
        rest
        for day in (result.day_plans if result else [])
        for rest in day.rest_slots
    ]
    known_intensities = [
        activity for activity in activities
        if activity.intensity_status == ActivityAttributeStatus.AVAILABLE
    ]
    weather_evaluable = [
        activity for activity in activities if activity.weather_compatible is not None
    ]
    snapshot_prices = {
        item.candidate_id: item.price
        for item in (state.candidate_snapshot.activities if state.candidate_snapshot else [])
    }
    expected_prices = {
        item["candidate_id"]: item["price"]
        for item in source["candidate_templates"]
        if scenario["fixture_variant"] != "missing_evening"
        or item["time_slot"] != "evening"
        if scenario["fixture_variant"] != "high_only"
        or item["intensity"] == "high"
    }
    breakdown = state.budget_breakdown
    return {
        "id": scenario["id"],
        "pace": scenario["pace"],
        "weather": scenario["weather"],
        "fixture_variant": scenario["fixture_variant"],
        "state": state.state.value,
        "selected_candidate_ids": [item.candidate_id for item in activities],
        "daily_activity_counts": [
            len(day.activities) for day in (result.day_plans if result else [])
        ],
        "activity_count": len(activities),
        "rest_count": len(rests),
        "rest_slots": [item.time_slot for item in rests],
        "known_high_intensity_count": sum(
            item.intensity == ActivityIntensity.HIGH for item in known_intensities
        ),
        "known_intensity_count": len(known_intensities),
        "interest_match_count": sum(
            _matches_interest(item, preferences.interests) for item in activities
        ),
        "weather_hard_pass_count": sum(
            item.weather_compatible is True for item in weather_evaluable
        ),
        "weather_evaluable_count": len(weather_evaluable),
        "activity_cost": breakdown.activity_cost if breakdown else None,
        "total_cost": breakdown.total_cost if breakdown else None,
        "budget": preferences.budget,
        "adjustment_round": state.adjustment_round,
        "adjustment_history": [
            item.model_dump(mode="json") for item in state.adjustment_history
        ],
        "constraint_issues": [
            item.model_dump(mode="json")
            for item in (result.constraint_issues if result else [])
        ],
        "candidate_signature": sorted(
            [candidate_id, price] for candidate_id, price in snapshot_prices.items()
        ),
        "candidate_prices_unchanged": snapshot_prices == expected_prices,
        "pace_policy_version": result.pace_policy_version if result else "",
    }


def _aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    core = {
        item["pace"] or "unspecified": item
        for item in results
        if item["id"] in {
            "legacy_unspecified_sunny",
            "relaxed_sunny",
            "balanced_sunny",
            "packed_sunny",
        }
    }
    by_pace: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in results:
        by_pace[item["pace"] or "unspecified"].append(item)
    pace_summary = {}
    for pace, items in sorted(by_pace.items()):
        activities = sum(item["activity_count"] for item in items)
        known = sum(item["known_intensity_count"] for item in items)
        pace_summary[pace] = {
            "scenario_count": len(items),
            "average_daily_activity_count": round(
                sum(sum(item["daily_activity_counts"]) for item in items)
                / max(sum(len(item["daily_activity_counts"]) for item in items), 1),
                4,
            ),
            "known_high_intensity_rate": round(
                sum(item["known_high_intensity_count"] for item in items)
                / max(known, 1),
                4,
            ),
            "interest_match_rate": round(
                sum(item["interest_match_count"] for item in items)
                / max(activities, 1),
                4,
            ),
            "weather_hard_pass_rate": round(
                sum(item["weather_hard_pass_count"] for item in items)
                / max(sum(item["weather_evaluable_count"] for item in items), 1),
                4,
            ),
            "completed_count": sum(item["state"] == "completed" for item in items),
            "budget_infeasible_count": sum(
                item["state"] == "budget_infeasible" for item in items
            ),
            "pace_infeasible_count": sum(
                item["state"] == "pace_constraints_unsatisfied" for item in items
            ),
        }
    signatures = [item["candidate_signature"] for item in core.values()]
    return {
        "core_only_variable_is_pace": len(signatures) == 4
        and all(signature == signatures[0] for signature in signatures[1:]),
        "core_results": {
            pace: {
                "daily_activity_counts": item["daily_activity_counts"],
                "rest_count": item["rest_count"],
                "known_high_intensity_count": item["known_high_intensity_count"],
                "activity_cost": item["activity_cost"],
                "total_cost": item["total_cost"],
                "state": item["state"],
            }
            for pace, item in core.items()
        },
        "by_pace": pace_summary,
    }


async def _run(source: dict[str, Any]) -> list[dict[str, Any]]:
    return [await _run_one(source, scenario) for scenario in source["scenarios"]]


def run(scenario_path: Path, output_path: Path) -> dict[str, Any]:
    source = json.loads(scenario_path.read_text(encoding="utf-8"))
    results = asyncio.run(_run(source))
    report = {
        "experiment": "stage-c0-pace-planning",
        "experiment_version": source["experiment_version"],
        "activity_dataset_version": source["activity_dataset_version"],
        "weather_source_version": source["weather_source_version"],
        "scenario_count": len(results),
        "scenario_results": results,
        "metrics": _aggregate(results),
        "limitations": source["limitations"],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = run(args.scenarios, args.output)
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
