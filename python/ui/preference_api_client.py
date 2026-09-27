"""Small synchronous HTTP client used by the Streamlit preference flow."""

from __future__ import annotations

import os
from datetime import date
from typing import Any

import httpx


class PreferenceApiError(RuntimeError):
    """The preference API returned an error or could not be reached."""


class PreferenceApiTimeout(PreferenceApiError):
    """The preference API did not respond within the configured timeout."""


class PreferenceApiClient:
    def __init__(self, base_url: str | None = None, timeout_seconds: float = 10.0) -> None:
        self.base_url = (
            base_url
            or os.getenv("TRAVEL_API_BASE_URL", "http://127.0.0.1:8000")
        ).rstrip("/")
        self.timeout_seconds = timeout_seconds

    def parse(self, text: str, reference_date: date) -> dict[str, Any]:
        return self._post(
            "/api/preferences/parse",
            {
                "text": text,
                "reference_date": reference_date.isoformat(),
                "config": self._mock_config(),
            },
        )

    def clarify(
        self,
        draft: dict[str, Any],
        draft_receipt: str,
        answer: str,
        reference_date: date,
    ) -> dict[str, Any]:
        return self._post(
            "/api/preferences/clarify",
            {
                "draft": draft,
                "draft_receipt": draft_receipt,
                "answer": answer,
                "reference_date": reference_date.isoformat(),
                "config": self._mock_config(),
            },
        )

    def plan(
        self,
        draft: dict[str, Any],
        draft_receipt: str,
        reference_date: date,
    ) -> dict[str, Any]:
        return self._post(
            "/api/preferences/plan",
            {
                "draft": draft,
                "draft_receipt": draft_receipt,
                "confirmed": True,
                "reference_date": reference_date.isoformat(),
                "config": self._mock_config(),
            },
        )

    def compare_confirmed(
        self,
        draft: dict[str, Any],
        draft_receipt: str,
        reference_date: date,
        *,
        planning_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._post(
            "/api/preferences/compare",
            {
                "draft": draft,
                "draft_receipt": draft_receipt,
                "confirmed": True,
                "reference_date": reference_date.isoformat(),
                "config": self._mock_config(),
                "planning_config": planning_config or {},
            },
        )

    def compare_structured(
        self,
        preferences: dict[str, Any],
        *,
        planning_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._post(
            "/api/plans/compare",
            {
                "preferences": preferences,
                "plan_count": 2,
                "config": planning_config or {},
            },
        )

    def plan_structured(self, preferences: dict[str, Any]) -> dict[str, Any]:
        """Run the existing full single-plan endpoint for the structured UI."""
        return self._post("/api/plan/full", preferences)

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = httpx.post(
                f"{self.base_url}{path}",
                json=payload,
                timeout=self.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise PreferenceApiTimeout(
                f"偏好服务请求超时（{self.timeout_seconds:g} 秒），请稍后重试。"
            ) from exc
        except httpx.RequestError as exc:
            raise PreferenceApiError(f"无法连接偏好服务：{exc}") from exc

        if response.is_error:
            raw_detail = response.text.strip()
            try:
                parsed = response.json()
                detail = parsed.get("detail") if isinstance(parsed, dict) else None
            except ValueError:
                detail = None
            detail = detail or raw_detail or response.reason_phrase or "未知错误"
            raise PreferenceApiError(
                f"偏好服务返回 HTTP {response.status_code}：{detail}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise PreferenceApiError("偏好服务返回了无法解析的响应。") from exc
        if not isinstance(body, dict):
            raise PreferenceApiError("偏好服务响应格式不正确。")
        return body

    @staticmethod
    def _mock_config() -> dict[str, str]:
        return {
            "provider": "mock",
            "locale": "zh-CN",
            "version": "mock-preference-parser-v1",
        }
