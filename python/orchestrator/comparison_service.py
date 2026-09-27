"""Application service joining C1 planning and C2 read-only evaluation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from evaluation import PlanComparator
from models.multi_plan import MultiPlanConfig, MultiPlanRequest, MultiPlanResult
from models.plan_evaluation import PlanComparisonResult, ScoringPolicy
from models.schemas import UserPreferences
from orchestrator.comparison import ComparisonOrchestrator


SENSITIVITY_POLICY = ScoringPolicy(
    policy_version="plan-scoring-sensitivity-v1",
    budget_weight=0.35,
    interest_weight=0.25,
    pace_weight=0.25,
    weather_weight=0.15,
)


@dataclass(frozen=True)
class ComparisonApplicationResult:
    """Owned outputs from one planning run and two read-only evaluations."""

    multi_plan: MultiPlanResult
    comparison: PlanComparisonResult
    sensitivity_comparison: PlanComparisonResult


class PlanComparisonApplicationService:
    """Single backend path used by structured and confirmed-draft APIs."""

    def __init__(
        self,
        *,
        orchestrator_factory: Callable[[], ComparisonOrchestrator] | None = None,
        comparator: PlanComparator | None = None,
        sensitivity_comparator: PlanComparator | None = None,
    ) -> None:
        self._orchestrator_factory = orchestrator_factory or ComparisonOrchestrator
        self._comparator = comparator or PlanComparator()
        self._sensitivity_comparator = sensitivity_comparator or PlanComparator(
            SENSITIVITY_POLICY
        )

    async def compare(
        self,
        preferences: UserPreferences,
        config: MultiPlanConfig | None = None,
    ) -> ComparisonApplicationResult:
        if not isinstance(preferences, UserPreferences):
            raise TypeError("comparison service requires validated UserPreferences")
        request = MultiPlanRequest(
            preferences=preferences.model_copy(deep=True),
            config=(config or MultiPlanConfig()).model_copy(deep=True),
        )
        multi_plan = await self._orchestrator_factory().run(request)
        comparison = self._comparator.compare(multi_plan)
        sensitivity = self._sensitivity_comparator.compare(multi_plan)
        return ComparisonApplicationResult(
            multi_plan=multi_plan,
            comparison=comparison,
            sensitivity_comparison=sensitivity,
        )
