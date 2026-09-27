"""Stage A batch 1 tests for deterministic preference clarification."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from agents.activity_agent import ActivityAgent
from agents.hotel_agent import HotelAgent
from models.schemas import TravelStyle, UserPreferences
from orchestrator.pipeline import TravelPlanningPipeline
from preferences import (
    ClarificationManager,
    IncompletePreferencesError,
    MockPreferenceParser,
    PreferenceField,
    PreferenceParser,
    PreferenceValueSource,
)


REFERENCE_DATE = date(2026, 9, 24)
CORPUS_PATH = Path(__file__).parent / "fixtures" / "preference_utterances.json"
CORPUS = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


def _comparable(value: object) -> object:
    if hasattr(value, "value"):
        return value.value
    return value


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CORPUS, ids=lambda case: case["id"])
async def test_fixed_chinese_corpus(case: dict[str, object]) -> None:
    """The finite Mock parser covers the declared corpus, not open-ended NLU."""
    parser = MockPreferenceParser()

    draft = await parser.parse(case["text"], reference_date=REFERENCE_DATE)

    for field, expected in case["expected"].items():
        actual = getattr(draft, field)
        if isinstance(actual, list):
            actual = [_comparable(item) for item in actual]
        else:
            actual = _comparable(actual)
        assert actual == expected, f"{case['id']} field={field}"


def test_corpus_is_fixed_and_within_declared_size() -> None:
    assert 30 <= len(CORPUS) <= 50
    assert len({case["id"] for case in CORPUS}) == len(CORPUS)


@pytest.mark.asyncio
async def test_t1_extracts_basic_natural_language_without_inventing_unknowns() -> None:
    parser = MockPreferenceParser()

    draft = await parser.parse(
        "国庆想和朋友从上海出去玩五天，预算一万五，不想太累，喜欢拍照和吃东西。",
        reference_date=REFERENCE_DATE,
    )

    assert draft.departure_city == "上海"
    assert draft.budget == 15000
    assert draft.duration_days == 5
    assert draft.pace.value == "relaxed"
    assert draft.interests == ["摄影", "美食"]
    assert draft.num_travelers is None
    assert draft.start_date is None
    assert draft.end_date is None
    assert draft.travel_style is None
    assert draft.source_for(PreferenceField.BUDGET) == PreferenceValueSource.EXPLICIT
    assert draft.source_for(PreferenceField.NUM_TRAVELERS) == PreferenceValueSource.UNKNOWN
    assert PreferenceField.START_DATE in draft.ambiguous_fields


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("预算一万五", 15000),
        ("预算一万五千", 15000),
        ("预算两万", 20000),
        ("预算八千", 8000),
        ("预算1.5万", 15000),
        ("预算15000元", 15000),
    ],
)
async def test_t2_parses_supported_chinese_budget_forms(
    utterance: str,
    expected: float,
) -> None:
    draft = await MockPreferenceParser().parse(
        utterance,
        reference_date=REFERENCE_DATE,
    )
    assert draft.budget == expected


@pytest.mark.asyncio
async def test_t3_questions_only_missing_or_ambiguous_required_fields() -> None:
    result = await ClarificationManager().start(
        "国庆想和朋友从上海出去玩五天，预算一万五，不想太累，喜欢拍照和吃东西。",
        reference_date=REFERENCE_DATE,
    )

    assert result.is_complete is False
    assert result.missing_required_fields == [
        PreferenceField.START_DATE,
        PreferenceField.END_DATE,
        PreferenceField.NUM_TRAVELERS,
        PreferenceField.TRAVEL_STYLE,
    ]
    assert [question.field for question in result.questions] == result.missing_required_fields
    assert PreferenceField.DEPARTURE_CITY not in result.missing_required_fields
    assert PreferenceField.BUDGET not in result.missing_required_fields


@pytest.mark.asyncio
async def test_t4_supplemental_answer_merges_without_losing_confirmed_fields() -> None:
    manager = ClarificationManager()
    first = await manager.start(
        "国庆想和朋友从上海出去玩五天，预算一万五，不想太累，喜欢拍照和吃东西。",
        reference_date=REFERENCE_DATE,
    )

    result = await manager.clarify(
        first,
        "我们一共3人，2026年10月1日出发，舒适游。",
        reference_date=REFERENCE_DATE,
    )

    assert result.is_complete is True
    assert result.draft.departure_city == "上海"
    assert result.draft.budget == 15000
    assert result.draft.num_travelers == 3
    assert result.draft.start_date == "2026-10-01"
    assert result.draft.end_date == "2026-10-06"
    assert result.draft.source_for(PreferenceField.END_DATE) == PreferenceValueSource.INFERRED
    assert result.draft.travel_style == TravelStyle.COMFORT
    assert result.draft.interests == ["摄影", "美食"]
    assert result.draft.revision == 1


@pytest.mark.asyncio
async def test_t5_explicit_budget_change_replaces_only_budget() -> None:
    manager = ClarificationManager()
    first = await manager.start(
        "从上海出发，预算一万五，3人，舒适游，2026年10月1日到2026年10月6日。",
        reference_date=REFERENCE_DATE,
    )

    changed = await manager.clarify(
        first,
        "预算改成两万。",
        reference_date=REFERENCE_DATE,
    )

    assert changed.is_complete is True
    assert changed.draft.budget == 20000
    assert changed.draft.departure_city == first.draft.departure_city
    assert changed.draft.start_date == first.draft.start_date
    assert changed.draft.end_date == first.draft.end_date
    assert changed.draft.num_travelers == first.draft.num_travelers


@pytest.mark.asyncio
async def test_t6_unrelated_answer_does_not_overwrite_confirmed_fields() -> None:
    manager = ClarificationManager()
    first = await manager.start(
        "从上海出发，预算一万五，3人，舒适游，2026年10月1日到2026年10月6日。",
        reference_date=REFERENCE_DATE,
    )
    original = first.draft.model_dump(exclude={"raw_text", "revision"})

    changed = await manager.clarify(
        first,
        "顺便说一句，希望酒店安静。",
        reference_date=REFERENCE_DATE,
    )

    assert changed.draft.model_dump(exclude={"raw_text", "revision"}) == original
    assert changed.draft.revision == 1


@pytest.mark.asyncio
async def test_t7_unrecognized_input_does_not_create_false_fields() -> None:
    draft = await MockPreferenceParser().parse(
        "最近有点想出去转转。",
        reference_date=REFERENCE_DATE,
    )

    fields = (
        "departure_city",
        "budget",
        "start_date",
        "end_date",
        "duration_days",
        "num_travelers",
        "travel_style",
        "pace",
        "interests",
    )
    assert all(getattr(draft, field) is None for field in fields)
    assert all(
        draft.source_for(PreferenceField(field)) == PreferenceValueSource.UNKNOWN
        for field in fields
    )


@pytest.mark.asyncio
async def test_t8_invalid_values_and_dates_cannot_complete() -> None:
    manager = ClarificationManager()
    result = await manager.start(
        "从上海出发，预算负一百，0人，舒适游，2026年2月30日到2026年3月3日。",
        reference_date=REFERENCE_DATE,
    )

    assert result.is_complete is False
    assert PreferenceField.BUDGET in result.validation_errors
    assert PreferenceField.NUM_TRAVELERS in result.validation_errors
    assert PreferenceField.START_DATE in result.validation_errors
    with pytest.raises(IncompletePreferencesError):
        manager.to_user_preferences(result)

    reversed_dates = await manager.start(
        "从上海出发，预算一万，2人，舒适游，2026年10月5日到2026年10月1日。",
        reference_date=REFERENCE_DATE,
    )
    assert PreferenceField.END_DATE in reversed_dates.validation_errors
    assert reversed_dates.is_complete is False


@pytest.mark.asyncio
async def test_t9_parser_is_deterministic_for_fixed_reference_date() -> None:
    parser = MockPreferenceParser()
    text = "明天从深圳出发玩三天，预算一万五，两个人，休闲游，喜欢海边和拍照。"

    serializations = [
        (await parser.parse(text, reference_date=REFERENCE_DATE)).model_dump_json()
        for _ in range(5)
    ]

    assert len(set(serializations)) == 1


@pytest.mark.asyncio
async def test_t10_complete_draft_converts_to_existing_user_preferences() -> None:
    manager = ClarificationManager()
    result = await manager.start(
        "从北京出发，预算两万，共3人，舒适游，2026年10月1日到2026年10月6日，喜欢历史。",
        reference_date=REFERENCE_DATE,
    )

    preferences = manager.to_user_preferences(result)

    assert isinstance(preferences, UserPreferences)
    assert preferences.departure_city == "北京"
    assert preferences.budget == 20000
    assert preferences.num_travelers == 3
    assert preferences.travel_style == TravelStyle.COMFORT
    assert preferences.start_date == "2026-10-01"
    assert preferences.end_date == "2026-10-06"
    assert preferences.interests == ["历史"]


@pytest.mark.asyncio
async def test_t11_incomplete_draft_cannot_start_formal_pipeline() -> None:
    manager = ClarificationManager()
    result = await manager.start(
        "从上海出发，预算一万。",
        reference_date=REFERENCE_DATE,
    )

    with pytest.raises(IncompletePreferencesError):
        manager.to_user_preferences(result)
    with pytest.raises(TypeError, match="validated UserPreferences"):
        await TravelPlanningPipeline().run(result.draft)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_date_semantics_match_activity_days_and_hotel_nights() -> None:
    result = await ClarificationManager().start(
        "2026年10月1日从上海出发玩五天，预算一万五，2人，舒适游。",
        reference_date=REFERENCE_DATE,
    )

    assert result.draft.end_date == "2026-10-06"
    assert result.draft.duration_days == 5
    assert HotelAgent._calc_nights(result.draft.start_date, result.draft.end_date) == 5
    assert len(ActivityAgent._get_travel_days(
        result.draft.start_date,
        result.draft.end_date,
    )) == 5


@pytest.mark.asyncio
async def test_explicit_matching_duration_keeps_explicit_provenance() -> None:
    draft = await MockPreferenceParser().parse(
        "从上海出发，2026年10月1日到2026年10月6日，玩五天。",
        reference_date=REFERENCE_DATE,
    )

    assert draft.duration_days == 5
    assert draft.source_for(PreferenceField.DURATION_DAYS) == PreferenceValueSource.EXPLICIT


@pytest.mark.asyncio
async def test_invalid_explicit_date_is_not_replaced_by_a_derived_date() -> None:
    result = await ClarificationManager().start(
        "从上海出发，2026年2月30日到2026年3月5日，玩五天，预算一万，2人，舒适游。",
        reference_date=REFERENCE_DATE,
    )

    assert result.draft.start_date == "2026-02-30"
    assert PreferenceField.START_DATE in result.validation_errors
    assert result.is_complete is False


@pytest.mark.asyncio
async def test_conflicting_dates_and_duration_generate_a_specific_question() -> None:
    result = await ClarificationManager().start(
        "从上海出发，预算一万，2人，舒适游，2026年10月1日到2026年10月6日，玩三天。",
        reference_date=REFERENCE_DATE,
    )

    assert result.is_complete is False
    assert PreferenceField.DURATION_DAYS in result.validation_errors
    assert any(
        question.field == PreferenceField.DURATION_DAYS
        for question in result.questions
    )


@pytest.mark.asyncio
async def test_ambiguous_ranges_are_asked_not_guessed() -> None:
    result = await ClarificationManager().start(
        "从上海出发，预算一万到两万，大概两三人，国庆出发，舒适游。",
        reference_date=REFERENCE_DATE,
    )

    assert result.draft.budget is None
    assert result.draft.num_travelers is None
    assert {
        PreferenceField.BUDGET,
        PreferenceField.NUM_TRAVELERS,
        PreferenceField.START_DATE,
        PreferenceField.END_DATE,
    }.issubset(set(result.missing_required_fields))
    assert all(
        question.reason == "当前信息存在歧义，需要确认"
        for question in result.questions
        if question.field in {
            PreferenceField.BUDGET,
            PreferenceField.NUM_TRAVELERS,
            PreferenceField.START_DATE,
            PreferenceField.END_DATE,
        }
    )


@pytest.mark.asyncio
async def test_manual_update_is_explicit_fallback_for_unparsed_answers() -> None:
    manager = ClarificationManager()
    first = await manager.start(
        "最近有点想出去转转。",
        reference_date=REFERENCE_DATE,
    )

    result = manager.apply_manual_update(
        first,
        {
            "departure_city": "上海",
            "budget": 12000.0,
            "start_date": "2026-11-01",
            "end_date": "2026-11-05",
            "num_travelers": 2,
            "travel_style": TravelStyle.COMFORT,
            "interests": ["摄影"],
        },
    )

    assert result.is_complete is True
    assert result.draft.duration_days == 4
    assert result.draft.source_for(PreferenceField.DURATION_DAYS) == PreferenceValueSource.INFERRED
    assert all(
        result.draft.source_for(field) == PreferenceValueSource.EXPLICIT
        for field in (
            PreferenceField.DEPARTURE_CITY,
            PreferenceField.BUDGET,
            PreferenceField.START_DATE,
            PreferenceField.END_DATE,
            PreferenceField.NUM_TRAVELERS,
            PreferenceField.TRAVEL_STYLE,
            PreferenceField.INTERESTS,
        )
    )


@pytest.mark.asyncio
async def test_changing_start_date_keeps_duration_and_recomputes_inferred_end() -> None:
    manager = ClarificationManager()
    first = await manager.start(
        "2026年10月1日从上海出发玩五天，预算一万五，2人，舒适游。",
        reference_date=REFERENCE_DATE,
    )

    changed = await manager.clarify(
        first,
        "改成2026年10月2日出发。",
        reference_date=REFERENCE_DATE,
    )

    assert changed.is_complete is True
    assert changed.draft.start_date == "2026-10-02"
    assert changed.draft.duration_days == 5
    assert changed.draft.end_date == "2026-10-07"
    assert changed.draft.source_for(PreferenceField.END_DATE) == PreferenceValueSource.INFERRED


def test_parser_implements_replaceable_protocol() -> None:
    assert isinstance(MockPreferenceParser(), PreferenceParser)
