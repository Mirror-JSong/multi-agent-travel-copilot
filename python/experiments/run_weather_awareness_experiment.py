"""Reproducible B3 control/treatment experiment for weather-aware planning."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from agents.activity_agent import ActivityAgent
from agents.base_agent import BaseAgent
from agents.flight_agent import FlightAgent
from agents.hotel_agent import HotelAgent
from agents.weather_agent import WeatherAgent
from agents.weather_compatibility import WeatherCompatibilityEvaluator
from models.schemas import (
    Activity,
    ActivityEnvironment,
    ActivityPlanningMode,
    ActivitySearchRequest,
    DailyWeather,
    DataSource,
    Destination,
    DestinationRecommendation,
    Flight,
    Hotel,
    PlanningState,
    TravelPlanState,
    UserPreferences,
    WeatherAvailability,
    WeatherCondition,
    WeatherResult,
    WeatherResultStatus,
    WeatherScenario,
    WeatherSearchRequest,
    WeatherSensitivity,
)
from orchestrator.pipeline import TravelPlanningPipeline
from tools.activity_search import MockActivityProvider
from tools.deterministic import stable_candidate_id


ROOT = Path(__file__).resolve().parent
DEFAULT_SCENARIOS = ROOT / "weather_awareness_scenarios.json"
DEFAULT_OUTPUT = ROOT / "results" / "weather_awareness_results.json"


class FixedDestinationAgent(BaseAgent):
    name = "DestinationAgent"
    output_fields = ("destination_rec",)

    def __init__(self, city: str) -> None:
        super().__init__()
        self.city = city

    async def execute(self, state: TravelPlanState) -> TravelPlanState:
        country = {"东京": "日本", "曼谷": "泰国", "首尔": "韩国"}.get(
            self.city, "Fixture"
        )
        destination = Destination(
            city=self.city,
            country=country,
            description="B3 固定实验目的地",
            highlights=["固定实验候选"],
        )
        state.destination_rec = DestinationRecommendation(
            destinations=[destination],
            selected=destination,
            reasoning="B3 控制变量实验固定目的地，不执行目的地比较。",
        )
        return state


class FixedWeatherProvider:
    def __init__(self, profile: dict[str, Any], source_version: str) -> None:
        self.profile = profile
        self.source_version = source_version

    async def search(self, request: WeatherSearchRequest) -> WeatherResult:
        start = date.fromisoformat(request.start_date)
        end = date.fromisoformat(request.end_date)
        dates = [
            start + timedelta(days=offset)
            for offset in range((end - start).days)
        ]
        if self.profile.get("unavailable"):
            daily = [DailyWeather(
                date=item.isoformat(),
                availability_status=WeatherAvailability.UNAVAILABLE,
            ) for item in dates]
            status = WeatherResultStatus.UNAVAILABLE
            note = "B3 固定 Mock 情景：天气不可用，未伪造晴天。"
        else:
            definitions = self.profile.get("days")
            if definitions is None:
                definitions = [self.profile["repeat"] for _ in dates]
            if len(definitions) != len(dates):
                raise ValueError("weather profile length must equal activity date count")
            daily = [DailyWeather(
                date=item.isoformat(),
                weather_condition=WeatherCondition(definition["condition"]),
                rain_probability=definition["rain_probability"],
                temperature=definition["temperature"],
                severe_weather=definition["severe"],
                availability_status=WeatherAvailability.AVAILABLE,
            ) for item, definition in zip(dates, definitions)]
            status = WeatherResultStatus.AVAILABLE
            note = "B3 固定 Mock 天气情景，不是实时天气预报。"
        return WeatherResult(
            destination=request.destination,
            start_date=request.start_date,
            end_date=request.end_date,
            scenario=WeatherScenario.MIXED,
            daily_weather=daily,
            source=DataSource.MOCK,
            source_version=self.source_version,
            is_mock=True,
            availability_status=status,
            availability_note=note,
        )


class SlowWeatherProvider:
    """Controlled timeout fixture for the opt-in service/UI demonstration."""

    async def search(self, request: WeatherSearchRequest) -> WeatherResult:
        await asyncio.sleep(0.05)
        raise AssertionError("slow weather fixture should have timed out")


class FixedFlightProvider:
    def __init__(self, version: str) -> None:
        self.version = version

    async def search(self, request) -> list[Flight]:
        common = {
            "source": DataSource.MOCK,
            "source_version": self.version,
            "airline": "B3 Fixture Air",
            "departure_city": request.departure_city,
            "arrival_city": request.arrival_city,
            "cabin_class": request.cabin_class,
        }
        identity = request.model_dump(mode="json")
        return [
            Flight(
                candidate_id=stable_candidate_id(
                    "weather-experiment-flight-premium", identity, self.version
                ),
                flight_no="B3P",
                departure_time=f"{request.travel_date}T08:00:00",
                arrival_time=f"{request.travel_date}T09:00:00",
                price=500,
                duration_hours=1,
                stops=0,
                **common,
            ),
            Flight(
                candidate_id=stable_candidate_id(
                    "weather-experiment-flight-economy", identity, self.version
                ),
                flight_no="B3E",
                departure_time=f"{request.travel_date}T08:00:00",
                arrival_time=f"{request.travel_date}T20:00:00",
                price=300,
                duration_hours=12,
                stops=2,
                **common,
            ),
        ]


class FailingFlightProvider:
    """Controlled failure used only by the opt-in B3 demonstration endpoint."""

    async def search(self, request) -> list[Flight]:
        raise RuntimeError("B3 controlled demo flight provider failure")


class FixedHotelProvider:
    def __init__(self, version: str) -> None:
        self.version = version

    async def search(self, request) -> list[Hotel]:
        common = {
            "source": DataSource.MOCK,
            "source_version": self.version,
            "city": request.city,
            "check_in": request.check_in,
            "check_out": request.check_out,
        }
        identity = request.model_dump(mode="json")
        return [
            Hotel(
                candidate_id=stable_candidate_id(
                    "weather-experiment-hotel-premium", identity, self.version
                ),
                name="B3 Central Hotel",
                star_rating=3.5,
                user_rating=9.5,
                price_per_night=500,
                distance_to_center_km=0.1,
                **common,
            ),
            Hotel(
                candidate_id=stable_candidate_id(
                    "weather-experiment-hotel-economy", identity, self.version
                ),
                name="B3 Economy Hotel",
                star_rating=2.5,
                user_rating=7.5,
                price_per_night=200,
                distance_to_center_km=5,
                **common,
            ),
        ]


class FixedActivityProvider:
    def __init__(
        self,
        definitions: list[dict[str, Any]],
        version: str,
    ) -> None:
        self.definitions = definitions
        self.version = version

    async def search(self, request: ActivitySearchRequest) -> list[Activity]:
        return [Activity(
            candidate_id=definition["id"],
            source=DataSource.MOCK,
            source_version=self.version,
            name=definition["name"],
            category="history",
            location=request.city,
            available_start_date=request.start_date,
            available_end_date=request.end_date,
            duration_hours=2,
            price=float(definition["price"]),
            rating=float(definition["rating"]),
            description=f"history {definition['name']}",
            time_slot=definition["slot"],
            environment=ActivityEnvironment(definition["environment"]),
            weather_sensitivity=WeatherSensitivity(definition["sensitivity"]),
        ) for definition in self.definitions]


def _preferences(scenario: dict[str, Any]) -> UserPreferences:
    return UserPreferences(
        budget=float(scenario["budget"]),
        departure_city="上海",
        start_date=scenario["start_date"],
        end_date=scenario["end_date"],
        num_travelers=scenario["travelers"],
        interests=scenario["interests"],
    )


def _activity_provider(source: dict, scenario: dict):
    version = source["activity_dataset_version"]
    fixture_name = scenario["activity_fixture"]
    if fixture_name == "provider":
        return MockActivityProvider(version)
    return FixedActivityProvider(
        source["activity_fixtures"][fixture_name],
        version,
    )


async def _run_group(
    source: dict,
    scenario: dict,
    mode: ActivityPlanningMode,
    *,
    inject_agent_failure: bool = False,
    inject_weather_timeout: bool = False,
) -> TravelPlanState:
    activity_agent = ActivityAgent(
        _activity_provider(source, scenario),
        require_weather_context=True,
        planning_mode=mode,
    )
    pipeline = TravelPlanningPipeline(
        flight_agent=FlightAgent(
            FailingFlightProvider()
            if inject_agent_failure
            else FixedFlightProvider(source["business_fixture_version"])
        ),
        hotel_agent=HotelAgent(FixedHotelProvider(
            source["business_fixture_version"]
        )),
        weather_agent=WeatherAgent(
            SlowWeatherProvider()
            if inject_weather_timeout
            else FixedWeatherProvider(
                source["weather_profiles"][scenario["weather_profile"]],
                source["weather_source_version"],
            ),
            timeout_seconds=0.001 if inject_weather_timeout else None,
        ),
        activity_agent=activity_agent,
    )
    pipeline.destination_agent = FixedDestinationAgent(scenario["destination"])
    return await pipeline.run(_preferences(scenario))


def load_scenario_source(
    scenario_path: Path = DEFAULT_SCENARIOS,
) -> dict[str, Any]:
    """Load the fixed B3 fixture without mutating the shared source object."""
    return json.loads(scenario_path.read_text(encoding="utf-8"))


async def run_controlled_plan(
    scenario_id: str,
    mode: ActivityPlanningMode = ActivityPlanningMode.WEATHER_AWARE,
    *,
    inject_agent_failure: bool = False,
    inject_weather_timeout: bool = False,
    scenario_path: Path = DEFAULT_SCENARIOS,
) -> TravelPlanState:
    """Run one isolated, deterministic B3 scenario for tests or demos.

    This entry point deliberately uses the same Pipeline and business agents as
    the product.  Only fixed Mock providers and the explicit planning mode are
    injected.  It is not a public real-weather API.
    """
    source = load_scenario_source(scenario_path)
    scenario = next(
        (item for item in source["scenarios"] if item["id"] == scenario_id),
        None,
    )
    if scenario is None:
        raise KeyError(f"unknown B3 weather scenario: {scenario_id}")
    return await _run_group(
        source,
        scenario,
        mode,
        inject_agent_failure=inject_agent_failure,
        inject_weather_timeout=inject_weather_timeout,
    )


def _candidate_signature(state: TravelPlanState) -> dict[str, Any]:
    return {
        "outbound": [
            {"candidate_id": item.candidate_id, "price": item.price}
            for item in state.flight_result.outbound_flights
        ],
        "return": [
            {"candidate_id": item.candidate_id, "price": item.price}
            for item in state.flight_result.return_flights
        ],
        "hotels": [
            {
                "candidate_id": item.candidate_id,
                "price_per_night": item.price_per_night,
            }
            for item in state.hotel_result.hotels
        ],
        "activities": [
            {"candidate_id": item.candidate_id, "price": item.price}
            for item in state.activity_result.activity_candidates
        ],
    }


def _matches_interest(activity: Activity, interests: list[str]) -> bool:
    if not interests:
        return True
    haystack = f"{activity.name} {activity.category} {activity.description}".lower()
    return any(item.strip().lower() in haystack for item in interests if item.strip())


def _selected(state: TravelPlanState) -> list[tuple[str, Activity]]:
    if state.activity_result is None:
        return []
    return [
        (day.date, activity)
        for day in state.activity_result.day_plans
        for activity in day.activities
    ]


def _group_measurement(state: TravelPlanState) -> dict[str, Any]:
    evaluator = WeatherCompatibilityEvaluator()
    weather_by_date = {
        item.date: item
        for item in (state.weather_result.daily_weather if state.weather_result else [])
    }
    selected = _selected(state)
    audits = [
        evaluator.evaluate(activity, weather_by_date.get(day), activity_date=day)
        for day, activity in selected
    ]
    evaluable = [item for item in audits if item.data_sufficient]
    conflicts = [item for item in evaluable if not item.hard_constraint_satisfied]
    interests = state.preferences.interests
    interest_matches = sum(
        _matches_interest(activity, interests) for _, activity in selected
    )
    required_slots = (
        date.fromisoformat(state.preferences.end_date)
        - date.fromisoformat(state.preferences.start_date)
    ).days * 3
    operational_complete = len(selected) == required_slots
    verified_feasible = (
        operational_complete
        and len(evaluable) == required_slots
        and not conflicts
    )
    breakdown = state.budget_breakdown
    budget_compliant = bool(
        state.state == PlanningState.COMPLETED
        and breakdown is not None
        and breakdown.is_within_budget
        and operational_complete
    )
    return {
        "selected_count": len(selected),
        "evaluable_count": len(evaluable),
        "weather_conflict_count": len(conflicts),
        "unknown_weather_count": len(audits) - len(evaluable),
        "interest_match_count": interest_matches,
        "interest_sample_count": len(selected),
        "operational_complete": operational_complete,
        "verified_weather_feasible": verified_feasible,
        "budget_compliant": budget_compliant,
    }


def _state_result(state: TravelPlanState) -> dict[str, Any]:
    activity = state.activity_result
    breakdown = state.budget_breakdown
    return {
        "state": state.state.value,
        "weather": state.weather_result.model_dump(mode="json") if state.weather_result else None,
        "weather_planning": state.weather_planning.model_dump(mode="json"),
        "candidate_signature": _candidate_signature(state),
        "selected_activities": [
            {
                "date": day,
                "candidate_id": item.candidate_id,
                "name": item.name,
                "time_slot": item.time_slot,
                "price": item.price,
                "weather_compatible": item.weather_compatible,
            }
            for day, item in _selected(state)
        ],
        "weather_decisions": (
            [item.model_dump(mode="json") for item in activity.weather_decisions]
            if activity else []
        ),
        "constraint_issues": (
            [item.model_dump(mode="json") for item in activity.constraint_issues]
            if activity else []
        ),
        "initial_total_cost": state.initial_total_cost,
        "final_total_cost": state.final_total_cost,
        "costs": (
            breakdown.model_dump(mode="json") if breakdown else None
        ),
        "adjustment_history": [
            item.model_dump(mode="json") for item in state.adjustment_history
        ],
        "measurement": _group_measurement(state),
    }


async def _run_scenario(source: dict, scenario: dict) -> dict[str, Any]:
    control, treatment = await asyncio.gather(
        _run_group(source, scenario, ActivityPlanningMode.BASELINE),
        _run_group(source, scenario, ActivityPlanningMode.WEATHER_AWARE),
    )
    if control.weather_result != treatment.weather_result:
        raise AssertionError(f"{scenario['id']}: paired weather differs")
    if _candidate_signature(control) != _candidate_signature(treatment):
        raise AssertionError(f"{scenario['id']}: paired candidates differ")
    if control.state.value != scenario["expected_control"]:
        raise AssertionError(
            f"{scenario['id']}: control state {control.state.value} != expected"
        )
    if treatment.state.value != scenario["expected_treatment"]:
        raise AssertionError(
            f"{scenario['id']}: treatment state {treatment.state.value} != expected"
        )
    return {
        "id": scenario["id"],
        "destination": scenario["destination"],
        "weather_profile": scenario["weather_profile"],
        "activity_fixture": scenario["activity_fixture"],
        "preferences": _preferences(scenario).model_dump(mode="json", exclude_none=True),
        "paired_invariants": {
            "weather_equal": True,
            "candidate_sets_equal": True,
            "activity_dataset_version": source["activity_dataset_version"],
            "weather_source_version": source["weather_source_version"],
            "business_fixture_version": source["business_fixture_version"],
        },
        "control": _state_result(control),
        "treatment": _state_result(treatment),
    }


def _aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for group in ("control", "treatment"):
        measurements = [item[group]["measurement"] for item in results]
        evaluable = sum(item["evaluable_count"] for item in measurements)
        conflicts = sum(item["weather_conflict_count"] for item in measurements)
        interest_samples = sum(item["interest_sample_count"] for item in measurements)
        interest_matches = sum(item["interest_match_count"] for item in measurements)
        compliant = sum(item["budget_compliant"] for item in measurements)
        verified = sum(item["verified_weather_feasible"] for item in measurements)
        operational = sum(item["operational_complete"] for item in measurements)
        metrics.setdefault("M1_weather_conflict_rate", {})[group] = {
            "conflicts": conflicts,
            "evaluable_activities": evaluable,
            "unknown_or_missing_activities": sum(
                item["unknown_weather_count"] for item in measurements
            ),
            "rate": conflicts / evaluable if evaluable else None,
        }
        metrics.setdefault("M2_interest_match_rate", {})[group] = {
            "matches": interest_matches,
            "selected_activities": interest_samples,
            "rate": interest_matches / interest_samples if interest_samples else None,
        }
        states = [item[group]["state"] for item in results]
        metrics.setdefault("M3_budget_compliance_rate", {})[group] = {
            "compliant": compliant,
            "all_scenarios": len(results),
            "rate": compliant / len(results),
            "budget_infeasible": states.count(PlanningState.BUDGET_INFEASIBLE.value),
            "activity_constraints_unsatisfied": states.count(
                PlanningState.ACTIVITY_CONSTRAINTS_UNSATISFIED.value
            ),
            "failed": states.count(PlanningState.FAILED.value),
        }
        metrics.setdefault("M4_plan_feasibility_rate", {})[group] = {
            "verified_weather_feasible": verified,
            "operationally_complete": operational,
            "all_scenarios": len(results),
            "verified_rate": verified / len(results),
            "operational_rate": operational / len(results),
        }

    changed = [
        decision
        for item in results
        for decision in item["treatment"]["weather_decisions"]
        if decision["weather_driven_change"]
    ]
    explained = [
        decision for decision in changed
        if decision["baseline_candidate_id"]
        and decision["initial_selected_candidate_id"]
        and decision["reason"]
    ]
    metrics["M5_explanation_coverage"] = {
        "actual_weather_driven_changes": len(changed),
        "changes_with_traceable_basis": len(explained),
        "rate": len(explained) / len(changed) if changed else None,
        "non_changes_not_counted_as_replacements": True,
    }
    metrics["M6_cost_changes"] = [{
        "id": item["id"],
        "control_state": item["control"]["state"],
        "treatment_state": item["treatment"]["state"],
        "control_initial_total": item["control"]["initial_total_cost"],
        "treatment_initial_total": item["treatment"]["initial_total_cost"],
        "control_final_total": item["control"]["final_total_cost"],
        "treatment_final_total": item["treatment"]["final_total_cost"],
        "control_activity_cost": (
            item["control"]["costs"]["activity_cost"]
            if item["control"]["costs"] else None
        ),
        "treatment_activity_cost": (
            item["treatment"]["costs"]["activity_cost"]
            if item["treatment"]["costs"] else None
        ),
        "final_total_delta": (
            item["treatment"]["final_total_cost"]
            - item["control"]["final_total_cost"]
            if item["treatment"]["final_total_cost"] is not None
            and item["control"]["final_total_cost"] is not None
            else None
        ),
    } for item in results]
    return metrics


async def _run(source: dict) -> dict[str, Any]:
    results = []
    for scenario in source["scenarios"]:
        results.append(await _run_scenario(source, scenario))
    return {
        "experiment": "stage-b-weather-control-vs-treatment",
        "method": (
            "paired deterministic simulation; only activity weather-awareness mode differs"
        ),
        "experiment_version": source["experiment_version"],
        "scenario_count": len(results),
        "time_semantics": source["time_semantics"],
        "future_extensions": source["future_extensions"],
        "scenario_results": results,
        "metrics": _aggregate(results),
        "limitations": [
            "天气、价格和候选均为固定 Mock，不代表真实预报或市场库存。",
            "Control 是天气审计但不参与选择的自动化基线，不是随机对照用户实验。",
            "结果不能推导真人满意度、真实 API 性能或开放域泛化能力。",
        ],
    }


def run(scenario_path: Path, output_path: Path) -> dict[str, Any]:
    source = json.loads(scenario_path.read_text(encoding="utf-8"))
    report = asyncio.run(_run(source))
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
    print(f"results: {args.output.resolve()}")


if __name__ == "__main__":
    main()
