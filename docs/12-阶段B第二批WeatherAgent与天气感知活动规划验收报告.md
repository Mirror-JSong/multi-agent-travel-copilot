# 阶段 B 第二批：WeatherAgent 与天气感知活动规划验收报告

验收日期：2026-09-25
范围：仅 B2；未开发 B3、阶段 C、真实天气 API、真实 LLM 或 pace 业务消费。

## 1. 验收结论

B2 的必要验收项已通过：WeatherAgent 正式接入已有 WeatherProvider；Pipeline 调整为 Flight、Hotel、Weather 并行，Activity 在天气结果或明确回退状态确定后执行；活动选择和预算活动重选复用同一套天气硬约束；天气不可用、超时、非法数据、内部故障及无可行活动具有不同结果。

开发前 Baseline 为 `207 passed, 1 warning`。最终全量结果为 `221 passed, 1 warning`。warning 来自 Starlette `TestClient` 使用 AnyIO 旧别名的第三方弃用提示，不是业务失败。

## 2. 版本边界与成果保护

- 当前分支：`main`。
- 开始时 HEAD：`8132dfcded4539a4ca28b1c1fc316b93001c0b08`。
- 仓库在 B2 开始前已包含阶段 0、阶段 A、B1 和用户原有的大量未提交/未跟踪成果；本报告的 Git 差异不能被解释为仅 B2 的独立 diff。
- 已验证既有阶段备份，并创建 B1 完整工作区快照：`.stage-backups/stage-b1-worktree-main-8132dfc-20260925.tar.gz`。
- 快照包含 173 个条目，大小 206573 bytes，SHA-256：`2A7006E4E0F38333657BD4982D59CF41A8BE64D8F2C68CCC5C22B0932A9BAF4F`。
- 快照内容已抽查 B1 核心代码、测试及文档；未归档 `.env`，未发现或输出真实密钥。
- 未执行 `restore`、`reset`、`clean`、`checkout`、`stage`、`commit`、`merge` 或 `push`。
- 原有 `flight_agent.py`、`schemas.py`、`streamlit_app.py`、PRD、阶段报告和阶段 0/A/B1 新文件均保留；未清理任何未跟踪成果。

建议由仓库所有者在确认累计差异归属后，以已验证快照为恢复点，按“阶段 0 → 阶段 A → B1 → B2”建立可审查提交/标签，再从 B2 标签创建独立 B3 分支。本次未代替用户执行这些 Git 写操作。

## 3. Pipeline 执行顺序与状态隔离

```text
PreferenceAgent
      ↓
DestinationAgent
      ↓
┌──────────────┬─────────────┬──────────────┐
│ FlightAgent  │ HotelAgent  │ WeatherAgent │  隔离 State 并行执行
└──────────────┴─────────────┴──────────────┘
      ↓ 编排器按 Agent 顺序合并声明的输出字段
ActivityAgent（天气已 READY/PARTIAL，或已明确 fallback）
      ↓
BudgetLoop（固定候选快照；活动重选复用天气评价器）
```

`ParallelExecutor(isolate_state=True)` 为三个并行 Agent 提供深拷贝状态，只合并各 Agent 在 `output_fields` 中声明的字段和新产生的结构化错误，避免并行任务写同一可变 State。ActivityAgent 仍由既有执行器生命周期管理，但在三路并行完成后单独启动。没有为未来双方案比较重写整个 Pipeline。

健康接口不再硬编码数量，使用实际注册的 7 个业务 Agent 计算。

## 4. WeatherAgent 契约与异常策略

WeatherAgent 只负责编排已有 `WeatherProvider`，没有复制天气生成算法。输入来自已验证的目的地和结束日期排除的旅行日期范围；输出写入：

- `TravelPlanState.weather_result: WeatherResult | None`
- `TravelPlanState.weather_planning: WeatherPlanningMetadata`

状态策略：

| 情况 | `weather_planning.status` | Pipeline 行为 |
|---|---|---|
| 完整有效天气 | `ready` | 天气感知活动规划 |
| 部分日期可用 | `partial` | 可用日期使用天气规则，缺失日期明确基础回退 |
| Provider 明确 unavailable | `unavailable` | 继续基础活动规划，不宣称天气优化 |
| Provider 超时 | `degraded_timeout` | 保留超时原因并继续基础活动规划 |
| Schema、目的地或日期不一致 | `failed_data` | 记录结构化 Agent 故障，终止必要流程 |
| Provider 内部异常 | `failed_internal` | 保留原错误类型与原因，终止必要流程 |

WeatherAgent 会复验目的地、开始/结束日期和每日覆盖；未知天气不会被转换成晴天。超时是明确授权的业务降级，非法数据和内部故障不是降级成功。

## 5. 活动候选与数据版本

活动候选新增/启用以下已有兼容字段：

- `environment`: `indoor` / `outdoor` / `mixed` / `unknown`
- `weather_sensitivity`: `none` / `low` / `medium` / `high` / `unknown`
- `weather_compatible`: 最终选择是否经过充分天气数据验证；回退时为 `null`
- `recommendation_reason`: 最终选择依据

活动数据版本更新为 `2026.09-activity-weather-v2`，候选继续由统一 `ActivityProvider` 提供，保留稳定 `candidate_id`、`source` 和 `source_version`。既有价格没有被修改；新增的是具有自身真实 Mock 价格的新候选。

雨天候选缺口修复：

- 曼谷 morning：新增室内低敏感“泰国文化博物馆”。
- 曼谷 evening：新增室内低敏感“泰式文化剧场”。
- default evening：新增室内低敏感“室内文化演出”。
- 东京和现有池的候选逐项显式标注；没有把全部 `unknown` 批量改成室内。

专项测试证明曼谷及 default 池的 morning、afternoon、evening 都存在明确室内、低/无敏感候选。

## 6. 天气适配与活动选择规则

集中规则位于 `WeatherCompatibilityEvaluator`，注入式配置位于 `WeatherCompatibilityPolicy`。策略版本为 `weather-compatibility-v1`，默认阈值：一般降雨 30%、硬降雨 70%、高敏感活动降雨限制 40%、天气软评分权重 0.5。阈值顺序受到 Pydantic 校验；测试可注入其他合法策略。

每个评价输出：日期、时段、候选 ID、策略版本、天气与降雨概率、硬约束结果、0–10 天气分、数据是否足够以及理由。

确定性选择顺序：

1. Provider 查询先限定城市和旅行日期范围。
2. 按具体日期和 `morning` / `afternoon` / `evening` 时段过滤。
3. 执行天气硬约束：恶劣天气排除高暴露室外/高敏感候选；高风险下属性未知的候选不能视为已验证安全。
4. 对合格候选计算 `基础 rating + 兴趣加分 + 天气分 × 权重`。
5. 同分时依次按更低价格和稳定 `candidate_id` 排序。

低降雨风险下室外活动获得较高软评分；普通降雨不会禁止全部室外活动，低敏感候选仍可保留但降分；恶劣天气优先低暴露室内活动。当前兴趣在初始 ActivityAgent 中仍沿用既有软评分语义，没有把所有兴趣突然升级为硬约束；预算活动重选继续遵守既有兴趣保留规则。

若任一日期/时段没有合格真实候选，系统不编造活动，返回 `ACTIVITY_CONSTRAINTS_UNSATISFIED` 和逐时段 `constraint_issues`。该状态不同于 `BUDGET_INFEASIBLE`，也不同于程序 `FAILED`，且不会进入虚假的完整预算结算。

## 7. 天气—预算联合约束

BudgetOptimizer 的活动轮次在固定 `CandidateSnapshot` 上执行以下过滤：城市、日期（结束日期排除）、时段、价格更低、既有兴趣约束，以及同一 `WeatherCompatibilityEvaluator` 的天气硬约束。

候选价格保持不变；重选后按实际候选价格和旅行人数重算；`ActivityReplacement.selection_reason` 保存天气适配理由，候选评价写入活动结果。若不存在更便宜且天气合规的候选，该轮 `improved=false`、节省金额为 0，并继续后续酒店/航班策略。

固定联合测试中，已选室内活动 `indoor-expensive` 单价 100，候选池另有室外活动 `outdoor-cheap` 单价 1；当天为强降雨。实际结果：活动轮次未选择低价室外候选，前后 ID 均为 `indoor-expensive`，`improved=false`、`saved_amount=0`，候选快照价格未变。

## 8. 可解释数据

`/api/plan/full` 当前会返回：

- 每日 WeatherResult、来源、数据版本、Mock 标记及可用状态。
- `weather_planning` 的正常、回退或失败原因。
- 所有被评价活动的 `weather_evaluations`，包括被硬过滤/软降权的依据。
- `weather_optimized` 与 `weather_fallback_reason`。
- 最终活动的 `weather_compatible` 与 `recommendation_reason`。
- 无可行候选的 `constraint_issues`。
- 预算替换的候选 ID、费用变化和 `selection_reason`。

这些字段描述规则评价和最终选择，不把没有对照选择的活动误称为“因天气被替换”。专门的用户解释 UI 留待 B3。

## 9. 自动化测试覆盖矩阵

| 验收项 | 自动化证据 |
|---|---|
| T1 WeatherAgent 正常契约 | `test_t1_weather_agent_returns_validated_result_and_metadata` |
| T2 三个 Agent 并行 | `test_t2_t3_flight_hotel_weather_run_in_parallel_before_activity` 使用门闩证明三者同时进入 Provider |
| T3 Activity 等待天气 | 同一测试在释放三路任务前断言 Activity 尚未启动，并在启动时检查天气状态 |
| T4 晴天选择 | `test_t4_t5_t6_sunny_and_rainy_change_selection_on_same_candidates` |
| T5 雨天排除不合适户外 | 同上，且验证排除理由 |
| T6 同一候选池晴雨差异 | 同上，先严格比较两边完整候选快照相等 |
| T7 兴趣保留 | `test_t7_t8_rainy_selection_keeps_interest_and_all_time_slots` |
| T8 三时段 | 同上，严格断言 morning/afternoon/evening |
| T9 unavailable 回退 | `test_t9_weather_unavailable_uses_explicit_unoptimized_fallback` |
| T10 超时/非法/内部异常 | `test_t10_weather_timeout_degrades_but_invalid_and_internal_fail` |
| T11 unknown 不冒充雨天安全 | `test_t11_unknown_activity_metadata_is_not_rain_verified` |
| T12 无合适活动 | `test_t12_no_rain_compatible_candidate_returns_business_constraint_state` |
| T13 预算不选违规低价活动 | `test_t13_budget_optimizer_rejects_cheaper_weather_incompatible_activity` |
| T14 费用和历史一致 | `test_t14_t15_costs_reconcile_and_five_runs_are_identical` 及原预算测试 |
| T15 连续五次确定性 | 同上，完整 State JSON 五次严格相等 |
| T16 并发隔离 | `test_t16_concurrent_weather_plans_do_not_pollute_each_other` |
| T17 两入口一致 | `test_t7_natural_and_structured_entries_produce_identical_plans` 已扩展比较天气结果、元数据和评价；阶段 A 实验 M5 为 8/8 |
| T18 原 207 项回归 | 最终全量 `221 passed`，无新增失败 |

补充测试还覆盖：普通降雨下低敏感户外候选不会被一刀切；非法策略阈值被拒绝；健康接口返回 7 个 Agent；完整 API 暴露天气和候选快照；曼谷/default 三时段雨天池完备。

## 10. 实际运行记录

### 测试

- 开发前：`207 passed, 1 warning in 15.52s`。
- B2 专项：`14 passed, 1 warning in 0.88s`。
- 天气 Provider + B2 联合专项：`41 passed, 1 warning in 2.03s`（加入策略版本前后的定向执行，随后 B2 专项再次通过）。
- 最终全量：`221 passed, 1 warning`。

一次未设置工作区 TEMP 的全量运行出现 8 个 `tmp_path` setup permission errors；将 `TEMP`/`TMP` 指向已有 `.test-tmp` 后，这些环境错误全部消失。另一次真实全量定位到阶段 A 记录结果因 B2 活动版本/天气规划发生合法变化；使用原实验脚本、原 8 场景、原指标口径重新生成记录后，严格相等断言恢复通过，没有删除或弱化断言。

阶段 A 重跑指标保持：M1 8/8、M2 51/51、M5 两入口规划一致 8/8、M6 异常恢复 7/7。M5 的规划签名已补充天气日序列、天气元数据、最终活动天气适配与选择理由。

### 固定同池场景

输入固定为北京出发、2026-10-01 至 2026-10-05、1 人、预算 50000、同一活动数据版本。实际执行：

| 情景 | 状态 | 首日活动 | 活动费用 | 总费用 | 天气优化 |
|---|---|---|---:|---:|---|
| sunny | `COMPLETED / ready` | 城市地标、老城区漫步、日落观景点 | 200 | 4028 | 是 |
| rainy | `COMPLETED / ready` | 当地博物馆、特色午餐、室内文化演出 | 1640 | 5468 | 是 |
| unavailable | `COMPLETED / unavailable` | 当地博物馆、特色午餐、日落观景点 | 1120 | 4948 | 否，明确回退 |

三种情景使用相同候选集合和 `2026.09-activity-weather-v2`，差异不是由更换活动池产生。

## 11. 修改与新增文件

B2 本批实际修改/新增：

- 配置与文档：`python/.env.example`、`python/config/settings.py`、`python/README.md`、本报告。
- 数据契约：`python/models/schemas.py`。
- Agent：`python/agents/weather_agent.py`（新增）、`python/agents/weather_compatibility.py`（新增）、`python/agents/activity_agent.py`、`python/agents/base_agent.py`、`python/agents/flight_agent.py`、`python/agents/hotel_agent.py`、`python/agents/budget_agent.py`、`python/agents/__init__.py`。
- 编排与预算：`python/orchestrator/pipeline.py`、`python/orchestrator/parallel.py`、`python/orchestrator/budget_loop.py`、`python/orchestrator/budget_optimizer.py`。
- Provider 数据：`python/tools/activity_search.py`。
- API：`python/api/app.py`。
- 测试与 Fixture：`python/tests/test_weather_activity_planning.py`（新增）、`python/tests/fixtures/weather_activity_scenarios.json`（新增）、`python/tests/test_preference_plan.py`。
- 可重复实验：`python/experiments/run_preference_experiment.py`、`python/experiments/results/preference_clarification_results.json`。

未修改 B1 WeatherProvider 的核心确定性生成架构，也未修改阶段 0 的价格生成或以直接改价实现预算优化。

## 12. API 兼容性

- 原 `/api/plan`、`/api/plan/full`、自然语言确认入口的请求模型不变。
- `/api/plan/full` 新增天气、活动评价和约束问题字段，属于向后兼容扩展。
- `/api/health` 的 Agent 数由 6 更新为实际的 7。
- 正常合法请求仍返回既有 `COMPLETED` / `BUDGET_INFEASIBLE` / `FAILED`；新增 `ACTIVITY_CONSTRAINTS_UNSATISFIED` 只在没有真实可行活动时出现，避免虚假成功。枚举严格客户端需要接受该新增业务状态。
- 自然语言和结构化入口继续调用同一 Pipeline；固定实验和 API 测试均验证结果一致。

## 13. Git 检查

- `git diff --check`：退出码 0，无空白错误；Git 仅提示 Windows 工作区未来可能进行 LF→CRLF 转换。
- `git diff --stat` 是相对原 HEAD 的累计差异，包含阶段 0/A/B1，不能作为 B2 独立统计。
- `git status --short` 仍显示大量既有修改和未跟踪文件，以及本批新增文件；没有暂存或提交。

## 14. 遗留风险与 B3 准备

1. 所有天气仍是确定性 Mock 情景，不是实时或可信远期预报。
2. B3 需使用同一活动版本 `2026.09-activity-weather-v2` 做“天气 unavailable 基线 vs sunny/rainy”控制变量实验，不能拿旧活动池与新版活动池比较。
3. Weather 场景目前通过依赖注入固定，未开放为面向普通用户的生产请求参数；B3 实验应继续使用隔离测试配置，避免让客户端伪造天气。
4. Streamlit 尚无专门天气解释组件；B3 可直接消费 `/api/plan/full` 中已有数据，不应在前端重算规则。
5. `pace` 仍只保存在 PreferencesDraft，未进入 UserPreferences 或影响活动密度；如后续实现必须单独立项和对照，不能把 pace 效果归因于天气。
6. 当前兴趣仍主要通过名称、类别和描述匹配，尚无独立结构化活动标签；扩展开放域候选前应定义标签词表和“软偏好/硬要求”的显式契约。
7. 金额仍为 float 并在统一费用模块归一化到两位；Decimal 迁移仍是后续技术优化。
8. 并行隔离依赖 Agent 正确声明 `output_fields`；未来新 Agent 必须遵守该契约。阶段 C 的不同目的地仍应创建独立 `TravelPlanState`，不能复用本批单目的地 State。
9. 尚未执行 B3 的专门用户展示和天气效果统计，因此本报告只宣称 B2 工程规则与固定测试通过，不宣称真人体验、真实预报准确率或开放域泛化效果。

## 15. B3 接口准备清单

- 使用 `weather_result.daily_weather` 展示每日 Mock 天气和可用性。
- 使用 `weather_planning` 展示回退/失败原因。
- 使用 `activity_result.weather_evaluations` 展示过滤和降权依据。
- 使用最终活动的 `weather_compatible` / `recommendation_reason` 展示选择理由。
- 使用 `constraint_issues` 展示无可行活动，而不是成功卡片。
- 使用 `weather_activity_scenarios.json` 固定同一偏好、活动数据版本和 sunny/rainy/unavailable 控制组。
- 单独定义 B3 指标：天气冲突数、三时段完整率、兴趣保持率、费用变化、预算重选天气合规率；不得推导真人满意度。

阶段 B2 到此停止，不自动进入 B3 或阶段 C。
