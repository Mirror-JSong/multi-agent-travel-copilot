"""Cumulative budget loop using one fixed candidate snapshot."""

from __future__ import annotations

from loguru import logger

from agents.base_agent import record_agent_failure
from agents.budget_agent import BudgetAgent
from config.settings import settings
from models.schemas import FailureCode, PlanningState, TravelPlanState

from .budget_optimizer import (
    BudgetOptimizer,
    capture_candidate_snapshot,
    capture_initial_selection,
)
from .parallel import ParallelExecutor


class BudgetLoopController:
    """Search once, then reselect activities, hotel, and flights cumulatively."""

    def __init__(
        self,
        parallel_executor: ParallelExecutor | None,
        budget_agent: BudgetAgent | None = None,
        max_retries: int | None = None,
        optimizer: BudgetOptimizer | None = None,
    ) -> None:
        self.parallel_executor = parallel_executor
        self.budget_agent = budget_agent or BudgetAgent()
        configured_retries = (
            max_retries if max_retries is not None else settings.BUDGET_MAX_RETRIES
        )
        self.max_retries = max(
            0,
            min(configured_retries, len(BudgetOptimizer.targets)),
        )
        self.optimizer = optimizer or BudgetOptimizer()

    async def run(self, state: TravelPlanState) -> TravelPlanState:
        state.max_adjustments = self.max_retries
        state.adjustment_round = 0
        state.adjustment_history = []
        if self.parallel_executor is not None:
            logger.info("[BudgetLoop] initial provider search and recommendation")
            state = await self.parallel_executor.run(state)
            if state.state == PlanningState.FAILED:
                logger.error("[BudgetLoop] required agent failed; stop before budget evaluation")
                return state
        else:
            logger.info("[BudgetLoop] using provider results supplied by Pipeline")

        if state.state in {
            PlanningState.ACTIVITY_CONSTRAINTS_UNSATISFIED,
            PlanningState.PACE_CONSTRAINTS_UNSATISFIED,
        }:
            return state

        state.state = PlanningState.BUDGET_CHECKING
        state = await self.budget_agent.run(state)
        if state.state == PlanningState.FAILED:
            return state

        try:
            state.candidate_snapshot = capture_candidate_snapshot(state)
            state.initial_selection = capture_initial_selection(state)
            state.initial_total_cost = state.initial_selection.total_cost
            state.final_total_cost = state.initial_selection.total_cost
        except Exception as exc:
            record_agent_failure(
                state,
                agent_name="BudgetOptimizer",
                code=FailureCode.AGENT_EXECUTION_ERROR,
                error_type=type(exc).__name__,
                reason=str(exc) or "failed to capture candidate snapshot",
                required=True,
            )
            return state

        if state.state == PlanningState.COMPLETED:
            logger.info("[BudgetLoop] initial recommendation already satisfies budget")
            return state

        targets = self.optimizer.targets[: self.max_retries]
        for round_number, target in enumerate(targets, start=1):
            state.adjustment_round = round_number
            state.state = PlanningState.ADJUSTING
            logger.info(
                f"[BudgetLoop] round {round_number}: reselect {target.value} from snapshot"
            )
            try:
                history = self.optimizer.apply(state, target, round_number)
            except Exception as exc:
                record_agent_failure(
                    state,
                    agent_name="BudgetOptimizer",
                    code=FailureCode.AGENT_EXECUTION_ERROR,
                    error_type=type(exc).__name__,
                    reason=str(exc) or f"failed to optimize {target.value}",
                    required=True,
                )
                return state
            state.adjustment_history.append(history)

            state.state = PlanningState.BUDGET_CHECKING
            state = await self.budget_agent.run(state)
            if state.state in (PlanningState.COMPLETED, PlanningState.FAILED):
                return state

        assert state.budget_breakdown is not None
        state.state = PlanningState.BUDGET_INFEASIBLE
        state.final_total_cost = state.budget_breakdown.total_cost
        state.status_message = (
            "Planning completed, but the current staged search strategy did not find "
            f"an eligible budget-compliant plan after {state.adjustment_round} adjustment rounds."
        )
        state.error_messages.append(state.status_message)
        logger.warning(
            f"[BudgetLoop] budget infeasible: total={state.final_total_cost:.2f}, "
            f"budget={state.preferences.budget:.2f}"
        )
        return state
