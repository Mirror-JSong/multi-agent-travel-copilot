"""Real HTTP acceptance demo for Stage A batch 3.

Start the API first, then run this module.  Every request goes through HTTP;
the script never calls the parser, clarification manager, or Pipeline directly.
"""

from __future__ import annotations

import copy
import json
import os

import httpx


BASE_URL = os.getenv("TRAVEL_API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
REFERENCE_DATE = "2026-09-24"
CONFIG = {
    "provider": "mock",
    "locale": "zh-CN",
    "version": "mock-preference-parser-v1",
}


def _parse(client: httpx.Client, text: str) -> dict:
    response = client.post(
        "/api/preferences/parse",
        json={"text": text, "reference_date": REFERENCE_DATE, "config": CONFIG},
    )
    response.raise_for_status()
    return response.json()


def _clarify(client: httpx.Client, previous: dict, answer: str) -> dict:
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
    response.raise_for_status()
    return response.json()


def _plan_response(
    client: httpx.Client,
    preference_response: dict,
    *,
    confirmed: bool = True,
) -> httpx.Response:
    return client.post(
        "/api/preferences/plan",
        json={
            "draft": preference_response["draft"],
            "draft_receipt": preference_response["draft_receipt"],
            "confirmed": confirmed,
            "reference_date": REFERENCE_DATE,
            "config": CONFIG,
        },
    )


def _plan(client: httpx.Client, preference_response: dict) -> dict:
    response = _plan_response(client, preference_response)
    response.raise_for_status()
    return response.json()


def _summary(label: str, preference_response: dict, result: dict) -> dict:
    plan = result["plan"]
    breakdown = plan.get("budget_breakdown") or {}
    return {
        "scenario": label,
        "confirmed_budget": result["preferences"]["budget"],
        "status": plan["state"],
        "destination": (plan.get("destination_rec") or {}).get("selected"),
        "total_cost": breakdown.get("total_cost"),
        "adjustment_rounds": plan.get("adjustment_round"),
        "adjustment_history_count": len(plan.get("adjustment_history") or []),
        "date_summary": preference_response.get("date_summary"),
    }


def main() -> None:
    evidence: list[dict] = []
    with httpx.Client(base_url=BASE_URL, timeout=30) as client:
        # A: partial request -> one clarification -> completed plan.
        partial = _parse(
            client,
            "国庆想和朋友从上海出去玩五天，预算一万五，不想太累，"
            "喜欢拍照和吃东西。",
        )
        completed = _clarify(
            client,
            partial,
            "我们一共3人，2026年10月1日出发，舒适游。",
        )
        evidence.append(_summary("A_partial_then_confirmed", completed, _plan(client, completed)))

        # B: conflicting duration -> explicit correction -> completed plan.
        conflict = _parse(
            client,
            "从上海出发，预算一万，2人，舒适游，"
            "2026年10月1日到2026年10月6日，玩三天。",
        )
        resolved = _clarify(client, conflict, "旅行天数改成五天。")
        evidence.append(_summary("B_date_conflict_resolved", resolved, _plan(client, resolved)))

        # C: a signed update replaces the old budget before confirmation.
        original = _parse(
            client,
            "从北京出发，预算两万，2人，舒适游，"
            "2026年11月1日到2026年11月5日，喜欢美食。",
        )
        changed = _clarify(client, original, "预算改成10000元。")
        evidence.append(_summary("C_budget_changed_before_confirmation", changed, _plan(client, changed)))

        # D: low budget stays a truthful business result with adjustment history.
        low = _parse(
            client,
            "从北京出发，预算100元，2人，经济游，"
            "2026年12月1日到2026年12月5日。",
        )
        evidence.append(_summary("D_budget_infeasible", low, _plan(client, low)))

        # E: tampering is rejected, while the untouched signed draft remains usable.
        tampered = copy.deepcopy(completed)
        tampered["draft"]["budget"] = 1
        rejected = _plan_response(client, tampered)
        retry = _plan(client, completed)
        evidence.append(
            {
                "scenario": "E_tampered_receipt_then_safe_retry",
                "tampered_http_status": rejected.status_code,
                "tampered_detail": rejected.json().get("detail"),
                "retry_status": retry["plan"]["state"],
                "valid_draft_preserved": retry["preferences"]["budget"] == 15000,
            }
        )

    print(json.dumps(evidence, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
