"""Deterministic, candidate-based budget optimization.

Providers supply facts. Agents make the initial recommendation. This module only
reselects from the frozen candidate snapshot; it never changes candidate prices.
"""

from __future__ import annotations

from datetime import date
from math import ceil

from agents.weather_compatibility import WeatherCompatibilityEvaluator
from agents.pace_planning import PaceCompatibilityEvaluator
from config.settings import settings
from models.costing import recalculate_selected_costs
from models.schemas import (
    Activity,
    ActivityPlanningMode,
    ActivityReplacement,
    ActivitySelection,
    BudgetAdjustment,
    BudgetComponent,
    CandidateSnapshot,
    Flight,
    InitialPlanSelection,
    TravelPlanState,
)


def _money(value: float) -> float:
    return round(float(value), 2)


def capture_candidate_snapshot(state: TravelPlanState) -> CandidateSnapshot:
    if state.flight_result is None or state.hotel_result is None or state.activity_result is None:
        raise ValueError("cannot snapshot missing planning results")

    groups = (
        state.flight_result.outbound_flights,
        state.flight_result.return_flights,
        state.hotel_result.hotels,
        state.activity_result.activity_candidates,
    )
    candidates = [candidate for group in groups for candidate in group]
    if any(not candidate.candidate_id for candidate in candidates):
        raise ValueError("every budget candidate must have a stable candidate_id")
    if any(not candidate.source_version for candidate in candidates):
        raise ValueError("every budget candidate must retain its source_version")

    return CandidateSnapshot(
        outbound_flights=tuple(item.model_copy(deep=True) for item in groups[0]),
        return_flights=tuple(item.model_copy(deep=True) for item in groups[1]),
        hotels=tuple(item.model_copy(deep=True) for item in groups[2]),
        activities=tuple(item.model_copy(deep=True) for item in groups[3]),
    )


def capture_initial_selection(state: TravelPlanState) -> InitialPlanSelection:
    flight_cost, hotel_cost, activity_cost, total_cost = recalculate_selected_costs(state)
    assert state.flight_result is not None
    assert state.hotel_result is not None
    assert state.activity_result is not None
    assert state.flight_result.recommended_outbound is not None
    assert state.flight_result.recommended_return is not None
    assert state.hotel_result.recommended is not None

    activities = [
        ActivitySelection(
            date=day.date,
            time_slot=activity.time_slot,
            candidate_id=activity.candidate_id,
        )
        for day in state.activity_result.day_plans
        for activity in day.activities
    ]
    return InitialPlanSelection(
        outbound_flight_id=state.flight_result.recommended_outbound.candidate_id,
        return_flight_id=state.flight_result.recommended_return.candidate_id,
        hotel_id=state.hotel_result.recommended.candidate_id,
        activities=activities,
        flight_cost=flight_cost,
        hotel_cost=hotel_cost,
        activity_cost=activity_cost,
        total_cost=total_cost,
    )


class BudgetOptimizer:
    """Apply one cumulative optimization round using a fixed candidate snapshot."""

    targets = (
        BudgetComponent.ACTIVITIES,
        BudgetComponent.HOTEL,
        BudgetComponent.FLIGHTS,
    )

    def __init__(
        self,
        weather_evaluator: WeatherCompatibilityEvaluator | None = None,
        pace_evaluator: PaceCompatibilityEvaluator | None = None,
        *,
        activity_planning_mode: ActivityPlanningMode = ActivityPlanningMode.WEATHER_AWARE,
    ) -> None:
        self.weather_evaluator = weather_evaluator or WeatherCompatibilityEvaluator()
        self.pace_evaluator = pace_evaluator or PaceCompatibilityEvaluator()
        self.activity_planning_mode = activity_planning_mode

    def apply(
        self,
        state: TravelPlanState,
        target: BudgetComponent,
        round_number: int,
    ) -> BudgetAdjustment:
        if state.preferences is None or state.candidate_snapshot is None:
            raise ValueError("preferences and candidate snapshot are required")
        if target == BudgetComponent.ACTIVITIES:
            return self._optimize_activities(state, round_number)
        if target == BudgetComponent.HOTEL:
            return self._optimize_hotel(state, round_number)
        if target == BudgetComponent.FLIGHTS:
            return self._optimize_flights(state, round_number)
        raise ValueError(f"unsupported budget component: {target}")

    def _history(
        self,
        state: TravelPlanState,
        *,
        round_number: int,
        target: BudgetComponent,
        before_ids: list[str],
        after_ids: list[str],
        before_cost: float,
        after_cost: float,
        replacements: list[ActivityReplacement] | None = None,
    ) -> BudgetAdjustment:
        _, _, _, current_total = recalculate_selected_costs(state)
        saved = _money(max(0.0, before_cost - after_cost))
        return BudgetAdjustment(
            round_number=round_number,
            target=target,
            policy_version=settings.BUDGET_POLICY_VERSION,
            before_candidate_ids=before_ids,
            after_candidate_ids=after_ids,
            before_cost=_money(before_cost),
            after_cost=_money(after_cost),
            saved_amount=saved,
            current_total=current_total,
            budget_remaining=_money(state.preferences.budget - current_total),
            improved=saved > 0,
            activity_replacements=replacements or [],
        )

    @staticmethod
    def _matches_interest(activity: Activity, interests: list[str]) -> bool:
        haystack = f"{activity.name} {activity.category} {activity.description}".lower()
        return any(interest.strip().lower() in haystack for interest in interests if interest.strip())

    def _optimize_activities(
        self, state: TravelPlanState, round_number: int
    ) -> BudgetAdjustment:
        assert state.activity_result is not None
        snapshot = state.candidate_snapshot
        assert snapshot is not None
        interests = state.preferences.interests
        destination = state.selected_destination
        if destination is None:
            raise ValueError("selected destination is required")
        before_cost = state.activity_result.total_activity_cost
        before_ids = [
            activity.candidate_id
            for day in state.activity_result.day_plans
            for activity in day.activities
        ]
        replacements: list[ActivityReplacement] = []
        weather_by_date = {
            item.date: item
            for item in (state.weather_result.daily_weather if state.weather_result else [])
        }

        for day in state.activity_result.day_plans:
            for index, selected in enumerate(day.activities):
                other_duration = sum(
                    activity.duration_hours
                    for other_index, activity in enumerate(day.activities)
                    if other_index != index
                    and activity.duration_status.value == "available"
                )
                lower_with_constraints = []
                for candidate in snapshot.activities:
                    if not (
                        candidate.location == destination.city
                        and candidate.time_slot == selected.time_slot
                        and candidate.available_start_date
                        <= day.date
                        < candidate.available_end_date
                        and candidate.price < selected.price
                    ):
                        continue
                    evaluation = self.weather_evaluator.evaluate(
                        candidate,
                        weather_by_date.get(day.date),
                        activity_date=day.date,
                    )
                    pace_evaluation = self.pace_evaluator.evaluate(
                        candidate,
                        state.preferences.pace,
                        activity_date=day.date,
                        projected_daily_duration=other_duration + candidate.duration_hours,
                    )
                    if (
                        self.activity_planning_mode == ActivityPlanningMode.BASELINE
                        or evaluation.hard_constraint_satisfied
                    ) and pace_evaluation.hard_constraint_satisfied:
                        lower_with_constraints.append(
                            (candidate, evaluation, pace_evaluation)
                        )
                lower = [item[0] for item in lower_with_constraints]
                interest_matches = [
                    candidate for candidate in lower
                    if self._matches_interest(candidate, interests)
                ]
                eligible = interest_matches if interests else lower
                if not eligible:
                    continue
                replacement = min(
                    eligible,
                    key=lambda item: (
                        item.price,
                        -int(self._matches_interest(item, interests)),
                        -item.rating,
                        item.candidate_id,
                    ),
                )
                replacement_evaluation = next(
                    evaluation
                    for candidate, evaluation, _ in lower_with_constraints
                    if candidate.candidate_id == replacement.candidate_id
                )
                replacement_pace_evaluation = next(
                    pace_evaluation
                    for candidate, _, pace_evaluation in lower_with_constraints
                    if candidate.candidate_id == replacement.candidate_id
                )
                selection_reason = replacement_evaluation.reason
                if state.preferences.pace is not None:
                    selection_reason = (
                        f"{selection_reason} "
                        f"节奏约束：{replacement_pace_evaluation.reason}"
                    )
                if self.activity_planning_mode == ActivityPlanningMode.BASELINE:
                    selection_reason = (
                        "控制组预算重选只使用城市、日期、时段、兴趣和价格约束；"
                        f"天气仅审计：{replacement_evaluation.reason}"
                    )
                day.activities[index] = replacement.model_copy(
                    deep=True,
                    update={
                        "description": f"{day.date} {selected.time_slot} - {replacement.name}",
                        "weather_compatible": (
                            replacement_evaluation.hard_constraint_satisfied
                            if replacement_evaluation.data_sufficient
                            else None
                        ),
                        "recommendation_reason": selection_reason,
                        "pace_compatible": (
                            replacement_pace_evaluation.hard_constraint_satisfied
                        ),
                        "pace_recommendation_reason": replacement_pace_evaluation.reason,
                    },
                )
                state.activity_result.weather_evaluations.append(
                    replacement_evaluation.model_copy(deep=True)
                )
                state.activity_result.pace_evaluations.append(
                    replacement_pace_evaluation.model_copy(
                        deep=True,
                        update={
                            "selected": True,
                            "selection_outcome": "selected_by_budget_adjustment",
                        },
                    )
                )
                replacements.append(ActivityReplacement(
                    date=day.date,
                    time_slot=selected.time_slot,
                    before_candidate_id=selected.candidate_id,
                    after_candidate_id=replacement.candidate_id,
                    before_cost=_money(selected.price * state.preferences.num_travelers),
                    after_cost=_money(replacement.price * state.preferences.num_travelers),
                    selection_reason=selection_reason,
                ))
                for decision in state.activity_result.weather_decisions:
                    if (
                        decision.date == day.date
                        and decision.time_slot == selected.time_slot
                    ):
                        decision.final_candidate_id = replacement.candidate_id
                        decision.final_candidate_name = replacement.name
                        decision.budget_adjusted = True
                        decision.budget_reason = selection_reason
                        break
                for decision in state.activity_result.pace_decisions:
                    if (
                        decision.date == day.date
                        and decision.time_slot == selected.time_slot
                    ):
                        decision.candidate_id = replacement.candidate_id
                        decision.candidate_name = replacement.name
                        decision.reason = selection_reason
                        break

        recalculate_selected_costs(state)
        after_cost = state.activity_result.total_activity_cost
        after_ids = [
            activity.candidate_id
            for day in state.activity_result.day_plans
            for activity in day.activities
        ]
        return self._history(
            state,
            round_number=round_number,
            target=BudgetComponent.ACTIVITIES,
            before_ids=before_ids,
            after_ids=after_ids,
            before_cost=before_cost,
            after_cost=after_cost,
            replacements=replacements,
        )

    def _optimize_hotel(
        self, state: TravelPlanState, round_number: int
    ) -> BudgetAdjustment:
        assert state.hotel_result is not None
        selected = state.hotel_result.recommended
        assert selected is not None
        snapshot = state.candidate_snapshot
        assert snapshot is not None
        destination = state.selected_destination
        if destination is None:
            raise ValueError("selected destination is required")
        before_cost = state.hotel_result.total_hotel_cost
        rooms = ceil(state.preferences.num_travelers / 2)
        nights = (
            date.fromisoformat(state.preferences.end_date)
            - date.fromisoformat(state.preferences.start_date)
        ).days
        eligible = [
            candidate
            for candidate in snapshot.hotels
            if candidate.city == destination.city
            and candidate.check_in == state.preferences.start_date
            and candidate.check_out == state.preferences.end_date
            and candidate.user_rating >= settings.BUDGET_MIN_HOTEL_RATING
            and candidate.price_per_night * nights * rooms < before_cost
        ]
        if eligible:
            replacement = min(
                eligible,
                key=lambda item: (
                    item.price_per_night * nights * rooms,
                    -item.user_rating,
                    item.distance_to_center_km,
                    item.candidate_id,
                ),
            )
            state.hotel_result.recommended = replacement.model_copy(deep=True)

        recalculate_selected_costs(state)
        after_cost = state.hotel_result.total_hotel_cost
        after_id = state.hotel_result.recommended.candidate_id
        return self._history(
            state,
            round_number=round_number,
            target=BudgetComponent.HOTEL,
            before_ids=[selected.candidate_id],
            after_ids=[after_id],
            before_cost=before_cost,
            after_cost=after_cost,
        )

    @staticmethod
    def _cheaper_flight(
        selected: Flight,
        candidates: tuple[Flight, ...],
        *,
        departure_city: str,
        arrival_city: str,
        travel_date: str,
    ) -> Flight:
        eligible = [
            candidate
            for candidate in candidates
            if candidate.departure_city == departure_city
            and candidate.arrival_city == arrival_city
            and candidate.departure_time.startswith(travel_date)
            and candidate.cabin_class == selected.cabin_class
            and candidate.price < selected.price
        ]
        if not eligible:
            return selected
        return min(
            eligible,
            key=lambda item: (
                item.price,
                item.stops,
                item.duration_hours,
                item.candidate_id,
            ),
        )

    def _optimize_flights(
        self, state: TravelPlanState, round_number: int
    ) -> BudgetAdjustment:
        assert state.flight_result is not None
        outbound = state.flight_result.recommended_outbound
        return_flight = state.flight_result.recommended_return
        assert outbound is not None and return_flight is not None
        snapshot = state.candidate_snapshot
        assert snapshot is not None
        destination = state.selected_destination
        if destination is None:
            raise ValueError("selected destination is required")
        before_cost = state.flight_result.total_flight_cost
        new_outbound = self._cheaper_flight(
            outbound,
            snapshot.outbound_flights,
            departure_city=state.preferences.departure_city,
            arrival_city=destination.city,
            travel_date=state.preferences.start_date,
        )
        new_return = self._cheaper_flight(
            return_flight,
            snapshot.return_flights,
            departure_city=destination.city,
            arrival_city=state.preferences.departure_city,
            travel_date=state.preferences.end_date,
        )
        state.flight_result.recommended_outbound = new_outbound.model_copy(deep=True)
        state.flight_result.recommended_return = new_return.model_copy(deep=True)

        recalculate_selected_costs(state)
        after_cost = state.flight_result.total_flight_cost
        return self._history(
            state,
            round_number=round_number,
            target=BudgetComponent.FLIGHTS,
            before_ids=[outbound.candidate_id, return_flight.candidate_id],
            after_ids=[new_outbound.candidate_id, new_return.candidate_id],
            before_cost=before_cost,
            after_cost=after_cost,
        )
