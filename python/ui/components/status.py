"""Truthful planning and transport status components."""

from __future__ import annotations

import html

import streamlit as st


def render_status_banner(title: str, message: str, *, tone: str = "info") -> None:
    safe_tone = tone if tone in {"success", "warning", "error", "info"} else "info"
    st.markdown(
        f'<div class="travel-status-banner {safe_tone}"><strong>{html.escape(title)}</strong>'
        f'<span>{html.escape(message)}</span></div>',
        unsafe_allow_html=True,
    )


def render_planning_loading(*, comparison: bool) -> None:
    last_step = "独立比较与解释" if comparison else "完整结果组装"
    st.markdown(
        f"""
        <div class="travel-card travel-loading" role="status" aria-live="polite">
          <div class="travel-orbit" aria-hidden="true"></div>
          <h2>整体规划请求进行中</h2>
          <div class="travel-indeterminate" aria-hidden="true"></div>
          <p>目的地候选 → 航班 / 酒店 / 天气并行 → 天气明确后安排活动 → 预算闭环 → {last_step}</p>
          <p class="travel-footer-note">这里描述固定执行顺序。后端尚未提供进度事件，因此不显示虚假的 Agent 百分比。</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_retryable_error(message: str) -> None:
    st.error(
        "无法完成本次请求："
        f"{message} 已确认偏好和有效草稿仍保留，可以在服务恢复后重试。"
    )
