"""Preference completeness checks, deterministic questions, and multi-turn merging."""

from __future__ import annotations

import math
from datetime import date
from typing import Mapping

from models.schemas import MAX_TRAVEL_BUDGET, MAX_TRAVELERS, UserPreferences

from .contracts import (
    ClarificationQuestion,
    ClarificationResult,
    PreferenceField,
    PreferenceValueSource,
    PreferencesDraft,
    TRACKED_PREFERENCE_FIELDS,
)
from .date_semantics import reconcile_dates
from .parser import MockPreferenceParser, PreferenceParser


REQUIRED_PREFERENCE_FIELDS = (
    PreferenceField.DEPARTURE_CITY,
    PreferenceField.BUDGET,
    PreferenceField.START_DATE,
    PreferenceField.END_DATE,
    PreferenceField.NUM_TRAVELERS,
    PreferenceField.TRAVEL_STYLE,
)


class IncompletePreferencesError(ValueError):
    """Raised when a draft is used before required information is valid."""


class ClarificationManager:
    def __init__(self, parser: PreferenceParser | None = None) -> None:
        self.parser = parser or MockPreferenceParser()

    async def start(self, text: str, *, reference_date: date) -> ClarificationResult:
        draft = await self.parser.parse(text, reference_date=reference_date)
        return self.evaluate(draft)

    async def clarify(
        self,
        current: ClarificationResult | PreferencesDraft,
        answer: str,
        *,
        reference_date: date,
    ) -> ClarificationResult:
        base = current.draft if isinstance(current, ClarificationResult) else current
        update = await self.parser.parse(answer, reference_date=reference_date)
        return self.evaluate(self._merge(base, update))

    def apply_manual_update(
        self,
        current: ClarificationResult | PreferencesDraft,
        updates: Mapping[str, object],
    ) -> ClarificationResult:
        """Explicit fallback for fields the finite Mock parser cannot understand."""
        base = current.draft if isinstance(current, ClarificationResult) else current
        allowed = {field.value for field in TRACKED_PREFERENCE_FIELDS}
        unsupported = sorted(set(updates) - allowed)
        if unsupported:
            raise ValueError(f"unsupported preference fields: {', '.join(unsupported)}")

        sources = {
            PreferenceField(key): PreferenceValueSource.EXPLICIT
            for key in updates
        }
        update = PreferencesDraft(
            raw_text="[manual update]",
            **dict(updates),
            field_sources=sources,
        )
        update = reconcile_dates(
            update,
            changed_fields=set(sources),
        )
        return self.evaluate(self._merge(base, update))

    def evaluate(self, draft: PreferencesDraft) -> ClarificationResult:
        errors = dict(draft.field_errors)
        self._validate_scalar_fields(draft, errors)
        self._validate_dates(draft, errors)

        ambiguous = set(draft.ambiguous_fields)
        missing: list[PreferenceField] = []
        for field in REQUIRED_PREFERENCE_FIELDS:
            value = getattr(draft, field.value)
            if value is None or field in errors or field in ambiguous:
                missing.append(field)

        question_fields = list(missing)
        for field in TRACKED_PREFERENCE_FIELDS:
            if (
                (field in errors or field in ambiguous)
                and field not in question_fields
            ):
                question_fields.append(field)
        questions = [
            self._question_for(field, errors.get(field), field in ambiguous)
            for field in question_fields
        ]
        return ClarificationResult(
            draft=draft,
            missing_required_fields=missing,
            questions=questions,
            validation_errors=errors,
            is_complete=not missing and not errors,
        )

    def to_user_preferences(
        self,
        value: ClarificationResult | PreferencesDraft,
    ) -> UserPreferences:
        result = value if isinstance(value, ClarificationResult) else self.evaluate(value)
        if not result.is_complete:
            missing = ", ".join(field.value for field in result.missing_required_fields)
            raise IncompletePreferencesError(
                f"preferences are incomplete or invalid; manual clarification required: {missing}"
            )

        draft = result.draft
        assert draft.departure_city is not None
        assert draft.budget is not None
        assert draft.start_date is not None
        assert draft.end_date is not None
        assert draft.num_travelers is not None
        assert draft.travel_style is not None
        return UserPreferences(
            departure_city=draft.departure_city,
            budget=float(draft.budget),
            start_date=draft.start_date,
            end_date=draft.end_date,
            num_travelers=draft.num_travelers,
            travel_style=draft.travel_style,
            pace=draft.pace,
            interests=list(draft.interests or []),
        )

    @staticmethod
    def _merge(base: PreferencesDraft, update: PreferencesDraft) -> PreferencesDraft:
        values = base.model_dump()
        sources = dict(base.field_sources)
        errors = dict(base.field_errors)
        ambiguous = list(base.ambiguous_fields)
        changed: set[PreferenceField] = set()

        for field in TRACKED_PREFERENCE_FIELDS:
            source = update.source_for(field)
            if source == PreferenceValueSource.UNKNOWN:
                continue
            values[field.value] = getattr(update, field.value)
            sources[field] = source
            errors.pop(field, None)
            ambiguous = [item for item in ambiguous if item != field]
            changed.add(field)

        for field, message in update.field_errors.items():
            errors[field] = message
            if field not in changed:
                changed.add(field)
        for field in update.ambiguous_fields:
            if field not in ambiguous:
                ambiguous.append(field)

        values["raw_text"] = "\n".join(
            part for part in (base.raw_text, update.raw_text) if part
        )
        values["field_sources"] = sources
        values["field_errors"] = errors
        values["ambiguous_fields"] = ambiguous
        values["revision"] = base.revision + 1
        merged = PreferencesDraft.model_validate(values)
        return reconcile_dates(merged, changed_fields=changed)

    @staticmethod
    def _validate_scalar_fields(
        draft: PreferencesDraft,
        errors: dict[PreferenceField, str],
    ) -> None:
        if draft.departure_city is not None and not draft.departure_city.strip():
            errors[PreferenceField.DEPARTURE_CITY] = "出发城市不能为空"
        if draft.budget is not None and (
            not math.isfinite(draft.budget)
            or draft.budget <= 0
            or draft.budget > MAX_TRAVEL_BUDGET
        ):
            errors[PreferenceField.BUDGET] = (
                f"预算必须大于 0 且不超过 {MAX_TRAVEL_BUDGET}"
            )
        if draft.num_travelers is not None and not (
            1 <= draft.num_travelers <= MAX_TRAVELERS
        ):
            errors[PreferenceField.NUM_TRAVELERS] = (
                f"旅行人数必须在 1 到 {MAX_TRAVELERS} 之间"
            )
        if draft.duration_days is not None and draft.duration_days <= 0:
            errors[PreferenceField.DURATION_DAYS] = "旅行天数必须是正整数"

    @staticmethod
    def _validate_dates(
        draft: PreferencesDraft,
        errors: dict[PreferenceField, str],
    ) -> None:
        parsed: dict[PreferenceField, date] = {}
        for field in (PreferenceField.START_DATE, PreferenceField.END_DATE):
            value = getattr(draft, field.value)
            if value is None:
                continue
            try:
                parsed_date = date.fromisoformat(value)
                if parsed_date.isoformat() != value:
                    raise ValueError
                parsed[field] = parsed_date
            except ValueError:
                errors[field] = "日期必须是有效的 YYYY-MM-DD"
        if all(field in parsed for field in (PreferenceField.START_DATE, PreferenceField.END_DATE)):
            if parsed[PreferenceField.END_DATE] <= parsed[PreferenceField.START_DATE]:
                errors[PreferenceField.END_DATE] = "返程日期必须晚于出发日期"

    @staticmethod
    def _question_for(
        field: PreferenceField,
        error: str | None,
        ambiguous: bool,
    ) -> ClarificationQuestion:
        prompts = {
            PreferenceField.DEPARTURE_CITY: "请补充从哪个城市出发，例如“从上海出发”。",
            PreferenceField.BUDGET: "请补充本次全部旅行者的总预算，例如“预算一万五”。",
            PreferenceField.START_DATE: "请提供明确出发日期，例如“2026年10月1日出发”。",
            PreferenceField.END_DATE: "请提供明确返程日期，或在出发日期基础上说明旅行天数。",
            PreferenceField.DURATION_DAYS: "请确认准确旅行天数，使其与出发和返程日期一致。",
            PreferenceField.NUM_TRAVELERS: "请说明准确出行人数，例如“共3人”。",
            PreferenceField.TRAVEL_STYLE: (
                "请选择旅行风格：budget、comfort、luxury、adventure、cultural 或 relaxation。"
            ),
        }
        reason = error or ("当前信息存在歧义，需要确认" if ambiguous else "必要信息尚未提供")
        return ClarificationQuestion(
            field=field,
            prompt=prompts.get(field, f"请确认 {field.value}。"),
            reason=reason,
        )
