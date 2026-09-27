"""Weather Agent: obtain and validate daily weather before activity planning."""

from __future__ import annotations

import asyncio

from pydantic import ValidationError

from config.settings import settings
from models.schemas import (
    TravelPlanState,
    WeatherPlanningMetadata,
    WeatherPlanningStatus,
    WeatherResult,
    WeatherResultStatus,
    WeatherScenario,
    WeatherSearchRequest,
)
from tools.providers import (
    WeatherProvider,
    WeatherProviderDataError,
    WeatherProviderTimeoutError,
)
from tools.weather_provider import MockWeatherProvider

from .base_agent import BaseAgent


class WeatherAgent(BaseAgent):
    name = "WeatherAgent"
    output_fields = ("weather_result", "weather_planning")

    def __init__(
        self,
        provider: WeatherProvider | None = None,
        *,
        scenario: WeatherScenario = WeatherScenario.GENERATED,
        timeout_seconds: float | None = None,
    ) -> None:
        super().__init__()
        self.provider = provider or MockWeatherProvider()
        self.scenario = scenario
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else settings.WEATHER_PROVIDER_TIMEOUT
        )

    async def execute(self, state: TravelPlanState) -> TravelPlanState:
        preferences = state.preferences
        destination = state.selected_destination
        if preferences is None or destination is None:
            raise ValueError("缺少偏好或目的地信息")

        request = WeatherSearchRequest(
            destination=destination.city,
            start_date=preferences.start_date,
            end_date=preferences.end_date,
            scenario=self.scenario,
        )
        try:
            raw_result = await asyncio.wait_for(
                self.provider.search(request),
                timeout=self.timeout_seconds,
            )
        except (asyncio.TimeoutError, TimeoutError, WeatherProviderTimeoutError) as exc:
            reason = f"天气 Provider 超时（限制 {self.timeout_seconds:g} 秒），采用基础活动规划。"
            state.weather_result = None
            state.weather_planning = WeatherPlanningMetadata(
                status=WeatherPlanningStatus.DEGRADED_TIMEOUT,
                fallback_used=True,
                issue_code="weather_provider_timeout",
                reason=reason,
            )
            state.error_messages.append(reason)
            return state
        except WeatherProviderDataError as exc:
            state.weather_planning = WeatherPlanningMetadata(
                status=WeatherPlanningStatus.FAILED_DATA,
                issue_code="weather_provider_invalid_data",
                reason=str(exc) or "天气 Provider 返回非法数据",
            )
            raise
        except Exception as exc:
            state.weather_planning = WeatherPlanningMetadata(
                status=WeatherPlanningStatus.FAILED_INTERNAL,
                issue_code="weather_provider_internal_error",
                reason=str(exc) or "天气 Provider 内部故障",
            )
            raise

        try:
            result = (
                raw_result.model_copy(deep=True)
                if isinstance(raw_result, WeatherResult)
                else WeatherResult.model_validate(raw_result)
            )
            if result.destination != destination.city:
                raise ValueError("天气目的地与当前规划目的地不一致")
            if (
                result.start_date != preferences.start_date
                or result.end_date != preferences.end_date
            ):
                raise ValueError("天气日期范围与活动日期范围不一致")
        except (ValidationError, ValueError, TypeError) as exc:
            reason = str(exc) or "天气 Provider 返回非法数据"
            state.weather_planning = WeatherPlanningMetadata(
                status=WeatherPlanningStatus.FAILED_DATA,
                issue_code="weather_provider_invalid_data",
                reason=reason,
            )
            raise WeatherProviderDataError(reason) from exc

        state.weather_result = result
        if result.availability_status == WeatherResultStatus.UNAVAILABLE:
            state.weather_planning = WeatherPlanningMetadata(
                status=WeatherPlanningStatus.UNAVAILABLE,
                fallback_used=True,
                issue_code="weather_data_unavailable",
                reason=result.availability_note,
            )
        elif result.availability_status == WeatherResultStatus.PARTIAL:
            state.weather_planning = WeatherPlanningMetadata(
                status=WeatherPlanningStatus.PARTIAL,
                fallback_used=True,
                issue_code="weather_data_partial",
                reason=result.availability_note,
            )
        else:
            state.weather_planning = WeatherPlanningMetadata(
                status=WeatherPlanningStatus.READY,
                fallback_used=False,
                reason=result.availability_note,
            )
        return state
