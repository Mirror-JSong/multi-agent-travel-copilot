"""Dual-plan comparison cards and server-owned explanation rendering."""

from __future__ import annotations

from typing import Any, MutableMapping

import streamlit as st

from models.multi_plan import DestinationPlan, MultiPlanResult
from models.plan_evaluation import (
    MetricName,
    PlanComparisonResult,
    RecommendationDecision,
)
from models.schemas import PlanningState, UserPreferences
from ui.components.status import render_status_banner
from ui.workbench_state import set_ui_step


METRIC_LABELS = {
    MetricName.BUDGET_MATCH: "预算匹配",
    MetricName.INTEREST_MATCH: "兴趣匹配",
    MetricName.PACE_FIT: "节奏适配",
    MetricName.WEATHER_SUITABILITY: "天气适宜度",
}


def parse_comparison_payload(
    payload: dict[str, Any],
) -> tuple[MultiPlanResult, PlanComparisonResult, UserPreferences]:
    return (
        MultiPlanResult.model_validate(payload["multi_plan"]),
        PlanComparisonResult.model_validate(payload["comparison"]),
        UserPreferences.model_validate(payload["preferences"]),
    )


def _render_preference_summary(preferences: UserPreferences, payload: dict[str, Any]) -> None:
    st.markdown("### 用户偏好摘要")
    first, second = st.columns(2)
    first.write(
        f"**出发：** {preferences.departure_city}　"
        f"**日期：** {preferences.start_date} → {preferences.end_date}"
    )
    first.write(
        f"**人数：** {preferences.num_travelers}　"
        f"**总预算：** ¥{preferences.budget:,.2f}（每个独立方案）"
    )
    second.write(
        f"**旅行风格：** {preferences.travel_style.value}　"
        f"**旅行节奏：** {preferences.pace.value if preferences.pace else '未指定'}"
    )
    second.write(
        "**兴趣：** " + ("、".join(preferences.interests) if preferences.interests else "未声明")
    )
    st.caption(payload.get("mock_notice", ""))


def _render_metric_rows(evaluation: Any) -> None:
    metric_map = {item.metric_name: item for item in evaluation.metric_evaluations}
    for name in (
        MetricName.BUDGET_MATCH,
        MetricName.INTEREST_MATCH,
        MetricName.PACE_FIT,
        MetricName.WEATHER_SUITABILITY,
    ):
        metric = metric_map[name]
        value = (
            f"{metric.normalized_score:.2f}"
            if metric.normalized_score is not None
            else "—"
        )
        st.metric(METRIC_LABELS[name], value)
        if metric.normalized_score is None:
            st.caption(f"{metric.evaluation_status.value}：{metric.explanation}")


def _render_plan_card(
    plan: DestinationPlan,
    evaluation: Any | None,
    *,
    session: MutableMapping[str, Any] | None,
) -> None:
    st.markdown(f"### {plan.destination.city}")
    st.caption(f"{plan.destination.country} · 独立 TravelPlanState")
    st.metric("规划业务状态", plan.status.value)
    total = plan.comparison_data.final_total_cost
    remaining = plan.comparison_data.budget - total if total is not None else None
    cost_col, budget_col = st.columns(2)
    cost_col.metric("最终总费用", "—" if total is None else f"¥{total:,.2f}")
    budget_col.metric("预算余量", "—" if remaining is None else f"¥{remaining:,.2f}")
    if evaluation is None:
        st.warning("该方案没有评价记录，不能显示综合分。")
    else:
        _render_metric_rows(evaluation)
        st.metric(
            "综合评价",
            "—" if evaluation.overall_score is None else f"{evaluation.overall_score:.2f}",
        )
        if evaluation.overall_score is None:
            st.caption(
                f"评价资格：{evaluation.evaluation_eligibility.value}；未显示虚假综合分。"
            )
    if session is not None and st.button(
        "查看每日行程",
        key=f"comparison_view_{plan.destination_id}",
        use_container_width=True,
    ):
        session["active_plan_id"] = plan.destination_id
        set_ui_step(session, "detail")
        st.rerun()


def _render_recommendation(comparison: PlanComparisonResult) -> None:
    decision = comparison.recommendation.decision
    title_map = {
        RecommendationDecision.RECOMMENDED: "条件化推荐",
        RecommendationDecision.TIE: "两套方案并列",
        RecommendationDecision.ONLY_FEASIBLE: "仅一套方案完整可行",
        RecommendationDecision.NO_RECOMMENDATION: "当前无法综合推荐",
    }
    tone_map = {
        RecommendationDecision.RECOMMENDED: "success",
        RecommendationDecision.TIE: "info",
        RecommendationDecision.ONLY_FEASIBLE: "warning",
        RecommendationDecision.NO_RECOMMENDATION: "warning",
    }
    render_status_banner(
        title_map[decision],
        comparison.recommendation.reason,
        tone=tone_map[decision],
    )


def render_comparison_result(
    payload: dict[str, Any],
    *,
    session: MutableMapping[str, Any] | None = None,
) -> None:
    """Render C1/C2 facts without recomputing scores or recommendation."""
    multi_plan, comparison, preferences = parse_comparison_payload(payload)
    evaluations = {
        item.destination_id: item for item in comparison.plan_evaluations
    }

    _render_preference_summary(preferences, payload)
    _render_recommendation(comparison)
    if not multi_plan.plans:
        st.error("没有有效目的地方案；系统未编造第二个目的地。")
        return

    st.markdown("### 双方案对比")
    columns = st.columns(max(1, len(multi_plan.plans)))
    for column, plan in zip(columns, multi_plan.plans, strict=True):
        with column:
            with st.container(border=True):
                _render_plan_card(
                    plan,
                    evaluations.get(plan.destination_id),
                    session=session,
                )

    st.caption(
        f"比较状态：{comparison.comparability.value}；"
        f"请求状态：{payload.get('request_status', 'unknown')}"
    )

    explanation_col, weight_col = st.columns([3, 2])
    with explanation_col:
        st.markdown("### 可追溯解释")
        for explanation in comparison.explanations:
            st.write(f"- **{explanation.code}：** {explanation.message}")
        evidence_rows = [
            {
                "解释": explanation.code,
                "字段路径": evidence.field_path,
                "候选 ID": "、".join(evidence.candidate_ids) or "—",
                "证据值": evidence.value_json,
            }
            for explanation in comparison.explanations
            for evidence in explanation.evidence
        ]
        if evidence_rows:
            with st.expander("查看解释证据链"):
                st.table(evidence_rows)
    with weight_col:
        st.markdown("### 权重与局限")
        if comparison.effective_weights:
            st.table([
                {
                    "评价维度": METRIC_LABELS[name],
                    "有效权重": f"{weight:.1%}",
                    "策略": comparison.scoring_policy.policy_version,
                }
                for name, weight in comparison.effective_weights.items()
            ])
        else:
            st.info(
                "当前结果没有可公平比较的有效权重；"
                f"策略版本 {comparison.scoring_policy.policy_version}。"
            )
        sensitivity = payload.get("sensitivity", {})
        if sensitivity.get("decision_changed"):
            st.warning(sensitivity.get("notice", ""))
        else:
            st.info(sensitivity.get("notice", ""))
        for limitation in comparison.limitations:
            st.caption(f"限制：{limitation}")

    completed_ids = [
        plan.destination_id
        for plan in multi_plan.plans
        if plan.status == PlanningState.COMPLETED
    ]
    if completed_ids:
        names = {plan.destination_id: plan.destination.city for plan in multi_plan.plans}
        selected = st.radio(
            "我希望进一步查看的方案（仅产品内选择，不代表预订）",
            completed_ids,
            format_func=lambda value: names[value],
            key=f"comparison_user_choice_{multi_plan.request_id}",
        )
        if session is not None:
            session["active_plan_id"] = selected
    else:
        st.warning("当前没有完整可行方案可供选择，但仍可查看真实失败或约束信息。")


def select_destination_plan(
    payload: dict[str, Any],
    destination_id: str | None,
) -> DestinationPlan | None:
    multi_plan = MultiPlanResult.model_validate(payload["multi_plan"])
    if not multi_plan.plans:
        return None
    return next(
        (plan for plan in multi_plan.plans if plan.destination_id == destination_id),
        multi_plan.plans[0],
    )
