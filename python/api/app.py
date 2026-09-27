"""
FastAPI 后端 —— 提供 REST API 接口。

端点:
  POST /api/plan    - 提交行程规划请求
  GET  /api/health  - 健康检查
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from datetime import date, timedelta

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from agents import PLANNING_AGENT_TYPES
from api.comparison_models import (
    ComparisonRequestStatus,
    ConfirmedPreferenceComparisonRequest,
    PlanComparisonApiResponse,
    SensitivityDisclosure,
)
from api.preference_models import (
    ConfirmedPreferencePlanResponse,
    PreferenceApiResponse,
    PreferenceClarifyRequest,
    PreferenceDateSummary,
    PreferenceParseRequest,
    PreferencePlanRequest,
    PreferenceParserConfig,
)
from config.settings import settings
from models.multi_plan import MultiPlanRequest
from models.schemas import (
    ActivityPlanningMode,
    AgentFailure,
    MAX_TRAVEL_BUDGET,
    MAX_TRAVELERS,
    PlanningState,
    TravelPlanState,
    TravelStyle,
    UserPreferences,
)
from orchestrator.pipeline import TravelPlanningPipeline
from orchestrator.comparison_service import PlanComparisonApplicationService
from preferences import ClarificationManager, ClarificationResult, PreferenceField, PreferencesDraft


_preference_manager = ClarificationManager()
_comparison_service = PlanComparisonApplicationService()
_preference_signing_key = (
    os.getenv("PREFERENCE_DRAFT_SIGNING_KEY", "").encode("utf-8")
    or secrets.token_bytes(32)
)

app = FastAPI(
    title="多Agent智能旅游行程规划系统",
    description="7-Agent 天气感知 Pipeline + 并行搜索 + 预算循环",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class PlanRequest(UserPreferences):
    """API 请求模型复用领域校验，避免入口与 Pipeline 规则漂移。"""

    budget: float = Field(
        10000,
        gt=0,
        le=MAX_TRAVEL_BUDGET,
        allow_inf_nan=False,
        strict=True,
        description="总预算（人民币）",
    )
    departure_city: str = Field("北京", min_length=1, max_length=100, description="出发城市")
    start_date: str = Field("2026-05-01", description="出发日期")
    end_date: str = Field("2026-05-05", description="返回日期")
    travel_style: TravelStyle = Field(TravelStyle.COMFORT, description="旅行风格")
    num_travelers: int = Field(1, ge=1, le=MAX_TRAVELERS, strict=True, description="出行人数")


class PlanSummary(BaseModel):
    status: PlanningState
    message: str = ""
    destination: str = ""
    country: str = ""
    flight_cost: float = 0
    hotel_cost: float = 0
    activity_cost: float = 0
    total_cost: float = 0
    budget: float = 0
    within_budget: bool = True
    adjustment_rounds: int = 0
    hotel_name: str = ""
    days: int = 0
    highlights: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[AgentFailure] = Field(default_factory=list)


class WeatherDemoPlanRequest(BaseModel):
    """Opt-in B3 acceptance-demo request; unavailable in normal operation."""

    scenario_id: str = Field(min_length=1, max_length=100)
    planning_mode: ActivityPlanningMode = ActivityPlanningMode.WEATHER_AWARE
    inject_agent_failure: bool = False
    inject_weather_timeout: bool = False


class WeatherDemoPlanResponse(BaseModel):
    mock_demo: bool = True
    scenario_id: str
    planning_mode: ActivityPlanningMode
    plan: TravelPlanState


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "service": "travel-planner",
        "agents": len(PLANNING_AGENT_TYPES),
    }


def _require_weather_demo_enabled() -> None:
    if os.getenv("ENABLE_WEATHER_DEMO_ENDPOINTS", "0") != "1":
        raise HTTPException(status_code=404, detail="Not Found")


@app.post(
    "/api/demo/weather/plan",
    response_model=WeatherDemoPlanResponse,
    include_in_schema=False,
)
async def weather_demo_plan(
    req: WeatherDemoPlanRequest,
) -> WeatherDemoPlanResponse:
    """Run a fixed Mock scenario when the acceptance-demo switch is enabled."""
    _require_weather_demo_enabled()
    from experiments.run_weather_awareness_experiment import run_controlled_plan

    try:
        state = await run_controlled_plan(
            req.scenario_id,
            req.planning_mode,
            inject_agent_failure=req.inject_agent_failure,
            inject_weather_timeout=req.inject_weather_timeout,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return WeatherDemoPlanResponse(
        scenario_id=req.scenario_id,
        planning_mode=req.planning_mode,
        plan=state,
    )


def _draft_receipt_payload(
    draft: PreferencesDraft,
    reference_date: date,
    config: PreferenceParserConfig,
) -> bytes:
    payload = {
        "draft": draft.model_dump(mode="json"),
        "reference_date": reference_date.isoformat(),
        "config": config.model_dump(mode="json"),
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sign_draft(
    draft: PreferencesDraft,
    reference_date: date,
    config: PreferenceParserConfig,
) -> str:
    return hmac.new(
        _preference_signing_key,
        _draft_receipt_payload(draft, reference_date, config),
        hashlib.sha256,
    ).hexdigest()


def _validate_draft_receipt(
    req: (
        PreferenceClarifyRequest
        | PreferencePlanRequest
        | ConfirmedPreferenceComparisonRequest
    ),
) -> None:
    expected = _sign_draft(req.draft, req.reference_date, req.config)
    if not hmac.compare_digest(expected, req.draft_receipt):
        raise HTTPException(
            status_code=422,
            detail=(
                "上一轮偏好草稿、日期基准或解析配置已被修改；"
                "请使用上一轮接口返回的完整草稿和 draft_receipt。"
            ),
        )


def _validated_confirmed_preferences(
    req: PreferencePlanRequest | ConfirmedPreferenceComparisonRequest,
) -> tuple[ClarificationResult, UserPreferences]:
    """Recompute completeness and confirmation; never trust client flags."""
    _validate_draft_receipt(req)
    result = _preference_manager.evaluate(req.draft.model_copy(deep=True))
    if not result.is_complete:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "preferences_incomplete",
                "missing_fields": [
                    field.value for field in result.missing_required_fields
                ],
                "validation_errors": {
                    field.value: message
                    for field, message in result.validation_errors.items()
                },
                "questions": [
                    question.model_dump(mode="json")
                    for question in result.questions
                ],
            },
        )
    if not req.confirmed:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "confirmation_required",
                "message": "用户尚未确认最终偏好，不能启动正式规划。",
            },
        )
    return result, _preference_manager.to_user_preferences(result)


def _comparison_request_status(result) -> ComparisonRequestStatus:
    if not result.orchestration_completed:
        return ComparisonRequestStatus.FAILED
    if result.generated_plan_count == 0:
        return ComparisonRequestStatus.NO_PLANS
    if (
        result.generated_plan_count == result.requested_plan_count
        and result.successful_plan_count == result.generated_plan_count
    ):
        return ComparisonRequestStatus.COMPLETED
    return ComparisonRequestStatus.COMPLETED_WITH_ISSUES


async def _run_comparison(
    preferences: UserPreferences,
    config,
    *,
    confirmation_accepted: bool | None,
) -> PlanComparisonApiResponse:
    application_result = await _comparison_service.compare(preferences, config)
    primary = application_result.comparison
    alternative = application_result.sensitivity_comparison
    decision_changed = (
        primary.recommendation.decision != alternative.recommendation.decision
        or primary.recommendation.recommended_destination_id
        != alternative.recommendation.recommended_destination_id
    )
    notice = (
        "同一规划结果在已声明的替代权重下产生了不同推荐结论；"
        "当前推荐对权重敏感，应结合分项事实自主选择。"
        if decision_changed
        else
        "已使用同一规划结果执行替代权重检查，本次正式推荐结论未改变；"
        "这不代表对其他权重设置均不敏感。"
    )
    return PlanComparisonApiResponse(
        request_status=_comparison_request_status(application_result.multi_plan),
        confirmation_accepted=confirmation_accepted,
        preferences=preferences.model_copy(deep=True),
        multi_plan=application_result.multi_plan,
        comparison=primary,
        sensitivity=SensitivityDisclosure(
            # Both comparators consume the one immutable C1 result created above.
            # The C2 fingerprint intentionally includes the scoring-policy version,
            # so comparing those fingerprints would incorrectly report different
            # planning inputs whenever only the weights change.
            same_planning_input=True,
            primary_policy_version=primary.scoring_policy.policy_version,
            alternative_policy=alternative.scoring_policy,
            primary_decision=primary.recommendation.decision,
            alternative_decision=alternative.recommendation.decision,
            primary_recommended_destination_id=(
                primary.recommendation.recommended_destination_id
            ),
            alternative_recommended_destination_id=(
                alternative.recommendation.recommended_destination_id
            ),
            decision_changed=decision_changed,
            notice=notice,
        ),
    )


def _date_summary(draft: PreferencesDraft) -> PreferenceDateSummary | None:
    if not draft.start_date or not draft.end_date:
        return None
    try:
        start = date.fromisoformat(draft.start_date)
        end = date.fromisoformat(draft.end_date)
    except ValueError:
        return None
    nights = (end - start).days
    if nights <= 0:
        return None
    return PreferenceDateSummary(
        departure_date=start.isoformat(),
        return_date=end.isoformat(),
        activity_start_date=start.isoformat(),
        activity_end_date=(end - timedelta(days=1)).isoformat(),
        hotel_nights=nights,
    )


def _preference_response(
    result: ClarificationResult,
    reference_date: date,
    config: PreferenceParserConfig,
) -> PreferenceApiResponse:
    pipeline_preview = (
        _preference_manager.to_user_preferences(result)
        if result.is_complete
        else None
    )
    draft_only_fields = []
    return PreferenceApiResponse(
        draft=result.draft,
        clarification_result=result,
        missing_fields=result.missing_required_fields,
        questions=result.questions,
        is_complete=result.is_complete,
        can_confirm=result.is_complete,
        date_summary=_date_summary(result.draft),
        pipeline_preferences_preview=pipeline_preview,
        draft_only_fields=draft_only_fields,
        draft_receipt=_sign_draft(result.draft, reference_date, config),
        parser_config=config,
    )


@app.post("/api/preferences/parse", response_model=PreferenceApiResponse)
async def parse_preferences(req: PreferenceParseRequest) -> PreferenceApiResponse:
    result = await _preference_manager.start(
        req.text,
        reference_date=req.reference_date,
    )
    return _preference_response(result, req.reference_date, req.config)


@app.post("/api/preferences/clarify", response_model=PreferenceApiResponse)
async def clarify_preferences(req: PreferenceClarifyRequest) -> PreferenceApiResponse:
    _validate_draft_receipt(req)
    # Completion and errors are recomputed by the domain manager. The API never
    # accepts a client-supplied is_complete/can_confirm flag.
    current = _preference_manager.evaluate(req.draft.model_copy(deep=True))
    result = await _preference_manager.clarify(
        current,
        req.answer,
        reference_date=req.reference_date,
    )
    return _preference_response(result, req.reference_date, req.config)


@app.post(
    "/api/preferences/plan",
    response_model=ConfirmedPreferencePlanResponse,
)
async def plan_confirmed_preferences(
    req: PreferencePlanRequest,
) -> ConfirmedPreferencePlanResponse:
    _validate_draft_receipt(req)
    result = _preference_manager.evaluate(req.draft.model_copy(deep=True))
    if not result.is_complete:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "preferences_incomplete",
                "missing_fields": [
                    field.value for field in result.missing_required_fields
                ],
                "validation_errors": {
                    field.value: message
                    for field, message in result.validation_errors.items()
                },
                "questions": [
                    question.model_dump(mode="json")
                    for question in result.questions
                ],
            },
        )
    if not req.confirmed:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "confirmation_required",
                "message": "用户尚未确认最终偏好，不能启动正式规划。",
            },
        )

    preferences = _preference_manager.to_user_preferences(result)
    # PreferenceAgent may enrich its Pipeline-owned copy (for example, default
    # interests). Keep the confirmed API snapshot immutable and traceable.
    state = await TravelPlanningPipeline().run(preferences.model_copy(deep=True))
    draft_only_fields = []
    return ConfirmedPreferencePlanResponse(
        confirmation_accepted=True,
        preferences=preferences,
        draft=result.draft,
        draft_only_fields=draft_only_fields,
        plan=state,
    )


@app.post(
    "/api/plans/compare",
    response_model=PlanComparisonApiResponse,
)
async def compare_structured_plans(
    req: MultiPlanRequest,
) -> PlanComparisonApiResponse:
    """Generate and evaluate independent plans from validated preferences."""
    return await _run_comparison(
        req.preferences,
        req.config,
        confirmation_accepted=None,
    )


@app.post(
    "/api/preferences/compare",
    response_model=PlanComparisonApiResponse,
)
async def compare_confirmed_preferences(
    req: ConfirmedPreferenceComparisonRequest,
) -> PlanComparisonApiResponse:
    """Verify the HMAC draft boundary before running the same comparison service."""
    _, preferences = _validated_confirmed_preferences(req)
    return await _run_comparison(
        preferences,
        req.planning_config,
        confirmation_accepted=True,
    )


@app.post("/api/plan", response_model=PlanSummary)
async def create_plan(req: PlanRequest):
    prefs = UserPreferences.model_validate(req.model_dump())

    pipeline = TravelPlanningPipeline()
    state: TravelPlanState = await pipeline.run(prefs)

    dest = state.selected_destination
    bb = state.budget_breakdown

    return PlanSummary(
        status=state.state,
        message=state.status_message,
        destination=dest.city if dest else "",
        country=dest.country if dest else "",
        flight_cost=bb.flight_cost if bb else 0,
        hotel_cost=bb.hotel_cost if bb else 0,
        activity_cost=bb.activity_cost if bb else 0,
        total_cost=bb.total_cost if bb else 0,
        budget=bb.budget if bb else req.budget,
        within_budget=bb.is_within_budget if bb else False,
        adjustment_rounds=state.adjustment_round,
        hotel_name=state.hotel_result.recommended.name if state.hotel_result and state.hotel_result.recommended else "",
        days=len(state.activity_result.day_plans) if state.activity_result else 0,
        highlights=dest.highlights if dest else [],
        warnings=state.error_messages,
        errors=state.agent_failures,
    )


@app.post("/api/plan/full")
async def create_plan_full(req: PlanRequest):
    """返回完整的 TravelPlanState（用于调试和前端渲染）。"""
    prefs = UserPreferences.model_validate(req.model_dump())

    pipeline = TravelPlanningPipeline()
    state = await pipeline.run(prefs)
    return state.model_dump()


def start():
    import uvicorn
    uvicorn.run(app, host=settings.API_HOST, port=settings.API_PORT)


if __name__ == "__main__":
    start()
