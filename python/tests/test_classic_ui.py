"""Acceptance coverage for the independent Classic two-column Streamlit UI."""

from __future__ import annotations

import copy
import importlib
import tempfile
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from orchestrator.comparison_service import PlanComparisonApplicationService
from tests.fixtures.plan_comparison import c2_orchestrator
from ui.classic_state import (
    clarification_answer_from_form,
    initialize_classic_state,
    invalidate_classic_results,
    preference_conflicts,
)


APP_PATH = Path(__file__).parents[1] / "ui" / "streamlit_classic_app.py"
api_module = importlib.import_module("api.app")
COMPLETE_REQUEST = (
    "从上海出发，预算五万，2人，舒适游，节奏适中，"
    "2026年10月1日到2026年10月6日，喜欢文化。"
)


@pytest.fixture
def classic_environment(monkeypatch, tmp_path):
    """Route the UI through real FastAPI routes without opening a socket."""
    monkeypatch.setattr(
        api_module,
        "_comparison_service",
        PlanComparisonApplicationService(orchestrator_factory=c2_orchestrator),
    )
    monkeypatch.setenv("TRAVEL_API_BASE_URL", "http://classic.test")
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setenv("TMP", str(tmp_path))
    previous_tempdir = tempfile.tempdir
    tempfile.tempdir = str(tmp_path)
    client = TestClient(api_module.app)
    called_paths: list[str] = []

    def local_post(url, *, json, timeout):
        path = urlparse(url).path
        called_paths.append(path)
        return client.post(path, json=json)

    monkeypatch.setattr(httpx, "post", local_post)
    yield client, called_paths
    tempfile.tempdir = previous_tempdir


def _new_app_test():
    from streamlit.testing.v1 import AppTest

    app_test = AppTest.from_file(str(APP_PATH), default_timeout=30).run()
    assert not app_test.exception
    return app_test


def _set_fixed_form(app_test, *, budget: float = 50_000.0) -> None:
    app_test.number_input(key="classic_budget").set_value(budget)
    app_test.text_input(key="classic_departure_city").set_value("Origin")
    app_test.date_input(key="classic_start_date").set_value(date(2026, 5, 1))
    app_test.date_input(key="classic_end_date").set_value(date(2026, 5, 3))
    app_test.selectbox(key="classic_travel_style").set_value("comfort")
    app_test.selectbox(key="classic_pace").set_value("balanced")
    app_test.number_input(key="classic_num_travelers").set_value(1)
    app_test.multiselect(key="classic_interests").set_value(["文化"])


def _set_complete_natural_form(app_test) -> None:
    app_test.number_input(key="classic_budget").set_value(50_000.0)
    app_test.text_input(key="classic_departure_city").set_value("上海")
    app_test.date_input(key="classic_start_date").set_value(date(2026, 10, 1))
    app_test.date_input(key="classic_end_date").set_value(date(2026, 10, 6))
    app_test.selectbox(key="classic_travel_style").set_value("comfort")
    app_test.selectbox(key="classic_pace").set_value("balanced")
    app_test.number_input(key="classic_num_travelers").set_value(2)
    app_test.multiselect(key="classic_interests").set_value(["文化"])
    app_test.date_input(key="classic_reference_date").set_value(date(2026, 9, 24))


def test_classic_state_is_isolated_and_invalidates_only_derived_results() -> None:
    first: dict = {}
    second: dict = {}
    initialize_classic_state(first)
    initialize_classic_state(second)
    first["classic_plan_result"] = {"plan": "first"}
    first["classic_preference_response"] = {"draft": {}}
    first["classic_nl_confirmed"] = True

    invalidate_classic_results(
        first,
        reason="changed",
        signed_draft_stale=True,
    )

    assert first["classic_plan_result"] is None
    assert first["classic_preference_response"] == {"draft": {}}
    assert first["classic_nl_confirmed"] is False
    assert first["classic_nl_stale"] is True
    assert second["classic_plan_result"] is None
    assert second["classic_preference_response"] is None


def test_conflicts_and_form_clarification_are_deterministic() -> None:
    form = {
        "budget": 15_000.0,
        "departure_city": "上海",
        "start_date": date(2026, 10, 1),
        "end_date": date(2026, 10, 6),
        "travel_style": "comfort",
        "pace": "balanced",
        "num_travelers": 3,
        "interests": ["摄影", "美食"],
    }
    response = {
        "draft": {
            **{key: value for key, value in form.items() if key not in {"start_date", "end_date"}},
            "start_date": "2026-10-01",
            "end_date": "2026-10-06",
            "budget": 20_000.0,
            "field_sources": {key: "explicit" for key in form},
        }
    }

    assert [item["field"] for item in preference_conflicts(response, form)] == ["budget"]
    answer = clarification_answer_from_form(form)
    assert answer == clarification_answer_from_form(form)
    assert "总预算15000元" in answer
    assert "节奏适中" in answer
    assert "喜欢摄影和美食" in answer


def test_classic_initial_layout_and_structured_single_plan(classic_environment) -> None:
    _, called_paths = classic_environment
    app_test = _new_app_test()

    assert app_test.title[0].value == "✈️ 多 Agent 智能旅游行程规划"
    assert app_test.checkbox(key="classic_compare").value is False
    assert app_test.text_area(key="classic_notes").label == "额外备注"
    assert not app_test.tabs

    _set_fixed_form(app_test)
    app_test.button(key="classic_start_planning").click().run()

    assert not app_test.exception
    assert called_paths[-1] == "/api/plan/full"
    assert app_test.session_state["classic_plan_result"]["state"] == "completed"
    assert [tab.label for tab in app_test.tabs] == [
        "✈️ 航班",
        "🏨 酒店",
        "📅 行程",
        "💰 预算",
    ]
    assert any("Mock 模拟天气" in item.value for item in app_test.caption)


def test_first_mode_selection_does_not_claim_an_old_plan_was_invalidated(
    classic_environment,
) -> None:
    app_test = _new_app_test()
    app_test.checkbox(key="classic_compare").check().run()

    assert app_test.session_state["classic_invalidation_reason"] is None
    assert not any("旧规划和评分已失效" in item.value for item in app_test.info)


def test_dual_plan_switch_reuses_existing_result_and_server_scores(
    classic_environment,
) -> None:
    _, called_paths = classic_environment
    app_test = _new_app_test()
    _set_fixed_form(app_test, budget=5_000.0)
    app_test.checkbox(key="classic_compare").check()
    app_test.button(key="classic_start_planning").click().run()

    result = copy.deepcopy(app_test.session_state["classic_comparison_result"])
    assert not app_test.exception
    assert called_paths[-1] == "/api/plans/compare"
    assert result["multi_plan"]["generated_plan_count"] == 2
    assert result["comparison"]["recommendation"]["decision"] == "tie"
    assert any("两套方案并列" in item.value for item in app_test.info)
    before_switch_calls = list(called_paths)
    selector = app_test.radio(key="classic_destination_selector")
    selector.set_value(selector.options[1]).run()

    assert called_paths == before_switch_calls
    assert app_test.session_state["classic_comparison_result"] == result
    assert (
        app_test.session_state["classic_active_plan_id"]
        == result["multi_plan"]["plans"][1]["destination_id"]
    )


def test_notes_parse_conflict_requires_explicit_adoption_then_signed_plan(
    classic_environment,
) -> None:
    _, called_paths = classic_environment
    app_test = _new_app_test()
    app_test.date_input(key="classic_reference_date").set_value(date(2026, 9, 24))
    app_test.text_area(key="classic_notes").set_value(COMPLETE_REQUEST)
    app_test.button(key="classic_parse_notes").click().run()

    assert app_test.session_state["classic_preference_response"]["is_complete"] is True
    assert app_test.button(key="classic_adopt_notes")
    assert any("存在冲突" in item.value for item in app_test.warning)

    app_test.button(key="classic_adopt_notes").click().run()
    assert app_test.text_input(key="classic_departure_city").value == "上海"
    assert app_test.number_input(key="classic_budget").value == 50_000.0
    assert app_test.selectbox(key="classic_pace").value == "balanced"
    assert app_test.button(key="classic_confirm_natural")

    app_test.button(key="classic_confirm_natural").click().run()
    assert app_test.session_state["classic_nl_confirmed"] is True
    app_test.button(key="classic_start_planning").click().run()

    assert called_paths[-1] == "/api/preferences/plan"
    assert app_test.session_state["classic_plan_result"]["plan"]["state"] == "completed"


def test_form_change_invalidates_receipt_and_old_results(classic_environment) -> None:
    app_test = _new_app_test()
    _set_complete_natural_form(app_test)
    app_test.text_area(key="classic_notes").set_value(COMPLETE_REQUEST)
    app_test.button(key="classic_parse_notes").click().run()
    app_test.button(key="classic_confirm_natural").click().run()
    app_test.button(key="classic_start_planning").click().run()
    previous_response = copy.deepcopy(app_test.session_state["classic_preference_response"])
    assert app_test.session_state["classic_plan_result"] is not None

    app_test.number_input(key="classic_budget").set_value(40_000.0).run()

    assert app_test.session_state["classic_preference_response"] == previous_response
    assert app_test.session_state["classic_nl_stale"] is True
    assert app_test.session_state["classic_nl_confirmed"] is False
    assert app_test.session_state["classic_plan_result"] is None
    assert any("旧签名草稿" in item.value for item in app_test.warning)


@pytest.mark.parametrize(
    ("budget", "decision", "visible_text"),
    [
        (4_000.0, "recommended", "推荐方案"),
        (5_000.0, "tie", "两套方案并列"),
        (2_500.0, "only_feasible", "仅一套方案可行"),
        (100.0, "no_recommendation", "当前无法推荐"),
    ],
)
def test_comparison_business_states_preserve_server_semantics(
    classic_environment,
    budget,
    decision,
    visible_text,
) -> None:
    app_test = _new_app_test()
    _set_fixed_form(app_test, budget=budget)
    app_test.checkbox(key="classic_compare").check()
    app_test.button(key="classic_start_planning").click().run()

    payload = app_test.session_state["classic_comparison_result"]
    assert payload["comparison"]["recommendation"]["decision"] == decision
    messages = [item.value for item in app_test.info]
    messages += [item.value for item in app_test.warning]
    messages += [item.value for item in app_test.success]
    assert any(visible_text in value for value in messages)
    if decision in {"only_feasible", "no_recommendation"}:
        assert any(
            item["overall_score"] is None
            for item in payload["comparison"]["plan_evaluations"]
        )


def test_incomplete_notes_are_actively_clarified_then_adopted(
    classic_environment,
) -> None:
    app_test = _new_app_test()
    app_test.number_input(key="classic_budget").set_value(10_000.0)
    app_test.text_input(key="classic_departure_city").set_value("上海")
    app_test.date_input(key="classic_reference_date").set_value(date(2026, 9, 24))
    app_test.text_area(key="classic_notes").set_value("从上海出发，预算一万。")
    app_test.button(key="classic_parse_notes").click().run()

    assert app_test.session_state["classic_preference_response"]["is_complete"] is False
    assert app_test.button(key="classic_submit_clarification")
    assert any("还需要确认" in item.value for item in app_test.markdown)

    app_test.text_input(key="classic_clarification_answer").set_value(
        "一共2人，2026年10月1日到2026年10月6日，舒适游。"
    )
    app_test.button(key="classic_submit_clarification").click().run()
    assert app_test.session_state["classic_preference_response"]["is_complete"] is True
    assert len(app_test.session_state["classic_preference_history"]) == 4
    assert app_test.button(key="classic_adopt_notes")

    app_test.button(key="classic_adopt_notes").click().run()
    app_test.button(key="classic_confirm_natural").click().run()
    assert app_test.session_state["classic_nl_confirmed"] is True


def test_unavailable_weather_and_agent_failure_are_not_disguised(
    monkeypatch,
    tmp_path,
) -> None:
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("ENABLE_WEATHER_DEMO_ENDPOINTS", "1")
    monkeypatch.setenv("TRAVEL_API_BASE_URL", "http://classic-demo.test")
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setenv("TMP", str(tmp_path))
    previous_tempdir = tempfile.tempdir
    tempfile.tempdir = str(tmp_path)
    client = TestClient(api_module.app)
    scenario = {"value": "unavailable_default"}

    def local_post(url, *, json, timeout):
        path = urlparse(url).path
        if path == "/api/plan/full":
            return client.post(
                "/api/demo/weather/plan",
                json={
                    "scenario_id": (
                        "sunny_default" if scenario["value"] == "failure" else scenario["value"]
                    ),
                    "inject_agent_failure": scenario["value"] == "failure",
                },
            )
        return client.post(path, json=json)

    monkeypatch.setattr(httpx, "post", local_post)
    try:
        app_test = AppTest.from_file(str(APP_PATH), default_timeout=30).run()
        app_test.button(key="classic_start_planning").click().run()
        assert any("天气不可用" in item.value for item in app_test.warning)
        assert not any("· 晴" in item.value for item in app_test.markdown)

        scenario["value"] = "failure"
        app_test.button(key="classic_start_planning").click().run()
        assert app_test.session_state["classic_plan_result"]["plan"]["state"] == "failed"
        assert any("controlled demo" in item.value for item in app_test.error)
        assert any("规划失败" in metric.value for metric in app_test.metric)
    finally:
        tempfile.tempdir = previous_tempdir


def test_api_error_preserves_valid_input_for_retry(classic_environment, monkeypatch) -> None:
    app_test = _new_app_test()
    _set_fixed_form(app_test)
    app_test.run()
    previous_values = {
        "budget": app_test.number_input(key="classic_budget").value,
        "departure": app_test.text_input(key="classic_departure_city").value,
    }

    def unavailable_post(url, *, json, timeout):
        raise httpx.ConnectError("controlled unavailable")

    monkeypatch.setattr(httpx, "post", unavailable_post)
    app_test.button(key="classic_start_planning").click().run()

    assert app_test.session_state["classic_plan_result"] is None
    assert app_test.number_input(key="classic_budget").value == previous_values["budget"]
    assert app_test.text_input(key="classic_departure_city").value == previous_values["departure"]
    assert any("无法连接偏好服务" in item.value for item in app_test.error)
    assert any("/api/health" in item.value for item in app_test.error)


def test_two_classic_sessions_do_not_share_form_or_results(classic_environment) -> None:
    first = _new_app_test()
    second = _new_app_test()
    first.text_input(key="classic_departure_city").set_value("上海").run()
    _set_fixed_form(first)
    first.button(key="classic_start_planning").click().run()

    assert first.session_state["classic_plan_result"] is not None
    assert second.session_state["classic_plan_result"] is None
    assert second.text_input(key="classic_departure_city").value == "北京"


def test_tampered_signed_draft_is_rejected_by_existing_hmac_boundary(
    classic_environment,
) -> None:
    client, _ = classic_environment
    parsed = client.post(
        "/api/preferences/parse",
        json={
            "text": COMPLETE_REQUEST,
            "reference_date": "2026-09-24",
            "config": {
                "provider": "mock",
                "locale": "zh-CN",
                "version": "mock-preference-parser-v1",
            },
        },
    ).json()
    tampered = copy.deepcopy(parsed["draft"])
    tampered["budget"] = 100.0

    response = client.post(
        "/api/preferences/plan",
        json={
            "draft": tampered,
            "draft_receipt": parsed["draft_receipt"],
            "confirmed": True,
            "reference_date": "2026-09-24",
            "config": {
                "provider": "mock",
                "locale": "zh-CN",
                "version": "mock-preference-parser-v1",
            },
        },
    )

    assert response.status_code == 422
    assert "receipt" in str(response.json()["detail"]).lower()
