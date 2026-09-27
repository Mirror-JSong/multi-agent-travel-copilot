"""Probe the isolated controlled-failure Uvicorn server over real HTTP."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8767")
    parser.add_argument("--output", type=Path, default=Path("experiments/results/d3_agent_failure_http.json"))
    args = parser.parse_args()
    payload = {
        "preferences": {
            "budget": 5000,
            "departure_city": "Origin",
            "start_date": "2026-05-01",
            "end_date": "2026-05-03",
            "travel_style": "comfort",
            "pace": "balanced",
            "num_travelers": 2,
            "interests": ["文化"],
        },
        "plan_count": 2,
        "config": {"execution_mode": "concurrent", "max_concurrency": 2},
    }
    with httpx.Client(base_url=args.api, timeout=30) as client:
        response = client.post("/api/plans/compare", json=payload)
    body = response.json()
    plans = body["multi_plan"]["plans"]
    report = {
        "http_status": response.status_code,
        "request_status": body["request_status"],
        "plan_statuses": [plan["status"] for plan in plans],
        "failure_records": [
            failure
            for plan in plans
            for failure in plan.get("errors", [])
        ],
        "decision": body["comparison"]["recommendation"]["decision"],
        "passed": (
            response.status_code == 200
            and "failed" in [plan["status"] for plan in plans]
            and any(plan["status"] == "completed" for plan in plans)
            and any(plan.get("errors") for plan in plans)
        ),
        "scope_note": "固定测试注入；未修改普通生产入口。",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
