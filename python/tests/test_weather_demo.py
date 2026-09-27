"""Opt-in B3 HTTP demo and Streamlit weather-explanation acceptance tests."""

from __future__ import annotations

import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi.testclient import TestClient

from api.app import app


APP_PATH = Path(__file__).parents[1] / "ui" / "streamlit_app.py"


def test_weather_demo_endpoint_is_hidden_by_default(monkeypatch) -> None:
    monkeypatch.delenv("ENABLE_WEATHER_DEMO_ENDPOINTS", raising=False)
    response = TestClient(app).post(
        "/api/demo/weather/plan",
        json={"scenario_id": "sunny_default"},
    )
    assert response.status_code == 404


def test_controlled_http_demo_preserves_pair_invariants_and_decision_trace(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENABLE_WEATHER_DEMO_ENDPOINTS", "1")
    client = TestClient(app)
    control = client.post(
        "/api/demo/weather/plan",
        json={
            "scenario_id": "normal_rain_default",
            "planning_mode": "baseline",
        },
    )
    treatment = client.post(
        "/api/demo/weather/plan",
        json={
            "scenario_id": "normal_rain_default",
            "planning_mode": "weather_aware",
        },
    )

    assert control.status_code == treatment.status_code == 200
    base = control.json()["plan"]
    weather = treatment.json()["plan"]
    assert base["weather_result"] == weather["weather_result"]
    assert base["candidate_snapshot"] == weather["candidate_snapshot"]
    assert base["final_total_cost"] == 1550
    assert weather["final_total_cost"] == 1910
    assert any(
        item["weather_driven_change"]
        for item in weather["activity_result"]["weather_decisions"]
    )
    assert all(
        not item["weather_driven_change"]
        for item in base["activity_result"]["weather_decisions"]
    )


def test_controlled_http_demo_distinguishes_unavailable_constraints_and_failure(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ENABLE_WEATHER_DEMO_ENDPOINTS", "1")
    client = TestClient(app)

    unavailable = client.post(
        "/api/demo/weather/plan",
        json={"scenario_id": "unavailable_default"},
    ).json()["plan"]
    assert unavailable["state"] == "completed"
    assert unavailable["weather_planning"]["status"] == "unavailable"
    assert unavailable["activity_result"]["weather_optimized"] is False

    timed_out = client.post(
        "/api/demo/weather/plan",
        json={
            "scenario_id": "sunny_default",
            "inject_weather_timeout": True,
        },
    ).json()["plan"]
    assert timed_out["state"] == "completed"
    assert timed_out["weather_planning"]["status"] == "degraded_timeout"
    assert timed_out["weather_result"] is None

    constrained = client.post(
        "/api/demo/weather/plan",
        json={"scenario_id": "no_feasible_outdoor_only"},
    ).json()["plan"]
    assert constrained["state"] == "activity_constraints_unsatisfied"
    assert constrained["budget_breakdown"] is None

    failed = client.post(
        "/api/demo/weather/plan",
        json={
            "scenario_id": "sunny_default",
            "inject_agent_failure": True,
        },
    ).json()["plan"]
    assert failed["state"] == "failed"
    assert failed["budget_breakdown"] is None
    assert failed["agent_failures"][0]["agent"] == "FlightAgent"
    assert "controlled demo" in failed["agent_failures"][0]["reason"]


def test_streamlit_fixed_mock_demo_renders_weather_and_distinct_terminal_states(
    monkeypatch,
    tmp_path,
) -> None:
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("ENABLE_WEATHER_DEMO_ENDPOINTS", "1")
    monkeypatch.setenv("ENABLE_WEATHER_DEMO_CONTROLS", "1")
    monkeypatch.setenv("TRAVEL_API_BASE_URL", "http://b3.test")
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setenv("TMP", str(tmp_path))
    previous_tempdir = tempfile.tempdir
    tempfile.tempdir = str(tmp_path)
    client = TestClient(app)

    def local_post(url, *, json, timeout):
        return client.post(urlparse(url).path, json=json)

    monkeypatch.setattr(httpx, "post", local_post)
    try:
        app_test = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
        app_test.radio(key="input_mode").set_value(
            "天气验收演示（固定 Mock）"
        ).run()
        app_test.selectbox(key="weather_demo_scenario").set_value(
            "固定雨天"
        )
        app_test.button(key="run_weather_demo").click().run()
        assert not app_test.exception
        assert any("Mock 模拟天气" in item.value for item in app_test.caption)
        assert any("天气驱动变化" in item.value for item in app_test.info)

        app_test.selectbox(key="weather_demo_scenario").set_value(
            "天气不可用回退"
        )
        app_test.button(key="run_weather_demo").click().run()
        assert any("天气不可用" in item.value for item in app_test.warning)
        assert not any("· 晴" in item.value for item in app_test.markdown)

        app_test.checkbox(key="weather_demo_timeout").check()
        app_test.selectbox(key="weather_demo_scenario").set_value("晴天基线")
        app_test.button(key="run_weather_demo").click().run()
        assert any("Provider 超时" in item.value for item in app_test.warning)
        app_test.checkbox(key="weather_demo_timeout").uncheck()

        app_test.selectbox(key="weather_demo_scenario").set_value(
            "无天气合规活动"
        )
        app_test.button(key="run_weather_demo").click().run()
        assert any(
            "活动约束不可满足" in metric.value
            for metric in app_test.metric
            if metric.label == "当前规划状态"
        )
        assert any(
            "业务状态代码：activity_constraints_unsatisfied" in item.value
            for item in app_test.caption
        )

        app_test.checkbox(key="weather_demo_failure").check()
        app_test.selectbox(key="weather_demo_scenario").set_value("晴天基线")
        app_test.button(key="run_weather_demo").click().run()
        assert any(
            "FAILED" in metric.value
            for metric in app_test.metric
            if metric.label == "当前规划状态"
        )
        assert any("controlled demo" in item.value for item in app_test.error)
    finally:
        tempfile.tempdir = previous_tempdir
