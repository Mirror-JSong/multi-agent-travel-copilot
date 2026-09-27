"""Probe controlled weather/status scenarios over an explicitly enabled demo API."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8768")
    parser.add_argument("--output", type=Path, default=Path("experiments/results/d3_weather_status_http.json"))
    args = parser.parse_args()
    requests = {
        "sunny": {"scenario_id": "sunny_default"},
        "rainy": {"scenario_id": "normal_rain_default"},
        "unavailable": {"scenario_id": "unavailable_default"},
        "no_feasible_activity": {"scenario_id": "no_feasible_outdoor_only"},
        "provider_timeout": {"scenario_id": "sunny_default", "inject_weather_timeout": True},
        "agent_failure": {"scenario_id": "sunny_default", "inject_agent_failure": True},
    }
    records = {}
    with httpx.Client(base_url=args.api, timeout=30) as client:
        for name, payload in requests.items():
            response = client.post("/api/demo/weather/plan", json=payload)
            envelope = response.json()
            body = envelope.get("plan", {})
            records[name] = {
                "http_status": response.status_code,
                "planning_state": body.get("state"),
                "weather_planning_status": body.get("weather_planning", {}).get("status"),
                "fallback_used": body.get("weather_planning", {}).get("fallback_used"),
                "activity_candidate_ids": [
                    activity["candidate_id"]
                    for day in (body.get("activity_result") or {}).get("day_plans", [])
                    for activity in day["activities"]
                ],
                "final_total_cost": body.get("final_total_cost"),
                "business_issues": [item.get("reason") for item in (body.get("activity_result") or {}).get("constraint_issues", [])],
                "agent_failures": body.get("agent_failures", []),
            }
    report = {
        "endpoint_mode": "explicitly_enabled_fixed_mock_acceptance_only",
        "records": records,
        "assertions": {
            "sunny_rainy_choices_differ": records["sunny"]["activity_candidate_ids"] != records["rainy"]["activity_candidate_ids"],
            "unavailable_is_not_sunny": records["unavailable"]["weather_planning_status"] == "unavailable",
            "timeout_is_explicit_degradation": records["provider_timeout"]["weather_planning_status"] == "degraded_timeout",
            "no_feasible_is_business_state": records["no_feasible_activity"]["planning_state"] == "activity_constraints_unsatisfied",
            "agent_failure_is_failed": (
                records["agent_failure"]["planning_state"] == "failed"
                and bool(records["agent_failure"]["agent_failures"])
            ),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
