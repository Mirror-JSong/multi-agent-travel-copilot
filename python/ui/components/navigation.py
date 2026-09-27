"""Five-step route navigation and workbench page headers."""

from __future__ import annotations

from typing import Any, MutableMapping

import streamlit as st

from ui.workbench_state import set_ui_step


ROUTE_STEPS = (
    ("intake", "需求对话", "说出想法"),
    ("confirmation", "偏好确认", "核对约束"),
    ("planning", "规划执行", "等待结果"),
    ("comparison", "方案比较", "理解取舍"),
    ("detail", "行程详情", "查看每天"),
)


def _available_steps(state: MutableMapping[str, Any]) -> set[str]:
    available = {"intake"}
    response = state.get("preference_response")
    if response and response.get("is_complete"):
        available.add("confirmation")
    if state.get("planning_request_state") in {"submitting", "error"}:
        available.add("planning")
    if state.get("preference_comparison_result") or state.get("structured_comparison_result"):
        available.update({"planning", "comparison", "detail"})
    if state.get("preference_plan_result") or state.get("structured_plan_result"):
        available.update({"planning", "detail"})
    return available


def render_route_navigation(state: MutableMapping[str, Any]) -> None:
    current = state.get("ui_step", "intake")
    available = _available_steps(state)
    with st.sidebar:
        st.markdown(
            """
            <div class="travel-brand">
              <svg viewBox="0 0 48 48" aria-hidden="true">
                <path d="M8 35c8-2 7-15 16-15s7 10 16-7" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"></path>
                <circle cx="8" cy="35" r="5" fill="currentColor"></circle>
                <circle cx="24" cy="20" r="5" fill="#d85a43"></circle>
                <circle cx="40" cy="13" r="5" fill="#e3ad37"></circle>
              </svg>
              <div><strong>旅迹</strong><small>TRAVEL COPILOT</small></div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        for index, (step, title, caption) in enumerate(ROUTE_STEPS, start=1):
            marker = "当前" if step == current else "已完成" if step in available else "未开始"
            st.markdown(
                f'<div class="travel-route-state">{index:02d} · {marker}</div>',
                unsafe_allow_html=True,
            )
            if st.button(
                title,
                key=f"workbench_nav_{step}",
                disabled=step not in available or step == current,
                use_container_width=True,
            ):
                set_ui_step(state, step)
                st.rerun()
            st.markdown(
                f'<div class="travel-route-caption">{caption}</div>',
                unsafe_allow_html=True,
            )
        st.markdown(
            '<div class="travel-mock-stamp">MOCK / NOT LIVE<br>确定性 Provider · 非实时预订</div>',
            unsafe_allow_html=True,
        )


def render_page_header(
    *,
    step_number: int,
    section: str,
    title: str,
    description: str,
) -> None:
    st.markdown(
        f"""
        <div class="travel-topline">
          <div class="travel-crumb">旅行工作台 / <strong>{section}</strong></div>
          <div class="travel-badges">
            <span class="travel-badge mock">示例 Mock 数据</span>
            <span class="travel-badge">不代表预订</span>
          </div>
        </div>
        <div class="travel-page-heading">
          <div class="travel-eyebrow">STEP {step_number:02d}</div>
          <h1>{title}</h1>
          <p class="travel-lede">{description}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
