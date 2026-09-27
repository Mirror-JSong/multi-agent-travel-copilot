"""Independent deterministic C1 fixtures, separate from product demo Mock data."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

from agents.activity_agent import ActivityAgent
from agents.base_agent import BaseAgent
from agents.flight_agent import FlightAgent
from agents.hotel_agent import HotelAgent
from agents.weather_agent import WeatherAgent
from models.multi_plan import MultiPlanConfig
from models.schemas import (
    Activity,
    ActivityAttributeStatus,
    ActivityEnvironment,
    ActivityIntensity,
    ActivitySearchRequest,
    DailyWeather,
    DataSource,
    Destination,
    DestinationRecommendation,
    Flight,
    Hotel,
    PlanningState,
    TravelPlanState,
    WeatherAvailability,
    WeatherCondition,
    WeatherResult,
    WeatherResultStatus,
    WeatherScenario,
    WeatherSearchRequest,
    WeatherSensitivity,
)
from orchestrator.pipeline import TravelPlanningPipeline
from tools.deterministic import stable_candidate_id


C1_FIXTURE_VERSION = "c1-multi-plan-fixture-v1"
DESTINATION_A = Destination(
    city="海城",
    country="Fixtureland",
    description="C1 固定低成本目的地",
    cost_level="low",
    highlights=["文化街区"],
)
DESTINATION_B = Destination(
    city="山城",
    country="Fixtureland",
    description="C1 固定高成本目的地",
    cost_level="high",
    highlights=["山地博物馆"],
)


class FixedDestinationAgent(BaseAgent):
    name = "DestinationAgent"
    output_fields = ("destination_rec",)

    def __init__(self, destinations: list[Destination]) -> None:
        super().__init__()
        self.destinations = [item.model_copy(deep=True) for item in destinations]

    async def execute(self, state: TravelPlanState) -> TravelPlanState:
        candidates = [item.model_copy(deep=True) for item in self.destinations]
        state.destination_rec = DestinationRecommendation(
            destinations=candidates,
            selected=(candidates[0].model_copy(deep=True) if candidates else None),
            reasoning="C1 固定目的地候选顺序，用于隔离及边界验收。",
        )
        state.state = PlanningState.SEARCHING_PARALLEL
        return state


def _route_destination(request) -> str:
    return (
        request.arrival_city
        if request.departure_city == "Origin"
        else request.departure_city
    )


class FixedMultiPlanFlightProvider:
    def __init__(self, failing_city: str | None = None) -> None:
        self.failing_city = failing_city

    async def search(self, request) -> list[Flight]:
        city = _route_destination(request)
        if city == self.failing_city:
            raise RuntimeError(f"C1 controlled flight failure for {city}")
        prices = (500.0, 300.0) if city == DESTINATION_A.city else (800.0, 700.0)
        identity = request.model_dump(mode="json")
        common = dict(
            source=DataSource.MOCK,
            source_version=C1_FIXTURE_VERSION,
            airline="C1 Fixture Air",
            departure_city=request.departure_city,
            arrival_city=request.arrival_city,
            departure_time=f"{request.travel_date}T08:00:00",
            cabin_class=request.cabin_class,
        )
        return [
            Flight(
                candidate_id=stable_candidate_id(
                    "c1-flight-premium", identity, C1_FIXTURE_VERSION
                ),
                flight_no=f"C1P-{city}",
                arrival_time=f"{request.travel_date}T09:00:00",
                price=prices[0],
                duration_hours=1.0,
                stops=0,
                **common,
            ),
            Flight(
                candidate_id=stable_candidate_id(
                    "c1-flight-economy", identity, C1_FIXTURE_VERSION
                ),
                flight_no=f"C1E-{city}",
                arrival_time=f"{request.travel_date}T20:00:00",
                price=prices[1],
                duration_hours=12.0,
                stops=2,
                **common,
            ),
        ]


class FixedMultiPlanHotelProvider:
    async def search(self, request) -> list[Hotel]:
        prices = (
            (250.0, 100.0)
            if request.city == DESTINATION_A.city
            else (700.0, 600.0)
        )
        identity = request.model_dump(mode="json")
        common = dict(
            source=DataSource.MOCK,
            source_version=C1_FIXTURE_VERSION,
            city=request.city,
            check_in=request.check_in,
            check_out=request.check_out,
        )
        return [
            Hotel(
                candidate_id=stable_candidate_id(
                    "c1-hotel-premium", identity, C1_FIXTURE_VERSION
                ),
                name=f"{request.city} Central Fixture Hotel",
                star_rating=3.5,
                user_rating=9.5,
                price_per_night=prices[0],
                distance_to_center_km=0.1,
                **common,
            ),
            Hotel(
                candidate_id=stable_candidate_id(
                    "c1-hotel-economy", identity, C1_FIXTURE_VERSION
                ),
                name=f"{request.city} Economy Fixture Hotel",
                star_rating=2.5,
                user_rating=7.5,
                price_per_night=prices[1],
                distance_to_center_km=5.0,
                **common,
            ),
        ]


class FixedMultiPlanWeatherProvider:
    def __init__(
        self,
        condition_by_city: dict[str, WeatherCondition] | None = None,
    ) -> None:
        self.condition_by_city = dict(condition_by_city or {})

    async def search(self, request: WeatherSearchRequest) -> WeatherResult:
        condition = self.condition_by_city.get(
            request.destination,
            WeatherCondition.SUNNY,
        )
        start = date.fromisoformat(request.start_date)
        end = date.fromisoformat(request.end_date)
        days = [
            start + timedelta(days=offset)
            for offset in range((end - start).days)
        ]
        severe = condition in {
            WeatherCondition.HEAVY_RAIN,
            WeatherCondition.THUNDERSTORM,
        }
        rain_probability = 95 if severe else 5
        return WeatherResult(
            destination=request.destination,
            start_date=request.start_date,
            end_date=request.end_date,
            scenario=WeatherScenario.MIXED,
            daily_weather=[
                DailyWeather(
                    date=item.isoformat(),
                    weather_condition=condition,
                    rain_probability=rain_probability,
                    temperature=22.0,
                    severe_weather=severe,
                    availability_status=WeatherAvailability.AVAILABLE,
                )
                for item in days
            ],
            source=DataSource.MOCK,
            source_version=C1_FIXTURE_VERSION,
            is_mock=True,
            availability_status=WeatherResultStatus.AVAILABLE,
            availability_note="C1 固定 Mock 天气；不是实时天气预报。",
        )


class FixedMultiPlanActivityProvider:
    def __init__(self, variant: str = "normal") -> None:
        self.variant = variant

    async def search(self, request: ActivitySearchRequest) -> list[Activity]:
        slots = ["morning", "afternoon", "evening"]
        if self.variant == "no_evening":
            slots = slots[:-1]
        candidates: list[Activity] = []
        for slot in slots:
            definitions = [
                {
                    "tier": "premium",
                    "price": 100.0,
                    "rating": 9.5,
                    "environment": ActivityEnvironment.INDOOR,
                    "sensitivity": WeatherSensitivity.LOW,
                    "intensity": ActivityIntensity.LOW,
                },
                {
                    "tier": "economy",
                    "price": 10.0,
                    "rating": 7.5,
                    "environment": ActivityEnvironment.INDOOR,
                    "sensitivity": WeatherSensitivity.LOW,
                    "intensity": ActivityIntensity.LOW,
                },
            ]
            if self.variant == "outdoor_only":
                definitions = [dict(
                    item,
                    environment=ActivityEnvironment.OUTDOOR,
                    sensitivity=WeatherSensitivity.HIGH,
                ) for item in definitions]
            elif self.variant == "high_only":
                definitions = [dict(
                    item,
                    intensity=ActivityIntensity.HIGH,
                    duration=5.0,
                ) for item in definitions]
            elif self.variant == "weather_choice":
                definitions = [
                    {
                        "tier": "cheap-outdoor",
                        "price": 5.0,
                        "rating": 9.8,
                        "environment": ActivityEnvironment.OUTDOOR,
                        "sensitivity": WeatherSensitivity.HIGH,
                        "intensity": ActivityIntensity.LOW,
                    },
                    {
                        "tier": "indoor-safe",
                        "price": 30.0,
                        "rating": 8.0,
                        "environment": ActivityEnvironment.INDOOR,
                        "sensitivity": WeatherSensitivity.LOW,
                        "intensity": ActivityIntensity.LOW,
                    },
                ]
            for definition in definitions:
                identity = {
                    "city": request.city,
                    "slot": slot,
                    "tier": definition["tier"],
                    "variant": self.variant,
                }
                candidates.append(Activity(
                    candidate_id=stable_candidate_id(
                        "c1-activity", identity, C1_FIXTURE_VERSION
                    ),
                    source=DataSource.MOCK,
                    source_version=C1_FIXTURE_VERSION,
                    name=f"{request.city} 文化 {slot} {definition['tier']}",
                    category="文化",
                    location=request.city,
                    available_start_date=request.start_date,
                    available_end_date=request.end_date,
                    duration_hours=definition.get("duration", 2.0),
                    duration_status=ActivityAttributeStatus.AVAILABLE,
                    intensity=definition["intensity"],
                    intensity_status=ActivityAttributeStatus.AVAILABLE,
                    pace_attribute_source=DataSource.MOCK,
                    price=definition["price"],
                    rating=definition["rating"],
                    description="C1 固定 Mock 文化活动；强度与时长不是实测数据。",
                    time_slot=slot,
                    environment=definition["environment"],
                    weather_sensitivity=definition["sensitivity"],
                ))
        return [item.model_copy(deep=True) for item in candidates]


def fixed_pipeline_factory(
    *,
    activity_variant_by_city: dict[str, str] | None = None,
    weather_by_city: dict[str, WeatherCondition] | None = None,
    failing_flight_city: str | None = None,
) -> Callable[[Destination, MultiPlanConfig], TravelPlanningPipeline]:
    variants = dict(activity_variant_by_city or {})
    weather = dict(weather_by_city or {})

    def build(
        destination: Destination,
        config: MultiPlanConfig,
    ) -> TravelPlanningPipeline:
        activity_agent = ActivityAgent(
            provider=FixedMultiPlanActivityProvider(
                variants.get(destination.city, "normal")
            ),
            require_weather_context=True,
            planning_mode=config.activity_planning_mode,
        )
        return TravelPlanningPipeline(
            flight_agent=FlightAgent(
                FixedMultiPlanFlightProvider(failing_flight_city)
            ),
            hotel_agent=HotelAgent(FixedMultiPlanHotelProvider()),
            weather_agent=WeatherAgent(
                FixedMultiPlanWeatherProvider(weather),
                scenario=config.weather_scenario,
            ),
            activity_agent=activity_agent,
            activity_planning_mode=config.activity_planning_mode,
        )

    return build
