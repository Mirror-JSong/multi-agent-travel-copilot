"""Session-local state and conflict helpers for the independent Classic UI."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import Any, Mapping, MutableMapping


CLASSIC_DEFAULTS: dict[str, Any] = {
    "classic_preference_response": None,
    "classic_preference_history": [],
    "classic_nl_confirmed": False,
    "classic_nl_stale": False,
    "classic_plan_result": None,
    "classic_comparison_result": None,
    "classic_active_plan_id": None,
    "classic_last_error": None,
    "classic_invalidation_reason": None,
    "classic_explicit_fields": set(),
}

FORM_FIELDS = (
    "budget",
    "departure_city",
    "start_date",
    "end_date",
    "travel_style",
    "pace",
    "num_travelers",
    "interests",
)

STYLE_LABELS = {
    "comfort": "舒适游",
    "budget": "经济游",
    "luxury": "豪华游",
    "adventure": "探险游",
    "cultural": "文化游",
    "relaxation": "休闲游",
}

PACE_LABELS = {
    "relaxed": "慢节奏",
    "balanced": "节奏适中",
    "packed": "紧凑",
}


def initialize_classic_state(state: MutableMapping[str, Any]) -> None:
    for key, value in CLASSIC_DEFAULTS.items():
        if key not in state:
            state[key] = deepcopy(value)


def invalidate_classic_results(
    state: MutableMapping[str, Any],
    *,
    reason: str,
    signed_draft_stale: bool,
) -> None:
    """Invalidate derived results without deleting valid user input."""
    state["classic_plan_result"] = None
    state["classic_comparison_result"] = None
    state["classic_active_plan_id"] = None
    state["classic_nl_confirmed"] = False
    state["classic_last_error"] = None
    state["classic_invalidation_reason"] = reason
    if signed_draft_stale and state.get("classic_preference_response") is not None:
        state["classic_nl_stale"] = True


def mark_form_field_explicit(
    state: MutableMapping[str, Any],
    field: str,
) -> None:
    fields = set(state.get("classic_explicit_fields", set()))
    fields.add(field)
    state["classic_explicit_fields"] = fields
    invalidate_classic_results(
        state,
        reason="旅行偏好已修改，旧确认、规划和评分均已失效。",
        signed_draft_stale=True,
    )


def accept_classic_preference_response(
    state: MutableMapping[str, Any],
    response: dict[str, Any],
    *,
    user_text: str,
) -> None:
    questions = response.get("questions", [])
    assistant_text = (
        "；".join(str(item.get("prompt", "")) for item in questions)
        if questions
        else "必要信息已经完整，请核对表单与识别结果。"
    )
    history = list(state.get("classic_preference_history", []))
    history.extend(
        [
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": assistant_text},
        ]
    )
    state["classic_preference_response"] = response
    state["classic_preference_history"] = history
    state["classic_nl_confirmed"] = False
    state["classic_nl_stale"] = False
    state["classic_plan_result"] = None
    state["classic_comparison_result"] = None
    state["classic_active_plan_id"] = None
    state["classic_last_error"] = None
    state["classic_invalidation_reason"] = None


def _json_value(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list):
        return sorted(str(item) for item in value)
    return value


def preference_conflicts(
    response: Mapping[str, Any] | None,
    form_values: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return explicit/inferred draft values that disagree with the form."""
    if response is None:
        return []
    draft = response["draft"]
    sources = draft.get("field_sources", {})
    conflicts: list[dict[str, Any]] = []
    for field in FORM_FIELDS:
        source = sources.get(field, "unknown")
        draft_value = draft.get(field)
        if source == "unknown" or draft_value is None:
            continue
        form_value = form_values.get(field)
        if _json_value(draft_value) != _json_value(form_value):
            conflicts.append(
                {
                    "field": field,
                    "source": source,
                    "form_value": form_value,
                    "draft_value": draft_value,
                }
            )
    return conflicts


def draft_matches_form(
    response: Mapping[str, Any] | None,
    form_values: Mapping[str, Any],
) -> bool:
    return response is not None and not preference_conflicts(response, form_values)


def clarification_answer_from_form(form_values: Mapping[str, Any]) -> str:
    """Build a deterministic answer that the existing Mock parser validates."""
    start = _json_value(form_values["start_date"])
    end = _json_value(form_values["end_date"])
    style = STYLE_LABELS[str(form_values["travel_style"])]
    parts = [
        f"从{form_values['departure_city']}出发",
        f"总预算{float(form_values['budget']):g}元",
        f"日期{start}到{end}",
        f"一共{int(form_values['num_travelers'])}人",
        style,
    ]
    pace = form_values.get("pace")
    if pace:
        parts.append(PACE_LABELS[str(pace)])
    interests = list(form_values.get("interests") or [])
    if interests:
        parts.append("喜欢" + "和".join(str(item) for item in interests))
    return "，".join(parts) + "。"


def apply_draft_to_form(
    state: MutableMapping[str, Any],
    response: Mapping[str, Any],
) -> None:
    """Copy server-signed values into widgets after an explicit user action."""
    draft = response["draft"]
    sources = draft.get("field_sources", {})
    widget_keys = {
        "budget": "classic_budget",
        "departure_city": "classic_departure_city",
        "start_date": "classic_start_date",
        "end_date": "classic_end_date",
        "travel_style": "classic_travel_style",
        "pace": "classic_pace",
        "num_travelers": "classic_num_travelers",
        "interests": "classic_interests",
    }
    for field, widget_key in widget_keys.items():
        value = draft.get(field)
        if value is None or sources.get(field, "unknown") == "unknown":
            continue
        if field in {"start_date", "end_date"}:
            value = date.fromisoformat(value)
        elif field == "num_travelers":
            value = int(value)
        elif field == "budget":
            value = float(value)
        elif field == "interests":
            value = list(value)
        state[widget_key] = value
    state["classic_nl_confirmed"] = False
    state["classic_nl_stale"] = False
    state["classic_plan_result"] = None
    state["classic_comparison_result"] = None
    state["classic_active_plan_id"] = None
    state["classic_invalidation_reason"] = None


def reset_classic_state(state: MutableMapping[str, Any]) -> None:
    for key in CLASSIC_DEFAULTS:
        state.pop(key, None)
    initialize_classic_state(state)
