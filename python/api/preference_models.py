"""HTTP contracts for stateless preference parsing and clarification."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from models.schemas import TravelPlanState, UserPreferences
from preferences import (
    ClarificationQuestion,
    ClarificationResult,
    PreferenceField,
    PreferencesDraft,
)


class PreferenceParserConfig(BaseModel):
    """Frozen public options for the deterministic parser used in this stage."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["mock"] = "mock"
    locale: Literal["zh-CN"] = "zh-CN"
    version: Literal["mock-preference-parser-v1"] = "mock-preference-parser-v1"


class PreferenceParseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    text: str = Field(min_length=1, max_length=4_000)
    reference_date: date
    config: PreferenceParserConfig = Field(default_factory=PreferenceParserConfig)

    @field_validator("text")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value:
            raise ValueError("用户输入不能为空")
        return value


class PreferenceClarifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    draft: PreferencesDraft
    draft_receipt: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    answer: str = Field(min_length=1, max_length=4_000)
    reference_date: date
    config: PreferenceParserConfig = Field(default_factory=PreferenceParserConfig)

    @field_validator("answer")
    @classmethod
    def reject_blank_answer(cls, value: str) -> str:
        if not value:
            raise ValueError("补充回答不能为空")
        return value


class PreferencePlanRequest(BaseModel):
    """A signed, complete draft plus an explicit client confirmation action."""

    model_config = ConfigDict(extra="forbid")

    draft: PreferencesDraft
    draft_receipt: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    confirmed: bool
    reference_date: date
    config: PreferenceParserConfig = Field(default_factory=PreferenceParserConfig)


class PreferenceDateSummary(BaseModel):
    departure_date: str
    return_date: str
    activity_start_date: str
    activity_end_date: str
    hotel_nights: int = Field(ge=1)


class PreferenceApiResponse(BaseModel):
    """UI-oriented response while retaining the complete domain result."""

    draft: PreferencesDraft
    clarification_result: ClarificationResult
    missing_fields: list[PreferenceField] = Field(default_factory=list)
    questions: list[ClarificationQuestion] = Field(default_factory=list)
    is_complete: bool
    can_confirm: bool
    date_summary: PreferenceDateSummary | None = None
    pipeline_preferences_preview: UserPreferences | None = None
    draft_only_fields: list[PreferenceField] = Field(default_factory=list)
    draft_receipt: str
    parser_config: PreferenceParserConfig
    is_mock: bool = True
    notice: str = "当前使用确定性 Mock Parser，并非真实 LLM。"


class ConfirmedPreferencePlanResponse(BaseModel):
    """Full planning result produced by the existing pipeline after confirmation."""

    confirmation_accepted: bool
    preferences: UserPreferences
    draft: PreferencesDraft
    draft_only_fields: list[PreferenceField] = Field(default_factory=list)
    plan: TravelPlanState
    is_mock: bool = True
    identity_notice: str = (
        "该确认只表示客户端提交了确认动作；当前没有身份认证，"
        "不能据此证明特定自然人的身份。"
    )
