"""FastAPI 输入错误、内部错误和 Agent 故障响应边界。"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from agents.flight_agent import FlightAgent

api_module = importlib.import_module("api.app")


def _payload(**overrides) -> dict:
    payload = {
        "budget": 50_000,
        "departure_city": "北京",
        "start_date": "2026-05-01",
        "end_date": "2026-05-05",
        "travel_style": "comfort",
        "num_travelers": 1,
        "interests": [],
        "notes": "",
    }
    payload.update(overrides)
    return payload


def test_legal_request_keeps_existing_response_fields_and_adds_status():
    response = TestClient(api_module.app).post("/api/plan", json=_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["within_budget"] is True
    assert body["errors"] == []
    for existing_field in (
        "destination",
        "country",
        "flight_cost",
        "hotel_cost",
        "activity_cost",
        "total_cost",
        "budget",
        "adjustment_rounds",
        "hotel_name",
        "days",
        "highlights",
        "warnings",
    ):
        assert existing_field in body


@pytest.mark.parametrize(
    "overrides",
    [
        {"start_date": "bad-date"},
        {"start_date": "2026-05-05", "end_date": "2026-05-01"},
        {"start_date": "2026-05-05", "end_date": "2026-05-05"},
        {"budget": 0},
        {"budget": "50000"},
        {"num_travelers": 0},
        {"num_travelers": 21},
        {"departure_city": "   "},
        {"travel_style": "unknown"},
    ],
)
def test_invalid_api_input_returns_422_and_never_runs_planning(overrides):
    response = TestClient(api_module.app).post(
        "/api/plan",
        json=_payload(**overrides),
    )

    assert response.status_code == 422
    assert "detail" in response.json()


def test_required_agent_failure_reaches_api_as_structured_failed_result(monkeypatch):
    async def fail_flight(self, state):
        raise RuntimeError("flight provider unavailable")

    monkeypatch.setattr(FlightAgent, "execute", fail_flight)
    response = TestClient(api_module.app).post("/api/plan", json=_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["within_budget"] is False
    assert body["errors"] == [
        {
            "agent": "FlightAgent",
            "code": "agent_execution_error",
            "error_type": "RuntimeError",
            "reason": "flight provider unavailable",
            "required": True,
        }
    ]
    assert "flight provider unavailable" in body["warnings"][0]


def test_budget_infeasible_is_explicit_http_200_business_result():
    response = TestClient(api_module.app).post(
        "/api/plan",
        json=_payload(budget=100),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "budget_infeasible"
    assert body["within_budget"] is False
    assert body["total_cost"] > body["budget"]
    assert body["errors"] == []
    assert "current staged search strategy did not find" in body["message"]


def test_full_api_exposes_candidate_snapshot_and_adjustment_history():
    response = TestClient(api_module.app).post(
        "/api/plan/full",
        json=_payload(budget=100),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "budget_infeasible"
    assert body["candidate_snapshot"] is not None
    assert body["initial_selection"] is not None
    assert body["initial_total_cost"] >= body["final_total_cost"]
    assert len(body["adjustment_history"]) == 3
    assert [item["target"] for item in body["adjustment_history"]] == [
        "activities",
        "hotel",
        "flights",
    ]


def test_unexpected_internal_error_is_http_500_not_input_422(monkeypatch):
    class CrashingPipeline:
        async def run(self, preferences):
            raise RuntimeError("unexpected orchestrator bug")

    monkeypatch.setattr(api_module, "TravelPlanningPipeline", CrashingPipeline)
    client = TestClient(api_module.app, raise_server_exceptions=False)

    response = client.post("/api/plan", json=_payload())

    assert response.status_code == 500
