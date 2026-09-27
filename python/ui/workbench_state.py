"""Session-local state contract for the five-step Streamlit workbench."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, MutableMapping


UI_STEPS = ("intake", "confirmation", "planning", "comparison", "detail")

WORKBENCH_DEFAULTS: dict[str, Any] = {
    "ui_step": "intake",
    "ui_last_step": "intake",
    "ui_input_mode": "自然语言澄清",
    "planning_request_state": "idle",
    "pending_planning_request": None,
    "active_plan_id": None,
    "active_detail_tab": "itinerary",
    "ui_last_error": None,
    "ui_last_warning": None,
    "result_invalidation_reason": None,
    "confirmed_preferences_fingerprint": None,
    "structured_plan_result": None,
    "structured_plan_fingerprint": None,
    "structured_comparison_result": None,
    "structured_comparison_fingerprint": None,
}


def initialize_workbench_state(state: MutableMapping[str, Any]) -> None:
    """Initialize independent defaults for one browser session."""
    for key, value in WORKBENCH_DEFAULTS.items():
        if key not in state:
            state[key] = deepcopy(value)


def set_ui_step(state: MutableMapping[str, Any], step: str) -> None:
    if step not in UI_STEPS:
        raise ValueError(f"Unknown workbench step: {step}")
    previous = state.get("ui_step", "intake")
    state["ui_last_step"] = previous
    state["ui_step"] = step


def invalidate_planning_results(
    state: MutableMapping[str, Any],
    *,
    reason: str,
    keep_preference_draft: bool = True,
) -> None:
    """Invalidate results after a preference/configuration change.

    The signed draft may be retained only when it remains the current server-issued
    draft. Callers that mutate draft content must obtain a new receipt first.
    """
    for key in (
        "preference_plan_result",
        "preference_comparison_result",
        "preference_selected_destination_id",
        "structured_plan_result",
        "structured_plan_fingerprint",
        "structured_comparison_result",
        "structured_comparison_fingerprint",
        "active_plan_id",
        "confirmed_preferences_fingerprint",
        "pending_planning_request",
    ):
        state[key] = None
    state["preference_confirmed"] = False
    state["preference_planning"] = False
    state["planning_request_state"] = "idle"
    state["result_invalidation_reason"] = reason
    state["ui_last_error"] = None
    if not keep_preference_draft:
        state["preference_response"] = None
        state["preference_history"] = []
    response = state.get("preference_response")
    target = "confirmation" if response and response.get("is_complete") else "intake"
    set_ui_step(state, target)


def record_transport_error(state: MutableMapping[str, Any], message: str) -> None:
    """Record a retryable transport failure without discarding valid preferences."""
    state["planning_request_state"] = "error"
    state["ui_last_error"] = message


def invalidate_structured_results(
    state: MutableMapping[str, Any],
    *,
    reason: str,
) -> None:
    """Invalidate only results created from the structured-form entry."""
    for key in (
        "structured_plan_result",
        "structured_plan_fingerprint",
        "structured_comparison_result",
        "structured_comparison_fingerprint",
        "active_plan_id",
        "pending_planning_request",
    ):
        state[key] = None
    state["planning_request_state"] = "idle"
    state["result_invalidation_reason"] = reason
    state["ui_last_error"] = None
    set_ui_step(state, "intake")


def clear_transport_error(state: MutableMapping[str, Any]) -> None:
    state["ui_last_error"] = None
    if state.get("planning_request_state") == "error":
        state["planning_request_state"] = "idle"


def reset_workbench_state(state: MutableMapping[str, Any]) -> None:
    """Remove only state owned by the current workbench task."""
    for key in WORKBENCH_DEFAULTS:
        state.pop(key, None)
    initialize_workbench_state(state)
