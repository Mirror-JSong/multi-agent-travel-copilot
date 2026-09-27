"""Canonical calculations for the currently selected travel plan."""

from __future__ import annotations

from datetime import date
from math import ceil

from .schemas import TravelPlanState


def _money(value: float) -> float:
    return round(float(value), 2)


def recalculate_selected_costs(state: TravelPlanState) -> tuple[float, float, float, float]:
    """Recalculate and persist every component from the actually selected items."""
    if state.preferences is None:
        raise ValueError("preferences are required for cost calculation")
    if state.flight_result is None or state.hotel_result is None or state.activity_result is None:
        raise ValueError("all planning results are required for cost calculation")

    outbound = state.flight_result.recommended_outbound
    return_flight = state.flight_result.recommended_return
    hotel = state.hotel_result.recommended
    if outbound is None or return_flight is None or hotel is None:
        raise ValueError("selected outbound flight, return flight, and hotel are required")

    travelers = state.preferences.num_travelers
    nights = (
        date.fromisoformat(state.preferences.end_date)
        - date.fromisoformat(state.preferences.start_date)
    ).days
    rooms = ceil(travelers / 2)

    flight_cost = _money((outbound.price + return_flight.price) * travelers)
    hotel_cost = _money(hotel.price_per_night * nights * rooms)

    activity_cost = 0.0
    for day_plan in state.activity_result.day_plans:
        day_plan.day_cost = _money(
            sum(activity.price for activity in day_plan.activities) * travelers
        )
        activity_cost += day_plan.day_cost
    activity_cost = _money(activity_cost)

    state.flight_result.total_flight_cost = flight_cost
    state.hotel_result.total_nights = nights
    state.hotel_result.total_hotel_cost = hotel_cost
    state.activity_result.total_activity_cost = activity_cost
    return flight_cost, hotel_cost, activity_cost, _money(
        flight_cost + hotel_cost + activity_cost
    )
