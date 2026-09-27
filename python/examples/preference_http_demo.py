"""Real HTTP demo for the Stage A batch 2 preference endpoints."""

from __future__ import annotations

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


def parse(client: httpx.Client, text: str) -> dict:
    response = client.post(
        "/api/preferences/parse",
        json={"text": text, "reference_date": REFERENCE_DATE, "config": CONFIG},
    )
    response.raise_for_status()
    return response.json()


def clarify(client: httpx.Client, previous: dict, answer: str) -> dict:
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


def summary(label: str, first: dict, final: dict) -> None:
    print(json.dumps(
        {
            "example": label,
            "round1_missing": first["missing_fields"],
            "round1_questions": [item["field"] for item in first["questions"]],
            "final_complete": final["is_complete"],
            "final_draft": final["draft"],
            "date_summary": final["date_summary"],
        },
        ensure_ascii=False,
        indent=2,
    ))


def main() -> None:
    with httpx.Client(base_url=BASE_URL, timeout=10) as client:
        first = parse(
            client,
            "国庆想和朋友从上海出去玩五天，预算一万五，不想太累，喜欢拍照和吃东西。",
        )
        completed = clarify(
            client,
            first,
            "我们一共3人，2026年10月1日出发，舒适游。",
        )
        summary("missing-fields", first, completed)

        conflict = parse(
            client,
            "从上海出发，预算一万，2人，舒适游，"
            "2026年10月1日到2026年10月6日，玩三天。",
        )
        resolved = clarify(client, conflict, "旅行天数改成五天。")
        summary("date-conflict", conflict, resolved)

        invalid = client.post(
            "/api/preferences/parse",
            json={"text": "从上海出发", "reference_date": "bad-date", "config": CONFIG},
        )
        print(json.dumps(
            {
                "example": "invalid-reference-date",
                "http_status": invalid.status_code,
                "detail": invalid.json().get("detail"),
            },
            ensure_ascii=False,
            indent=2,
        ))


if __name__ == "__main__":
    main()
