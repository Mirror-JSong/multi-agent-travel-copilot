"""HTTP client for the opt-in, fixed-Mock B3 acceptance demonstration."""

from __future__ import annotations

import os
from typing import Any

import httpx


class WeatherDemoApiError(RuntimeError):
    pass


class WeatherDemoApiClient:
    def __init__(self, base_url: str | None = None, timeout_seconds: float = 20.0) -> None:
        self.base_url = (
            base_url
            or os.getenv("TRAVEL_API_BASE_URL", "http://127.0.0.1:8000")
        ).rstrip("/")
        self.timeout_seconds = timeout_seconds

    def plan(
        self,
        scenario_id: str,
        planning_mode: str,
        *,
        inject_agent_failure: bool = False,
        inject_weather_timeout: bool = False,
    ) -> dict[str, Any]:
        try:
            response = httpx.post(
                f"{self.base_url}/api/demo/weather/plan",
                json={
                    "scenario_id": scenario_id,
                    "planning_mode": planning_mode,
                    "inject_agent_failure": inject_agent_failure,
                    "inject_weather_timeout": inject_weather_timeout,
                },
                timeout=self.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise WeatherDemoApiError("天气验收演示请求超时，请安全重试。") from exc
        except httpx.RequestError as exc:
            raise WeatherDemoApiError(f"无法连接天气验收演示服务：{exc}") from exc
        if response.is_error:
            raise WeatherDemoApiError(
                f"天气验收演示服务返回 HTTP {response.status_code}：{response.text}"
            )
        body = response.json()
        if not isinstance(body, dict) or "plan" not in body:
            raise WeatherDemoApiError("天气验收演示响应格式不正确。")
        return body
