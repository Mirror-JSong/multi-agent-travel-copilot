"""Recompute the final product evidence summary from saved experiment records.

The inputs are deterministic Mock/fixture experiment outputs.  This script does
not turn automated evidence into user-research, live-market, or LLM claims.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
DEFAULT_OUTPUT = RESULTS / "d3_product_evidence_summary.json"


def _load(name: str) -> tuple[dict[str, Any], dict[str, str]]:
    path = RESULTS / name
    raw = path.read_bytes()
    return json.loads(raw.decode("utf-8")), {
        "path": str(path.relative_to(ROOT.parent)),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _ratio(numerator: int | float, denominator: int | float) -> float | None:
    return numerator / denominator if denominator else None


def _summarize_stage_a(report: dict[str, Any]) -> dict[str, Any]:
    rows = report["scenario_results"]
    extractions = [row["information_extraction"] for row in rows]
    recoveries = report["metrics"]["M6_exception_recovery_success_rate"]["cases"]
    correct = sum(item["correct"] for item in extractions)
    labeled = sum(item["labeled_fields"] for item in extractions)
    natural_steps = sum(row["information_collection"]["natural_interaction_steps"] for row in rows)
    structured_steps = sum(row["information_collection"]["structured_interaction_steps"] for row in rows)
    return {
        "scenario_count": len(rows),
        "required_field_collection": {
            "completed": sum(row["information_collection"]["final_complete"] for row in rows),
            "total": len(rows),
            "rate": _ratio(sum(row["information_collection"]["final_complete"] for row in rows), len(rows)),
        },
        "supported_field_extraction": {
            "correct": correct,
            "labeled": labeled,
            "missing": sum(item["missing"] for item in extractions),
            "incorrect": sum(item["incorrect"] for item in extractions),
            "false_positive_count": sum(len(item["false_positive_fields"]) for item in extractions),
            "accuracy": _ratio(correct, labeled),
        },
        "clarification_rounds": {
            "total": sum(row["information_collection"]["clarification_rounds"] for row in rows),
            "average": _ratio(sum(row["information_collection"]["clarification_rounds"] for row in rows), len(rows)),
        },
        "interaction_steps": {
            "natural_total": natural_steps,
            "structured_total": structured_steps,
            "natural_average": _ratio(natural_steps, len(rows)),
            "structured_average": _ratio(structured_steps, len(rows)),
        },
        "planning_consistency": {
            "consistent": sum(row["planning"]["consistent"] for row in rows),
            "total": len(rows),
            "rate": _ratio(sum(row["planning"]["consistent"] for row in rows), len(rows)),
        },
        "exception_recovery": {
            "successful": sum(item["success"] for item in recoveries),
            "total": len(recoveries),
            "rate": _ratio(sum(item["success"] for item in recoveries), len(recoveries)),
        },
    }


def _weather_group(rows: list[dict[str, Any]], group: str) -> dict[str, Any]:
    measurements = [row[group]["measurement"] for row in rows]
    conflicts = sum(item["weather_conflict_count"] for item in measurements)
    evaluable = sum(item["evaluable_count"] for item in measurements)
    matches = sum(item["interest_match_count"] for item in measurements)
    interest_samples = sum(item["interest_sample_count"] for item in measurements)
    return {
        "weather_conflicts": conflicts,
        "weather_evaluable_activities": evaluable,
        "weather_conflict_rate": _ratio(conflicts, evaluable),
        "interest_matches": matches,
        "interest_samples": interest_samples,
        "interest_match_rate": _ratio(matches, interest_samples),
        "budget_compliant": sum(item["budget_compliant"] for item in measurements),
        "verified_weather_feasible": sum(item["verified_weather_feasible"] for item in measurements),
        "operationally_complete": sum(item["operational_complete"] for item in measurements),
        "scenario_count": len(rows),
    }


def _summarize_stage_b(report: dict[str, Any]) -> dict[str, Any]:
    rows = report["scenario_results"]
    changes = [
        decision
        for row in rows
        for decision in row["treatment"]["weather_decisions"]
        if decision["weather_driven_change"]
    ]
    traceable = [
        item for item in changes
        if item.get("baseline_candidate_id") and item.get("final_candidate_id") and item.get("reason")
    ]
    return {
        "scenario_count": len(rows),
        "control": _weather_group(rows, "control"),
        "treatment": _weather_group(rows, "treatment"),
        "weather_change_explanation": {
            "actual_changes": len(changes),
            "traceable_changes": len(traceable),
            "coverage": _ratio(len(traceable), len(changes)),
        },
        "paired_cost_changes": [
            {
                "scenario_id": row["id"],
                "control_final_total": row["control"]["final_total_cost"],
                "treatment_final_total": row["treatment"]["final_total_cost"],
                "delta": (
                    row["treatment"]["final_total_cost"] - row["control"]["final_total_cost"]
                    if row["treatment"]["final_total_cost"] is not None
                    and row["control"]["final_total_cost"] is not None
                    else None
                ),
                "control_state": row["control"]["state"],
                "treatment_state": row["treatment"]["state"],
            }
            for row in rows
        ],
    }


def _summarize_pace(report: dict[str, Any]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in report["scenario_results"]:
        groups[row["pace"] or "unspecified"].append(row)
    summary = {}
    for pace, rows in sorted(groups.items()):
        activity_count = sum(row["activity_count"] for row in rows)
        day_count = sum(len(row["daily_activity_counts"]) for row in rows)
        known_intensity = sum(row["known_intensity_count"] for row in rows)
        evaluable_weather = sum(row["weather_evaluable_count"] for row in rows)
        summary[pace] = {
            "scenario_count": len(rows),
            "average_daily_activity_count": _ratio(activity_count, day_count),
            "rest_count": sum(row["rest_count"] for row in rows),
            "known_high_intensity_rate": _ratio(sum(row["known_high_intensity_count"] for row in rows), known_intensity),
            "interest_match_rate": _ratio(sum(row["interest_match_count"] for row in rows), activity_count),
            "weather_hard_pass_rate": _ratio(sum(row["weather_hard_pass_count"] for row in rows), evaluable_weather),
            "states": {state: sum(row["state"] == state for row in rows) for state in sorted({row["state"] for row in rows})},
        }
    return {"scenario_count": len(report["scenario_results"]), "by_pace": summary}


def _summarize_stage_c(c2: dict[str, Any], c3: dict[str, Any]) -> dict[str, Any]:
    rows = c3["scenarios"]
    generated_pairs = [row for row in rows if row["treatment"]["generated_plan_count"] == 2]
    evaluated = sum(row["treatment"]["evaluated_metric_count"] for row in rows)
    evidenced = sum(row["treatment"]["evidenced_metric_count"] for row in rows)
    dimensions = sum(len(row["treatment"]["difference_dimensions"]) for row in generated_pairs)
    defined_dimensions = 4 * len(generated_pairs)
    return {
        "c2_decisions": {row["scenario_id"]: row["summary"]["recommendation"]["decision"] for row in c2["scenarios"]},
        "weight_sensitivity": {
            "default_decision": c2["sensitivity"]["default"]["recommendation"]["decision"],
            "shifted_decision": c2["sensitivity"]["shifted"]["recommendation"]["decision"],
        },
        "c3_scenario_count": len(rows),
        "two_completed_plans": {
            "scenarios": sum(row["treatment"]["completed_plan_count"] == 2 for row in rows),
            "total": len(rows),
            "rate": _ratio(sum(row["treatment"]["completed_plan_count"] == 2 for row in rows), len(rows)),
        },
        "evaluation_evidence": {"evidenced": evidenced, "evaluated": evaluated, "rate": _ratio(evidenced, evaluated)},
        "explanation_consistency": {
            "consistent": sum(row["treatment"]["explanation_audit"]["all_consistent"] for row in rows),
            "total": len(rows),
            "rate": _ratio(sum(row["treatment"]["explanation_audit"]["all_consistent"] for row in rows), len(rows)),
        },
        "comparison_state_accuracy": {
            "correct": sum(row["treatment"]["decision_matches_expected"] for row in rows),
            "total": len(rows),
            "rate": _ratio(sum(row["treatment"]["decision_matches_expected"] for row in rows), len(rows)),
        },
        "difference_information": {"reported": dimensions, "defined": defined_dimensions, "rate": _ratio(dimensions, defined_dimensions)},
        "timing_ms": {
            "control_total": round(sum(row["control"]["elapsed_ms"] for row in rows), 4),
            "treatment_total": round(sum(row["treatment"]["elapsed_ms"] for row in rows), 4),
            "additional_by_scenario": {row["scenario_id"]: row["treatment"]["additional_elapsed_ms"] for row in rows},
        },
    }


def build_summary() -> dict[str, Any]:
    preference, preference_source = _load("preference_clarification_results.json")
    weather, weather_source = _load("weather_awareness_results.json")
    pace, pace_source = _load("pace_planning_results.json")
    c2, c2_source = _load("plan_comparison_c2_results.json")
    c3, c3_source = _load("comparison_product_c3_results.json")
    return {
        "evidence_type": "deterministic_mock_automated_experiments",
        "stage_a": _summarize_stage_a(preference),
        "stage_b": _summarize_stage_b(weather),
        "stage_c0_pace": _summarize_pace(pace),
        "stage_c": _summarize_stage_c(c2, c3),
        "source_files": {
            "stage_a": preference_source,
            "stage_b": weather_source,
            "stage_c0": pace_source,
            "stage_c2": c2_source,
            "stage_c3": c3_source,
        },
        "claim_boundaries": [
            "固定 Mock 自动化实验，不等同于真实 LLM 泛化能力。",
            "HTTP/浏览器功能验收与真人可用性测试是不同证据层级。",
            "没有真实参与者记录时，不能报告用户满意度、任务耗时或可用性成功率。",
            "候选、价格、天气和库存均不是实时市场数据。",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = build_summary()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "stages": ["A", "B", "C0", "C"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
