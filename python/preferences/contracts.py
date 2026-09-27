"""Data contracts for incomplete, source-aware preference collection."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from models.schemas import TravelPace, TravelStyle

Pace = TravelPace


class PreferenceField(str, Enum):
    DEPARTURE_CITY = "departure_city"
    BUDGET = "budget"
    START_DATE = "start_date"
    END_DATE = "end_date"
    DURATION_DAYS = "duration_days"
    NUM_TRAVELERS = "num_travelers"
    TRAVEL_STYLE = "travel_style"
    PACE = "pace"
    INTERESTS = "interests"


class PreferenceValueSource(str, Enum):
    EXPLICIT = "explicit"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


TRACKED_PREFERENCE_FIELDS = tuple(PreferenceField)


class PreferencesDraft(BaseModel):
    """Incomplete preference values plus provenance; no business defaults are applied."""

    model_config = ConfigDict(extra="forbid")

    raw_text: str = ""
    departure_city: str | None = None
    budget: float | None = None
    start_date: str | None = None
    end_date: str | None = None
    duration_days: int | None = None
    num_travelers: int | None = None
    travel_style: TravelStyle | None = None
    pace: Pace | None = None
    interests: list[str] | None = None
    field_sources: dict[PreferenceField, PreferenceValueSource] = Field(default_factory=dict)
    field_errors: dict[PreferenceField, str] = Field(default_factory=dict)
    ambiguous_fields: list[PreferenceField] = Field(default_factory=list)
    revision: int = Field(default=0, ge=0)

    @field_validator("pace", mode="before")
    @classmethod
    def normalize_legacy_pace(cls, value):
        return TravelPace.PACKED if value == "fast" else value

    @model_validator(mode="after")
    def normalize_contract(self) -> PreferencesDraft:
        self.raw_text = self.raw_text.strip()
        if self.departure_city is not None:
            self.departure_city = self.departure_city.strip()
        if self.interests is not None:
            self.interests = list(dict.fromkeys(item.strip() for item in self.interests if item.strip()))

        sources = dict(self.field_sources)
        for field in TRACKED_PREFERENCE_FIELDS:
            if field not in sources:
                value = getattr(self, field.value)
                sources[field] = (
                    PreferenceValueSource.EXPLICIT
                    if value is not None
                    else PreferenceValueSource.UNKNOWN
                )
        self.field_sources = sources
        self.ambiguous_fields = list(dict.fromkeys(self.ambiguous_fields))
        return self

    def source_for(self, field: PreferenceField) -> PreferenceValueSource:
        return self.field_sources.get(field, PreferenceValueSource.UNKNOWN)


class ClarificationQuestion(BaseModel):
    field: PreferenceField
    prompt: str
    reason: str


class ClarificationResult(BaseModel):
    draft: PreferencesDraft
    missing_required_fields: list[PreferenceField] = Field(default_factory=list)
    questions: list[ClarificationQuestion] = Field(default_factory=list)
    validation_errors: dict[PreferenceField, str] = Field(default_factory=dict)
    is_complete: bool = False
