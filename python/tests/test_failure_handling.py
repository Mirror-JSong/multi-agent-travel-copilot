"""必要 Agent 的异常、超时和缺失结果不得变成虚假成功。"""

from __future__ import annotations

import asyncio

import pytest

from agents.activity_agent import ActivityAgent
from agents.base_agent import BaseAgent
from agents.budget_agent import BudgetAgent
from agents.hotel_agent import HotelAgent
from models.schemas import FailureCode, PlanningState, TravelPlanState, UserPreferences
from orchestrator.parallel import ParallelExecutor
from orchestrator.pipeline import TravelPlanningPipeline


def _state() -> TravelPlanState:
    return TravelPlanState(
        preferences=UserPreferences(
            budget=10_000,
            departure_city="北京",
            start_date="2026-05-01",
            end_date="2026-05-05",
        )
    )


class ExplodingAgent(BaseAgent):
    name = "ExplodingAgent"

    async def execute(self, state: TravelPlanState) -> TravelPlanState:
        raise RuntimeError("provider unavailable")


class SlowAgent(BaseAgent):
    name = "SlowAgent"

    async def execute(self, state: TravelPlanState) -> TravelPlanState:
        await asyncio.sleep(0.1)
        return state


class OptionalExplodingAgent(ExplodingAgent):
    name = "OptionalExplodingAgent"
    required = False


@pytest.mark.asyncio
async def test_required_agent_exception_is_structured_and_terminal():
    result = await ParallelExecutor([ExplodingAgent()]).run(_state())

    assert result.state == PlanningState.FAILED
    assert len(result.agent_failures) == 1
    failure = result.agent_failures[0]
    assert failure.agent == "ExplodingAgent"
    assert failure.code == FailureCode.AGENT_EXECUTION_ERROR
    assert failure.error_type == "RuntimeError"
    assert failure.reason == "provider unavailable"
    assert failure.required is True


@pytest.mark.asyncio
async def test_required_agent_timeout_is_distinct_from_execution_error():
    result = await ParallelExecutor([SlowAgent()], timeout=0.01).run(_state())

    assert result.state == PlanningState.FAILED
    assert len(result.agent_failures) == 1
    failure = result.agent_failures[0]
    assert failure.agent == "SlowAgent"
    assert failure.code == FailureCode.AGENT_TIMEOUT
    assert failure.error_type == "TimeoutError"
    assert "0.01" in failure.reason


@pytest.mark.asyncio
async def test_optional_agent_failure_is_partial_not_complete_success():
    result = await ParallelExecutor([OptionalExplodingAgent()]).run(_state())

    assert result.state == PlanningState.COLLECTING_PREFERENCES
    assert result.state != PlanningState.COMPLETED
    assert len(result.agent_failures) == 1
    assert result.agent_failures[0].required is False


@pytest.mark.asyncio
async def test_budget_agent_rejects_missing_required_results_instead_of_zero_cost():
    result = await BudgetAgent().run(_state())

    assert result.state == PlanningState.FAILED
    assert result.budget_breakdown is None
    assert result.candidate_snapshot is None
    assert result.adjustment_history == []
    assert {failure.agent for failure in result.agent_failures} == {
        "FlightAgent",
        "HotelAgent",
        "ActivityAgent",
    }
    assert all(
        failure.code == FailureCode.MISSING_REQUIRED_RESULT
        for failure in result.agent_failures
    )


@pytest.mark.asyncio
async def test_budget_agent_does_not_overwrite_upstream_failed_state():
    state = _state()
    state.state = PlanningState.FAILED
    state.error_messages.append("upstream failed")

    result = await BudgetAgent().run(state)

    assert result.state == PlanningState.FAILED
    assert result.budget_breakdown is None
    assert result.candidate_snapshot is None
    assert result.adjustment_history == []
    assert result.error_messages == ["upstream failed"]
    assert result.agent_failures == []


@pytest.mark.asyncio
async def test_pipeline_stops_when_required_agent_raises(monkeypatch):
    async def fail_hotel(self, state):
        raise ConnectionError("hotel provider offline")

    monkeypatch.setattr(HotelAgent, "execute", fail_hotel)
    pipeline = TravelPlanningPipeline()

    result = await pipeline.run(_state().preferences)

    assert result.state == PlanningState.FAILED
    assert result.budget_breakdown is None
    assert any(
        failure.agent == "HotelAgent"
        and failure.code == FailureCode.AGENT_EXECUTION_ERROR
        and failure.error_type == "ConnectionError"
        for failure in result.agent_failures
    )


@pytest.mark.asyncio
async def test_pipeline_stops_when_required_agent_times_out(monkeypatch):
    async def slow_activity(self, state):
        await asyncio.sleep(0.1)
        return state

    monkeypatch.setattr(ActivityAgent, "execute", slow_activity)
    pipeline = TravelPlanningPipeline()
    pipeline.parallel_executor.timeout = 0.01

    result = await pipeline.run(_state().preferences)

    assert result.state == PlanningState.FAILED
    assert result.budget_breakdown is None
    assert any(
        failure.agent == "ActivityAgent"
        and failure.code == FailureCode.AGENT_TIMEOUT
        for failure in result.agent_failures
    )
