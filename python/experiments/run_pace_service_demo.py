"""Record the C0 real-service HTTP and Streamlit health demonstration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import httpx


DEFAULT_OUTPUT = Path(__file__).resolve().parent / "results" / "pace_c0_service_demo.json"


def _post(client: httpx.Client, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = client.post(path, json=payload)
    response.raise_for_status()
    return {"status_code": response.status_code, "body": response.json()}


def run(api_base_url: str, streamlit_base_url: str, output_path: Path) -> dict[str, Any]:
    config = {
        "provider": "mock",
        "locale": "zh-CN",
        "version": "mock-preference-parser-v1",
    }
    with httpx.Client(base_url=api_base_url, timeout=30) as client:
        health_response = client.get("/api/health")
        health_response.raise_for_status()
        parsed = _post(client, "/api/preferences/parse", {
            "text": (
                "从北京出发，预算五万，1人，舒适游，"
                "2026年10月1日到2026年10月3日，不想太累，喜欢历史。"
            ),
            "reference_date": "2026-09-25",
            "config": config,
        })
        parse_body = parsed["body"]
        planned = _post(client, "/api/preferences/plan", {
            "draft": parse_body["draft"],
            "draft_receipt": parse_body["draft_receipt"],
            "confirmed": True,
            "reference_date": "2026-09-25",
            "config": config,
        })
        structured = _post(client, "/api/plan/full", {
            "budget": 50000.0,
            "departure_city": "北京",
            "start_date": "2026-10-01",
            "end_date": "2026-10-03",
            "num_travelers": 1,
            "travel_style": "comfort",
            "pace": "relaxed",
            "interests": ["历史"],
        })

    streamlit_response = httpx.get(
        f"{streamlit_base_url.rstrip('/')}/_stcore/health",
        timeout=10,
    )
    streamlit_response.raise_for_status()
    natural_plan = planned["body"]["plan"]
    structured_plan = structured["body"]
    natural_days = natural_plan["activity_result"]["day_plans"]
    structured_days = structured_plan["activity_result"]["day_plans"]
    report = {
        "demo": "stage-c0-real-service",
        "mock_notice": "All travel, weather, duration, intensity, and price data are deterministic Mock data.",
        "api_health": {
            "status_code": health_response.status_code,
            "body": health_response.json(),
        },
        "streamlit_health": {
            "status_code": streamlit_response.status_code,
            "body": streamlit_response.text,
        },
        "natural_language_flow": {
            "parse_http_status": parsed["status_code"],
            "plan_http_status": planned["status_code"],
            "draft_complete": parse_body["is_complete"],
            "confirmed_pace": planned["body"]["preferences"]["pace"],
            "planning_state": natural_plan["state"],
            "daily_activity_counts": [len(day["activities"]) for day in natural_days],
            "rest_slots": [
                {"date": day["date"], "time_slot": rest["time_slot"]}
                for day in natural_days
                for rest in day["rest_slots"]
            ],
            "pace_policy_version": natural_plan["activity_result"]["pace_policy_version"],
        },
        "structured_flow": {
            "http_status": structured["status_code"],
            "confirmed_pace": structured_plan["preferences"]["pace"],
            "planning_state": structured_plan["state"],
            "daily_activity_counts": [len(day["activities"]) for day in structured_days],
            "rest_count": sum(len(day["rest_slots"]) for day in structured_days),
        },
        "ui_interaction_evidence": (
            "Streamlit process served its health endpoint; the relaxed selection, "
            "planning click, pace caption, and explicit rest rendering are verified "
            "by test_c0_structured_relaxed_pace_renders_explicit_rest."
        ),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8765")
    parser.add_argument("--streamlit", default="http://127.0.0.1:8766")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = run(args.api, args.streamlit, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
