"""Stage B2 WeatherAgent, activity selection, and budget compatibility tests."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from agents.activity_agent import ActivityAgent
from agents.flight_agent import FlightAgent
from agents.hotel_agent import HotelAgent
from agents.weather_agent import WeatherAgent
from agents.weather_compatibility import (
    WeatherCompatibilityEvaluator,
    WeatherCompatibilityPolicy,
)
from api.app import app
from config.settings import settings
from models.costing import recalculate_selected_costs
from models.schemas import (
    Activity,
    ActivityEnvironment,
    ActivitySearchRequest,
    ActivitySearchResult,
    BudgetComponent,
    CandidateSnapshot,
    DailyWeather,
    DataSource,
    DayPlan,
    Destination,
    DestinationRecommendation,
    Flight,
    FlightSearchResult,
    Hotel,
    HotelSearchResult,
    PlanningState,
    TravelPlanState,
    UserPreferences,
    WeatherAvailability,
    WeatherCondition,
    WeatherPlanningStatus,
    WeatherResult,
    WeatherResultStatus,
    WeatherScenario,
    WeatherSensitivity,
)
from orchestrator.budget_optimizer import BudgetOptimizer
from orchestrator.pipeline import TravelPlanningPipeline
from tools.activity_search import MockActivityProvider
from tools.flight_search import MockFlightProvider
from tools.hotel_search import MockHotelProvider
from tools.weather_provider import MockWeatherProvider


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "weather_activity_scenarios.json"
DATASET = "weather-budget-fixture-v1"


@pytest.fixture(scope="module")
def b2_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _preferences(**updates) -> UserPreferences:
    values = {
        "budget": 50_000.0,
        "departure_city": "北京",
        "start_date": "2026-10-01",
        "end_date": "2026-10-05",
        "num_travelers": 1,
        "interests": ["经典景点"],
    }
    values.update(updates)
    return UserPreferences(**values)


def _state(*, city: str = "首尔", preferences: UserPreferences | None = None) -> TravelPlanState:
    destination = Destination(city=city, country="Fixture")
    return TravelPlanState(
        preferences=preferences or _preferences(),
        destination_rec=DestinationRecommendation(
            destinations=[destination],
            selected=destination,
        ),
    )


def _weather_result(
    *,
    city: str = "首尔",
    start: str = "2026-10-01",
    end: str = "2026-10-02",
    condition: WeatherCondition = WeatherCondition.HEAVY_RAIN,
    rain_probability: int = 95,
    severe: bool = True,
) -> WeatherResult:
    return WeatherResult(
        destination=city,
        start_date=start,
        end_date=end,
        scenario=WeatherScenario.RAINY,
        daily_weather=[DailyWeather(
            date=start,
            weather_condition=condition,
            rain_probability=rain_probability,
            temperature=22,
            severe_weather=severe,
            availability_status=WeatherAvailability.AVAILABLE,
        )],
        source=DataSource.MOCK,
        source_version="weather-budget-fixture-v1",
        is_mock=True,
        availability_status=WeatherResultStatus.AVAILABLE,
        availability_note="固定 B2 Mock 天气，不是实时预报。",
    )


def _activity(
    candidate_id: str,
    *,
    slot: str,
    price: float,
    environment: ActivityEnvironment,
    sensitivity: WeatherSensitivity,
    name: str | None = None,
) -> Activity:
    return Activity(
        candidate_id=candidate_id,
        source=DataSource.MOCK,
        source_version=DATASET,
        name=name or candidate_id,
        category="history",
        location="首尔",
        available_start_date="2026-10-01",
        available_end_date="2026-10-02",
        duration_hours=2,
        price=price,
        rating=9,
        description="history fixture",
        time_slot=slot,
        environment=environment,
        weather_sensitivity=sensitivity,
    )


async def _run_pipeline(scenario: WeatherScenario) -> TravelPlanState:
    pipeline = TravelPlanningPipeline(
        weather_agent=WeatherAgent(
            provider=MockWeatherProvider("weather-activity-b2-v1"),
            scenario=scenario,
        ),
        activity_agent=ActivityAgent(
            provider=MockActivityProvider("2026.09-activity-weather-v2"),
            require_weather_context=True,
        ),
    )
    return await pipeline.run(_preferences())


@pytest.mark.asyncio
async def test_t1_weather_agent_returns_validated_result_and_metadata() -> None:
    state = _state()

    result = await WeatherAgent(scenario=WeatherScenario.SUNNY).run(state)

    assert result.state != PlanningState.FAILED
    assert result.weather_result is not None
    assert isinstance(result.weather_result, WeatherResult)
    assert result.weather_result.destination == "首尔"
    assert result.weather_planning.status == WeatherPlanningStatus.READY
    assert result.weather_planning.fallback_used is False
    assert len(result.weather_result.daily_weather) == 4


class _GatedProvider:
    def __init__(self, label, delegate, started, all_started, release):
        self.label = label
        self.delegate = delegate
        self.started = started
        self.all_started = all_started
        self.release = release

    async def search(self, request):
        if self.label not in self.started:
            self.started.add(self.label)
            if len(self.started) == 3:
                self.all_started.set()
            await self.release.wait()
        return await self.delegate.search(request)


class _TrackingActivityAgent(ActivityAgent):
    def __init__(self, all_started: asyncio.Event):
        super().__init__(require_weather_context=True)
        self.all_started = all_started
        self.started = False

    async def execute(self, state):
        self.started = True
        assert self.all_started.is_set()
        assert state.weather_planning.status != WeatherPlanningStatus.NOT_STARTED
        return await super().execute(state)


@pytest.mark.asyncio
async def test_t2_t3_flight_hotel_weather_run_in_parallel_before_activity() -> None:
    started: set[str] = set()
    all_started = asyncio.Event()
    release = asyncio.Event()
    activity_agent = _TrackingActivityAgent(all_started)
    pipeline = TravelPlanningPipeline(
        flight_agent=FlightAgent(_GatedProvider(
            "flight", MockFlightProvider(), started, all_started, release
        )),
        hotel_agent=HotelAgent(_GatedProvider(
            "hotel", MockHotelProvider(), started, all_started, release
        )),
        weather_agent=WeatherAgent(_GatedProvider(
            "weather", MockWeatherProvider(), started, all_started, release
        )),
        activity_agent=activity_agent,
    )

    task = asyncio.create_task(pipeline.run(_preferences()))
    await asyncio.wait_for(all_started.wait(), timeout=2)
    assert started == {"flight", "hotel", "weather"}
    assert activity_agent.started is False
    release.set()
    result = await asyncio.wait_for(task, timeout=5)

    assert activity_agent.started is True
    assert result.state == PlanningState.COMPLETED


@pytest.mark.asyncio
async def test_t4_t5_t6_sunny_and_rainy_change_selection_on_same_candidates(
    b2_fixture,
) -> None:
    sunny, rainy = await asyncio.gather(
        _run_pipeline(WeatherScenario.SUNNY),
        _run_pipeline(WeatherScenario.RAINY),
    )

    sunny_candidates = [
        item.model_dump(mode="json") for item in sunny.candidate_snapshot.activities
    ]
    rainy_candidates = [
        item.model_dump(mode="json") for item in rainy.candidate_snapshot.activities
    ]
    assert sunny_candidates == rainy_candidates
    assert {item["source_version"] for item in sunny_candidates} == {
        b2_fixture["activity_dataset_version"]
    }

    sunny_selected = [
        activity
        for day in sunny.activity_result.day_plans
        for activity in day.activities
    ]
    rainy_selected = [
        activity
        for day in rainy.activity_result.day_plans
        for activity in day.activities
    ]
    assert {item.environment.value for item in sunny_selected} == set(
        b2_fixture["expected"]["sunny_environments"]
    )
    assert {item.environment.value for item in rainy_selected} == set(
        b2_fixture["expected"]["rainy_environments"]
    )
    assert [item.candidate_id for item in sunny_selected] != [
        item.candidate_id for item in rainy_selected
    ]
    assert all(item.weather_compatible is True for item in rainy_selected)
    assert all(
        evaluation.reason
        for evaluation in rainy.activity_result.weather_evaluations
        if not evaluation.hard_constraint_satisfied
    )


class _FixedActivityProvider:
    def __init__(self, candidates: list[Activity]):
        self.candidates = candidates

    async def search(self, request):
        return [item.model_copy(deep=True) for item in self.candidates]


@pytest.mark.asyncio
async def test_t7_t8_rainy_selection_keeps_interest_and_all_time_slots() -> None:
    candidates = []
    for slot in ("morning", "afternoon", "evening"):
        candidates.extend([
            _activity(
                f"history-{slot}-outdoor",
                slot=slot,
                price=10,
                environment=ActivityEnvironment.OUTDOOR,
                sensitivity=WeatherSensitivity.HIGH,
            ),
            _activity(
                f"history-{slot}-indoor",
                slot=slot,
                price=30,
                environment=ActivityEnvironment.INDOOR,
                sensitivity=WeatherSensitivity.NONE,
            ),
        ])
    state = _state(preferences=_preferences(
        start_date="2026-10-01",
        end_date="2026-10-02",
        interests=["history"],
    ))
    state.weather_result = _weather_result()
    state.weather_planning.status = WeatherPlanningStatus.READY

    result = await ActivityAgent(
        _FixedActivityProvider(candidates),
        require_weather_context=True,
    ).run(state)

    selected = result.activity_result.day_plans[0].activities
    assert [item.time_slot for item in selected] == [
        "morning", "afternoon", "evening"
    ]
    assert all("history" in f"{item.name} {item.category}" for item in selected)
    assert all(item.environment == ActivityEnvironment.INDOOR for item in selected)
    assert result.activity_result.constraint_issues == []


@pytest.mark.asyncio
async def test_t9_weather_unavailable_uses_explicit_unoptimized_fallback() -> None:
    result = await _run_pipeline(WeatherScenario.UNAVAILABLE)

    assert result.state == PlanningState.COMPLETED
    assert result.weather_result is not None
    assert result.weather_planning.status == WeatherPlanningStatus.UNAVAILABLE
    assert result.weather_planning.fallback_used is True
    assert result.activity_result.weather_optimized is False
    assert result.activity_result.weather_fallback_reason
    assert all(
        activity.weather_compatible is None
        for day in result.activity_result.day_plans
        for activity in day.activities
    )


class _SlowWeatherProvider:
    async def search(self, request):
        await asyncio.sleep(1)


class _InvalidWeatherProvider:
    async def search(self, request):
        return {"destination": request.destination, "invalid": True}


class _BrokenWeatherProvider:
    async def search(self, request):
        raise RuntimeError("weather provider exploded")


@pytest.mark.asyncio
async def test_t10_weather_timeout_degrades_but_invalid_and_internal_fail() -> None:
    timeout_state = await WeatherAgent(
        _SlowWeatherProvider(), timeout_seconds=0.001
    ).run(_state())
    invalid_state = await WeatherAgent(_InvalidWeatherProvider()).run(_state())
    broken_state = await WeatherAgent(_BrokenWeatherProvider()).run(_state())

    assert timeout_state.state != PlanningState.FAILED
    assert timeout_state.weather_planning.status == WeatherPlanningStatus.DEGRADED_TIMEOUT
    assert timeout_state.weather_planning.fallback_used is True
    assert timeout_state.weather_result is None

    assert invalid_state.state == PlanningState.FAILED
    assert invalid_state.weather_planning.status == WeatherPlanningStatus.FAILED_DATA
    assert invalid_state.agent_failures[0].error_type == "WeatherProviderDataError"

    assert broken_state.state == PlanningState.FAILED
    assert broken_state.weather_planning.status == WeatherPlanningStatus.FAILED_INTERNAL
    assert broken_state.agent_failures[0].error_type == "RuntimeError"


def test_t11_unknown_activity_metadata_is_not_rain_verified() -> None:
    unknown = _activity(
        "unknown-rain-candidate",
        slot="morning",
        price=1,
        environment=ActivityEnvironment.UNKNOWN,
        sensitivity=WeatherSensitivity.UNKNOWN,
    )

    evaluation = WeatherCompatibilityEvaluator().evaluate(
        unknown,
        _weather_result().daily_weather[0],
        activity_date="2026-10-01",
    )

    assert evaluation.hard_constraint_satisfied is False
    assert evaluation.data_sufficient is False
    assert evaluation.policy_version == "weather-compatibility-v1"
    assert "未知" in evaluation.reason


def test_weather_policy_keeps_low_sensitivity_outdoor_activity_in_normal_rain() -> None:
    activity = _activity(
        "low-sensitivity-outdoor",
        slot="morning",
        price=10,
        environment=ActivityEnvironment.OUTDOOR,
        sensitivity=WeatherSensitivity.LOW,
    )
    weather = DailyWeather(
        date="2026-10-01",
        weather_condition=WeatherCondition.RAIN,
        rain_probability=45,
        temperature=22,
        severe_weather=False,
        availability_status=WeatherAvailability.AVAILABLE,
    )
    evaluator = WeatherCompatibilityEvaluator(WeatherCompatibilityPolicy(
        soft_rain_probability=25,
        hard_rain_probability=75,
        high_sensitivity_rain_limit=50,
        score_weight=0.75,
    ))

    evaluation = evaluator.evaluate(
        activity, weather, activity_date="2026-10-01"
    )

    assert evaluation.hard_constraint_satisfied is True
    assert evaluation.weather_score == 4
    assert evaluator.policy.score_weight == 0.75
    with pytest.raises(ValidationError, match="soft rain threshold"):
        WeatherCompatibilityPolicy(
            soft_rain_probability=80,
            hard_rain_probability=70,
        )


@pytest.mark.asyncio
async def test_t12_no_rain_compatible_candidate_returns_business_constraint_state() -> None:
    candidates = [
        _activity(
            f"outdoor-{slot}",
            slot=slot,
            price=1,
            environment=ActivityEnvironment.OUTDOOR,
            sensitivity=WeatherSensitivity.HIGH,
        )
        for slot in ("morning", "afternoon", "evening")
    ]
    state = _state(preferences=_preferences(
        start_date="2026-10-01", end_date="2026-10-02"
    ))
    state.weather_result = _weather_result()
    state.weather_planning.status = WeatherPlanningStatus.READY

    result = await ActivityAgent(
        _FixedActivityProvider(candidates),
        require_weather_context=True,
    ).run(state)

    assert result.state == PlanningState.ACTIVITY_CONSTRAINTS_UNSATISFIED
    assert result.budget_breakdown is None
    assert len(result.activity_result.constraint_issues) == 3
    assert all(day.activities == [] for day in result.activity_result.day_plans)
    assert result.agent_failures == []


def _budget_weather_state() -> TravelPlanState:
    preferences = _preferences(
        budget=100,
        start_date="2026-10-01",
        end_date="2026-10-02",
        interests=["history"],
    )
    state = _state(preferences=preferences)
    outbound = Flight(
        candidate_id="outbound",
        source=DataSource.MOCK,
        source_version=DATASET,
        airline="Fixture Air",
        flight_no="F1",
        departure_city="北京",
        arrival_city="首尔",
        departure_time="2026-10-01T08:00:00",
        arrival_time="2026-10-01T10:00:00",
        price=20,
        duration_hours=2,
    )
    returning = outbound.model_copy(update={
        "candidate_id": "return",
        "flight_no": "F2",
        "departure_city": "首尔",
        "arrival_city": "北京",
        "departure_time": "2026-10-02T08:00:00",
        "arrival_time": "2026-10-02T10:00:00",
    })
    hotel = Hotel(
        candidate_id="hotel",
        source=DataSource.MOCK,
        source_version=DATASET,
        name="Fixture Hotel",
        city="首尔",
        check_in="2026-10-01",
        check_out="2026-10-02",
        user_rating=9,
        price_per_night=20,
    )
    indoor = _activity(
        "indoor-expensive",
        slot="morning",
        price=100,
        environment=ActivityEnvironment.INDOOR,
        sensitivity=WeatherSensitivity.NONE,
    )
    outdoor = _activity(
        "outdoor-cheap",
        slot="morning",
        price=1,
        environment=ActivityEnvironment.OUTDOOR,
        sensitivity=WeatherSensitivity.HIGH,
    )
    state.flight_result = FlightSearchResult(
        outbound_flights=[outbound],
        return_flights=[returning],
        recommended_outbound=outbound.model_copy(deep=True),
        recommended_return=returning.model_copy(deep=True),
    )
    state.hotel_result = HotelSearchResult(
        hotels=[hotel],
        recommended=hotel.model_copy(deep=True),
        total_nights=1,
    )
    state.activity_result = ActivitySearchResult(
        activity_candidates=[indoor, outdoor],
        day_plans=[DayPlan(
            date="2026-10-01",
            activities=[indoor.model_copy(deep=True)],
        )],
        weather_optimized=True,
    )
    state.candidate_snapshot = CandidateSnapshot(
        outbound_flights=(outbound.model_copy(deep=True),),
        return_flights=(returning.model_copy(deep=True),),
        hotels=(hotel.model_copy(deep=True),),
        activities=(
            indoor.model_copy(deep=True),
            outdoor.model_copy(deep=True),
        ),
    )
    state.weather_result = _weather_result()
    state.weather_planning.status = WeatherPlanningStatus.READY
    recalculate_selected_costs(state)
    return state


def test_t13_budget_optimizer_rejects_cheaper_weather_incompatible_activity() -> None:
    state = _budget_weather_state()
    original_prices = {
        item.candidate_id: item.price for item in state.candidate_snapshot.activities
    }

    adjustment = BudgetOptimizer().apply(
        state, BudgetComponent.ACTIVITIES, round_number=1
    )

    selected = state.activity_result.day_plans[0].activities[0]
    assert adjustment.improved is False
    assert adjustment.saved_amount == 0
    assert adjustment.before_candidate_ids == ["indoor-expensive"]
    assert adjustment.after_candidate_ids == ["indoor-expensive"]
    assert selected.candidate_id == "indoor-expensive"
    assert {
        item.candidate_id: item.price for item in state.candidate_snapshot.activities
    } == original_prices


@pytest.mark.asyncio
async def test_t14_t15_costs_reconcile_and_five_runs_are_identical() -> None:
    results = [await _run_pipeline(WeatherScenario.RAINY) for _ in range(5)]
    dumps = [item.model_dump_json() for item in results]

    assert len(set(dumps)) == 1
    result = results[0]
    breakdown = result.budget_breakdown
    assert breakdown.total_cost == (
        breakdown.flight_cost + breakdown.hotel_cost + breakdown.activity_cost
    )
    assert result.final_total_cost == breakdown.total_cost
    assert all(
        replacement.selection_reason
        for adjustment in result.adjustment_history
        for replacement in adjustment.activity_replacements
    )


@pytest.mark.asyncio
async def test_t16_concurrent_weather_plans_do_not_pollute_each_other() -> None:
    sunny_results = await asyncio.gather(*(
        _run_pipeline(WeatherScenario.SUNNY) for _ in range(5)
    ))
    rainy_results = await asyncio.gather(*(
        _run_pipeline(WeatherScenario.RAINY) for _ in range(5)
    ))

    assert len({item.model_dump_json() for item in sunny_results}) == 1
    assert len({item.model_dump_json() for item in rainy_results}) == 1
    assert {
        activity.environment
        for day in sunny_results[0].activity_result.day_plans
        for activity in day.activities
    } == {ActivityEnvironment.OUTDOOR}
    assert {
        activity.environment
        for day in rainy_results[0].activity_result.day_plans
        for activity in day.activities
    } == {ActivityEnvironment.INDOOR}


def test_api_health_and_full_plan_expose_weather_agent_contract() -> None:
    client = TestClient(app)
    health = client.get("/api/health")
    response = client.post("/api/plan/full", json={
        "budget": 50_000.0,
        "travel_style": "comfort",
        "departure_city": "北京",
        "start_date": "2026-10-01",
        "end_date": "2026-10-05",
        "num_travelers": 1,
        "interests": ["经典景点"],
    })

    assert health.status_code == 200
    assert health.json()["agents"] == 7
    assert response.status_code == 200
    body = response.json()
    assert body["weather_result"]["is_mock"] is True
    assert body["weather_planning"]["status"] in {"ready", "partial"}
    assert body["activity_result"]["weather_evaluations"]
    assert body["candidate_snapshot"]["activities"]
    assert body["activity_result"]["weather_optimized"] is True


def test_mock_activity_pool_has_explicit_rain_options_for_bangkok_and_default() -> None:
    async def load(city):
        return await MockActivityProvider().search(ActivitySearchRequest(
            city=city,
            start_date="2026-10-01",
            end_date="2026-10-02",
        ))

    bangkok = asyncio.run(load("曼谷"))
    default = asyncio.run(load("首尔"))

    for pool in (bangkok, default):
        indoor_slots = {
            item.time_slot
            for item in pool
            if item.environment == ActivityEnvironment.INDOOR
            and item.weather_sensitivity in {
                WeatherSensitivity.NONE,
                WeatherSensitivity.LOW,
            }
        }
        assert indoor_slots == {"morning", "afternoon", "evening"}
        assert all(item.source_version == settings.MOCK_ACTIVITY_DATA_VERSION for item in pool)
