"""Five-step Streamlit workbench backed by the existing FastAPI services.

Run from ``python`` with::

    streamlit run ui/streamlit_app.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
from pydantic import ValidationError

from models.schemas import TravelPace, TravelPlanState, TravelStyle, UserPreferences
from ui.components.comparison import (
    parse_comparison_payload,
    render_comparison_result,
    select_destination_plan,
)
from ui.components.navigation import render_page_header, render_route_navigation
from ui.components.plan_result import render_plan_result
from ui.components.preferences import (
    render_confirmation_card,
    render_draft_dossier,
    render_history,
    render_questions,
)
from ui.components.status import (
    render_planning_loading,
    render_retryable_error,
    render_status_banner,
)
from ui.preference_api_client import (
    PreferenceApiClient,
    PreferenceApiError,
    PreferenceApiTimeout,
)
from ui.preference_session import (
    accept_preference_response,
    initialize_preference_session,
    reset_preference_session,
)
from ui.theme import apply_workbench_theme
from ui.weather_demo_client import WeatherDemoApiClient, WeatherDemoApiError
from ui.workbench_state import (
    clear_transport_error,
    initialize_workbench_state,
    invalidate_planning_results,
    invalidate_structured_results,
    record_transport_error,
    reset_workbench_state,
    set_ui_step,
)


def _initialize_session() -> None:
    initialize_preference_session(
        st.session_state,
        default_reference_date=date.today(),
    )
    initialize_workbench_state(st.session_state)


def _reset_current_trip() -> None:
    reset_preference_session(
        st.session_state,
        default_reference_date=date.today(),
    )
    reset_workbench_state(st.session_state)


def _accept_response(response: dict[str, Any], *, user_text: str) -> None:
    accept_preference_response(
        st.session_state,
        response,
        user_text=user_text,
    )
    clear_transport_error(st.session_state)
    set_ui_step(
        st.session_state,
        "confirmation" if response.get("is_complete") else "intake",
    )


def _render_input_mode_switch() -> str:
    modes = ["自然语言澄清", "结构化表单"]
    if os.getenv("ENABLE_WEATHER_DEMO_CONTROLS", "0") == "1":
        modes.append("天气验收演示（固定 Mock）")
    mode = st.radio(
        "输入方式",
        modes,
        horizontal=True,
        key="input_mode",
    )
    previous = st.session_state.get("ui_input_mode")
    if previous != mode:
        st.session_state.ui_input_mode = mode
        set_ui_step(st.session_state, "intake")
    return mode


def _render_natural_intake() -> None:
    render_page_header(
        step_number=1,
        section="需求对话",
        title="TravelMate 智能旅行助手",
        description=(
            "每一次旅行，都值得精心规划。"
            "系统会继续追问，不会用默认值替你决定~"
        ),
    )
    st.info("当前使用确定性 Mock Parser，并非真实 LLM；仅支持已声明的规则和测试语料。")

    response = st.session_state.preference_response
    top_left, top_right = st.columns([4, 1])
    with top_left:
        if response is not None and st.session_state.preference_reference_date_locked:
            st.session_state.preference_reference_date = (
                st.session_state.preference_reference_date_locked
            )
        reference_date = st.date_input(
            "日期解析基准",
            key="preference_reference_date",
            disabled=response is not None,
            help="“明天/后天”等相对日期均以此日期为基准；会话开始后保持不变。",
        )
    with top_right:
        st.write("")
        st.write("")
        if st.button("重新开始", key="reset_preference", use_container_width=True):
            _reset_current_trip()
            st.rerun()

    dialogue_col, summary_col = st.columns([2, 1], gap="large")
    with dialogue_col:
        with st.container(border=True):
            st.markdown("### 告诉我你的旅行需求")
            st.caption("确定出行日期后，多轮回答会合并整理到一份旅行计划里哦~")
            if response is not None:
                render_history(st.session_state.preference_history)
            natural_text = st.text_area(
                "自然语言旅行需求",
                placeholder="例：国庆想和朋友从上海出去玩五天，预算一万五，不想太累，喜欢拍照和吃东西。",
                key="natural_requirement",
                disabled=response is not None,
            )
            if st.button(
                "开始计划！",
                key="parse_preference",
                type="primary",
                disabled=response is not None,
            ):
                if not natural_text.strip():
                    st.error("请输入你的旅行需求!")
                else:
                    try:
                        parsed = PreferenceApiClient().parse(natural_text, reference_date)
                    except PreferenceApiTimeout as exc:
                        record_transport_error(st.session_state, str(exc))
                        st.error(str(exc))
                    except PreferenceApiError as exc:
                        record_transport_error(st.session_state, str(exc))
                        st.error(str(exc))
                    else:
                        st.session_state.preference_reference_date_locked = reference_date
                        _accept_response(parsed, user_text=natural_text)
                        st.rerun()

            if response is not None:
                render_questions(response)
                answer = st.text_area(
                    "补充回答或修改已填写的信息",
                    placeholder="例：我们一共3人，2026年10月1日出发，舒适游；或“预算改成两万”。",
                    key="clarification_answer",
                )
                if st.button("提交补充信息", key="clarify_preference", type="primary"):
                    if not answer.strip():
                        st.error("请输入补充信息或修改已填写的内容。")
                    else:
                        try:
                            updated = PreferenceApiClient().clarify(
                                response["draft"],
                                response["draft_receipt"],
                                answer,
                                st.session_state.preference_reference_date_locked
                                or st.session_state.preference_reference_date,
                            )
                        except PreferenceApiTimeout as exc:
                            record_transport_error(st.session_state, str(exc))
                            st.error(str(exc))
                        except PreferenceApiError as exc:
                            record_transport_error(st.session_state, str(exc))
                            st.error(str(exc))
                        else:
                            _accept_response(updated, user_text=answer)
                            st.rerun()

    with summary_col:
        if response is None:
            st.markdown(
                '<div class="travel-card"><h3>实时需求提取</h3>'
                '<p class="travel-kicker">提交信息后，这里会显示字段值、来源和缺失信息。</p>'
                '<div class="travel-status-banner info"><strong>尚未开始</strong>'
                '<span>未知信息不会被默认值掩盖。</span></div></div>',
                unsafe_allow_html=True,
            )
        else:
            render_draft_dossier(response, compact=True)
            with st.expander("查看全部识别字段与来源"):
                render_draft_dossier(response)
            if response.get("is_complete"):
                render_status_banner(
                    "必要信息已完整",
                    "仍需在下一步核对日期并且进行确认。",
                    tone="success",
                )
            else:
                st.warning("必要信息尚未填写完整，当前不能确认需求或进入正式规划。")

    if response is not None and not response.get("is_complete"):
        st.button(
            "确认并生成旅行方案",
            key="confirm_preference",
            type="primary",
            disabled=True,
        )


def _execute_confirmed_preference_plan(response: dict[str, Any], mode: str) -> None:
    """Queue one confirmed request so it executes on the real planning page."""
    st.session_state.preference_planning = True
    st.session_state.planning_request_state = "submitting"
    st.session_state.pending_planning_request = {
        "source": "preference",
        "mode": mode,
    }
    st.session_state.ui_last_error = None
    set_ui_step(st.session_state, "planning")
    st.rerun()


def _perform_pending_planning_request(request: dict[str, Any]) -> None:
    """Execute one queued API request after the planning page is visible."""
    source = request["source"]
    mode = request["mode"]
    response = st.session_state.preference_response
    try:
        if source == "preference":
            if response is None:
                raise PreferenceApiError("已确认偏好不存在，请返回重新确认。")
            if mode == "compare":
                planned = PreferenceApiClient().compare_confirmed(
                    response["draft"],
                    response["draft_receipt"],
                    st.session_state.preference_reference_date_locked
                    or st.session_state.preference_reference_date,
                )
            else:
                planned = PreferenceApiClient().plan(
                    response["draft"],
                    response["draft_receipt"],
                    st.session_state.preference_reference_date_locked
                    or st.session_state.preference_reference_date,
                )
        else:
            preferences = UserPreferences.model_validate(request["preferences"])
            if mode == "compare":
                planned = PreferenceApiClient().compare_structured(
                    preferences.model_dump(mode="json")
                )
            else:
                planned = PreferenceApiClient().plan_structured(
                    preferences.model_dump(mode="json")
                )
    except (PreferenceApiTimeout, PreferenceApiError) as exc:
        if source == "preference":
            st.session_state.preference_confirmed = False
            set_ui_step(st.session_state, "confirmation")
        elif mode == "compare":
            st.session_state.structured_comparison_result = request.get("previous")
            set_ui_step(st.session_state, "intake")
        else:
            st.session_state.structured_plan_result = request.get("previous")
            set_ui_step(st.session_state, "intake")
        record_transport_error(st.session_state, str(exc))
    else:
        clear_transport_error(st.session_state)
        if source == "preference":
            if mode == "compare":
                st.session_state.preference_comparison_result = planned
                st.session_state.preference_plan_result = None
                set_ui_step(st.session_state, "comparison")
            else:
                st.session_state.preference_plan_result = planned
                st.session_state.preference_comparison_result = None
                set_ui_step(st.session_state, "detail")
            st.session_state.preference_confirmed = True
        else:
            if mode == "compare":
                st.session_state.structured_comparison_result = planned
                st.session_state.structured_comparison_fingerprint = request["fingerprint"]
                st.session_state.structured_plan_result = None
                set_ui_step(st.session_state, "comparison")
            else:
                st.session_state.structured_plan_result = planned
                st.session_state.structured_plan_fingerprint = request["fingerprint"]
                st.session_state.structured_comparison_result = None
                set_ui_step(st.session_state, "detail")
        st.session_state.planning_request_state = "success"
    finally:
        st.session_state.preference_planning = False
        st.session_state.pending_planning_request = None
    st.rerun()


def _render_natural_confirmation() -> None:
    response = st.session_state.preference_response
    if response is None or not response.get("is_complete"):
        set_ui_step(st.session_state, "intake")
        st.warning("必要信息尚未完整，不能进入偏好确认。")
        return
    render_page_header(
        step_number=2,
        section="偏好确认",
        title="确认以后，再开始规划",
        description=(
            "这里显示实际进入 UserPreferences 的内容。活动安排到返程日前一天，"
            "住宿晚数等于出发日与返程日之间的日期差。"
        ),
    )
    if st.session_state.ui_last_error:
        render_retryable_error(st.session_state.ui_last_error)
    date_col, mode_col = st.columns([3, 2], gap="large")
    with date_col:
        with st.container(border=True):
            render_confirmation_card(response)
            st.info("信息完整后才允许进入正式规划；服务端仍会重新验证草稿和 HMAC 回执。")
    with mode_col:
        with st.container(border=True):
            st.markdown("### 选择规划模式")
            st.caption("两个目的地方案分别使用完整用户预算，不会把预算拆成两份。")
            planning_mode = st.radio(
                "规划模式",
                ["single", "compare"],
                key="preference_planning_mode",
                horizontal=True,
                format_func=lambda value: "单方案规划" if value == "single" else "双方案比较",
                disabled=(
                    st.session_state.preference_plan_result is not None
                    or st.session_state.preference_comparison_result is not None
                ),
            )
            st.caption("当前为 Mock 规划；确认动作不代表身份认证、航班预订或酒店预订。")
            if st.button("返回修改偏好", key="edit_preference", use_container_width=True):
                set_ui_step(st.session_state, "intake")
                st.rerun()
            if st.button(
                "确认并生成旅行方案" if planning_mode == "single" else "确认并生成双方案比较",
                key="confirm_preference",
                type="primary",
                disabled=(
                    not response.get("can_confirm", False)
                    or st.session_state.preference_planning
                    or st.session_state.preference_plan_result is not None
                    or st.session_state.preference_comparison_result is not None
                ),
                use_container_width=True,
            ):
                _execute_confirmed_preference_plan(response, planning_mode)

    with st.expander("修改已识别信息"):
        st.caption("修改会通过既有澄清接口生成新草稿和新回执；旧规划结果不会继续沿用。")
        answer = st.text_area(
            "补充回答或修改已识别信息",
            placeholder="例：预算改成两万；或返程日期改为2026年10月7日。",
            key="clarification_answer",
        )
        if st.button("提交补充信息", key="clarify_preference", type="primary"):
            if not answer.strip():
                st.error("请输入补充回答或修改内容。")
            else:
                try:
                    updated = PreferenceApiClient().clarify(
                        response["draft"],
                        response["draft_receipt"],
                        answer,
                        st.session_state.preference_reference_date_locked
                        or st.session_state.preference_reference_date,
                    )
                except (PreferenceApiTimeout, PreferenceApiError) as exc:
                    record_transport_error(st.session_state, str(exc))
                    st.error(str(exc))
                else:
                    _accept_response(updated, user_text=answer)
                    st.rerun()


def _render_planning_page() -> None:
    render_page_header(
        step_number=3,
        section="规划执行",
        title="正在生成可核对的旅行方案",
        description=(
            "后端当前返回整体响应，没有细粒度进度事件。这里展示真实整体加载或错误状态，"
            "不会用计时器伪造 Agent 的完成百分比。"
        ),
    )
    if st.session_state.ui_last_error:
        render_retryable_error(st.session_state.ui_last_error)
        response = st.session_state.preference_response
        if response and response.get("can_confirm"):
            mode = st.session_state.preference_planning_mode
            if st.button(
                "使用已确认偏好重试",
                key="confirm_preference",
                type="primary",
            ):
                _execute_confirmed_preference_plan(response, mode)
        return_label = "返回核对偏好" if response else "返回结构化表单"
        if st.button(return_label, key="return_confirmation"):
            set_ui_step(
                st.session_state,
                "confirmation" if response and response.get("is_complete") else "intake",
            )
            st.rerun()
        return
    render_planning_loading(
        comparison=(
            st.session_state.get("preference_planning_mode") == "compare"
            or st.session_state.get("structured_planning_mode") == "compare"
        )
    )
    pending = st.session_state.pending_planning_request
    if pending is not None:
        _perform_pending_planning_request(pending)


def _structured_preferences() -> tuple[UserPreferences | None, ValidationError | None]:
    try:
        return UserPreferences(
            budget=float(st.session_state.structured_budget),
            travel_style=TravelStyle(st.session_state.structured_style),
            pace=(
                TravelPace(st.session_state.structured_pace)
                if st.session_state.structured_pace is not None
                else None
            ),
            departure_city=st.session_state.structured_departure,
            start_date=st.session_state.structured_start_date.strftime("%Y-%m-%d"),
            end_date=st.session_state.structured_end_date.strftime("%Y-%m-%d"),
            num_travelers=int(st.session_state.structured_travelers),
            interests=st.session_state.structured_interests,
            notes=st.session_state.structured_notes,
        ), None
    except ValidationError as exc:
        return None, exc


def _render_structured_intake() -> None:
    render_page_header(
        step_number=1,
        section="直接输入",
        title="直接填写已确定的旅行需求",
        description="本表单与对话入口使用同一份 UserPreferences、Provider 和规划规则。",
    )
    form_col, guide_col = st.columns([3, 2], gap="large")
    default_start = date.today() + timedelta(days=30)
    with form_col:
        with st.container(border=True):
            st.markdown("### 旅行偏好")
            st.number_input(
                "总预算（¥）", min_value=100, max_value=500000, value=10000, step=100,
                key="structured_budget",
            )
            st.text_input("出发城市", value="北京", key="structured_departure")
            dates = st.columns(2)
            dates[0].date_input("出发日期", value=default_start, key="structured_start_date")
            dates[1].date_input(
                "返回日期", value=default_start + timedelta(days=4), key="structured_end_date"
            )
            st.selectbox(
                "旅行风格",
                ["comfort", "budget", "luxury", "adventure", "cultural", "relaxation"],
                key="structured_style",
                format_func=lambda value: {
                    "comfort": "舒适", "budget": "经济", "luxury": "豪华",
                    "adventure": "探险", "cultural": "文化", "relaxation": "休闲",
                }[value],
            )
            st.selectbox(
                "旅行节奏",
                [None, "relaxed", "balanced", "packed"],
                key="structured_pace",
                format_func=lambda value: {
                    None: "每日活动强度（保持早、中、晚三个时段活动）",
                    "relaxed": "轻松（每天 1～2 项活动，可休息）",
                    "balanced": "均衡（三时段）",
                    "packed": "紧凑（三时段内优先较高强度活动）",
                }[value],
            )
            st.number_input("出行人数", min_value=1, max_value=10, value=1, key="structured_travelers")
            st.multiselect(
                "兴趣标签", ["美食", "历史", "文化", "艺术", "自然", "购物", "摄影", "运动"],
                key="structured_interests",
            )
            st.text_area(
                "额外备注", placeholder="额外信息，如：不吃辣、需要无障碍设施...", key="structured_notes"
            )
            planning_mode = st.radio(
                "规划模式",
                ["single", "compare"],
                key="structured_planning_mode",
                horizontal=True,
                format_func=lambda value: "单方案规划" if value == "single" else "双方案比较",
            )
            plan_clicked = st.button(
                "开始规划" if planning_mode == "single" else "生成双方案比较",
                key="start_structured_plan",
                type="primary",
                use_container_width=True,
            )
    with guide_col:
        st.markdown(
            '<div class="travel-card"><h3>结构化入口说明</h3>'
            '<p>该入口保留原有字段与启动方式，但正式结果仍来自相同 FastAPI / Pipeline。</p>'
            '<div class="travel-status-banner info"><strong>日期规则</strong>'
            '<span>返回日期不安排活动；住宿晚数按日期差计算。</span></div>'
            '<p class="travel-footer-note">所有航班、酒店、活动和天气均为确定性 Mock 数据。</p></div>',
            unsafe_allow_html=True,
        )

    preferences, preference_error = _structured_preferences()
    fingerprint = (
        json.dumps(
            preferences.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if preferences is not None
        else None
    )
    previous_fingerprint = (
        st.session_state.get("structured_comparison_fingerprint")
        or st.session_state.get("structured_plan_fingerprint")
    )
    if previous_fingerprint is not None and previous_fingerprint != fingerprint:
        invalidate_structured_results(
            st.session_state,
            reason="结构化偏好已修改，旧规划和评分已失效。",
        )

    if not plan_clicked:
        return
    if preference_error is not None:
        st.error(f"输入校验失败：{preference_error.errors()[0]['msg']}")
        return
    assert preferences is not None

    previous = (
        st.session_state.structured_comparison_result
        if planning_mode == "compare"
        else st.session_state.structured_plan_result
    )
    st.session_state.planning_request_state = "submitting"
    st.session_state.pending_planning_request = {
        "source": "structured",
        "mode": planning_mode,
        "preferences": preferences.model_dump(mode="json"),
        "fingerprint": fingerprint,
        "previous": previous,
    }
    set_ui_step(st.session_state, "planning")
    st.rerun()


def _active_comparison_payload() -> dict[str, Any] | None:
    return (
        st.session_state.preference_comparison_result
        or st.session_state.structured_comparison_result
    )


def _invalidate_structured_from_widget() -> None:
    invalidate_structured_results(
        st.session_state,
        reason="结构化偏好已修改，旧规划和评分已失效。",
    )


def _render_structured_edit_panel() -> None:
    default_start = date.today() + timedelta(days=30)
    with st.expander("修改结构化偏好"):
        st.warning("修改任一字段后，当前规划和评分立即失效，需要重新生成。")
        st.number_input(
            "总预算（¥）", min_value=100, max_value=500000, value=10000, step=100,
            key="structured_budget", on_change=_invalidate_structured_from_widget,
        )
        st.text_input(
            "出发城市", value="北京", key="structured_departure",
            on_change=_invalidate_structured_from_widget,
        )
        st.date_input(
            "出发日期", value=default_start, key="structured_start_date",
            on_change=_invalidate_structured_from_widget,
        )
        st.date_input(
            "返回日期", value=default_start + timedelta(days=4), key="structured_end_date",
            on_change=_invalidate_structured_from_widget,
        )
        st.selectbox(
            "旅行风格",
            ["comfort", "budget", "luxury", "adventure", "cultural", "relaxation"],
            key="structured_style",
            on_change=_invalidate_structured_from_widget,
        )
        st.selectbox(
            "旅行节奏", [None, "relaxed", "balanced", "packed"],
            key="structured_pace", on_change=_invalidate_structured_from_widget,
        )
        st.number_input(
            "出行人数", min_value=1, max_value=10, value=1,
            key="structured_travelers", on_change=_invalidate_structured_from_widget,
        )
        st.multiselect(
            "兴趣标签", ["美食", "历史", "文化", "艺术", "自然", "购物", "摄影", "运动"],
            key="structured_interests", on_change=_invalidate_structured_from_widget,
        )
        st.text_area(
            "额外备注", key="structured_notes", on_change=_invalidate_structured_from_widget,
        )


def _render_comparison_page() -> None:
    payload = _active_comparison_payload()
    if payload is None:
        set_ui_step(st.session_state, "intake")
        st.warning("当前没有可展示的比较结果。")
        return
    render_page_header(
        step_number=4,
        section="方案比较",
        title="比较事实，也解释取舍",
        description=(
            "两套方案来自独立 TravelPlanState。页面直接展示服务端 C2 评价、解释、有效权重和"
            "敏感性披露，不在前端重新计算分数。"
        ),
    )
    render_comparison_result(payload, session=st.session_state)
    if st.session_state.structured_comparison_result is not None:
        _render_structured_edit_panel()
    if st.button("修改偏好并重新规划", key="invalidate_comparison"):
        if st.session_state.preference_comparison_result is not None:
            invalidate_planning_results(
                st.session_state,
                reason="偏好将被修改，旧比较结果已失效。",
            )
        else:
            invalidate_structured_results(
                st.session_state,
                reason="结构化偏好将被修改，旧比较结果已失效。",
            )
        st.rerun()


def _single_plan_state() -> TravelPlanState | None:
    if st.session_state.preference_plan_result is not None:
        return TravelPlanState.model_validate(
            st.session_state.preference_plan_result["plan"]
        )
    if st.session_state.structured_plan_result is not None:
        return TravelPlanState.model_validate(st.session_state.structured_plan_result)
    return None


def _render_detail_page() -> None:
    comparison_payload = _active_comparison_payload()
    selected_plan = None
    if comparison_payload is not None:
        multi_plan, _, _ = parse_comparison_payload(comparison_payload)
        plan_ids = [plan.destination_id for plan in multi_plan.plans]
        names = {plan.destination_id: plan.destination.city for plan in multi_plan.plans}
        if st.session_state.active_plan_id not in plan_ids:
            st.session_state.active_plan_id = plan_ids[0] if plan_ids else None
        if plan_ids:
            selected_id = st.radio(
                "切换查看方案",
                plan_ids,
                key="active_plan_id",
                horizontal=True,
                format_func=lambda value: names[value],
            )
            selected_plan = select_destination_plan(comparison_payload, selected_id)
    state = selected_plan.plan if selected_plan is not None else _single_plan_state()
    if state is None:
        set_ui_step(st.session_state, "intake")
        st.warning("当前没有可展示的旅行方案。")
        return
    destination = state.selected_destination.city if state.selected_destination else "旅行方案"
    render_page_header(
        step_number=5,
        section="行程详情",
        title=f"{destination} · 每日行程与预算",
        description=(
            "天气、活动节奏和费用来自同一份后端规划结果。切换两个目的地只改变当前查看对象，"
            "不会重新运行 Pipeline，也不代表真实预订。"
        ),
    )
    if st.session_state.preference_confirmed:
        st.success("偏好已确认，并已提交现有 TravelPlanningPipeline。")
        st.button(
            "偏好已确认",
            key="confirm_preference",
            disabled=True,
        )
        if st.button("重新开始", key="reset_preference"):
            _reset_current_trip()
            st.rerun()
    if comparison_payload is not None and st.button("返回双方案比较", key="back_to_comparison"):
        set_ui_step(st.session_state, "comparison")
        st.rerun()
    render_plan_result(state)
    if selected_plan is not None and selected_plan.business_issues:
        st.markdown("#### 业务问题")
        for issue in selected_plan.business_issues:
            st.warning(f"{issue.code}：{issue.message}")


WEATHER_DEMO_SCENARIOS = {
    "晴天基线": "sunny_default",
    "固定雨天": "normal_rain_default",
    "室内替代费用更高": "higher_cost_indoor",
    "预算内天气合规重选": "budget_reselect_indoor",
    "天气不可用回退": "unavailable_default",
    "无天气合规活动": "no_feasible_outdoor_only",
    "极低预算不可满足": "extreme_budget_infeasible",
}


def render_weather_acceptance_demo() -> None:
    render_page_header(
        step_number=3,
        section="受控验收",
        title="天气规划固定场景",
        description="该入口只用于测试天气、预算与错误状态，不出现在普通用户配置中。",
    )
    st.warning(
        "这是阶段 B3 受控验收入口：天气、候选和价格均来自固定 Mock Fixture，"
        "不代表实时预报或真实库存。普通运行默认不显示此入口。"
    )
    scenario_label = st.selectbox(
        "固定验收场景", list(WEATHER_DEMO_SCENARIOS), key="weather_demo_scenario"
    )
    planning_mode = st.radio(
        "活动选择模式",
        ["weather_aware", "baseline"],
        format_func=lambda value: (
            "Treatment：天气参与硬约束和排序"
            if value == "weather_aware"
            else "Control：记录相同天气，但不参与活动选择"
        ),
        key="weather_demo_planning_mode",
    )
    inject_failure = st.checkbox(
        "注入必要 FlightAgent 故障（仅验收）", key="weather_demo_failure"
    )
    inject_weather_timeout = st.checkbox(
        "注入 WeatherProvider 超时（仅验收，按降级策略继续）", key="weather_demo_timeout"
    )
    if st.button("运行固定 Mock 验收场景", key="run_weather_demo", type="primary"):
        previous = st.session_state.get("weather_demo_result")
        try:
            with st.spinner("正在通过 FastAPI 运行固定天气验收场景..."):
                result = WeatherDemoApiClient().plan(
                    WEATHER_DEMO_SCENARIOS[scenario_label],
                    planning_mode,
                    inject_agent_failure=inject_failure,
                    inject_weather_timeout=inject_weather_timeout,
                )
        except WeatherDemoApiError as exc:
            st.session_state.weather_demo_result = previous
            st.error(str(exc))
        else:
            st.session_state.weather_demo_result = result

    result = st.session_state.get("weather_demo_result")
    if result is not None:
        st.caption(
            f"场景 {result['scenario_id']} · 模式 {result['planning_mode']} · "
            "由受控 FastAPI 演示端点返回"
        )
        render_plan_result(TravelPlanState.model_validate(result["plan"]))


def main() -> None:
    st.set_page_config(
        page_title="旅迹 · 智能旅行规划工作台",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _initialize_session()
    apply_workbench_theme()
    render_route_navigation(st.session_state)
    mode = _render_input_mode_switch()

    if mode == "天气验收演示（固定 Mock）":
        render_weather_acceptance_demo()
        return
    step = st.session_state.ui_step
    if step == "planning":
        _render_planning_page()
    elif step == "comparison":
        _render_comparison_page()
    elif step == "detail":
        _render_detail_page()
    elif mode == "结构化表单":
        _render_structured_intake()
    elif step == "confirmation":
        _render_natural_confirmation()
    else:
        _render_natural_intake()


main()
