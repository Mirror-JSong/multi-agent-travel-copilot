"""确定性 Mock Provider、Agent 推荐和 Pipeline 可复现性。"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agents.activity_agent import ActivityAgent
from models.schemas import (
    Activity,
    ActivitySearchRequest,
    DataSource,
    Destination,
    DestinationRecommendation,
    Flight,
    FlightSearchRequest,
    Hotel,
    HotelSearchRequest,
    TravelPlanState,
    TravelStyle,
    UserPreferences,
)
from orchestrator.pipeline import quick_plan
from tools.activity_search import MockActivityProvider
from tools.flight_search import MockFlightProvider
from tools.hotel_search import MockHotelProvider
from tools.providers import ActivityProvider, FlightProvider, HotelProvider


PYTHON_ROOT = Path(__file__).resolve().parent.parent


def _dump_models(models) -> str:
    return json.dumps(
        [model.model_dump(mode="json") for model in models],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _activity_state(interests: list[str]) -> TravelPlanState:
    destination = Destination(city="东京", country="日本")
    return TravelPlanState(
        preferences=UserPreferences(
            budget=50_000,
            departure_city="北京",
            start_date="2026-05-01",
            end_date="2026-05-05",
            interests=interests,
        ),
        destination_rec=DestinationRecommendation(
            destinations=[destination],
            selected=destination,
        ),
    )


@pytest.fixture(params=["flight", "hotel", "activity"])
def provider_case(request):
    if request.param == "flight":
        return (
            MockFlightProvider(),
            FlightSearchRequest(
                departure_city="北京",
                arrival_city="东京",
                travel_date="2026-05-01",
            ),
            Flight,
            FlightProvider,
        )
    if request.param == "hotel":
        return (
            MockHotelProvider(),
            HotelSearchRequest(
                city="东京",
                check_in="2026-05-01",
                check_out="2026-05-05",
                travel_style=TravelStyle.COMFORT,
            ),
            Hotel,
            HotelProvider,
        )
    return (
        MockActivityProvider(),
        ActivitySearchRequest(
            city="东京",
            start_date="2026-05-01",
            end_date="2026-05-05",
        ),
        Activity,
        ActivityProvider,
    )


@pytest.mark.asyncio
async def test_same_provider_input_is_identical_across_five_calls(provider_case):
    provider, query, _, _ = provider_case

    results = [_dump_models(await provider.search(query)) for _ in range(5)]

    assert len(set(results)) == 1


@pytest.mark.asyncio
async def test_concurrent_provider_calls_are_identical(provider_case):
    provider, query, _, _ = provider_case

    concurrent_results = await asyncio.gather(
        *(provider.search(query) for _ in range(10))
    )

    assert len({_dump_models(result) for result in concurrent_results}) == 1


@pytest.mark.asyncio
async def test_provider_results_follow_schema_and_source_contract(provider_case):
    provider, query, candidate_type, protocol_type = provider_case

    candidates = await provider.search(query)

    assert isinstance(provider, protocol_type)
    assert candidates
    assert all(isinstance(candidate, candidate_type) for candidate in candidates)
    assert all(candidate.candidate_id for candidate in candidates)
    assert len({candidate.candidate_id for candidate in candidates}) == len(candidates)
    assert all(candidate.source == DataSource.MOCK for candidate in candidates)
    assert all(candidate.source_version == provider.dataset_version for candidate in candidates)
    if isinstance(query, FlightSearchRequest):
        assert all(query.travel_date in candidate.departure_time for candidate in candidates)
    elif isinstance(query, HotelSearchRequest):
        assert all(candidate.city == query.city for candidate in candidates)
        assert all(candidate.check_in == query.check_in for candidate in candidates)
        assert all(candidate.check_out == query.check_out for candidate in candidates)
    else:
        assert all(candidate.location == query.city for candidate in candidates)
        assert all(
            candidate.available_start_date == query.start_date
            and candidate.available_end_date == query.end_date
            for candidate in candidates
        )


@pytest.mark.asyncio
async def test_mutating_returned_object_does_not_pollute_provider_data():
    flight_provider = MockFlightProvider()
    flight_query = FlightSearchRequest(
        departure_city="北京",
        arrival_city="东京",
        travel_date="2026-05-01",
    )
    original_flights = await flight_provider.search(flight_query)
    original_flight_price = original_flights[0].price
    original_flights[0].price = 1
    assert (await flight_provider.search(flight_query))[0].price == original_flight_price

    hotel_provider = MockHotelProvider()
    hotel_query = HotelSearchRequest(
        city="东京",
        check_in="2026-05-01",
        check_out="2026-05-05",
    )
    original_hotels = await hotel_provider.search(hotel_query)
    original_amenities = list(original_hotels[0].amenities)
    original_hotels[0].amenities.append("污染值")
    assert (await hotel_provider.search(hotel_query))[0].amenities == original_amenities

    activity_provider = MockActivityProvider()
    activity_query = ActivitySearchRequest(
        city="东京",
        start_date="2026-05-01",
        end_date="2026-05-05",
    )
    original_activities = await activity_provider.search(activity_query)
    original_description = original_activities[0].description
    original_activities[0].description = "污染值"
    assert (
        await activity_provider.search(activity_query)
    )[0].description == original_description


@pytest.mark.asyncio
async def test_relevant_query_changes_produce_designed_candidate_differences():
    flight_provider = MockFlightProvider()
    may_flights = await flight_provider.search(FlightSearchRequest(
        departure_city="北京",
        arrival_city="东京",
        travel_date="2026-05-01",
    ))
    june_flights = await flight_provider.search(FlightSearchRequest(
        departure_city="北京",
        arrival_city="东京",
        travel_date="2026-06-01",
    ))
    seoul_flights = await flight_provider.search(FlightSearchRequest(
        departure_city="北京",
        arrival_city="首尔",
        travel_date="2026-05-01",
    ))
    assert _dump_models(may_flights) != _dump_models(june_flights)
    assert _dump_models(may_flights) != _dump_models(seoul_flights)

    hotel_provider = MockHotelProvider()
    comfort_hotels = await hotel_provider.search(HotelSearchRequest(
        city="东京",
        check_in="2026-05-01",
        check_out="2026-05-05",
        travel_style=TravelStyle.COMFORT,
    ))
    luxury_hotels = await hotel_provider.search(HotelSearchRequest(
        city="东京",
        check_in="2026-05-01",
        check_out="2026-05-05",
        travel_style=TravelStyle.LUXURY,
    ))
    assert _dump_models(comfort_hotels) != _dump_models(luxury_hotels)

    activity_provider = MockActivityProvider()
    tokyo_activities = await activity_provider.search(ActivitySearchRequest(
        city="东京",
        start_date="2026-05-01",
        end_date="2026-05-05",
    ))
    bangkok_activities = await activity_provider.search(ActivitySearchRequest(
        city="曼谷",
        start_date="2026-05-01",
        end_date="2026-05-05",
    ))
    assert _dump_models(tokyo_activities) != _dump_models(bangkok_activities)


@pytest.mark.asyncio
async def test_activity_interest_changes_agent_recommendation_not_provider_candidates():
    provider = MockActivityProvider()
    query = ActivitySearchRequest(
        city="东京",
        start_date="2026-05-01",
        end_date="2026-05-05",
    )
    candidates_before = _dump_models(await provider.search(query))

    kimono_result = await ActivityAgent(provider=provider).run(_activity_state(["和服"]))
    temple_result = await ActivityAgent(provider=provider).run(_activity_state(["浅草寺"]))

    assert kimono_result.activity_result.day_plans[0].activities[0].name == "和服体验"
    assert temple_result.activity_result.day_plans[0].activities[0].name == "浅草寺"
    assert _dump_models(await provider.search(query)) == candidates_before


def test_all_providers_are_stable_across_python_processes_and_hash_seeds():
    script = r'''
import asyncio
import json
from models.schemas import ActivitySearchRequest, FlightSearchRequest, HotelSearchRequest
from tools.activity_search import MockActivityProvider
from tools.flight_search import MockFlightProvider
from tools.hotel_search import MockHotelProvider

async def main():
    result = {
        "flight": await MockFlightProvider().search(FlightSearchRequest(
            departure_city="北京",
            arrival_city="东京",
            travel_date="2026-05-01",
        )),
        "hotel": await MockHotelProvider().search(HotelSearchRequest(
            city="东京",
            check_in="2026-05-01",
            check_out="2026-05-05",
        )),
        "activity": await MockActivityProvider().search(ActivitySearchRequest(
            city="东京",
            start_date="2026-05-01",
            end_date="2026-05-05",
        )),
    }
    print(json.dumps(
        {
            key: [item.model_dump(mode="json") for item in candidates]
            for key, candidates in result.items()
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ))

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


@pytest.mark.asyncio
async def test_dataset_version_changes_the_stable_candidate_set():
    query = FlightSearchRequest(
        departure_city="北京",
        arrival_city="东京",
        travel_date="2026-05-01",
    )

    version_one = await MockFlightProvider("dataset-v1").search(query)
    version_two = await MockFlightProvider("dataset-v2").search(query)

    assert _dump_models(version_one) != _dump_models(version_two)
    assert {item.source_version for item in version_one} == {"dataset-v1"}
    assert {item.source_version for item in version_two} == {"dataset-v2"}


@pytest.mark.asyncio
async def test_complete_high_budget_pipeline_is_identical_across_five_runs():
    serialized_states = []
    for _ in range(5):
        state = await quick_plan(
            budget=50_000,
            departure="北京",
            start="2026-05-01",
            end="2026-05-05",
            style="comfort",
            travelers=1,
        )
        serialized_states.append(state.model_dump_json())

    assert len(set(serialized_states)) == 1
