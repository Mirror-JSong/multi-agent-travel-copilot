"""Run the reproducible Stage C1 internal orchestration demonstration.

This script intentionally uses the independent C1 acceptance fixture rather
than product demo prices.  It does not expose an API or implement C2 ranking.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from loguru import logger

from models.multi_plan import (
    MultiPlanConfig,
    MultiPlanExecutionMode,
    MultiPlanRequest,
)
from models.schemas import UserPreferences
from orchestrator.comparison import ComparisonOrchestrator
from tests.fixtures.multi_plan import (
    C1_FIXTURE_VERSION,
    DESTINATION_A,
    DESTINATION_B,
    FixedDestinationAgent,
    fixed_pipeline_factory,
)


DEFAULT_OUTPUT = Path(__file__).resolve().parent / "results" / "multi_plan_c1_demo.json"


def _request(budget: float, execution_mode: MultiPlanExecutionMode) -> MultiPlanRequest:
    return MultiPlanRequest(
        preferences=UserPreferences(
            budget=budget,
            departure_city="Origin",
            start_date="2026-05-01",
            end_date="2026-05-03",
            num_travelers=1,
            interests=["文化"],
        ),
        config=MultiPlanConfig(
            execution_mode=execution_mode,
            max_concurrency=2,
            mock_data_version=C1_FIXTURE_VERSION,
            mock_activity_data_version=C1_FIXTURE_VERSION,
            destination_data_version=C1_FIXTURE_VERSION,
        ),
    )


def _orchestrator() -> ComparisonOrchestrator:
    return ComparisonOrchestrator(
        destination_agent=FixedDestinationAgent([DESTINATION_A, DESTINATION_B]),
        pipeline_factory=fixed_pipeline_factory(),
    )


def _plan_record(plan) -> dict[str, Any]:
    state = plan.plan
    breakdown = state.budget_breakdown
    selected_activities = [
        activity
        for day in (state.activity_result.day_plans if state.activity_result else [])
        for activity in day.activities
    ]
    return {
        "destination_id": plan.destination_id,
        "destination": plan.destination.model_dump(mode="json"),
        "status": plan.status.value,
        "budget": state.preferences.budget,
        "costs": (
            {
                "flight": breakdown.flight_cost,
                "hotel": breakdown.hotel_cost,
                "activity": breakdown.activity_cost,
                "total": breakdown.total_cost,
                "within_budget": breakdown.is_within_budget,
            }
            if breakdown
            else None
        ),
        "adjustment_round": state.adjustment_round,
        "adjustment_history": [
            item.model_dump(mode="json") for item in state.adjustment_history
        ],
        "selected_candidate_ids": {
            "outbound": (
                state.flight_result.recommended_outbound.candidate_id
                if state.flight_result and state.flight_result.recommended_outbound
                else None
            ),
            "return": (
                state.flight_result.recommended_return.candidate_id
                if state.flight_result and state.flight_result.recommended_return
                else None
            ),
            "hotel": (
                state.hotel_result.recommended.candidate_id
                if state.hotel_result and state.hotel_result.recommended
                else None
            ),
            "activities": [item.candidate_id for item in selected_activities],
        },
        "weather_destination": (
            state.weather_result.destination if state.weather_result else None
        ),
        "activity_locations": sorted({item.location for item in selected_activities}),
        "comparison_data": plan.comparison_data.model_dump(mode="json"),
        "errors": [item.model_dump(mode="json") for item in plan.errors],
        "business_issues": [
            item.model_dump(mode="json") for item in plan.business_issues
        ],
    }


def _result_record(result) -> dict[str, Any]:
    return {
        "request_id": result.request_id,
        "orchestration_completed": result.orchestration_completed,
        "generated_plan_count": result.generated_plan_count,
        "successful_plan_count": result.successful_plan_count,
        "failed_or_infeasible_plan_count": result.failed_or_infeasible_plan_count,
        "destination_candidates": [
            item.model_dump(mode="json") for item in result.destination_candidates
        ],
        "plans": [_plan_record(item) for item in result.plans],
        "issues": [item.model_dump(mode="json") for item in result.issues],
    }


async def run(output_path: Path) -> dict[str, Any]:
    success = await _orchestrator().run(_request(
        5_000.0,
        MultiPlanExecutionMode.CONCURRENT,
    ))
    partial = await _orchestrator().run(_request(
        2_500.0,
        MultiPlanExecutionMode.CONCURRENT,
    ))
    serial = await _orchestrator().run(_request(
        2_500.0,
        MultiPlanExecutionMode.SERIAL,
    ))

    partial_business = partial.model_dump(mode="json")
    serial_business = serial.model_dump(mode="json")
    partial_business.pop("execution_mode")
    serial_business.pop("execution_mode")

    isolation = {
        "travel_plan_state_objects_distinct": (
            success.plans[0].plan is not success.plans[1].plan
        ),
        "preference_objects_distinct": (
            success.plans[0].plan.preferences is not success.plans[1].plan.preferences
        ),
        "candidate_snapshots_distinct": (
            success.plans[0].plan.candidate_snapshot
            is not success.plans[1].plan.candidate_snapshot
        ),
        "adjustment_histories_distinct": (
            partial.plans[0].plan.adjustment_history
            is not partial.plans[1].plan.adjustment_history
        ),
        "weather_destinations_match": all(
            item.plan.weather_result is not None
            and item.plan.weather_result.destination == item.destination.city
            for item in success.plans
        ),
        "activity_locations_match": all(
            activity.location == item.destination.city
            for item in success.plans
            for day in item.plan.activity_result.day_plans
            for activity in day.activities
        ),
        "serial_concurrent_business_equal": partial_business == serial_business,
    }

    payload = {
        "metadata": {
            "stage": "C1",
            "fixture_version": C1_FIXTURE_VERSION,
            "is_mock": True,
            "notice": (
                "独立固定验收 Fixture；价格、天气与活动属性均非实时或实测数据。"
            ),
            "budget_semantics": "每个目的地各自使用完整用户预算，不拆分预算。",
        },
        "scenario_a_both_completed": _result_record(success),
        "scenario_b_partial_success": _result_record(partial),
        "isolation_evidence": isolation,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload


def main() -> None:
    logger.remove()
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = asyncio.run(run(args.output))
    print(json.dumps({
        "output": str(args.output),
        "scenario_a": [
            (item["destination"]["city"], item["status"], item["costs"]["total"])
            for item in result["scenario_a_both_completed"]["plans"]
        ],
        "scenario_b": [
            (item["destination"]["city"], item["status"], item["costs"]["total"])
            for item in result["scenario_b_partial_success"]["plans"]
        ],
        "isolation_evidence": result["isolation_evidence"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
