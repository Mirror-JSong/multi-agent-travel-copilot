"""B3 controlled experiment, decision trace, and metric acceptance tests."""

from __future__ import annotations

import json
from pathlib import Path

from experiments.run_weather_awareness_experiment import run


EXPERIMENT_DIR = Path(__file__).parents[1] / "experiments"
SCENARIOS = EXPERIMENT_DIR / "weather_awareness_scenarios.json"
RECORDED = EXPERIMENT_DIR / "results" / "weather_awareness_results.json"


def test_fixed_weather_experiment_has_required_coverage_without_intraday_claims() -> None:
    source = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    scenarios = source["scenarios"]

    assert len(scenarios) >= 12
    assert len({item["id"] for item in scenarios}) == len(scenarios)
    assert {item["destination"] for item in scenarios} >= {"东京", "曼谷", "首尔"}
    assert {item["weather_profile"] for item in scenarios} >= {
        "sunny",
        "normal_rain",
        "heavy_rain",
        "mixed_four_days",
        "unavailable",
    }
    assert {item["activity_fixture"] for item in scenarios} >= {
        "provider",
        "high_cost_indoor",
        "budget_reselect",
        "no_cheaper_compliant",
        "outdoor_only",
    }
    assert "day-level" in source["time_semantics"]
    assert any("Intraday" in item for item in source["future_extensions"])


def test_experiment_reproduces_recorded_raw_results_and_metrics(tmp_path) -> None:
    rerun = run(SCENARIOS, tmp_path / "weather-result.json")
    recorded = json.loads(RECORDED.read_text(encoding="utf-8"))

    assert rerun == recorded
    assert rerun["scenario_count"] == 13


def test_control_and_treatment_only_differ_in_weather_aware_decisions() -> None:
    report = json.loads(RECORDED.read_text(encoding="utf-8"))

    for scenario in report["scenario_results"]:
        assert scenario["paired_invariants"] == {
            "weather_equal": True,
            "candidate_sets_equal": True,
            "activity_dataset_version": "2026.09-activity-weather-v2",
            "weather_source_version": "weather-experiment-v1",
            "business_fixture_version": "weather-experiment-business-v1",
        }
        assert scenario["control"]["weather"] == scenario["treatment"]["weather"]
        assert (
            scenario["control"]["candidate_signature"]
            == scenario["treatment"]["candidate_signature"]
        )


def test_six_metrics_report_samples_tradeoffs_and_traceable_changes() -> None:
    metrics = json.loads(RECORDED.read_text(encoding="utf-8"))["metrics"]

    control_conflicts = metrics["M1_weather_conflict_rate"]["control"]
    treatment_conflicts = metrics["M1_weather_conflict_rate"]["treatment"]
    assert control_conflicts["conflicts"] == 33
    assert control_conflicts["evaluable_activities"] == 63
    assert treatment_conflicts["conflicts"] == 0
    assert treatment_conflicts["evaluable_activities"] == 60
    assert metrics["M2_interest_match_rate"]["control"]["selected_activities"] == 69
    assert metrics["M2_interest_match_rate"]["treatment"]["selected_activities"] == 66
    assert metrics["M3_budget_compliance_rate"]["treatment"]["budget_infeasible"] == 1
    assert (
        metrics["M3_budget_compliance_rate"]["treatment"]
        ["activity_constraints_unsatisfied"]
        == 1
    )
    assert metrics["M4_plan_feasibility_rate"]["treatment"]["operationally_complete"] == 12
    assert metrics["M5_explanation_coverage"] == {
        "actual_weather_driven_changes": 34,
        "changes_with_traceable_basis": 34,
        "rate": 1.0,
        "non_changes_not_counted_as_replacements": True,
    }
    assert len(metrics["M6_cost_changes"]) == 13
    assert any(
        item["final_total_delta"] is not None and item["final_total_delta"] > 0
        for item in metrics["M6_cost_changes"]
    )
    assert any(
        item["final_total_delta"] is not None and item["final_total_delta"] < 0
        for item in metrics["M6_cost_changes"]
    )


def test_budget_and_no_feasible_scenarios_keep_weather_constraints_explicit() -> None:
    results = {
        item["id"]: item
        for item in json.loads(RECORDED.read_text(encoding="utf-8"))["scenario_results"]
    }
    budget = results["budget_reselect_indoor"]["treatment"]
    activity_round = budget["adjustment_history"][0]
    assert activity_round["target"] == "activities"
    assert activity_round["improved"] is True
    assert activity_round["saved_amount"] == 270
    assert all(
        item["weather_compatible"] is True
        for item in budget["selected_activities"]
    )

    no_cheaper = results["no_cheaper_weather_compliant"]["treatment"]
    assert no_cheaper["adjustment_history"][0]["target"] == "activities"
    assert no_cheaper["adjustment_history"][0]["improved"] is False
    assert no_cheaper["adjustment_history"][0]["saved_amount"] == 0

    no_feasible = results["no_feasible_outdoor_only"]["treatment"]
    assert no_feasible["state"] == "activity_constraints_unsatisfied"
    assert no_feasible["costs"] is None
    assert len(no_feasible["constraint_issues"]) == 3
