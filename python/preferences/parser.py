"""Deterministic rule-based preference parser and replaceable parser contract."""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Protocol, runtime_checkable

from models.schemas import TravelStyle

from .contracts import (
    Pace,
    PreferenceField,
    PreferenceValueSource,
    PreferencesDraft,
)
from .date_semantics import reconcile_dates


@runtime_checkable
class PreferenceParser(Protocol):
    async def parse(self, text: str, *, reference_date: date) -> PreferencesDraft: ...


_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "俩": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CHINESE_UNITS = {"十": 10, "百": 100, "千": 1_000, "万": 10_000}

_KNOWN_CITIES = (
    "北京", "上海", "广州", "深圳", "成都", "杭州", "南京", "武汉", "西安",
    "重庆", "天津", "苏州", "厦门", "青岛", "长沙", "郑州", "香港", "澳门",
)

_INTEREST_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("摄影", ("拍照", "摄影", "旅拍")),
    ("美食", ("美食", "吃东西", "好吃的", "小吃", "餐厅")),
    ("历史", ("历史", "古迹", "遗址")),
    ("艺术", ("艺术", "画展", "美术馆")),
    ("自然", ("自然", "山水", "风景", "森林")),
    ("购物", ("购物", "买东西", "逛街")),
    ("户外", ("户外", "徒步", "登山", "露营")),
    ("博物馆", ("博物馆",)),
    ("海滩", ("海边", "海滩", "沙滩")),
    ("亲子", ("亲子", "带孩子", "遛娃")),
)


class MockPreferenceParser:
    """Finite Chinese corpus parser. It does not call an LLM or external service."""

    async def parse(self, text: str, *, reference_date: date) -> PreferencesDraft:
        normalized = " ".join(text.strip().split())
        values: dict[str, object] = {"raw_text": normalized}
        sources: dict[PreferenceField, PreferenceValueSource] = {}
        errors: dict[PreferenceField, str] = {}
        ambiguous: list[PreferenceField] = []

        def set_explicit(field: PreferenceField, value: object) -> None:
            values[field.value] = value
            sources[field] = PreferenceValueSource.EXPLICIT

        city = _extract_departure_city(normalized)
        if city:
            set_explicit(PreferenceField.DEPARTURE_CITY, city)

        budget, budget_ambiguous = _extract_budget(normalized)
        if budget_ambiguous:
            ambiguous.append(PreferenceField.BUDGET)
        elif budget is not None:
            set_explicit(PreferenceField.BUDGET, budget)

        duration = _extract_count(normalized, r"([负\-]?[0-9]+|[负零〇一二两俩三四五六七八九十百]+)\s*天")
        if duration is not None:
            set_explicit(PreferenceField.DURATION_DAYS, duration)

        travelers, travelers_ambiguous = _extract_travelers(normalized)
        if travelers_ambiguous:
            ambiguous.append(PreferenceField.NUM_TRAVELERS)
        elif travelers is not None:
            set_explicit(PreferenceField.NUM_TRAVELERS, travelers)

        style = _extract_style(normalized)
        if style is not None:
            set_explicit(PreferenceField.TRAVEL_STYLE, style)

        pace = _extract_pace(normalized)
        if pace is not None:
            set_explicit(PreferenceField.PACE, pace)

        interests = _extract_interests(normalized)
        if interests is not None:
            set_explicit(PreferenceField.INTERESTS, interests)

        date_values, date_sources, date_errors, date_ambiguous = _extract_dates(
            normalized,
            reference_date,
        )
        values.update(date_values)
        sources.update(date_sources)
        errors.update(date_errors)
        ambiguous.extend(date_ambiguous)

        draft = PreferencesDraft(
            **values,
            field_sources=sources,
            field_errors=errors,
            ambiguous_fields=ambiguous,
        )
        return reconcile_dates(draft)


def _extract_departure_city(text: str) -> str | None:
    for city in _KNOWN_CITIES:
        if re.search(rf"(?:从|出发地(?:是|为)?\s*){re.escape(city)}(?:出发|出去|走|飞|去|\b|，|,)", text):
            return city
    patterns = (
        r"从\s*([\u4e00-\u9fffA-Za-z·]{2,20}?)(?:出发|出去|走|飞)",
        r"出发地(?:是|为)?\s*([\u4e00-\u9fffA-Za-z·]{2,20})",
        r"([\u4e00-\u9fff]{2,10})\s*出发",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return None


def _extract_budget(text: str) -> tuple[float | None, bool]:
    range_pattern = (
        r"预算[^，。；,;]{0,6}?"
        r"([0-9]+(?:\.[0-9]+)?|[零〇一二两三四五六七八九十百千万]+)"
        r"\s*(?:到|至|[-~])\s*"
        r"([0-9]+(?:\.[0-9]+)?|[零〇一二两三四五六七八九十百千万]+)"
    )
    if re.search(range_pattern, text):
        return None, True

    arabic = re.search(
        r"预算(?:大概|大约|约|是|为|改成|调整为|提高到|降到)?\s*"
        r"(-?[0-9]+(?:\.[0-9]+)?)\s*(万|千|元|块)?",
        text,
    )
    if arabic:
        value = float(arabic.group(1))
        multiplier = {"万": 10_000, "千": 1_000}.get(arabic.group(2), 1)
        return value * multiplier, False

    chinese = re.search(
        r"预算(?:大概|大约|约|是|为|改成|调整为|提高到|降到)?\s*"
        r"(负?[零〇一二两三四五六七八九十百千万]+)(?:元|块)?",
        text,
    )
    if chinese:
        return float(_parse_chinese_integer(chinese.group(1))), False
    return None, False


def _extract_travelers(text: str) -> tuple[int | None, bool]:
    if re.search(
        r"(?:[0-9]+|[零〇一二两三四五六七八九十]+)\s*(?:到|至|[-~])\s*"
        r"(?:[0-9]+|[零〇一二两三四五六七八九十]+)\s*(?:个?人|位)",
        text,
    ) or re.search(r"两三\s*(?:个?人|位)", text):
        return None, True

    count = _extract_count(
        text,
        r"([负\-]?[0-9]+|[负零〇一二两俩三四五六七八九十百]+)\s*(?:个?人|位|口)",
    )
    return count, False


def _extract_count(text: str, pattern: str) -> int | None:
    match = re.search(pattern, text)
    if not match:
        return None
    token = match.group(1)
    if re.fullmatch(r"-?[0-9]+", token):
        return int(token)
    return _parse_chinese_integer(token)


def _extract_style(text: str) -> TravelStyle | None:
    mappings = (
        (TravelStyle.BUDGET, ("穷游", "经济游", "省钱游", "预算型")),
        (TravelStyle.LUXURY, ("豪华游", "奢华", "高端游")),
        (TravelStyle.ADVENTURE, ("探险游", "冒险游", "探险风格")),
        (TravelStyle.CULTURAL, ("文化游", "人文游", "文化风格")),
        (TravelStyle.RELAXATION, ("休闲游", "度假型", "度假风格")),
        (TravelStyle.COMFORT, ("舒适游", "舒适型", "旅行风格舒适")),
    )
    for style, terms in mappings:
        if any(term in text for term in terms):
            return style
    return None


def _extract_pace(text: str) -> Pace | None:
    if any(term in text for term in ("不想太累", "不赶行程", "慢节奏", "轻松一点", "悠闲")):
        return Pace.RELAXED
    if any(term in text for term in ("特种兵", "紧凑", "多安排景点", "行程快一点", "排满", "高密度")):
        return Pace.PACKED
    if any(term in text for term in ("节奏适中", "不要太松也不要太赶", "正常节奏")):
        return Pace.BALANCED
    return None


def _extract_interests(text: str) -> list[str] | None:
    if any(term in text for term in ("没有特别兴趣", "无特别偏好", "都可以")):
        return []
    interests = [
        canonical
        for canonical, terms in _INTEREST_TERMS
        if any(term in text for term in terms)
    ]
    return interests or None


def _extract_dates(
    text: str,
    reference_date: date,
) -> tuple[
    dict[str, object],
    dict[PreferenceField, PreferenceValueSource],
    dict[PreferenceField, str],
    list[PreferenceField],
]:
    values: dict[str, object] = {}
    sources: dict[PreferenceField, PreferenceValueSource] = {}
    errors: dict[PreferenceField, str] = {}
    ambiguous: list[PreferenceField] = []

    iso_range = re.search(
        r"(\d{4}-\d{1,2}-\d{1,2})\s*(?:到|至|[-~])\s*(\d{4}-\d{1,2}-\d{1,2})",
        text,
    )
    chinese_range = re.search(
        r"(\d{4})年(\d{1,2})月(\d{1,2})日?\s*(?:到|至|[-~])\s*"
        r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日?",
        text,
    )
    if iso_range:
        _set_date(values, sources, errors, PreferenceField.START_DATE, iso_range.group(1))
        _set_date(values, sources, errors, PreferenceField.END_DATE, iso_range.group(2))
        return values, sources, errors, ambiguous
    if chinese_range:
        start_raw = f"{int(chinese_range.group(1)):04d}-{int(chinese_range.group(2)):02d}-{int(chinese_range.group(3)):02d}"
        end_year = int(chinese_range.group(4) or chinese_range.group(1))
        end_raw = f"{end_year:04d}-{int(chinese_range.group(5)):02d}-{int(chinese_range.group(6)):02d}"
        _set_date(values, sources, errors, PreferenceField.START_DATE, start_raw)
        _set_date(values, sources, errors, PreferenceField.END_DATE, end_raw)
        return values, sources, errors, ambiguous

    matches = list(re.finditer(r"\d{4}-\d{1,2}-\d{1,2}", text))
    if not matches:
        matches = list(re.finditer(r"(\d{4})年(\d{1,2})月(\d{1,2})日?", text))
    if matches:
        match = matches[0]
        if "年" in match.group(0):
            raw = f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
        else:
            raw = match.group(0)
        context = text[max(0, match.start() - 8): min(len(text), match.end() + 8)]
        field = (
            PreferenceField.END_DATE
            if any(term in context for term in ("返程", "返回", "回来", "回家"))
            else PreferenceField.START_DATE
        )
        _set_date(values, sources, errors, field, raw)
    else:
        relative_offsets = (("后天", 2), ("明天", 1), ("今天", 0))
        for term, offset in relative_offsets:
            if term in text:
                values[PreferenceField.START_DATE.value] = (
                    reference_date + timedelta(days=offset)
                ).isoformat()
                sources[PreferenceField.START_DATE] = PreferenceValueSource.INFERRED
                break

    if not values and any(term in text for term in ("国庆", "春节", "暑假", "寒假")):
        ambiguous.extend([PreferenceField.START_DATE, PreferenceField.END_DATE])
    return values, sources, errors, ambiguous


def _set_date(
    values: dict[str, object],
    sources: dict[PreferenceField, PreferenceValueSource],
    errors: dict[PreferenceField, str],
    field: PreferenceField,
    raw: str,
) -> None:
    normalized = raw
    try:
        parsed = date.fromisoformat(raw)
        normalized = parsed.isoformat()
    except ValueError:
        errors[field] = f"{raw} 不是有效日期"
    values[field.value] = normalized
    sources[field] = PreferenceValueSource.EXPLICIT


def _parse_chinese_integer(token: str) -> int:
    negative = token.startswith("负")
    if negative:
        token = token[1:]
    if not token:
        raise ValueError("empty Chinese number")

    if "万" in token:
        left, right = token.split("万", 1)
        result = _parse_chinese_integer(left or "一") * 10_000
        if right:
            if all(char in _CHINESE_DIGITS for char in right) and len(right) == 1:
                result += _CHINESE_DIGITS[right] * 1_000
            else:
                result += _parse_chinese_integer(right)
        return -result if negative else result

    if all(char in _CHINESE_DIGITS for char in token):
        digits = "".join(str(_CHINESE_DIGITS[char]) for char in token)
        result = int(digits)
        return -result if negative else result

    total = 0
    current = 0
    for char in token:
        if char in _CHINESE_DIGITS:
            current = _CHINESE_DIGITS[char]
        elif char in _CHINESE_UNITS:
            unit = _CHINESE_UNITS[char]
            total += (current or 1) * unit
            current = 0
        else:
            raise ValueError(f"unsupported Chinese number: {token}")
    result = total + current
    return -result if negative else result
