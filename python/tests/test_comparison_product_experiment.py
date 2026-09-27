"""Acceptance checks for the reproducible C3 product experiment."""

from __future__ import annotations

import pytest

from experiments.run_comparison_product_experiment import run_experiment


@pytest.mark.asyncio
async def test_c3_product_experiment_covers_all_formal_comparison_decisions():
    result = await run_experiment()
    decisions = {
        item["treatment"]["decision"] for item in result["scenarios"]
    }

    assert result["scenario_count"] == 7
    assert decisions == {
        "recommended",
        "tie",
        "only_feasible",
        "no_recommendation",
    }
    assert all(
        item["treatment"]["decision_matches_expected"]
        for item in result["scenarios"]
    )


@pytest.mark.asyncio
async def test_c3_product_metrics_are_derived_from_traceable_raw_records():
    result = await run_experiment()
    metrics = result["metrics"]

    assert metrics["M2_evaluation_evidence_completeness"]["ratio"] == 1
    assert metrics["M3_explanation_consistency"]["ratio"] == 1
    assert metrics["M4_comparison_state_accuracy"]["ratio"] == 1
    assert metrics["M5_difference_information_coverage"]["ratio"] == 1
    assert metrics["M6_weight_sensitivity_disclosure"]["disclosed_scenarios"] == 7
    assert metrics["timing_ms"]["control_total"] >= 0
    assert metrics["timing_ms"]["treatment_total"] >= 0
    assert all(
        record["raw"]["multi_plan"]["generated_plan_count"]
        == record["treatment"]["generated_plan_count"]
        for record in result["scenarios"]
    )


@pytest.mark.asyncio
async def test_c3_product_experiment_is_business_deterministic_while_timing_is_measured():
    first = await run_experiment()
    second = await run_experiment()

    def normalized(payload):
        return [
            {
                "scenario_id": item["scenario_id"],
                "control_status": item["control"]["status"],
                "control_cost": item["control"]["final_total_cost"],
                "generated": item["treatment"]["generated_plan_count"],
                "completed": item["treatment"]["completed_plan_count"],
                "statuses": item["treatment"]["statuses"],
                "costs": item["treatment"]["costs"],
                "decision": item["treatment"]["decision"],
                "dimensions": item["treatment"]["difference_dimensions"],
            }
            for item in payload["scenarios"]
        ]

    assert normalized(first) == normalized(second)
