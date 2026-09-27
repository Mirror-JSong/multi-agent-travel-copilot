"""候选数据 Provider 接口。

Mock 与未来 Real Provider 都实现相同的 async search 契约。Provider 只负责
查询/生成数据，不做航班、酒店、活动或天气的业务评分。WeatherProvider
沿用“Pydantic 请求 -> Pydantic 结果”的同一接口模式。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from models.schemas import (
    Activity,
    ActivitySearchRequest,
    Flight,
    FlightSearchRequest,
    Hotel,
    HotelSearchRequest,
    WeatherResult,
    WeatherSearchRequest,
)


class WeatherProviderError(RuntimeError):
    """Base class for expected WeatherProvider integration failures."""


class WeatherProviderTimeoutError(WeatherProviderError):
    """The provider did not answer within the configured timeout."""


class WeatherProviderDataError(WeatherProviderError):
    """The provider answered, but its payload violated the weather contract."""


@runtime_checkable
class FlightProvider(Protocol):
    async def search(self, request: FlightSearchRequest) -> list[Flight]: ...


@runtime_checkable
class HotelProvider(Protocol):
    async def search(self, request: HotelSearchRequest) -> list[Hotel]: ...


@runtime_checkable
class ActivityProvider(Protocol):
    async def search(self, request: ActivitySearchRequest) -> list[Activity]: ...


@runtime_checkable
class WeatherProvider(Protocol):
    """Replaceable weather data source; orchestration policy belongs to B2."""

    async def search(self, request: WeatherSearchRequest) -> WeatherResult: ...
