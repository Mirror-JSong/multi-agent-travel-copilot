"""Pydantic contracts for deterministic Stage C2 plan evaluation."""

from __future__ import annotations

from enum import Enum
from math import isclose

from pydantic import BaseModel, ConfigDict, Field, model_validator

from models.schemas import PlanningState


class MetricName(str, Enum):
    BUDGET_MATCH = "budget_match"
    INTEREST_MATCH = "interest_match"
    PACE_FIT = "pace_fit"
    WEATHER_SUITABILITY = "weather_suitability"


class MetricEvaluationStatus(str, Enum):
    EVALUATED = "evaluated"
    NOT_APPLICABLE = "not_applicable"
    PARTIAL = "partial"
    INSUFFICIENT_DATA = "insufficient_data"
    INELIGIBLE = "ineligible"


class PlanEvaluationEligibility(str, Enum):
    ELIGIBLE = "eligible"
    BUSINESS_INFEASIBLE = "business_infeasible"
    FAILED = "failed"
    INSUFFICIENT_DATA = "insufficient_data"


class PlanComparability(str, Enum):
    COMPARABLE = "comparable"
    SINGLE_FEASIBLE = "single_feasible"
    NOT_COMPARABLE = "not_comparable"


class RecommendationDecision(str, Enum):
    RECOMMENDED = "recommended"
    TIE = "tie"
    ONLY_FEASIBLE = "only_feasible"
    NO_RECOMMENDATION = "no_recommendation"


class MetricEvidence(BaseModel):
    """A stable reference to an input field or candidate used by a metric."""

    model_config = ConfigDict(extra="forbid")

    field_path: str = Field(min_length=1, max_length=300)
    value_json: str = Field(min_length=1)
    candidate_ids: list[str] = Field(default_factory=list)
    description: str = Field(default="", max_length=1000)


class MetricEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric_name: MetricName
    raw_value: float | None = Field(default=None, allow_inf_nan=False)
    normalized_score: float | None = Field(
        default=None,
        ge=0,
        le=100,
        allow_inf_nan=False,
    )
    numerator: float | None = Field(default=None, allow_inf_nan=False)
    denominator: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    evaluation_status: MetricEvaluationStatus
    configured_weight: float = Field(ge=0, le=1, allow_inf_nan=False)
    effective_weight: float = Field(default=0.0, ge=0, le=1, allow_inf_nan=False)
    evidence: list[MetricEvidence] = Field(default_factory=list)
    explanation: str = Field(min_length=1, max_length=2000)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_score_status(self) -> MetricEvaluation:
        if self.evaluation_status == MetricEvaluationStatus.EVALUATED:
            if self.normalized_score is None or self.raw_value is None:
                raise ValueError("evaluated metric requires raw_value and normalized_score")
            if self.denominator is None or self.denominator <= 0:
                raise ValueError("evaluated metric requires a positive denominator")
        elif self.normalized_score is not None:
            raise ValueError("non-evaluated metric must not expose a normalized score")
        return self


class EvaluationDataCompleteness(BaseModel):
    model_config = ConfigDict(extra="forbid")

    applicable_metric_count: int = Field(ge=0, le=4, strict=True)
    evaluated_metric_count: int = Field(ge=0, le=4, strict=True)
    completeness_ratio: float = Field(ge=0, le=1, allow_inf_nan=False)
    missing_metrics: list[MetricName] = Field(default_factory=list)
    not_applicable_metrics: list[MetricName] = Field(default_factory=list)
    source_versions: dict[str, list[str]] = Field(default_factory=dict)


class PlanEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destination_id: str = Field(min_length=1, max_length=100)
    destination_name: str = Field(min_length=1, max_length=100)
    planning_status: PlanningState
    evaluation_eligibility: PlanEvaluationEligibility
    metric_evaluations: list[MetricEvaluation]
    overall_score: float | None = Field(
        default=None,
        ge=0,
        le=100,
        allow_inf_nan=False,
    )
    scoring_policy_version: str = Field(min_length=1, max_length=100)
    data_completeness: EvaluationDataCompleteness
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_metric_set(self) -> PlanEvaluation:
        names = [item.metric_name for item in self.metric_evaluations]
        if len(names) != 4 or set(names) != set(MetricName):
            raise ValueError("PlanEvaluation must contain each of the four metrics once")
        if (
            self.evaluation_eligibility != PlanEvaluationEligibility.ELIGIBLE
            and self.overall_score is not None
        ):
            raise ValueError("ineligible plan cannot expose a normal overall score")
        return self


class MetricDifference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric_name: MetricName
    destination_a_id: str
    destination_b_id: str
    destination_a_raw_value: float | None = Field(default=None, allow_inf_nan=False)
    destination_b_raw_value: float | None = Field(default=None, allow_inf_nan=False)
    destination_a_score: float | None = Field(default=None, allow_inf_nan=False)
    destination_b_score: float | None = Field(default=None, allow_inf_nan=False)
    score_difference_a_minus_b: float | None = Field(default=None, allow_inf_nan=False)
    explanation: str = Field(min_length=1, max_length=2000)
    evidence: list[MetricEvidence] = Field(default_factory=list)


class ComparisonExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=2000)
    destination_ids: list[str] = Field(default_factory=list)
    evidence: list[MetricEvidence] = Field(default_factory=list)


class RecommendationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: RecommendationDecision
    recommended_destination_id: str | None = None
    tied_destination_ids: list[str] = Field(default_factory=list)
    score_difference: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    reason: str = Field(min_length=1, max_length=2000)
    evidence: list[MetricEvidence] = Field(default_factory=list)


class ScoringPolicy(BaseModel):
    """Central, versioned and injectable C2 scoring rules."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str = Field(default="plan-scoring-v1", min_length=1, max_length=100)
    budget_weight: float = Field(default=0.30, ge=0, le=1, allow_inf_nan=False)
    interest_weight: float = Field(default=0.30, ge=0, le=1, allow_inf_nan=False)
    pace_weight: float = Field(default=0.25, ge=0, le=1, allow_inf_nan=False)
    weather_weight: float = Field(default=0.15, ge=0, le=1, allow_inf_nan=False)
    budget_score_floor: float = Field(default=80.0, ge=0, le=100)
    budget_headroom_target_ratio: float = Field(default=0.20, gt=0, le=1)
    packed_capacity_weight: float = Field(default=0.70, ge=0, le=1)
    packed_high_affinity: float = Field(default=1.0, ge=0, le=1)
    packed_medium_affinity: float = Field(default=0.75, ge=0, le=1)
    packed_low_affinity: float = Field(default=0.50, ge=0, le=1)
    tie_threshold_points: float = Field(default=2.0, ge=0, le=20)
    score_precision: int = Field(default=4, ge=0, le=6, strict=True)

    @model_validator(mode="after")
    def validate_weights(self) -> ScoringPolicy:
        weights = (
            self.budget_weight,
            self.interest_weight,
            self.pace_weight,
            self.weather_weight,
        )
        if not isclose(sum(weights), 1.0, abs_tol=1e-9):
            raise ValueError("four scoring weights must sum to 1.0")
        return self

    def weights(self) -> dict[MetricName, float]:
        return {
            MetricName.BUDGET_MATCH: self.budget_weight,
            MetricName.INTEREST_MATCH: self.interest_weight,
            MetricName.PACE_FIT: self.pace_weight,
            MetricName.WEATHER_SUITABILITY: self.weather_weight,
        }


class PlanComparisonResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=100)
    comparison_id: str = Field(min_length=1, max_length=100)
    input_fingerprint: str = Field(min_length=1, max_length=100)
    plan_evaluations: list[PlanEvaluation]
    metric_differences: list[MetricDifference]
    comparability: PlanComparability
    common_evaluated_metrics: list[MetricName] = Field(default_factory=list)
    effective_weights: dict[MetricName, float] = Field(default_factory=dict)
    recommendation: RecommendationResult
    explanations: list[ComparisonExplanation] = Field(default_factory=list)
    scoring_policy: ScoringPolicy
    source_versions: dict[str, dict[str, list[str]]] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_comparison(self) -> PlanComparisonResult:
        ids = [item.destination_id for item in self.plan_evaluations]
        if len(ids) != len(set(ids)):
            raise ValueError("plan evaluations must reference different destinations")
        if self.comparability == PlanComparability.COMPARABLE:
            if len(self.plan_evaluations) != 2:
                raise ValueError("comparable result requires exactly two plans")
            if not isclose(sum(self.effective_weights.values()), 1.0, abs_tol=1e-9):
                raise ValueError("effective weights must sum to 1 for comparable plans")
            if any(item.overall_score is None for item in self.plan_evaluations):
                raise ValueError("comparable plans require overall scores")
        return self
