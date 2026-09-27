"""
并行执行器 —— 同时运行多个 Agent，等待全部完成后合并结果。

面试考点:
  - 为什么并行？ Flight/Hotel/Activity 三个 Agent 互不依赖，串行执行浪费时间
  - asyncio.gather vs ThreadPoolExecutor: 纯 IO 密集用 asyncio，CPU 密集用线程
  - 错误处理: 某个 Agent 失败不影响其他 Agent，用 return_exceptions=True
  - 超时控制: asyncio.wait_for 限制单个 Agent 最大执行时间
"""

from __future__ import annotations

import asyncio
import copy
from typing import Sequence

from loguru import logger

from agents.base_agent import BaseAgent, record_agent_failure
from config.settings import settings
from models.schemas import FailureCode, PlanningState, TravelPlanState


class ParallelExecutor:
    """并行执行一组 Agent，将各自输出合并到同一 state 对象。"""

    def __init__(
        self,
        agents: Sequence[BaseAgent],
        timeout: int | None = None,
        *,
        isolate_state: bool = False,
    ):
        self.agents = list(agents)
        self.timeout = timeout or settings.PARALLEL_TIMEOUT
        self.isolate_state = isolate_state

    async def run(self, state: TravelPlanState) -> TravelPlanState:
        logger.info(f"[ParallelExecutor] 启动 {len(self.agents)} 个 Agent 并行执行...")

        baseline_failure_count = len(state.agent_failures)
        baseline_error_count = len(state.error_messages)
        tasks = []
        for agent in self.agents:
            agent_state = state.model_copy(deep=True) if self.isolate_state else state
            tasks.append(asyncio.wait_for(agent.run(agent_state), timeout=self.timeout))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for agent, result in zip(self.agents, results):
            if isinstance(result, Exception):
                is_timeout = isinstance(result, (asyncio.TimeoutError, TimeoutError))
                reason = (
                    f"执行超时（限制 {self.timeout} 秒）"
                    if is_timeout
                    else str(result) or "并行执行失败"
                )
                code = (
                    FailureCode.AGENT_TIMEOUT
                    if is_timeout
                    else FailureCode.AGENT_EXECUTION_ERROR
                )
                err_msg = f"{agent.name} 并行执行失败: {reason}"
                logger.error(err_msg)
                record_agent_failure(
                    state,
                    agent_name=agent.name,
                    code=code,
                    error_type=type(result).__name__,
                    reason=reason,
                    required=agent.required,
                )
            elif self.isolate_state:
                self._merge_isolated_result(
                    state,
                    agent,
                    result,
                    baseline_failure_count=baseline_failure_count,
                    baseline_error_count=baseline_error_count,
                )

        logger.info("[ParallelExecutor] 并行执行完成")
        return state

    @staticmethod
    def _merge_isolated_result(
        state: TravelPlanState,
        agent: BaseAgent,
        result: TravelPlanState,
        *,
        baseline_failure_count: int,
        baseline_error_count: int,
    ) -> None:
        """Deterministically merge only fields declared by each Agent."""

        for field_name in agent.output_fields:
            setattr(state, field_name, copy.deepcopy(getattr(result, field_name)))

        new_failures = result.agent_failures[baseline_failure_count:]
        state.agent_failures.extend(copy.deepcopy(new_failures))
        state.error_messages.extend(
            result.error_messages[baseline_error_count:]
        )
        if any(failure.required for failure in new_failures):
            state.state = PlanningState.FAILED
