"""Aggregate consented, de-identified D3 usability session records."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path
from typing import Any


TRUE_VALUES = {"true", "1", "yes", "y"}
FALSE_VALUES = {"false", "0", "no", "n"}


def _boolean(value: str, *, allow_na: bool = False) -> bool | None:
    normalized = value.strip().lower()
    if allow_na and normalized in {"", "na", "n/a"}:
        return None
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise ValueError(f"invalid boolean value: {value!r}")


def analyze(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        return {
            "status": "pending_real_participants",
            "participant_count": 0,
            "record_count": 0,
            "message": "模板尚无真实参与者记录，不能计算可用性指标。",
        }
    required = {"participant_id", "consent_confirmed", "task_id", "completed", "duration_seconds", "error_count", "assistance_count", "recommendation_basis_correct"}
    missing = required - set(rows[0])
    if missing:
        raise ValueError(f"missing columns: {', '.join(sorted(missing))}")
    if any(not _boolean(row["consent_confirmed"]) for row in rows):
        raise ValueError("all included records must have explicit participant consent")
    participants = sorted({row["participant_id"].strip() for row in rows if row["participant_id"].strip()})
    by_task: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_task.setdefault(row["task_id"].strip(), []).append(row)
    task_metrics = {}
    for task, task_rows in sorted(by_task.items()):
        durations = [float(row["duration_seconds"]) for row in task_rows]
        completions = [_boolean(row["completed"]) for row in task_rows]
        task_metrics[task] = {
            "records": len(task_rows),
            "completed": sum(completions),
            "completion_rate": sum(completions) / len(task_rows),
            "average_duration_seconds": round(statistics.fmean(durations), 2),
            "median_duration_seconds": round(statistics.median(durations), 2),
            "errors": sum(int(row["error_count"]) for row in task_rows),
            "assistance_events": sum(int(row["assistance_count"]) for row in task_rows),
        }
    basis_values = [_boolean(row["recommendation_basis_correct"], allow_na=True) for row in rows]
    evaluable_basis = [value for value in basis_values if value is not None]
    return {
        "status": "completed_real_participant_records",
        "participant_count": len(participants),
        "record_count": len(rows),
        "tasks": task_metrics,
        "recommendation_basis_understanding": {
            "correct": sum(evaluable_basis),
            "evaluable": len(evaluable_basis),
            "rate": sum(evaluable_basis) / len(evaluable_basis) if evaluable_basis else None,
        },
        "privacy_note": "仅汇总参与者代号和经同意收集的任务记录；脚本不需要姓名或联系方式。",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = analyze(args.input)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
