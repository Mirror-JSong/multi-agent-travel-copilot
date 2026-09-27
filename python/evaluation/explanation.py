"""Deterministic evidence-bound explanations for Stage C2."""

from __future__ import annotations

from models.multi_plan import MultiPlanResult
from models.plan_evaluation import (
    ComparisonExplanation,
    MetricDifference,
    MetricEvaluationStatus,
    MetricName,
    PlanComparability,
    PlanEvaluation,
    RecommendationDecision,
    RecommendationResult,
)


def _metric_map(evaluation: PlanEvaluation):
    return {item.metric_name: item for item in evaluation.metric_evaluations}


class ExplanationService:
    """Build structured text only from actual result fields and metric evidence."""

    def explain(
        self,
        source: MultiPlanResult,
        evaluations: list[PlanEvaluation],
        differences: list[MetricDifference],
        recommendation: RecommendationResult,
        comparability: PlanComparability,
    ) -> list[ComparisonExplanation]:
        if len(evaluations) != 2:
            return [ComparisonExplanation(
                code="insufficient_plan_count",
                message="不足两套方案，不能生成双方案差异解释。",
            )]

        first, second = evaluations
        plans = {item.destination_id: item for item in source.plans}
        explanations: list[ComparisonExplanation] = []

        for evaluation in evaluations:
            if evaluation.planning_status.value != "completed":
                plan = plans[evaluation.destination_id]
                detail = (
                    plan.plan.status_message
                    or (plan.plan.error_messages[-1] if plan.plan.error_messages else "无附加原因")
                )
                explanations.append(ComparisonExplanation(
                    code=f"plan_status_{evaluation.planning_status.value}",
                    message=(
                        f"{evaluation.destination_name} 状态为"
                        f" {evaluation.planning_status.value}：{detail}"
                    ),
                    destination_ids=[evaluation.destination_id],
                    evidence=[
                        item
                        for metric in evaluation.metric_evaluations
                        for item in metric.evidence
                        if item.field_path.endswith(".status")
                    ],
                ))

        first_cost = plans[first.destination_id].comparison_data.final_total_cost
        second_cost = plans[second.destination_id].comparison_data.final_total_cost
        if first_cost is not None and second_cost is not None:
            if first_cost == second_cost:
                message = f"两套方案最终费用相同，均为 {first_cost:.2f}。"
            else:
                cheaper = first if first_cost < second_cost else second
                saving = abs(first_cost - second_cost)
                message = (
                    f"{cheaper.destination_name} 最终费用低 {saving:.2f}；"
                    f"两方案分别为 {first_cost:.2f} 和 {second_cost:.2f}。"
                    "费用差异是事实，不单独等同于整体更优。"
                )
            budget_diff = next(
                item for item in differences if item.metric_name == MetricName.BUDGET_MATCH
            )
            explanations.append(ComparisonExplanation(
                code="cost_difference",
                message=message,
                destination_ids=[first.destination_id, second.destination_id],
                evidence=budget_diff.evidence,
            ))

        first_map = _metric_map(first)
        second_map = _metric_map(second)
        for name, code, label in (
            (MetricName.INTEREST_MATCH, "interest_difference", "兴趣匹配"),
            (MetricName.PACE_FIT, "pace_difference", "节奏适配"),
            (MetricName.WEATHER_SUITABILITY, "weather_difference", "天气适宜度"),
        ):
            a = first_map[name]
            b = second_map[name]
            if (
                a.evaluation_status == MetricEvaluationStatus.EVALUATED
                and b.evaluation_status == MetricEvaluationStatus.EVALUATED
            ):
                assert a.normalized_score is not None and b.normalized_score is not None
                if a.normalized_score == b.normalized_score:
                    message = (
                        f"两套方案的{label}证据得分相同，均为"
                        f" {a.normalized_score:.2f}；未人为制造差异。"
                    )
                else:
                    better = first if a.normalized_score > b.normalized_score else second
                    message = (
                        f"{better.destination_name} 的{label}规则分更高；"
                        f"两方案分别为 {a.normalized_score:.2f} 和"
                        f" {b.normalized_score:.2f}。"
                    )
                if name == MetricName.WEATHER_SUITABILITY:
                    message += (
                        "这里比较实际天气条件下的活动适配，不把城市天气差异"
                        "宣称为天气优化机制的因果效果。"
                    )
                explanations.append(ComparisonExplanation(
                    code=code,
                    message=message,
                    destination_ids=[first.destination_id, second.destination_id],
                    evidence=[*a.evidence, *b.evidence],
                ))
            else:
                explanations.append(ComparisonExplanation(
                    code=f"{code}_unavailable",
                    message=(
                        f"{label}缺少双方一致且完整的评价证据，"
                        "未填 0 分，也未据此生成优劣结论。"
                    ),
                    destination_ids=[first.destination_id, second.destination_id],
                    evidence=[*a.evidence, *b.evidence],
                ))

        explanations.append(ComparisonExplanation(
            code=f"recommendation_{recommendation.decision.value}",
            message=recommendation.reason,
            destination_ids=(
                [recommendation.recommended_destination_id]
                if recommendation.recommended_destination_id
                else recommendation.tied_destination_ids
            ),
            evidence=recommendation.evidence,
        ))
        if comparability == PlanComparability.NOT_COMPARABLE:
            explanations.append(ComparisonExplanation(
                code="comparison_limit",
                message="必要数据或状态不支持公平比较，因此只展示已知事实。",
                destination_ids=[first.destination_id, second.destination_id],
            ))
        elif recommendation.decision == RecommendationDecision.RECOMMENDED:
            explanations.append(ComparisonExplanation(
                code="conditional_tradeoff",
                message=(
                    "推荐仅在当前用户已声明偏好、硬约束、评分版本和权重下成立；"
                    "用户仍应查看各维度与完整行程后自主选择。"
                ),
                destination_ids=[first.destination_id, second.destination_id],
            ))
        return explanations
