"""
Hotel Agent —— 酒店搜索 Agent。

职责: 搜索酒店，匹配用户偏好（位置/价格/评分/设施）。
在并行阶段执行，与 Flight Agent / Activity Agent 同时运行。

面试考点:
  - 按旅行风格自动调整星级/价格范围
  - 总花费 = 每晚价格 × 入住天数 × 房间数
  - 降级策略: 超预算时可降低星级或选择距离稍远的酒店
"""

from __future__ import annotations

from datetime import datetime

from loguru import logger

from models.schemas import Hotel, HotelSearchRequest, HotelSearchResult, TravelPlanState
from tools.hotel_search import MockHotelProvider
from tools.providers import HotelProvider

from .base_agent import BaseAgent


class HotelAgent(BaseAgent):
    name = "HotelAgent"
    output_fields = ("hotel_result",)

    def __init__(self, provider: HotelProvider | None = None) -> None:
        super().__init__()
        self.provider = provider or MockHotelProvider()

    async def execute(self, state: TravelPlanState) -> TravelPlanState:
        pref = state.preferences
        dest = state.selected_destination
        if pref is None or dest is None:
            raise ValueError("缺少偏好或目的地信息")

        nights = self._calc_nights(pref.start_date, pref.end_date)
        hotels = await self.provider.search(HotelSearchRequest(
            city=dest.city,
            check_in=pref.start_date,
            check_out=pref.end_date,
            travel_style=pref.travel_style,
        ))

        rec = self._best_hotel(hotels, pref.budget * 0.4 / max(nights, 1), pref.travel_style.value)
        total = (rec.price_per_night * nights * max(1, (pref.num_travelers + 1) // 2)) if rec else 0

        state.hotel_result = HotelSearchResult(
            hotels=hotels,
            recommended=rec,
            total_nights=nights,
            total_hotel_cost=total,
        )
        logger.info(f"[{self.name}] {dest.city} 找到 {len(hotels)} 家酒店, 推荐: {rec.name if rec else 'N/A'}, 总价: ¥{total:.0f}")
        return state

    @staticmethod
    def _calc_nights(start: str, end: str) -> int:
        d1 = datetime.strptime(start, "%Y-%m-%d")
        d2 = datetime.strptime(end, "%Y-%m-%d")
        nights = (d2 - d1).days
        if nights <= 0:
            raise ValueError("结束日期必须晚于开始日期")
        return nights

    @staticmethod
    def _best_hotel(hotels: list[Hotel], nightly_budget: float, style: str) -> Hotel | None:
        if not hotels:
            return None

        star_pref = {"budget": 2.5, "comfort": 3.5, "luxury": 4.5,
                     "adventure": 2.5, "cultural": 3.5, "relaxation": 4.0}
        target_star = star_pref.get(style, 3.5)

        def score(h: Hotel) -> float:
            price_ok = 20 if h.price_per_night <= nightly_budget else 0
            star_fit = 30 - abs(h.star_rating - target_star) * 10
            rating_s = h.user_rating * 3
            dist_s = max(0, 10 - h.distance_to_center_km * 3)
            return price_ok + star_fit + rating_s + dist_s

        return max(
            hotels,
            key=lambda hotel: (score(hotel), -hotel.price_per_night, hotel.candidate_id),
        )
