"""Deterministic, read-only comparison of already planned destinations."""

from __future__ import annotations

import json
from collections import defaultdict

from models.multi_plan import DestinationPlan, MultiPlanResult
from models.plan_evaluation import (
    EvaluationDataCompleteness,
    MetricDifference,
    MetricEvaluation,
    MetricEvaluationStatus,
    MetricEvidence,
    MetricName,
    PlanComparability,
    PlanComparisonResult,
    PlanEvaluation,
    PlanEvaluationEligibility,
    RecommendationDecision,
    RecommendationResult,
    ScoringPolicy,
)
from models.schemas import (
    ActivityAttributeStatus,
    ActivityIntensity,
    PlanningState,
    TravelPace,
    UserPreferences,
    WeatherResultStatus,
)
from tools.deterministic import stable_candidate_id

from .explanation import ExplanationService


def _round(value: float, policy: ScoringPolicy) -> float:
    return round(float(value), policy.score_precision)


def _evidence(
    field_path: str,
    value,
    *,
    candidate_ids: list[str] | None = None,
    description: str = "",
) -> MetricEvidence:
    return MetricEvidence(
        field_path=field_path,
        value_json=json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        candidate_ids=sorted(candidate_ids or []),
        description=description,
    )


def _metric_map(evaluation: PlanEvaluation) -> dict[MetricName, MetricEvaluation]:
    return {item.metric_name: item for item in evaluation.metric_evaluations}


class PlanComparator:
    """Evaluate C1 facts without rerunning or mutating any planning state."""

    def __init__(
        self,
        policy: ScoringPolicy | None = None,
        explanation_service: ExplanationService | None = None,
    ) -> None:
        self.policy = policy or ScoringPolicy()
        self.explanation_service = explanation_service or ExplanationService()

    def compare(self, result: MultiPlanResult) -> PlanComparisonResult:
        if not isinstance(result, MultiPlanResult):
            raise TypeError("PlanComparator requires a validated MultiPlanResult")

        # Only operate on an owned copy; the fingerprint covers the exact input.
        input_json = result.model_dump_json()
        owned = result.model_copy(deep=True)
        fingerprint_payload = owned.model_dump(mode="json")
        # Execution strategy is not a business fact and must not change C2 output.
        fingerprint_payload.pop("execution_mode", None)
        input_fingerprint = stable_candidate_id(
            "comparison-input",
            fingerprint_payload,
            self.policy.policy_version,
        )
        comparison_id = stable_candidate_id(
            "plan-comparison",
            {
                "request_id": owned.request_id,
                "input_fingerprint": input_fingerprint,
                "policy": self.policy.model_dump(mode="json"),
            },
            self.policy.policy_version,
        )

        request_preferences = owned.request_preferences
        evaluations = [
            self._evaluate_plan(plan, request_preferences)
            for plan in owned.plans
        ]
        comparability, common_metrics, effective_weights = self._apply_pair_scoring(
            evaluations
        )
        differences = self._build_differences(evaluations)
        recommendation = self._recommend(evaluations, comparability)
        explanations = self.explanation_service.explain(
            owned,
            evaluations,
            differences,
            recommendation,
            comparability,
        )

        limitations = [
            "评分规则是版本化产品假设，未经过真人偏好研究验证。",
            "Mock 价格、天气、活动强度和时长不代表真实市场或实测数据。",
        ]
        if request_preferences is None:
            limitations.append(
                "输入结果缺少原始请求偏好，不能区分用户声明与系统默认兴趣。"
            )
        if comparability == PlanComparability.NOT_COMPARABLE:
            limitations.append(
                "必要状态或证据不足，未强行计算可比较的综合分或推荐。"
            )
        elif comparability == PlanComparability.SINGLE_FEASIBLE:
            limitations.append(
                "只有一个完整可行方案；结果是可行性条件推荐，不是两套正常评分排名。"
            )

        output = PlanComparisonResult(
            request_id=owned.request_id,
            comparison_id=comparison_id,
            input_fingerprint=input_fingerprint,
            plan_evaluations=evaluations,
            metric_differences=differences,
            comparability=comparability,
            common_evaluated_metrics=common_metrics,
            effective_weights=effective_weights,
            recommendation=recommendation,
            explanations=explanations,
            scoring_policy=self.policy,
            source_versions={
                plan.destination_id: {
                    key: list(values)
                    for key, values in plan.comparison_data.source_versions.items()
                }
                for plan in owned.plans
            },
            limitations=limitations,
        )
        if result.model_dump_json() != input_json:
            raise RuntimeError("PlanComparator modified its MultiPlanResult input")
        return output

    def _evaluate_plan(
        self,
        plan: DestinationPlan,
        request_preferences: UserPreferences | None,
    ) -> PlanEvaluation:
        weights = self.policy.weights()
        if plan.status != PlanningState.COMPLETED:
            eligibility = (
                PlanEvaluationEligibility.FAILED
                if plan.status == PlanningState.FAILED
                else PlanEvaluationEligibility.BUSINESS_INFEASIBLE
            )
            metrics = [
                self._ineligible_metric(plan, name, weights[name])
                for name in MetricName
            ]
            return PlanEvaluation(
                destination_id=plan.destination_id,
                destination_name=plan.destination.city,
                planning_status=plan.status,
                evaluation_eligibility=eligibility,
                metric_evaluations=metrics,
                scoring_policy_version=self.policy.policy_version,
                data_completeness=self._completeness(plan, metrics),
                limitations=[
                    "非 COMPLETED 方案不参与普通加权评分；保留原始业务状态。"
                ],
            )

        metrics = [
            self._budget_metric(plan),
            self._interest_metric(plan, request_preferences),
            self._pace_metric(plan, request_preferences),
            self._weather_metric(plan),
        ]
        missing = [
            item.metric_name
            for item in metrics
            if item.evaluation_status
            not in {
                MetricEvaluationStatus.EVALUATED,
                MetricEvaluationStatus.NOT_APPLICABLE,
            }
        ]
        eligibility = (
            PlanEvaluationEligibility.INSUFFICIENT_DATA
            if missing
            else PlanEvaluationEligibility.ELIGIBLE
        )
        limitations = []
        if missing:
            limitations.append(
                "以下适用指标缺少完整证据："
                + ", ".join(item.value for item in missing)
            )
        return PlanEvaluation(
            destination_id=plan.destination_id,
            destination_name=plan.destination.city,
            planning_status=plan.status,
            evaluation_eligibility=eligibility,
            metric_evaluations=metrics,
            scoring_policy_version=self.policy.policy_version,
            data_completeness=self._completeness(plan, metrics),
            limitations=limitations,
        )

    def _ineligible_metric(
        self,
        plan: DestinationPlan,
        metric_name: MetricName,
        configured_weight: float,
    ) -> MetricEvaluation:
        data = plan.comparison_data
        raw_value = None
        numerator = None
        denominator = None
        evidence = [
            _evidence(
                f"plans[{plan.destination_id}].status",
                plan.status.value,
                description="原始方案业务状态",
            )
        ]
        if metric_name == MetricName.BUDGET_MATCH and data.final_total_cost is not None:
            numerator = data.budget - data.final_total_cost
            denominator = data.budget
            raw_value = numerator / denominator
            evidence.append(_evidence(
                f"plans[{plan.destination_id}].comparison_data",
                {
                    "budget": data.budget,
                    "final_total_cost": data.final_total_cost,
                    "within_budget": data.within_budget,
                },
                description="不可行方案的真实费用，仅作诊断，不生成正常分数",
            ))
        return MetricEvaluation(
            metric_name=metric_name,
            raw_value=raw_value,
            numerator=numerator,
            denominator=denominator,
            evaluation_status=MetricEvaluationStatus.INELIGIBLE,
            configured_weight=configured_weight,
            evidence=evidence,
            explanation=(
                f"方案状态为 {plan.status.value}，该维度不生成普通归一化分数。"
            ),
        )

    def _budget_metric(self, plan: DestinationPlan) -> MetricEvaluation:
        data = plan.comparison_data
        weight = self.policy.budget_weight
        evidence = [_evidence(
            f"plans[{plan.destination_id}].comparison_data.budget",
            data.budget,
            description="用户对每个目的地方案的完整预算上限",
        )]
        if data.final_total_cost is None or data.within_budget is None:
            return MetricEvaluation(
                metric_name=MetricName.BUDGET_MATCH,
                evaluation_status=MetricEvaluationStatus.INSUFFICIENT_DATA,
                configured_weight=weight,
                evidence=evidence,
                explanation="缺少最终费用或预算合规标记，不能计算预算匹配。",
            )
        remaining = data.budget - data.final_total_cost
        evidence.append(_evidence(
            f"plans[{plan.destination_id}].comparison_data.final_total_cost",
            data.final_total_cost,
            description="实际最终总费用",
        ))
        if not data.within_budget or remaining < 0:
            return MetricEvaluation(
                metric_name=MetricName.BUDGET_MATCH,
                raw_value=remaining / data.budget,
                numerator=remaining,
                denominator=data.budget,
                evaluation_status=MetricEvaluationStatus.INSUFFICIENT_DATA,
                configured_weight=weight,
                evidence=evidence,
                explanation="COMPLETED 方案的费用与预算状态矛盾，禁止正常评分。",
            )

        headroom_ratio = remaining / data.budget
        capped_ratio = min(
            max(headroom_ratio, 0.0) / self.policy.budget_headroom_target_ratio,
            1.0,
        )
        score = self.policy.budget_score_floor + (
            100.0 - self.policy.budget_score_floor
        ) * capped_ratio
        return MetricEvaluation(
            metric_name=MetricName.BUDGET_MATCH,
            raw_value=_round(headroom_ratio, self.policy),
            normalized_score=_round(score, self.policy),
            numerator=_round(remaining, self.policy),
            denominator=data.budget,
            evaluation_status=MetricEvaluationStatus.EVALUATED,
            configured_weight=weight,
            evidence=evidence,
            explanation=(
                f"预算余量为 {remaining:.2f}（{headroom_ratio:.2%}）；"
                f"余量达到 {self.policy.budget_headroom_target_ratio:.0%} 后分数封顶，"
                "不会继续把更低费用自动解释为更好。"
            ),
        )

    def _interest_metric(
        self,
        plan: DestinationPlan,
        request_preferences: UserPreferences | None,
    ) -> MetricEvaluation:
        weight = self.policy.interest_weight
        if request_preferences is None:
            return MetricEvaluation(
                metric_name=MetricName.INTEREST_MATCH,
                evaluation_status=MetricEvaluationStatus.INSUFFICIENT_DATA,
                configured_weight=weight,
                explanation="缺少原始请求偏好，不能判断兴趣是用户声明还是系统默认。",
                limitations=["不得把 PreferenceAgent 默认兴趣当成用户明确偏好。"],
            )
        interests = [item.strip() for item in request_preferences.interests if item.strip()]
        if not interests:
            return MetricEvaluation(
                metric_name=MetricName.INTEREST_MATCH,
                evaluation_status=MetricEvaluationStatus.NOT_APPLICABLE,
                configured_weight=weight,
                evidence=[_evidence("request_preferences.interests", [])],
                explanation="用户没有声明兴趣，该指标不适用且不填 0 分。",
            )
        activities = [
            item for item in plan.comparison_data.activity_facts if not item.is_rest
        ]
        if not activities:
            return MetricEvaluation(
                metric_name=MetricName.INTEREST_MATCH,
                evaluation_status=MetricEvaluationStatus.INSUFFICIENT_DATA,
                configured_weight=weight,
                evidence=[_evidence("request_preferences.interests", interests)],
                explanation="没有实际入选活动，不能计算兴趣匹配率。",
            )
        interest_set = set(interests)
        matched = [
            item
            for item in activities
            if interest_set.intersection(item.matched_interests)
        ]
        ratio = len(matched) / len(activities)
        return MetricEvaluation(
            metric_name=MetricName.INTEREST_MATCH,
            raw_value=_round(ratio, self.policy),
            normalized_score=_round(ratio * 100.0, self.policy),
            numerator=float(len(matched)),
            denominator=float(len(activities)),
            evaluation_status=MetricEvaluationStatus.EVALUATED,
            configured_weight=weight,
            evidence=[
                _evidence("request_preferences.interests", interests),
                _evidence(
                    f"plans[{plan.destination_id}].comparison_data.activity_facts",
                    {
                        "matched_activity_count": len(matched),
                        "evaluable_activity_count": len(activities),
                    },
                    candidate_ids=[item.candidate_id for item in matched if item.candidate_id],
                    description="休息时段未进入兴趣匹配分母",
                ),
            ],
            explanation=(
                f"实际入选活动中 {len(matched)}/{len(activities)} 项匹配用户声明兴趣；"
                "休息时段不计为不匹配。"
            ),
        )

    def _pace_metric(
        self,
        plan: DestinationPlan,
        request_preferences: UserPreferences | None,
    ) -> MetricEvaluation:
        weight = self.policy.pace_weight
        if request_preferences is None:
            return MetricEvaluation(
                metric_name=MetricName.PACE_FIT,
                evaluation_status=MetricEvaluationStatus.INSUFFICIENT_DATA,
                configured_weight=weight,
                explanation="缺少原始请求偏好，不能确定用户是否明确选择 pace。",
            )
        pace = request_preferences.pace
        if pace is None:
            return MetricEvaluation(
                metric_name=MetricName.PACE_FIT,
                evaluation_status=MetricEvaluationStatus.NOT_APPLICABLE,
                configured_weight=weight,
                evidence=[_evidence("request_preferences.pace", None)],
                explanation="用户没有选择 pace，不推断主观舒适度，也不填 0 分。",
            )

        facts = plan.comparison_data.activity_facts
        by_date: dict[str, list] = defaultdict(list)
        for fact in facts:
            by_date[fact.date].append(fact)
        dates = sorted(plan.comparison_data.daily_activity_counts)
        if not dates:
            return MetricEvaluation(
                metric_name=MetricName.PACE_FIT,
                evaluation_status=MetricEvaluationStatus.INSUFFICIENT_DATA,
                configured_weight=weight,
                explanation="缺少每日活动数量，不能评价旅行节奏。",
            )

        if pace == TravelPace.BALANCED:
            passed = sum(
                plan.comparison_data.daily_activity_counts[day] == 3
                and not any(item.is_rest for item in by_date[day])
                for day in dates
            )
            ratio = passed / len(dates)
            return self._pace_result(
                plan,
                pace,
                ratio,
                float(passed),
                float(len(dates)),
                (
                    f"balanced 要求保持历史三时段容量；{passed}/{len(dates)} 天"
                    "安排 3 项活动且无休息占位。强度不被主观地解释为越低越好。"
                ),
            )

        activities = [item for item in facts if not item.is_rest]
        unknown = [
            item for item in activities
            if item.intensity_status != ActivityAttributeStatus.AVAILABLE
            or item.intensity is None
            or item.intensity == ActivityIntensity.UNKNOWN
            or item.duration_status != ActivityAttributeStatus.AVAILABLE
            or item.duration_hours is None
        ]
        if unknown:
            return MetricEvaluation(
                metric_name=MetricName.PACE_FIT,
                evaluation_status=MetricEvaluationStatus.INSUFFICIENT_DATA,
                configured_weight=weight,
                evidence=[_evidence(
                    f"plans[{plan.destination_id}].comparison_data.activity_facts",
                    {"unknown_attribute_count": len(unknown)},
                    candidate_ids=[item.candidate_id for item in unknown if item.candidate_id],
                )],
                explanation="已选活动存在未知时长或强度，不能伪造 pace 适配分数。",
            )

        if pace == TravelPace.RELAXED:
            passed = 0
            details = []
            for day in dates:
                day_facts = by_date[day]
                day_activities = [item for item in day_facts if not item.is_rest]
                rest_count = sum(item.is_rest for item in day_facts)
                duration = sum(item.duration_hours or 0 for item in day_activities)
                ok = (
                    1 <= len(day_activities) <= 2
                    and rest_count >= 1
                    and all(item.intensity != ActivityIntensity.HIGH for item in day_activities)
                    and duration <= 5.0
                    and all(item.pace_compatible is True for item in day_activities)
                )
                passed += int(ok)
                details.append({
                    "date": day,
                    "activity_count": len(day_activities),
                    "rest_count": rest_count,
                    "known_duration_hours": duration,
                    "passed": ok,
                })
            ratio = passed / len(dates)
            return self._pace_result(
                plan,
                pace,
                ratio,
                float(passed),
                float(len(dates)),
                f"relaxed 有 {passed}/{len(dates)} 天满足 1～2 项、明确休息、无 high、"
                "已知总时长不超过 5 小时及 pace 硬约束。",
                details,
            )

        capacity_passed = sum(
            plan.comparison_data.daily_activity_counts[day] == 3
            and not any(item.is_rest for item in by_date[day])
            for day in dates
        )
        capacity_rate = capacity_passed / len(dates)
        affinity = {
            ActivityIntensity.HIGH: self.policy.packed_high_affinity,
            ActivityIntensity.MEDIUM: self.policy.packed_medium_affinity,
            ActivityIntensity.LOW: self.policy.packed_low_affinity,
        }
        intensity_affinity = sum(affinity[item.intensity] for item in activities) / len(activities)
        combined = (
            self.policy.packed_capacity_weight * capacity_rate
            + (1 - self.policy.packed_capacity_weight) * intensity_affinity
        )
        return self._pace_result(
            plan,
            pace,
            combined,
            _round(combined, self.policy),
            1.0,
            (
                f"packed 容量达成率 {capacity_rate:.2%}，已知强度亲和度"
                f" {intensity_affinity:.2%}；high/medium/low 分别按"
                f" {self.policy.packed_high_affinity:.2f}/"
                f"{self.policy.packed_medium_affinity:.2f}/"
                f"{self.policy.packed_low_affinity:.2f} 计入，"
                "不把 high 一律解释为不舒适。"
            ),
            {
                "capacity_rate": capacity_rate,
                "intensity_affinity": intensity_affinity,
                "known_activity_count": len(activities),
            },
        )

    def _pace_result(
        self,
        plan: DestinationPlan,
        pace: TravelPace,
        raw_value: float,
        numerator: float,
        denominator: float,
        explanation: str,
        details=None,
    ) -> MetricEvaluation:
        return MetricEvaluation(
            metric_name=MetricName.PACE_FIT,
            raw_value=_round(raw_value, self.policy),
            normalized_score=_round(raw_value * 100.0, self.policy),
            numerator=numerator,
            denominator=denominator,
            evaluation_status=MetricEvaluationStatus.EVALUATED,
            configured_weight=self.policy.pace_weight,
            evidence=[
                _evidence("request_preferences.pace", pace.value),
                _evidence(
                    f"plans[{plan.destination_id}].comparison_data.pace_facts",
                    details if details is not None else {},
                    candidate_ids=[
                        item.candidate_id
                        for item in plan.comparison_data.activity_facts
                        if item.candidate_id
                    ],
                ),
            ],
            explanation=explanation,
        )

    def _weather_metric(self, plan: DestinationPlan) -> MetricEvaluation:
        weight = self.policy.weather_weight
        weather = plan.plan.weather_result
        metadata = plan.plan.weather_planning
        base_evidence = [_evidence(
            f"plans[{plan.destination_id}].plan.weather_planning",
            metadata.model_dump(mode="json"),
            description="天气数据取得、降级或故障状态",
        )]
        if weather is None:
            return MetricEvaluation(
                metric_name=MetricName.WEATHER_SUITABILITY,
                evaluation_status=MetricEvaluationStatus.INSUFFICIENT_DATA,
                configured_weight=weight,
                evidence=base_evidence,
                explanation="没有天气结果；未将未知天气替换为晴天或 0 分。",
            )
        base_evidence.append(_evidence(
            f"plans[{plan.destination_id}].plan.weather_result.availability_status",
            weather.availability_status.value,
        ))
        if weather.availability_status == WeatherResultStatus.PARTIAL:
            return MetricEvaluation(
                metric_name=MetricName.WEATHER_SUITABILITY,
                evaluation_status=MetricEvaluationStatus.PARTIAL,
                configured_weight=weight,
                evidence=base_evidence,
                explanation="天气仅部分可用，不生成可与完整天气直接比较的分数。",
            )
        if weather.availability_status == WeatherResultStatus.UNAVAILABLE:
            return MetricEvaluation(
                metric_name=MetricName.WEATHER_SUITABILITY,
                evaluation_status=MetricEvaluationStatus.INSUFFICIENT_DATA,
                configured_weight=weight,
                evidence=base_evidence,
                explanation="天气明确不可用，采用基础规划但不虚构天气适宜度。",
            )

        activities = [
            item for item in plan.comparison_data.activity_facts if not item.is_rest
        ]
        evaluable = [item for item in activities if item.weather_compatible is not None]
        if not activities:
            return MetricEvaluation(
                metric_name=MetricName.WEATHER_SUITABILITY,
                evaluation_status=MetricEvaluationStatus.INSUFFICIENT_DATA,
                configured_weight=weight,
                evidence=base_evidence,
                explanation="没有实际活动，不能计算天气适宜度。",
            )
        if len(evaluable) != len(activities):
            return MetricEvaluation(
                metric_name=MetricName.WEATHER_SUITABILITY,
                evaluation_status=MetricEvaluationStatus.PARTIAL,
                configured_weight=weight,
                evidence=base_evidence + [_evidence(
                    f"plans[{plan.destination_id}].comparison_data.activity_facts",
                    {
                        "evaluable_activity_count": len(evaluable),
                        "activity_count": len(activities),
                    },
                    candidate_ids=[item.candidate_id for item in evaluable if item.candidate_id],
                )],
                explanation="部分活动缺少天气适配证据，不生成完整天气分数。",
            )
        compatible = [item for item in evaluable if item.weather_compatible is True]
        ratio = len(compatible) / len(evaluable)
        return MetricEvaluation(
            metric_name=MetricName.WEATHER_SUITABILITY,
            raw_value=_round(ratio, self.policy),
            normalized_score=_round(ratio * 100.0, self.policy),
            numerator=float(len(compatible)),
            denominator=float(len(evaluable)),
            evaluation_status=MetricEvaluationStatus.EVALUATED,
            configured_weight=weight,
            evidence=base_evidence + [_evidence(
                f"plans[{plan.destination_id}].comparison_data.activity_facts",
                {
                    "weather_compatible_count": len(compatible),
                    "weather_evaluable_count": len(evaluable),
                },
                candidate_ids=[item.candidate_id for item in evaluable if item.candidate_id],
                description="只评价实际入选且具有天气判断的活动",
            )],
            explanation=(
                f"实际入选活动中 {len(compatible)}/{len(evaluable)} 项通过天气硬约束；"
                "不根据晴雨类型额外制造奖励。"
            ),
        )

    def _completeness(
        self,
        plan: DestinationPlan,
        metrics: list[MetricEvaluation],
    ) -> EvaluationDataCompleteness:
        not_applicable = [
            item.metric_name
            for item in metrics
            if item.evaluation_status == MetricEvaluationStatus.NOT_APPLICABLE
        ]
        applicable = [item for item in metrics if item.metric_name not in not_applicable]
        evaluated = [
            item for item in applicable
            if item.evaluation_status == MetricEvaluationStatus.EVALUATED
        ]
        missing = [
            item.metric_name
            for item in applicable
            if item.evaluation_status != MetricEvaluationStatus.EVALUATED
        ]
        return EvaluationDataCompleteness(
            applicable_metric_count=len(applicable),
            evaluated_metric_count=len(evaluated),
            completeness_ratio=(
                round(len(evaluated) / len(applicable), 4) if applicable else 1.0
            ),
            missing_metrics=missing,
            not_applicable_metrics=not_applicable,
            source_versions={
                key: list(values)
                for key, values in plan.comparison_data.source_versions.items()
            },
        )

    def _apply_pair_scoring(
        self,
        evaluations: list[PlanEvaluation],
    ) -> tuple[PlanComparability, list[MetricName], dict[MetricName, float]]:
        eligible = [
            item
            for item in evaluations
            if item.evaluation_eligibility == PlanEvaluationEligibility.ELIGIBLE
        ]
        if len(evaluations) != 2:
            return PlanComparability.NOT_COMPARABLE, [], {}
        if len(eligible) == 1:
            return PlanComparability.SINGLE_FEASIBLE, [], {}
        if len(eligible) != 2:
            return PlanComparability.NOT_COMPARABLE, [], {}

        maps = [_metric_map(item) for item in evaluations]
        common: list[MetricName] = []
        for name in MetricName:
            statuses = [mapping[name].evaluation_status for mapping in maps]
            if statuses == [MetricEvaluationStatus.EVALUATED] * 2:
                common.append(name)
            elif statuses == [MetricEvaluationStatus.NOT_APPLICABLE] * 2:
                continue
            else:
                return PlanComparability.NOT_COMPARABLE, [], {}
        if not common:
            return PlanComparability.NOT_COMPARABLE, [], {}

        weights = self.policy.weights()
        total = sum(weights[name] for name in common)
        effective = {name: weights[name] / total for name in common}
        for evaluation in evaluations:
            updated = []
            metric_map = _metric_map(evaluation)
            for name in MetricName:
                updated.append(metric_map[name].model_copy(update={
                    "effective_weight": effective.get(name, 0.0),
                }))
            evaluation.metric_evaluations = updated
            evaluation.overall_score = _round(sum(
                _metric_map(evaluation)[name].normalized_score * effective[name]
                for name in common
            ), self.policy)
        return PlanComparability.COMPARABLE, common, effective

    def _build_differences(
        self,
        evaluations: list[PlanEvaluation],
    ) -> list[MetricDifference]:
        if len(evaluations) != 2:
            return []
        first, second = evaluations
        first_map = _metric_map(first)
        second_map = _metric_map(second)
        differences = []
        for name in MetricName:
            a = first_map[name]
            b = second_map[name]
            delta = (
                _round(a.normalized_score - b.normalized_score, self.policy)
                if a.normalized_score is not None and b.normalized_score is not None
                else None
            )
            differences.append(MetricDifference(
                metric_name=name,
                destination_a_id=first.destination_id,
                destination_b_id=second.destination_id,
                destination_a_raw_value=a.raw_value,
                destination_b_raw_value=b.raw_value,
                destination_a_score=a.normalized_score,
                destination_b_score=b.normalized_score,
                score_difference_a_minus_b=delta,
                explanation=(
                    f"{name.value} 的规范化分差（A-B）为 {delta:.4f}。"
                    if delta is not None
                    else f"{name.value} 缺少双方可直接比较的规范化分数。"
                ),
                evidence=[*a.evidence, *b.evidence],
            ))
        return differences

    def _recommend(
        self,
        evaluations: list[PlanEvaluation],
        comparability: PlanComparability,
    ) -> RecommendationResult:
        if comparability == PlanComparability.SINGLE_FEASIBLE:
            eligible = next(
                item
                for item in evaluations
                if item.evaluation_eligibility == PlanEvaluationEligibility.ELIGIBLE
            )
            other = next(item for item in evaluations if item is not eligible)
            return RecommendationResult(
                decision=RecommendationDecision.ONLY_FEASIBLE,
                recommended_destination_id=eligible.destination_id,
                reason=(
                    f"仅 {eligible.destination_name} 是完整可行方案；"
                    f"{other.destination_name} 保留状态 {other.planning_status.value}。"
                    "这是可行性条件推荐，不是把不可行方案当作低分方案。"
                ),
                evidence=[
                    _evidence(
                        f"plan_evaluations[{eligible.destination_id}].planning_status",
                        eligible.planning_status.value,
                    ),
                    _evidence(
                        f"plan_evaluations[{other.destination_id}].planning_status",
                        other.planning_status.value,
                    ),
                ],
            )
        if comparability != PlanComparability.COMPARABLE:
            return RecommendationResult(
                decision=RecommendationDecision.NO_RECOMMENDATION,
                reason="方案状态或必要证据不足，无法进行公平综合比较。",
            )

        first, second = evaluations
        assert first.overall_score is not None and second.overall_score is not None
        difference = abs(first.overall_score - second.overall_score)
        if difference <= self.policy.tie_threshold_points:
            return RecommendationResult(
                decision=RecommendationDecision.TIE,
                tied_destination_ids=[first.destination_id, second.destination_id],
                score_difference=_round(difference, self.policy),
                reason=(
                    f"综合分差 {difference:.4f} 不超过并列阈值"
                    f" {self.policy.tie_threshold_points:.4f}，没有强行指定胜出方案。"
                ),
                evidence=[_evidence(
                    "plan_evaluations.overall_scores",
                    {
                        first.destination_id: first.overall_score,
                        second.destination_id: second.overall_score,
                    },
                )],
            )
        winner = first if first.overall_score > second.overall_score else second
        return RecommendationResult(
            decision=RecommendationDecision.RECOMMENDED,
            recommended_destination_id=winner.destination_id,
            score_difference=_round(difference, self.policy),
            reason=(
                f"在 {self.policy.policy_version} 的已声明权重和共同可评价指标下，"
                f"{winner.destination_name} 综合分更高；该结论是条件化规则结果，"
                "不是绝对客观排序。"
            ),
            evidence=[_evidence(
                "plan_evaluations.overall_scores",
                {
                    first.destination_id: first.overall_score,
                    second.destination_id: second.overall_score,
                },
            )],
        )
