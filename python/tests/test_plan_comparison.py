"""Stage C2 acceptance tests for deterministic and explainable comparison."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from evaluation import PlanComparator
from models.multi_plan import MultiPlanExecutionMode
from models.plan_evaluation import (
    MetricEvaluationStatus,
    MetricName,
    PlanComparability,
    PlanEvaluationEligibility,
    RecommendationDecision,
    ScoringPolicy,
)
from models.schemas import PlanningState, TravelPace, WeatherCondition
from tests.fixtures.multi_plan import DESTINATION_A, DESTINATION_B
from tests.fixtures.plan_comparison import (
    c2_orchestrator,
    c2_request,
    custom_orchestrator,
    interest_tradeoff_pipeline_factory,
    unavailable_weather_pipeline_factory,
)


PYTHON_ROOT = Path(__file__).resolve().parent.parent


def _metrics(evaluation):
    return {item.metric_name: item for item in evaluation.metric_evaluations}


@pytest.mark.asyncio
async def test_c2_t1_completed_plans_produce_complete_four_dimension_evaluation():
    source = await c2_orchestrator().run(c2_request())
    result = PlanComparator().compare(source)

    assert result.comparability == PlanComparability.COMPARABLE
    assert len(result.plan_evaluations) == 2
    assert all(
        item.evaluation_eligibility == PlanEvaluationEligibility.ELIGIBLE
        for item in result.plan_evaluations
    )
    assert all(
        set(_metrics(item)) == set(MetricName)
        for item in result.plan_evaluations
    )
    assert all(item.data_completeness.completeness_ratio == 1 for item in result.plan_evaluations)


@pytest.mark.asyncio
async def test_c2_t2_real_cost_difference_and_budget_scores_reconcile():
    source = await c2_orchestrator().run(c2_request(budget=3_600))
    result = PlanComparator().compare(source)
    first, second = result.plan_evaluations

    assert [item.comparison_data.final_total_cost for item in source.plans] == [2100, 3600]
    assert _metrics(first)[MetricName.BUDGET_MATCH].normalized_score == 100
    assert _metrics(second)[MetricName.BUDGET_MATCH].normalized_score == 80
    cost_text = next(item.message for item in result.explanations if item.code == "cost_difference")
    assert "1500.00" in cost_text


@pytest.mark.asyncio
async def test_c2_t3_interest_difference_uses_selected_activity_evidence():
    source = await custom_orchestrator(interest_tradeoff_pipeline_factory).run(
        c2_request(budget=3_600)
    )
    result = PlanComparator().compare(source)
    first, second = result.plan_evaluations
    a = _metrics(first)[MetricName.INTEREST_MATCH]
    b = _metrics(second)[MetricName.INTEREST_MATCH]

    assert (a.numerator, a.denominator, a.normalized_score) == (4, 6, pytest.approx(66.6667))
    assert (b.numerator, b.denominator, b.normalized_score) == (6, 6, 100)
    assert len(a.evidence[-1].candidate_ids) == 4
    assert len(b.evidence[-1].candidate_ids) == 6


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pace", "factory_options", "expected"),
    [
        (TravelPace.RELAXED, {}, 100.0),
        (TravelPace.BALANCED, {}, 100.0),
        (
            TravelPace.PACKED,
            {"activity_variant_by_city": {
                DESTINATION_A.city: "high_only",
                DESTINATION_B.city: "high_only",
            }},
            100.0,
        ),
    ],
)
async def test_c2_t4_pace_rules_follow_the_declared_user_pace(
    pace, factory_options, expected
):
    source = await c2_orchestrator(**factory_options).run(c2_request(pace=pace))
    result = PlanComparator().compare(source)
    assert all(
        _metrics(item)[MetricName.PACE_FIT].normalized_score == expected
        for item in result.plan_evaluations
    )


@pytest.mark.asyncio
async def test_c2_t4_packed_rewards_supported_high_intensity_without_claiming_discomfort():
    low_source = await c2_orchestrator().run(c2_request(pace=TravelPace.PACKED))
    high_source = await c2_orchestrator(activity_variant_by_city={
        DESTINATION_A.city: "high_only",
        DESTINATION_B.city: "high_only",
    }).run(c2_request(pace=TravelPace.PACKED))
    low = PlanComparator().compare(low_source)
    high = PlanComparator().compare(high_source)
    assert _metrics(low.plan_evaluations[0])[MetricName.PACE_FIT].normalized_score == 85
    assert _metrics(high.plan_evaluations[0])[MetricName.PACE_FIT].normalized_score == 100


@pytest.mark.asyncio
async def test_c2_t5_weather_metric_uses_actual_compatibility_without_sunny_bonus():
    sunny = PlanComparator().compare(await c2_orchestrator().run(c2_request()))
    rainy = PlanComparator().compare(await c2_orchestrator(
        weather_by_city={
            DESTINATION_A.city: WeatherCondition.HEAVY_RAIN,
            DESTINATION_B.city: WeatherCondition.HEAVY_RAIN,
        }
    ).run(c2_request()))
    assert all(_metrics(item)[MetricName.WEATHER_SUITABILITY].normalized_score == 100 for item in sunny.plan_evaluations)
    assert all(_metrics(item)[MetricName.WEATHER_SUITABILITY].normalized_score == 100 for item in rainy.plan_evaluations)


@pytest.mark.asyncio
async def test_c2_t6_unavailable_weather_has_no_fabricated_score_or_recommendation():
    source = await custom_orchestrator(unavailable_weather_pipeline_factory).run(
        c2_request()
    )
    result = PlanComparator().compare(source)
    assert all(item.status == PlanningState.COMPLETED for item in source.plans)
    assert all(
        _metrics(item)[MetricName.WEATHER_SUITABILITY].evaluation_status
        == MetricEvaluationStatus.INSUFFICIENT_DATA
        for item in result.plan_evaluations
    )
    assert result.comparability == PlanComparability.NOT_COMPARABLE
    assert result.recommendation.decision == RecommendationDecision.NO_RECOMMENDATION


@pytest.mark.asyncio
async def test_c2_t7_unspecified_interest_and_pace_are_not_applicable_not_zero():
    source = await custom_orchestrator(unavailable_weather_pipeline_factory).run(
        c2_request(interests=[], pace=None)
    )
    result = PlanComparator().compare(source)
    for evaluation in result.plan_evaluations:
        metrics = _metrics(evaluation)
        for name in (MetricName.INTEREST_MATCH, MetricName.PACE_FIT):
            assert metrics[name].evaluation_status == MetricEvaluationStatus.NOT_APPLICABLE
            assert metrics[name].normalized_score is None


@pytest.mark.asyncio
async def test_c2_t8_one_completed_one_budget_infeasible_is_conditional_only_feasible():
    source = await c2_orchestrator().run(c2_request(budget=2_500))
    result = PlanComparator().compare(source)
    assert [item.status for item in source.plans] == [
        PlanningState.COMPLETED,
        PlanningState.BUDGET_INFEASIBLE,
    ]
    assert result.comparability == PlanComparability.SINGLE_FEASIBLE
    assert result.recommendation.decision == RecommendationDecision.ONLY_FEASIBLE
    assert result.recommendation.recommended_destination_id == source.plans[0].destination_id
    assert result.plan_evaluations[1].overall_score is None


@pytest.mark.asyncio
async def test_c2_t9_activity_constraint_failure_keeps_original_business_status():
    source = await c2_orchestrator(
        activity_variant_by_city={DESTINATION_B.city: "outdoor_only"},
        weather_by_city={DESTINATION_B.city: WeatherCondition.HEAVY_RAIN},
    ).run(c2_request())
    result = PlanComparator().compare(source)
    assert source.plans[1].status == PlanningState.ACTIVITY_CONSTRAINTS_UNSATISFIED
    assert result.plan_evaluations[1].evaluation_eligibility == PlanEvaluationEligibility.BUSINESS_INFEASIBLE
    assert result.plan_evaluations[1].overall_score is None
    assert any(item.code == "plan_status_activity_constraints_unsatisfied" for item in result.explanations)


@pytest.mark.asyncio
async def test_c2_t10_failed_agent_never_gets_a_normal_score():
    source = await c2_orchestrator(failing_flight_city=DESTINATION_B.city).run(
        c2_request()
    )
    result = PlanComparator().compare(source)
    failed = result.plan_evaluations[1]
    assert failed.planning_status == PlanningState.FAILED
    assert failed.evaluation_eligibility == PlanEvaluationEligibility.FAILED
    assert failed.overall_score is None
    assert all(item.evaluation_status == MetricEvaluationStatus.INELIGIBLE for item in failed.metric_evaluations)


@pytest.mark.asyncio
async def test_c2_t11_equal_metrics_return_tie_instead_of_forced_winner():
    result = PlanComparator().compare(await c2_orchestrator().run(c2_request()))
    assert result.recommendation.decision == RecommendationDecision.TIE
    assert result.recommendation.recommended_destination_id is None
    assert len(result.recommendation.tied_destination_ids) == 2


@pytest.mark.asyncio
async def test_c2_t12_metric_numbers_and_explanations_reconcile_with_source():
    source = await custom_orchestrator(interest_tradeoff_pipeline_factory).run(
        c2_request(budget=3_600)
    )
    result = PlanComparator().compare(source)
    for plan, evaluation in zip(source.plans, result.plan_evaluations, strict=True):
        metrics = _metrics(evaluation)
        budget = metrics[MetricName.BUDGET_MATCH]
        interest = metrics[MetricName.INTEREST_MATCH]
        assert budget.numerator == pytest.approx(
            plan.comparison_data.budget - plan.comparison_data.final_total_cost
        )
        activities = [item for item in plan.comparison_data.activity_facts if not item.is_rest]
        matched = [item for item in activities if item.matched_interests]
        assert (interest.numerator, interest.denominator) == (len(matched), len(activities))
        assert f"{len(matched)}/{len(activities)}" in interest.explanation


@pytest.mark.asyncio
async def test_c2_t13_pair_uses_the_same_common_metric_set_and_effective_weights():
    result = PlanComparator().compare(await c2_orchestrator().run(c2_request()))
    assert set(result.common_evaluated_metrics) == set(MetricName)
    assert sum(result.effective_weights.values()) == pytest.approx(1)
    for evaluation in result.plan_evaluations:
        assert {
            name: _metrics(evaluation)[name].effective_weight
            for name in result.common_evaluated_metrics
        } == result.effective_weights


@pytest.mark.asyncio
async def test_c2_t14_comparison_output_is_deeply_isolated_and_source_is_read_only():
    source = await c2_orchestrator().run(c2_request())
    before = source.model_dump_json()
    result = PlanComparator().compare(source)
    second_score = result.plan_evaluations[1].metric_evaluations[0].normalized_score

    result.plan_evaluations[0].metric_evaluations[0].normalized_score = 1
    result.plan_evaluations[0].metric_evaluations[0].evidence.clear()

    assert source.model_dump_json() == before
    assert result.plan_evaluations[1].metric_evaluations[0].normalized_score == second_score


@pytest.mark.asyncio
async def test_c2_t15_same_input_is_identical_across_five_runs():
    comparator = PlanComparator()
    outputs = []
    for _ in range(5):
        source = await c2_orchestrator().run(c2_request(budget=3_600))
        outputs.append(comparator.compare(source).model_dump_json())
    assert len(set(outputs)) == 1


def test_c2_t15_same_input_is_identical_across_python_processes():
    script = r'''
import asyncio
import base64
from evaluation import PlanComparator
from tests.fixtures.plan_comparison import c2_orchestrator, c2_request

async def main():
    source = await c2_orchestrator().run(c2_request(budget=3600))
    payload = PlanComparator().compare(source).model_dump_json().encode("utf-8")
    print("C2JSON:" + base64.b64encode(payload).decode("ascii"))

asyncio.run(main())
'''
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PYTHON_ROOT)
    outputs = [
        subprocess.run(
            [sys.executable, "-c", script],
            cwd=PYTHON_ROOT.parent,
            env=env,
            capture_output=True,
            check=True,
        ).stdout.split(b"C2JSON:")[-1].strip()
        for _ in range(2)
    ]
    assert outputs[0] == outputs[1]


@pytest.mark.asyncio
async def test_c2_t16_serial_and_concurrent_plans_receive_identical_evaluation():
    serial_source, concurrent_source = await asyncio.gather(
        c2_orchestrator().run(c2_request(execution_mode=MultiPlanExecutionMode.SERIAL)),
        c2_orchestrator().run(c2_request(execution_mode=MultiPlanExecutionMode.CONCURRENT)),
    )
    serial = PlanComparator().compare(serial_source).model_dump(mode="json")
    concurrent = PlanComparator().compare(concurrent_source).model_dump(mode="json")
    assert serial == concurrent


def test_c2_t17_policy_version_weights_and_invalid_configuration_are_explicit():
    policy = ScoringPolicy(
        policy_version="plan-scoring-sensitivity-v1",
        budget_weight=0.35,
        interest_weight=0.25,
        pace_weight=0.25,
        weather_weight=0.15,
    )
    assert policy.policy_version == "plan-scoring-sensitivity-v1"
    assert policy.weights()[MetricName.BUDGET_MATCH] == 0.35
    with pytest.raises(ValidationError, match="sum to 1.0"):
        ScoringPolicy(budget_weight=0.5)


@pytest.mark.asyncio
async def test_c2_t18_weight_sensitivity_is_reproducible_and_disclosed():
    source = await custom_orchestrator(interest_tradeoff_pipeline_factory).run(
        c2_request(budget=3_600)
    )
    default = PlanComparator().compare(source)
    shifted_policy = ScoringPolicy(
        policy_version="plan-scoring-sensitivity-v1",
        budget_weight=0.35,
        interest_weight=0.25,
        pace_weight=0.25,
        weather_weight=0.15,
    )
    shifted = PlanComparator(shifted_policy).compare(source)
    shifted_repeat = PlanComparator(shifted_policy).compare(source)

    assert default.recommendation.decision == RecommendationDecision.RECOMMENDED
    assert default.recommendation.recommended_destination_id == source.plans[1].destination_id
    assert shifted.recommendation.decision == RecommendationDecision.TIE
    assert shifted.model_dump_json() == shifted_repeat.model_dump_json()
    assert shifted.scoring_policy.policy_version == "plan-scoring-sensitivity-v1"
