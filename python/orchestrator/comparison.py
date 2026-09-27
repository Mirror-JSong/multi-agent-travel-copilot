"""Stage C1 orchestration for two independent destination plans.

The orchestrator performs destination discovery once, creates a fresh Pipeline
for every selected destination and returns factual comparison-ready data.  It
does not rank the completed plans or recommend a winner.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from agents import (
    ActivityAgent,
    DestinationAgent,
    FlightAgent,
    HotelAgent,
    PreferenceAgent,
    WeatherAgent,
)
from agents.base_agent import BaseAgent
from models.multi_plan import (
    ActivityComparisonFact,
    DestinationCandidateSummary,
    DestinationPlan,
    MULTI_PLAN_CONTRACT_VERSION,
    MultiPlanConfig,
    MultiPlanExecutionMode,
    MultiPlanIssue,
    MultiPlanRequest,
    MultiPlanResult,
    PlanComparisonData,
)
from models.schemas import (
    ActivityAttributeStatus,
    AgentFailure,
    Destination,
    DestinationRecommendation,
    FailureCode,
    PlanningState,
    TravelPlanState,
)
from orchestrator.pipeline import TravelPlanningPipeline
from tools.activity_search import MockActivityProvider
from tools.deterministic import stable_candidate_id
from tools.flight_search import MockFlightProvider
from tools.hotel_search import MockHotelProvider
from tools.weather_provider import MockWeatherProvider


PipelineFactory = Callable[[Destination, MultiPlanConfig], TravelPlanningPipeline]

_TERMINAL_STATES = {
    PlanningState.COMPLETED,
    PlanningState.BUDGET_INFEASIBLE,
    PlanningState.ACTIVITY_CONSTRAINTS_UNSATISFIED,
    PlanningState.PACE_CONSTRAINTS_UNSATISFIED,
    PlanningState.FAILED,
}


def _default_pipeline_factory(
    destination: Destination,
    config: MultiPlanConfig,
) -> TravelPlanningPipeline:
    del destination
    activity_agent = ActivityAgent(
        provider=MockActivityProvider(config.mock_activity_data_version),
        require_weather_context=True,
        planning_mode=config.activity_planning_mode,
    )
    return TravelPlanningPipeline(
        flight_agent=FlightAgent(MockFlightProvider(config.mock_data_version)),
        hotel_agent=HotelAgent(MockHotelProvider(config.mock_data_version)),
        weather_agent=WeatherAgent(
            MockWeatherProvider(config.mock_data_version),
            scenario=config.weather_scenario,
        ),
        activity_agent=activity_agent,
        activity_planning_mode=config.activity_planning_mode,
    )


def _request_id(request: MultiPlanRequest) -> str:
    """Execution mode is intentionally excluded from the business request ID."""
    return stable_candidate_id(
        "multi-plan-request",
        {
            "preferences": request.preferences.model_dump(mode="json"),
            "plan_count": request.plan_count,
            "activity_planning_mode": request.config.activity_planning_mode.value,
            "weather_scenario": request.config.weather_scenario.value,
            "mock_data_version": request.config.mock_data_version,
            "mock_activity_data_version": request.config.mock_activity_data_version,
            "destination_data_version": request.config.destination_data_version,
        },
        MULTI_PLAN_CONTRACT_VERSION,
    )


def _destination_id(destination: Destination, config: MultiPlanConfig) -> str:
    return stable_candidate_id(
        "destination",
        {"city": destination.city.strip(), "country": destination.country.strip()},
        config.destination_data_version,
    )


def _matched_interests(activity, interests: list[str]) -> list[str]:
    haystack = f"{activity.name} {activity.category} {activity.description}".lower()
    return [item for item in interests if item.lower() in haystack]


def _source_versions(state: TravelPlanState) -> dict[str, list[str]]:
    versions: dict[str, set[str]] = {
        "flight": set(),
        "hotel": set(),
        "activity": set(),
        "weather": set(),
    }
    if state.candidate_snapshot is not None:
        versions["flight"].update(
            item.source_version
            for item in (
                *state.candidate_snapshot.outbound_flights,
                *state.candidate_snapshot.return_flights,
            )
            if item.source_version
        )
        versions["hotel"].update(
            item.source_version
            for item in state.candidate_snapshot.hotels
            if item.source_version
        )
        versions["activity"].update(
            item.source_version
            for item in state.candidate_snapshot.activities
            if item.source_version
        )
    else:
        if state.flight_result is not None:
            versions["flight"].update(
                item.source_version
                for item in (
                    *state.flight_result.outbound_flights,
                    *state.flight_result.return_flights,
                )
                if item.source_version
            )
        if state.hotel_result is not None:
            versions["hotel"].update(
                item.source_version
                for item in state.hotel_result.hotels
                if item.source_version
            )
        if state.activity_result is not None:
            versions["activity"].update(
                item.source_version
                for item in state.activity_result.activity_candidates
                if item.source_version
            )
    if state.weather_result is not None and state.weather_result.source_version:
        versions["weather"].add(state.weather_result.source_version)
    return {key: sorted(value) for key, value in versions.items()}


def build_comparison_data(state: TravelPlanState) -> PlanComparisonData:
    """Project one plan into traceable C2 inputs without computing a score."""
    preferences = state.preferences
    if preferences is None:
        raise ValueError("comparison data requires plan preferences")

    missing_data: list[str] = []
    if state.flight_result is None:
        missing_data.append("flight_result")
    if state.hotel_result is None:
        missing_data.append("hotel_result")
    if state.weather_result is None:
        missing_data.append("weather_result")
    if state.activity_result is None:
        missing_data.append("activity_result")
    if state.budget_breakdown is None:
        missing_data.append("budget_breakdown")
    if state.candidate_snapshot is None:
        missing_data.append("candidate_snapshot")

    facts: list[ActivityComparisonFact] = []
    daily_counts: dict[str, int] = {}
    if state.activity_result is not None:
        for day in state.activity_result.day_plans:
            daily_counts[day.date] = len(day.activities)
            facts.extend(
                ActivityComparisonFact(
                    date=day.date,
                    time_slot=activity.time_slot,
                    candidate_id=activity.candidate_id,
                    price_per_traveler=activity.price,
                    matched_interests=_matched_interests(
                        activity,
                        preferences.interests,
                    ),
                    weather_compatible=activity.weather_compatible,
                    pace_compatible=activity.pace_compatible,
                    intensity=activity.intensity,
                    intensity_status=activity.intensity_status,
                    duration_hours=(
                        activity.duration_hours
                        if activity.duration_status == ActivityAttributeStatus.AVAILABLE
                        else None
                    ),
                    duration_status=activity.duration_status,
                    reason=activity.recommendation_reason,
                )
                for activity in day.activities
            )
            facts.extend(
                ActivityComparisonFact(
                    date=day.date,
                    time_slot=rest.time_slot,
                    is_rest=True,
                    reason=rest.reason,
                )
                for rest in day.rest_slots
            )

    breakdown = state.budget_breakdown
    return PlanComparisonData(
        budget=preferences.budget,
        flight_cost=breakdown.flight_cost if breakdown else None,
        hotel_cost=breakdown.hotel_cost if breakdown else None,
        activity_cost=breakdown.activity_cost if breakdown else None,
        final_total_cost=(
            breakdown.total_cost
            if breakdown is not None
            else state.final_total_cost
        ),
        within_budget=breakdown.is_within_budget if breakdown else None,
        adjustment_round=state.adjustment_round,
        adjustment_count=len(state.adjustment_history),
        daily_activity_counts=daily_counts,
        activity_facts=facts,
        source_versions=_source_versions(state),
        candidate_snapshot_available=state.candidate_snapshot is not None,
        missing_data=missing_data,
    )


class ComparisonOrchestrator:
    """Discover destinations once and independently plan up to two of them."""

    def __init__(
        self,
        *,
        destination_agent: BaseAgent | None = None,
        pipeline_factory: PipelineFactory | None = None,
        max_concurrency: int = 2,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")
        self.preference_agent = PreferenceAgent()
        self.destination_agent = destination_agent or DestinationAgent()
        self.pipeline_factory = pipeline_factory or _default_pipeline_factory
        self.max_concurrency = min(max_concurrency, 2)

    async def run(self, request: MultiPlanRequest) -> MultiPlanResult:
        if not isinstance(request, MultiPlanRequest):
            raise TypeError("ComparisonOrchestrator requires a validated MultiPlanRequest")

        discovery = TravelPlanState(
            preferences=request.preferences.model_copy(deep=True)
        )
        discovery = await self.preference_agent.run(discovery)
        if discovery.state != PlanningState.FAILED:
            discovery = await self.destination_agent.run(discovery)

        if discovery.state == PlanningState.FAILED:
            return MultiPlanResult(
                request_id=_request_id(request),
                request_preferences=request.preferences.model_copy(deep=True),
                requested_plan_count=request.plan_count,
                selected_destination_count=0,
                generated_plan_count=0,
                successful_plan_count=0,
                failed_or_infeasible_plan_count=0,
                orchestration_completed=False,
                execution_mode=request.config.execution_mode,
                issues=[MultiPlanIssue(
                    code="destination_discovery_failed",
                    message=(
                        discovery.error_messages[-1]
                        if discovery.error_messages
                        else "目的地发现 Agent 执行失败。"
                    ),
                )],
            )

        candidates = (
            discovery.destination_rec.destinations
            if discovery.destination_rec is not None
            else []
        )
        unique: list[tuple[str, Destination]] = []
        seen: set[str] = set()
        issues: list[MultiPlanIssue] = []
        for candidate in candidates:
            if not candidate.city.strip() or not candidate.country.strip():
                issues.append(MultiPlanIssue(
                    code="invalid_destination_candidate",
                    message="目的地候选缺少非空城市或国家，已排除。",
                ))
                continue
            candidate_id = _destination_id(candidate, request.config)
            if candidate_id in seen:
                issues.append(MultiPlanIssue(
                    code="duplicate_destination_candidate",
                    destination_id=candidate_id,
                    message=f"重复目的地 {candidate.city} 已排除，未用于凑足方案数量。",
                ))
                continue
            seen.add(candidate_id)
            unique.append((candidate_id, candidate.model_copy(deep=True)))

        selected = unique[: request.plan_count]
        if not unique:
            issues.append(MultiPlanIssue(
                code="no_valid_destination_candidates",
                message="DestinationAgent 没有返回有效目的地，未编造候选方案。",
            ))
        elif len(selected) < request.plan_count:
            issues.append(MultiPlanIssue(
                code="insufficient_destination_candidates",
                message=(
                    f"仅有 {len(selected)} 个不同且有效的目的地；"
                    f"请求 {request.plan_count} 个，未编造缺失目的地。"
                ),
            ))

        selected_ids = {item[0] for item in selected}
        summaries = [
            DestinationCandidateSummary(
                destination_id=candidate_id,
                destination=destination.model_copy(deep=True),
                rank=rank,
                selected_for_planning=candidate_id in selected_ids,
                selection_reason=(
                    f"DestinationAgent 稳定排序第 {rank}；"
                    + (
                        "位于本次前 N 个不同有效候选中。"
                        if candidate_id in selected_ids
                        else "未进入本次请求的前 N 个候选。"
                    )
                ),
                source_version=request.config.destination_data_version,
            )
            for rank, (candidate_id, destination) in enumerate(unique, start=1)
        ]

        if request.config.execution_mode == MultiPlanExecutionMode.SERIAL:
            plans = [
                await self._run_destination(request, candidate_id, destination)
                for candidate_id, destination in selected
            ]
        else:
            concurrency = min(
                request.config.max_concurrency,
                self.max_concurrency,
                max(len(selected), 1),
            )
            semaphore = asyncio.Semaphore(concurrency)

            async def bounded(candidate_id: str, destination: Destination):
                async with semaphore:
                    return await self._run_destination(
                        request,
                        candidate_id,
                        destination,
                    )

            plans = list(await asyncio.gather(*(
                bounded(candidate_id, destination)
                for candidate_id, destination in selected
            )))

        successful = sum(
            item.status == PlanningState.COMPLETED for item in plans
        )
        return MultiPlanResult(
            request_id=_request_id(request),
            request_preferences=request.preferences.model_copy(deep=True),
            destination_candidates=summaries,
            plans=plans,
            requested_plan_count=request.plan_count,
            selected_destination_count=len(selected),
            generated_plan_count=len(plans),
            successful_plan_count=successful,
            failed_or_infeasible_plan_count=len(plans) - successful,
            orchestration_completed=True,
            execution_mode=request.config.execution_mode,
            issues=issues,
        )

    async def _run_destination(
        self,
        request: MultiPlanRequest,
        destination_id: str,
        destination: Destination,
    ) -> DestinationPlan:
        try:
            pipeline = self.pipeline_factory(
                destination.model_copy(deep=True),
                request.config.model_copy(deep=True),
            )
            state = await pipeline.run_for_destination(
                request.preferences.model_copy(deep=True),
                destination.model_copy(deep=True),
            )
        except Exception as exc:  # isolate an unexpected failure to this destination
            reason = str(exc) or "指定目的地 Pipeline 执行失败"
            owned_destination = destination.model_copy(deep=True)
            failure = AgentFailure(
                agent="ComparisonOrchestrator",
                code=FailureCode.AGENT_EXECUTION_ERROR,
                error_type=type(exc).__name__,
                reason=reason,
                required=True,
            )
            state = TravelPlanState(
                state=PlanningState.FAILED,
                preferences=request.preferences.model_copy(deep=True),
                destination_rec=DestinationRecommendation(
                    destinations=[owned_destination.model_copy(deep=True)],
                    selected=owned_destination,
                    reasoning="C1 指定目的地执行发生隔离故障。",
                ),
                status_message=reason,
                agent_failures=[failure],
                error_messages=[
                    f"ComparisonOrchestrator [agent_execution_error/"
                    f"{type(exc).__name__}]: {reason}"
                ],
            )

        business_issues: list[MultiPlanIssue] = []
        if state.state not in {PlanningState.COMPLETED, PlanningState.FAILED}:
            business_issues.append(MultiPlanIssue(
                code=state.state.value,
                destination_id=destination_id,
                message=(
                    state.status_message
                    or (state.error_messages[-1] if state.error_messages else state.state.value)
                ),
            ))
        return DestinationPlan(
            destination_id=destination_id,
            destination=destination.model_copy(deep=True),
            plan=state.model_copy(deep=True),
            status=state.state,
            execution_completed=state.state in _TERMINAL_STATES,
            errors=[item.model_copy(deep=True) for item in state.agent_failures],
            business_issues=business_issues,
            comparison_data=build_comparison_data(state),
        )
