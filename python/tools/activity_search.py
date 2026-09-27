"""
活动搜索工具 —— 模拟 Google Maps Places API / 大众点评 API。

在生产环境中，这里会对接 Google Places API / TripAdvisor API 等。
Mock 模式下返回各城市的景点、餐厅、体验活动数据。
"""

from __future__ import annotations

from config.settings import settings
from models.schemas import (
    Activity,
    ActivityAttributeStatus,
    ActivityEnvironment,
    ActivityIntensity,
    ActivitySearchRequest,
    DataSource,
    WeatherSensitivity,
)

from .deterministic import stable_candidate_id, stable_rng


CITY_ACTIVITIES: dict[str, list[dict]] = {
    "东京": [
        {"name": "浅草寺", "cat": "sightseeing", "price": 0, "hours": 2.0, "slot": "morning", "env": "outdoor", "sensitivity": "high", "intensity": "medium"},
        {"name": "筑地市场海鲜早餐", "cat": "food", "price": 200, "hours": 1.5, "slot": "morning", "env": "indoor", "sensitivity": "low", "intensity": "low"},
        {"name": "明治神宫", "cat": "sightseeing", "price": 0, "hours": 1.5, "slot": "morning", "env": "outdoor", "sensitivity": "high", "intensity": "medium"},
        {"name": "涩谷十字路口", "cat": "sightseeing", "price": 0, "hours": 0.5, "slot": "afternoon", "env": "outdoor", "sensitivity": "high", "intensity": "low"},
        {"name": "teamLab数字艺术馆", "cat": "experience", "price": 250, "hours": 2.5, "slot": "afternoon", "env": "indoor", "sensitivity": "none", "intensity": "low"},
        {"name": "东京塔", "cat": "sightseeing", "price": 80, "hours": 1.5, "slot": "afternoon", "env": "mixed", "sensitivity": "low", "intensity": "low"},
        {"name": "新宿歌舞伎町", "cat": "experience", "price": 0, "hours": 2.0, "slot": "evening", "env": "outdoor", "sensitivity": "medium", "intensity": "medium"},
        {"name": "居酒屋体验", "cat": "food", "price": 300, "hours": 2.0, "slot": "evening", "env": "indoor", "sensitivity": "none", "intensity": "low"},
        {"name": "秋叶原动漫街", "cat": "experience", "price": 0, "hours": 2.0, "slot": "afternoon", "env": "mixed", "sensitivity": "low", "intensity": "medium"},
        {"name": "和服体验", "cat": "experience", "price": 400, "hours": 3.0, "slot": "morning", "env": "indoor", "sensitivity": "low", "intensity": "medium"},
    ],
    "曼谷": [
        {"name": "大皇宫", "cat": "sightseeing", "price": 35, "hours": 2.5, "slot": "morning", "env": "outdoor", "sensitivity": "high", "intensity": "medium"},
        {"name": "卧佛寺", "cat": "sightseeing", "price": 15, "hours": 1.5, "slot": "morning", "env": "outdoor", "sensitivity": "high", "intensity": "medium"},
        {"name": "水上市场", "cat": "experience", "price": 80, "hours": 3.0, "slot": "morning", "env": "outdoor", "sensitivity": "high", "intensity": "high"},
        {"name": "泰国文化博物馆", "cat": "sightseeing", "price": 90, "hours": 2.5, "slot": "morning", "env": "indoor", "sensitivity": "none", "intensity": "low"},
        {"name": "暹罗广场购物", "cat": "experience", "price": 0, "hours": 2.0, "slot": "afternoon", "env": "indoor", "sensitivity": "none", "intensity": "low"},
        {"name": "泰式按摩", "cat": "experience", "price": 120, "hours": 2.0, "slot": "afternoon", "env": "indoor", "sensitivity": "none", "intensity": "low"},
        {"name": "考山路小吃", "cat": "food", "price": 60, "hours": 2.0, "slot": "evening", "env": "outdoor", "sensitivity": "high", "intensity": "medium"},
        {"name": "湄南河夜游", "cat": "experience", "price": 200, "hours": 2.0, "slot": "evening", "env": "outdoor", "sensitivity": "high", "intensity": "low"},
        {"name": "路边摊美食之旅", "cat": "food", "price": 100, "hours": 2.0, "slot": "evening", "env": "outdoor", "sensitivity": "high", "intensity": "medium"},
        {"name": "泰式文化剧场", "cat": "experience", "price": 180, "hours": 2.0, "slot": "evening", "env": "indoor", "sensitivity": "none", "intensity": "low"},
    ],
    "default": [
        {"name": "城市地标", "cat": "sightseeing", "price": 0, "hours": 2.0, "slot": "morning", "env": "outdoor", "sensitivity": "high", "intensity": "medium"},
        {"name": "当地博物馆", "cat": "sightseeing", "price": 80, "hours": 2.5, "slot": "morning", "env": "indoor", "sensitivity": "none", "intensity": "low"},
        {"name": "特色午餐", "cat": "food", "price": 150, "hours": 1.5, "slot": "afternoon", "env": "indoor", "sensitivity": "low", "intensity": "low"},
        {"name": "老城区漫步", "cat": "sightseeing", "price": 0, "hours": 2.0, "slot": "afternoon", "env": "outdoor", "sensitivity": "high", "intensity": "medium"},
        {"name": "日落观景点", "cat": "sightseeing", "price": 50, "hours": 1.0, "slot": "evening", "env": "outdoor", "sensitivity": "high", "intensity": "low"},
        {"name": "当地夜市", "cat": "food", "price": 100, "hours": 2.0, "slot": "evening", "env": "outdoor", "sensitivity": "medium", "intensity": "medium"},
        {"name": "室内文化演出", "cat": "experience", "price": 180, "hours": 2.0, "slot": "evening", "env": "indoor", "sensitivity": "none", "intensity": "low"},
    ],
}


class MockActivityProvider:
    """生成确定性活动候选；兴趣匹配和每日编排仍由 ActivityAgent 负责。"""

    def __init__(self, dataset_version: str | None = None):
        self.dataset_version = dataset_version or settings.MOCK_ACTIVITY_DATA_VERSION

    async def search(self, request: ActivitySearchRequest) -> list[Activity]:
        return _generate_activities(request, self.dataset_version)


def _generate_activities(
    request: ActivitySearchRequest,
    dataset_version: str,
) -> list[Activity]:
    templates = CITY_ACTIVITIES.get(request.city, CITY_ACTIVITIES["default"])
    rng = stable_rng(
        "activity",
        request.model_dump(mode="json"),
        dataset_version,
    )

    results: list[Activity] = []
    for index, tmpl in enumerate(templates):
        candidate_id = stable_candidate_id(
            "activity",
            {"city": request.city, "name": tmpl["name"], "index": index},
            dataset_version,
        )
        results.append(Activity(
            candidate_id=candidate_id,
            source=DataSource.MOCK,
            source_version=dataset_version,
            name=tmpl["name"],
            category=tmpl["cat"],
            location=request.city,
            available_start_date=request.start_date,
            available_end_date=request.end_date,
            duration_hours=tmpl["hours"],
            duration_status=ActivityAttributeStatus.AVAILABLE,
            intensity=ActivityIntensity(tmpl["intensity"]),
            intensity_status=ActivityAttributeStatus.AVAILABLE,
            pace_attribute_source=DataSource.MOCK,
            price=float(tmpl["price"]),
            rating=round(rng.uniform(7.5, 9.5), 1),
            description=f"{request.city} - {tmpl['name']}",
            time_slot=tmpl["slot"],
            environment=ActivityEnvironment(tmpl["env"]),
            weather_sensitivity=WeatherSensitivity(tmpl["sensitivity"]),
        ))

    return sorted(results, key=lambda activity: activity.candidate_id)


def search_activities(
    city: str,
    interests: list[str] | None = None,
    start_date: str = "2026-01-01",
    end_date: str = "2026-01-02",
) -> list[Activity]:
    """兼容原同步工具入口；兴趣参数由 Agent 使用，不影响 Provider 评分。"""
    del interests
    request = ActivitySearchRequest(
        city=city,
        start_date=start_date,
        end_date=end_date,
    )
    return _generate_activities(request, settings.MOCK_ACTIVITY_DATA_VERSION)
