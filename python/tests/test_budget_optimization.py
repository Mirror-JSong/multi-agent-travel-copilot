"""Candidate-based, cumulative budget optimization acceptance tests."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agents.activity_agent import ActivityAgent
from agents.flight_agent import FlightAgent
from agents.hotel_agent import HotelAgent
from config.settings import settings
from models.costing import recalculate_selected_costs
from models.schemas import (
    Activity,
    BudgetComponent,
    DataSource,
    Destination,
    DestinationRecommendation,
    Flight,
    Hotel,
    PlanningState,
    TravelPlanState,
    UserPreferences,
)
from orchestrator.budget_loop import BudgetLoopController
from orchestrator.parallel import ParallelExecutor


PYTHON_ROOT = Path(__file__).resolve().parent.parent
DATASET = "budget-fixture-v1"


def _flight(candidate_id: str, request, price: float, duration: float, stops: int) -> Flight:
    return Flight(
        candidate_id=candidate_id,
        source=DataSource.MOCK,
        source_version=DATASET,
        airline="Fixture Air",
        flight_no=candidate_id,
        departure_city=request.departure_city,
        arrival_city=request.arrival_city,
        departure_time=f"{request.travel_date}T08:00:00",
        arrival_time=f"{request.travel_date}T20:00:00",
        price=price,
        duration_hours=duration,
        stops=stops,
        cabin_class=request.cabin_class,
    )


class FixedFlightProvider:
    async def search(self, request):
        prefix = "out" if request.departure_city == "Origin" else "return"
        return [
            _flight(f"{prefix}-expensive", request, 500.0, 1.0, 0),
            _flight(f"{prefix}-economy", request, 300.0, 12.0, 2),
        ]


class FixedHotelProvider:
    async def search(self, request):
        common = dict(
            source=DataSource.MOCK,
            source_version=DATASET,
            city=request.city,
            check_in=request.check_in,
            check_out=request.check_out,
        )
        return [
            Hotel(
                candidate_id="hotel-expensive",
                name="Central Fixture Hotel",
                star_rating=3.5,
                user_rating=9.5,
                price_per_night=500.0,
                distance_to_center_km=0.1,
                **common,
            ),
            Hotel(
                candidate_id="hotel-economy",
                name="Economy Fixture Hotel",
                star_rating=2.0,
                user_rating=7.5,
                price_per_night=200.0,
                distance_to_center_km=5.0,
                **common,
            ),
        ]


class FixedActivityProvider:
    async def search(self, request):
        candidates = []
        for slot in ("morning", "afternoon", "evening"):
            common = dict(
                source=DataSource.MOCK,
                source_version=DATASET,
                category="sightseeing",
                location=request.city,
                available_start_date=request.start_date,
                available_end_date=request.end_date,
                duration_hours=2.0,
                time_slot=slot,
            )
            candidates.extend([
                Activity(
                    candidate_id=f"activity-{slot}-expensive",
                    name=f"Premium {slot}",
                    price=100.0,
                    rating=9.5,
                    **common,
                ),
                Activity(
                    candidate_id=f"activity-{slot}-economy",
                    name=f"Economy {slot}",
                    price=10.0,
                    rating=7.5,
                    **common,
                ),
            ])
        return candidates


async def _run_fixed_budget(
    budget: float,
    max_retries: int = 3,
    activity_provider=None,
    flight_provider=None,
    hotel_provider=None,
    start_date: str = "2026-05-01",
    end_date: str = "2026-05-03",
    travelers: int = 2,
    interests: list[str] | None = None,
) -> TravelPlanState:
    destination = Destination(city="Destination", country="Fixture")
    state = TravelPlanState(
        preferences=UserPreferences(
            budget=budget,
            departure_city="Origin",
            start_date=start_date,
            end_date=end_date,
            num_travelers=travelers,
            interests=interests or [],
        ),
        destination_rec=DestinationRecommendation(
            destinations=[destination],
            selected=destination,
        ),
    )
    executor = ParallelExecutor([
        FlightAgent(flight_provider or FixedFlightProvider()),
        HotelAgent(hotel_provider or FixedHotelProvider()),
        ActivityAgent(activity_provider or FixedActivityProvider()),
    ])
    return await BudgetLoopController(
        executor,
        max_retries=max_retries,
    ).run(state)


def _assert_total_invariant(state: TravelPlanState) -> None:
    assert state.budget_breakdown is not None
    breakdown = state.budget_breakdown
    assert breakdown.total_cost == (
        breakdown.flight_cost + breakdown.hotel_cost + breakdown.activity_cost
    )
    assert state.final_total_cost == breakdown.total_cost


@pytest.mark.asyncio
async def test_a_initial_plan_within_budget_skips_adjustment_and_preserves_snapshot():
    state = await _run_fixed_budget(5_000.0)

    assert state.state == PlanningState.COMPLETED
    assert state.adjustment_round == 0
    assert state.adjustment_history == []
    assert state.initial_total_cost == 4_200.0
    assert state.final_total_cost == 4_200.0
    assert state.candidate_snapshot is not None
    assert {flight.price for flight in state.candidate_snapshot.outbound_flights} == {300.0, 500.0}
    assert {hotel.price_per_night for hotel in state.candidate_snapshot.hotels} == {200.0, 500.0}
    assert {activity.price for activity in state.candidate_snapshot.activities} == {10.0, 100.0}
    _assert_total_invariant(state)


@pytest.mark.asyncio
async def test_b_round_one_reselects_real_activities_and_records_savings():
    state = await _run_fixed_budget(3_500.0, interests=["sightseeing"])

    assert state.state == PlanningState.COMPLETED
    assert state.adjustment_round == 1
    assert state.budget_breakdown.activity_cost == 120.0
    assert all(
        activity.candidate_id.endswith("-economy")
        for day in state.activity_result.day_plans
        for activity in day.activities
    )
    assert all(
        [activity.time_slot for activity in day.activities]
        == ["morning", "afternoon", "evening"]
        for day in state.activity_result.day_plans
    )
    assert [day.date for day in state.activity_result.day_plans] == [
        "2026-05-01",
        "2026-05-02",
    ]
    assert all(
        activity.location == "Destination"
        and activity.category == "sightseeing"
        and activity.available_start_date <= day.date <= activity.available_end_date
        for day in state.activity_result.day_plans
        for activity in day.activities
    )
    history = state.adjustment_history[0]
    assert history.target == BudgetComponent.ACTIVITIES
    assert history.before_cost == 1_200.0
    assert history.after_cost == 120.0
    assert history.saved_amount == 1_080.0
    assert history.current_total == 3_120.0
    assert history.budget_remaining == 380.0
    assert history.improved is True
    assert len(history.activity_replacements) == 6
    assert {activity.price for activity in state.candidate_snapshot.activities} == {10.0, 100.0}
    _assert_total_invariant(state)


@pytest.mark.asyncio
async def test_c_round_two_preserves_activity_changes_and_reselects_hotel():
    state = await _run_fixed_budget(2_800.0)

    assert state.state == PlanningState.COMPLETED
    assert state.adjustment_round == 2
    assert state.budget_breakdown.activity_cost == 120.0
    assert state.hotel_result.recommended.candidate_id == "hotel-economy"
    assert state.hotel_result.recommended.candidate_id in {
        item.candidate_id for item in state.candidate_snapshot.hotels
    }
    assert state.hotel_result.recommended.check_in == "2026-05-01"
    assert state.hotel_result.recommended.check_out == "2026-05-03"
    assert state.hotel_result.recommended.user_rating >= settings.BUDGET_MIN_HOTEL_RATING
    assert state.budget_breakdown.hotel_cost == 400.0  # 200 x 2 nights x 1 room
    hotel_history = state.adjustment_history[1]
    assert hotel_history.target == BudgetComponent.HOTEL
    assert hotel_history.before_candidate_ids == ["hotel-expensive"]
    assert hotel_history.after_candidate_ids == ["hotel-economy"]
    assert hotel_history.before_cost == 1_000.0
    assert hotel_history.after_cost == 400.0
    assert hotel_history.saved_amount == 600.0
    assert hotel_history.current_total == 2_520.0
    assert state.adjustment_history[0].after_candidate_ids == [
        activity.candidate_id
        for day in state.activity_result.day_plans
        for activity in day.activities
    ]
    assert {hotel.price_per_night for hotel in state.candidate_snapshot.hotels} == {200.0, 500.0}
    _assert_total_invariant(state)


@pytest.mark.asyncio
async def test_d_round_three_preserves_prior_changes_and_reselects_both_flights():
    state = await _run_fixed_budget(2_000.0)

    assert state.state == PlanningState.COMPLETED
    assert state.adjustment_round == 3
    assert state.budget_breakdown.activity_cost == 120.0
    assert state.hotel_result.recommended.candidate_id == "hotel-economy"
    assert state.flight_result.recommended_outbound.candidate_id == "out-economy"
    assert state.flight_result.recommended_return.candidate_id == "return-economy"
    outbound = state.flight_result.recommended_outbound
    return_flight = state.flight_result.recommended_return
    assert (
        outbound.departure_city,
        outbound.arrival_city,
        outbound.departure_time[:10],
        outbound.cabin_class,
    ) == ("Origin", "Destination", "2026-05-01", "economy")
    assert (
        return_flight.departure_city,
        return_flight.arrival_city,
        return_flight.departure_time[:10],
        return_flight.cabin_class,
    ) == ("Destination", "Origin", "2026-05-03", "economy")
    assert state.budget_breakdown.flight_cost == 1_200.0  # (300 + 300) x 2 people
    flight_history = state.adjustment_history[2]
    assert flight_history.target == BudgetComponent.FLIGHTS
    assert flight_history.before_cost == 2_000.0
    assert flight_history.after_cost == 1_200.0
    assert flight_history.saved_amount == 800.0
    assert flight_history.current_total == 1_720.0
    assert {item.price for item in state.candidate_snapshot.outbound_flights} == {300.0, 500.0}
    assert {item.price for item in state.candidate_snapshot.return_flights} == {300.0, 500.0}
    _assert_total_invariant(state)


@pytest.mark.asyncio
async def test_e_impossible_budget_is_business_result_not_false_success():
    state = await _run_fixed_budget(100.0)

    assert state.state == PlanningState.BUDGET_INFEASIBLE
    assert state.adjustment_round == 3
    assert state.agent_failures == []
    assert state.budget_breakdown.is_within_budget is False
    assert state.final_total_cost == 1_720.0
    assert state.final_total_cost > state.preferences.budget
    assert len(state.adjustment_history) == 3
    assert "current staged search strategy did not find" in state.status_message
    assert all(item.saved_amount >= 0 for item in state.adjustment_history)

    snapshot_ids = {
        candidate.candidate_id
        for group in (
            state.candidate_snapshot.outbound_flights,
            state.candidate_snapshot.return_flights,
            state.candidate_snapshot.hotels,
            state.candidate_snapshot.activities,
        )
        for candidate in group
    }
    selected_ids = {
        state.flight_result.recommended_outbound.candidate_id,
        state.flight_result.recommended_return.candidate_id,
        state.hotel_result.recommended.candidate_id,
        *(
            activity.candidate_id
            for day in state.activity_result.day_plans
            for activity in day.activities
        ),
    }
    assert selected_ids <= snapshot_ids
    _assert_total_invariant(state)


@pytest.mark.asyncio
async def test_no_lower_candidate_is_recorded_and_next_round_still_runs():
    class NoLowerActivityProvider(FixedActivityProvider):
        async def search(self, request):
            return [
                item
                for item in await super().search(request)
                if item.candidate_id.endswith("-expensive")
            ]

    state = await _run_fixed_budget(
        3_500.0,
        activity_provider=NoLowerActivityProvider(),
    )

    assert state.adjustment_history[0].target == BudgetComponent.ACTIVITIES
    assert state.adjustment_history[0].improved is False
    assert state.adjustment_history[0].saved_amount == 0.0
    assert state.adjustment_history[0].before_candidate_ids == state.adjustment_history[0].after_candidate_ids
    assert state.adjustment_history[0].before_cost == state.adjustment_history[0].after_cost
    assert state.adjustment_history[0].current_total == 4_200.0
    assert state.adjustment_history[1].target == BudgetComponent.HOTEL
    assert state.adjustment_history[2].target == BudgetComponent.FLIGHTS
    assert state.adjustment_history[1].current_total <= state.adjustment_history[0].current_total
    assert state.adjustment_history[2].current_total <= state.adjustment_history[1].current_total
    assert state.state == PlanningState.COMPLETED


@pytest.mark.asyncio
async def test_s7_three_travelers_four_nights_reconciles_every_component():
    state = await _run_fixed_budget(
        4_000.0,
        start_date="2026-05-01",
        end_date="2026-05-05",
        travelers=3,
    )

    assert state.state == PlanningState.COMPLETED
    assert state.adjustment_round == 3
    assert state.initial_total_cost == 10_600.0
    assert state.budget_breakdown.flight_cost == 1_800.0  # (300 + 300) x 3
    assert state.budget_breakdown.hotel_cost == 1_600.0  # 200 x 4 nights x 2 rooms
    assert state.budget_breakdown.activity_cost == 360.0  # 10 x 3 slots x 4 days x 3
    assert state.budget_breakdown.total_cost == 3_760.0
    assert [item.saved_amount for item in state.adjustment_history] == [
        3_240.0,
        2_400.0,
        1_200.0,
    ]
    assert [item.current_total for item in state.adjustment_history] == [
        7_360.0,
        4_960.0,
        3_760.0,
    ]
    _assert_total_invariant(state)


@pytest.mark.asyncio
async def test_s8_cheapest_candidates_that_violate_hard_constraints_are_rejected():
    class InvalidCheapestFlightProvider:
        async def search(self, request):
            prefix = "out" if request.departure_city == "Origin" else "return"
            valid = _flight(f"{prefix}-valid", request, 500.0, 1.0, 0)
            wrong_date = _flight(f"{prefix}-wrong-date", request, 100.0, 20.0, 3)
            wrong_date.departure_time = "1999-01-01T08:00:00"
            wrong_route = _flight(f"{prefix}-wrong-route", request, 90.0, 20.0, 3)
            wrong_route.arrival_city = "Unauthorized"
            wrong_cabin = _flight(f"{prefix}-wrong-cabin", request, 80.0, 20.0, 3)
            wrong_cabin.cabin_class = "first"
            return [valid, wrong_date, wrong_route, wrong_cabin]

    class InvalidCheapestHotelProvider:
        async def search(self, request):
            common = dict(
                source=DataSource.MOCK,
                source_version=DATASET,
                city=request.city,
                check_in=request.check_in,
                check_out=request.check_out,
            )
            valid = Hotel(
                candidate_id="hotel-valid",
                name="Valid Hotel",
                star_rating=3.5,
                user_rating=9.5,
                price_per_night=500.0,
                distance_to_center_km=0.1,
                **common,
            )
            wrong_dates = Hotel(
                candidate_id="hotel-wrong-dates",
                name="Wrong Dates Hotel",
                star_rating=1.0,
                user_rating=0.0,
                price_per_night=10.0,
                distance_to_center_km=50.0,
                **{**common, "check_in": "1999-01-01", "check_out": "1999-01-02"},
            )
            low_rating = Hotel(
                candidate_id="hotel-low-rating",
                name="Low Rating Hotel",
                star_rating=1.0,
                user_rating=1.0,
                price_per_night=20.0,
                distance_to_center_km=50.0,
                **common,
            )
            return [valid, wrong_dates, low_rating]

    class InvalidCheapestActivityProvider:
        async def search(self, request):
            candidates = []
            for slot in ("morning", "afternoon", "evening"):
                common = dict(
                    source=DataSource.MOCK,
                    source_version=DATASET,
                    location=request.city,
                    available_start_date=request.start_date,
                    available_end_date=request.end_date,
                    duration_hours=2.0,
                )
                candidates.extend([
                    Activity(
                        candidate_id=f"activity-{slot}-valid",
                        name=f"Museum {slot}",
                        category="museum",
                        price=100.0,
                        rating=9.5,
                        time_slot=slot,
                        **common,
                    ),
                    Activity(
                        candidate_id=f"activity-{slot}-wrong-interest",
                        name=f"Shopping {slot}",
                        category="shopping",
                        price=1.0,
                        rating=1.0,
                        time_slot=slot,
                        **common,
                    ),
                ])
            candidates.append(Activity(
                candidate_id="activity-wrong-slot",
                name="Museum at invalid slot",
                category="museum",
                price=0.5,
                rating=1.0,
                location=request.city,
                available_start_date=request.start_date,
                available_end_date=request.end_date,
                duration_hours=2.0,
                time_slot="night",
                source=DataSource.MOCK,
                source_version=DATASET,
            ))
            return candidates

    state = await _run_fixed_budget(
        100.0,
        flight_provider=InvalidCheapestFlightProvider(),
        hotel_provider=InvalidCheapestHotelProvider(),
        activity_provider=InvalidCheapestActivityProvider(),
        interests=["museum"],
    )

    assert state.state == PlanningState.BUDGET_INFEASIBLE
    assert [item.improved for item in state.adjustment_history] == [False, False, False]
    assert [item.saved_amount for item in state.adjustment_history] == [0.0, 0.0, 0.0]
    assert state.hotel_result.recommended.candidate_id == "hotel-valid"
    assert state.flight_result.recommended_outbound.candidate_id == "out-valid"
    assert state.flight_result.recommended_return.candidate_id == "return-valid"
    assert all(
        activity.candidate_id.endswith("-valid")
        for day in state.activity_result.day_plans
        for activity in day.activities
    )
    rejected_ids = {
        "hotel-wrong-dates",
        "hotel-low-rating",
        "out-wrong-date",
        "out-wrong-route",
        "out-wrong-cabin",
        "return-wrong-date",
        "return-wrong-route",
        "return-wrong-cabin",
        "activity-wrong-slot",
        "activity-morning-wrong-interest",
        "activity-afternoon-wrong-interest",
        "activity-evening-wrong-interest",
    }
    assert rejected_ids.isdisjoint({
        candidate_id
        for item in state.adjustment_history
        for candidate_id in item.after_candidate_ids
    })
    _assert_total_invariant(state)


@pytest.mark.asyncio
async def test_fractional_amounts_are_rounded_and_reconciled_at_cost_boundary():
    state = await _run_fixed_budget(
        20_000.0,
        start_date="2026-05-01",
        end_date="2026-05-05",
        travelers=3,
    )
    state.flight_result.recommended_outbound = state.flight_result.recommended_outbound.model_copy(
        deep=True, update={"price": 0.1}
    )
    state.flight_result.recommended_return = state.flight_result.recommended_return.model_copy(
        deep=True, update={"price": 0.2}
    )
    state.hotel_result.recommended = state.hotel_result.recommended.model_copy(
        deep=True, update={"price_per_night": 0.1}
    )
    for day in state.activity_result.day_plans:
        day.activities = [
            activity.model_copy(deep=True, update={"price": 0.1})
            for activity in day.activities
        ]

    flight, hotel, activity, total = recalculate_selected_costs(state)

    assert (flight, hotel, activity, total) == (0.9, 0.8, 3.6, 5.3)
    assert all(day.day_cost == 0.9 for day in state.activity_result.day_plans)


@pytest.mark.asyncio
async def test_concurrent_planning_requests_have_independent_history_and_selections():
    completed, infeasible = await asyncio.gather(
        _run_fixed_budget(2_000.0),
        _run_fixed_budget(100.0),
    )

    assert completed is not infeasible
    assert completed.adjustment_history is not infeasible.adjustment_history
    assert completed.candidate_snapshot is not infeasible.candidate_snapshot
    assert completed.state == PlanningState.COMPLETED
    assert infeasible.state == PlanningState.BUDGET_INFEASIBLE
    completed.adjustment_history.pop()
    completed.flight_result.recommended_outbound.price = 1.0
    assert len(infeasible.adjustment_history) == 3
    assert infeasible.flight_result.recommended_outbound.price == 300.0
    assert infeasible.candidate_snapshot.outbound_flights[0].price in {300.0, 500.0}


@pytest.mark.asyncio
async def test_adjustment_rounds_reuse_one_provider_candidate_snapshot():
    class CountingFlightProvider(FixedFlightProvider):
        calls = 0

        async def search(self, request):
            self.calls += 1
            return await super().search(request)

    class CountingHotelProvider(FixedHotelProvider):
        calls = 0

        async def search(self, request):
            self.calls += 1
            return await super().search(request)

    class CountingActivityProvider(FixedActivityProvider):
        calls = 0

        async def search(self, request):
            self.calls += 1
            return await super().search(request)

    flight_provider = CountingFlightProvider()
    hotel_provider = CountingHotelProvider()
    activity_provider = CountingActivityProvider()
    destination = Destination(city="Destination", country="Fixture")
    state = TravelPlanState(
        preferences=UserPreferences(
            budget=100.0,
            departure_city="Origin",
            start_date="2026-05-01",
            end_date="2026-05-03",
            num_travelers=2,
        ),
        destination_rec=DestinationRecommendation(
            destinations=[destination],
            selected=destination,
        ),
    )
    executor = ParallelExecutor([
        FlightAgent(flight_provider),
        HotelAgent(hotel_provider),
        ActivityAgent(activity_provider),
    ])

    result = await BudgetLoopController(executor).run(state)

    assert result.adjustment_round == 3
    assert flight_provider.calls == 2  # outbound and return, once each
    assert hotel_provider.calls == 1
    assert activity_provider.calls == 1


@pytest.mark.asyncio
async def test_budget_optimization_is_identical_across_five_runs():
    outputs = [(await _run_fixed_budget(100.0)).model_dump_json() for _ in range(5)]
    assert len(set(outputs)) == 1


def test_complete_over_budget_pipeline_is_stable_across_python_processes():
    script = r'''
import asyncio
from orchestrator.pipeline import quick_plan

async def main():
    state = await quick_plan(
        budget=100.0,
        departure="Beijing",
        start="2026-05-01",
        end="2026-05-05",
        style="comfort",
        travelers=2,
    )
    print(state.model_dump_json())

asyncio.run(main())
'''
    outputs = []
    for hash_seed in ("1", "987654"):
        env = os.environ.copy()
        env["PYTHONHASHSEED"] = hash_seed
        env["PYTHONIOENCODING"] = "utf-8"
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=PYTHON_ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        outputs.append(completed.stdout.strip())

    assert outputs[0]
    assert outputs[0] == outputs[1]
