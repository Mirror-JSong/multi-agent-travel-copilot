"""Run the reproducible structured-vs-clarification product experiment.

This is an automated interaction simulation over a finite Mock Parser corpus. It
does not measure real-user time, satisfaction, or open-domain LLM accuracy.
"""

from __future__ import annotations

import argparse
import copy
import json
from datetime import date
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from api.app import _sign_draft, app
from api.preference_models import PreferenceParserConfig
from config.settings import settings
from preferences.contracts import PreferencesDraft
from ui.preference_api_client import PreferenceApiClient, PreferenceApiError
from ui.preference_session import accept_preference_response, initialize_preference_session


ROOT = Path(__file__).resolve().parent
DEFAULT_SCENARIOS = ROOT / "preference_clarification_scenarios.json"
DEFAULT_OUTPUT = ROOT / "results" / "preference_clarification_results.json"
PREFERENCE_FIELDS = (
    "departure_city",
    "budget",
    "start_date",
    "end_date",
    "duration_days",
    "num_travelers",
    "travel_style",
    "pace",
    "interests",
)


def post_ok(client: TestClient, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = client.post(path, json=payload)
    if response.status_code != 200:
        raise RuntimeError(f"{path} returned {response.status_code}: {response.text}")
    return response.json()


def preference_payload(config: dict[str, str], reference_date: str, text: str) -> dict:
    return {"text": text, "reference_date": reference_date, "config": config}


def clarification_payload(
    config: dict[str, str],
    reference_date: str,
    previous: dict,
    answer: str,
) -> dict:
    return {
        "draft": previous["draft"],
        "draft_receipt": previous["draft_receipt"],
        "answer": answer,
        "reference_date": reference_date,
        "config": config,
    }


def plan_payload(config: dict[str, str], reference_date: str, previous: dict) -> dict:
    return {
        "draft": previous["draft"],
        "draft_receipt": previous["draft_receipt"],
        "confirmed": True,
        "reference_date": reference_date,
        "config": config,
    }


def extraction_score(draft: dict, expected: dict, expected_ambiguous: list[str]) -> dict:
    correct = 0
    missing = 0
    incorrect = 0
    comparisons = {}
    for field, expected_value in expected.items():
        actual = draft.get(field)
        is_correct = actual == expected_value
        comparisons[field] = {
            "expected": expected_value,
            "actual": actual,
            "correct": is_correct,
        }
        if is_correct:
            correct += 1
        elif actual is None:
            missing += 1
        else:
            incorrect += 1

    false_positive_fields = [
        field
        for field in PREFERENCE_FIELDS
        if field not in expected
        and field not in expected_ambiguous
        and draft.get(field) is not None
    ]
    actual_ambiguous = set(draft.get("ambiguous_fields", []))
    expected_ambiguous_set = set(expected_ambiguous)
    return {
        "labeled_fields": len(expected),
        "correct": correct,
        "missing": missing,
        "incorrect": incorrect,
        "false_positive_fields": false_positive_fields,
        "expected_ambiguous": sorted(expected_ambiguous_set),
        "recognized_ambiguous": sorted(actual_ambiguous & expected_ambiguous_set),
        "missed_ambiguous": sorted(expected_ambiguous_set - actual_ambiguous),
        "unexpected_ambiguous": sorted(actual_ambiguous - expected_ambiguous_set),
        "comparisons": comparisons,
    }


def plan_signature(plan: dict) -> dict:
    weather = plan["weather_result"]
    activity_result = plan["activity_result"]
    return {
        "status": plan["state"],
        "destination": plan["destination_rec"]["selected"]["city"],
        "outbound_candidate_id": plan["flight_result"]["recommended_outbound"]["candidate_id"],
        "return_candidate_id": plan["flight_result"]["recommended_return"]["candidate_id"],
        "hotel_candidate_id": plan["hotel_result"]["recommended"]["candidate_id"],
        "activity_candidate_ids": [
            activity["candidate_id"]
            for day_plan in activity_result["day_plans"]
            for activity in day_plan["activities"]
        ],
        "activity_weather_compatibility": [
            {
                "candidate_id": activity["candidate_id"],
                "weather_compatible": activity["weather_compatible"],
                "recommendation_reason": activity["recommendation_reason"],
            }
            for day_plan in activity_result["day_plans"]
            for activity in day_plan["activities"]
        ],
        "weather": {
            "scenario": weather["scenario"],
            "source": weather["source"],
            "source_version": weather["source_version"],
            "availability_status": weather["availability_status"],
            "daily_weather": weather["daily_weather"],
            "planning": plan["weather_planning"],
            "activity_weather_optimized": activity_result["weather_optimized"],
            "activity_weather_fallback_reason": activity_result["weather_fallback_reason"],
        },
        "final_total_cost": plan["final_total_cost"],
        "budget_breakdown": plan["budget_breakdown"],
        "adjustment_history": plan["adjustment_history"],
    }


def structured_interaction_steps(final_preferences: dict) -> int:
    required_field_entries = 6
    optional_interest_entry = 1 if final_preferences.get("interests") else 0
    submit_click = 1
    return required_field_entries + optional_interest_entry + submit_click


def run_scenario(
    client: TestClient,
    scenario: dict,
    reference_date: str,
    config: dict[str, str],
) -> tuple[dict, dict]:
    first = post_ok(
        client,
        "/api/preferences/parse",
        preference_payload(config, reference_date, scenario["initial_text"]),
    )
    current = first
    for answer in scenario["answers"]:
        current = post_ok(
            client,
            "/api/preferences/clarify",
            clarification_payload(config, reference_date, current, answer),
        )

    # The recorded Stage A experiment predates formal pace consumption. Keep its
    # planning leg on the original semantics while still scoring pace extraction.
    # This is an experiment-compatibility boundary, never a product API bypass.
    planning_current = current
    if (
        current["draft"].get("pace") is not None
        and "pace" not in scenario["final_preferences"]
    ):
        planning_current = copy.deepcopy(current)
        draft_payload = planning_current["draft"]
        draft_payload["pace"] = None
        draft_payload["field_sources"]["pace"] = "unknown"
        signed_draft = PreferencesDraft.model_validate(draft_payload)
        planning_current["draft_receipt"] = _sign_draft(
            signed_draft,
            date.fromisoformat(reference_date),
            PreferenceParserConfig.model_validate(config),
        )

    natural_plan_response = client.post(
        "/api/preferences/plan",
        json=plan_payload(config, reference_date, planning_current),
    )
    natural_plan = natural_plan_response.json() if natural_plan_response.status_code == 200 else None
    structured_response = client.post(
        "/api/plan/full",
        json=scenario["final_preferences"],
    )
    structured_plan = structured_response.json() if structured_response.status_code == 200 else None

    expected_preferences = scenario["final_preferences"]
    actual_preferences = (
        natural_plan["preferences"] if natural_plan is not None else None
    )
    final_fields_match = (
        actual_preferences is not None
        and all(
            actual_preferences.get(field) == expected
            for field, expected in expected_preferences.items()
        )
    )
    natural_signature = (
        plan_signature(natural_plan["plan"])
        if natural_plan is not None
        and natural_plan["plan"]["state"] != "failed"
        else None
    )
    structured_signature = (
        plan_signature(structured_plan)
        if structured_plan is not None and structured_plan["state"] != "failed"
        else None
    )
    recovery_success = (
        scenario["recovery_type"] == "none"
        or (
            current["is_complete"]
            and final_fields_match
            and natural_plan_response.status_code == 200
        )
    )
    result = {
        "id": scenario["id"],
        "information_extraction": extraction_score(
            first["draft"],
            scenario["initial_expected"],
            scenario["expected_ambiguous"],
        ),
        "information_collection": {
            "initial_complete": first["is_complete"],
            "final_complete": current["is_complete"],
            "clarification_rounds": len(scenario["answers"]),
            "initial_missing_fields": first["missing_fields"],
            "final_missing_fields": current["missing_fields"],
            "final_fields_match_annotation": final_fields_match,
            "natural_interaction_steps": 3 + 2 * len(scenario["answers"]),
            "structured_interaction_steps": structured_interaction_steps(expected_preferences),
        },
        "planning": {
            "natural_http_status": natural_plan_response.status_code,
            "structured_http_status": structured_response.status_code,
            "natural_signature": natural_signature,
            "structured_signature": structured_signature,
            "consistent": natural_signature == structured_signature and natural_signature is not None,
        },
        "recovery": {
            "type": scenario["recovery_type"],
            "success": recovery_success,
        },
    }
    return result, current


def run_security_and_transport_recovery(
    client: TestClient,
    complete: dict,
    reference_date: str,
    config: dict[str, str],
) -> list[dict]:
    tampered = copy.deepcopy(complete)
    tampered["draft"]["budget"] = 1
    rejected = client.post(
        "/api/preferences/plan",
        json=plan_payload(config, reference_date, tampered),
    )
    valid_retry = client.post(
        "/api/preferences/plan",
        json=plan_payload(config, reference_date, complete),
    )

    state: dict[str, Any] = {}
    initialize_preference_session(
        state,
        default_reference_date=date.fromisoformat(reference_date),
    )
    accept_preference_response(state, complete, user_text="experiment complete request")
    before_failure = copy.deepcopy(state)
    transport_failed = False
    try:
        PreferenceApiClient(
            "http://127.0.0.1:1",
            timeout_seconds=0.05,
        ).plan(
            complete["draft"],
            complete["draft_receipt"],
            date.fromisoformat(reference_date),
        )
    except PreferenceApiError:
        transport_failed = True
    state_preserved = state == before_failure
    transport_retry = client.post(
        "/api/preferences/plan",
        json=plan_payload(config, reference_date, complete),
    )
    return [
        {
            "type": "tampered_receipt_then_valid_retry",
            "success": rejected.status_code == 422 and valid_retry.status_code == 200,
            "first_status": rejected.status_code,
            "retry_status": valid_retry.status_code,
        },
        {
            "type": "transport_failure_state_preservation_then_retry",
            "success": (
                transport_failed
                and state_preserved
                and transport_retry.status_code == 200
            ),
            "transport_failed": transport_failed,
            "state_preserved": state_preserved,
            "retry_status": transport_retry.status_code,
        },
    ]


def aggregate(results: list[dict], recovery_probes: list[dict]) -> dict:
    extraction = [item["information_extraction"] for item in results]
    labeled = sum(item["labeled_fields"] for item in extraction)
    correct = sum(item["correct"] for item in extraction)
    missing = sum(item["missing"] for item in extraction)
    incorrect = sum(item["incorrect"] for item in extraction)
    expected_ambiguities = sum(len(item["expected_ambiguous"]) for item in extraction)
    recognized_ambiguities = sum(len(item["recognized_ambiguous"]) for item in extraction)
    recovery_cases = [
        item["recovery"]
        for item in results
        if item["recovery"]["type"] != "none"
    ] + recovery_probes
    return {
        "M1_required_field_collection_completion_rate": {
            "completed": sum(item["information_collection"]["final_complete"] for item in results),
            "total": len(results),
            "rate": sum(item["information_collection"]["final_complete"] for item in results) / len(results),
        },
        "M2_supported_field_extraction_accuracy": {
            "correct": correct,
            "labeled": labeled,
            "missing": missing,
            "incorrect": incorrect,
            "accuracy": correct / labeled if labeled else None,
            "false_positive_count": sum(len(item["false_positive_fields"]) for item in extraction),
            "ambiguities_recognized": recognized_ambiguities,
            "ambiguities_expected": expected_ambiguities,
            "ambiguity_recognition_rate": (
                recognized_ambiguities / expected_ambiguities
                if expected_ambiguities
                else None
            ),
        },
        "M3_clarification_rounds": {
            "per_scenario": {
                item["id"]: item["information_collection"]["clarification_rounds"]
                for item in results
            },
            "average": sum(item["information_collection"]["clarification_rounds"] for item in results) / len(results),
        },
        "M4_interaction_steps": {
            "definition": {
                "natural": "输入文本+点击解析=2；每轮补充为输入+提交=2；确认规划=1",
                "structured": "6个必要字段各1次；非空兴趣1次；提交规划1次",
            },
            "natural_total": sum(item["information_collection"]["natural_interaction_steps"] for item in results),
            "structured_total": sum(item["information_collection"]["structured_interaction_steps"] for item in results),
            "natural_average": sum(item["information_collection"]["natural_interaction_steps"] for item in results) / len(results),
            "structured_average": sum(item["information_collection"]["structured_interaction_steps"] for item in results) / len(results),
        },
        "M5_planning_consistency": {
            "consistent": sum(item["planning"]["consistent"] for item in results),
            "total": len(results),
            "rate": sum(item["planning"]["consistent"] for item in results) / len(results),
        },
        "M6_exception_recovery_success_rate": {
            "successful": sum(item["success"] for item in recovery_cases),
            "total": len(recovery_cases),
            "rate": sum(item["success"] for item in recovery_cases) / len(recovery_cases),
            "cases": recovery_cases,
        },
    }


def run(scenario_path: Path, output_path: Path) -> dict:
    source = json.loads(scenario_path.read_text(encoding="utf-8"))
    original_activity_version = settings.MOCK_ACTIVITY_DATA_VERSION
    settings.MOCK_ACTIVITY_DATA_VERSION = "2026.09-activity-weather-v2"
    try:
        client = TestClient(app)
        results = []
        complete_for_recovery = None
        for scenario in source["scenarios"]:
            result, completed = run_scenario(
                client,
                scenario,
                source["reference_date"],
                source["parser_config"],
            )
            results.append(result)
            if scenario["id"] == "complete_request":
                complete_for_recovery = completed
    finally:
        settings.MOCK_ACTIVITY_DATA_VERSION = original_activity_version
    if complete_for_recovery is None:
        raise RuntimeError("complete_request scenario is required for recovery probes")

    recovery_probes = run_security_and_transport_recovery(
        client,
        complete_for_recovery,
        source["reference_date"],
        source["parser_config"],
    )
    report = {
        "experiment": "stage-a-structured-vs-clarification",
        "method": "automated interaction simulation with deterministic Mock Parser and Providers",
        "reference_date": source["reference_date"],
        "parser_config": source["parser_config"],
        "scenario_count": len(results),
        "scenario_results": results,
        "metrics": aggregate(results, recovery_probes),
        "limitations": [
            "固定规则语料结果不能外推为真实 LLM 或开放域自然语言准确率。",
            "交互步骤是按预先定义的自动化原子操作计数，不是用户耗时。",
            "实验没有真人参与，不能推导用户满意度、学习成本或真实任务效率。",
            "价格、候选和规划结果来自确定性 Mock Provider，不代表真实市场。",
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = run(args.scenarios, args.output)
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))
    print(f"results: {args.output.resolve()}")


if __name__ == "__main__":
    main()
