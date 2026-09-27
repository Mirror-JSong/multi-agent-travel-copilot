"""End-to-end API tests from signed preference drafts into the existing Pipeline."""

from __future__ import annotations

import copy
import importlib

from fastapi.testclient import TestClient

from agents.flight_agent import FlightAgent


api_module = importlib.import_module("api.app")
client = TestClient(api_module.app)
REFERENCE_DATE = "2026-09-24"
CONFIG = {
    "provider": "mock",
    "locale": "zh-CN",
    "version": "mock-preference-parser-v1",
}


def _parse(text: str) -> dict:
    response = client.post(
        "/api/preferences/parse",
        json={"text": text, "reference_date": REFERENCE_DATE, "config": CONFIG},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _clarify(previous: dict, answer: str) -> dict:
    response = client.post(
        "/api/preferences/clarify",
        json={
            "draft": previous["draft"],
            "draft_receipt": previous["draft_receipt"],
            "answer": answer,
            "reference_date": REFERENCE_DATE,
            "config": CONFIG,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _plan_payload(previous: dict, *, confirmed: bool = True) -> dict:
    return {
        "draft": previous["draft"],
        "draft_receipt": previous["draft_receipt"],
        "confirmed": confirmed,
        "reference_date": REFERENCE_DATE,
        "config": CONFIG,
    }


def _plan(previous: dict, *, confirmed: bool = True):
    return client.post(
        "/api/preferences/plan",
        json=_plan_payload(previous, confirmed=confirmed),
    )


def _complete_draft(budget: int = 20_000) -> dict:
    return _parse(
        f"从北京出发，预算{budget}元，2人，舒适游，"
        "2026年10月1日到2026年10月5日，喜欢历史。"
    )


def _plan_signature(plan: dict) -> dict:
    flight = plan["flight_result"]
    hotel = plan["hotel_result"]
    activities = plan["activity_result"]
    return {
        "status": plan["state"],
        "destination": plan["destination_rec"]["selected"]["city"],
        "outbound": flight["recommended_outbound"]["candidate_id"],
        "return": flight["recommended_return"]["candidate_id"],
        "hotel": hotel["recommended"]["candidate_id"],
        "activities": [
            activity["candidate_id"]
            for day in activities["day_plans"]
            for activity in day["activities"]
        ],
        "weather_result": plan["weather_result"],
        "weather_planning": plan["weather_planning"],
        "weather_evaluations": activities["weather_evaluations"],
        "weather_optimized": activities["weather_optimized"],
        "budget_breakdown": plan["budget_breakdown"],
        "adjustment_history": plan["adjustment_history"],
    }


def test_t1_complete_confirmed_draft_starts_existing_pipeline() -> None:
    draft = _complete_draft()

    response = _plan(draft)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["confirmation_accepted"] is True
    assert body["preferences"] == draft["pipeline_preferences_preview"]
    assert body["plan"]["state"] == "completed"
    assert body["plan"]["destination_rec"]["selected"] is not None
    assert body["plan"]["flight_result"] is not None
    assert body["plan"]["hotel_result"] is not None
    assert body["plan"]["activity_result"] is not None
    assert body["plan"]["budget_breakdown"] is not None
    assert "不能据此证明特定自然人的身份" in body["identity_notice"]


def test_t2_incomplete_draft_cannot_start_planning() -> None:
    draft = _parse("从北京出发，预算两万。")

    response = _plan(draft)

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "preferences_incomplete"
    assert set(detail["missing_fields"]) == {
        "start_date",
        "end_date",
        "num_travelers",
        "travel_style",
    }


def test_t3_client_completion_flags_cannot_bypass_server_validation() -> None:
    draft = _parse("从北京出发，预算两万。")
    payload = _plan_payload(draft)
    payload["is_complete"] = True
    payload["can_confirm"] = True

    response = client.post("/api/preferences/plan", json=payload)

    assert response.status_code == 422
    assert response.json()["detail"]


def test_t4_old_receipt_rejects_draft_source_and_context_tampering() -> None:
    original = _complete_draft()

    tampered = copy.deepcopy(original)
    tampered["draft"]["budget"] = 100
    assert _plan(tampered).status_code == 422

    tampered = copy.deepcopy(original)
    tampered["draft"]["field_sources"]["budget"] = "inferred"
    assert _plan(tampered).status_code == 422

    payload = _plan_payload(original)
    payload["reference_date"] = "2026-09-25"
    assert client.post("/api/preferences/plan", json=payload).status_code == 422

    payload = _plan_payload(original)
    payload["config"] = {**CONFIG, "locale": "en-US"}
    assert client.post("/api/preferences/plan", json=payload).status_code == 422


def test_t5_unconfirmed_complete_draft_cannot_start_planning() -> None:
    response = _plan(_complete_draft(), confirmed=False)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "confirmation_required"


def test_t6_clarified_draft_converts_to_expected_user_preferences() -> None:
    first = _parse(
        "国庆想和朋友从上海出去玩五天，预算一万五，"
        "不想太累，喜欢拍照和吃东西。"
    )
    complete = _clarify(
        first,
        "我们一共3人，2026年10月1日出发，舒适游。",
    )

    body = _plan(complete).json()

    assert body["preferences"] == {
        "budget": 15000.0,
        "travel_style": "comfort",
        "pace": "relaxed",
        "departure_city": "上海",
        "start_date": "2026-10-01",
        "end_date": "2026-10-06",
        "num_travelers": 3,
        "interests": ["摄影", "美食"],
        "dietary_restrictions": [],
        "accessibility_needs": [],
        "notes": "",
    }
    assert body["draft_only_fields"] == []
    assert body["preferences"]["pace"] == "relaxed"


def test_t7_natural_and_structured_entries_produce_identical_plans() -> None:
    draft = _complete_draft()
    natural_response = _plan(draft)
    structured_response = client.post(
        "/api/plan/full",
        json=draft["pipeline_preferences_preview"],
    )

    assert natural_response.status_code == 200
    assert structured_response.status_code == 200
    natural = natural_response.json()["plan"]
    structured = structured_response.json()
    assert natural["preferences"] == structured["preferences"]
    assert _plan_signature(natural) == _plan_signature(structured)


def test_t8_normal_budget_returns_completed_with_balanced_costs() -> None:
    body = _plan(_complete_draft(50_000)).json()
    plan = body["plan"]
    breakdown = plan["budget_breakdown"]

    assert plan["state"] == "completed"
    assert breakdown["is_within_budget"] is True
    assert breakdown["total_cost"] == (
        breakdown["flight_cost"]
        + breakdown["hotel_cost"]
        + breakdown["activity_cost"]
    )


def test_confirmed_preferences_snapshot_is_not_mutated_by_pipeline_enrichment() -> None:
    draft = _parse(
        "从北京出发，预算两万，2人，舒适游，2026年10月1日到2026年10月5日。"
    )

    body = _plan(draft).json()

    assert body["preferences"]["interests"] == []
    assert body["draft"]["interests"] is None
    assert body["plan"]["preferences"]["interests"] == [
        "经典景点",
        "当地美食",
        "文化体验",
    ]


def test_t9_low_budget_returns_budget_infeasible_with_history() -> None:
    body = _plan(_complete_draft(100)).json()
    plan = body["plan"]

    assert plan["state"] == "budget_infeasible"
    assert plan["budget_breakdown"]["is_within_budget"] is False
    assert plan["budget_breakdown"]["total_cost"] > 100
    assert len(plan["adjustment_history"]) == 3
    assert plan["agent_failures"] == []


def test_t10_required_agent_failure_returns_failed_not_success(monkeypatch) -> None:
    async def fail_flight(self, state):
        raise RuntimeError("injected flight failure")

    monkeypatch.setattr(FlightAgent, "execute", fail_flight)

    response = _plan(_complete_draft())

    assert response.status_code == 200
    plan = response.json()["plan"]
    assert plan["state"] == "failed"
    assert plan["budget_breakdown"] is None
    assert plan["agent_failures"] == [
        {
            "agent": "FlightAgent",
            "code": "agent_execution_error",
            "error_type": "RuntimeError",
            "reason": "injected flight failure",
            "required": True,
        }
    ]


def test_t11_updated_preferences_require_new_receipt_and_drive_latest_plan() -> None:
    original = _complete_draft(20_000)
    updated = _clarify(original, "预算改成100元。")

    stale_payload = _plan_payload(original)
    stale_payload["draft"] = updated["draft"]
    assert client.post("/api/preferences/plan", json=stale_payload).status_code == 422

    body = _plan(updated).json()
    assert body["preferences"]["budget"] == 100
    assert body["plan"]["budget_breakdown"]["budget"] == 100
    assert body["plan"]["state"] == "budget_infeasible"
