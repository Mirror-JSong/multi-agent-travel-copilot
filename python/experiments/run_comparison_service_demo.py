"""Exercise the public C3 HTTP API against running FastAPI and Streamlit servers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import httpx


DEFAULT_OUTPUT = (
    Path(__file__).resolve().parent / "results" / "comparison_c3_service_demo.json"
)
PARSER_CONFIG = {
    "provider": "mock",
    "locale": "zh-CN",
    "version": "mock-preference-parser-v1",
}


def _post(client: httpx.Client, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = client.post(path, json=payload)
    body = response.json()
    return {
        "path": path,
        "http_status": response.status_code,
        "elapsed_ms": round(response.elapsed.total_seconds() * 1000, 4),
        "body": body,
    }


def _structured_payload(*, budget: float, weather_scenario: str = "generated") -> dict:
    return {
        "preferences": {
            "budget": budget,
            "departure_city": "上海",
            "start_date": "2026-10-01",
            "end_date": "2026-10-06",
            "travel_style": "comfort",
            "pace": "balanced",
            "num_travelers": 2,
            "interests": ["美食", "摄影"],
        },
        "plan_count": 2,
        "config": {
            "execution_mode": "concurrent",
            "max_concurrency": 2,
            "weather_scenario": weather_scenario,
        },
    }


def run_demo(api_base_url: str, streamlit_base_url: str) -> dict[str, Any]:
    with httpx.Client(base_url=api_base_url, timeout=30) as client:
        health = client.get("/api/health")
        parse = _post(client, "/api/preferences/parse", {
            "text": (
                "国庆想和朋友从上海出去玩五天，预算一万五，不想太累，"
                "喜欢拍照和吃东西。"
            ),
            "reference_date": "2026-09-24",
            "config": PARSER_CONFIG,
        })
        parsed = parse["body"]
        clarify = _post(client, "/api/preferences/clarify", {
            "draft": parsed["draft"],
            "draft_receipt": parsed["draft_receipt"],
            "answer": "我们一共3人，2026年10月1日出发，舒适游。",
            "reference_date": "2026-09-24",
            "config": PARSER_CONFIG,
        })
        confirmed = clarify["body"]
        natural_compare = _post(client, "/api/preferences/compare", {
            "draft": confirmed["draft"],
            "draft_receipt": confirmed["draft_receipt"],
            "confirmed": True,
            "reference_date": "2026-09-24",
            "config": PARSER_CONFIG,
            "planning_config": {},
        })
        high_budget = _post(
            client,
            "/api/plans/compare",
            _structured_payload(budget=50000),
        )
        mid_budget = _post(
            client,
            "/api/plans/compare",
            _structured_payload(budget=8000),
        )
        low_budget = _post(
            client,
            "/api/plans/compare",
            _structured_payload(budget=100),
        )
        unavailable_weather = _post(
            client,
            "/api/plans/compare",
            _structured_payload(budget=50000, weather_scenario="unavailable"),
        )
        sunny_weather = _post(
            client,
            "/api/plans/compare",
            _structured_payload(budget=50000, weather_scenario="sunny"),
        )
        rainy_weather = _post(
            client,
            "/api/plans/compare",
            _structured_payload(budget=50000, weather_scenario="rainy"),
        )
        tampered_payload = {
            "draft": dict(confirmed["draft"], budget=100),
            "draft_receipt": confirmed["draft_receipt"],
            "confirmed": True,
            "reference_date": "2026-09-24",
            "config": PARSER_CONFIG,
            "planning_config": {},
        }
        tampered = _post(client, "/api/preferences/compare", tampered_payload)
        modified = _post(client, "/api/preferences/clarify", {
            "draft": confirmed["draft"],
            "draft_receipt": confirmed["draft_receipt"],
            "answer": "预算改成一百元。",
            "reference_date": "2026-09-24",
            "config": PARSER_CONFIG,
        })
        modified_body = modified["body"]
        modified_compare = _post(client, "/api/preferences/compare", {
            "draft": modified_body["draft"],
            "draft_receipt": modified_body["draft_receipt"],
            "confirmed": True,
            "reference_date": "2026-09-24",
            "config": PARSER_CONFIG,
            "planning_config": {},
        })

    streamlit_health = httpx.get(
        f"{streamlit_base_url.rstrip('/')}/_stcore/health",
        timeout=10,
    )

    def comparison_summary(record: dict[str, Any]) -> dict[str, Any]:
        body = record["body"]
        plans = body.get("multi_plan", {}).get("plans", [])
        return {
            "http_status": record["http_status"],
            "elapsed_ms": record["elapsed_ms"],
            "request_status": body.get("request_status"),
            "generated_plan_count": body.get("multi_plan", {}).get(
                "generated_plan_count"
            ),
            "plan_statuses": [item["status"] for item in plans],
            "final_total_costs": [item["plan"].get("final_total_cost") for item in plans],
            "adjustment_rounds": [item["plan"].get("adjustment_round") for item in plans],
            "activity_candidate_ids": [
                [
                    activity["candidate_id"]
                    for day in (item["plan"].get("activity_result") or {}).get("day_plans", [])
                    for activity in day["activities"]
                ]
                for item in plans
            ],
            "decision": body.get("comparison", {}).get("recommendation", {}).get(
                "decision"
            ),
            "policy_version": body.get("comparison", {}).get(
                "scoring_policy", {}
            ).get("policy_version"),
            "sensitivity_changed": body.get("sensitivity", {}).get(
                "decision_changed"
            ),
        }

    comparisons = {
        "natural_multiturn": comparison_summary(natural_compare),
        "structured_high_budget": comparison_summary(high_budget),
        "structured_mid_budget": comparison_summary(mid_budget),
        "structured_low_budget": comparison_summary(low_budget),
        "weather_unavailable": comparison_summary(unavailable_weather),
        "structured_sunny": comparison_summary(sunny_weather),
        "structured_rainy": comparison_summary(rainy_weather),
        "modified_budget": comparison_summary(modified_compare),
    }
    weather_effect = {
        "same_destination_count": (
            comparisons["structured_sunny"]["generated_plan_count"]
            == comparisons["structured_rainy"]["generated_plan_count"]
        ),
        "activity_choices_changed": (
            comparisons["structured_sunny"]["activity_candidate_ids"]
            != comparisons["structured_rainy"]["activity_candidate_ids"]
        ),
        "sunny_final_total_costs": comparisons["structured_sunny"]["final_total_costs"],
        "rainy_final_total_costs": comparisons["structured_rainy"]["final_total_costs"],
    }
    return {
        "demo": "stage_c3_real_service_http_and_streamlit_health",
        "scope_note": (
            "Requests were sent over TCP to real Uvicorn and Streamlit processes. "
            "Travel data remains deterministic Mock data. Streamlit interaction "
            "behavior is separately verified with AppTest."
        ),
        "api_health": {
            "http_status": health.status_code,
            "body": health.json(),
        },
        "streamlit_health": {
            "http_status": streamlit_health.status_code,
            "body": streamlit_health.text,
        },
        "preference_flow": {
            "parse_http_status": parse["http_status"],
            "parse_complete": parsed["is_complete"],
            "parse_missing_fields": parsed["missing_fields"],
            "clarify_http_status": clarify["http_status"],
            "clarify_complete": confirmed["is_complete"],
            "confirmed_budget": confirmed["draft"]["budget"],
            "modified_budget": modified_body["draft"]["budget"],
        },
        "comparisons": comparisons,
        "weather_effect": weather_effect,
        "receipt_tamper_check": {
            "http_status": tampered["http_status"],
            "detail": tampered["body"].get("detail"),
        },
        "raw_responses": {
            "natural_compare": natural_compare["body"],
            "structured_high_budget": high_budget["body"],
            "structured_mid_budget": mid_budget["body"],
            "structured_low_budget": low_budget["body"],
            "weather_unavailable": unavailable_weather["body"],
            "structured_sunny": sunny_weather["body"],
            "structured_rainy": rainy_weather["body"],
            "modified_budget": modified_compare["body"],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--streamlit-base-url", default="http://127.0.0.1:8766")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = run_demo(args.api_base_url, args.streamlit_base_url)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(args.output),
        "api_health": payload["api_health"],
        "streamlit_health": payload["streamlit_health"],
        "preference_flow": payload["preference_flow"],
        "comparisons": payload["comparisons"],
        "receipt_tamper_check": payload["receipt_tamper_check"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
