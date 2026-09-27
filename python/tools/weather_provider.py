"""Deterministic weather Provider used by the Stage B Mock implementation.

The result is a simulated scenario, not a real forecast.  Each activity date
uses its own stable digest so overlapping queries produce identical weather
for the overlapping dates and concurrent calls never share random state.
"""

from __future__ import annotations

from datetime import date, timedelta

from pydantic import ValidationError

from config.settings import settings
from models.schemas import (
    DailyWeather,
    DataSource,
    WeatherAvailability,
    WeatherCondition,
    WeatherResult,
    WeatherResultStatus,
    WeatherScenario,
    WeatherSearchRequest,
)
from tools.deterministic import stable_rng
from tools.providers import WeatherProviderDataError


_SEASONAL_TEMPERATURE_C = {
    "spring": 18.0,
    "summer": 29.0,
    "autumn": 20.0,
    "winter": 6.0,
}
_SEASONAL_RAIN_BASE = {
    "spring": 35,
    "summer": 50,
    "autumn": 25,
    "winter": 15,
}


def _season(month: int) -> str:
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    if month in (9, 10, 11):
        return "autumn"
    return "winter"


def _activity_dates(request: WeatherSearchRequest) -> list[date]:
    start = date.fromisoformat(request.start_date)
    end = date.fromisoformat(request.end_date)
    return [start + timedelta(days=offset) for offset in range((end - start).days)]


def _day_rng(request: WeatherSearchRequest, day: date, dataset_version: str):
    # Deliberately omit the requested range.  A day's weather identity is the
    # destination, date, scenario, query config and dataset version.
    return stable_rng(
        "weather-day",
        {
            "destination": request.destination,
            "date": day.isoformat(),
            "scenario": request.scenario.value,
            "config": request.config.model_dump(mode="json"),
        },
        dataset_version,
    )


def _temperature(request: WeatherSearchRequest, day: date, dataset_version: str) -> float:
    rng = _day_rng(request, day, dataset_version)
    baseline = _SEASONAL_TEMPERATURE_C[_season(day.month)]
    return round(baseline + rng.uniform(-5.0, 5.0), 1)


def _generated_day(
    request: WeatherSearchRequest,
    day: date,
    dataset_version: str,
) -> DailyWeather:
    rng = _day_rng(request, day, dataset_version)
    season = _season(day.month)
    rain_probability = max(
        0,
        min(100, _SEASONAL_RAIN_BASE[season] + rng.randint(-15, 15)),
    )
    draw = rng.randint(0, 99)
    if rain_probability >= 65 and draw < rain_probability // 3:
        condition = WeatherCondition.THUNDERSTORM
    elif draw < rain_probability:
        condition = WeatherCondition.RAIN
    elif draw < min(95, rain_probability + 35):
        condition = WeatherCondition.CLOUDY
    else:
        condition = WeatherCondition.SUNNY
    if _temperature(request, day, dataset_version) <= 0 and condition == WeatherCondition.RAIN:
        condition = WeatherCondition.SNOW
    return DailyWeather(
        date=day.isoformat(),
        weather_condition=condition,
        rain_probability=rain_probability,
        temperature=_temperature(request, day, dataset_version),
        severe_weather=condition == WeatherCondition.THUNDERSTORM,
        availability_status=WeatherAvailability.AVAILABLE,
    )


def _fixed_day(
    request: WeatherSearchRequest,
    day: date,
    dataset_version: str,
) -> DailyWeather:
    rng = _day_rng(request, day, dataset_version)
    scenario = request.scenario
    if scenario == WeatherScenario.SUNNY:
        condition = WeatherCondition.SUNNY
        rain_probability = rng.randint(0, 10)
    elif scenario == WeatherScenario.RAINY:
        condition = (
            WeatherCondition.HEAVY_RAIN
            if day.toordinal() % 2
            else WeatherCondition.RAIN
        )
        rain_probability = 85 + rng.randint(0, 10)
    else:
        mixed = (
            (WeatherCondition.SUNNY, 5),
            (WeatherCondition.CLOUDY, 30),
            (WeatherCondition.RAIN, 80),
            (WeatherCondition.THUNDERSTORM, 95),
        )
        condition, rain_probability = mixed[day.toordinal() % len(mixed)]
    return DailyWeather(
        date=day.isoformat(),
        weather_condition=condition,
        rain_probability=rain_probability,
        temperature=round(
            _temperature(request, day, dataset_version) + rng.uniform(-1.0, 1.0),
            1,
        ),
        severe_weather=condition in {
            WeatherCondition.HEAVY_RAIN,
            WeatherCondition.THUNDERSTORM,
        },
        availability_status=WeatherAvailability.AVAILABLE,
    )


def generate_mock_weather(
    request: WeatherSearchRequest,
    dataset_version: str,
) -> WeatherResult:
    """Build one fresh validated result without reading global random state."""

    days = _activity_dates(request)
    if request.scenario == WeatherScenario.UNAVAILABLE:
        daily_weather = [
            DailyWeather(
                date=day.isoformat(),
                availability_status=WeatherAvailability.UNAVAILABLE,
            )
            for day in days
        ]
        result_status = WeatherResultStatus.UNAVAILABLE
        note = (
            "确定性 Mock Provider 明确返回天气数据不可用；"
            "未将未知天气替换为晴天，也不是实时天气预报。"
        )
    else:
        daily_weather = [
            _generated_day(request, day, dataset_version)
            if request.scenario == WeatherScenario.GENERATED
            else _fixed_day(request, day, dataset_version)
            for day in days
        ]
        result_status = WeatherResultStatus.AVAILABLE
        note = (
            f"确定性 Mock 天气情景 {request.scenario.value}；"
            "仅用于测试与演示，不是实时天气预报。"
        )

    try:
        return WeatherResult(
            destination=request.destination,
            start_date=request.start_date,
            end_date=request.end_date,
            scenario=request.scenario,
            daily_weather=daily_weather,
            source=DataSource.MOCK,
            source_version=dataset_version,
            is_mock=True,
            availability_status=result_status,
            availability_note=note,
        )
    except ValidationError as exc:
        raise WeatherProviderDataError("Mock 天气结果违反 WeatherResult 契约") from exc


class MockWeatherProvider:
    """Replaceable async Provider backed only by deterministic Mock data."""

    def __init__(self, dataset_version: str | None = None):
        self.dataset_version = dataset_version or settings.MOCK_DATA_VERSION

    async def search(self, request: WeatherSearchRequest) -> WeatherResult:
        return generate_mock_weather(request, self.dataset_version)
