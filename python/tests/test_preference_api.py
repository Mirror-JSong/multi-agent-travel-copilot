"""HTTP contract tests for Stage A batch 2 preference clarification."""

from __future__ import annotations

import copy
import importlib

import pytest
from fastapi.testclient import TestClient


api_module = importlib.import_module("api.app")
client = TestClient(api_module.app)
REFERENCE_DATE = "2026-09-24"
MOCK_CONFIG = {
    "provider": "mock",
    "locale": "zh-CN",
    "version": "mock-preference-parser-v1",
}


def _parse(text: str) -> dict:
    response = client.post(
        "/api/preferences/parse",
        json={
            "text": text,
            "reference_date": REFERENCE_DATE,
            "config": MOCK_CONFIG,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _clarify(previous: dict, answer: str):
    return client.post(
        "/api/preferences/clarify",
        json={
            "draft": previous["draft"],
            "draft_receipt": previous["draft_receipt"],
            "answer": answer,
            "reference_date": REFERENCE_DATE,
            "config": MOCK_CONFIG,
        },
    )


def test_t1_t2_parse_endpoint_returns_draft_result_and_targeted_questions() -> None:
    body = _parse(
        "国庆想和朋友从上海出去玩五天，预算一万五，不想太累，喜欢拍照和吃东西。"
    )

    assert body["draft"] == body["clarification_result"]["draft"]
    assert body["draft"]["departure_city"] == "上海"
    assert body["draft"]["budget"] == 15000
    assert body["draft"]["duration_days"] == 5
    assert body["draft"]["pace"] == "relaxed"
    assert body["draft"]["interests"] == ["摄影", "美食"]
    assert body["missing_fields"] == [
        "start_date",
        "end_date",
        "num_travelers",
        "travel_style",
    ]
    assert [question["field"] for question in body["questions"]] == body["missing_fields"]
    assert body["is_complete"] is False
    assert body["can_confirm"] is False
    assert body["pipeline_preferences_preview"] is None
    assert body["is_mock"] is True
    assert "并非真实 LLM" in body["notice"]
    assert len(body["draft_receipt"]) == 64


def test_t3_t4_clarify_uses_previous_draft_and_preserves_confirmed_fields() -> None:
    first = _parse(
        "国庆想和朋友从上海出去玩五天，预算一万五，不想太累，喜欢拍照和吃东西。"
    )

    response = _clarify(first, "我们一共3人，2026年10月1日出发，舒适游。")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["is_complete"] is True
    assert body["can_confirm"] is True
    assert body["draft"]["revision"] == 1
    assert body["draft"]["departure_city"] == "上海"
    assert body["draft"]["budget"] == 15000
    assert body["draft"]["interests"] == ["摄影", "美食"]
    assert body["draft"]["pace"] == "relaxed"
    assert body["draft"]["num_travelers"] == 3
    assert body["draft"]["end_date"] == "2026-10-06"
    assert body["draft"]["field_sources"]["end_date"] == "inferred"
    assert body["date_summary"] == {
        "departure_date": "2026-10-01",
        "return_date": "2026-10-06",
        "activity_start_date": "2026-10-01",
        "activity_end_date": "2026-10-05",
        "hotel_nights": 5,
    }
    assert body["pipeline_preferences_preview"]["departure_city"] == "上海"
    assert body["pipeline_preferences_preview"]["pace"] == "relaxed"
    assert body["draft_only_fields"] == []


def test_t5_explicit_budget_change_is_saved_by_clarify_endpoint() -> None:
    first = _parse(
        "从上海出发，预算一万五，3人，舒适游，2026年10月1日到2026年10月6日。"
    )
    assert first["is_complete"] is True

    response = _clarify(first, "预算改成两万。")

    assert response.status_code == 200
    body = response.json()
    assert body["draft"]["budget"] == 20000
    assert body["pipeline_preferences_preview"]["budget"] == 20000
    assert body["draft"]["departure_city"] == "上海"
    assert body["draft"]["start_date"] == "2026-10-01"


def test_t6_independent_stateless_conversations_do_not_share_drafts() -> None:
    shanghai = _parse("从上海出发，预算一万。")
    beijing = _parse("从北京出发，预算两万。")
    original_beijing = copy.deepcopy(beijing)

    updated_shanghai = _clarify(
        shanghai,
        "2人，舒适游，2026年10月1日到2026年10月5日。",
    ).json()

    assert updated_shanghai["draft"]["departure_city"] == "上海"
    assert updated_shanghai["draft"]["revision"] == 1
    assert beijing == original_beijing
    assert beijing["draft"]["departure_city"] == "北京"
    assert beijing["draft"]["revision"] == 0


@pytest.mark.parametrize(
    "payload",
    [
        {"text": "", "reference_date": REFERENCE_DATE},
        {"text": "   ", "reference_date": REFERENCE_DATE},
        {"text": "从上海出发", "reference_date": "bad-date"},
        {
            "text": "从上海出发",
            "reference_date": REFERENCE_DATE,
            "config": {"provider": "llm", "locale": "zh-CN", "version": "v1"},
        },
        {"text": "从上海出发", "reference_date": REFERENCE_DATE, "is_complete": True},
    ],
)
def test_t8_malformed_parse_requests_return_422(payload: dict) -> None:
    response = client.post("/api/preferences/parse", json=payload)
    assert response.status_code == 422
    assert "detail" in response.json()


def test_t8_invalid_business_values_are_normal_clarification_result() -> None:
    body = _parse(
        "从上海出发，预算负一百，0人，舒适游，2026年2月30日到2026年3月3日。"
    )

    assert body["is_complete"] is False
    errors = body["clarification_result"]["validation_errors"]
    assert {"budget", "num_travelers", "start_date"}.issubset(errors)
    assert body["pipeline_preferences_preview"] is None


def test_t9_date_duration_conflict_returns_a_clarification_question() -> None:
    body = _parse(
        "从上海出发，预算一万，2人，舒适游，"
        "2026年10月1日到2026年10月6日，玩三天。"
    )

    assert body["is_complete"] is False
    assert body["date_summary"]["hotel_nights"] == 5
    assert "duration_days" in body["clarification_result"]["validation_errors"]
    assert any(question["field"] == "duration_days" for question in body["questions"])


def test_client_cannot_tamper_with_draft_source_values_or_reference_date() -> None:
    first = _parse("从上海出发，预算一万。")
    tampered = copy.deepcopy(first)
    tampered["draft"]["budget"] = 1
    tampered["draft"]["field_sources"]["budget"] = "inferred"

    response = _clarify(tampered, "2人。")

    assert response.status_code == 422
    assert "已被修改" in response.json()["detail"]

    response = client.post(
        "/api/preferences/clarify",
        json={
            "draft": first["draft"],
            "draft_receipt": first["draft_receipt"],
            "answer": "2人。",
            "reference_date": "2026-09-25",
            "config": MOCK_CONFIG,
        },
    )
    assert response.status_code == 422


def test_clarify_contract_rejects_client_completion_flag_and_unknown_draft_fields() -> None:
    first = _parse("从上海出发，预算一万。")
    payload = {
        "draft": first["draft"],
        "draft_receipt": first["draft_receipt"],
        "answer": "2人。",
        "reference_date": REFERENCE_DATE,
        "config": MOCK_CONFIG,
        "is_complete": True,
    }
    response = client.post("/api/preferences/clarify", json=payload)
    assert response.status_code == 422

    payload.pop("is_complete")
    payload["draft"]["server_confirmed"] = True
    response = client.post("/api/preferences/clarify", json=payload)
    assert response.status_code == 422


def test_same_parse_input_is_repeatable_and_server_keeps_no_conversation_state() -> None:
    results = [
        _parse("明天从深圳出发玩三天，预算一万五，两个人，休闲游。")
        for _ in range(5)
    ]

    assert all(result == results[0] for result in results[1:])
    assert all(result["draft"]["revision"] == 0 for result in results)
