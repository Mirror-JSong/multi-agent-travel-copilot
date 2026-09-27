"""Preference conversation, dossier and confirmation components."""

from __future__ import annotations

import html
from typing import Any, Mapping

import streamlit as st


FIELD_LABELS = {
    "departure_city": "出发城市",
    "budget": "总预算",
    "start_date": "出发日期",
    "end_date": "返程日期",
    "duration_days": "旅行天数",
    "num_travelers": "旅行人数",
    "travel_style": "旅行风格",
    "pace": "旅行节奏",
    "interests": "兴趣",
}

SOURCE_LABELS = {
    "explicit": "用户提供",
    "inferred": "规则推导",
    "unknown": "尚未确定",
}


def display_value(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, list):
        return "、".join(str(item) for item in value) if value else "明确无特别偏好"
    return str(value)


def render_draft_dossier(response: Mapping[str, Any], *, compact: bool = False) -> None:
    draft = response["draft"]
    sources = draft["field_sources"]
    if compact:
        fields = ("departure_city", "start_date", "end_date", "num_travelers", "budget", "pace")
        blocks = []
        for field in fields:
            source = sources.get(field, "unknown")
            blocks.append(
                '<div class="travel-field">'
                f'<span>{html.escape(FIELD_LABELS[field])}'
                f'<em class="travel-source {"unknown" if source == "unknown" else ""}">'
                f'{html.escape(SOURCE_LABELS.get(source, source))}</em></span>'
                f'<strong>{html.escape(display_value(draft.get(field)))}</strong></div>'
            )
        st.markdown(
            '<div class="travel-card"><h3>实时偏好摘要</h3>'
            '<p class="travel-kicker">缺失字段不会被默认值掩盖</p>'
            f'<div class="travel-dossier">{"".join(blocks)}</div></div>',
            unsafe_allow_html=True,
        )
        return

    rows = [
        {
            "字段": label,
            "识别结果": display_value(draft.get(field)),
            "来源": SOURCE_LABELS.get(sources.get(field, "unknown"), sources.get(field, "unknown")),
        }
        for field, label in FIELD_LABELS.items()
    ]
    st.markdown("#### 已识别偏好")
    st.table(rows)


def render_questions(response: Mapping[str, Any]) -> None:
    result = response["clarification_result"]
    errors = result.get("validation_errors", {})
    if errors:
        st.markdown("#### 需要修正")
        for field, message in errors.items():
            st.error(f"{FIELD_LABELS.get(field, field)}：{message}")
    questions = response.get("questions", [])
    if questions:
        st.markdown("#### 需要补充")
        for question in questions:
            st.warning(question["prompt"])


def render_history(history: list[dict[str, str]]) -> None:
    if not history:
        st.markdown(
            '<div class="travel-status-banner info"><strong>先描述一次旅行</strong>'
            '<span>系统只提取明确表达的信息，并针对缺失字段继续追问。</span></div>',
            unsafe_allow_html=True,
        )
        return
    for item in history:
        role = "user" if item["role"] == "user" else "assistant"
        with st.chat_message(role):
            st.write(item["content"])


def render_confirmation_card(response: Mapping[str, Any]) -> None:
    draft = response["draft"]
    st.markdown("### 最终偏好确认")
    date_summary = response.get("date_summary")
    if date_summary:
        c1, c2 = st.columns(2)
        c1.metric("出发日期", date_summary["departure_date"])
        c2.metric("返程日期", date_summary["return_date"])
        st.write(
            "**活动日期范围：** "
            f"{date_summary['activity_start_date']} 至 {date_summary['activity_end_date']}（含）"
        )
        st.write(f"**预计住宿晚数：** {date_summary['hotel_nights']} 晚")

    st.write(
        f"**出发城市：** {display_value(draft.get('departure_city'))}　"
        f"**人数：** {display_value(draft.get('num_travelers'))} 人"
    )
    st.write(
        f"**总预算：** ¥{float(draft['budget']):,.0f}　"
        f"**旅行风格：** {display_value(draft.get('travel_style'))}"
    )
    st.write(
        f"**旅行节奏：** {display_value(draft.get('pace'))}　"
        f"**兴趣：** {display_value(draft.get('interests'))}"
    )
    if draft.get("pace") is not None:
        st.info("旅行节奏已通过确认接口进入正式规划，并由 ActivityAgent 的节奏规则消费。")
    st.caption("确认后将按上方 UserPreferences 预览调用现有 TravelPlanningPipeline。")
