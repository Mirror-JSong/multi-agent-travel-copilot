"""Central, configurable travel-pace rules for activity planning."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from models.schemas import (
    Activity,
    ActivityAttributeStatus,
    ActivityIntensity,
    PaceCompatibilityEvaluation,
    TravelPace,
)


class PacePlanningPolicy(BaseModel):
    """Deterministic capacity and intensity rules; injectable in tests."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str = Field(default="pace-policy-v1", min_length=1)
    relaxed_min_activities_per_day: int = Field(default=1, ge=1, le=3)
    relaxed_max_activities_per_day: int = Field(default=2, ge=1, le=3)
    relaxed_max_daily_duration_hours: float = Field(default=5.0, gt=0, le=24)
    relaxed_max_single_duration_hours: float = Field(default=3.0, gt=0, le=12)
    relaxed_low_intensity_score: float = Field(default=3.0, ge=0, le=10)
    relaxed_medium_intensity_score: float = Field(default=1.0, ge=0, le=10)
    packed_high_intensity_score: float = Field(default=3.0, ge=0, le=10)
    packed_medium_intensity_score: float = Field(default=2.0, ge=0, le=10)
    packed_low_intensity_score: float = Field(default=1.0, ge=0, le=10)

    @model_validator(mode="after")
    def validate_capacity(self) -> "PacePlanningPolicy":
        if self.relaxed_min_activities_per_day > self.relaxed_max_activities_per_day:
            raise ValueError("relaxed minimum activities cannot exceed maximum")
        return self


class PaceCompatibilityEvaluator:
    """Evaluate pace hard constraints and ranking evidence for one activity."""

    def __init__(self, policy: PacePlanningPolicy | None = None) -> None:
        self.policy = policy or PacePlanningPolicy()

    def evaluate(
        self,
        activity: Activity,
        pace: TravelPace | None,
        *,
        activity_date: str,
        projected_daily_duration: float = 0.0,
    ) -> PaceCompatibilityEvaluation:
        duration_known = (
            activity.duration_status == ActivityAttributeStatus.AVAILABLE
        )
        intensity_known = (
            activity.intensity_status == ActivityAttributeStatus.AVAILABLE
            and activity.intensity != ActivityIntensity.UNKNOWN
        )
        data_sufficient = duration_known and intensity_known
        duration = activity.duration_hours if duration_known else None

        if pace is None:
            return self._result(
                activity,
                pace,
                activity_date,
                True,
                0,
                data_sufficient,
                duration,
                "用户未指定旅行节奏，保持历史三时段选择规则。",
            )

        if pace == TravelPace.BALANCED:
            return self._result(
                activity,
                pace,
                activity_date,
                True,
                0,
                data_sufficient,
                duration,
                "balanced 保留原有三时段活动选择，不额外过滤强度。",
            )

        if pace == TravelPace.RELAXED:
            if not data_sufficient:
                return self._result(
                    activity,
                    pace,
                    activity_date,
                    False,
                    0,
                    False,
                    duration,
                    "relaxed 需要明确时长和强度；未知属性不能默认视为低强度。",
                )
            if activity.intensity == ActivityIntensity.HIGH:
                return self._result(
                    activity,
                    pace,
                    activity_date,
                    False,
                    0,
                    True,
                    duration,
                    "高强度活动不符合 relaxed 的硬约束。",
                )
            if activity.duration_hours > self.policy.relaxed_max_single_duration_hours:
                return self._result(
                    activity,
                    pace,
                    activity_date,
                    False,
                    0,
                    True,
                    duration,
                    "单项活动时长超过 relaxed 上限。",
                )
            if projected_daily_duration > self.policy.relaxed_max_daily_duration_hours:
                return self._result(
                    activity,
                    pace,
                    activity_date,
                    False,
                    0,
                    True,
                    duration,
                    "加入该活动后每日总时长超过 relaxed 上限。",
                )
            score = (
                self.policy.relaxed_low_intensity_score
                if activity.intensity == ActivityIntensity.LOW
                else self.policy.relaxed_medium_intensity_score
            )
            return self._result(
                activity,
                pace,
                activity_date,
                True,
                score,
                True,
                duration,
                "活动强度和预计时长符合 relaxed 规则。",
            )

        score = {
            ActivityIntensity.HIGH: self.policy.packed_high_intensity_score,
            ActivityIntensity.MEDIUM: self.policy.packed_medium_intensity_score,
            ActivityIntensity.LOW: self.policy.packed_low_intensity_score,
            ActivityIntensity.UNKNOWN: 0.0,
        }[activity.intensity]
        reason = (
            "packed 在现有三时段容量内允许并优先较高强度活动。"
            if data_sufficient
            else "packed 保留属性未知候选，但不宣称其强度已验证。"
        )
        return self._result(
            activity,
            pace,
            activity_date,
            True,
            score,
            data_sufficient,
            duration,
            reason,
        )

    def _result(
        self,
        activity: Activity,
        pace: TravelPace | None,
        activity_date: str,
        eligible: bool,
        score: float,
        sufficient: bool,
        duration: float | None,
        reason: str,
    ) -> PaceCompatibilityEvaluation:
        return PaceCompatibilityEvaluation(
            date=activity_date,
            time_slot=activity.time_slot,
            candidate_id=activity.candidate_id,
            policy_version=self.policy.policy_version,
            requested_pace=pace,
            hard_constraint_satisfied=eligible,
            pace_score=score,
            data_sufficient=sufficient,
            duration_hours=duration,
            intensity=activity.intensity,
            reason=reason,
        )
