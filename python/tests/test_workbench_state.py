"""Stage D2 session-state contract tests for the Streamlit workbench."""

from __future__ import annotations

import pytest

from ui.workbench_state import (
    UI_STEPS,
    clear_transport_error,
    initialize_workbench_state,
    invalidate_planning_results,
    invalidate_structured_results,
    record_transport_error,
    reset_workbench_state,
    set_ui_step,
)


def _populated_state() -> dict:
    state: dict = {}
    initialize_workbench_state(state)
    state.update(
        {
            "preference_response": {
                "is_complete": True,
                "draft": {"departure_city": "上海"},
            },
            "preference_history": [{"role": "user", "content": "从上海出发"}],
            "preference_confirmed": True,
            "preference_planning": False,
            "preference_plan_result": {"plan": {"state": "completed"}},
            "preference_comparison_result": {"comparison": {"decision": "recommendation"}},
            "preference_selected_destination_id": "destination-a",
            "structured_plan_result": {"state": "completed"},
            "structured_plan_fingerprint": "single-v1",
            "structured_comparison_result": {"comparison": {"decision": "tie"}},
            "structured_comparison_fingerprint": "compare-v1",
            "active_plan_id": "destination-a",
            "confirmed_preferences_fingerprint": "draft-v1",
            "planning_request_state": "success",
        }
    )
    return state


def test_d2_initial_state_is_independent_between_browser_sessions() -> None:
    first: dict = {}
    second: dict = {}

    initialize_workbench_state(first)
    initialize_workbench_state(second)
    set_ui_step(first, "confirmation")
    first["ui_last_error"] = "session-one-only"

    assert first["ui_step"] == "confirmation"
    assert second["ui_step"] == "intake"
    assert second["ui_last_error"] is None


def test_d2_preference_change_invalidates_every_stale_plan_and_score() -> None:
    state = _populated_state()

    invalidate_planning_results(state, reason="预算已修改")

    for key in (
        "preference_plan_result",
        "preference_comparison_result",
        "preference_selected_destination_id",
        "structured_plan_result",
        "structured_comparison_result",
        "active_plan_id",
        "confirmed_preferences_fingerprint",
        "pending_planning_request",
    ):
        assert state[key] is None
    assert state["preference_confirmed"] is False
    assert state["planning_request_state"] == "idle"
    assert state["result_invalidation_reason"] == "预算已修改"
    assert state["preference_response"]["draft"]["departure_city"] == "上海"
    assert state["ui_step"] == "confirmation"


def test_d2_incomplete_preference_returns_to_intake_without_stale_result() -> None:
    state = _populated_state()
    state["preference_response"]["is_complete"] = False

    invalidate_planning_results(state, reason="日期需重新澄清")

    assert state["ui_step"] == "intake"
    assert state["preference_plan_result"] is None
    assert state["preference_comparison_result"] is None


def test_d2_transport_error_preserves_confirmed_preferences_for_retry() -> None:
    state = _populated_state()
    signed_draft = state["preference_response"]

    record_transport_error(state, "API 超时")

    assert state["planning_request_state"] == "error"
    assert state["ui_last_error"] == "API 超时"
    assert state["preference_response"] is signed_draft
    assert state["preference_confirmed"] is True

    clear_transport_error(state)
    assert state["planning_request_state"] == "idle"
    assert state["ui_last_error"] is None


def test_d2_structured_edit_only_invalidates_structured_results() -> None:
    state = _populated_state()
    natural_result = state["preference_comparison_result"]

    invalidate_structured_results(state, reason="结构化表单已修改")

    assert state["structured_plan_result"] is None
    assert state["structured_comparison_result"] is None
    assert state["active_plan_id"] is None
    assert state["preference_comparison_result"] is natural_result
    assert state["preference_confirmed"] is True
    assert state["ui_step"] == "intake"


def test_d2_reset_removes_workbench_task_state_but_preserves_unrelated_state() -> None:
    state = _populated_state()
    state["unrelated_user_state"] = {"keep": True}

    reset_workbench_state(state)

    assert state["ui_step"] == "intake"
    assert state["planning_request_state"] == "idle"
    assert state["structured_plan_result"] is None
    assert state["unrelated_user_state"] == {"keep": True}


def test_d2_navigation_rejects_unknown_pages() -> None:
    state: dict = {}
    initialize_workbench_state(state)

    with pytest.raises(ValueError, match="Unknown workbench step"):
        set_ui_step(state, "checkout")

    assert tuple(UI_STEPS) == (
        "intake",
        "confirmation",
        "planning",
        "comparison",
        "detail",
    )
