"""Strict HTTP contracts for Stage C3 dual-plan comparison."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from api.preference_models import PreferencePlanRequest
from models.multi_plan import MultiPlanConfig, MultiPlanResult
from models.plan_evaluation import (
    PlanComparisonResult,
    RecommendationDecision,
    ScoringPolicy,
)
from models.schemas import UserPreferences


class ComparisonRequestStatus(str, Enum):
    COMPLETED = "completed"
    COMPLETED_WITH_ISSUES = "completed_with_issues"
    NO_PLANS = "no_plans"
    FAILED = "failed"


class ConfirmedPreferenceComparisonRequest(PreferencePlanRequest):
    """Signed clarification context plus independent multi-plan configuration."""

    planning_config: MultiPlanConfig = Field(default_factory=MultiPlanConfig)


class SensitivityDisclosure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluated: bool = True
    same_planning_input: bool
    primary_policy_version: str
    alternative_policy: ScoringPolicy
    primary_decision: RecommendationDecision
    alternative_decision: RecommendationDecision
    primary_recommended_destination_id: str | None = None
    alternative_recommended_destination_id: str | None = None
    decision_changed: bool
    notice: str


class PlanComparisonApiResponse(BaseModel):
    """One server-owned planning run plus its C2 comparison."""

    model_config = ConfigDict(extra="forbid")

    request_status: ComparisonRequestStatus
    confirmation_accepted: bool | None = None
    preferences: UserPreferences
    multi_plan: MultiPlanResult
    comparison: PlanComparisonResult
    sensitivity: SensitivityDisclosure
    is_mock: bool = True
    mock_notice: str = (
        "航班、酒店、活动、天气、时长与强度均为版本化 Mock 数据，"
        "不是实时价格、库存或预报。"
    )
    identity_notice: str = (
        "确认动作仅说明客户端提交了确认；当前没有身份认证，不能证明特定自然人身份。"
    )
