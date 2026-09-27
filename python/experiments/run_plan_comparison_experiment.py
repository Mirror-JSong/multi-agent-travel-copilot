"""Run the fixed Stage C2 comparison demonstrations and sensitivity check.

This is a rules/Mock-data experiment. It does not measure human satisfaction
and does not call a real LLM, weather service, or travel supplier.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from loguru import logger

from evaluation import PlanComparator
from models.plan_evaluation import ScoringPolicy
from tests.fixtures.plan_comparison import (
    C2_FIXTURE_VERSION,
    c2_orchestrator,
    c2_request,
    custom_orchestrator,
    interest_tradeoff_pipeline_factory,
    unavailable_weather_pipeline_factory,
)


DEFAULT_OUTPUT = (
    Path(__file__).resolve().parent / "results" / "plan_comparison_c2_results.json"
)


def _summary(source, comparison) -> dict[str, Any]:
    return {
        "planning_statuses": {
            item.destination_id: item.status.value for item in source.plans
        },
        "costs": {
            item.destination_id: item.comparison_data.final_total_cost
            for item in source.plans
        },
        "metrics": {
            evaluation.destination_id: {
                metric.metric_name.value: {
                    "raw_value": metric.raw_value,
                    "normalized_score": metric.normalized_score,
                    "numerator": metric.numerator,
                    "denominator": metric.denominator,
                    "status": metric.evaluation_status.value,
                    "effective_weight": metric.effective_weight,
                }
                for metric in evaluation.metric_evaluations
            }
            for evaluation in comparison.plan_evaluations
        },
        "overall_scores": {
            item.destination_id: item.overall_score
            for item in comparison.plan_evaluations
        },
        "comparability": comparison.comparability.value,
        "recommendation": comparison.recommendation.model_dump(mode="json"),
        "policy_version": comparison.scoring_policy.policy_version,
        "effective_weights": {
            key.value: value for key, value in comparison.effective_weights.items()
        },
    }


async def _record(name: str, orchestrator, request, comparator) -> dict[str, Any]:
    source = await orchestrator.run(request)
    comparison = comparator.compare(source)
    return {
        "scenario_id": name,
        "summary": _summary(source, comparison),
        "source_multi_plan_result": source.model_dump(mode="json"),
        "comparison_result": comparison.model_dump(mode="json"),
    }


async def run_experiment() -> dict[str, Any]:
    default_comparator = PlanComparator()
    scenario_a_source = await custom_orchestrator(
        interest_tradeoff_pipeline_factory
    ).run(c2_request(budget=3_600))
    scenario_a_default = default_comparator.compare(scenario_a_source)
    shifted_policy = ScoringPolicy(
        policy_version="plan-scoring-sensitivity-v1",
        budget_weight=0.35,
        interest_weight=0.25,
        pace_weight=0.25,
        weather_weight=0.15,
    )
    scenario_a_shifted = PlanComparator(shifted_policy).compare(scenario_a_source)

    scenarios = [
        {
            "scenario_id": "A_two_completed_tradeoff",
            "summary": _summary(scenario_a_source, scenario_a_default),
            "source_multi_plan_result": scenario_a_source.model_dump(mode="json"),
            "comparison_result": scenario_a_default.model_dump(mode="json"),
        },
        await _record(
            "B_one_completed_one_budget_infeasible",
            c2_orchestrator(),
            c2_request(budget=2_500),
            default_comparator,
        ),
        await _record(
            "C_missing_weather_and_unspecified_preferences",
            custom_orchestrator(unavailable_weather_pipeline_factory),
            c2_request(interests=[], pace=None),
            default_comparator,
        ),
    ]
    return {
        "experiment": "stage_c2_plan_comparison",
        "fixture_version": C2_FIXTURE_VERSION,
        "scope_note": (
            "Deterministic automated rule experiment over fixed Mock data; "
            "not human preference, satisfaction, or real-market evidence."
        ),
        "scenarios": scenarios,
        "sensitivity": {
            "same_source_input_fingerprint": scenario_a_default.input_fingerprint,
            "default": _summary(scenario_a_source, scenario_a_default),
            "shifted": _summary(scenario_a_source, scenario_a_shifted),
            "finding": (
                "A five-point transfer from interest to budget changes the result "
                "from a destination recommendation to a tie under the declared "
                "two-point threshold; this indicates weight sensitivity."
            ),
        },
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
        "scenario_count": len(payload["scenarios"]),
        "scenario_recommendations": {
            item["scenario_id"]: item["summary"]["recommendation"]["decision"]
            for item in payload["scenarios"]
        },
        "sensitivity": {
            "default": payload["sensitivity"]["default"]["recommendation"]["decision"],
            "shifted": payload["sensitivity"]["shifted"]["recommendation"]["decision"],
        },
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
