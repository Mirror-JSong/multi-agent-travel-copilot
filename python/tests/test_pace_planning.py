"""C0 pace contracts, planning policy, budget compatibility, and experiment tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import app
from config.settings import settings
from experiments.run_pace_planning_experiment import (
    DEFAULT_OUTPUT,
    DEFAULT_SCENARIOS,
    run,
)
from models.schemas import (
    ActivityAttributeStatus,
    ActivityIntensity,
    ActivitySearchRequest,
    DataSource,
    PlanningState,
    TravelPace,
    UserPreferences,
)
from tools.activity_search import MockActivityProvider


client = TestClient(app)


def _results() -> dict:
    return json.loads(DEFAULT_OUTPUT.read_text(encoding="utf-8"))


def _scenario(report: dict, scenario_id: str) -> dict:
    return next(item for item in report["scenario_results"] if item["id"] == scenario_id)


def test_user_preferences_pace_is_optional_and_legacy_fast_is_canonicalized() -> None:
    base = {
        "budget": 10000.0,
        "departure_city": "北京",
        "start_date": "2026-10-01",
        "end_date": "2026-10-03",
        "num_travelers": 1,
    }
    assert UserPreferences(**base).pace is None
    assert UserPreferences(**base, pace="relaxed").pace == TravelPace.RELAXED
    assert UserPreferences(**base, pace="balanced").pace == TravelPace.BALANCED
    assert UserPreferences(**base, pace="packed").pace == TravelPace.PACKED
    assert UserPreferences(**base, pace="fast").pace == TravelPace.PACKED


@pytest.mark.asyncio
async def test_default_mock_activity_attributes_are_explicit_versioned_and_isolated() -> None:
    provider = MockActivityProvider()
    request = ActivitySearchRequest(
        city="东京",
        start_date="2026-10-01",
        end_date="2026-10-03",
    )
    first = await provider.search(request)
    second = await provider.search(request)

    assert first == second
    assert {item.source_version for item in first} == {
        "2026.09-activity-weather-pace-v3"
    }
    assert all(item.source == DataSource.MOCK for item in first)
    assert all(item.duration_status == ActivityAttributeStatus.AVAILABLE for item in first)
    assert all(item.intensity_status == ActivityAttributeStatus.AVAILABLE for item in first)
    assert all(item.intensity != ActivityIntensity.UNKNOWN for item in first)
    assert all(item.pace_attribute_source == DataSource.MOCK for item in first)
    assert len({item.candidate_id for item in first}) == len(first)

    first[0].name = "mutated"
    third = await provider.search(request)
    assert third[0].name != "mutated"
    assert settings.MOCK_ACTIVITY_DATA_VERSION == "2026.09-activity-weather-pace-v3"


def test_fixed_experiment_reproduces_recorded_results(tmp_path) -> None:
    rerun = run(DEFAULT_SCENARIOS, tmp_path / "pace-results.json")
    assert rerun == _results()
    assert rerun["scenario_count"] == 11
    assert rerun["metrics"]["core_only_variable_is_pace"] is True


def test_same_pool_produces_real_relaxed_balanced_and_packed_differences() -> None:
    report = _results()
    relaxed = _scenario(report, "relaxed_sunny")
    balanced = _scenario(report, "balanced_sunny")
    packed = _scenario(report, "packed_sunny")
    legacy = _scenario(report, "legacy_unspecified_sunny")

    assert relaxed["daily_activity_counts"] == [2, 2]
    assert relaxed["rest_count"] == 2
    assert relaxed["known_high_intensity_count"] == 0
    assert balanced["daily_activity_counts"] == [3, 3]
    assert balanced["known_high_intensity_count"] == 0
    assert packed["daily_activity_counts"] == [3, 3]
    assert packed["known_high_intensity_count"] == 6
    assert legacy["selected_candidate_ids"] == balanced["selected_candidate_ids"]
    assert legacy["rest_count"] == 0


def test_relaxed_weather_and_budget_joint_constraints_preserve_rest_and_pace() -> None:
    item = _scenario(_results(), "relaxed_rainy_low_budget")

    assert item["state"] == PlanningState.BUDGET_INFEASIBLE.value
    assert item["rest_count"] == 2
    assert item["known_high_intensity_count"] == 0
    assert item["weather_hard_pass_count"] == item["weather_evaluable_count"]
    activity_round = item["adjustment_history"][0]
    assert activity_round["target"] == "activities"
    assert activity_round["improved"] is True
    assert activity_round["saved_amount"] == 160.0
    assert all("medium" in candidate_id for candidate_id in activity_round["after_candidate_ids"])
    assert item["candidate_prices_unchanged"] is True


def test_unknown_or_high_only_candidates_are_not_treated_as_relaxed_safe() -> None:
    report = _results()
    unknown = _scenario(report, "relaxed_unknown_attributes")
    high_only = _scenario(report, "relaxed_high_only")

    assert unknown["state"] == PlanningState.PACE_CONSTRAINTS_UNSATISFIED.value
    assert high_only["state"] == PlanningState.PACE_CONSTRAINTS_UNSATISFIED.value
    assert unknown["activity_count"] == high_only["activity_count"] == 0
    assert any(
        issue["code"] == "pace_minimum_not_met"
        for issue in unknown["constraint_issues"]
    )


def test_missing_balanced_slot_is_activity_not_budget_or_pace_failure() -> None:
    item = _scenario(_results(), "balanced_missing_evening")
    assert item["state"] == PlanningState.ACTIVITY_CONSTRAINTS_UNSATISFIED.value
    assert item["adjustment_round"] == 0
    assert item["total_cost"] is None


def test_natural_language_confirmed_pace_reaches_pipeline_and_receipt_blocks_tamper() -> None:
    parsed = client.post("/api/preferences/parse", json={
        "text": (
            "从北京出发，预算五万，1人，舒适游，"
            "2026年10月1日到2026年10月3日，不想太累，喜欢历史。"
        ),
        "reference_date": "2026-09-25",
        "config": {
            "provider": "mock",
            "locale": "zh-CN",
            "version": "mock-preference-parser-v1",
        },
    })
    assert parsed.status_code == 200
    parsed_body = parsed.json()
    assert parsed_body["is_complete"] is True
    assert parsed_body["draft"]["pace"] == "relaxed"

    payload = {
        "draft": parsed_body["draft"],
        "draft_receipt": parsed_body["draft_receipt"],
        "confirmed": True,
        "reference_date": "2026-09-25",
        "config": {
            "provider": "mock",
            "locale": "zh-CN",
            "version": "mock-preference-parser-v1",
        },
    }
    planned = client.post("/api/preferences/plan", json=payload)
    assert planned.status_code == 200
    body = planned.json()
    assert body["preferences"]["pace"] == "relaxed"
    assert all(
        len(day["activities"]) <= 2 for day in body["plan"]["activity_result"]["day_plans"]
    )
    assert any(
        day["rest_slots"] for day in body["plan"]["activity_result"]["day_plans"]
    )

    tampered = json.loads(json.dumps(payload, ensure_ascii=False))
    tampered["draft"]["pace"] = "packed"
    rejected = client.post("/api/preferences/plan", json=tampered)
    assert rejected.status_code == 422


def test_structured_api_accepts_pace_and_unset_request_keeps_legacy_density() -> None:
    base = {
        "budget": 50000.0,
        "departure_city": "北京",
        "start_date": "2026-10-01",
        "end_date": "2026-10-03",
        "travel_style": "comfort",
        "num_travelers": 1,
        "interests": ["历史"],
    }
    legacy = client.post("/api/plan/full", json=base)
    relaxed = client.post("/api/plan/full", json={**base, "pace": "relaxed"})

    assert legacy.status_code == relaxed.status_code == 200
    assert legacy.json()["preferences"]["pace"] is None
    assert relaxed.json()["preferences"]["pace"] == "relaxed"
    assert all(
        len(day["activities"]) == 3
        for day in legacy.json()["activity_result"]["day_plans"]
    )
    assert all(
        1 <= len(day["activities"]) <= 2
        for day in relaxed.json()["activity_result"]["day_plans"]
    )
