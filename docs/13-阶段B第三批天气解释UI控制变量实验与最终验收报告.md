# 阶段 B 第三批：天气解释 UI、控制变量实验与最终验收报告

验收日期：2026-09-25<br>
范围：阶段 B 最终验收；未开发阶段 C、真实天气 API、真实 LLM、pace 业务消费或整体 UI 重设计。

## 1. 验收结论

阶段 B 的必要验收项已全部通过，可以在既定 Mock MVP 边界内关闭：

- B1 的天气契约和确定性 `MockWeatherProvider` 保持通过。
- B2 的 WeatherAgent、Pipeline 依赖、天气适配规则及天气—预算联合约束保持通过。
- Streamlit 已直接消费后端真实决策记录，展示每日天气、天气选择依据、真实天气驱动变化、硬约束排除、预算联动及各类降级/失败状态。
- 已建立 13 个固定配对场景。Control 与 Treatment 除“天气是否参与活动过滤和排序”外，天气、候选、价格、预算、Provider 版本及其他业务配置均相同。
- 实验原始数据、逐场景费用、统计脚本和服务级演示证据均已落盘并可重复执行。
- 开发前 Baseline：`221 passed, 1 warning`；最终全量：`230 passed, 1 warning in 18.77s`，无回归失败。

这里的“阶段 B 完成”仅指确定性 Mock MVP 的工程和产品验收，不代表真实天气准确率、开放域活动覆盖或真人体验已经得到验证。

## 2. 版本安全、快照与 Git 边界

- 当前分支：`main`。
- 当前 HEAD：`8132dfcded4539a4ca28b1c1fc316b93001c0b08`。
- 工作区包含阶段 0、A、B1、B2、用户原有修改和本批 B3 的累计差异，不能把相对 HEAD 的完整 diff 当作 B3 独立 diff。
- B3 开始前已验证既有阶段归档，并创建 B2 完整工作区快照：`.stage-backups/stage-b2-worktree-main-8132dfc-20260925.tar.gz`。
- 快照大小 226910 bytes，共 178 个条目，SHA-256：`6CD621ABDE01DFC6409B8136977077618AB0937BFD89A30ECC3E708AF88DA92F`。
- 已对 `weather_agent.py`、`weather_compatibility.py`、`pipeline.py`、B2 专项测试及 B2 报告执行归档/当前字节校验，结果一致。
- 快照排除了 `.git`、`.env` 和旧备份；未读取、输出或归档真实密钥。
- 本批未执行 restore、reset、clean、checkout、stage、commit、merge 或 push，也没有删除或覆盖用户原有修改、PRD、阶段报告或未跟踪成果。

### 建议版本整理方案

由仓库所有者先确认原有用户修改归属，然后在备份外单独保存用户原始补丁，再依次建立可审查边界：

1. `baseline/user-original`：只记录用户原始修改。
2. `stage-0-complete`：阶段 0 代码、测试和报告。
3. `stage-a-complete`：A1–A3，基于阶段 0。
4. `stage-b1-complete`、`stage-b2-complete`、`stage-b-complete`：分别记录天气 Provider、天气规划规则和本批展示/实验。
5. 从 `stage-b-complete` 创建 `feature/stage-c-comparison`，阶段 C 每个候选目的地使用独立 `TravelPlanState`。

在所有者审核前不应把当前 60 条工作区状态直接整体提交，更不能把 `.stage-backups`、本地 `.env` 或测试临时目录纳入版本。

## 3. 阶段 B 完整成果

### B1：天气数据层

- `WeatherSearchRequest`、`DailyWeather`、`WeatherResult` 及天气可用性/来源契约。
- 可替换 `WeatherProvider` Protocol 和确定性 `MockWeatherProvider`。
- sunny、rainy、mixed、generated、unavailable 固定情景。
- 请求级稳定摘要与局部随机状态；连续、并发、跨进程和重叠日期均可复现。

### B2：天气业务层和编排

- WeatherAgent 正式接入现有 Pipeline。
- Flight、Hotel、Weather 使用隔离 State 并行，Activity 在天气 READY、明确 unavailable 或 timeout 降级后才启动。
- `WeatherCompatibilityEvaluator` 集中处理硬约束、软评分、规则版本和原因。
- BudgetOptimizer 的活动重选复用天气硬约束，不会为省钱选择恶劣天气下的违规低价候选。
- 无真实可行活动返回 `ACTIVITY_CONSTRAINTS_UNSATISFIED`，与 `BUDGET_INFEASIBLE` 和 `FAILED` 区分。

### B3：解释、实验和产品验收

- 新增逐日期/时段 `ActivityWeatherDecision`，记录基础选择、天气选择、最终选择、硬排除候选、未知候选、是否真实发生变化和预算后变化。
- `WeatherCompatibilityEvaluation` 补充活动评分分解、最终是否被选中及选择结果，均为向后兼容默认字段。
- Streamlit 新增天气页和活动天气说明，不在前端重算业务规则。
- 增加明确的 `baseline` / `weather_aware` 实验模式；仅活动天气过滤和排序不同，Provider 结果不变。
- 增加默认关闭的受控演示 HTTP/UI 入口；普通运行不可见、端点返回 404。

## 4. 最终数据流与解释契约

```text
UserPreferences
      ↓
DestinationAgent
      ↓
┌──────────────┬─────────────┬──────────────┐
│ FlightAgent  │ HotelAgent  │ WeatherAgent │  隔离状态并行
└──────────────┴─────────────┴──────────────┘
      ↓ 天气 READY / 明确回退 / 明确故障
ActivityAgent
      ├─ 同池基础选择（用于可验证对照）
      ├─ 候选天气评价与硬排除
      ├─ 天气感知选择
      └─ ActivityWeatherDecision
      ↓
BudgetLoop（固定 CandidateSnapshot）
      ├─ 活动重选继续遵守当前实验模式及天气硬约束
      └─ 回写预算替换及原因
      ↓
API TravelPlanState → Streamlit 只展示，不推断
```

`ActivityWeatherDecision` 明确区分：

1. `baseline_selection`：Control 的基础选择；天气仅用于事后评价。
2. `weather_rule_selection`：天气参与选择，但最终候选与同池基础选择相同。
3. `weather_driven_change`：相同候选池和约束下，天气选择确实不同于基础选择。
4. `weather_unavailable_fallback`：天气未知，采用基础规则，不能声称已天气验证。
5. `no_feasible_candidate`：硬约束过滤后无候选，不编造活动。

前端只有在 `weather_driven_change=true` 时才展示“基础选择 → 天气方案”的替换描述；普通天气评分不会伪装成已发生替换。预算重选后，`final_candidate_id`、`budget_adjusted` 和 `budget_reason` 会同步更新，因此最终解释与实际方案一致。

## 5. Streamlit 展示验收

新增的最小展示范围：

- 每日天气：日期、天气、降雨概率、温度、恶劣天气标记、Mock 来源和版本。
- 活动解释：天气适配原因、基础/兴趣/天气加权评分；真实变化时展示前后候选。
- 硬排除审计：按日期、时段、候选 ID/名称及后端原因展示。
- 预算联动：继续复用阶段 0 的费用和 adjustment history，并展示活动预算替换的天气/选择依据。
- 明确区分 `ready`、`partial`、`unavailable`、`degraded_timeout`、`FAILED_DATA`、`FAILED_INTERNAL`、`ACTIVITY_CONSTRAINTS_UNSATISFIED`、`BUDGET_INFEASIBLE` 和 `FAILED`。
- unavailable 不显示为晴天；timeout 表示明确基础规划降级；程序故障不显示成功卡。

验收专用模式只有同时设置 `ENABLE_WEATHER_DEMO_CONTROLS=1` 才出现在 Streamlit；页面明确说明天气、候选和价格均为固定 Mock。对应 API 还要求服务端 `ENABLE_WEATHER_DEMO_ENDPOINTS=1`，默认端点返回 404，且未加入 OpenAPI schema。

Streamlit AppTest 实际运行了固定雨天、天气不可用、Provider 超时、无可行活动和必要 Agent 失败，断言了对应解释与业务状态。

## 6. 控制变量实验设计

### 配对原则

每个场景创建两个独立 Pipeline 和两个独立 `TravelPlanState`，不共享可变候选或结果。

| 配置 | Control | Treatment |
|---|---|---|
| 用户偏好、目的地、日期、人数、预算 | 相同 | 相同 |
| 天气 Profile 及逐日结果 | 相同并完整记录 | 相同并完整记录 |
| Flight/Hotel/Activity 候选与 ID | 相同 | 相同 |
| Mock 来源和版本 | 相同 | 相同 |
| 天气硬约束/软评分参与活动选择 | 否 | 是 |
| 城市、日期、时段、兴趣和预算业务规则 | 保留 | 保留 |

Control 不是天气 unavailable 回退。它取得与 Treatment 完全相同的天气，只关闭天气特有的过滤和排序，因而避免把流程差异、Provider 差异或活动池版本差异冒充天气效果。

### 固定场景

`python/experiments/weather_awareness_scenarios.json` 共 13 个场景：

1. `sunny_default`
2. `normal_rain_default`
3. `heavy_rain_default`
4. `mixed_tokyo_four_days`
5. `daily_sequence_bangkok`
6. `unavailable_default`
7. `rainy_bangkok_interest`
8. `higher_cost_indoor`
9. `budget_reselect_indoor`
10. `no_cheaper_weather_compliant`
11. `no_feasible_outdoor_only`
12. `multi_traveler_four_days`
13. `extreme_budget_infeasible`

覆盖东京、曼谷和使用 default 池的首尔，覆盖晴天、普通雨、强降雨、混合逐日天气、不可用、兴趣室内替代、费用提高、预算重选、无更便宜合规候选、无可行活动、多人多日和极低预算。

当前 `DailyWeather` 是日级天气。Fixture 没有构造“同一天上午下雨、下午放晴”；该需求已在 `future_extensions` 中明确记录为时段级天气契约的未来扩展。

## 7. 六项指标实测

原始结果：`python/experiments/results/weather_awareness_results.json`<br>
统计脚本：`python/experiments/run_weather_awareness_experiment.py`

### 汇总

| 指标 | Control | Treatment | 解释 |
|---|---:|---:|---|
| M1 天气冲突率 | 33/63 = 52.38% | 0/60 = 0% | 未知/缺失活动各 6 条，不进入分母 |
| M2 兴趣匹配率 | 63/69 = 91.30% | 48/66 = 72.73% | 当前候选池中天气安全替代会牺牲部分软兴趣匹配 |
| M3 预算合规率 | 12/13 = 92.31% | 11/13 = 84.62% | Treatment 含 1 个预算不可满足和 1 个活动约束无解 |
| M4 天气可验证可行率 | 1/13 = 7.69% | 11/13 = 84.62% | 完整活动方案分别为 13/13 和 12/13；不可用不算已验证天气可行 |
| M5 天气变化解释覆盖率 | 不适用 | 34/34 = 100% | 仅统计真实发生的天气驱动变化 |

M1 没有把未知天气或无活动直接当成零冲突；M3 没有排除无解场景；M4 同时保留“操作上完整”和“天气可验证”两个口径。M2 的下降和 M3 的代价被如实保留，不能将固定规则实验描述为全面改善用户体验。

### M6 逐场景费用

| 场景 | Control 状态/最终费用 | Treatment 状态/最终费用 | 活动费用变化 | 最终总费用变化 |
|---|---:|---:|---:|---:|
| sunny_default | completed / 1550 | completed / 1550 | 50 → 50 | 0 |
| normal_rain_default | completed / 1550 | completed / 1910 | 50 → 410 | +360 |
| heavy_rain_default | completed / 1550 | completed / 1910 | 50 → 410 | +360 |
| mixed_tokyo_four_days | completed / 4200 | completed / 4900 | 1200 → 1900 | +700 |
| daily_sequence_bangkok | completed / 6240 | completed / 6200 | 2240 → 2200 | -40 |
| unavailable_default | completed / 2100 | completed / 2100 | 100 → 100 | 0 |
| rainy_bangkok_interest | completed / 1615 | completed / 1770 | 115 → 270 | +155 |
| higher_cost_indoor | completed / 1500 | completed / 1800 | 0 → 300 | +300 |
| budget_reselect_indoor | completed / 1503 | completed / 1530 | 3 → 30 | +27 |
| no_cheaper_weather_compliant | completed / 1503 | completed / 1440 | 3 → 240 | -63 |
| no_feasible_outdoor_only | completed / 1503 | activity_constraints_unsatisfied / — | 3 → — | — |
| multi_traveler_four_days | completed / 14800 | completed / 15100 | 7800 → 8100 | +300 |
| extreme_budget_infeasible | budget_infeasible / 1400 | budget_infeasible / 2000 | 0 → 600 | +600 |

`budget_reselect_indoor` 中 Treatment 初始费用 1800，经天气合规的活动预算重选后为 1530，活动轮节省 270。`no_cheaper_weather_compliant` 的活动轮明确记录 `improved=false`、节省 0，随后酒店轮继续执行并使最终费用降至 1440。候选原始价格未改变。

## 8. 真实服务级演示

演示证据：`python/experiments/results/weather_b3_service_demo.json`<br>
演示客户端：`python/examples/weather_b3_service_demo.py`

实际启动了 Uvicorn `127.0.0.1:8765` 和 Streamlit `127.0.0.1:8766`：

- FastAPI `/api/health`：HTTP 200，`agents=7`。
- Streamlit `/_stcore/health`：HTTP 200，响应 `ok`。
- 自然语言首次输入缺少日期、人数和旅行风格；一次补充后 `is_complete=true`，签名确认成功，正式规划返回 `COMPLETED / ready / is_mock=true`。
- 固定晴天总费用 1550；固定雨天活动 ID 与晴天不同，3 个时段均有真实变化，总费用 1910，增加 360。
- 强降雨下高价室内替代返回 1800，真实反映费用增加。
- 天气 unavailable 返回 `COMPLETED / unavailable`，活动使用基础回退，不伪造晴天。
- 天气 Provider 超时返回 `COMPLETED / degraded_timeout`，天气结果为空并保留降级原因。
- 户外候选池在强降雨下返回 `ACTIVITY_CONSTRAINTS_UNSATISFIED`，3 个时段问题，未生成预算明细。
- 极低预算返回 `BUDGET_INFEASIBLE`，执行 3 轮调整后最终 2000，不伪造折扣。
- 受控 FlightProvider 故障返回 `FAILED / agent_execution_error`，没有预算明细。

演示完成后只按本次记录的 PID 停止两个进程；8765/8766 均无残留监听。临时服务日志目录已删除，机器可读演示摘要保留在 results 中。函数级测试、TestClient、Streamlit AppTest 与真实进程 HTTP 演示分别记录，没有互相冒充。

## 9. 自动化测试与回归

新增 B3 自动化证据：

- `test_weather_experiment.py`：固定场景覆盖、无时段天气虚构、严格复现、配对不变量、六项指标、预算/无解场景。
- `test_weather_demo.py`：演示端点默认关闭、Control/Treatment 候选与天气一致、unavailable/timeout/约束无解/FAILED 状态，以及 Streamlit 解释渲染。
- 原 B1/B2 Provider、天气适配、预算联合约束，阶段 A 自然语言流程和阶段 0 预算回归全部随全量测试执行。

实际结果：

- B3 实验专项：`5 passed in 0.52s`。
- B3 HTTP/UI 专项：`4 passed, 1 warning in 4.22s`。
- B3 与既有 UI/实验联合定向执行：失败断言修正后全部通过；修正的是 JSON tuple/list 序列化表示和“未伪造晴天”提示文本的检查口径，没有降低业务断言。
- 最终全量：`230 passed, 1 warning in 18.77s`。
- 唯一 warning 是 Starlette `TestClient` 引用 AnyIO 旧别名的第三方弃用提示，不是业务失败。

全量测试继续覆盖跨 Python 进程确定性、连续五次确定性、并发隔离、两入口一致性、候选快照不污染、金额与 adjustment history 对账及 Agent 异常/超时/缺失传播。

## 10. 修改及新增文件

本批 B3 的逻辑修改：

- `python/models/schemas.py`：实验模式、天气决策类型、决策记录和评价分解字段。
- `python/agents/activity_agent.py`：同池基础/天气选择、真实变化记录及 Control/Treatment 模式。
- `python/orchestrator/budget_optimizer.py`：按实验模式执行活动重选并回写最终决策。
- `python/orchestrator/pipeline.py`：向后兼容的活动规划模式注入。
- `python/api/app.py`：7 Agent 描述；默认关闭的 B3 受控演示端点。
- `python/ui/streamlit_app.py`：天气卡、活动解释、排除原因、状态/预算联动及受控演示 UI。
- `python/ui/weather_demo_client.py`：受控演示 HTTP 客户端。
- `python/README.md`：阶段 B 架构、实验、运行和限制说明。

本批 B3 的固定数据、脚本和测试：

- `python/experiments/weather_awareness_scenarios.json`
- `python/experiments/run_weather_awareness_experiment.py`
- `python/experiments/results/weather_awareness_results.json`
- `python/examples/weather_b3_service_demo.py`
- `python/experiments/results/weather_b3_service_demo.json`
- `python/tests/test_weather_experiment.py`
- `python/tests/test_weather_demo.py`
- 本报告。

没有修改 B1 天气 Provider 的核心生成架构，没有重写预算循环，没有接入真实服务，也没有开发多目的地方案比较。

## 11. Git 状态与差异检查

最终检查时：

- 分支 `main`，HEAD 未变化。
- `git status --porcelain`：20 个已跟踪修改、40 个未跟踪条目，共 60 条；这些是阶段 0/A/B 累计工作区，不是 B3 独立计数。
- `git diff --numstat` 的已跟踪累计差异涉及 20 个文件；未跟踪的新 Provider、测试、实验、报告不会出现在该统计中。
- `git diff --check`：退出码 0，无空白错误。输出仅有 Git 对 Windows 工作区未来 LF→CRLF 转换的提示。
- 无暂存文件、无新提交、无 push/merge。

## 12. 收益、代价和剩余风险

### 已验证收益

- Treatment 将可评价活动天气硬冲突从 33/63 降至 0/60。
- 天气可验证可行场景由 1/13 提升至 11/13。
- 34 个真实天气驱动变化全部有基础候选、最终候选和明确原因，解释覆盖 100%。
- 天气约束贯穿初始选择和预算重选，未出现“先天气合规、后为省钱选回违规户外活动”。

### 已验证代价

- 当前固定池中兴趣匹配率从 91.30% 降至 72.73%，说明室内替代活动标签和候选丰富度仍需提高。
- 多个雨天场景费用增加，最高固定配对场景增加 700；极低预算场景 Treatment 最终费用比 Control 高 600。
- 1 个 Treatment 场景因不存在天气合规活动而明确无解；系统选择诚实失败而非编造活动。

### 未解决风险

1. 天气全部是 Mock，不能用于真实出行决策。
2. 只有日级天气，尚不支持同日分时天气和临近预报更新。
3. 活动兴趣依赖文本匹配，缺少成熟的结构化标签体系；这直接影响 M2。
4. `pace` 仍只保存在 PreferencesDraft，没有进入 UserPreferences 或活动密度规则。
5. 金额仍采用统一两位归一化的 float，Decimal 迁移仍是后续技术优化。
6. 受控演示开关是验收工具，不是生产鉴权；部署时必须保持关闭。
7. 当前无真实身份认证和服务端会话；draft receipt 只证明内容完整性。
8. 并行隔离仍依赖每个 Agent 正确声明 `output_fields`。

## 13. 阶段 C 启动依赖与建议

阶段 C 可复用以下稳定契约：

- `UserPreferences`、`PreferencesDraft` 和签名确认入口。
- `CandidateSnapshot`、稳定 candidate ID、来源和数据版本。
- `TravelPlanState` 的三种主要结果及 `ACTIVITY_CONSTRAINTS_UNSATISFIED`。
- `WeatherResult`、`WeatherPlanningMetadata`、`ActivityWeatherDecision` 和 adjustment history。
- `baseline` / `weather_aware` 作为实验配置，而不是面向普通用户的伪实时参数。

建议阶段 C：

1. 为每个候选目的地创建独立、深拷贝的规划 State 和候选快照。
2. Comparator 只读取已完成的方案，不修改方案内候选或价格。
3. 将预算、天气可行性、兴趣匹配和解释覆盖作为可配置、版本化评分维度。
4. Explanation Service 引用现有真实决策记录，不能从最终页面反向猜测原因。
5. 使用相同 Mock 数据版本进行双方案对照，避免数据版本成为隐藏变量。

阶段 B 到此停止，不自动进入阶段 C、真实天气 API、pace 开发或最终 UI 重设计。
