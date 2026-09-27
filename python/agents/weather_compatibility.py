"""Central, configurable weather compatibility rules for activity planning."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from models.schemas import (
    Activity,
    ActivityEnvironment,
    DailyWeather,
    WeatherAvailability,
    WeatherCompatibilityEvaluation,
    WeatherCondition,
    WeatherSensitivity,
)


class WeatherCompatibilityPolicy(BaseModel):
    """Deterministic thresholds; injectable in tests and future configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_version: str = Field(default="weather-compatibility-v1", min_length=1)
    soft_rain_probability: int = Field(default=30, ge=0, le=100)
    hard_rain_probability: int = Field(default=70, ge=0, le=100)
    high_sensitivity_rain_limit: int = Field(default=40, ge=0, le=100)
    score_weight: float = Field(default=0.5, ge=0, le=2)

    @model_validator(mode="after")
    def validate_threshold_order(self) -> "WeatherCompatibilityPolicy":
        if self.soft_rain_probability > self.hard_rain_probability:
            raise ValueError("soft rain threshold cannot exceed hard rain threshold")
        if self.high_sensitivity_rain_limit > self.hard_rain_probability:
            raise ValueError(
                "high-sensitivity rain limit cannot exceed hard rain threshold"
            )
        return self


class WeatherCompatibilityEvaluator:
    """Evaluate hard eligibility and a 0-10 weather score with a reason."""

    def __init__(self, policy: WeatherCompatibilityPolicy | None = None) -> None:
        self.policy = policy or WeatherCompatibilityPolicy()

    def evaluate(
        self,
        activity: Activity,
        weather: DailyWeather | None,
        *,
        activity_date: str,
    ) -> WeatherCompatibilityEvaluation:
        if weather is None or weather.availability_status == WeatherAvailability.UNAVAILABLE:
            return self._result(
                activity,
                activity_date,
                weather,
                eligible=True,
                score=0,
                sufficient=False,
                reason="天气数据不可用，采用基础规划；未验证该活动的天气适配性。",
            )

        assert weather.weather_condition is not None
        assert weather.rain_probability is not None
        metadata_known = (
            activity.environment != ActivityEnvironment.UNKNOWN
            and activity.weather_sensitivity != WeatherSensitivity.UNKNOWN
        )
        severe = bool(weather.severe_weather) or weather.weather_condition in {
            WeatherCondition.HEAVY_RAIN,
            WeatherCondition.THUNDERSTORM,
            WeatherCondition.SNOW,
        }
        hard_rain = weather.rain_probability >= self.policy.hard_rain_probability

        if not metadata_known:
            if severe or hard_rain:
                return self._result(
                    activity,
                    activity_date,
                    weather,
                    eligible=False,
                    score=0,
                    sufficient=False,
                    reason="恶劣或高降雨风险下，活动室内外/敏感度未知，无法验证安全适配。",
                )
            return self._result(
                activity,
                activity_date,
                weather,
                eligible=True,
                score=0,
                sufficient=False,
                reason="天气风险较低，但活动天气属性未知；不标记为已验证适配。",
            )

        environment = activity.environment
        sensitivity = activity.weather_sensitivity

        if severe:
            if environment == ActivityEnvironment.INDOOR:
                return self._result(
                    activity, activity_date, weather, True, 10, True,
                    "恶劣天气下选择低暴露室内活动。",
                )
            if (
                environment == ActivityEnvironment.MIXED
                and sensitivity in {WeatherSensitivity.NONE, WeatherSensitivity.LOW}
            ):
                return self._result(
                    activity, activity_date, weather, True, 5, True,
                    "恶劣天气下混合场地仍可使用，但适配分降低。",
                )
            return self._result(
                activity, activity_date, weather, False, 0, True,
                "恶劣天气与室外或高敏感活动冲突，按硬约束排除。",
            )

        if hard_rain or weather.weather_condition == WeatherCondition.RAIN:
            if environment == ActivityEnvironment.INDOOR:
                return self._result(
                    activity, activity_date, weather, True, 10, True,
                    "降雨条件下室内活动天气适配良好。",
                )
            if sensitivity in {WeatherSensitivity.HIGH, WeatherSensitivity.MEDIUM} and (
                hard_rain
                or weather.rain_probability >= self.policy.high_sensitivity_rain_limit
            ):
                return self._result(
                    activity, activity_date, weather, False, 0, True,
                    "降雨概率达到阈值，天气敏感的非室内活动按硬约束排除。",
                )
            score = 6 if environment == ActivityEnvironment.MIXED else 4
            return self._result(
                activity, activity_date, weather, True, score, True,
                "活动对普通降雨敏感度较低，保留但降低天气适配分。",
            )

        if weather.rain_probability >= self.policy.soft_rain_probability:
            score = {
                ActivityEnvironment.INDOOR: 9,
                ActivityEnvironment.MIXED: 7,
                ActivityEnvironment.OUTDOOR: 6,
            }[environment]
            return self._result(
                activity, activity_date, weather, True, score, True,
                "存在一般降雨风险，未触发硬过滤，按场地类型软评分。",
            )

        score = {
            ActivityEnvironment.OUTDOOR: 10,
            ActivityEnvironment.MIXED: 9,
            ActivityEnvironment.INDOOR: 7,
        }[environment]
        return self._result(
            activity, activity_date, weather, True, score, True,
            "低降雨风险，按室外/混合/室内场地给予确定性适配分。",
        )

    def _result(
        self,
        activity: Activity,
        activity_date: str,
        weather: DailyWeather | None,
        eligible: bool,
        score: float,
        sufficient: bool,
        reason: str,
    ) -> WeatherCompatibilityEvaluation:
        return WeatherCompatibilityEvaluation(
            date=activity_date,
            time_slot=activity.time_slot,
            candidate_id=activity.candidate_id,
            policy_version=self.policy.policy_version,
            weather_condition=(weather.weather_condition if weather else None),
            rain_probability=(weather.rain_probability if weather else None),
            hard_constraint_satisfied=eligible,
            weather_score=score,
            data_sufficient=sufficient,
            reason=reason,
        )
