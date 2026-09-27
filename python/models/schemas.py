"""
Pydantic 数据模型 —— 定义整个系统在 Agent 之间流转的数据结构。

设计原则:
  1. 每个 Agent 有明确的输入/输出 Schema
  2. 所有金额统一使用 float（单位: 人民币元）
  3. 日期统一使用 ISO 格式字符串 YYYY-MM-DD
"""

from __future__ import annotations

from datetime import date, timedelta
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


MAX_TRAVEL_BUDGET = 10_000_000
MAX_TRAVELERS = 20


def _validated_iso_date(value: str) -> str:
    if len(value) != 10 or value[4:5] != "-" or value[7:8] != "-":
        raise ValueError("日期必须采用 YYYY-MM-DD 格式")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("日期必须是有效的 YYYY-MM-DD 日期") from exc
    if parsed.isoformat() != value:
        raise ValueError("日期必须采用 YYYY-MM-DD 格式")
    return value


def _normalized_required_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name}不能为空")
    return normalized


# ━━━━━━━━━━━━━━━━━━ 枚举 ━━━━━━━━━━━━━━━━━━


class TravelStyle(str, Enum):  #限制旅行风格，而不是让用户随意发挥
    BUDGET = "budget"
    COMFORT = "comfort"
    LUXURY = "luxury"
    ADVENTURE = "adventure"
    CULTURAL = "cultural"
    RELAXATION = "relaxation"


class TravelPace(str, Enum):
    """Canonical trip-density preference used by the formal Pipeline."""

    RELAXED = "relaxed"
    BALANCED = "balanced"
    PACKED = "packed"
    # Source-compatible Python alias. Legacy JSON input "fast" is normalized
    # by the preference draft validator and API contract to canonical "packed".
    FAST = "packed"


class DataSource(str, Enum):
    MOCK = "mock"
    REAL = "real"


class WeatherScenario(str, Enum):
    """Deterministic scenarios supported by the current MockWeatherProvider."""

    GENERATED = "generated"
    SUNNY = "sunny"
    RAINY = "rainy"
    MIXED = "mixed"
    UNAVAILABLE = "unavailable"


class WeatherCondition(str, Enum):
    SUNNY = "sunny"
    CLOUDY = "cloudy"
    RAIN = "rain"
    HEAVY_RAIN = "heavy_rain"
    THUNDERSTORM = "thunderstorm"
    SNOW = "snow"


class WeatherAvailability(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class WeatherResultStatus(str, Enum):
    AVAILABLE = "available"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class WeatherPlanningStatus(str, Enum):
    NOT_STARTED = "not_started"
    READY = "ready"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    DEGRADED_TIMEOUT = "degraded_timeout"
    FAILED_DATA = "failed_data"
    FAILED_INTERNAL = "failed_internal"


class ActivityEnvironment(str, Enum):
    INDOOR = "indoor"
    OUTDOOR = "outdoor"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class WeatherSensitivity(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class ActivityIntensity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class ActivityAttributeStatus(str, Enum):
    AVAILABLE = "available"
    UNKNOWN = "unknown"


class ActivityPlanningMode(str, Enum):
    """Whether weather changes activity selection or is audit-only."""

    BASELINE = "baseline"
    WEATHER_AWARE = "weather_aware"


class WeatherDecisionType(str, Enum):
    BASELINE_SELECTION = "baseline_selection"
    WEATHER_RULE_SELECTION = "weather_rule_selection"
    WEATHER_DRIVEN_CHANGE = "weather_driven_change"
    WEATHER_UNAVAILABLE_FALLBACK = "weather_unavailable_fallback"
    NO_FEASIBLE_CANDIDATE = "no_feasible_candidate"


class PlanningState(str, Enum):
    COLLECTING_PREFERENCES = "collecting_preferences"
    RECOMMENDING_DESTINATIONS = "recommending_destinations"
    SEARCHING_PARALLEL = "searching_parallel"
    BUDGET_CHECKING = "budget_checking"
    ADJUSTING = "adjusting"
    COMPLETED = "completed"
    BUDGET_INFEASIBLE = "budget_infeasible"
    ACTIVITY_CONSTRAINTS_UNSATISFIED = "activity_constraints_unsatisfied"
    PACE_CONSTRAINTS_UNSATISFIED = "pace_constraints_unsatisfied"
    FAILED = "failed"


class BudgetComponent(str, Enum):
    ACTIVITIES = "activities"
    HOTEL = "hotel"
    FLIGHTS = "flights"


class FailureCode(str, Enum):
    INVALID_INPUT = "invalid_input"
    AGENT_EXECUTION_ERROR = "agent_execution_error"
    AGENT_TIMEOUT = "agent_timeout"
    MISSING_REQUIRED_RESULT = "missing_required_result"


class AgentFailure(BaseModel):
    """可从 Agent 一直传播到 API 的结构化故障信息。"""

    agent: str
    code: FailureCode
    error_type: str
    reason: str
    required: bool = True


# ━━━━━━━━━━━━━━━━━━ 用户偏好 ━━━━━━━━━━━━━━━━━━


class UserPreferences(BaseModel):
    budget: float = Field(
        ...,
        gt=0,
        le=MAX_TRAVEL_BUDGET,
        allow_inf_nan=False,
        strict=True,
        description="总预算（人民币元）",
    )
    travel_style: TravelStyle = Field(default=TravelStyle.COMFORT)
    pace: TravelPace | None = Field(
        default=None,
        description="可选旅行节奏；未提供时保持历史三时段行为",
    )
    departure_city: str = Field(..., min_length=1, max_length=100, description="出发城市")
    start_date: str = Field(..., description="出发日期 YYYY-MM-DD")
    end_date: str = Field(..., description="返回日期 YYYY-MM-DD")
    num_travelers: int = Field(default=1, ge=1, le=MAX_TRAVELERS, strict=True)
    interests: list[str] = Field(default_factory=list, description="兴趣标签")
    dietary_restrictions: list[str] = Field(default_factory=list)
    accessibility_needs: list[str] = Field(default_factory=list)
    notes: str = Field(default="", description="额外备注")

    @field_validator("pace", mode="before")
    @classmethod
    def normalize_legacy_pace(cls, value):
        return TravelPace.PACKED if value == "fast" else value

    @field_validator("departure_city")
    @classmethod
    def validate_departure_city(cls, value: str) -> str:
        return _normalized_required_text(value, "出发城市")

    @field_validator("start_date", "end_date")
    @classmethod
    def validate_iso_date(cls, value: str) -> str:
        return _validated_iso_date(value)

    @model_validator(mode="after")
    def validate_date_range(self) -> UserPreferences:
        if date.fromisoformat(self.end_date) <= date.fromisoformat(self.start_date):
            raise ValueError("结束日期必须晚于开始日期")
        return self


class FlightSearchRequest(BaseModel):
    """FlightProvider 的可验证查询契约。"""

    departure_city: str = Field(min_length=1, max_length=100)
    arrival_city: str = Field(min_length=1, max_length=100)
    travel_date: str
    cabin_class: str = Field(default="economy", pattern="^(economy|business|first)$")
    count: int = Field(default=5, ge=1, le=20, strict=True)

    @field_validator("departure_city", "arrival_city")
    @classmethod
    def validate_city(cls, value: str) -> str:
        return _normalized_required_text(value, "城市")

    @field_validator("travel_date")
    @classmethod
    def validate_travel_date(cls, value: str) -> str:
        return _validated_iso_date(value)


class HotelSearchRequest(BaseModel):
    """HotelProvider 的可验证查询契约。"""

    city: str = Field(min_length=1, max_length=100)
    check_in: str
    check_out: str
    travel_style: TravelStyle = TravelStyle.COMFORT

    @field_validator("city")
    @classmethod
    def validate_city(cls, value: str) -> str:
        return _normalized_required_text(value, "城市")

    @field_validator("check_in", "check_out")
    @classmethod
    def validate_stay_date(cls, value: str) -> str:
        return _validated_iso_date(value)

    @model_validator(mode="after")
    def validate_stay_range(self) -> HotelSearchRequest:
        if date.fromisoformat(self.check_out) <= date.fromisoformat(self.check_in):
            raise ValueError("退房日期必须晚于入住日期")
        return self


class ActivitySearchRequest(BaseModel):
    """ActivityProvider 的可验证查询契约；兴趣评分仍由 Agent 负责。"""

    city: str = Field(min_length=1, max_length=100)
    start_date: str
    end_date: str

    @field_validator("city")
    @classmethod
    def validate_city(cls, value: str) -> str:
        return _normalized_required_text(value, "城市")

    @field_validator("start_date", "end_date")
    @classmethod
    def validate_activity_date(cls, value: str) -> str:
        return _validated_iso_date(value)

    @model_validator(mode="after")
    def validate_activity_range(self) -> ActivitySearchRequest:
        if date.fromisoformat(self.end_date) <= date.fromisoformat(self.start_date):
            raise ValueError("结束日期必须晚于开始日期")
        return self


class WeatherQueryConfig(BaseModel):
    """Provider query options frozen for the Stage B Mock contract."""

    model_config = ConfigDict(extra="forbid")

    temperature_unit: Literal["celsius"] = "celsius"
    locale: Literal["zh-CN"] = "zh-CN"


class WeatherSearchRequest(BaseModel):
    """End-exclusive WeatherProvider query contract."""

    model_config = ConfigDict(extra="forbid")

    destination: str = Field(min_length=1, max_length=100)
    start_date: str
    end_date: str
    scenario: WeatherScenario = WeatherScenario.GENERATED
    config: WeatherQueryConfig = Field(default_factory=WeatherQueryConfig)

    @field_validator("destination")
    @classmethod
    def validate_destination(cls, value: str) -> str:
        return _normalized_required_text(value, "目的地")

    @field_validator("start_date", "end_date")
    @classmethod
    def validate_weather_date(cls, value: str) -> str:
        return _validated_iso_date(value)

    @model_validator(mode="after")
    def validate_weather_range(self) -> WeatherSearchRequest:
        if date.fromisoformat(self.end_date) <= date.fromisoformat(self.start_date):
            raise ValueError("结束日期必须晚于开始日期")
        return self


class DailyWeather(BaseModel):
    """Weather for one activity date, or an explicit unavailable marker."""

    model_config = ConfigDict(extra="forbid")

    date: str
    weather_condition: WeatherCondition | None = None
    rain_probability: int | None = Field(default=None, ge=0, le=100, strict=True)
    temperature: float | None = Field(default=None, ge=-80, le=60, allow_inf_nan=False)
    severe_weather: bool | None = None
    availability_status: WeatherAvailability

    @field_validator("date")
    @classmethod
    def validate_date(cls, value: str) -> str:
        return _validated_iso_date(value)

    @model_validator(mode="after")
    def validate_availability_fields(self) -> DailyWeather:
        weather_values = (
            self.weather_condition,
            self.rain_probability,
            self.temperature,
            self.severe_weather,
        )
        if self.availability_status == WeatherAvailability.AVAILABLE:
            if any(value is None for value in weather_values):
                raise ValueError("可用天气必须包含天气、降雨概率、温度和恶劣天气标记")
        elif any(value is not None for value in weather_values):
            raise ValueError("不可用天气不得伪造天气、降雨概率、温度或恶劣天气标记")
        return self


class WeatherResult(BaseModel):
    """Validated, source-labelled daily coverage for an end-exclusive range."""

    model_config = ConfigDict(extra="forbid")

    destination: str = Field(min_length=1, max_length=100)
    start_date: str
    end_date: str
    scenario: WeatherScenario
    daily_weather: list[DailyWeather]
    source: DataSource
    source_version: str = Field(min_length=1, max_length=100)
    is_mock: bool
    availability_status: WeatherResultStatus
    availability_note: str = Field(min_length=1, max_length=500)

    @field_validator("destination", "source_version", "availability_note")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        return _normalized_required_text(value, "天气结果文本字段")

    @field_validator("start_date", "end_date")
    @classmethod
    def validate_result_date(cls, value: str) -> str:
        return _validated_iso_date(value)

    @model_validator(mode="after")
    def validate_result_contract(self) -> WeatherResult:
        start = date.fromisoformat(self.start_date)
        end = date.fromisoformat(self.end_date)
        if end <= start:
            raise ValueError("结束日期必须晚于开始日期")

        expected_dates = [
            (start + timedelta(days=offset)).isoformat()
            for offset in range((end - start).days)
        ]
        actual_dates = [item.date for item in self.daily_weather]
        if actual_dates != expected_dates:
            raise ValueError("每日天气必须按顺序完整覆盖结束日期排除的活动日期")

        available_count = sum(
            item.availability_status == WeatherAvailability.AVAILABLE
            for item in self.daily_weather
        )
        expected_status = (
            WeatherResultStatus.AVAILABLE
            if available_count == len(self.daily_weather)
            else WeatherResultStatus.UNAVAILABLE
            if available_count == 0
            else WeatherResultStatus.PARTIAL
        )
        if self.availability_status != expected_status:
            raise ValueError("天气结果状态必须与每日数据可用性一致")
        if self.source == DataSource.MOCK and not self.is_mock:
            raise ValueError("Mock 来源必须明确标记 is_mock=true")
        if self.source == DataSource.REAL and self.is_mock:
            raise ValueError("Real 来源不得标记为 Mock")
        return self


class WeatherPlanningMetadata(BaseModel):
    """WeatherAgent outcome and explicit fallback/failure classification."""

    status: WeatherPlanningStatus = WeatherPlanningStatus.NOT_STARTED
    fallback_used: bool = False
    issue_code: str = ""
    reason: str = ""


class WeatherCompatibilityEvaluation(BaseModel):
    """Traceable rule evaluation for one candidate on one activity date."""

    date: str
    time_slot: str
    candidate_id: str
    policy_version: str = Field(min_length=1, max_length=100)
    weather_condition: WeatherCondition | None = None
    rain_probability: int | None = Field(default=None, ge=0, le=100)
    hard_constraint_satisfied: bool
    weather_score: float = Field(ge=0, le=10)
    data_sufficient: bool
    reason: str
    activity_name: str = ""
    activity_rating: float = Field(default=0, ge=0, le=10)
    interest_score: float = Field(default=0, ge=0)
    weighted_weather_score: float = Field(default=0, ge=0)
    total_selection_score: float = Field(default=0, ge=0)
    selected: bool = False
    selection_outcome: str = "not_ranked"

    @field_validator("date")
    @classmethod
    def validate_evaluation_date(cls, value: str) -> str:
        return _validated_iso_date(value)


class ActivityConstraintIssue(BaseModel):
    date: str
    time_slot: str
    code: str
    reason: str

    @field_validator("date")
    @classmethod
    def validate_issue_date(cls, value: str) -> str:
        return _validated_iso_date(value)


class ActivityWeatherDecision(BaseModel):
    """Actual activity decision plus its same-candidate baseline comparison."""

    date: str
    time_slot: str
    planning_mode: ActivityPlanningMode
    decision_type: WeatherDecisionType
    weather_data_available: bool
    baseline_candidate_id: str = ""
    baseline_candidate_name: str = ""
    weather_candidate_id: str = ""
    weather_candidate_name: str = ""
    initial_selected_candidate_id: str = ""
    initial_selected_candidate_name: str = ""
    final_candidate_id: str = ""
    final_candidate_name: str = ""
    weather_driven_change: bool = False
    hard_excluded_candidate_ids: list[str] = Field(default_factory=list)
    unknown_candidate_ids: list[str] = Field(default_factory=list)
    reason: str
    budget_adjusted: bool = False
    budget_reason: str = ""

    @field_validator("date")
    @classmethod
    def validate_decision_date(cls, value: str) -> str:
        return _validated_iso_date(value)


class PaceCompatibilityEvaluation(BaseModel):
    """Traceable pace eligibility and ranking evidence for one candidate."""

    date: str
    time_slot: str
    candidate_id: str
    policy_version: str = Field(min_length=1, max_length=100)
    requested_pace: TravelPace | None = None
    hard_constraint_satisfied: bool
    pace_score: float = Field(default=0, ge=0, le=10)
    data_sufficient: bool
    duration_hours: float | None = Field(default=None, ge=0)
    intensity: ActivityIntensity = ActivityIntensity.UNKNOWN
    reason: str
    selected: bool = False
    selection_outcome: str = "not_ranked"

    @field_validator("date")
    @classmethod
    def validate_pace_evaluation_date(cls, value: str) -> str:
        return _validated_iso_date(value)


class PaceSlotDecision(BaseModel):
    """Actual activity/rest outcome for one date and time slot."""

    date: str
    time_slot: str
    requested_pace: TravelPace | None = None
    outcome: Literal["activity", "rest", "no_feasible_activity"]
    candidate_id: str = ""
    candidate_name: str = ""
    reason: str

    @field_validator("date")
    @classmethod
    def validate_pace_decision_date(cls, value: str) -> str:
        return _validated_iso_date(value)


# ━━━━━━━━━━━━━━━━━━ 目的地 ━━━━━━━━━━━━━━━━━━


class Destination(BaseModel):
    city: str
    country: str
    description: str = ""
    best_season: str = ""
    visa_required: bool = False
    safety_score: float = Field(default=8.0, ge=0, le=10)
    cost_level: str = Field(default="medium", description="low / medium / high")
    highlights: list[str] = Field(default_factory=list)


class DestinationRecommendation(BaseModel):
    destinations: list[Destination]
    selected: Optional[Destination] = None  #说明可能有目的地，也可能暂时没有
    reasoning: str = ""


# ━━━━━━━━━━━━━━━━━━ 航班 ━━━━━━━━━━━━━━━━━━


class Flight(BaseModel):
    candidate_id: str = ""
    source: DataSource = DataSource.MOCK
    source_version: str = ""
    airline: str
    flight_no: str
    departure_city: str
    arrival_city: str
    departure_time: str
    arrival_time: str
    price: float = Field(ge=0)
    duration_hours: float = Field(ge=0)
    stops: int = Field(default=0, ge=0)
    cabin_class: str = Field(default="economy")


class FlightSearchResult(BaseModel):
    outbound_flights: list[Flight] = Field(default_factory=list)
    return_flights: list[Flight] = Field(default_factory=list)
    recommended_outbound: Optional[Flight] = None
    recommended_return: Optional[Flight] = None
    total_flight_cost: float = 0.0


# ━━━━━━━━━━━━━━━━━━ 酒店 ━━━━━━━━━━━━━━━━━━


class Hotel(BaseModel):
    candidate_id: str = ""
    source: DataSource = DataSource.MOCK
    source_version: str = ""
    name: str
    city: str
    check_in: str = ""
    check_out: str = ""
    address: str = ""
    star_rating: float = Field(default=3.0, ge=1, le=5)
    user_rating: float = Field(default=8.0, ge=0, le=10)
    price_per_night: float = Field(ge=0)
    amenities: list[str] = Field(default_factory=list)
    distance_to_center_km: float = Field(default=0.0, ge=0)


class HotelSearchResult(BaseModel):
    hotels: list[Hotel] = Field(default_factory=list)
    recommended: Optional[Hotel] = None
    total_nights: int = 0
    total_hotel_cost: float = 0.0


# ━━━━━━━━━━━━━━━━━━ 活动 ━━━━━━━━━━━━━━━━━━


class Activity(BaseModel):
    candidate_id: str = ""
    source: DataSource = DataSource.MOCK
    source_version: str = ""
    name: str
    category: str = Field(default="sightseeing", description="sightseeing / food / experience / transport")
    location: str = ""
    available_start_date: str = ""
    available_end_date: str = ""
    duration_hours: float = Field(default=2.0, ge=0)
    duration_status: ActivityAttributeStatus = ActivityAttributeStatus.UNKNOWN
    intensity: ActivityIntensity = ActivityIntensity.UNKNOWN
    intensity_status: ActivityAttributeStatus = ActivityAttributeStatus.UNKNOWN
    pace_attribute_source: DataSource | None = None
    price: float = Field(default=0.0, ge=0)
    rating: float = Field(default=8.0, ge=0, le=10)
    description: str = ""
    time_slot: str = Field(default="", description="morning / afternoon / evening")
    environment: ActivityEnvironment = ActivityEnvironment.UNKNOWN
    weather_sensitivity: WeatherSensitivity = WeatherSensitivity.UNKNOWN
    weather_compatible: Optional[bool] = None
    recommendation_reason: str = ""
    pace_compatible: Optional[bool] = None
    pace_recommendation_reason: str = ""


class RestPeriod(BaseModel):
    """An explicit no-cost rest outcome, never a fabricated Activity candidate."""

    time_slot: str
    reason: str


class DayPlan(BaseModel):
    date: str
    activities: list[Activity] = Field(default_factory=list)
    rest_slots: list[RestPeriod] = Field(default_factory=list)
    day_cost: float = 0.0


class ActivitySearchResult(BaseModel):
    activity_candidates: list[Activity] = Field(default_factory=list)
    day_plans: list[DayPlan] = Field(default_factory=list)
    total_activity_cost: float = 0.0
    weather_evaluations: list[WeatherCompatibilityEvaluation] = Field(default_factory=list)
    weather_decisions: list[ActivityWeatherDecision] = Field(default_factory=list)
    planning_mode: ActivityPlanningMode = ActivityPlanningMode.WEATHER_AWARE
    weather_optimized: bool = False
    weather_fallback_reason: str = ""
    constraint_issues: list[ActivityConstraintIssue] = Field(default_factory=list)
    requested_pace: TravelPace | None = None
    pace_policy_version: str = ""
    pace_evaluations: list[PaceCompatibilityEvaluation] = Field(default_factory=list)
    pace_decisions: list[PaceSlotDecision] = Field(default_factory=list)


# ━━━━━━━━━━━━━━━━━━ 预算 ━━━━━━━━━━━━━━━━━━


class BudgetBreakdown(BaseModel):
    flight_cost: float = 0.0
    hotel_cost: float = 0.0
    activity_cost: float = 0.0
    total_cost: float = 0.0
    budget: float = 0.0
    remaining: float = 0.0
    is_within_budget: bool = True
    over_budget_amount: float = 0.0
    suggestions: list[str] = Field(default_factory=list)


class CandidateSnapshot(BaseModel):
    """One immutable-by-contract copy of all candidates used by budget optimization."""

    outbound_flights: tuple[Flight, ...] = ()
    return_flights: tuple[Flight, ...] = ()
    hotels: tuple[Hotel, ...] = ()
    activities: tuple[Activity, ...] = ()


class ActivitySelection(BaseModel):
    date: str
    time_slot: str
    candidate_id: str


class InitialPlanSelection(BaseModel):
    outbound_flight_id: str
    return_flight_id: str
    hotel_id: str
    activities: list[ActivitySelection] = Field(default_factory=list)
    flight_cost: float = Field(ge=0)
    hotel_cost: float = Field(ge=0)
    activity_cost: float = Field(ge=0)
    total_cost: float = Field(ge=0)


class ActivityReplacement(BaseModel):
    date: str
    time_slot: str
    before_candidate_id: str
    after_candidate_id: str
    before_cost: float = Field(ge=0)
    after_cost: float = Field(ge=0)
    selection_reason: str = ""


class BudgetAdjustment(BaseModel):
    round_number: int = Field(ge=1)
    target: BudgetComponent
    policy_version: str
    before_candidate_ids: list[str] = Field(default_factory=list)
    after_candidate_ids: list[str] = Field(default_factory=list)
    before_cost: float = Field(ge=0)
    after_cost: float = Field(ge=0)
    saved_amount: float = Field(ge=0)
    current_total: float = Field(ge=0)
    budget_remaining: float
    improved: bool
    activity_replacements: list[ActivityReplacement] = Field(default_factory=list)


# ━━━━━━━━━━━━━━━━━━ 全局状态 ━━━━━━━━━━━━━━━━━━


class TravelPlanState(BaseModel):
    """Pipeline 中在 Agent 之间流转的全局状态对象。"""

    state: PlanningState = PlanningState.COLLECTING_PREFERENCES
    preferences: Optional[UserPreferences] = None
    destination_rec: Optional[DestinationRecommendation] = None
    flight_result: Optional[FlightSearchResult] = None
    hotel_result: Optional[HotelSearchResult] = None
    weather_result: Optional[WeatherResult] = None
    weather_planning: WeatherPlanningMetadata = Field(
        default_factory=WeatherPlanningMetadata
    )
    activity_result: Optional[ActivitySearchResult] = None
    budget_breakdown: Optional[BudgetBreakdown] = None
    candidate_snapshot: Optional[CandidateSnapshot] = None
    initial_selection: Optional[InitialPlanSelection] = None
    adjustment_history: list[BudgetAdjustment] = Field(default_factory=list)
    initial_total_cost: Optional[float] = None
    final_total_cost: Optional[float] = None
    status_message: str = ""
    adjustment_round: int = 0
    max_adjustments: int = 3
    agent_failures: list[AgentFailure] = Field(default_factory=list)
    error_messages: list[str] = Field(default_factory=list)

    @property
    def selected_destination(self) -> Optional[Destination]:
        if self.destination_rec and self.destination_rec.selected:
            return self.destination_rec.selected
        return None
