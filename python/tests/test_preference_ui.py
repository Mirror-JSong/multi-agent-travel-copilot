"""Streamlit AppTest and state-isolation coverage for Stage A batch 2."""

from __future__ import annotations

import copy
import socket
import tempfile
import threading
import time
from datetime import date
from pathlib import Path

import httpx
import pytest
import uvicorn

from agents.flight_agent import FlightAgent
from api.app import app
from ui.preference_api_client import PreferenceApiClient, PreferenceApiTimeout
from ui.preference_session import (
    accept_preference_response,
    initialize_preference_session,
    reset_preference_session,
)


APP_PATH = Path(__file__).parents[1] / "ui" / "streamlit_app.py"


@pytest.fixture(scope="module")
def live_api_base_url() -> str:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    config = uvicorn.Config(
        app,
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
        pytest.fail("Uvicorn test server did not start")

    yield base_url

    server.should_exit = True
    thread.join(timeout=5)
    assert not thread.is_alive()


@pytest.fixture
def app_test_environment(monkeypatch, tmp_path, live_api_base_url):
    monkeypatch.setenv("TRAVEL_API_BASE_URL", live_api_base_url)
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setenv("TMP", str(tmp_path))
    previous_tempdir = tempfile.tempdir
    tempfile.tempdir = str(tmp_path)
    yield
    tempfile.tempdir = previous_tempdir


def _new_app_test():
    from streamlit.testing.v1 import AppTest

    app_test = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
    assert not app_test.exception
    return app_test


def _parse_with_app(app_test, text: str):
    app_test.date_input(key="preference_reference_date").set_value(date(2026, 9, 24))
    app_test.text_area(key="natural_requirement").set_value(text)
    app_test.button(key="parse_preference").click().run()
    assert not app_test.exception
    return app_test


def _clarify_with_app(app_test, answer: str):
    app_test.text_area(key="clarification_answer").set_value(answer)
    app_test.button(key="clarify_preference").click().run()
    assert not app_test.exception
    return app_test


def test_t6_plain_state_helpers_keep_sessions_independent_and_reset_cleanly() -> None:
    first: dict = {}
    second: dict = {}
    initialize_preference_session(first, default_reference_date=date(2026, 9, 24))
    initialize_preference_session(second, default_reference_date=date(2026, 9, 24))

    response = {"questions": [], "draft": {"departure_city": "上海"}}
    accept_preference_response(first, response, user_text="从上海出发")

    assert first["preference_response"] == response
    assert len(first["preference_history"]) == 2
    assert second["preference_response"] is None
    assert second["preference_history"] == []

    reset_preference_session(first, default_reference_date=date(2026, 9, 25))
    assert first["preference_response"] is None
    assert first["preference_history"] == []
    assert first["preference_confirmed"] is False
    assert first["preference_reference_date"] == date(2026, 9, 25)


def test_t10_t15_multiturn_app_flow_reaches_confirmation_and_can_restart(
    app_test_environment,
) -> None:
    app_test = _new_app_test()
    assert any("确定性 Mock Parser" in item.value for item in app_test.info)
    app_test = _parse_with_app(
        app_test,
        "国庆想和朋友从上海出去玩五天，预算一万五，不想太累，喜欢拍照和吃东西。",
    )

    first = app_test.session_state["preference_response"]
    assert first["is_complete"] is False
    assert first["draft"]["departure_city"] == "上海"
    assert len(app_test.session_state["preference_history"]) == 2
    assert app_test.button(key="confirm_preference").disabled is True
    assert any(
        expander.label == "查看全部识别字段与来源"
        for expander in app_test.expander
    )

    app_test = _clarify_with_app(
        app_test,
        "我们一共3人，2026年10月1日出发，舒适游。",
    )

    final = app_test.session_state["preference_response"]
    assert final["is_complete"] is True
    assert final["draft"]["end_date"] == "2026-10-06"
    assert final["date_summary"]["activity_end_date"] == "2026-10-05"
    assert final["date_summary"]["hotel_nights"] == 5
    assert len(app_test.session_state["preference_history"]) == 4
    assert app_test.button(key="confirm_preference").disabled is False
    metrics = {metric.label: metric.value for metric in app_test.metric}
    assert metrics["出发日期"] == "2026-10-01"
    assert metrics["返程日期"] == "2026-10-06"
    rendered_text = [item.value for item in app_test.markdown]
    assert any("活动日期范围" in value and "2026-10-05" in value for value in rendered_text)
    assert any("预计住宿晚数" in value and "5 晚" in value for value in rendered_text)
    assert any("正式规划" in item.value for item in app_test.info)

    app_test.button(key="confirm_preference").click().run()
    assert app_test.session_state["preference_confirmed"] is True
    assert app_test.session_state["preference_plan_result"]["plan"]["state"] == "completed"
    assert any("偏好已确认" in item.value for item in app_test.success)
    assert app_test.button(key="confirm_preference").disabled is True

    app_test.button(key="reset_preference").click().run()
    assert not app_test.exception
    assert app_test.session_state["preference_response"] is None
    assert app_test.session_state["preference_history"] == []
    assert app_test.session_state["preference_confirmed"] is False
    assert app_test.session_state["preference_plan_result"] is None


def test_t9_t15_date_conflict_is_visible_and_second_multiturn_flow_resolves_it(
    app_test_environment,
) -> None:
    app_test = _parse_with_app(
        _new_app_test(),
        "从上海出发，预算一万，2人，舒适游，"
        "2026年10月1日到2026年10月6日，玩三天。",
    )

    first = app_test.session_state["preference_response"]
    assert first["is_complete"] is False
    assert any(question["field"] == "duration_days" for question in first["questions"])
    assert any("旅行天数" in item.value for item in app_test.warning)

    app_test = _clarify_with_app(app_test, "旅行天数改成五天。")

    resolved = app_test.session_state["preference_response"]
    assert resolved["is_complete"] is True
    assert resolved["draft"]["duration_days"] == 5
    assert resolved["draft"]["end_date"] == "2026-10-06"
    assert resolved["date_summary"]["hotel_nights"] == 5

    app_test = _clarify_with_app(app_test, "预算改成两万。")
    modified = app_test.session_state["preference_response"]
    assert modified["is_complete"] is True
    assert modified["draft"]["budget"] == 20000
    assert modified["draft"]["start_date"] == "2026-10-01"

    app_test.button(key="confirm_preference").click().run()
    assert app_test.session_state["preference_confirmed"] is True
    assert app_test.session_state["preference_plan_result"]["plan"]["state"] == "completed"


def test_t6_two_apptest_browser_sessions_do_not_share_state(app_test_environment) -> None:
    shanghai = _parse_with_app(
        _new_app_test(),
        "从上海出发，预算五万，2人，舒适游，2026年10月1日到2026年10月5日。",
    )
    beijing = _parse_with_app(
        _new_app_test(),
        "从北京出发，预算100元，2人，舒适游，2026年10月1日到2026年10月5日。",
    )

    shanghai.button(key="confirm_preference").click().run()
    beijing.button(key="confirm_preference").click().run()

    assert shanghai.session_state["preference_response"]["draft"]["departure_city"] == "上海"
    assert beijing.session_state["preference_response"]["draft"]["departure_city"] == "北京"
    assert shanghai.session_state["preference_history"] != beijing.session_state["preference_history"]
    assert shanghai.session_state["preference_plan_result"]["plan"]["state"] == "completed"
    assert beijing.session_state["preference_plan_result"]["plan"]["state"] == "budget_infeasible"
    assert shanghai.session_state["preference_plan_result"]["preferences"]["budget"] == 50000
    assert beijing.session_state["preference_plan_result"]["preferences"]["budget"] == 100
    assert any("当前分阶段搜索策略" in item.value for item in beijing.warning)


def test_t11_ui_preserves_valid_state_when_api_becomes_unavailable(
    app_test_environment,
    monkeypatch,
) -> None:
    app_test = _parse_with_app(_new_app_test(), "从上海出发，预算一万。")
    previous_response = copy.deepcopy(app_test.session_state["preference_response"])
    previous_history = copy.deepcopy(app_test.session_state["preference_history"])
    monkeypatch.setenv("TRAVEL_API_BASE_URL", "http://127.0.0.1:1")

    app_test = _clarify_with_app(app_test, "2人，舒适游。")

    assert app_test.session_state["preference_response"] == previous_response
    assert app_test.session_state["preference_history"] == previous_history
    assert any("无法连接偏好服务" in item.value for item in app_test.error)


def test_planning_api_error_keeps_confirmable_preferences_for_retry(
    app_test_environment,
    monkeypatch,
) -> None:
    app_test = _parse_with_app(
        _new_app_test(),
        "从上海出发，预算两万，2人，舒适游，2026年10月1日到2026年10月5日。",
    )
    previous_response = copy.deepcopy(app_test.session_state["preference_response"])
    monkeypatch.setenv("TRAVEL_API_BASE_URL", "http://127.0.0.1:1")

    app_test.button(key="confirm_preference").click().run()

    assert app_test.session_state["preference_response"] == previous_response
    assert app_test.session_state["preference_plan_result"] is None
    assert app_test.session_state["preference_confirmed"] is False
    assert app_test.button(key="confirm_preference").disabled is False
    assert any("无法连接偏好服务" in item.value for item in app_test.error)


def test_required_agent_failure_is_rendered_as_failed_plan(
    app_test_environment,
    monkeypatch,
) -> None:
    async def fail_flight(self, state):
        raise RuntimeError("ui injected flight failure")

    monkeypatch.setattr(FlightAgent, "execute", fail_flight)
    app_test = _parse_with_app(
        _new_app_test(),
        "从上海出发，预算两万，2人，舒适游，2026年10月1日到2026年10月5日。",
    )

    app_test.button(key="confirm_preference").click().run()

    plan = app_test.session_state["preference_plan_result"]["plan"]
    assert plan["state"] == "failed"
    assert plan["budget_breakdown"] is None
    assert plan["agent_failures"][0]["reason"] == "ui injected flight failure"
    assert any("内部执行失败" in item.value for item in app_test.error)
    assert any("ui injected flight failure" in item.value for item in app_test.error)
    assert any(
        metric.label == "当前规划状态" and "FAILED" in metric.value
        for metric in app_test.metric
    )


def test_t11_api_client_maps_timeout_to_explicit_error(monkeypatch) -> None:
    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(httpx, "post", timeout)

    with pytest.raises(PreferenceApiTimeout, match="请求超时"):
        PreferenceApiClient("http://127.0.0.1:9999", timeout_seconds=0.01).parse(
            "从上海出发",
            date(2026, 9, 24),
        )


def test_t12_original_structured_form_still_runs(app_test_environment) -> None:
    app_test = _new_app_test()
    app_test.radio(key="input_mode").set_value("结构化表单").run()
    assert not app_test.exception
    assert app_test.text_input[0].label == "出发城市"

    app_test.number_input[0].set_value(50000)
    app_test.button(key="start_structured_plan").click().run()

    assert not app_test.exception
    assert any("行程规划完成" in item.value for item in app_test.success)
    assert any(metric.label == "当前规划状态" for metric in app_test.metric)
    assert next(
        metric.value
        for metric in app_test.metric
        if metric.label == "当前规划状态"
    ) == "COMPLETED"
    assert any("业务状态代码：completed" in item.value for item in app_test.caption)


def test_c0_structured_relaxed_pace_renders_explicit_rest(app_test_environment) -> None:
    app_test = _new_app_test()
    app_test.radio(key="input_mode").set_value("结构化表单").run()
    app_test.number_input[0].set_value(50000)
    app_test.selectbox(key="structured_pace").set_value("relaxed")
    app_test.button(key="start_structured_plan").click().run()

    assert not app_test.exception
    assert any("旅行节奏：relaxed" in item.value for item in app_test.caption)
    assert any("休息（¥0）" in item.value for item in app_test.markdown)
