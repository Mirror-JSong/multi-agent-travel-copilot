"""
Flight Agent —— 航班搜索 Agent。

职责: 搜索航班、比价、推荐最优组合（价格 × 时长 × 中转次数）。
在并行阶段执行，与 Hotel Agent / Activity Agent 同时运行。

面试考点:
  - 并行执行: 与其他两个 Agent 通过 asyncio.gather 并行，延迟降低 60%
  - 评分函数: 多因素加权（价格占 50%, 时长 30%, 中转 20%）
  - Mock vs 真实: 生产环境对接 Amadeus GDS API
"""

from __future__ import annotations

from loguru import logger

from models.schemas import Flight, FlightSearchRequest, FlightSearchResult, TravelPlanState
from tools.flight_search import MockFlightProvider
from tools.providers import FlightProvider

from .base_agent import BaseAgent


class FlightAgent(BaseAgent):
    name = "FlightAgent"
    output_fields = ("flight_result",)

    def __init__(self, provider: FlightProvider | None = None) -> None:
        super().__init__()
        self.provider = provider or MockFlightProvider()

    async def execute(self, state: TravelPlanState) -> TravelPlanState:
        pref = state.preferences           #这里从全局state里拿到用户的偏好信息和已选目的地
        dest = state.selected_destination
        if pref is None or dest is None:
            raise ValueError("缺少偏好或目的地信息")

        outbound = await self.provider.search(FlightSearchRequest(
            departure_city=pref.departure_city,
            arrival_city=dest.city,
            travel_date=pref.start_date,
        ))  #生成去程
        returns = await self.provider.search(FlightSearchRequest(
            departure_city=dest.city,
            arrival_city=pref.departure_city,
            travel_date=pref.end_date,
        ))  #生成返程
        #这里是随机造出一些测试数据，并没有真的去携程、航空公司或者 Amadeus 查询。
        # 随机值来自 Provider 的请求级局部 RNG，因此相同输入和数据版本可复现。

        #注意！！！不是简单找最便宜的航班，而是在做一个多因素评分，选出价格 × 时长 × 中转次数的综合最优组合即最优航班。
        rec_out = self._best_flight(outbound, pref.budget * 0.3)   #pref.budget * 0.3这个就是给机票分配的预算，假设总预算的 30% 用于机票。
        rec_ret = self._best_flight(returns, pref.budget * 0.3)

        total = (rec_out.price if rec_out else 0) + (rec_ret.price if rec_ret else 0)  #一人往返机票费用
        total *= pref.num_travelers  #乘以人数，得到总机票费用

        state.flight_result = FlightSearchResult(
            outbound_flights=outbound,     #所有候选去程航班
            return_flights=returns,        #所有候选返程航班
            recommended_outbound=rec_out,  #推荐去程航班
            recommended_return=rec_ret,    #推荐返程航班
            total_flight_cost=total,       #推荐方案总机票费用
        )
        logger.info(f"[{self.name}] 找到 {len(outbound)} 个去程 + {len(returns)} 个返程航班, 推荐总价: ¥{total:.0f}")
        return state

    #具体评分公式在这里：航班总评分 = 价格评分 * 50% + 时长评分 * 30% + 中转评分 * 20% + 预算分数（如果价格在预算范围内，额外加 10 分）
    @staticmethod
    def _best_flight(flights: list[Flight], budget_share: float) -> Flight | None:
        if not flights:
            return None

        max_price = max(f.price for f in flights) or 1
        max_dur = max(f.duration_hours for f in flights) or 1

        def score(f: Flight) -> float:
            price_score = 1 - (f.price / max_price)
            dur_score = 1 - (f.duration_hours / max_dur)
            stop_score = 1 - (f.stops / 3)
            budget_bonus = 10 if f.price <= budget_share else 0                        #如果这个航班价格没有超过分配给机票的预算，就额外加 10 分。
            return price_score * 50 + dur_score * 30 + stop_score * 20 + budget_bonus  #总评分

        return max(
            flights,
            key=lambda flight: (score(flight), -flight.price, flight.candidate_id),
        )
