"""Stage C1 acceptance tests for independent dual-destination planning."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from agents.base_agent import BaseAgent
from models.multi_plan import (
    MultiPlanConfig,
    MultiPlanExecutionMode,
    MultiPlanRequest,
)
from models.schemas import (
    Destination,
    PlanningState,
    TravelPace,
    TravelPlanState,
    UserPreferences,
    WeatherCondition,
)
from orchestrator.comparison import ComparisonOrchestrator
from orchestrator.pipeline import TravelPlanningPipeline
from tests.fixtures.multi_plan import (
    C1_FIXTURE_VERSION,
    DESTINATION_A,
    DESTINATION_B,
    FixedDestinationAgent,
    fixed_pipeline_factory,
)


PYTHON_ROOT = Path(__file__).resolve().parent.parent


def _preferences(
    *,
    budget: float = 5_000.0,
    pace: TravelPace | None = None,
) -> UserPreferences:
    return UserPreferences(
        budget=budget,
        departure_city="Origin",
        start_date="2026-05-01",
        end_date="2026-05-03",
        num_travelers=1,
        interests=["文化"],
        pace=pace,
    )


def _request(
    *,
    budget: float = 5_000.0,
    pace: TravelPace | None = None,
    execution_mode: MultiPlanExecutionMode = MultiPlanExecutionMode.CONCURRENT,
) -> MultiPlanRequest:
    return MultiPlanRequest(
        preferences=_preferences(budget=budget, pace=pace),
        config=MultiPlanConfig(
            execution_mode=execution_mode,
            max_concurrency=2,
            mock_data_version=C1_FIXTURE_VERSION,
            mock_activity_data_version=C1_FIXTURE_VERSION,
            destination_data_version=C1_FIXTURE_VERSION,
        ),
    )


def _orchestrator(**factory_options) -> ComparisonOrchestrator:
    return ComparisonOrchestrator(
        destination_agent=FixedDestinationAgent([DESTINATION_A, DESTINATION_B]),
        pipeline_factory=fixed_pipeline_factory(**factory_options),
    )


def _business_signature(result) -> dict:
    payload = result.model_dump(mode="json")
    payload.pop("execution_mode")
    return payload


@pytest.mark.asyncio
async def test_c1_t1_two_distinct_destinations_complete_with_full_contract():
    result = await _orchestrator().run(_request())

    assert result.orchestration_completed is True
    assert result.generated_plan_count == 2
    assert result.successful_plan_count == 2
    assert result.failed_or_infeasible_plan_count == 0
    assert [item.destination.city for item in result.plans] == ["海城", "山城"]
    assert len({item.destination_id for item in result.plans}) == 2
    assert [item.comparison_data.final_total_cost for item in result.plans] == [
        2_100.0,
        3_600.0,
    ]
    assert all(item.plan.preferences.budget == 5_000.0 for item in result.plans)
    assert all(item.plan.flight_result is not None for item in result.plans)
    assert all(item.plan.hotel_result is not None for item in result.plans)
    assert all(item.plan.weather_result is not None for item in result.plans)
    assert all(item.plan.activity_result is not None for item in result.plans)
    assert all(item.plan.budget_breakdown is not None for item in result.plans)


@pytest.mark.asyncio
async def test_default_destination_agent_and_product_mock_providers_are_reused():
    preferences = UserPreferences(
        budget=30_000.0,
        departure_city="上海",
        start_date="2026-10-01",
        end_date="2026-10-03",
        num_travelers=1,
        interests=["文化"],
    )
    result = await ComparisonOrchestrator().run(MultiPlanRequest(
        preferences=preferences,
    ))

    assert result.generated_plan_count == 2
    assert result.successful_plan_count == 2
    assert len(result.destination_candidates) == 3
    assert len({item.destination.city for item in result.plans}) == 2
    assert all(item.plan.preferences.budget == 30_000.0 for item in result.plans)
    assert all(
        item.plan.weather_result.destination == item.destination.city
        for item in result.plans
    )


class ExplodingDestinationAgent(BaseAgent):
    name = "DestinationAgent"

    async def execute(self, state: TravelPlanState) -> TravelPlanState:
        raise AssertionError("run_for_destination must not execute DestinationAgent")


@pytest.mark.asyncio
async def test_run_for_destination_skips_destination_agent_and_owns_inputs():
    config = _request().config
    pipeline = fixed_pipeline_factory()(DESTINATION_A, config)
    pipeline.destination_agent = ExplodingDestinationAgent()
    preferences = _preferences()
    before = preferences.model_dump_json()

    state = await pipeline.run_for_destination(preferences, DESTINATION_A)

    assert state.state == PlanningState.COMPLETED
    assert state.selected_destination.city == DESTINATION_A.city
    assert state.destination_rec.reasoning.startswith("C1 指定目的地独立规划")
    assert state.preferences is not preferences
    assert preferences.model_dump_json() == before


@pytest.mark.asyncio
async def test_c1_t9_states_candidates_histories_and_preferences_are_isolated():
    original = _preferences(budget=2_500.0)
    before = original.model_dump_json()
    result = await _orchestrator().run(MultiPlanRequest(
        preferences=original,
        config=_request(budget=2_500.0).config,
    ))
    first, second = result.plans

    assert first.plan is not second.plan
    assert first.plan.preferences is not second.plan.preferences
    assert first.plan.candidate_snapshot is not second.plan.candidate_snapshot
    assert first.plan.adjustment_history is not second.plan.adjustment_history
    assert first.plan.weather_result is not second.plan.weather_result
    assert first.plan.activity_result is not second.plan.activity_result
    assert original.model_dump_json() == before

    second_price = second.plan.candidate_snapshot.outbound_flights[0].price
    first.plan.candidate_snapshot.outbound_flights[0].price = 1.0
    assert second.plan.candidate_snapshot.outbound_flights[0].price == second_price

    second_history_size = len(second.plan.adjustment_history)
    first.plan.adjustment_history.append(
        second.plan.adjustment_history[0].model_copy(deep=True)
    )
    assert len(second.plan.adjustment_history) == second_history_size


@pytest.mark.asyncio
async def test_c1_t11_same_request_is_identical_across_five_runs():
    request = _request(budget=2_500.0)
    outputs = [
        (await _orchestrator().run(request)).model_dump_json()
        for _ in range(5)
    ]
    assert len(set(outputs)) == 1


def test_c1_t11_same_request_is_identical_across_python_processes():
    script = r'''
import asyncio
from models.multi_plan import MultiPlanConfig, MultiPlanRequest
from models.schemas import UserPreferences
from orchestrator.comparison import ComparisonOrchestrator
from tests.fixtures.multi_plan import (
    C1_FIXTURE_VERSION, DESTINATION_A, DESTINATION_B,
    FixedDestinationAgent, fixed_pipeline_factory,
)

async def main():
    preferences = UserPreferences(
        budget=2500.0, departure_city="Origin",
        start_date="2026-05-01", end_date="2026-05-03",
        num_travelers=1, interests=["文化"],
    )
    request = MultiPlanRequest(
        preferences=preferences,
        config=MultiPlanConfig(
            mock_data_version=C1_FIXTURE_VERSION,
            mock_activity_data_version=C1_FIXTURE_VERSION,
            destination_data_version=C1_FIXTURE_VERSION,
        ),
    )
    orchestrator = ComparisonOrchestrator(
        destination_agent=FixedDestinationAgent([DESTINATION_A, DESTINATION_B]),
        pipeline_factory=fixed_pipeline_factory(),
    )
    print((await orchestrator.run(request)).model_dump_json())

asyncio.run(main())
'''
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PYTHON_ROOT)
    outputs = [
        subprocess.run(
            [sys.executable, "-c", script],
            cwd=PYTHON_ROOT,
            env=env,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
        for _ in range(2)
    ]
    assert outputs[0]
    assert outputs[0] == outputs[1]


@pytest.mark.asyncio
async def test_c1_t13_serial_and_controlled_concurrent_business_results_match():
    serial = await _orchestrator().run(_request(
        budget=2_500.0,
        execution_mode=MultiPlanExecutionMode.SERIAL,
    ))
    concurrent = await _orchestrator().run(_request(
        budget=2_500.0,
        execution_mode=MultiPlanExecutionMode.CONCURRENT,
    ))

    assert serial.request_id == concurrent.request_id
    assert _business_signature(serial) == _business_signature(concurrent)


@pytest.mark.asyncio
async def test_c1_t2_partial_success_preserves_budget_infeasible_plan():
    result = await _orchestrator().run(_request(budget=2_500.0))
    first, second = result.plans

    assert first.status == PlanningState.COMPLETED
    assert first.comparison_data.final_total_cost == 2_100.0
    assert second.status == PlanningState.BUDGET_INFEASIBLE
    assert second.comparison_data.final_total_cost == 2_660.0
    assert second.plan.adjustment_round == 3
    assert len(second.plan.adjustment_history) == 3
    assert second.business_issues[0].code == "budget_infeasible"
    assert result.successful_plan_count == 1
    assert result.failed_or_infeasible_plan_count == 1


@pytest.mark.asyncio
async def test_c1_t3_one_weather_activity_constraint_failure_is_isolated():
    result = await _orchestrator(
        activity_variant_by_city={DESTINATION_B.city: "outdoor_only"},
        weather_by_city={DESTINATION_B.city: WeatherCondition.HEAVY_RAIN},
    ).run(_request())

    assert [item.status for item in result.plans] == [
        PlanningState.COMPLETED,
        PlanningState.ACTIVITY_CONSTRAINTS_UNSATISFIED,
    ]
    constrained = result.plans[1]
    assert constrained.plan.activity_result is not None
    assert constrained.plan.budget_breakdown is None
    assert any(
        issue.code == "no_weather_compatible_candidate"
        for issue in constrained.plan.activity_result.constraint_issues
    )


@pytest.mark.asyncio
async def test_c1_t4_pace_constraint_failure_is_not_budget_or_program_failure():
    result = await _orchestrator(
        activity_variant_by_city={DESTINATION_B.city: "high_only"},
    ).run(_request(pace=TravelPace.RELAXED))
    constrained = result.plans[1]

    assert constrained.status == PlanningState.PACE_CONSTRAINTS_UNSATISFIED
    assert constrained.errors == []
    assert constrained.plan.budget_breakdown is None
    assert constrained.business_issues[0].code == "pace_constraints_unsatisfied"


@pytest.mark.asyncio
async def test_c1_t5_required_agent_failure_does_not_discard_other_plan():
    result = await _orchestrator(
        failing_flight_city=DESTINATION_B.city,
    ).run(_request())
    first, second = result.plans

    assert first.status == PlanningState.COMPLETED
    assert second.status == PlanningState.FAILED
    assert second.plan.budget_breakdown is None
    assert any(
        failure.agent == "FlightAgent"
        and "controlled flight failure" in failure.reason
        for failure in second.errors
    )
    assert result.orchestration_completed is True


@pytest.mark.asyncio
async def test_c1_t6_two_distinct_business_infeasibilities_are_preserved():
    result = await _orchestrator(
        activity_variant_by_city={DESTINATION_B.city: "outdoor_only"},
        weather_by_city={DESTINATION_B.city: WeatherCondition.HEAVY_RAIN},
    ).run(_request(budget=100.0))

    assert [item.status for item in result.plans] == [
        PlanningState.BUDGET_INFEASIBLE,
        PlanningState.ACTIVITY_CONSTRAINTS_UNSATISFIED,
    ]
    assert all(item.status != PlanningState.FAILED for item in result.plans)


@pytest.mark.asyncio
async def test_c1_t7_one_valid_candidate_is_reported_without_fabrication():
    orchestrator = ComparisonOrchestrator(
        destination_agent=FixedDestinationAgent([DESTINATION_A]),
        pipeline_factory=fixed_pipeline_factory(),
    )
    result = await orchestrator.run(_request())

    assert result.orchestration_completed is True
    assert result.selected_destination_count == 1
    assert result.generated_plan_count == 1
    assert len(result.destination_candidates) == 1
    assert any(
        issue.code == "insufficient_destination_candidates"
        for issue in result.issues
    )


@pytest.mark.asyncio
async def test_c1_t8_no_valid_candidate_is_reported_without_fabrication():
    orchestrator = ComparisonOrchestrator(
        destination_agent=FixedDestinationAgent([]),
        pipeline_factory=fixed_pipeline_factory(),
    )
    result = await orchestrator.run(_request())

    assert result.orchestration_completed is True
    assert result.selected_destination_count == 0
    assert result.generated_plan_count == 0
    assert result.plans == []
    assert any(
        issue.code == "no_valid_destination_candidates"
        for issue in result.issues
    )


@pytest.mark.asyncio
async def test_duplicate_destinations_are_filtered_by_stable_identity():
    duplicate = DESTINATION_A.model_copy(deep=True)
    orchestrator = ComparisonOrchestrator(
        destination_agent=FixedDestinationAgent([DESTINATION_A, duplicate]),
        pipeline_factory=fixed_pipeline_factory(),
    )
    result = await orchestrator.run(_request())

    assert result.generated_plan_count == 1
    assert any(
        issue.code == "duplicate_destination_candidate"
        for issue in result.issues
    )
    assert any(
        issue.code == "insufficient_destination_candidates"
        for issue in result.issues
    )


@pytest.mark.asyncio
async def test_c1_t10_weather_and_pace_hard_constraints_apply_per_destination():
    result = await _orchestrator(
        activity_variant_by_city={
            DESTINATION_A.city: "weather_choice",
            DESTINATION_B.city: "high_only",
        },
        weather_by_city={DESTINATION_A.city: WeatherCondition.HEAVY_RAIN},
    ).run(_request(pace=TravelPace.RELAXED))
    weather_plan, pace_plan = result.plans

    assert weather_plan.status == PlanningState.COMPLETED
    selected = [
        activity
        for day in weather_plan.plan.activity_result.day_plans
        for activity in day.activities
    ]
    assert selected
    assert all(activity.environment.value == "indoor" for activity in selected)
    assert all(activity.weather_compatible is True for activity in selected)
    assert any(
        evaluation.selection_outcome == "excluded_by_weather_hard_constraint"
        for evaluation in weather_plan.plan.activity_result.weather_evaluations
    )

    assert pace_plan.status == PlanningState.PACE_CONSTRAINTS_UNSATISFIED
    assert any(
        evaluation.selection_outcome == "excluded_by_pace_hard_constraint"
        for evaluation in pace_plan.plan.activity_result.pace_evaluations
    )
    assert any(fact.is_rest for fact in pace_plan.comparison_data.activity_facts)


@pytest.mark.asyncio
async def test_c1_t12_independent_concurrent_requests_do_not_pollute_results():
    orchestrator = _orchestrator()
    first, second = await asyncio.gather(
        orchestrator.run(_request(budget=2_500.0)),
        orchestrator.run(_request(budget=2_500.0)),
    )

    assert first.model_dump_json() == second.model_dump_json()
    assert first.plans[0].plan is not second.plans[0].plan
    first.plans[0].plan.error_messages.append("mutation probe")
    assert "mutation probe" not in second.plans[0].plan.error_messages


@pytest.mark.asyncio
async def test_c1_t14_single_destination_pipeline_business_output_is_unchanged():
    config = _request().config
    automatic = fixed_pipeline_factory()(DESTINATION_A, config)
    automatic.destination_agent = FixedDestinationAgent([DESTINATION_A, DESTINATION_B])
    specified = fixed_pipeline_factory()(DESTINATION_A, config)

    auto_state = await automatic.run(_preferences())
    specified_state = await specified.run_for_destination(
        _preferences(),
        DESTINATION_A,
    )

    assert auto_state.state == specified_state.state
    for field in (
        "flight_result",
        "hotel_result",
        "weather_result",
        "weather_planning",
        "activity_result",
        "budget_breakdown",
        "candidate_snapshot",
        "initial_selection",
        "adjustment_history",
        "initial_total_cost",
        "final_total_cost",
    ):
        assert getattr(auto_state, field) == getattr(specified_state, field)


@pytest.mark.asyncio
async def test_c2_factual_inputs_are_complete_and_contain_no_aggregate_score():
    result = await _orchestrator().run(_request())

    for destination_plan in result.plans:
        data = destination_plan.comparison_data
        assert data.flight_cost is not None
        assert data.hotel_cost is not None
        assert data.activity_cost is not None
        assert data.final_total_cost == (
            data.flight_cost + data.hotel_cost + data.activity_cost
        )
        assert data.candidate_snapshot_available is True
        assert data.missing_data == []
        assert data.daily_activity_counts == {
            "2026-05-01": 3,
            "2026-05-02": 3,
        }
        assert all(fact.matched_interests == ["文化"] for fact in data.activity_facts)
        assert set(data.source_versions) == {"flight", "hotel", "activity", "weather"}
        assert all(
            versions == [C1_FIXTURE_VERSION]
            for versions in data.source_versions.values()
        )
        assert "score" not in data.model_dump()


class FailingDestinationDiscoveryAgent(BaseAgent):
    name = "DestinationAgent"

    async def execute(self, state: TravelPlanState) -> TravelPlanState:
        raise RuntimeError("C1 controlled destination discovery failure")


@pytest.mark.asyncio
async def test_destination_discovery_program_failure_is_not_reported_as_completed():
    result = await ComparisonOrchestrator(
        destination_agent=FailingDestinationDiscoveryAgent(),
        pipeline_factory=fixed_pipeline_factory(),
    ).run(_request())

    assert result.orchestration_completed is False
    assert result.plans == []
    assert result.issues[0].code == "destination_discovery_failed"
    assert "controlled destination discovery failure" in result.issues[0].message


def test_multi_plan_request_rejects_more_than_two_plans():
    with pytest.raises(ValidationError):
        MultiPlanRequest(preferences=_preferences(), plan_count=3)


@pytest.mark.asyncio
async def test_run_for_destination_rejects_invalid_destination_type():
    pipeline = TravelPlanningPipeline()
    with pytest.raises(TypeError):
        await pipeline.run_for_destination(_preferences(), "海城")  # type: ignore[arg-type]
