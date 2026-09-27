"""Framework-light state helpers for isolated Streamlit clarification sessions."""

from __future__ import annotations

from datetime import date
from typing import Any, MutableMapping


SESSION_DEFAULTS: dict[str, Any] = {
    "preference_response": None,
    "preference_history": [],
    "preference_confirmed": False,
    "preference_reference_date": None,
    "preference_reference_date_locked": None,
    "preference_plan_result": None,
    "preference_comparison_result": None,
    "preference_planning_mode": "single",
    "preference_selected_destination_id": None,
    "preference_planning": False,
}


def initialize_preference_session(
    state: MutableMapping[str, Any],
    *,
    default_reference_date: date,
) -> None:
    for key, value in SESSION_DEFAULTS.items():
        if key not in state:
            state[key] = (
                default_reference_date
                if key == "preference_reference_date"
                else list(value) if isinstance(value, list) else value
            )


def reset_preference_session(
    state: MutableMapping[str, Any],
    *,
    default_reference_date: date,
) -> None:
    for key in SESSION_DEFAULTS:
        state.pop(key, None)
    state.pop("natural_requirement", None)
    state.pop("clarification_answer", None)
    initialize_preference_session(state, default_reference_date=default_reference_date)


def accept_preference_response(
    state: MutableMapping[str, Any],
    response: dict[str, Any],
    *,
    user_text: str,
) -> None:
    """Atomically update state only after a successful HTTP response."""
    questions = response.get("questions", [])
    assistant_text = (
        "；".join(str(question.get("prompt", "")) for question in questions)
        if questions
        else "必要信息已经完整，请核对确认卡片。"
    )
    history = list(state.get("preference_history", []))
    history.extend(
        [
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": assistant_text},
        ]
    )
    state["preference_response"] = response
    state["preference_history"] = history
    state["preference_confirmed"] = False
    state["preference_plan_result"] = None
    state["preference_comparison_result"] = None
    state["preference_selected_destination_id"] = None
    state["preference_planning"] = False
