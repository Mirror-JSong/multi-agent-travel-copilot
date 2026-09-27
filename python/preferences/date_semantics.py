"""Date reconciliation matching the existing Pipeline's end-exclusive semantics."""

from __future__ import annotations

from datetime import date, timedelta

from .contracts import PreferenceField, PreferenceValueSource, PreferencesDraft


def reconcile_dates(
    draft: PreferencesDraft,
    changed_fields: set[PreferenceField] | None = None,
) -> PreferencesDraft:
    """Derive one date field without hiding contradictions or invalid user values.

    `duration_days` equals `(end_date - start_date).days`. The return/check-out date
    is excluded from activity days, and the same number is used as hotel nights.
    """
    values = draft.model_dump()
    sources = dict(draft.field_sources)
    errors = dict(draft.field_errors)
    ambiguous = list(draft.ambiguous_fields)
    changed = changed_fields or set()

    if PreferenceField.DURATION_DAYS in changed and draft.start_date and PreferenceField.END_DATE not in changed:
        values["end_date"] = None
        sources[PreferenceField.END_DATE] = PreferenceValueSource.UNKNOWN
        errors.pop(PreferenceField.END_DATE, None)
    elif PreferenceField.START_DATE in changed and draft.duration_days and PreferenceField.END_DATE not in changed:
        values["end_date"] = None
        sources[PreferenceField.END_DATE] = PreferenceValueSource.UNKNOWN
        errors.pop(PreferenceField.END_DATE, None)
    elif PreferenceField.END_DATE in changed and draft.start_date and PreferenceField.DURATION_DAYS not in changed:
        values["duration_days"] = None
        sources[PreferenceField.DURATION_DAYS] = PreferenceValueSource.UNKNOWN
        errors.pop(PreferenceField.DURATION_DAYS, None)

    start = _parse_date(values.get("start_date"))
    end = _parse_date(values.get("end_date"))
    duration = values.get("duration_days")

    if duration is not None and duration <= 0:
        errors[PreferenceField.DURATION_DAYS] = "旅行天数必须是正整数"

    if start and end:
        calculated = (end - start).days
        if calculated <= 0:
            errors[PreferenceField.END_DATE] = "返程日期必须晚于出发日期"
        elif duration is not None and duration != calculated:
            errors[PreferenceField.DURATION_DAYS] = (
                f"旅行天数 {duration} 与日期区间 {calculated} 天不一致，请确认"
            )
            ambiguous.append(PreferenceField.DURATION_DAYS)
        else:
            values["duration_days"] = calculated
            if duration is None:
                sources[PreferenceField.DURATION_DAYS] = PreferenceValueSource.INFERRED
            errors.pop(PreferenceField.DURATION_DAYS, None)
            ambiguous = [
                field for field in ambiguous
                if field != PreferenceField.DURATION_DAYS
            ]
    elif (
        start
        and duration is not None
        and duration > 0
        and PreferenceField.END_DATE not in errors
    ):
        values["end_date"] = (start + timedelta(days=duration)).isoformat()
        sources[PreferenceField.END_DATE] = PreferenceValueSource.INFERRED
        errors.pop(PreferenceField.END_DATE, None)
        ambiguous = [
            field for field in ambiguous
            if field != PreferenceField.END_DATE
        ]
    elif (
        end
        and duration is not None
        and duration > 0
        and PreferenceField.START_DATE not in errors
    ):
        values["start_date"] = (end - timedelta(days=duration)).isoformat()
        sources[PreferenceField.START_DATE] = PreferenceValueSource.INFERRED
        errors.pop(PreferenceField.START_DATE, None)
        ambiguous = [
            field for field in ambiguous
            if field != PreferenceField.START_DATE
        ]

    values["field_sources"] = sources
    values["field_errors"] = errors
    values["ambiguous_fields"] = list(dict.fromkeys(ambiguous))
    return PreferencesDraft.model_validate(values)


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None
