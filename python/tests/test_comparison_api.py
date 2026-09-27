"""Stage C3 HTTP contracts for dual-plan comparison."""

from __future__ import annotations

import copy
import importlib
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.preference_models import PreferenceParserConfig
from models.multi_plan import MultiPlanExecutionMode
from models.plan_evaluation import RecommendationDecision
from models.schemas import PlanningState, TravelPace, TravelStyle
from orchestrator.comparison import ComparisonOrchestrator
from orchestrator.comparison_service import PlanComparisonApplicationService
from preferences import PreferencesDraft
from tests.fixtures.multi_plan import (
    DESTINATION_A,
    DESTINATION_B,
    FixedDestinationAgent,
    fixed_pipeline_factory,
)
from tests.fixtures.plan_comparison import (
    c2_orchestrator,
    c2_request,
    custom_orchestrator,
    interest_tradeoff_pipeline_factory,
    unavailable_weather_pipeline_factory,
)


api_module = importlib.import_module("api.app")
client = TestClient(api_module.app)
REFERENCE_DATE = date(2026, 9, 24)
PARSER_CONFIG = PreferenceParserConfig()
PYTHON_ROOT = Path(__file__).resolve().parent.parent


def _install_service(monkeypatch, factory) -> None:
    monkeypatch.setattr(
        api_module,
        "_comparison_service",
        PlanComparisonApplicationService(orchestrator_factory=factory),
    )


def _structured_payload(**request_kwargs) -> dict:
    return c2_request(**request_kwargs).model_dump(mode="json")


def _complete_draft(**updates) -> PreferencesDraft:
    values = {
        "raw_text": "C3 fixed confirmed preference fixture",
        "departure_city": "Origin",
        "budget": 5000.0,
        "start_date": "2026-05-01",
        "end_date": "2026-05-03",
        "duration_days": 2,
        "num_travelers": 1,
        "travel_style": TravelStyle.COMFORT,
        "pace": TravelPace.BALANCED,
        "interests": ["文化"],
    }
    values.update(updates)
    return PreferencesDraft(**values)


def _confirmed_payload(draft: PreferencesDraft, *, confirmed: bool = True) -> dict:
    receipt = api_module._sign_draft(draft, REFERENCE_DATE, PARSER_CONFIG)
    return {
        "draft": draft.model_dump(mode="json"),
        "draft_receipt": receipt,
        "confirmed": confirmed,
        "reference_date": REFERENCE_DATE.isoformat(),
        "config": PARSER_CONFIG.model_dump(mode="json"),
        "planning_config": c2_request().config.model_dump(mode="json"),
    }


def _post_structured(payload: dict):
    return client.post("/api/plans/compare", json=payload)


def _post_confirmed(payload: dict):
    return client.post("/api/preferences/compare", json=payload)


def test_c3_t1_structured_endpoint_returns_server_owned_plans_and_comparison(monkeypatch):
    _install_service(monkeypatch, c2_orchestrator)

    response = _post_structured(_structured_payload(budget=3600))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["request_status"] == "completed"
    assert body["confirmation_accepted"] is None
    assert body["multi_plan"]["generated_plan_count"] == 2
    assert len(body["multi_plan"]["plans"]) == 2
    assert len(body["comparison"]["plan_evaluations"]) == 2
    assert body["comparison"]["scoring_policy"]["policy_version"] == "plan-scoring-v1"
    assert body["sensitivity"]["same_planning_input"] is True
    assert body["is_mock"] is True


def test_c3_t2_confirmed_preference_endpoint_reuses_signed_draft(monkeypatch):
    _install_service(monkeypatch, c2_orchestrator)
    payload = _confirmed_payload(_complete_draft())

    response = _post_confirmed(payload)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["confirmation_accepted"] is True
    assert body["preferences"]["departure_city"] == "Origin"
    assert body["preferences"]["pace"] == "balanced"
    assert body["multi_plan"]["request_preferences"] == body["preferences"]


def test_c3_t3_t4_tampering_unconfirmed_and_incomplete_drafts_are_rejected(monkeypatch):
    _install_service(monkeypatch, c2_orchestrator)
    valid = _confirmed_payload(_complete_draft())

    tampered = copy.deepcopy(valid)
    tampered["draft"]["budget"] = 1
    response = _post_confirmed(tampered)
    assert response.status_code == 422

    response = _post_confirmed({**valid, "confirmed": False})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "confirmation_required"

    incomplete = _complete_draft(num_travelers=None)
    response = _post_confirmed(_confirmed_payload(incomplete))
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "preferences_incomplete"

    client_scored = _structured_payload()
    client_scored["overall_score"] = 100
    assert _post_structured(client_scored).status_code == 422


@pytest.mark.parametrize(
    ("budget", "expected_decision", "expected_request_status"),
    [
        (5000, RecommendationDecision.TIE.value, "completed"),
        (2500, RecommendationDecision.ONLY_FEASIBLE.value, "completed_with_issues"),
        (100, RecommendationDecision.NO_RECOMMENDATION.value, "completed_with_issues"),
    ],
)
def test_c3_t7_t8_t10_comparison_states_preserve_business_semantics(
    monkeypatch, budget, expected_decision, expected_request_status
):
    _install_service(monkeypatch, c2_orchestrator)

    response = _post_structured(_structured_payload(budget=budget))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["request_status"] == expected_request_status
    assert body["comparison"]["recommendation"]["decision"] == expected_decision
    statuses = [plan["status"] for plan in body["multi_plan"]["plans"]]
    if budget == 2500:
        assert statuses == ["completed", "budget_infeasible"]
        assert body["comparison"]["plan_evaluations"][1]["overall_score"] is None
    if budget == 100:
        assert statuses == ["budget_infeasible", "budget_infeasible"]
        assert all(
            evaluation["overall_score"] is None
            for evaluation in body["comparison"]["plan_evaluations"]
        )


def test_c3_t6_t13_recommendation_explanations_versions_and_sensitivity_are_exposed(monkeypatch):
    _install_service(
        monkeypatch,
        lambda: custom_orchestrator(interest_tradeoff_pipeline_factory),
    )

    response = _post_structured(_structured_payload(budget=3600))

    assert response.status_code == 200, response.text
    body = response.json()
    recommendation = body["comparison"]["recommendation"]
    assert recommendation["decision"] == "recommended"
    assert recommendation["recommended_destination_id"]
    assert recommendation["evidence"]
    assert body["comparison"]["explanations"]
    assert body["comparison"]["effective_weights"]
    assert body["sensitivity"]["primary_policy_version"] == "plan-scoring-v1"
    assert body["sensitivity"]["alternative_policy"]["policy_version"] == "plan-scoring-sensitivity-v1"
    assert body["sensitivity"]["decision_changed"] is True


def test_c3_t9_t11_weather_unavailable_never_creates_fake_score(monkeypatch):
    _install_service(
        monkeypatch,
        lambda: custom_orchestrator(unavailable_weather_pipeline_factory),
    )

    response = _post_structured(_structured_payload())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["comparison"]["recommendation"]["decision"] == "no_recommendation"
    assert body["comparison"]["comparability"] == "not_comparable"
    for evaluation in body["comparison"]["plan_evaluations"]:
        weather = next(
            metric
            for metric in evaluation["metric_evaluations"]
            if metric["metric_name"] == "weather_suitability"
        )
        assert weather["evaluation_status"] == "insufficient_data"
        assert weather["normalized_score"] is None


def test_c3_t10_agent_failure_is_not_flattened_into_a_low_score(monkeypatch):
    _install_service(
        monkeypatch,
        lambda: c2_orchestrator(failing_flight_city=DESTINATION_B.city),
    )

    response = _post_structured(_structured_payload())

    assert response.status_code == 200, response.text
    body = response.json()
    failed = body["multi_plan"]["plans"][1]
    assert failed["status"] == PlanningState.FAILED.value
    assert failed["errors"]
    failed_eval = body["comparison"]["plan_evaluations"][1]
    assert failed_eval["evaluation_eligibility"] == "failed"
    assert failed_eval["overall_score"] is None
    assert body["comparison"]["recommendation"]["decision"] == "only_feasible"


def test_c3_t12_costs_history_and_metric_evidence_reconcile(monkeypatch):
    _install_service(monkeypatch, c2_orchestrator)

    body = _post_structured(_structured_payload(budget=2500)).json()

    for destination_plan in body["multi_plan"]["plans"]:
        plan = destination_plan["plan"]
        comparison_data = destination_plan["comparison_data"]
        if plan["budget_breakdown"] is not None:
            breakdown = plan["budget_breakdown"]
            assert breakdown["total_cost"] == pytest.approx(
                breakdown["flight_cost"]
                + breakdown["hotel_cost"]
                + breakdown["activity_cost"]
            )
            assert comparison_data["final_total_cost"] == breakdown["total_cost"]
        for adjustment in plan["adjustment_history"]:
            assert adjustment["saved_amount"] == pytest.approx(
                adjustment["before_cost"] - adjustment["after_cost"]
            )
    assert all(
        metric["evidence"]
        for evaluation in body["comparison"]["plan_evaluations"]
        for metric in evaluation["metric_evaluations"]
        if metric["evaluation_status"] == "evaluated"
    )


def test_c3_t17_only_one_destination_is_reported_without_fabrication(monkeypatch):
    def one_destination_orchestrator():
        return ComparisonOrchestrator(
            destination_agent=FixedDestinationAgent([DESTINATION_A]),
            pipeline_factory=fixed_pipeline_factory(),
        )

    _install_service(monkeypatch, one_destination_orchestrator)

    response = _post_structured(_structured_payload())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["request_status"] == "completed_with_issues"
    assert body["multi_plan"]["generated_plan_count"] == 1
    assert len(body["multi_plan"]["plans"]) == 1
    assert body["comparison"]["recommendation"]["decision"] == "no_recommendation"


def test_c3_t18_same_http_input_is_deterministic_five_times(monkeypatch):
    _install_service(monkeypatch, c2_orchestrator)
    payload = _structured_payload(budget=3600)

    bodies = [_post_structured(payload).json() for _ in range(5)]

    normalized = []
    for body in bodies:
        body = copy.deepcopy(body)
        body["multi_plan"].pop("request_id")
        body["comparison"].pop("request_id")
        normalized.append(body)
    assert all(item == normalized[0] for item in normalized[1:])


def test_c3_t18_comparison_response_is_identical_across_python_processes():
    script = r'''
import asyncio
import base64
from api import app as api_module
from orchestrator.comparison_service import PlanComparisonApplicationService
from tests.fixtures.plan_comparison import c2_orchestrator, c2_request

async def main():
    api_module._comparison_service = PlanComparisonApplicationService(
        orchestrator_factory=c2_orchestrator
    )
    request = c2_request(budget=3600)
    response = await api_module._run_comparison(
        request.preferences,
        request.config,
        confirmation_accepted=None,
    )
    print("C3JSON:" + base64.b64encode(
        response.model_dump_json().encode("utf-8")
    ).decode("ascii"))

asyncio.run(main())
'''
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PYTHON_ROOT)
    outputs = [
        subprocess.run(
            [sys.executable, "-c", script],
            cwd=PYTHON_ROOT.parent,
            env=env,
            capture_output=True,
            check=True,
        ).stdout.split(b"C3JSON:")[-1].strip()
        for _ in range(2)
    ]
    assert outputs[0] == outputs[1]


@pytest.mark.asyncio
async def test_c3_t19_serial_and_concurrent_plans_get_same_comparison():
    service = PlanComparisonApplicationService(orchestrator_factory=c2_orchestrator)
    serial_request = c2_request(
        budget=3600,
        execution_mode=MultiPlanExecutionMode.SERIAL,
    )
    concurrent_request = c2_request(
        budget=3600,
        execution_mode=MultiPlanExecutionMode.CONCURRENT,
    )

    serial = await service.compare(serial_request.preferences, serial_request.config)
    concurrent = await service.compare(
        concurrent_request.preferences,
        concurrent_request.config,
    )

    assert serial.comparison == concurrent.comparison
    assert serial.sensitivity_comparison == concurrent.sensitivity_comparison
    assert [item.plan for item in serial.multi_plan.plans] == [
        item.plan for item in concurrent.multi_plan.plans
    ]
