"""Exercise the B3 acceptance flow against running FastAPI and Streamlit services."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


CONFIG = {
    "provider": "mock",
    "locale": "zh-CN",
    "version": "mock-preference-parser-v1",
}


def _selected_ids(plan: dict[str, Any]) -> list[str]:
    result = plan.get("activity_result") or {}
    return [
        activity["candidate_id"]
        for day in result.get("day_plans", [])
        for activity in day["activities"]
    ]


def _summary(body: dict[str, Any]) -> dict[str, Any]:
    plan = body["plan"]
    weather = plan.get("weather_result")
    return {
        "scenario_id": body["scenario_id"],
        "planning_mode": body["planning_mode"],
        "state": plan["state"],
        "weather_status": plan["weather_planning"]["status"],
        "weather_is_mock": weather["is_mock"] if weather else None,
        "weather_conditions": (
            [item["weather_condition"] for item in weather["daily_weather"]]
            if weather else []
        ),
        "selected_activity_ids": _selected_ids(plan),
        "initial_total_cost": plan.get("initial_total_cost"),
        "final_total_cost": plan.get("final_total_cost"),
        "adjustment_round": plan["adjustment_round"],
        "weather_change_count": sum(
            item["weather_driven_change"]
            for item in (plan.get("activity_result") or {}).get(
                "weather_decisions", []
            )
        ),
        "constraint_issue_count": len(
            (plan.get("activity_result") or {}).get("constraint_issues", [])
        ),
        "failure_codes": [
            item["code"] for item in plan.get("agent_failures", [])
        ],
    }


def run(api_base: str, ui_base: str, output: Path) -> dict[str, Any]:
    api_base = api_base.rstrip("/")
    ui_base = ui_base.rstrip("/")
    with httpx.Client(timeout=30) as client:
        health = client.get(f"{api_base}/api/health")
        health.raise_for_status()
        ui_health = client.get(f"{ui_base}/_stcore/health")
        ui_health.raise_for_status()

        reference_date = "2026-09-24"
        parsed = client.post(
            f"{api_base}/api/preferences/parse",
            json={
                "text": (
                    "国庆想和朋友从上海出去玩五天，预算一万五，"
                    "不想太累，喜欢拍照和吃东西。"
                ),
                "reference_date": reference_date,
                "config": CONFIG,
            },
        )
        parsed.raise_for_status()
        first = parsed.json()
        clarified = client.post(
            f"{api_base}/api/preferences/clarify",
            json={
                "draft": first["draft"],
                "draft_receipt": first["draft_receipt"],
                "answer": "我们一共3人，2026年10月1日出发，舒适游。",
                "reference_date": reference_date,
                "config": CONFIG,
            },
        )
        clarified.raise_for_status()
        final = clarified.json()
        planned = client.post(
            f"{api_base}/api/preferences/plan",
            json={
                "draft": final["draft"],
                "draft_receipt": final["draft_receipt"],
                "confirmed": True,
                "reference_date": reference_date,
                "config": CONFIG,
            },
        )
        planned.raise_for_status()
        natural_plan = planned.json()["plan"]

        requests = [
            ("sunny_default", {}),
            ("normal_rain_default", {}),
            ("higher_cost_indoor", {}),
            ("unavailable_default", {}),
            ("no_feasible_outdoor_only", {}),
            ("extreme_budget_infeasible", {}),
            ("sunny_default", {"inject_weather_timeout": True}),
            ("sunny_default", {"inject_agent_failure": True}),
        ]
        scenario_results = []
        for scenario_id, overrides in requests:
            response = client.post(
                f"{api_base}/api/demo/weather/plan",
                json={
                    "scenario_id": scenario_id,
                    "planning_mode": "weather_aware",
                    **overrides,
                },
            )
            response.raise_for_status()
            summary = _summary(response.json())
            summary["injection"] = overrides or None
            scenario_results.append(summary)

    sunny, rainy = scenario_results[0], scenario_results[1]
    evidence = {
        "executed_at_utc": datetime.now(timezone.utc).isoformat(),
        "services": {
            "fastapi": {"url": api_base, "health": health.json()},
            "streamlit": {
                "url": ui_base,
                "health_status": ui_health.status_code,
                "health_body": ui_health.text,
            },
        },
        "natural_language_flow": {
            "initial_complete": first["is_complete"],
            "initial_missing_fields": first["missing_fields"],
            "clarified_complete": final["is_complete"],
            "confirmed": planned.json()["confirmation_accepted"],
            "plan_state": natural_plan["state"],
            "weather_status": natural_plan["weather_planning"]["status"],
            "weather_is_mock": natural_plan["weather_result"]["is_mock"],
        },
        "controlled_scenarios": scenario_results,
        "sunny_rainy_verified_difference": {
            "same_scenario_shape": len(sunny["selected_activity_ids"])
            == len(rainy["selected_activity_ids"]),
            "activities_differ": sunny["selected_activity_ids"]
            != rainy["selected_activity_ids"],
            "cost_delta": rainy["final_total_cost"] - sunny["final_total_cost"],
        },
        "notes": [
            "All controlled weather, candidates, and prices are deterministic Mock fixtures.",
            "The Streamlit service was started and its real health endpoint was reached; UI state rendering is additionally covered by Streamlit AppTest.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8765")
    parser.add_argument("--ui", default="http://127.0.0.1:8766")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("experiments/results/weather_b3_service_demo.json"),
    )
    args = parser.parse_args()
    evidence = run(args.api, args.ui, args.output)
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
