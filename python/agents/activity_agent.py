"""
Activity Agent —— 活动/景点推荐 Agent。

职责: 推荐景点/餐厅/体验，生成每日行程安排。
在并行阶段执行，与 Flight Agent / Hotel Agent 同时运行。

面试考点:
  - 每日行程的 time-slot 分配逻辑（morning / afternoon / evening）
  - 活动与用户兴趣的匹配度
  - 预算分配: 活动费用 ≈ 总预算的 20%-30%
"""

from __future__ import annotations

from datetime import datetime, timedelta

from loguru import logger

from models.schemas import (
    Activity,
    ActivityConstraintIssue,
    ActivityPlanningMode,
    ActivitySearchRequest,
    ActivitySearchResult,
    ActivityWeatherDecision,
    DailyWeather,
    DayPlan,
    PaceCompatibilityEvaluation,
    PaceSlotDecision,
    PlanningState,
    RestPeriod,
    TravelPlanState,
    TravelPace,
    WeatherAvailability,
    WeatherCompatibilityEvaluation,
    WeatherDecisionType,
    WeatherPlanningStatus,
)
from tools.activity_search import MockActivityProvider
from tools.providers import ActivityProvider

from .base_agent import BaseAgent
from .pace_planning import PaceCompatibilityEvaluator
from .weather_compatibility import WeatherCompatibilityEvaluator


class ActivityAgent(BaseAgent):
    name = "ActivityAgent"
    output_fields = ("activity_result",)

    def __init__(
        self,
        provider: ActivityProvider | None = None,
        *,
        weather_evaluator: WeatherCompatibilityEvaluator | None = None,
        pace_evaluator: PaceCompatibilityEvaluator | None = None,
        require_weather_context: bool = False,
        planning_mode: ActivityPlanningMode = ActivityPlanningMode.WEATHER_AWARE,
    ) -> None:
        super().__init__()
        self.provider = provider or MockActivityProvider()
        self.weather_evaluator = weather_evaluator or WeatherCompatibilityEvaluator()
        self.pace_evaluator = pace_evaluator or PaceCompatibilityEvaluator()
        self.require_weather_context = require_weather_context
        self.planning_mode = planning_mode

    async def execute(self, state: TravelPlanState) -> TravelPlanState:
        pref = state.preferences
        dest = state.selected_destination
        if pref is None or dest is None:
            raise ValueError("缺少偏好或目的地信息")

        weather_status = state.weather_planning.status
        if self.require_weather_context and weather_status == WeatherPlanningStatus.NOT_STARTED:
            raise RuntimeError("天气状态尚未确定，不能启动活动规划")
        if weather_status in {
            WeatherPlanningStatus.FAILED_DATA,
            WeatherPlanningStatus.FAILED_INTERNAL,
        }:
            raise RuntimeError("天气数据失败，不能继续活动规划")

        days = self._get_travel_days(pref.start_date, pref.end_date)
        daily_budget = (pref.budget * 0.25) / max(len(days), 1) / pref.num_travelers

        pool = await self.provider.search(ActivitySearchRequest(
            city=dest.city,
            start_date=pref.start_date,
            end_date=pref.end_date,
        ))
        weather_by_date = {
            item.date: item
            for item in (state.weather_result.daily_weather if state.weather_result else [])
        }
        day_plans: list[DayPlan] = []
        evaluations: list[WeatherCompatibilityEvaluation] = []
        constraint_issues: list[ActivityConstraintIssue] = []
        decisions: list[ActivityWeatherDecision] = []
        pace_evaluations: list[PaceCompatibilityEvaluation] = []
        pace_decisions: list[PaceSlotDecision] = []
        total_cost = 0.0

        for date_str in days:
            (
                plan,
                day_evaluations,
                day_issues,
                day_decisions,
                day_pace_evaluations,
                day_pace_decisions,
            ) = self._plan_one_day(
                date_str,
                pool,
                daily_budget,
                pref.interests,
                weather_by_date.get(date_str),
                pref.pace,
            )
            day_cost = sum(a.price for a in plan.activities) * pref.num_travelers
            plan.day_cost = day_cost
            total_cost += day_cost
            day_plans.append(plan)
            evaluations.extend(day_evaluations)
            constraint_issues.extend(day_issues)
            decisions.extend(day_decisions)
            pace_evaluations.extend(day_pace_evaluations)
            pace_decisions.extend(day_pace_decisions)

        weather_optimized = self.planning_mode == ActivityPlanningMode.WEATHER_AWARE and any(
            item.availability_status == WeatherAvailability.AVAILABLE
            for item in weather_by_date.values()
        )
        fallback_reason = (
            state.weather_planning.reason
            if state.weather_planning.fallback_used
            else ""
        )
        if weather_status == WeatherPlanningStatus.NOT_STARTED:
            fallback_reason = "未请求天气上下文，采用向后兼容的基础活动规划。"

        state.activity_result = ActivitySearchResult(
            activity_candidates=[activity.model_copy(deep=True) for activity in pool],
            day_plans=day_plans,
            total_activity_cost=total_cost,
            weather_evaluations=evaluations,
            weather_decisions=decisions,
            planning_mode=self.planning_mode,
            weather_optimized=weather_optimized,
            weather_fallback_reason=fallback_reason,
            constraint_issues=constraint_issues,
            requested_pace=pref.pace,
            pace_policy_version=self.pace_evaluator.policy.policy_version,
            pace_evaluations=pace_evaluations,
            pace_decisions=pace_decisions,
        )
        if constraint_issues:
            if any(issue.code.startswith("pace_") for issue in constraint_issues):
                state.state = PlanningState.PACE_CONSTRAINTS_UNSATISFIED
                state.status_message = (
                    "Activity planning could not satisfy the requested travel-pace "
                    "constraints with the available candidates."
                )
            else:
                state.state = PlanningState.ACTIVITY_CONSTRAINTS_UNSATISFIED
                state.status_message = (
                    "Activity planning could not satisfy all date, time-slot, interest, "
                    "and weather hard constraints."
                )
            state.error_messages.append(state.status_message)
            return state
        logger.info(f"[{self.name}] 生成 {len(day_plans)} 天行程, 活动总费用: ¥{total_cost:.0f}")
        return state

    @staticmethod
    def _get_travel_days(start: str, end: str) -> list[str]:
        d1 = datetime.strptime(start, "%Y-%m-%d")
        d2 = datetime.strptime(end, "%Y-%m-%d")
        days_count = (d2 - d1).days
        if days_count <= 0:
            raise ValueError("结束日期必须晚于开始日期")
        return [(d1 + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days_count)]

    def _plan_one_day(
        self,
        date: str,
        pool: list[Activity],
        daily_budget: float,
        interests: list[str],
        weather: DailyWeather | None,
        pace: TravelPace | None,
    ) -> tuple[
        DayPlan,
        list[WeatherCompatibilityEvaluation],
        list[ActivityConstraintIssue],
        list[ActivityWeatherDecision],
        list[PaceCompatibilityEvaluation],
        list[PaceSlotDecision],
    ]:
        del daily_budget  # 预算组合选择留给第三批预算闭环重构。
        slots = ["morning", "afternoon", "evening"]
        activities: list[Activity] = []
        rest_slots: list[RestPeriod] = []
        evaluations: list[WeatherCompatibilityEvaluation] = []
        issues: list[ActivityConstraintIssue] = []
        decisions: list[ActivityWeatherDecision] = []
        pace_evaluations: list[PaceCompatibilityEvaluation] = []
        pace_decisions: list[PaceSlotDecision] = []
        selected_duration = 0.0

        for slot in slots:
            candidates = [
                activity
                for activity in pool
                if activity.time_slot == slot
                and activity.available_start_date <= date < activity.available_end_date
            ]
            if not candidates:
                if pace == TravelPace.RELAXED:
                    reason = "relaxed 允许该时段休息；候选池没有可用活动。"
                    rest_slots.append(RestPeriod(time_slot=slot, reason=reason))
                    pace_decisions.append(PaceSlotDecision(
                        date=date,
                        time_slot=slot,
                        requested_pace=pace,
                        outcome="rest",
                        reason=reason,
                    ))
                    decisions.append(ActivityWeatherDecision(
                        date=date,
                        time_slot=slot,
                        planning_mode=self.planning_mode,
                        decision_type=WeatherDecisionType.NO_FEASIBLE_CANDIDATE,
                        weather_data_available=weather is not None,
                        reason="该时段按 relaxed 规则保留为休息，不编造活动。",
                    ))
                    continue
                issues.append(ActivityConstraintIssue(
                    date=date,
                    time_slot=slot,
                    code="no_available_candidate",
                    reason="该日期和时间段没有可用活动候选。",
                ))
                decisions.append(ActivityWeatherDecision(
                    date=date,
                    time_slot=slot,
                    planning_mode=self.planning_mode,
                    decision_type=WeatherDecisionType.NO_FEASIBLE_CANDIDATE,
                    weather_data_available=weather is not None,
                    reason="该日期和时间段没有基础活动候选。",
                ))
                continue

            evaluated: list[tuple[
                Activity,
                WeatherCompatibilityEvaluation,
                PaceCompatibilityEvaluation,
            ]] = []
            for activity in candidates:
                weather_evaluation = self.weather_evaluator.evaluate(
                    activity,
                    weather,
                    activity_date=date,
                )
                pace_evaluation = self.pace_evaluator.evaluate(
                    activity,
                    pace,
                    activity_date=date,
                    projected_daily_duration=selected_duration + activity.duration_hours,
                )
                interest_score = self._interest_bonus(activity, interests)
                weighted_weather = (
                    weather_evaluation.weather_score
                    * self.weather_evaluator.policy.score_weight
                )
                evaluated.append((
                    activity,
                    weather_evaluation.model_copy(update={
                        "activity_name": activity.name,
                        "activity_rating": activity.rating,
                        "interest_score": interest_score,
                        "weighted_weather_score": weighted_weather,
                        "total_selection_score": (
                            activity.rating
                            + interest_score
                            + weighted_weather
                            + pace_evaluation.pace_score
                        ),
                        "selection_outcome": (
                            "excluded_by_weather_hard_constraint"
                            if not weather_evaluation.hard_constraint_satisfied
                            else "eligible_not_selected"
                        ),
                    }),
                    pace_evaluation,
                ))

            if (
                pace == TravelPace.RELAXED
                and len(activities) >= self.pace_evaluator.policy.relaxed_max_activities_per_day
            ):
                reason = "已达到 relaxed 每日活动数量上限，保留明确休息时段。"
                rest_slots.append(RestPeriod(time_slot=slot, reason=reason))
                pace_decisions.append(PaceSlotDecision(
                    date=date,
                    time_slot=slot,
                    requested_pace=pace,
                    outcome="rest",
                    reason=reason,
                ))
                evaluations.extend(item[1] for item in evaluated)
                pace_evaluations.extend(
                    item[2].model_copy(update={"selection_outcome": "not_selected_rest_capacity"})
                    for item in evaluated
                )
                decisions.append(ActivityWeatherDecision(
                    date=date,
                    time_slot=slot,
                    planning_mode=self.planning_mode,
                    decision_type=WeatherDecisionType.BASELINE_SELECTION,
                    weather_data_available=weather is not None,
                    reason=reason,
                ))
                continue

            pace_eligible = [
                item for item in evaluated if item[2].hard_constraint_satisfied
            ]
            if not pace_eligible:
                pace_evaluations.extend(
                    item[2].model_copy(update={
                        "selection_outcome": "excluded_by_pace_hard_constraint",
                    })
                    for item in evaluated
                )
                evaluations.extend(item[1] for item in evaluated)
                reason = "该时段没有满足旅行节奏硬约束的真实候选。"
                if pace == TravelPace.RELAXED:
                    rest_slots.append(RestPeriod(time_slot=slot, reason=reason))
                    pace_decisions.append(PaceSlotDecision(
                        date=date,
                        time_slot=slot,
                        requested_pace=pace,
                        outcome="rest",
                        reason=reason,
                    ))
                    decisions.append(ActivityWeatherDecision(
                        date=date,
                        time_slot=slot,
                        planning_mode=self.planning_mode,
                        decision_type=WeatherDecisionType.NO_FEASIBLE_CANDIDATE,
                        weather_data_available=weather is not None,
                        reason=reason,
                    ))
                    continue
                issues.append(ActivityConstraintIssue(
                    date=date,
                    time_slot=slot,
                    code="pace_no_compatible_candidate",
                    reason=reason,
                ))
                pace_decisions.append(PaceSlotDecision(
                    date=date,
                    time_slot=slot,
                    requested_pace=pace,
                    outcome="no_feasible_activity",
                    reason=reason,
                ))
                continue

            baseline_best, _, _ = sorted(
                pace_eligible,
                key=lambda item: self._selection_key(
                    item[0], item[1], item[2], include_weather=False
                ),
            )[0]
            eligible = [
                item for item in pace_eligible if item[1].hard_constraint_satisfied
            ]
            weather_best_pair = (
                sorted(
                    eligible,
                    key=lambda item: self._selection_key(
                        item[0], item[1], item[2], include_weather=True
                    ),
                )[0]
                if eligible
                else None
            )
            weather_data_available = any(
                item[1].data_sufficient for item in evaluated
            )

            if (
                self.planning_mode == ActivityPlanningMode.WEATHER_AWARE
                and weather_data_available
                and weather_best_pair is None
            ):
                evaluations.extend(item[1] for item in evaluated)
                pace_evaluations.extend(item[2] for item in evaluated)
                weather_reason = "天气硬约束排除了全部 pace 合规候选。"
                if pace == TravelPace.RELAXED:
                    rest_slots.append(RestPeriod(time_slot=slot, reason=weather_reason))
                    pace_decisions.append(PaceSlotDecision(
                        date=date,
                        time_slot=slot,
                        requested_pace=pace,
                        outcome="rest",
                        reason=weather_reason,
                    ))
                else:
                    issues.append(ActivityConstraintIssue(
                        date=date,
                        time_slot=slot,
                        code="no_weather_compatible_candidate",
                        reason="该时间段没有满足天气硬约束的真实候选，未编造活动。",
                    ))
                    pace_decisions.append(PaceSlotDecision(
                        date=date,
                        time_slot=slot,
                        requested_pace=pace,
                        outcome="no_feasible_activity",
                        reason=weather_reason,
                    ))
                decisions.append(ActivityWeatherDecision(
                    date=date,
                    time_slot=slot,
                    planning_mode=self.planning_mode,
                    decision_type=WeatherDecisionType.NO_FEASIBLE_CANDIDATE,
                    weather_data_available=True,
                    baseline_candidate_id=baseline_best.candidate_id,
                    baseline_candidate_name=baseline_best.name,
                    hard_excluded_candidate_ids=[
                        item[0].candidate_id
                        for item in evaluated
                        if not item[1].hard_constraint_satisfied
                    ],
                    unknown_candidate_ids=[
                        item[0].candidate_id
                        for item in evaluated
                        if not item[1].data_sufficient
                    ],
                    reason="天气硬约束排除了该时段全部真实候选。",
                ))
                continue

            weather_best, weather_best_evaluation, weather_best_pace_evaluation = (
                weather_best_pair if weather_best_pair is not None else evaluated[0]
            )
            if (
                self.planning_mode == ActivityPlanningMode.BASELINE
                or not weather_data_available
            ):
                best = baseline_best
                best_evaluation = next(
                    item[1] for item in evaluated if item[0] is baseline_best
                )
                best_pace_evaluation = next(
                    item[2] for item in evaluated if item[0] is baseline_best
                )
            else:
                best = weather_best
                best_evaluation = weather_best_evaluation
                best_pace_evaluation = weather_best_pace_evaluation

            enriched_evaluations = []
            enriched_pace_evaluations = []
            for candidate, evaluation, pace_evaluation in evaluated:
                if candidate.candidate_id == best.candidate_id:
                    outcome = (
                        "selected_weather_unverified"
                        if not evaluation.data_sufficient
                        else "selected"
                    )
                    evaluation = evaluation.model_copy(update={
                        "selected": True,
                        "selection_outcome": outcome,
                    })
                    best_evaluation = evaluation
                    pace_evaluation = pace_evaluation.model_copy(update={
                        "selected": True,
                        "selection_outcome": "selected",
                    })
                    best_pace_evaluation = pace_evaluation
                elif not pace_evaluation.hard_constraint_satisfied:
                    pace_evaluation = pace_evaluation.model_copy(update={
                        "selection_outcome": "excluded_by_pace_hard_constraint",
                    })
                enriched_evaluations.append(evaluation)
                enriched_pace_evaluations.append(pace_evaluation)
            evaluations.extend(enriched_evaluations)
            pace_evaluations.extend(enriched_pace_evaluations)

            weather_driven_change = bool(
                weather_data_available
                and weather_best_pair is not None
                and baseline_best.candidate_id != weather_best.candidate_id
                and self.planning_mode == ActivityPlanningMode.WEATHER_AWARE
            )
            if self.planning_mode == ActivityPlanningMode.BASELINE:
                decision_type = WeatherDecisionType.BASELINE_SELECTION
                decision_reason = (
                    "控制组按基础评分选择；天气仅用于事后评价，不参与排序或过滤。"
                )
            elif not weather_data_available:
                decision_type = WeatherDecisionType.WEATHER_UNAVAILABLE_FALLBACK
                decision_reason = (
                    "天气不可用，按基础评分选择；未宣称经过天气优化。"
                )
            elif weather_driven_change:
                decision_type = WeatherDecisionType.WEATHER_DRIVEN_CHANGE
                decision_reason = (
                    f"同候选池基础方案为“{baseline_best.name}”，天气规则选择"
                    f"“{weather_best.name}”：{weather_best_evaluation.reason}"
                )
            else:
                decision_type = WeatherDecisionType.WEATHER_RULE_SELECTION
                decision_reason = (
                    "天气规则参与评价，但最终候选与同条件基础方案一致；"
                    "不记录为天气替换。"
                )

            activities.append(best.model_copy(
                deep=True,
                update={
                    "description": f"{date} {slot} - {best.name}",
                    "weather_compatible": (
                        best_evaluation.hard_constraint_satisfied
                        if best_evaluation.data_sufficient
                        else None
                    ),
                    "recommendation_reason": best_evaluation.reason,
                    "pace_compatible": best_pace_evaluation.hard_constraint_satisfied,
                    "pace_recommendation_reason": best_pace_evaluation.reason,
                },
            ))
            if best_pace_evaluation.duration_hours is not None:
                selected_duration += best_pace_evaluation.duration_hours
            pace_decisions.append(PaceSlotDecision(
                date=date,
                time_slot=slot,
                requested_pace=pace,
                outcome="activity",
                candidate_id=best.candidate_id,
                candidate_name=best.name,
                reason=best_pace_evaluation.reason,
            ))
            decisions.append(ActivityWeatherDecision(
                date=date,
                time_slot=slot,
                planning_mode=self.planning_mode,
                decision_type=decision_type,
                weather_data_available=weather_data_available,
                baseline_candidate_id=baseline_best.candidate_id,
                baseline_candidate_name=baseline_best.name,
                weather_candidate_id=(
                    weather_best.candidate_id if weather_best_pair is not None else ""
                ),
                weather_candidate_name=(
                    weather_best.name if weather_best_pair is not None else ""
                ),
                initial_selected_candidate_id=best.candidate_id,
                initial_selected_candidate_name=best.name,
                final_candidate_id=best.candidate_id,
                final_candidate_name=best.name,
                weather_driven_change=weather_driven_change,
                hard_excluded_candidate_ids=[
                    item[0].candidate_id
                    for item in evaluated
                    if not item[1].hard_constraint_satisfied
                ],
                unknown_candidate_ids=[
                    item[0].candidate_id
                    for item in evaluated
                    if not item[1].data_sufficient
                ],
                reason=decision_reason,
            ))

        if (
            pace == TravelPace.RELAXED
            and len(activities) < self.pace_evaluator.policy.relaxed_min_activities_per_day
        ):
            issues.append(ActivityConstraintIssue(
                date=date,
                time_slot="day",
                code="pace_minimum_not_met",
                reason="没有找到 relaxed 每日最低数量的 pace 与天气合规活动。",
            ))

        return (
            DayPlan(date=date, activities=activities, rest_slots=rest_slots),
            evaluations,
            issues,
            decisions,
            pace_evaluations,
            pace_decisions,
        )

    @staticmethod
    def _selection_key(
        activity: Activity,
        evaluation: WeatherCompatibilityEvaluation,
        pace_evaluation: PaceCompatibilityEvaluation,
        *,
        include_weather: bool,
    ) -> tuple[float, float, str]:
        score = activity.rating + evaluation.interest_score + pace_evaluation.pace_score
        if include_weather:
            score += evaluation.weighted_weather_score
        return (-score, activity.price, activity.candidate_id)

    @staticmethod
    def _interest_bonus(activity: Activity, interests: list[str]) -> float:
        return float(sum(
            3
            for tag in interests
            if tag.lower() in activity.name.lower()
            or tag.lower() in activity.category.lower()
            or tag.lower() in activity.description.lower()
        ))
