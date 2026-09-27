"""
航班搜索工具 —— 模拟 Amadeus GDS API。

在生产环境中，这里会对接 Amadeus API / 携程API 等真实数据源。
Mock 模式下返回随机但合理的航班数据，保证系统可以零成本运行演示。
"""

from __future__ import annotations

from config.settings import settings
from models.schemas import DataSource, Flight, FlightSearchRequest

from .deterministic import stable_candidate_id, stable_rng


AIRLINES = {
    "国内": [
        ("中国国航", "CA"), ("东方航空", "MU"), ("南方航空", "CZ"),
        ("海南航空", "HU"), ("春秋航空", "9C"), ("吉祥航空", "HO"),
        ("深圳航空", "ZH"), ("厦门航空", "MF"),
    ],
    "国际": [
        ("全日空", "NH"), ("日本航空", "JL"), ("大韩航空", "KE"),
        ("新加坡航空", "SQ"), ("泰国航空", "TG"), ("国泰航空", "CX"),
        ("阿联酋航空", "EK"), ("法国航空", "AF"),
    ],
}

ROUTE_DURATIONS = {
    ("北京", "东京"): (3.5, 4.5), ("上海", "东京"): (2.5, 3.5),
    ("北京", "首尔"): (2.0, 3.0), ("上海", "首尔"): (1.5, 2.5),
    ("北京", "曼谷"): (4.5, 6.0), ("上海", "曼谷"): (4.0, 5.5),
    ("北京", "巴黎"): (10.0, 13.0), ("上海", "巴黎"): (11.0, 14.0),
    ("北京", "大阪"): (3.0, 4.0), ("上海", "大阪"): (2.0, 3.0),
    ("北京", "清迈"): (5.0, 7.0), ("上海", "清迈"): (4.5, 6.5),
}


class MockFlightProvider:
    """基于稳定请求摘要生成候选；不包含航班推荐评分。"""

    def __init__(self, dataset_version: str | None = None):
        self.dataset_version = dataset_version or settings.MOCK_DATA_VERSION

    async def search(self, request: FlightSearchRequest) -> list[Flight]:
        return _generate_flights(request, self.dataset_version)


def _generate_flights(
    request: FlightSearchRequest,
    dataset_version: str,
) -> list[Flight]:
    key = (request.departure_city, request.arrival_city)
    rev_key = (request.arrival_city, request.departure_city)
    dur_range = ROUTE_DURATIONS.get(key) or ROUTE_DURATIONS.get(rev_key) or (3.0, 8.0)

    international_cities = {"东京", "首尔", "曼谷", "巴黎", "大阪", "清迈"}
    is_international = bool(
        {request.departure_city, request.arrival_city} & international_cities
    )
    airline_pool = AIRLINES["国际"] if is_international else AIRLINES["国内"]

    cabin_multiplier = {"economy": 1.0, "business": 2.5, "first": 5.0}
    multiplier = cabin_multiplier[request.cabin_class]
    rng = stable_rng(
        "flight",
        request.model_dump(mode="json"),
        dataset_version,
    )

    results: list[Flight] = []
    for i in range(request.count):
        airline_name, airline_code = airline_pool[i % len(airline_pool)]
        duration = round(rng.uniform(*dur_range), 1)
        stops = rng.choices([0, 1, 2], weights=[60, 30, 10])[0]
        base_price = 500 + duration * rng.randint(200, 500) + stops * (-300)
        price = max(300, round(base_price * multiplier))

        dep_hour = rng.randint(6, 20)
        dep_time = (
            f"{request.travel_date}T{dep_hour:02d}:"
            f"{rng.choice(['00', '30'])}:00"
        )
        candidate_id = stable_candidate_id(
            "flight",
            {
                "departure_city": request.departure_city,
                "arrival_city": request.arrival_city,
                "travel_date": request.travel_date,
                "cabin_class": request.cabin_class,
                "airline_code": airline_code,
                "index": i,
            },
            dataset_version,
        )

        results.append(Flight(
            candidate_id=candidate_id,
            source=DataSource.MOCK,
            source_version=dataset_version,
            airline=airline_name,
            flight_no=f"{airline_code}{rng.randint(100, 9999)}",
            departure_city=request.departure_city,
            arrival_city=request.arrival_city,
            departure_time=dep_time,
            arrival_time=f"(+{duration}h)",
            price=float(price),
            duration_hours=duration,
            stops=stops,
            cabin_class=request.cabin_class,
        ))

    return sorted(results, key=lambda flight: (flight.price, flight.candidate_id))


def search_flights(
    departure_city: str,
    arrival_city: str,
    date: str,
    cabin_class: str = "economy",
    count: int = 6,
) -> list[Flight]:
    """兼容原同步工具入口；Agent 使用 MockFlightProvider。"""
    request = FlightSearchRequest(
        departure_city=departure_city,
        arrival_city=arrival_city,
        travel_date=date,
        cabin_class=cabin_class,
        count=count,
    )
    return _generate_flights(request, settings.MOCK_DATA_VERSION)
