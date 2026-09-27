"""Stage C3 Streamlit AppTest coverage for dual-plan product integration."""

from __future__ import annotations

import copy
import importlib
import socket
import tempfile
import threading
import time
from datetime import date
from pathlib import Path

import httpx
import pytest
import uvicorn

from orchestrator.comparison_service import PlanComparisonApplicationService
from tests.fixtures.plan_comparison import c2_orchestrator


APP_PATH = Path(__file__).parents[1] / "ui" / "streamlit_app.py"
api_module = importlib.import_module("api.app")
COMPLETE_NATURAL_REQUEST = (
    "\u4ece\u4e0a\u6d77\u51fa\u53d1\uff0c\u9884\u7b97\u4e94\u4e07\uff0c2\u4eba\uff0c"
    "\u8212\u9002\u6e38\uff0c2026\u5e7410\u67081\u65e5\u52302026\u5e7410\u67086\u65e5\u3002"
)


@pytest.fixture(scope="module")
def c3_live_api_base_url() -> str:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = uvicorn.Config(
        api_module.app,
        host="127.0.0.1",
        port=port,
        log_level="error",
        access_log=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(f"{base_url}/api/health", timeout=0.2).status_code == 200:
                break
        except httpx.RequestError:
            time.sleep(0.02)
    else:
        server.should_exit = True
        thread.join(timeout=5)
        pytest.fail("C3 Uvicorn test server did not start")
    yield base_url
    server.should_exit = True
    thread.join(timeout=5)
    assert not thread.is_alive()


@pytest.fixture
def c3_app_environment(monkeypatch, tmp_path, c3_live_api_base_url):
    monkeypatch.setattr(
        api_module,
        "_comparison_service",
        PlanComparisonApplicationService(orchestrator_factory=c2_orchestrator),
    )
    monkeypatch.setenv("TRAVEL_API_BASE_URL", c3_live_api_base_url)
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setenv("TMP", str(tmp_path))
    previous_tempdir = tempfile.tempdir
    tempfile.tempdir = str(tmp_path)
    yield
    tempfile.tempdir = previous_tempdir


def _new_app_test():
    from streamlit.testing.v1 import AppTest

    result = AppTest.from_file(str(APP_PATH), default_timeout=30).run()
    assert not result.exception
    return result


def _parse_complete(app_test):
    app_test.date_input(key="preference_reference_date").set_value(date(2026, 9, 24))
    app_test.text_area(key="natural_requirement").set_value(COMPLETE_NATURAL_REQUEST)
    app_test.button(key="parse_preference").click().run()
    assert not app_test.exception
    assert app_test.session_state["preference_response"]["is_complete"] is True
    return app_test


def _switch_to_structured(app_test):
    mode = app_test.radio(key="input_mode")
    mode.set_value(mode.options[1]).run()
    assert not app_test.exception
    return app_test


def test_c3_t2_t6_natural_confirmation_reaches_dual_plan_comparison(
    c3_app_environment,
) -> None:
    app_test = _parse_complete(_new_app_test())
    app_test.radio(key="preference_planning_mode").set_value("compare").run()
    app_test.button(key="confirm_preference").click().run()

    assert not app_test.exception
    result = app_test.session_state["preference_comparison_result"]
    assert result["multi_plan"]["generated_plan_count"] == 2
    assert len(result["comparison"]["plan_evaluations"]) == 2
    assert result["comparison"]["recommendation"]["decision"] in {
        "recommended",
        "tie",
    }
    assert app_test.session_state["preference_plan_result"] is None
    assert app_test.session_state["preference_confirmed"] is True


def test_c3_t8_structured_only_feasible_has_no_fake_second_score(
    c3_app_environment,
) -> None:
    app_test = _switch_to_structured(_new_app_test())
    app_test.radio(key="structured_planning_mode").set_value("compare").run()
    # The structured form defaults to four hotel nights; this fixed budget leaves
    # destination A feasible after optimization while B remains infeasible.
    app_test.number_input[0].set_value(3000)
    app_test.button(key="start_structured_plan").click().run()

    assert not app_test.exception
    result = app_test.session_state["structured_comparison_result"]
    assert result["comparison"]["recommendation"]["decision"] == "only_feasible"
    assert [plan["status"] for plan in result["multi_plan"]["plans"]] == [
        "completed",
        "budget_infeasible",
    ]
    evaluations = result["comparison"]["plan_evaluations"]
    # C2 intentionally does not expose a normal cross-plan score when only one
    # plan is feasible; the conditional recommendation comes from hard status.
    assert evaluations[0]["overall_score"] is None
    assert evaluations[1]["overall_score"] is None


def test_c3_t14_editing_structured_preferences_invalidates_old_comparison(
    c3_app_environment,
) -> None:
    app_test = _switch_to_structured(_new_app_test())
    app_test.radio(key="structured_planning_mode").set_value("compare").run()
    app_test.number_input[0].set_value(5000)
    app_test.button(key="start_structured_plan").click().run()
    old_result = copy.deepcopy(app_test.session_state["structured_comparison_result"])
    assert old_result is not None

    app_test.number_input[0].set_value(6000).run()

    assert app_test.session_state["structured_comparison_result"] is None
    assert app_test.session_state["structured_comparison_fingerprint"] is None


def test_c3_t15_independent_streamlit_sessions_do_not_share_results(
    c3_app_environment,
) -> None:
    first = _parse_complete(_new_app_test())
    second = _new_app_test()
    first.radio(key="preference_planning_mode").set_value("compare").run()
    first.button(key="confirm_preference").click().run()

    assert first.session_state["preference_comparison_result"] is not None
    assert second.session_state["preference_comparison_result"] is None
    assert second.session_state["preference_response"] is None


def test_c3_t16_network_error_preserves_confirmable_draft(
    c3_app_environment,
    monkeypatch,
) -> None:
    app_test = _parse_complete(_new_app_test())
    previous = copy.deepcopy(app_test.session_state["preference_response"])
    app_test.radio(key="preference_planning_mode").set_value("compare").run()
    monkeypatch.setenv("TRAVEL_API_BASE_URL", "http://127.0.0.1:1")

    app_test.button(key="confirm_preference").click().run()

    assert app_test.session_state["preference_response"] == previous
    assert app_test.session_state["preference_comparison_result"] is None
    assert app_test.session_state["preference_confirmed"] is False
    assert app_test.radio(key="preference_planning_mode").value == "compare"
    assert app_test.error


@pytest.mark.parametrize(
    ("budget", "expected_decision", "visible_text"),
    [
        (5000, "tie", "两套方案并列"),
        (2500, "only_feasible", "仅一套方案完整可行"),
        (100, "no_recommendation", "当前无法综合推荐"),
    ],
)
def test_d2_comparison_page_preserves_server_decision_semantics(
    c3_app_environment,
    budget,
    expected_decision,
    visible_text,
) -> None:
    app_test = _switch_to_structured(_new_app_test())
    app_test.radio(key="structured_planning_mode").set_value("compare").run()
    app_test.number_input(key="structured_budget").set_value(budget)
    app_test.text_input(key="structured_departure").set_value("Origin")
    app_test.date_input(key="structured_start_date").set_value(date(2026, 5, 1))
    app_test.date_input(key="structured_end_date").set_value(date(2026, 5, 3))
    app_test.selectbox(key="structured_pace").set_value("balanced")
    app_test.multiselect(key="structured_interests").set_value(["文化"])
    app_test.button(key="start_structured_plan").click().run()

    result = app_test.session_state["structured_comparison_result"]
    assert result["comparison"]["recommendation"]["decision"] == expected_decision
    assert any(visible_text in item.value for item in app_test.markdown)
    if expected_decision != "tie":
        assert any(
            evaluation["overall_score"] is None
            for evaluation in result["comparison"]["plan_evaluations"]
        )


def test_d2_detail_switch_reuses_existing_comparison_without_replanning(
    c3_app_environment,
) -> None:
    app_test = _parse_complete(_new_app_test())
    app_test.radio(key="preference_planning_mode").set_value("compare").run()
    app_test.button(key="confirm_preference").click().run()
    original_result = copy.deepcopy(
        app_test.session_state["preference_comparison_result"]
    )
    first_plan = original_result["multi_plan"]["plans"][0]

    app_test.button(key=f"comparison_view_{first_plan['destination_id']}").click().run()

    assert app_test.session_state["ui_step"] == "detail"
    assert app_test.session_state["active_plan_id"] == first_plan["destination_id"]
    assert app_test.session_state["preference_comparison_result"] == original_result
    assert any("每日行程与预算" in item.value for item in app_test.markdown)
