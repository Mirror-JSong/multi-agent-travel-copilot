from __future__ import annotations

from pathlib import Path

from experiments.analyze_usability_results import analyze
from experiments.summarize_d3_evidence import build_summary


def test_d3_evidence_summary_recomputes_all_stage_metrics() -> None:
    summary = build_summary()
    assert summary["stage_a"]["supported_field_extraction"] == {
        "correct": 51,
        "labeled": 51,
        "missing": 0,
        "incorrect": 0,
        "false_positive_count": 0,
        "accuracy": 1.0,
    }
    assert summary["stage_b"]["control"]["weather_conflict_rate"] == 33 / 63
    assert summary["stage_b"]["treatment"]["weather_conflict_rate"] == 0.0
    assert summary["stage_c0_pace"]["by_pace"]["relaxed"]["rest_count"] > 0
    assert summary["stage_c"]["evaluation_evidence"]["rate"] == 1.0
    assert summary["stage_c"]["comparison_state_accuracy"]["rate"] == 1.0


def test_empty_usability_template_is_explicitly_pending() -> None:
    template = Path(__file__).parents[2] / "docs" / "templates" / "D3-用户可用性测试记录模板.csv"
    result = analyze(template)
    assert result["status"] == "pending_real_participants"
    assert result["participant_count"] == 0
    assert "不能计算" in result["message"]


def test_usability_aggregator_uses_consented_records(tmp_path: Path) -> None:
    source = tmp_path / "records.csv"
    source.write_text(
        "participant_id,consent_confirmed,test_date,task_id,completed,duration_seconds,error_count,assistance_count,misunderstanding_notes,recommendation_basis_correct,subjective_feedback,facilitator_notes\n"
        "P01,true,2026-09-26,A,true,60,1,0,,n/a,,\n"
        "P01,true,2026-09-26,C,true,120,0,1,,true,,\n"
        "P02,true,2026-09-26,A,false,90,2,1,,n/a,,\n",
        encoding="utf-8",
    )
    result = analyze(source)
    assert result["participant_count"] == 2
    assert result["tasks"]["A"]["completion_rate"] == 0.5
    assert result["tasks"]["A"]["median_duration_seconds"] == 75.0
    assert result["recommendation_basis_understanding"]["rate"] == 1.0
