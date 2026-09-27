"""用户输入的领域边界与日期约束。"""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from models.schemas import MAX_TRAVEL_BUDGET, MAX_TRAVELERS, UserPreferences
from tools.weather_api import get_weather


def _valid_preferences(**overrides) -> dict:
    values = {
        "budget": 10_000,
        "departure_city": "北京",
        "start_date": "2026-05-01",
        "end_date": "2026-05-05",
        "num_travelers": 1,
    }
    values.update(overrides)
    return values


@pytest.mark.parametrize(
    ("start_date", "end_date"),
    [
        ("not-a-date", "2026-05-05"),
        ("2026-02-30", "2026-05-05"),
        ("2026-5-01", "2026-05-05"),
        ("2026-05-05", "2026-05-01"),
        ("2026-05-05", "2026-05-05"),
    ],
)
def test_rejects_invalid_or_non_increasing_dates(start_date: str, end_date: str):
    with pytest.raises(ValidationError):
        UserPreferences(**_valid_preferences(start_date=start_date, end_date=end_date))


@pytest.mark.parametrize(
    "budget",
    [0, -1, math.inf, -math.inf, math.nan, MAX_TRAVEL_BUDGET + 1, "10000"],
)
def test_rejects_invalid_budget(budget):
    with pytest.raises(ValidationError):
        UserPreferences(**_valid_preferences(budget=budget))


@pytest.mark.parametrize("num_travelers", [0, -1, MAX_TRAVELERS + 1, 1.5, "2", True])
def test_rejects_invalid_traveler_count(num_travelers):
    with pytest.raises(ValidationError):
        UserPreferences(**_valid_preferences(num_travelers=num_travelers))


@pytest.mark.parametrize("departure_city", ["", "   "])
def test_rejects_blank_departure_city(departure_city: str):
    with pytest.raises(ValidationError):
        UserPreferences(**_valid_preferences(departure_city=departure_city))


def test_valid_preferences_keep_compatible_string_dates_and_normalize_city():
    preferences = UserPreferences(
        **_valid_preferences(departure_city="  北京  ", budget=10_000.5)
    )

    assert preferences.departure_city == "北京"
    assert preferences.start_date == "2026-05-01"
    assert preferences.end_date == "2026-05-05"
    assert preferences.budget == 10_000.5


def test_weather_tool_rejects_invalid_date_instead_of_using_default_month():
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        get_weather("东京", "not-a-date")
