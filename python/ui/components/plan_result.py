"""Read-only rendering of a server-owned TravelPlanState."""

from __future__ import annotations

from typing import Any

import streamlit as st

from models.schemas import (
    PlanningState,
    TravelPlanState,
    WeatherAvailability,
    WeatherPlanningStatus,
)


WEATHER_LABELS = {
    "sunny": "晴",
    "cloudy": "多云",
    "rain": "雨",
    "heavy_rain": "强降雨",
    "thunderstorm": "雷暴",
    "snow": "雪",
}
SLOT_LABELS = {"morning": "上午", "afternoon": "下午", "evening": "晚上"}


def _weather_for_date(state: TravelPlanState, date_value: str) -> Any | None:
    if state.weather_result is None:
        return None
    return next(
        (item for item in state.weather_result.daily_weather if item.date == date_value),
        None,
    )


def render_weather_status(state: TravelPlanState) -> None:
    metadata = state.weather_planning
    status = metadata.status
    if status == WeatherPlanningStatus.READY:
        st.success("天气数据可用，活动已按天气规则评价。")
    elif status == WeatherPlanningStatus.PARTIAL:
        st.warning(f"部分天气数据不可用：{metadata.reason or '存在缺失日期。'}")
    elif status == WeatherPlanningStatus.UNAVAILABLE:
        st.warning(f"天气不可用，采用基础活动规划：{metadata.reason}")
    elif status == WeatherPlanningStatus.DEGRADED_TIMEOUT:
        st.warning(f"天气 Provider 超时，采用基础活动规划：{metadata.reason}")
    elif status in {
        WeatherPlanningStatus.FAILED_DATA,
        WeatherPlanningStatus.FAILED_INTERNAL,
    }:
        st.error(f"天气处理失败：{metadata.reason or '天气契约异常。'}")


def render_daily_weather(state: TravelPlanState) -> None:
    render_weather_status(state)
    result = state.weather_result
    if result is None:
        st.info("没有可展示的每日天气记录；系统不会将未知天气显示为晴天。")
        return

    source_label = "Mock 模拟天气" if result.is_mock else result.source.value
    st.caption(
        f"数据来源：{source_label} · 版本 {result.source_version} · "
        "仅用于产品演示，不是实时天气预报。"
    )
    for daily in result.daily_weather:
        if daily.availability_status == WeatherAvailability.UNAVAILABLE:
            st.markdown(f"#### {daily.date}")
            st.warning("该日天气数据不可用；未伪造天气条件。")
            continue
        condition = WEATHER_LABELS.get(
            daily.weather_condition.value,
            daily.weather_condition.value,
        )
        risk = "恶劣天气风险" if daily.severe_weather else "未标记恶劣天气"
        st.markdown(f"#### {daily.date} · {condition}")
        c1, c2, c3 = st.columns(3)
        c1.metric("降雨概率", f"{daily.rain_probability}%")
        c2.metric("温度", f"{daily.temperature:.1f}℃")
        c3.metric("天气风险", risk)


def render_activity_weather_explanation(
    state: TravelPlanState,
    *,
    date_value: str,
    activity: Any,
) -> None:
    result = state.activity_result
    if result is None:
        return
    decision = next(
        (
            item
            for item in result.weather_decisions
            if item.date == date_value and item.time_slot == activity.time_slot
        ),
        None,
    )
    evaluation = next(
        (
            item
            for item in reversed(result.weather_evaluations)
            if item.date == date_value
            and item.time_slot == activity.time_slot
            and item.candidate_id == activity.candidate_id
        ),
        None,
    )

    if decision is not None and decision.weather_driven_change:
        st.info(
            "天气驱动变化："
            f"基础选择“{decision.baseline_candidate_name}” → "
            f"天气方案“{decision.initial_selected_candidate_name}”。"
            f" {decision.reason}"
        )
    elif decision is not None and decision.decision_type.value == "weather_rule_selection":
        st.caption(f"天气参与评价，但未发生相对基础方案替换。{decision.reason}")
    elif decision is not None and decision.decision_type.value == "baseline_selection":
        st.caption("控制组基础选择：天气结果仅用于事后评价，未参与排序。")
    elif decision is not None and decision.decision_type.value == "weather_unavailable_fallback":
        st.caption("天气未知：采用基础选择，不能声明该活动已经天气验证。")

    if activity.recommendation_reason:
        st.caption(f"天气适配依据：{activity.recommendation_reason}")
    if evaluation is not None:
        st.caption(
            "评分依据："
            f"基础 {evaluation.activity_rating:.1f} + "
            f"兴趣 {evaluation.interest_score:.1f} + "
            f"天气加权 {evaluation.weighted_weather_score:.1f} = "
            f"{evaluation.total_selection_score:.1f}"
        )
    if decision is not None and decision.budget_adjusted:
        st.caption(
            f"预算重选：{decision.initial_selected_candidate_name} → "
            f"{decision.final_candidate_name}；{decision.budget_reason}"
        )


def _render_itinerary(state: TravelPlanState) -> None:
    if state.activity_result is None:
        st.info("没有可展示的活动结果。")
        return
    for day_plan in state.activity_result.day_plans:
        weather = _weather_for_date(state, day_plan.date)
        if weather is None or weather.availability_status == WeatherAvailability.UNAVAILABLE:
            weather_text = "天气不可用"
        else:
            condition = WEATHER_LABELS.get(
                weather.weather_condition.value,
                weather.weather_condition.value,
            )
            weather_text = f"{condition} · {weather.temperature:.0f}℃ · 降雨 {weather.rain_probability}%"
        st.markdown(f"### {day_plan.date} · {weather_text} · ¥{day_plan.day_cost:.0f}")
        activities_by_slot = {
            activity.time_slot: activity for activity in day_plan.activities
        }
        rests_by_slot = {rest.time_slot: rest for rest in day_plan.rest_slots}
        for slot in ("morning", "afternoon", "evening"):
            activity = activities_by_slot.get(slot)
            if activity is not None:
                price = f"¥{activity.price:.0f}" if activity.price > 0 else "免费"
                st.write(
                    f"- **[{SLOT_LABELS[slot]}]** {activity.name} "
                    f"({activity.duration_hours}h / {activity.intensity.value}) {price}"
                )
                if activity.pace_recommendation_reason:
                    st.caption(f"节奏依据：{activity.pace_recommendation_reason}")
                render_activity_weather_explanation(
                    state,
                    date_value=day_plan.date,
                    activity=activity,
                )
            elif slot in rests_by_slot:
                st.write(f"- **[{SLOT_LABELS[slot]}]** 休息（¥0）")
                st.caption(rests_by_slot[slot].reason)

    if state.activity_result.constraint_issues:
        st.markdown("#### 未满足的活动约束")
        for issue in state.activity_result.constraint_issues:
            st.error(
                f"{issue.date} {SLOT_LABELS.get(issue.time_slot, issue.time_slot)}："
                f"{issue.reason}"
            )

    excluded_rows = []
    candidate_names = {
        item.candidate_id: item.name
        for item in state.activity_result.activity_candidates
    }
    evaluation_lookup = {
        (item.date, item.time_slot, item.candidate_id): item
        for item in state.activity_result.weather_evaluations
    }
    for decision in state.activity_result.weather_decisions:
        for candidate_id in decision.hard_excluded_candidate_ids:
            evaluation = evaluation_lookup.get(
                (decision.date, decision.time_slot, candidate_id)
            )
            excluded_rows.append({
                "日期": decision.date,
                "时段": SLOT_LABELS.get(decision.time_slot, decision.time_slot),
                "被排除候选": candidate_names.get(candidate_id, candidate_id),
                "原因": evaluation.reason if evaluation else "天气硬约束不满足",
            })
    if excluded_rows:
        with st.expander("查看因天气硬约束排除的候选"):
            st.table(excluded_rows)


def _render_transport_and_hotel(state: TravelPlanState) -> None:
    flight_col, hotel_col = st.columns(2)
    with flight_col:
        st.markdown("#### 往返航班")
        if state.flight_result:
            result = state.flight_result
            if result.recommended_outbound:
                outbound = result.recommended_outbound
                st.metric("去程推荐", f"{outbound.airline} {outbound.flight_no}", f"¥{outbound.price:.0f}")
            if result.recommended_return:
                inbound = result.recommended_return
                st.metric("返程推荐", f"{inbound.airline} {inbound.flight_no}", f"¥{inbound.price:.0f}")
            st.write(f"**航班总费用：** ¥{result.total_flight_cost:.0f}")
        else:
            st.info("没有完整航班结果。")
    with hotel_col:
        st.markdown("#### 住宿")
        if state.hotel_result and state.hotel_result.recommended:
            hotel = state.hotel_result.recommended
            st.metric("推荐酒店", hotel.name, f"{hotel.star_rating} 星")
            st.write(f"¥{hotel.price_per_night:.0f}/晚 × {state.hotel_result.total_nights} 晚")
            st.write(f"**设施：** {', '.join(hotel.amenities)}")
            st.write(f"**酒店总费用：** ¥{state.hotel_result.total_hotel_cost:.0f}")
        else:
            st.info("没有完整酒店结果。")


def _render_budget(state: TravelPlanState) -> None:
    if state.budget_breakdown is None:
        st.warning("没有完整预算明细；缺失 Agent 结果不会按 0 元处理。")
        return
    budget = state.budget_breakdown
    c1, c2, c3 = st.columns(3)
    c1.metric("航班", f"¥{budget.flight_cost:.0f}")
    c2.metric("酒店", f"¥{budget.hotel_cost:.0f}")
    c3.metric("活动", f"¥{budget.activity_cost:.0f}")
    st.divider()
    st.metric(
        "总计 / 预算",
        f"¥{budget.total_cost:.0f} / ¥{budget.budget:.0f}",
        delta=f"{'节省' if budget.remaining >= 0 else '超出'} ¥{abs(budget.remaining):.0f}",
        delta_color="normal" if budget.remaining >= 0 else "inverse",
    )
    if state.adjustment_round > 0:
        st.info(f"经过 {state.adjustment_round} 轮预算调整")
    if not state.adjustment_history:
        return
    st.markdown("#### 预算调整历史")
    st.table([
        {
            "轮次": item.round_number,
            "调整对象": item.target.value,
            "调整前": f"¥{item.before_cost:.2f}",
            "调整后": f"¥{item.after_cost:.2f}",
            "节省": f"¥{item.saved_amount:.2f}",
            "当前总费用": f"¥{item.current_total:.2f}",
            "有效调整": "是" if item.improved else "否",
        }
        for item in state.adjustment_history
    ])
    replacement_reasons = [
        {
            "日期": replacement.date,
            "时段": SLOT_LABELS.get(replacement.time_slot, replacement.time_slot),
            "调整": f"{replacement.before_candidate_id} → {replacement.after_candidate_id}",
            "天气/选择依据": replacement.selection_reason,
        }
        for item in state.adjustment_history
        for replacement in item.activity_replacements
    ]
    if replacement_reasons:
        st.markdown("#### 活动预算重选依据")
        st.table(replacement_reasons)


def render_plan_result(state: TravelPlanState) -> None:
    status_labels = {
        PlanningState.COMPLETED: "COMPLETED",
        PlanningState.BUDGET_INFEASIBLE: "预算不可满足",
        PlanningState.ACTIVITY_CONSTRAINTS_UNSATISFIED: "活动约束不可满足",
        PlanningState.PACE_CONSTRAINTS_UNSATISFIED: "节奏约束不可满足",
        PlanningState.FAILED: "FAILED",
    }
    status_text = status_labels.get(state.state, state.state.value)
    st.metric("当前规划状态", status_text)
    st.caption(f"业务状态代码：{state.state.value}")
    if state.state == PlanningState.COMPLETED:
        st.success("行程规划完成，方案满足当前预算和必要约束。")
    elif state.state == PlanningState.BUDGET_INFEASIBLE:
        st.warning(
            "规划已执行完成，但当前分阶段搜索策略未找到预算内方案；"
            "这不代表所有可能组合均无解。"
        )
    elif state.state == PlanningState.FAILED:
        st.error("必要 Agent 或内部执行失败，未生成可声明为完整成功的方案。")
    elif state.state == PlanningState.ACTIVITY_CONSTRAINTS_UNSATISFIED:
        st.error("至少一个日期或时段没有天气合规的真实活动，系统未编造完整行程。")
    elif state.state == PlanningState.PACE_CONSTRAINTS_UNSATISFIED:
        st.error("候选池无法满足已确认的旅行节奏约束，系统未放宽约束或编造活动。")

    if state.preferences:
        pace_value = state.preferences.pace.value if state.preferences.pace else "未指定（兼容旧行为）"
        st.caption(f"旅行节奏：{pace_value}")

    if state.selected_destination:
        destination = state.selected_destination
        st.subheader(f"目的地：{destination.city}, {destination.country}")
        st.write(destination.description)
        st.write(f"**亮点：** {', '.join(destination.highlights)}")

    tab1, tab2, tab3, tab4 = st.tabs(["每日行程", "交通与住宿", "天气", "预算与调整"])
    with tab1:
        _render_itinerary(state)
    with tab2:
        _render_transport_and_hotel(state)
    with tab3:
        render_daily_weather(state)
    with tab4:
        _render_budget(state)

    if state.agent_failures:
        for failure in state.agent_failures:
            st.error(
                f"{failure.agent} [{failure.code.value}/{failure.error_type}]："
                f"{failure.reason}"
            )
    elif state.error_messages:
        for message in state.error_messages:
            st.warning(message)
