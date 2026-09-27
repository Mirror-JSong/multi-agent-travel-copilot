"""Classic two-column Streamlit preview backed by the accepted FastAPI services.

Run from ``python/`` with::

    python -m streamlit run ui/streamlit_classic_app.py --server.port 8786

The D2/D3 workbench remains available at ``ui/streamlit_app.py``.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
from pydantic import ValidationError

from models.multi_plan import DestinationPlan, MultiPlanResult
from models.plan_evaluation import MetricName, PlanComparisonResult, RecommendationDecision
from models.schemas import (
    PlanningState,
    TravelPace,
    TravelPlanState,
    TravelStyle,
    UserPreferences,
    WeatherAvailability,
)
from ui.classic_state import (
    accept_classic_preference_response,
    apply_draft_to_form,
    clarification_answer_from_form,
    draft_matches_form,
    initialize_classic_state,
    invalidate_classic_results,
    mark_form_field_explicit,
    preference_conflicts,
)
from ui.components.plan_result import (
    SLOT_LABELS,
    WEATHER_LABELS,
    render_activity_weather_explanation,
    render_weather_status,
)
from ui.components.preferences import FIELD_LABELS, SOURCE_LABELS, display_value
from ui.preference_api_client import PreferenceApiClient, PreferenceApiError


st.set_page_config(
    page_title="智能旅游行程规划 · Classic",
    page_icon="✈️",
    layout="wide",
)


STYLE_OPTIONS = [
    "comfort",
    "budget",
    "luxury",
    "adventure",
    "cultural",
    "relaxation",
]
STYLE_LABELS = {
    "comfort": "舒适",
    "budget": "经济",
    "luxury": "豪华",
    "adventure": "探险",
    "cultural": "文化",
    "relaxation": "休闲",
}
PACE_OPTIONS = [None, "relaxed", "balanced", "packed"]
PACE_LABELS = {
    None: "未指定（保持原有行为）",
    "relaxed": "轻松",
    "balanced": "均衡",
    "packed": "紧凑",
}
INTEREST_OPTIONS = ["美食", "历史", "文化", "艺术", "自然", "购物", "摄影", "运动"]
METRIC_LABELS = {
    MetricName.BUDGET_MATCH: "预算",
    MetricName.INTEREST_MATCH: "兴趣",
    MetricName.PACE_FIT: "节奏",
    MetricName.WEATHER_SUITABILITY: "天气",
}
ENVIRONMENT_LABELS = {
    "indoor": "室内",
    "outdoor": "室外",
    "mixed": "室内外混合",
    "unknown": "环境未知",
}
INTENSITY_LABELS = {
    "low": "低强度",
    "medium": "中等强度",
    "high": "高强度",
    "unknown": "强度未知",
}
STATUS_LABELS = {
    PlanningState.COMPLETED: "已完成",
    PlanningState.BUDGET_INFEASIBLE: "预算不可满足",
    PlanningState.ACTIVITY_CONSTRAINTS_UNSATISFIED: "活动约束不可满足",
    PlanningState.PACE_CONSTRAINTS_UNSATISFIED: "节奏约束不可满足",
    PlanningState.FAILED: "规划失败",
}
DECISION_LABELS = {
    RecommendationDecision.RECOMMENDED: "推荐方案",
    RecommendationDecision.TIE: "两套方案并列",
    RecommendationDecision.ONLY_FEASIBLE: "仅一套方案可行",
    RecommendationDecision.NO_RECOMMENDATION: "当前无法推荐",
}


def _initialize() -> None:
    initialize_classic_state(st.session_state)
    default_start = date.today() + timedelta(days=30)
    defaults = {
        "classic_budget": 10000.0,
        "classic_departure_city": "北京",
        "classic_start_date": default_start,
        "classic_end_date": default_start + timedelta(days=4),
        "classic_travel_style": "comfort",
        "classic_pace": None,
        "classic_num_travelers": 1,
        "classic_interests": [],
        "classic_notes": "",
        "classic_compare": False,
        "classic_reference_date": date.today(),
        "classic_clarification_answer": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _form_values() -> dict[str, Any]:
    return {
        "budget": float(st.session_state.classic_budget),
        "departure_city": st.session_state.classic_departure_city.strip(),
        "start_date": st.session_state.classic_start_date,
        "end_date": st.session_state.classic_end_date,
        "travel_style": st.session_state.classic_travel_style,
        "pace": st.session_state.classic_pace,
        "num_travelers": int(st.session_state.classic_num_travelers),
        "interests": list(st.session_state.classic_interests),
    }


def _on_form_change(field: str) -> None:
    mark_form_field_explicit(st.session_state, field)


def _on_notes_change() -> None:
    st.session_state.classic_preference_response = None
    st.session_state.classic_preference_history = []
    invalidate_classic_results(
        st.session_state,
        reason="备注已修改，请重新识别后再确认；旧规划和评分已失效。",
        signed_draft_stale=False,
    )


def _on_mode_change() -> None:
    had_derived_state = bool(
        st.session_state.get("classic_nl_confirmed")
        or st.session_state.get("classic_plan_result") is not None
        or st.session_state.get("classic_comparison_result") is not None
    )
    invalidate_classic_results(
        st.session_state,
        reason="规划模式已修改，旧规划和评分已失效。",
        signed_draft_stale=False,
    )
    if not had_derived_state:
        st.session_state.classic_invalidation_reason = None


def _api_error_message(client: PreferenceApiClient, exc: PreferenceApiError) -> str:
    return (
        f"{exc}\n\n"
        f"请确认 FastAPI 已启动，并能访问 {client.base_url}/api/health。"
    )


def _adopt_note_values() -> None:
    response = st.session_state.get("classic_preference_response")
    if response is not None:
        apply_draft_to_form(st.session_state, response)


def _structured_preferences() -> UserPreferences:
    values = _form_values()
    return UserPreferences(
        budget=values["budget"],
        departure_city=values["departure_city"],
        start_date=values["start_date"].isoformat(),
        end_date=values["end_date"].isoformat(),
        travel_style=TravelStyle(values["travel_style"]),
        pace=TravelPace(values["pace"]) if values["pace"] else None,
        num_travelers=values["num_travelers"],
        interests=values["interests"],
        notes=st.session_state.classic_notes,
    )


def _parse_notes(client: PreferenceApiClient) -> None:
    text = st.session_state.classic_notes.strip()
    if not text:
        st.session_state.classic_last_error = "请先填写额外备注或自然语言旅行需求。"
        return
    try:
        response = client.parse(text, st.session_state.classic_reference_date)
    except PreferenceApiError as exc:
        st.session_state.classic_last_error = _api_error_message(client, exc)
        return
    accept_classic_preference_response(st.session_state, response, user_text=text)


def _clarify_notes(client: PreferenceApiClient, answer: str) -> None:
    response = st.session_state.get("classic_preference_response")
    if response is None:
        st.session_state.classic_last_error = "当前没有可继续澄清的偏好草稿。"
        return
    try:
        updated = client.clarify(
            response["draft"],
            response["draft_receipt"],
            answer,
            st.session_state.classic_reference_date,
        )
    except PreferenceApiError as exc:
        st.session_state.classic_last_error = _api_error_message(client, exc)
        return
    accept_classic_preference_response(st.session_state, updated, user_text=answer)
    # The widget may already exist in the current Streamlit run.  Clear it at
    # the beginning of the next run instead of mutating an instantiated key.
    st.session_state.classic_clear_clarification = True


def _sync_current_form(client: PreferenceApiClient) -> None:
    # "Use the form" means replacing the draft with a newly parsed, newly
    # signed canonical representation.  Clarifying the old draft would retain
    # optional fields (for example pace/interests) that the user explicitly
    # removed from the form.
    answer = clarification_answer_from_form(_form_values())
    try:
        updated = client.parse(answer, st.session_state.classic_reference_date)
    except PreferenceApiError as exc:
        st.session_state.classic_last_error = _api_error_message(client, exc)
        return
    accept_classic_preference_response(st.session_state, updated, user_text=answer)


def _render_preference_assistant(client: PreferenceApiClient) -> None:
    if st.session_state.pop("classic_clear_clarification", False):
        st.session_state.classic_clarification_answer = ""
    st.caption("当前使用确定性 Mock Parser，并非真实 LLM。默认表单值不会自动视为用户明确声明。")
    action_col, reference_col = st.columns([3, 2])
    if action_col.button(
        "识别并补全偏好",
        key="classic_parse_notes",
        use_container_width=True,
    ):
        _parse_notes(client)
    reference_col.date_input(
        "日期解析基准",
        key="classic_reference_date",
        on_change=_on_notes_change,
        help="相对日期以此日期为基准，便于复现。",
    )

    if st.session_state.classic_last_error:
        st.error(st.session_state.classic_last_error)
    if st.session_state.classic_invalidation_reason:
        st.info(st.session_state.classic_invalidation_reason)

    response = st.session_state.get("classic_preference_response")
    if response is None:
        return

    draft = response["draft"]
    sources = draft.get("field_sources", {})
    recognized_rows = [
        {
            "字段": FIELD_LABELS.get(field, field),
            "识别结果": display_value(draft.get(field)),
            "来源": SOURCE_LABELS.get(source, source),
        }
        for field, source in sources.items()
        if source != "unknown" and draft.get(field) is not None
    ]
    if recognized_rows:
        with st.expander("查看备注识别结果", expanded=True):
            st.table(recognized_rows)
        st.caption("只有上表中已识别并经服务端验证的字段会成为正式规划约束；其他备注原文会保留，但当前 Mock 能力不保证执行。")
    else:
        st.warning("当前 Mock Parser 未识别出可执行偏好；原备注仍保留，但不会假装已进入规划约束。")

    conflicts = preference_conflicts(response, _form_values())
    if conflicts:
        if st.session_state.classic_nl_stale:
            st.warning("表单已修改，旧签名草稿已经失效；必须重新验证后才能规划。")
        st.warning("备注识别结果与当前表单存在冲突，请明确选择采用哪一侧。")
        st.table(
            [
                {
                    "字段": FIELD_LABELS.get(item["field"], item["field"]),
                    "当前表单": display_value(item["form_value"]),
                    "备注识别": display_value(item["draft_value"]),
                    "识别来源": SOURCE_LABELS.get(item["source"], item["source"]),
                }
                for item in conflicts
            ]
        )
        conflict_cols = st.columns(2)
        conflict_cols[0].button(
            "采用备注识别结果",
            key="classic_adopt_notes",
            on_click=_adopt_note_values,
            use_container_width=True,
        )
        if conflict_cols[1].button(
            "以当前表单为准并重新验证",
            key="classic_keep_form",
            use_container_width=True,
        ):
            _sync_current_form(client)
            st.rerun()
        return

    if response.get("questions"):
        st.markdown("**还需要确认：**")
        for question in response["questions"]:
            st.warning(question["prompt"])
        st.text_input(
            "补充回答",
            key="classic_clarification_answer",
            placeholder="例如：一共 2 人，2026 年 10 月 1 日出发，舒适游。",
        )
        clarify_cols = st.columns(2)
        if clarify_cols[0].button(
            "提交补充",
            key="classic_submit_clarification",
            use_container_width=True,
        ):
            answer = st.session_state.classic_clarification_answer.strip()
            if answer:
                _clarify_notes(client, answer)
                st.rerun()
            else:
                st.session_state.classic_last_error = "补充回答不能为空。"
        if clarify_cols[1].button(
            "用当前表单补全并验证",
            key="classic_fill_from_form",
            use_container_width=True,
        ):
            _sync_current_form(client)
            st.rerun()
        return

    if st.session_state.classic_nl_stale:
        st.warning("表单已修改，旧签名草稿不能用于当前规划。请用当前表单重新验证。")
        if st.button(
            "用当前表单重新验证",
            key="classic_resign_form",
            use_container_width=True,
        ):
            _sync_current_form(client)
            st.rerun()
        return

    if response.get("is_complete") and draft_matches_form(response, _form_values()):
        summary = response.get("date_summary") or {}
        st.success("备注识别结果与当前表单一致，可以确认。")
        if summary:
            st.caption(
                f"活动日期：{summary['activity_start_date']} 至 {summary['activity_end_date']}；"
                f"住宿 {summary['hotel_nights']} 晚。"
            )
        if st.button(
            "确认识别后的旅行偏好",
            key="classic_confirm_natural",
            type="secondary",
            use_container_width=True,
        ):
            st.session_state.classic_nl_confirmed = True
            st.session_state.classic_invalidation_reason = None
            st.rerun()
        if st.session_state.classic_nl_confirmed:
            st.success("偏好已确认；规划时将提交服务端签名草稿。")


def _run_planning(client: PreferenceApiClient) -> None:
    response = st.session_state.get("classic_preference_response")
    compare = bool(st.session_state.classic_compare)
    try:
        if response is not None:
            if (
                not response.get("is_complete")
                or st.session_state.classic_nl_stale
                or not st.session_state.classic_nl_confirmed
                or not draft_matches_form(response, _form_values())
            ):
                st.session_state.classic_last_error = "请先完成备注澄清、冲突处理和最终确认。"
                return
            if compare:
                result = client.compare_confirmed(
                    response["draft"],
                    response["draft_receipt"],
                    st.session_state.classic_reference_date,
                )
            else:
                result = client.plan(
                    response["draft"],
                    response["draft_receipt"],
                    st.session_state.classic_reference_date,
                )
        else:
            preferences = _structured_preferences().model_dump(mode="json")
            result = (
                client.compare_structured(preferences)
                if compare
                else client.plan_structured(preferences)
            )
    except (PreferenceApiError, ValidationError) as exc:
        st.session_state.classic_last_error = (
            _api_error_message(client, exc)
            if isinstance(exc, PreferenceApiError)
            else str(exc)
        )
        return

    st.session_state.classic_last_error = None
    st.session_state.classic_invalidation_reason = None
    if compare:
        st.session_state.classic_comparison_result = result
        st.session_state.classic_plan_result = None
        multi = MultiPlanResult.model_validate(result["multi_plan"])
        st.session_state.classic_active_plan_id = (
            multi.plans[0].destination_id if multi.plans else None
        )
    else:
        st.session_state.classic_plan_result = result
        st.session_state.classic_comparison_result = None
        st.session_state.classic_active_plan_id = None


def _render_left_panel(client: PreferenceApiClient) -> None:
    st.subheader("📝 旅行偏好")
    st.number_input(
        "总预算（¥）",
        min_value=100.0,
        max_value=500000.0,
        step=100.0,
        key="classic_budget",
        on_change=_on_form_change,
        args=("budget",),
    )
    st.text_input(
        "出发城市",
        key="classic_departure_city",
        on_change=_on_form_change,
        args=("departure_city",),
    )
    date_cols = st.columns(2)
    date_cols[0].date_input(
        "出发日期",
        key="classic_start_date",
        on_change=_on_form_change,
        args=("start_date",),
    )
    date_cols[1].date_input(
        "返回日期",
        key="classic_end_date",
        on_change=_on_form_change,
        args=("end_date",),
    )
    st.selectbox(
        "旅行风格",
        STYLE_OPTIONS,
        key="classic_travel_style",
        format_func=lambda value: STYLE_LABELS[value],
        on_change=_on_form_change,
        args=("travel_style",),
    )
    st.selectbox(
        "旅行节奏",
        PACE_OPTIONS,
        key="classic_pace",
        format_func=lambda value: PACE_LABELS[value],
        on_change=_on_form_change,
        args=("pace",),
    )
    st.number_input(
        "出行人数",
        min_value=1,
        max_value=10,
        step=1,
        key="classic_num_travelers",
        on_change=_on_form_change,
        args=("num_travelers",),
    )
    st.multiselect(
        "兴趣标签",
        INTEREST_OPTIONS,
        key="classic_interests",
        on_change=_on_form_change,
        args=("interests",),
    )
    st.text_area(
        "额外备注",
        key="classic_notes",
        placeholder=(
            "可填写补充要求，或直接描述旅行需求，例如："
            "从上海出发，两人，国庆玩五天，预算一万五，不想太累，喜欢摄影和美食。"
        ),
        on_change=_on_notes_change,
    )
    _render_preference_assistant(client)
    st.checkbox(
        "生成两个可对比的旅行方案",
        key="classic_compare",
        on_change=_on_mode_change,
    )
    button_label = "生成双方案比较" if st.session_state.classic_compare else "🚀 开始规划"
    if st.button(
        button_label,
        key="classic_start_planning",
        type="primary",
        use_container_width=True,
    ):
        with st.spinner("7 个 Agent 正在协作规划；活动会等待天气状态确定后执行……"):
            _run_planning(client)
        st.rerun()


def _render_status(state: TravelPlanState) -> None:
    label = STATUS_LABELS.get(state.state, state.state.value)
    st.metric("规划状态", label)
    st.caption(f"业务状态代码：{state.state.value}")
    if state.state == PlanningState.COMPLETED:
        st.success("方案满足当前预算和必要约束。")
    elif state.state == PlanningState.BUDGET_INFEASIBLE:
        st.warning("当前分阶段搜索策略未找到预算内方案，不代表所有数学组合均无解。")
    elif state.state == PlanningState.FAILED:
        st.error("必要 Agent 执行失败，页面不会将缺失结果显示为成功。")
    elif state.state == PlanningState.ACTIVITY_CONSTRAINTS_UNSATISFIED:
        st.error("至少一个日期或时段没有天气合规的真实活动候选。")
    elif state.state == PlanningState.PACE_CONSTRAINTS_UNSATISFIED:
        st.error("候选池无法满足已确认的旅行节奏约束。")


def _render_comparison_summary(payload: Mapping[str, Any]) -> DestinationPlan | None:
    multi = MultiPlanResult.model_validate(payload["multi_plan"])
    comparison = PlanComparisonResult.model_validate(payload["comparison"])
    evaluations = {item.destination_id: item for item in comparison.plan_evaluations}
    decision = comparison.recommendation.decision
    message = f"{DECISION_LABELS[decision]}：{comparison.recommendation.reason}"
    if decision == RecommendationDecision.RECOMMENDED:
        st.success(message)
    elif decision == RecommendationDecision.TIE:
        st.info(message)
    else:
        st.warning(message)

    if not multi.plans:
        st.error("没有有效目的地方案；系统未编造第二个目的地。")
        return None

    columns = st.columns(len(multi.plans))
    for column, plan in zip(columns, multi.plans, strict=True):
        evaluation = evaluations.get(plan.destination_id)
        with column:
            with st.container(border=True):
                st.markdown(f"#### {plan.destination.city}")
                st.caption(f"{plan.destination.country} · {plan.status.value}")
                total = plan.comparison_data.final_total_cost
                st.metric("最终费用", "—" if total is None else f"¥{total:,.0f}")
                metric_map = (
                    {item.metric_name: item for item in evaluation.metric_evaluations}
                    if evaluation is not None
                    else {}
                )
                score_cols = st.columns(4)
                for score_col, metric_name in zip(score_cols, METRIC_LABELS, strict=True):
                    metric = metric_map.get(metric_name)
                    score_col.metric(
                        METRIC_LABELS[metric_name],
                        "—" if metric is None or metric.normalized_score is None else f"{metric.normalized_score:.0f}",
                    )
                if evaluation is None:
                    st.caption("无评价记录")
                else:
                    st.caption(
                        f"数据完整度 {evaluation.data_completeness.evaluated_metric_count}/"
                        f"{evaluation.data_completeness.applicable_metric_count} · "
                        f"综合分 {'—' if evaluation.overall_score is None else f'{evaluation.overall_score:.2f}'}"
                    )

    with st.expander("查看推荐解释与主要取舍"):
        for item in comparison.explanations:
            st.write(f"- {item.message}")
        if comparison.effective_weights:
            st.caption(
                "有效权重："
                + "，".join(
                    f"{METRIC_LABELS[name]} {weight:.0%}"
                    for name, weight in comparison.effective_weights.items()
                )
                + f"；策略 {comparison.scoring_policy.policy_version}"
            )
        sensitivity = payload.get("sensitivity", {})
        if sensitivity.get("notice"):
            st.caption(sensitivity["notice"])

    plan_names = {plan.destination_id: plan.destination.city for plan in multi.plans}
    plan_ids = list(plan_names)
    current = st.session_state.classic_active_plan_id
    if current not in plan_ids:
        current = plan_ids[0]
        st.session_state.classic_active_plan_id = current
    selected_id = st.radio(
        "当前查看的目的地（切换不会重新规划）",
        plan_ids,
        index=plan_ids.index(current),
        format_func=lambda value: plan_names[value],
        key="classic_destination_selector",
        horizontal=True,
    )
    st.session_state.classic_active_plan_id = selected_id
    return next(plan for plan in multi.plans if plan.destination_id == selected_id)


def _render_flights(state: TravelPlanState) -> None:
    result = state.flight_result
    if result is None:
        st.warning("没有完整航班结果；缺失费用不会按 0 元处理。")
        return
    cols = st.columns(2)
    if result.recommended_outbound:
        outbound = result.recommended_outbound
        cols[0].metric("去程推荐", f"{outbound.airline} {outbound.flight_no}", f"¥{outbound.price:.0f}/人")
        cols[0].caption(f"{outbound.departure_time} → {outbound.arrival_time} · {outbound.cabin_class}")
    if result.recommended_return:
        inbound = result.recommended_return
        cols[1].metric("返程推荐", f"{inbound.airline} {inbound.flight_no}", f"¥{inbound.price:.0f}/人")
        cols[1].caption(f"{inbound.departure_time} → {inbound.arrival_time} · {inbound.cabin_class}")
    st.write(f"**航班总费用：** ¥{result.total_flight_cost:,.0f}")


def _render_hotel(state: TravelPlanState) -> None:
    result = state.hotel_result
    if result is None or result.recommended is None:
        st.warning("没有完整酒店结果；缺失费用不会按 0 元处理。")
        return
    hotel = result.recommended
    st.metric("推荐酒店", hotel.name, f"{hotel.star_rating} 星")
    st.write(f"¥{hotel.price_per_night:,.0f}/晚 × {result.total_nights} 晚")
    st.write(f"**设施：** {', '.join(hotel.amenities)}")
    st.write(f"**酒店总费用：** ¥{result.total_hotel_cost:,.0f}")


def _weather_for_date(state: TravelPlanState, date_value: str) -> Any | None:
    if state.weather_result is None:
        return None
    return next((item for item in state.weather_result.daily_weather if item.date == date_value), None)


def _render_itinerary(state: TravelPlanState) -> None:
    render_weather_status(state)
    if state.weather_result is not None:
        source = "Mock 模拟天气" if state.weather_result.is_mock else state.weather_result.source.value
        st.caption(
            f"天气来源：{source} · {state.weather_result.source_version} · 非实时预报。"
        )
    if state.activity_result is None:
        st.warning("没有可展示的活动结果。")
        return
    for day_plan in state.activity_result.day_plans:
        weather = _weather_for_date(state, day_plan.date)
        if weather is None or weather.availability_status == WeatherAvailability.UNAVAILABLE:
            weather_text = "天气不可用，无法验证适配性"
        else:
            condition = WEATHER_LABELS.get(weather.weather_condition.value, weather.weather_condition.value)
            weather_text = f"{condition} · {weather.temperature:.0f}℃ · 降雨 {weather.rain_probability}%"
        st.markdown(f"### {day_plan.date} | {weather_text}")
        activities = {item.time_slot: item for item in day_plan.activities}
        rests = {item.time_slot: item for item in day_plan.rest_slots}
        for slot in ("morning", "afternoon", "evening"):
            activity = activities.get(slot)
            if activity is not None:
                environment = ENVIRONMENT_LABELS.get(activity.environment.value, activity.environment.value)
                intensity = INTENSITY_LABELS.get(activity.intensity.value, activity.intensity.value)
                price = "免费" if activity.price <= 0 else f"¥{activity.price:.0f}/人"
                st.write(
                    f"- **{SLOT_LABELS[slot]}：{activity.name}** · {environment} · "
                    f"{intensity} · {activity.duration_hours:g} 小时 · {price}"
                )
                if activity.pace_recommendation_reason:
                    st.caption(f"节奏依据：{activity.pace_recommendation_reason}")
                render_activity_weather_explanation(state, date_value=day_plan.date, activity=activity)
            elif slot in rests:
                st.write(f"- **{SLOT_LABELS[slot]}：休息** · ¥0")
                st.caption(rests[slot].reason)
        st.caption(f"当日活动费用：¥{day_plan.day_cost:,.0f}")
    for issue in state.activity_result.constraint_issues:
        st.error(f"{issue.date} {SLOT_LABELS.get(issue.time_slot, issue.time_slot)}：{issue.reason}")


def _render_budget(state: TravelPlanState) -> None:
    budget = state.budget_breakdown
    if budget is None:
        st.warning("没有完整预算明细；必要 Agent 缺失不会按 0 元处理。")
        return
    cols = st.columns(3)
    cols[0].metric("航班", f"¥{budget.flight_cost:,.0f}")
    cols[1].metric("酒店", f"¥{budget.hotel_cost:,.0f}")
    cols[2].metric("活动", f"¥{budget.activity_cost:,.0f}")
    st.divider()
    st.metric(
        "总计 / 预算",
        f"¥{budget.total_cost:,.0f} / ¥{budget.budget:,.0f}",
        delta=f"{'节省' if budget.remaining >= 0 else '超出'} ¥{abs(budget.remaining):,.0f}",
        delta_color="normal" if budget.remaining >= 0 else "inverse",
    )
    if state.adjustment_round:
        st.info(f"经过 {state.adjustment_round} 轮候选重选。")
    if state.adjustment_history:
        st.table(
            [
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
            ]
        )


def _render_plan_tabs(state: TravelPlanState) -> None:
    if state.selected_destination:
        destination = state.selected_destination
        st.subheader(f"🌍 目的地：{destination.city}, {destination.country}")
        st.write(destination.description)
        st.write(f"**亮点：** {', '.join(destination.highlights)}")
    _render_status(state)
    tabs = st.tabs(["✈️ 航班", "🏨 酒店", "📅 行程", "💰 预算"])
    with tabs[0]:
        _render_flights(state)
    with tabs[1]:
        _render_hotel(state)
    with tabs[2]:
        _render_itinerary(state)
    with tabs[3]:
        _render_budget(state)
    for failure in state.agent_failures:
        st.error(f"{failure.agent} [{failure.code.value}/{failure.error_type}]：{failure.reason}")
    if not state.agent_failures:
        for message in state.error_messages:
            st.warning(message)


def _render_right_panel() -> None:
    if st.session_state.classic_last_error:
        st.error(st.session_state.classic_last_error)
    comparison_payload = st.session_state.get("classic_comparison_result")
    single_payload = st.session_state.get("classic_plan_result")
    if comparison_payload is None and single_payload is None:
        st.info('请在左侧填写旅行偏好，然后点击“开始规划”。')
        st.subheader("系统协作方式")
        st.code(
            "Preference → Destination\n"
            "→ Flight + Hotel + Weather（并行）\n"
            "→ Activity（等待天气状态）\n"
            "→ Budget（固定候选重选）",
            language="text",
        )
        st.caption(
            "当前包含 7 个业务 Agent。航班、酒店、活动、天气和价格均为确定性 Mock 数据，"
            "不代表实时库存、预报或预订。"
        )
        return

    selected_plan: DestinationPlan | None = None
    if comparison_payload is not None:
        selected_plan = _render_comparison_summary(comparison_payload)
        if selected_plan is None:
            return
        state = selected_plan.plan
    else:
        payload = single_payload.get("plan", single_payload)
        state = TravelPlanState.model_validate(payload)
    _render_plan_tabs(state)
    if selected_plan is not None:
        for issue in selected_plan.business_issues:
            st.warning(f"{issue.code}：{issue.message}")


def main() -> None:
    _initialize()
    client = PreferenceApiClient(timeout_seconds=30)
    st.title("✈️ 多 Agent 智能旅游行程规划")
    st.markdown("简洁表单，天气感知行程，单方案或双目的地比较")
    st.caption("Classic UI 预览 · 7 Agent · 确定性 Mock 数据 · 非实时价格/天气 · 不代表预订")
    st.divider()
    form_col, result_col = st.columns([1, 2], gap="large")
    with form_col:
        _render_left_panel(client)
    with result_col:
        _render_right_panel()


if __name__ == "__main__":
    main()
