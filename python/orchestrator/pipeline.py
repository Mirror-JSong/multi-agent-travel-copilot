"""
Pipeline 编排器 —— 串联整个行程规划流程。

架构:
  用户输入 → PreferenceAgent → DestinationAgent
  → [FlightAgent + HotelAgent + WeatherAgent (并行)]
  → ActivityAgent（等待天气或明确降级）
  → BudgetAgent (预算校验)
  ↓ (超预算则循环调整)
  输出最终行程

面试考点:
  - Pipeline 模式 vs DAG 模式 vs State Machine 模式的区别
  - Pipeline 模式适合本项目的原因: 流程线性+并行分叉+循环，复杂度适中
  - 错误传播: 前序 Agent 失败则后序不执行，错误信息记录在 state 中
"""

from __future__ import annotations

from loguru import logger

from agents import (
    ActivityAgent,
    BudgetAgent,
    DestinationAgent,
    FlightAgent,
    HotelAgent,
    PreferenceAgent,
    WeatherAgent,
)
from models.schemas import (
    ActivityPlanningMode,
    Destination,
    DestinationRecommendation,
    PlanningState,
    TravelPlanState,
    UserPreferences,
)

from .budget_loop import BudgetLoopController
from .budget_optimizer import BudgetOptimizer
from .parallel import ParallelExecutor


class TravelPlanningPipeline:
    """主编排器: 串联所有 Agent 完成行程规划。"""

    def __init__(
        self,
        *,
        flight_agent: FlightAgent | None = None,
        hotel_agent: HotelAgent | None = None,
        weather_agent: WeatherAgent | None = None,
        activity_agent: ActivityAgent | None = None,
        activity_planning_mode: ActivityPlanningMode = ActivityPlanningMode.WEATHER_AWARE,
    ) -> None:
        self.preference_agent = PreferenceAgent()
        self.destination_agent = DestinationAgent()
        self.flight_agent = flight_agent or FlightAgent()
        self.hotel_agent = hotel_agent or HotelAgent()
        self.weather_agent = weather_agent or WeatherAgent()
        self.activity_agent = activity_agent or ActivityAgent(
            require_weather_context=True,
            planning_mode=activity_planning_mode,
        )
        self.budget_agent = BudgetAgent()

        self.parallel_executor = ParallelExecutor(
            agents=[self.flight_agent, self.hotel_agent, self.weather_agent],
            isolate_state=True,
        )
        self.budget_loop = BudgetLoopController(
            parallel_executor=None,
            budget_agent=self.budget_agent,
            optimizer=BudgetOptimizer(
                self.activity_agent.weather_evaluator,
                self.activity_agent.pace_evaluator,
                activity_planning_mode=self.activity_agent.planning_mode,
            ),
        )

    async def run(self, preferences: UserPreferences) -> TravelPlanState:
        """Run the historical single-destination flow unchanged in business terms."""
        if not isinstance(preferences, UserPreferences):
            raise TypeError(
                "TravelPlanningPipeline requires validated UserPreferences; "
                "an incomplete preference draft cannot start planning"
            )
        state = TravelPlanState(preferences=preferences.model_copy(deep=True))
        logger.info("=" * 60)
        logger.info("🚀 行程规划 Pipeline 启动")
        logger.info("=" * 60)

        # ── 阶段 1: 偏好收集 ──
        state = await self.preference_agent.run(state)
        if state.state == PlanningState.FAILED:
            return state

        # ── 阶段 2: 目的地推荐 ──
        state = await self.destination_agent.run(state)
        if state.state == PlanningState.FAILED:
            return state

        return await self._run_selected_destination_state(state)

    async def run_for_destination(
        self,
        preferences: UserPreferences,
        destination: Destination,
    ) -> TravelPlanState:
        """Plan one validated destination without rerunning DestinationAgent.

        A deep copy of both inputs is owned by this invocation.  This makes the
        method safe for C1 orchestration while preserving the existing Agents,
        Provider selection and BudgetOptimizer implementation.
        """
        if not isinstance(preferences, UserPreferences):
            raise TypeError(
                "TravelPlanningPipeline requires validated UserPreferences; "
                "an incomplete preference draft cannot start planning"
            )
        if not isinstance(destination, Destination):
            raise TypeError("run_for_destination requires a validated Destination")
        if not destination.city.strip() or not destination.country.strip():
            raise ValueError("指定目的地必须包含非空城市和国家")

        owned_destination = destination.model_copy(deep=True)
        state = TravelPlanState(preferences=preferences.model_copy(deep=True))
        logger.info("=" * 60)
        logger.info(f"🚀 指定目的地 Pipeline 启动: {owned_destination.city}")
        logger.info("=" * 60)

        state = await self.preference_agent.run(state)
        if state.state == PlanningState.FAILED:
            return state

        state.destination_rec = DestinationRecommendation(
            destinations=[owned_destination.model_copy(deep=True)],
            selected=owned_destination.model_copy(deep=True),
            reasoning="C1 指定目的地独立规划；未重新执行 DestinationAgent。",
        )
        state.state = PlanningState.SEARCHING_PARALLEL
        return await self._run_selected_destination_state(state)

    async def _run_selected_destination_state(
        self,
        state: TravelPlanState,
    ) -> TravelPlanState:
        """Execute the existing post-destination planning stages."""
        if state.preferences is None or state.selected_destination is None:
            raise ValueError("执行指定目的地阶段前必须准备偏好与 selected_destination")

        # ── 阶段 3: 航班、酒店、天气并行；使用隔离状态确定性合并 ──
        state = await self.parallel_executor.run(state)
        if state.state == PlanningState.FAILED:
            return state

        # ── 阶段 4: 天气状态明确后才能生成活动 ──
        activity_executor = ParallelExecutor(
            [self.activity_agent],
            timeout=self.parallel_executor.timeout,
        )
        state = await activity_executor.run(state)
        if state.state in {
            PlanningState.FAILED,
            PlanningState.ACTIVITY_CONSTRAINTS_UNSATISFIED,
            PlanningState.PACE_CONSTRAINTS_UNSATISFIED,
        }:
            return state

        # ── 阶段 5: 固定候选快照上的预算循环 ──
        state = await self.budget_loop.run(state)

        logger.info("=" * 60)
        logger.info(f"Pipeline 完成, 状态: {state.state.value}")
        if state.budget_breakdown:
            bb = state.budget_breakdown
            logger.info(f"总费用: ¥{bb.total_cost:.0f} / 预算: ¥{bb.budget:.0f}")
        logger.info("=" * 60)

        return state


async def quick_plan(
    budget: float = 10000,
    departure: str = "北京",
    start: str = "2026-05-01",
    end: str = "2026-05-05",
    style: str = "comfort",
    travelers: int = 1,
) -> TravelPlanState:
    """快速规划入口，便于 CLI / 测试调用。"""
    from models.schemas import TravelStyle

    prefs = UserPreferences(
        budget=budget,
        travel_style=TravelStyle(style),
        departure_city=departure,
        start_date=start,
        end_date=end,
        num_travelers=travelers,
    )
    pipeline = TravelPlanningPipeline()
    return await pipeline.run(prefs)
