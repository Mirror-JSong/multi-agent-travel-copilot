"""Fixed C2 evaluation fixtures, isolated from product demonstration data."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

from agents.activity_agent import ActivityAgent
from agents.weather_agent import WeatherAgent
from models.multi_plan import (
    MultiPlanConfig,
    MultiPlanExecutionMode,
    MultiPlanRequest,
)
from models.schemas import (
    Activity,
    ActivityPlanningMode,
    DailyWeather,
    DataSource,
    Destination,
    TravelPace,
    UserPreferences,
    WeatherAvailability,
    WeatherResult,
    WeatherResultStatus,
    WeatherScenario,
    WeatherSearchRequest,
)
from orchestrator.comparison import ComparisonOrchestrator
from orchestrator.pipeline import TravelPlanningPipeline
from tests.fixtures.multi_plan import (
    C1_FIXTURE_VERSION,
    DESTINATION_A,
    DESTINATION_B,
    FixedDestinationAgent,
    FixedMultiPlanActivityProvider,
    fixed_pipeline_factory,
)


C2_FIXTURE_VERSION = "c2-plan-evaluation-fixture-v1"


def c2_preferences(
    *,
    budget: float = 5_000.0,
    interests: list[str] | None = None,
    pace: TravelPace | None = TravelPace.BALANCED,
) -> UserPreferences:
    return UserPreferences(
        budget=budget,
        departure_city="Origin",
        start_date="2026-05-01",
        end_date="2026-05-03",
        num_travelers=1,
        interests=["文化"] if interests is None else list(interests),
        pace=pace,
    )


def c2_request(
    *,
    budget: float = 5_000.0,
    interests: list[str] | None = None,
    pace: TravelPace | None = TravelPace.BALANCED,
    execution_mode: MultiPlanExecutionMode = MultiPlanExecutionMode.CONCURRENT,
) -> MultiPlanRequest:
    return MultiPlanRequest(
        preferences=c2_preferences(
            budget=budget,
            interests=interests,
            pace=pace,
        ),
        config=MultiPlanConfig(
            execution_mode=execution_mode,
            max_concurrency=2,
            mock_data_version=C1_FIXTURE_VERSION,
            mock_activity_data_version=C1_FIXTURE_VERSION,
            destination_data_version=C1_FIXTURE_VERSION,
        ),
    )


def c2_orchestrator(**factory_options) -> ComparisonOrchestrator:
    return ComparisonOrchestrator(
        destination_agent=FixedDestinationAgent([DESTINATION_A, DESTINATION_B]),
        pipeline_factory=fixed_pipeline_factory(**factory_options),
    )


class InterestTradeoffActivityProvider(FixedMultiPlanActivityProvider):
    """Keep one A time-slot outside the declared culture interest."""

    async def search(self, request) -> list[Activity]:
        activities = await super().search(request)
        if request.city != DESTINATION_A.city:
            return activities
        adjusted: list[Activity] = []
        for item in activities:
            if item.time_slot != "afternoon":
                adjusted.append(item)
                continue
            adjusted.append(item.model_copy(update={
                "name": f"{request.city} riverside {item.time_slot}",
                "category": "scenery",
                "description": "C2 fixed non-interest activity evidence.",
                "source_version": C2_FIXTURE_VERSION,
            }))
        return adjusted


class UnavailableWeatherProvider:
    """Return full date coverage with explicit unavailability, never fake sun."""

    async def search(self, request: WeatherSearchRequest) -> WeatherResult:
        start = date.fromisoformat(request.start_date)
        end = date.fromisoformat(request.end_date)
        return WeatherResult(
            destination=request.destination,
            start_date=request.start_date,
            end_date=request.end_date,
            scenario=WeatherScenario.UNAVAILABLE,
            daily_weather=[
                DailyWeather(
                    date=(start + timedelta(days=offset)).isoformat(),
                    availability_status=WeatherAvailability.UNAVAILABLE,
                )
                for offset in range((end - start).days)
            ],
            source=DataSource.MOCK,
            source_version=C2_FIXTURE_VERSION,
            is_mock=True,
            availability_status=WeatherResultStatus.UNAVAILABLE,
            availability_note="C2 fixed unavailable weather; no condition fabricated.",
        )


def interest_tradeoff_pipeline_factory(
    destination: Destination,
    config: MultiPlanConfig,
) -> TravelPlanningPipeline:
    pipeline = fixed_pipeline_factory()(destination, config)
    activity_agent = ActivityAgent(
        provider=InterestTradeoffActivityProvider(),
        require_weather_context=True,
        planning_mode=config.activity_planning_mode,
    )
    # Rebuild so BudgetOptimizer receives the same evaluator contracts.
    return TravelPlanningPipeline(
        flight_agent=pipeline.flight_agent,
        hotel_agent=pipeline.hotel_agent,
        weather_agent=pipeline.weather_agent,
        activity_agent=activity_agent,
        activity_planning_mode=config.activity_planning_mode,
    )


def unavailable_weather_pipeline_factory(
    destination: Destination,
    config: MultiPlanConfig,
) -> TravelPlanningPipeline:
    pipeline = fixed_pipeline_factory()(destination, config)
    return TravelPlanningPipeline(
        flight_agent=pipeline.flight_agent,
        hotel_agent=pipeline.hotel_agent,
        weather_agent=WeatherAgent(
            UnavailableWeatherProvider(),
            scenario=WeatherScenario.UNAVAILABLE,
        ),
        activity_agent=ActivityAgent(
            provider=FixedMultiPlanActivityProvider(),
            require_weather_context=True,
            planning_mode=ActivityPlanningMode.WEATHER_AWARE,
        ),
        activity_planning_mode=ActivityPlanningMode.WEATHER_AWARE,
    )


def custom_orchestrator(
    factory: Callable[[Destination, MultiPlanConfig], TravelPlanningPipeline],
) -> ComparisonOrchestrator:
    return ComparisonOrchestrator(
        destination_agent=FixedDestinationAgent([DESTINATION_A, DESTINATION_B]),
        pipeline_factory=factory,
    )
