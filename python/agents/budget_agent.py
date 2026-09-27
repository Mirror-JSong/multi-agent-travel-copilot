"""Budget evaluator.

This agent validates required outputs and calculates totals. Candidate reselection
belongs to the budget orchestrator; this class never mutates a candidate price.
"""

from __future__ import annotations

from loguru import logger

from models.schemas import BudgetBreakdown, FailureCode, PlanningState, TravelPlanState
from models.costing import recalculate_selected_costs

from .base_agent import BaseAgent, record_agent_failure


class BudgetAgent(BaseAgent):
    name = "BudgetAgent"

    async def execute(self, state: TravelPlanState) -> TravelPlanState:
        if state.state in {
            PlanningState.FAILED,
            PlanningState.ACTIVITY_CONSTRAINTS_UNSATISFIED,
            PlanningState.PACE_CONSTRAINTS_UNSATISFIED,
        }:
            logger.error(f"[{self.name}] upstream terminal state; skip budget evaluation")
            return state

        if state.preferences is None:
            raise ValueError("user preferences are required")

        required_results = (
            ("FlightAgent", "flight_result"),
            ("HotelAgent", "hotel_result"),
            ("ActivityAgent", "activity_result"),
        )
        missing_results = [
            (agent_name, field_name)
            for agent_name, field_name in required_results
            if getattr(state, field_name) is None
        ]
        if missing_results:
            for agent_name, field_name in missing_results:
                record_agent_failure(
                    state,
                    agent_name=agent_name,
                    code=FailureCode.MISSING_REQUIRED_RESULT,
                    error_type="MissingRequiredResult",
                    reason=f"required result {field_name} is missing; budget cannot be calculated",
                    required=True,
                )
            return state

        assert state.flight_result is not None
        assert state.hotel_result is not None
        assert state.activity_result is not None
        incomplete_selections: list[tuple[str, str]] = []
        if (
            state.flight_result.recommended_outbound is None
            or state.flight_result.recommended_return is None
        ):
            incomplete_selections.append(("FlightAgent", "recommended outbound/return flight"))
        if state.hotel_result.recommended is None:
            incomplete_selections.append(("HotelAgent", "recommended hotel"))
        if (
            not state.activity_result.day_plans
            or any(not day.activities for day in state.activity_result.day_plans)
        ):
            incomplete_selections.append(("ActivityAgent", "complete daily activities"))
        if incomplete_selections:
            for agent_name, selection_name in incomplete_selections:
                record_agent_failure(
                    state,
                    agent_name=agent_name,
                    code=FailureCode.MISSING_REQUIRED_RESULT,
                    error_type="MissingRequiredResult",
                    reason=f"required selection {selection_name} is missing",
                    required=True,
                )
            return state

        flight_cost, hotel_cost, activity_cost, total = recalculate_selected_costs(state)
        remaining = round(state.preferences.budget - total, 2)
        within_budget = remaining >= 0
        over_amount = round(max(0.0, -remaining), 2)

        suggestions = []
        if not within_budget:
            suggestions = self._generate_suggestions(
                over_amount,
                flight_cost,
                hotel_cost,
                activity_cost,
                state.adjustment_round,
            )

        state.budget_breakdown = BudgetBreakdown(
            flight_cost=flight_cost,
            hotel_cost=hotel_cost,
            activity_cost=activity_cost,
            total_cost=total,
            budget=state.preferences.budget,
            remaining=remaining,
            is_within_budget=within_budget,
            over_budget_amount=over_amount,
            suggestions=suggestions,
        )
        state.final_total_cost = total

        if within_budget:
            state.state = PlanningState.COMPLETED
            state.status_message = (
                "The selected plan satisfies the budget and required constraints."
            )
            logger.info(
                f"[{self.name}] budget passed: total={total:.2f}, remaining={remaining:.2f}"
            )
        else:
            state.state = PlanningState.ADJUSTING
            state.status_message = (
                "The selected plan is over budget; candidate reselection is required."
            )
            logger.warning(
                f"[{self.name}] over budget by {over_amount:.2f} after round "
                f"{state.adjustment_round}"
            )
        return state

    @staticmethod
    def _generate_suggestions(
        over: float,
        flight: float,
        hotel: float,
        activity: float,
        round_num: int,
    ) -> list[str]:
        if round_num == 0:
            return [
                f"Reselect lower-cost activities to cover up to {min(over, activity):.2f}."
            ]
        if round_num == 1:
            return [
                f"Reselect a lower-cost eligible hotel to cover up to {min(over, hotel):.2f}."
            ]
        return [
            f"Reselect lower-cost eligible flights to cover up to {min(over, flight):.2f}."
        ]
