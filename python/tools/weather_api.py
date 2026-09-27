"""Backward-compatible single-day facade over the canonical weather Provider.

New code should depend on ``WeatherProvider``.  This module intentionally owns
no Mock generation logic, preventing the legacy helper and Provider from
drifting into two different weather data sources.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_type, timedelta

from config.settings import settings
from models.schemas import WeatherCondition, WeatherSearchRequest
from tools.weather_provider import generate_mock_weather


@dataclass
class WeatherInfo:
    city: str
    date: str
    temperature_high: int
    temperature_low: int
    condition: str
    humidity: int
    rain_probability: int
    suggestion: str


_CONDITION_LABELS = {
    WeatherCondition.SUNNY: "晴",
    WeatherCondition.CLOUDY: "多云",
    WeatherCondition.RAIN: "小雨",
    WeatherCondition.HEAVY_RAIN: "大雨",
    WeatherCondition.THUNDERSTORM: "雷阵雨",
    WeatherCondition.SNOW: "小雪",
}


def get_weather(city: str, date: str) -> WeatherInfo:
    """Return one deterministic Mock day through the new Provider contract."""

    try:
        parsed_date = date_type.fromisoformat(date)
    except (TypeError, ValueError) as exc:
        raise ValueError("日期必须是有效的 YYYY-MM-DD 日期") from exc
    if parsed_date.isoformat() != date:
        raise ValueError("日期必须采用 YYYY-MM-DD 格式")

    request = WeatherSearchRequest(
        destination=city,
        start_date=date,
        end_date=(parsed_date + timedelta(days=1)).isoformat(),
    )
    daily = generate_mock_weather(request, settings.MOCK_DATA_VERSION).daily_weather[0]
    assert daily.temperature is not None
    assert daily.rain_probability is not None
    assert daily.weather_condition is not None

    if daily.rain_probability > 50:
        suggestion = "建议携带雨具，穿防水鞋"
    elif daily.temperature > 30:
        suggestion = "天气炎热，注意防晒补水"
    elif daily.temperature < 5:
        suggestion = "天气寒冷，注意保暖"
    else:
        suggestion = "模拟天气较温和，可结合临近日期真实预报安排活动"

    return WeatherInfo(
        city=city.strip(),
        date=date,
        temperature_high=round(daily.temperature + 3),
        temperature_low=round(daily.temperature - 3),
        condition=_CONDITION_LABELS[daily.weather_condition],
        humidity=max(30, min(95, 40 + daily.rain_probability // 2)),
        rain_probability=daily.rain_probability,
        suggestion=suggestion,
    )
