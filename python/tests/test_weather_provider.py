"""Stage B1 weather contract and deterministic MockWeatherProvider tests."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from agents.activity_agent import ActivityAgent
from models.schemas import (
    Activity,
    ActivityEnvironment,
    DailyWeather,
    DataSource,
    WeatherAvailability,
    WeatherCondition,
    WeatherResult,
    WeatherResultStatus,
    WeatherScenario,
    WeatherSearchRequest,
    WeatherSensitivity,
)
from tools.providers import (
    WeatherProvider,
    WeatherProviderDataError,
    WeatherProviderError,
    WeatherProviderTimeoutError,
)
from tools.weather_api import get_weather
from tools.weather_provider import MockWeatherProvider


PYTHON_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "weather_scenarios.json"


@pytest.fixture(scope="module")
def weather_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _request(weather_fixture: dict, scenario: str = "generated", **updates) -> WeatherSearchRequest:
    values = {
        "destination": weather_fixture["destination"],
        "start_date": weather_fixture["start_date"],
        "end_date": weather_fixture["end_date"],
        "scenario": scenario,
    }
    values.update(updates)
    return WeatherSearchRequest(**values)


def _dump(result: WeatherResult) -> str:
    return result.model_dump_json()


def _daily_signature(result: WeatherResult) -> list[tuple]:
    return [
        (
            item.date,
            item.weather_condition,
            item.rain_probability,
            item.temperature,
            item.severe_weather,
            item.availability_status,
        )
        for item in result.daily_weather
    ]


@pytest.mark.asyncio
async def test_t1_same_weather_request_is_identical_across_five_calls(weather_fixture) -> None:
    provider = MockWeatherProvider("weather-v1")
    request = _request(weather_fixture)

    results = [_dump(await provider.search(request)) for _ in range(5)]

    assert len(set(results)) == 1


def test_t2_weather_is_stable_across_processes_and_hash_seeds() -> None:
    script = r'''
import asyncio
from models.schemas import WeatherSearchRequest
from tools.weather_provider import MockWeatherProvider

async def main():
    result = await MockWeatherProvider("weather-cross-process-v1").search(
        WeatherSearchRequest(
            destination="东京",
            start_date="2026-10-01",
            end_date="2026-10-05",
            scenario="mixed",
        )
    )
    print(result.model_dump_json())

asyncio.run(main())
'''
    outputs = []
    for hash_seed in ("1", "987654"):
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = hash_seed
        environment["PYTHONIOENCODING"] = "utf-8"
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=PYTHON_ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        outputs.append(completed.stdout.strip())

    assert outputs[0]
    assert outputs[0] == outputs[1]


@pytest.mark.asyncio
async def test_t3_concurrent_calls_do_not_change_weather(weather_fixture) -> None:
    provider = MockWeatherProvider("weather-v1")
    request = _request(weather_fixture, "mixed")

    results = await asyncio.gather(*(provider.search(request) for _ in range(20)))

    assert len({_dump(result) for result in results}) == 1


@pytest.mark.asyncio
async def test_t4_overlapping_ranges_share_identical_daily_weather() -> None:
    provider = MockWeatherProvider("weather-overlap-v1")
    first = await provider.search(WeatherSearchRequest(
        destination="东京",
        start_date="2026-10-01",
        end_date="2026-10-06",
    ))
    second = await provider.search(WeatherSearchRequest(
        destination="东京",
        start_date="2026-10-03",
        end_date="2026-10-08",
    ))

    first_by_date = {item.date: item for item in first.daily_weather}
    second_by_date = {item.date: item for item in second.daily_weather}
    overlap = sorted(set(first_by_date) & set(second_by_date))

    assert overlap == ["2026-10-03", "2026-10-04", "2026-10-05"]
    assert [first_by_date[item] for item in overlap] == [
        second_by_date[item] for item in overlap
    ]


@pytest.mark.asyncio
async def test_t5_city_date_scenario_and_version_change_applicable_results(weather_fixture) -> None:
    base_request = _request(weather_fixture)
    base = await MockWeatherProvider("weather-v1").search(base_request)
    other_city = await MockWeatherProvider("weather-v1").search(
        base_request.model_copy(update={"destination": "曼谷"})
    )
    next_dates = await MockWeatherProvider("weather-v1").search(
        base_request.model_copy(update={
            "start_date": "2026-10-02",
            "end_date": "2026-10-06",
        })
    )
    rainy = await MockWeatherProvider("weather-v1").search(
        base_request.model_copy(update={"scenario": WeatherScenario.RAINY})
    )
    other_version = await MockWeatherProvider("weather-v2").search(base_request)

    assert _daily_signature(base) != _daily_signature(other_city)
    assert _daily_signature(base) != _daily_signature(next_dates)
    assert _daily_signature(base) != _daily_signature(rainy)
    assert _daily_signature(base) != _daily_signature(other_version)


@pytest.mark.asyncio
async def test_t6_fixed_sunny_fixture_is_always_sunny(weather_fixture) -> None:
    result = await MockWeatherProvider().search(_request(weather_fixture, "sunny"))

    assert {
        item.weather_condition.value for item in result.daily_weather
    } == set(weather_fixture["scenarios"]["sunny"]["expected_conditions"])
    assert all(item.rain_probability <= 10 for item in result.daily_weather)
    assert all(item.severe_weather is False for item in result.daily_weather)


@pytest.mark.asyncio
async def test_t7_fixed_rainy_fixture_is_always_rainy(weather_fixture) -> None:
    result = await MockWeatherProvider().search(_request(weather_fixture, "rainy"))

    allowed = set(weather_fixture["scenarios"]["rainy"]["expected_conditions"])
    assert {item.weather_condition.value for item in result.daily_weather} <= allowed
    assert all(item.rain_probability >= 85 for item in result.daily_weather)


@pytest.mark.asyncio
async def test_t8_fixed_mixed_fixture_has_multiple_conditions(weather_fixture) -> None:
    result = await MockWeatherProvider().search(_request(weather_fixture, "mixed"))

    condition_count = len({item.weather_condition for item in result.daily_weather})
    assert condition_count >= weather_fixture["scenarios"]["mixed"]["minimum_condition_count"]


@pytest.mark.asyncio
async def test_t9_unavailable_is_explicit_and_never_fabricates_sun(weather_fixture) -> None:
    result = await MockWeatherProvider().search(_request(weather_fixture, "unavailable"))

    assert result.availability_status == WeatherResultStatus.UNAVAILABLE
    assert "未将未知天气替换为晴天" in result.availability_note
    assert all(
        item.availability_status == WeatherAvailability.UNAVAILABLE
        and item.weather_condition is None
        and item.rain_probability is None
        and item.temperature is None
        and item.severe_weather is None
        for item in result.daily_weather
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"destination": "", "start_date": "2026-10-01", "end_date": "2026-10-05"},
        {"destination": "东京", "start_date": "bad-date", "end_date": "2026-10-05"},
        {"destination": "东京", "start_date": "2026-10-05", "end_date": "2026-10-01"},
        {"destination": "东京", "start_date": "2026-10-01", "end_date": "2026-10-05", "scenario": "storm-by-chance"},
        {"destination": "东京", "start_date": "2026-10-01", "end_date": "2026-10-05", "unknown": True},
        {"destination": "东京", "start_date": "2026-10-01", "end_date": "2026-10-05", "config": {"locale": "en-US"}},
    ],
)
def test_t10_invalid_weather_queries_are_rejected(payload) -> None:
    with pytest.raises(ValidationError):
        WeatherSearchRequest.model_validate(payload)


@pytest.mark.parametrize("probability", [-1, 101, 1.5, "50"])
def test_t10_invalid_rain_probability_is_rejected(probability) -> None:
    with pytest.raises(ValidationError):
        DailyWeather(
            date="2026-10-01",
            weather_condition="rain",
            rain_probability=probability,
            temperature=20,
            severe_weather=False,
            availability_status="available",
        )


def test_t10_weather_result_rejects_missing_or_duplicate_coverage() -> None:
    daily = DailyWeather(
        date="2026-10-01",
        weather_condition="sunny",
        rain_probability=0,
        temperature=20,
        severe_weather=False,
        availability_status="available",
    )
    base = {
        "destination": "东京",
        "start_date": "2026-10-01",
        "end_date": "2026-10-03",
        "scenario": "sunny",
        "daily_weather": [daily, daily],
        "source": "mock",
        "source_version": "weather-v1",
        "is_mock": True,
        "availability_status": "available",
        "availability_note": "固定 Mock 数据，不是实时天气。",
    }

    with pytest.raises(ValidationError, match="完整覆盖"):
        WeatherResult.model_validate(base)


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"is_mock": False}, "Mock 来源"),
        ({"availability_status": "unavailable"}, "结果状态"),
    ],
)
def test_t10_weather_result_rejects_inconsistent_source_or_status(updates, message) -> None:
    base = {
        "destination": "东京",
        "start_date": "2026-10-01",
        "end_date": "2026-10-02",
        "scenario": "sunny",
        "daily_weather": [{
            "date": "2026-10-01",
            "weather_condition": "sunny",
            "rain_probability": 0,
            "temperature": 20,
            "severe_weather": False,
            "availability_status": "available",
        }],
        "source": "mock",
        "source_version": "weather-v1",
        "is_mock": True,
        "availability_status": "available",
        "availability_note": "固定 Mock 数据，不是实时天气。",
    }
    base.update(updates)

    with pytest.raises(ValidationError, match=message):
        WeatherResult.model_validate(base)


@pytest.mark.asyncio
async def test_t11_t12_provider_result_matches_schema_and_source_contract(weather_fixture) -> None:
    provider = MockWeatherProvider("weather-contract-v1")
    result = await provider.search(_request(weather_fixture))

    assert isinstance(provider, WeatherProvider)
    assert isinstance(result, WeatherResult)
    assert all(isinstance(item, DailyWeather) for item in result.daily_weather)
    assert result.source == DataSource.MOCK
    assert result.source_version == "weather-contract-v1"
    assert result.is_mock is True
    assert "不是实时天气预报" in result.availability_note


@pytest.mark.asyncio
async def test_t13_mutating_returned_result_does_not_pollute_next_query(weather_fixture) -> None:
    provider = MockWeatherProvider("weather-isolation-v1")
    request = _request(weather_fixture, "mixed")
    original = await provider.search(request)
    original_dump = _dump(original)

    original.daily_weather[0].temperature = -50
    original.availability_note = "污染值"
    fresh = await provider.search(request)

    assert _dump(fresh) == original_dump


@pytest.mark.asyncio
async def test_t14_weather_uses_same_end_exclusive_dates_as_activity_agent(weather_fixture) -> None:
    request = _request(weather_fixture, "sunny")
    result = await MockWeatherProvider().search(request)

    expected_dates = ActivityAgent._get_travel_days(request.start_date, request.end_date)
    assert [item.date for item in result.daily_weather] == expected_dates
    assert result.daily_weather[-1].date == (
        date.fromisoformat(request.end_date) - timedelta(days=1)
    ).isoformat()
    assert request.end_date not in {item.date for item in result.daily_weather}


def test_activity_weather_metadata_defaults_are_unknown_not_compatible() -> None:
    activity = Activity(name="未标注活动")

    assert activity.environment == ActivityEnvironment.UNKNOWN
    assert activity.weather_sensitivity == WeatherSensitivity.UNKNOWN
    assert activity.weather_compatible is None
    assert activity.recommendation_reason == ""


def test_weather_provider_error_types_remain_distinguishable() -> None:
    assert issubclass(WeatherProviderTimeoutError, WeatherProviderError)
    assert issubclass(WeatherProviderDataError, WeatherProviderError)
    assert WeatherProviderTimeoutError is not WeatherProviderDataError


def test_legacy_weather_api_delegates_to_deterministic_provider() -> None:
    results = [get_weather("东京", "2026-10-01") for _ in range(5)]

    assert results.count(results[0]) == 5
    assert results[0].date == "2026-10-01"
