"""Run the fixed C3 single-plan/control versus comparison/treatment experiment.

The measurements use deterministic Mock fixtures and automated interaction rules.
They are not evidence of real user preference, satisfaction, supplier accuracy, or
production latency.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from loguru import logger

from models.multi_plan import MultiPlanRequest
from models.plan_evaluation import MetricEvaluationStatus
from orchestrator.comparison import ComparisonOrchestrator
from orchestrator.comparison_service import PlanComparisonApplicationService
from tests.fixtures.multi_plan import (
    DESTINATION_A,
    FixedDestinationAgent,
    fixed_pipeline_factory,
)
from tests.fixtures.plan_comparison import (
    C2_FIXTURE_VERSION,
    c2_orchestrator,
    c2_request,
    custom_orchestrator,
    interest_tradeoff_pipeline_factory,
    unavailable_weather_pipeline_factory,
)


DEFAULT_OUTPUT = (
    Path(__file__).resolve().parent
    / "results"
    / "comparison_product_c3_results.json"
)


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    request: MultiPlanRequest
    orchestrator_factory: Callable[[], ComparisonOrchestrator]
    control_pipeline_factory: Callable
    expected_decision: str


def _scenarios() -> list[Scenario]:
    return [
        Scenario(
            "recommended_tradeoff",
            c2_request(budget=3600),
            lambda: custom_orchestrator(interest_tradeoff_pipeline_factory),
            interest_tradeoff_pipeline_factory,
            "recommended",
        ),
        Scenario(
            "tie_equal_metrics",
            c2_request(budget=5000),
            c2_orchestrator,
            fixed_pipeline_factory(),
            "tie",
        ),
        Scenario(
            "only_feasible_budget",
            c2_request(budget=2500),
            c2_orchestrator,
            fixed_pipeline_factory(),
            "only_feasible",
        ),
        Scenario(
            "no_recommendation_missing_weather",
            c2_request(budget=5000, interests=[], pace=None),
            lambda: custom_orchestrator(unavailable_weather_pipeline_factory),
            unavailable_weather_pipeline_factory,
            "no_recommendation",
        ),
        Scenario(
            "no_recommendation_both_infeasible",
            c2_request(budget=100),
            c2_orchestrator,
            fixed_pipeline_factory(),
            "no_recommendation",
        ),
        Scenario(
            "only_feasible_agent_failure",
            c2_request(budget=5000),
            lambda: c2_orchestrator(failing_flight_city="山城"),
            fixed_pipeline_factory(failing_flight_city="山城"),
            "only_feasible",
        ),
        Scenario(
            "one_destination_no_fabrication",
            c2_request(budget=5000),
            lambda: ComparisonOrchestrator(
                destination_agent=FixedDestinationAgent([DESTINATION_A]),
                pipeline_factory=fixed_pipeline_factory(),
            ),
            fixed_pipeline_factory(),
            "no_recommendation",
        ),
    ]


def _explanation_audit(application) -> dict[str, Any]:
    comparison = application.comparison
    source = application.multi_plan
    checks: list[dict[str, Any]] = []
    for explanation in comparison.explanations:
        checks.append({
            "code": explanation.code,
            "has_message": bool(explanation.message.strip()),
            "evidence_count": len(explanation.evidence),
            "evidence_traceable": all(
                bool(item.field_path and item.value_json)
                for item in explanation.evidence
            ),
        })
    cost_explanation = next(
        (item for item in comparison.explanations if item.code == "cost_difference"),
        None,
    )
    cost_consistent = True
    if cost_explanation is not None and len(source.plans) == 2:
        costs = [item.comparison_data.final_total_cost for item in source.plans]
        if all(value is not None for value in costs):
            difference = abs(costs[0] - costs[1])
            cost_consistent = f"{difference:.2f}" in cost_explanation.message
    return {
        "checks": checks,
        "cost_difference_consistent": cost_consistent,
        "all_consistent": cost_consistent and all(
            item["has_message"] and item["evidence_traceable"] for item in checks
        ),
    }


async def _run_scenario(scenario: Scenario) -> dict[str, Any]:
    request = scenario.request.model_copy(deep=True)
    control_pipeline = scenario.control_pipeline_factory(
        DESTINATION_A,
        request.config.model_copy(deep=True),
    )
    started = perf_counter()
    control = await control_pipeline.run_for_destination(
        request.preferences.model_copy(deep=True),
        DESTINATION_A.model_copy(deep=True),
    )
    control_ms = (perf_counter() - started) * 1000

    service = PlanComparisonApplicationService(
        orchestrator_factory=scenario.orchestrator_factory
    )
    started = perf_counter()
    application = await service.compare(request.preferences, request.config)
    treatment_ms = (perf_counter() - started) * 1000
    source = application.multi_plan
    comparison = application.comparison
    sensitivity = application.sensitivity_comparison

    evaluated_metrics = [
        metric
        for evaluation in comparison.plan_evaluations
        for metric in evaluation.metric_evaluations
        if metric.evaluation_status == MetricEvaluationStatus.EVALUATED
    ]
    evidenced_metrics = [metric for metric in evaluated_metrics if metric.evidence]
    difference_dimensions = {
        item.metric_name.value for item in comparison.metric_differences
    }
    decision_changed = (
        comparison.recommendation.decision
        != sensitivity.recommendation.decision
        or comparison.recommendation.recommended_destination_id
        != sensitivity.recommendation.recommended_destination_id
    )
    audit = _explanation_audit(application)
    return {
        "scenario_id": scenario.scenario_id,
        "fixture_version": C2_FIXTURE_VERSION,
        "expected_decision": scenario.expected_decision,
        "control": {
            "mode": "single_destination_plan",
            "destination": DESTINATION_A.city,
            "status": control.state.value,
            "final_total_cost": control.final_total_cost,
            "elapsed_ms": round(control_ms, 4),
        },
        "treatment": {
            "mode": "dual_plan_comparison",
            "generated_plan_count": source.generated_plan_count,
            "completed_plan_count": source.successful_plan_count,
            "statuses": [item.status.value for item in source.plans],
            "costs": [item.comparison_data.final_total_cost for item in source.plans],
            "decision": comparison.recommendation.decision.value,
            "decision_matches_expected": (
                comparison.recommendation.decision.value
                == scenario.expected_decision
            ),
            "evaluated_metric_count": len(evaluated_metrics),
            "evidenced_metric_count": len(evidenced_metrics),
            "evidence_complete": len(evidenced_metrics) == len(evaluated_metrics),
            "difference_dimensions": sorted(difference_dimensions),
            "difference_explanation_count": len(comparison.metric_differences),
            "explanation_audit": audit,
            "primary_policy_version": comparison.scoring_policy.policy_version,
            "alternative_policy_version": sensitivity.scoring_policy.policy_version,
            "sensitivity_decision_changed": decision_changed,
            "elapsed_ms": round(treatment_ms, 4),
            "additional_elapsed_ms": round(treatment_ms - control_ms, 4),
        },
        "raw": {
            "multi_plan": source.model_dump(mode="json"),
            "comparison": comparison.model_dump(mode="json"),
            "sensitivity_comparison": sensitivity.model_dump(mode="json"),
        },
    }


async def run_experiment() -> dict[str, Any]:
    records = [await _run_scenario(item) for item in _scenarios()]
    two_complete = sum(
        item["treatment"]["completed_plan_count"] == 2 for item in records
    )
    evaluated = sum(item["treatment"]["evaluated_metric_count"] for item in records)
    evidenced = sum(item["treatment"]["evidenced_metric_count"] for item in records)
    decisions_correct = sum(
        item["treatment"]["decision_matches_expected"] for item in records
    )
    explanations_consistent = sum(
        item["treatment"]["explanation_audit"]["all_consistent"]
        for item in records
    )
    sensitivity_disclosed = sum(
        "sensitivity_decision_changed" in item["treatment"] for item in records
    )
    comparable_output_records = [
        item for item in records
        if item["treatment"]["generated_plan_count"] == 2
    ]
    reported_difference_dimensions = sum(
        len(item["treatment"]["difference_dimensions"])
        for item in comparable_output_records
    )
    defined_difference_dimensions = 4 * len(comparable_output_records)
    return {
        "experiment": "stage_c3_comparison_product_control_treatment",
        "scope_note": (
            "Fixed deterministic Mock automation; it measures contract coverage, "
            "traceability, state classification and local execution time, not human "
            "preference, satisfaction, production latency or live travel accuracy."
        ),
        "control_definition": "One complete plan for the first selected destination.",
        "treatment_definition": (
            "Two independent plans where candidates exist, C2 evaluation, "
            "explanations and declared sensitivity check."
        ),
        "scenario_count": len(records),
        "metrics": {
            "M1_plan_choice_coverage": {
                "two_completed_scenarios": two_complete,
                "total_scenarios": len(records),
                "ratio": two_complete / len(records),
                "generated_plan_counts": [
                    item["treatment"]["generated_plan_count"] for item in records
                ],
            },
            "M2_evaluation_evidence_completeness": {
                "evidenced_evaluated_metrics": evidenced,
                "evaluated_metrics": evaluated,
                "ratio": evidenced / evaluated if evaluated else None,
            },
            "M3_explanation_consistency": {
                "consistent_scenarios": explanations_consistent,
                "total_scenarios": len(records),
                "ratio": explanations_consistent / len(records),
            },
            "M4_comparison_state_accuracy": {
                "correct_scenarios": decisions_correct,
                "total_scenarios": len(records),
                "ratio": decisions_correct / len(records),
            },
            "M5_difference_information_coverage": {
                "reported_dimensions": reported_difference_dimensions,
                "defined_dimensions": defined_difference_dimensions,
                "ratio": (
                    reported_difference_dimensions / defined_difference_dimensions
                    if defined_difference_dimensions else None
                ),
                "one_plan_scenarios_excluded_from_pairwise_denominator": (
                    len(records) - len(comparable_output_records)
                ),
                "reported_dimensions_by_scenario": {
                    item["scenario_id"]: item["treatment"]["difference_dimensions"]
                    for item in records
                },
            },
            "M6_weight_sensitivity_disclosure": {
                "disclosed_scenarios": sensitivity_disclosed,
                "total_scenarios": len(records),
                "decision_changed_scenarios": [
                    item["scenario_id"]
                    for item in records
                    if item["treatment"]["sensitivity_decision_changed"]
                ],
            },
            "timing_ms": {
                "control_total": round(sum(item["control"]["elapsed_ms"] for item in records), 4),
                "treatment_total": round(sum(item["treatment"]["elapsed_ms"] for item in records), 4),
                "paired_records": [
                    {
                        "scenario_id": item["scenario_id"],
                        "control": item["control"]["elapsed_ms"],
                        "treatment": item["treatment"]["elapsed_ms"],
                        "additional": item["treatment"]["additional_elapsed_ms"],
                    }
                    for item in records
                ],
            },
        },
        "scenarios": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    logger.remove()
    payload = asyncio.run(run_experiment())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(args.output),
        "scenario_count": payload["scenario_count"],
        "metrics": payload["metrics"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
