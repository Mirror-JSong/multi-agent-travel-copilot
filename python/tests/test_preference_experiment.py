"""Reproducibility checks for the Stage A product-effect experiment."""

from __future__ import annotations

import json
from pathlib import Path

from experiments.run_preference_experiment import run


EXPERIMENT_DIR = Path(__file__).parents[1] / "experiments"
SCENARIOS = EXPERIMENT_DIR / "preference_clarification_scenarios.json"
RECORDED_RESULTS = EXPERIMENT_DIR / "results" / "preference_clarification_results.json"


def test_fixed_experiment_dataset_has_declared_inputs_and_annotations() -> None:
    source = json.loads(SCENARIOS.read_text(encoding="utf-8"))

    assert source["reference_date"] == "2026-09-24"
    assert source["parser_config"]["provider"] == "mock"
    assert len(source["scenarios"]) == 8
    assert len({scenario["id"] for scenario in source["scenarios"]}) == 8
    for scenario in source["scenarios"]:
        assert scenario["initial_text"]
        assert isinstance(scenario["answers"], list)
        assert scenario["initial_expected"]
        assert scenario["final_preferences"]
        assert "recovery_type" in scenario


def test_experiment_reproduces_recorded_metrics_and_planning_signatures(tmp_path) -> None:
    rerun = run(SCENARIOS, tmp_path / "result.json")
    recorded = json.loads(RECORDED_RESULTS.read_text(encoding="utf-8"))

    assert rerun["metrics"] == recorded["metrics"]
    assert rerun["scenario_results"] == recorded["scenario_results"]
    assert rerun["metrics"]["M1_required_field_collection_completion_rate"]["rate"] == 1.0
    assert rerun["metrics"]["M2_supported_field_extraction_accuracy"]["accuracy"] == 1.0
    assert rerun["metrics"]["M5_planning_consistency"]["rate"] == 1.0
    assert rerun["metrics"]["M6_exception_recovery_success_rate"]["rate"] == 1.0
