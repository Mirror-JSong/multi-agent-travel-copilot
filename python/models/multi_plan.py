"""Stage C1 contracts for independent multi-destination planning.

These models intentionally expose factual, traceable plan data only.  Ranking,
weighted comparison scores and recommendation explanations belong to C2.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from config.settings import settings
from models.schemas import (
    ActivityAttributeStatus,
    ActivityIntensity,
    ActivityPlanningMode,
    AgentFailure,
    Destination,
    PlanningState,
    TravelPlanState,
    UserPreferences,
    WeatherScenario,
)


MULTI_PLAN_CONTRACT_VERSION = "multi-plan-contract-v1"
DESTINATION_DATA_VERSION = "2026.09-destination-v1"


class MultiPlanExecutionMode(str, Enum):
    SERIAL = "serial"
    CONCURRENT = "concurrent"


class MultiPlanConfig(BaseModel):
    """Explicit deterministic configuration shared by both destination plans."""

    model_config = ConfigDict(extra="forbid")

    execution_mode: MultiPlanExecutionMode = MultiPlanExecutionMode.CONCURRENT
    max_concurrency: int = Field(default=2, ge=1, le=2, strict=True)
    activity_planning_mode: ActivityPlanningMode = ActivityPlanningMode.WEATHER_AWARE
    weather_scenario: WeatherScenario = WeatherScenario.GENERATED
    mock_data_version: str = Field(
        default_factory=lambda: settings.MOCK_DATA_VERSION,
        min_length=1,
        max_length=100,
    )
    mock_activity_data_version: str = Field(
        default_factory=lambda: settings.MOCK_ACTIVITY_DATA_VERSION,
        min_length=1,
        max_length=100,
    )
    destination_data_version: str = Field(
        default=DESTINATION_DATA_VERSION,
        min_length=1,
        max_length=100,
    )


class MultiPlanRequest(BaseModel):
    """Validated request for up to two independently budgeted plans."""

    model_config = ConfigDict(extra="forbid")

    preferences: UserPreferences
    plan_count: int = Field(default=2, ge=1, le=2, strict=True)
    config: MultiPlanConfig = Field(default_factory=MultiPlanConfig)


class DestinationCandidateSummary(BaseModel):
    """Stable, ordered output retained from the one destination-discovery run."""

    model_config = ConfigDict(extra="forbid")

    destination_id: str = Field(min_length=1, max_length=100)
    destination: Destination
    rank: int = Field(ge=1, strict=True)
    selected_for_planning: bool
    selection_reason: str = Field(min_length=1, max_length=500)
    source_version: str = Field(min_length=1, max_length=100)


class MultiPlanIssue(BaseModel):
    """An orchestration/business issue, distinct from an Agent program failure."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=1000)
    destination_id: str | None = Field(default=None, min_length=1, max_length=100)


class ActivityComparisonFact(BaseModel):
    """Raw activity/rest facts that C2 can compare without inventing scores."""

    model_config = ConfigDict(extra="forbid")

    date: str
    time_slot: str
    candidate_id: str | None = None
    is_rest: bool = False
    price_per_traveler: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    matched_interests: list[str] = Field(default_factory=list)
    weather_compatible: bool | None = None
    pace_compatible: bool | None = None
    intensity: ActivityIntensity | None = None
    intensity_status: ActivityAttributeStatus | None = None
    duration_hours: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    duration_status: ActivityAttributeStatus | None = None
    reason: str = ""

    @model_validator(mode="after")
    def validate_activity_or_rest(self) -> ActivityComparisonFact:
        if self.is_rest and self.candidate_id is not None:
            raise ValueError("休息时段不得伪造活动 candidate_id")
        if not self.is_rest and not self.candidate_id:
            raise ValueError("活动事实必须包含 candidate_id")
        return self


class PlanComparisonData(BaseModel):
    """C1 factual projection; deliberately contains no aggregate recommendation score."""

    model_config = ConfigDict(extra="forbid")

    budget: float = Field(gt=0, allow_inf_nan=False)
    flight_cost: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    hotel_cost: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    activity_cost: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    final_total_cost: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    within_budget: bool | None = None
    adjustment_round: int = Field(default=0, ge=0, strict=True)
    adjustment_count: int = Field(default=0, ge=0, strict=True)
    daily_activity_counts: dict[str, int] = Field(default_factory=dict)
    activity_facts: list[ActivityComparisonFact] = Field(default_factory=list)
    source_versions: dict[str, list[str]] = Field(default_factory=dict)
    candidate_snapshot_available: bool = False
    missing_data: list[str] = Field(default_factory=list)


class DestinationPlan(BaseModel):
    """One destination's complete and independently owned planning result."""

    model_config = ConfigDict(extra="forbid")

    destination_id: str = Field(min_length=1, max_length=100)
    destination: Destination
    plan: TravelPlanState
    status: PlanningState
    execution_completed: bool
    errors: list[AgentFailure] = Field(default_factory=list)
    business_issues: list[MultiPlanIssue] = Field(default_factory=list)
    comparison_data: PlanComparisonData

    @model_validator(mode="after")
    def validate_plan_identity(self) -> DestinationPlan:
        if self.status != self.plan.state:
            raise ValueError("DestinationPlan status 必须与内部 plan.state 一致")
        selected = self.plan.selected_destination
        if selected is None:
            raise ValueError("DestinationPlan 必须保留 selected_destination")
        if (selected.city, selected.country) != (
            self.destination.city,
            self.destination.country,
        ):
            raise ValueError("内部规划目的地与 DestinationPlan 目的地不一致")
        return self


class MultiPlanResult(BaseModel):
    """Result of orchestration, even when one or both business plans are infeasible."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=100)
    request_preferences: UserPreferences | None = Field(
        default=None,
        description=(
            "Original validated request preferences before PreferenceAgent adds "
            "backward-compatible defaults; retained for fair C2 evaluation."
        ),
    )
    destination_candidates: list[DestinationCandidateSummary] = Field(default_factory=list)
    plans: list[DestinationPlan] = Field(default_factory=list)
    requested_plan_count: int = Field(ge=1, le=2, strict=True)
    selected_destination_count: int = Field(ge=0, le=2, strict=True)
    generated_plan_count: int = Field(ge=0, le=2, strict=True)
    successful_plan_count: int = Field(ge=0, le=2, strict=True)
    failed_or_infeasible_plan_count: int = Field(ge=0, le=2, strict=True)
    orchestration_completed: bool
    execution_mode: MultiPlanExecutionMode
    issues: list[MultiPlanIssue] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_counts_and_isolation_keys(self) -> MultiPlanResult:
        if self.generated_plan_count != len(self.plans):
            raise ValueError("generated_plan_count 必须等于 plans 数量")
        if self.selected_destination_count != sum(
            item.selected_for_planning for item in self.destination_candidates
        ):
            raise ValueError("selected_destination_count 与候选选择标记不一致")
        completed = sum(
            item.status == PlanningState.COMPLETED for item in self.plans
        )
        if self.successful_plan_count != completed:
            raise ValueError("successful_plan_count 仅统计 COMPLETED 方案")
        if self.failed_or_infeasible_plan_count != len(self.plans) - completed:
            raise ValueError("失败或不可行方案计数不一致")
        plan_ids = [item.destination_id for item in self.plans]
        if len(plan_ids) != len(set(plan_ids)):
            raise ValueError("双方案不得包含重复 destination_id")
        return self
