# Multi-Agent Travel Copilot（Python Mock MVP）

当前工程基线包含阶段 0 的确定性规划与预算闭环、阶段 A 的自然语言偏好澄清与完整规划交互，以及阶段 B 的 WeatherAgent、天气感知活动选择、天气—预算联合约束、解释 UI 和控制变量实验。

> 当前航班、酒店、活动及天气工具数据均为 Mock 数据，不是实时库存、报价或预报，也不应作为真实预订依据。默认配置不会调用真实 LLM 或付费第三方 API。

正式默认网页为 `ui/streamlit_classic_app.py`，默认端口 `8785`。D2/D3 五步工作台
`ui/streamlit_app.py` 作为备选界面继续保留。

## 环境与安装

- Python 3.10+（本阶段验收使用 Python 3.11.16）
- 建议在独立虚拟环境中运行

```bash
cd python
python -m pip install -r requirements.txt
```

复制 `.env.example` 为 `.env` 可覆盖默认配置。Mock 模式不需要 API Key。阶段 0 主要配置：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `LLM_PROVIDER` | `mock` | 保持 Mock 时不调用真实模型 |
| `MOCK_DATA_VERSION` | `2026.09-v1` | 确定性候选数据版本 |
| `MOCK_ACTIVITY_DATA_VERSION` | `2026.09-activity-weather-v2` | 带室内外及天气敏感属性的活动数据版本 |
| `WEATHER_PROVIDER_TIMEOUT` | `5` | 天气 Provider 超时后的显式基础规划回退阈值（秒） |
| `BUDGET_POLICY_VERSION` | `budget-policy-v1` | 预算调整规则版本 |
| `BUDGET_MAX_RETRIES` | `3` | 活动、酒店、航班三轮上限 |
| `BUDGET_MIN_HOTEL_RATING` | `7.0` | 酒店降级最低用户评分 |
| `PARALLEL_TIMEOUT` | `30` | 单个必要 Agent 超时秒数 |
| `TRAVEL_API_BASE_URL` | `http://127.0.0.1:8000` | Streamlit 调用 FastAPI 的地址 |
| `PREFERENCE_DRAFT_SIGNING_KEY` | 进程内随机值 | 草稿回执签名；多进程或重启后续聊时应配置私有随机值 |

## 本地启动

```bash
# CLI
python main.py --budget 15000 --departure 上海 --start 2026-06-01 --end 2026-06-07

# FastAPI：http://127.0.0.1:8000/docs
python -m uvicorn api.app:app --host 127.0.0.1 --port 8000

# 正式 Classic UI：http://127.0.0.1:8785
python -m streamlit run ui/streamlit_classic_app.py --server.address 127.0.0.1 --server.port 8785

# 备选 D2/D3 五步工作台：http://127.0.0.1:8501
python -m streamlit run ui/streamlit_app.py --server.address 127.0.0.1 --server.port 8501

# 全量测试
python -m pytest -q

# Classic UI 状态、API 与 Streamlit 专项测试
python -m pytest -q tests/test_classic_ui.py

# 仅预算专项测试
python -m pytest -q tests/test_budget_optimization.py

# 仅天气 Provider 专项测试
python -m pytest -q tests/test_weather_provider.py

# WeatherAgent、天气感知活动与预算联合约束
python -m pytest -q tests/test_weather_activity_planning.py

# 阶段 A 固定场景产品效果实验（结果写入 experiments/results/）
python experiments/run_preference_experiment.py

# 阶段 B 天气 Control/Treatment 配对实验
# Windows PowerShell 需先设置：$env:PYTHONPATH=(Get-Location).Path
python experiments/run_weather_awareness_experiment.py
```

Windows PowerShell 可直接调用本项目已经验收的 `travel-agent` 环境。请分别打开两个终端：

```powershell
# 终端 1：FastAPI
cd E:\Files\Documents\002_Mirror\AI\multi-agent-travel-planner\python
$TravelPython = "D:\AUSTstudy\anaconda\envs\travel-agent\python.exe"
& $TravelPython -m uvicorn api.app:app --host 127.0.0.1 --port 8000
```

```powershell
# 终端 2：正式 Classic UI
cd E:\Files\Documents\002_Mirror\AI\multi-agent-travel-planner\python
$TravelPython = "D:\AUSTstudy\anaconda\envs\travel-agent\python.exe"
$env:TRAVEL_API_BASE_URL = "http://127.0.0.1:8000"
& $TravelPython -m streamlit run ui/streamlit_classic_app.py --server.address 127.0.0.1 --server.port 8785
```

如果环境安装在其他位置，只需将 `$TravelPython` 改为对应 `travel-agent` 环境中的
`python.exe`。不要改用缺少 Streamlit 依赖的 base Python。

阶段 B 实验的 Control 与 Treatment 使用完全相同的天气、候选、价格、预算和业务配置。Control 仍记录天气供客观评价，但天气不参与活动过滤或排序；Treatment 启用天气硬约束和软评分。该开关仅用于明确的实验依赖注入，不改变 Provider 数据。

## API

保留以下接口：

- `GET /api/health`
- `POST /api/plan`：兼容原摘要响应，并增加明确的 `status` 和 `message`。
- `POST /api/plan/full`：返回候选快照、初始选择、费用明细和调整历史。
- `POST /api/preferences/parse`：首次解析自然语言，返回草稿、字段来源和追问。
- `POST /api/preferences/clarify`：携带上一轮草稿和签名回执进行补充或修改。
- `POST /api/preferences/plan`：验证完整签名草稿和显式确认后，调用现有 `TravelPlanningPipeline` 并返回完整规划状态。

偏好首次解析示例：

```json
{
  "text": "国庆想和朋友从上海出去玩五天，预算一万五，不想太累。",
  "reference_date": "2026-09-24",
  "config": {
    "provider": "mock",
    "locale": "zh-CN",
    "version": "mock-preference-parser-v1"
  }
}
```

澄清和确认接口需要原样回传上一轮响应中的 `draft` 和 `draft_receipt`。回执同时绑定日期基准和解析配置；任一内容被客户端修改时返回 HTTP 422。服务端不保存会话，Streamlit 使用各浏览器自己的 `session_state` 保存草稿、历史和规划结果。

偏好缺失或存在日期冲突时仍返回 HTTP 200 的澄清业务结果；请求体格式、日期基准、解析配置或草稿回执非法时返回 HTTP 422。只有服务端重新评估为完整的草稿才能规划；客户端伪造 `is_complete` 或 `can_confirm` 不会被接受。完整但未显式确认返回 HTTP 409。

确认规划请求示例：

```json
{
  "draft": {"上一轮响应中的完整 PreferencesDraft": "..."},
  "draft_receipt": "上一轮响应中的 64 位签名回执",
  "confirmed": true,
  "reference_date": "2026-09-24",
  "config": {
    "provider": "mock",
    "locale": "zh-CN",
    "version": "mock-preference-parser-v1"
  }
}
```

响应同时包含服务端确认后的 `preferences`、原草稿、仅保留在草稿中的字段和完整 `TravelPlanState`。`draft_receipt` 只提供完整性边界；当前没有用户身份认证，因此确认动作不能证明特定自然人的身份。若部署多个 Worker，应显式配置同一私密 `PREFERENCE_DRAFT_SIGNING_KEY`。

请求示例：

```json
{
  "budget": 10000,
  "departure_city": "北京",
  "start_date": "2026-10-24",
  "end_date": "2026-10-28",
  "travel_style": "comfort",
  "num_travelers": 2,
  "interests": ["美食", "历史"]
}
```

摘要响应示例：

```json
{
  "status": "completed",
  "message": "The selected plan satisfies the budget and required constraints.",
  "destination": "东京",
  "flight_cost": 4200.0,
  "hotel_cost": 2400.0,
  "activity_cost": 600.0,
  "total_cost": 7200.0,
  "budget": 10000.0,
  "within_budget": true,
  "adjustment_rounds": 0,
  "errors": []
}
```

输入日期必须严格采用 `YYYY-MM-DD` 且结束日期晚于开始日期。非法输入返回 HTTP 422；未预期的内部错误返回 HTTP 500。

## 规划结果状态

| 状态 | 含义 | HTTP 策略 |
|---|---|---|
| `COMPLETED` | 必要结果完整、约束成立且最终费用不超过预算 | 200 |
| `BUDGET_INFEASIBLE` | 正常完成搜索，但当前分阶段启发式策略未找到预算内方案；不代表数学上所有组合均无解 | 200 |
| `ACTIVITY_CONSTRAINTS_UNSATISFIED` | 某日期/时段没有满足日期、时段及天气硬约束的真实活动候选；不会编造活动 | 200，并返回约束问题与天气评价 |
| `PACE_CONSTRAINTS_UNSATISFIED` | 当前候选池不能满足已确认的旅行节奏硬约束；与预算不足和程序故障区分 | 200，并返回节奏约束问题 |
| `FAILED` | 必要 Agent 异常、超时、结果缺失或内部契约失败 | 200，并返回结构化 `errors` |

## 预算调整流程

1. Destination 完成后，Flight、Hotel、Weather Agent 基于隔离状态并行执行并确定性合并结果。
2. Activity Agent 等待天气正常结果或明确回退状态后执行；非法天气数据或内部故障会终止规划。
3. 对候选集合进行深拷贝，形成带稳定 `candidate_id`、来源及数据版本的 `CandidateSnapshot`。
4. 初始方案已在预算内时直接完成，`adjustment_round=0`。
5. 超预算时依次从固定候选快照重选活动、酒店、航班。
6. 每轮重新按实际候选价格计算费用，并写入 `adjustment_history`。
7. 三轮后仍超预算则返回 `BUDGET_INFEASIBLE`。

预算优化不会直接修改候选价格。活动保持城市、日期、时间段和已定义的兴趣约束；酒店保持入住日期并满足最低评分；航班保持路线、日期和舱位。酒店费用考虑晚数及 `ceil(人数 / 2)` 个房间，航班和活动费用均乘以人数。

## 确定性与测试

Mock Provider 使用规范化请求、数据版本和 SHA-256 派生的局部随机数生成器；不依赖 Python 内置 `hash()`、全局 `random.seed()` 或外部网络。

天气 Provider 使用独立可配置来源版本。天气查询采用结束日期排除语义，并支持 `generated`、`sunny`、`rainy`、`mixed` 和 `unavailable` 五种确定性情景。固定情景用于专项测试，不代表真实预报；同一目的地、日期、情景和版本在重叠区间及跨进程调用中保持一致。旧 `tools/weather_api.py` 仅是新 Provider 的单日兼容包装，不再维护独立随机 Mock 逻辑。

WeatherAgent 校验目的地和完整活动日期覆盖。天气正常时，ActivityAgent 先执行日期/时段硬约束，再执行集中式天气规则过滤与可配置软评分；同分按价格和稳定 `candidate_id` 排序。恶劣天气下，未知天气属性不会被当作已验证安全候选。天气明确 unavailable 或 Provider 超时会保留原因并采用不宣称天气优化的基础规划；非法数据和内部程序错误按结构化 Agent 故障终止。预算活动重选复用同一评价器，因此不会为省钱选择违反天气硬约束的候选。

测试覆盖：

- 严格输入校验和 API 422/500 边界。
- Provider 连续调用、并发调用及跨 Python 进程确定性。
- Agent 异常、超时和必要结果缺失传播。
- 初始预算满足、三轮累积降级、无有效替代、极低预算、多人数/多晚数和违规最低价候选。
- 候选快照不污染、并发规划请求隔离、金额两位小数归一化和费用对账。
- FastAPI 摘要及完整响应兼容性。
- 36 条固定中文语料、偏好多轮合并、签名草稿、防篡改和日期确认语义。
- 自然语言入口与结构化入口在相同规范化偏好下的规划结果一致性。
- Streamlit AppTest 多轮 HTTP 交互、完整规划、会话隔离、重置、预算不可满足、Agent 失败及 API 异常状态保留。
- WeatherProvider 连续五次、并发、跨进程、重叠区间、固定晴雨/混合/不可用情景、Schema 校验和返回对象隔离。
- WeatherAgent 编排依赖、晴雨同池对照、三时段雨天候选、无天气回退、无可行活动业务状态及天气—预算联合约束。
- 13 个固定天气配对场景、决策轨迹、六项产品指标，以及默认关闭的受控 HTTP/Streamlit 验收入口。

阶段 A 第三批全量验收结果为 `179 passed, 1 warning`。唯一 warning 来自 Starlette `TestClient` 对 AnyIO 旧别名的第三方弃用提示。

阶段 B 第一批加入天气契约与 Provider 后的全量验收结果为 `207 passed, 1 warning`。

阶段 B 第二批加入 WeatherAgent 和天气感知活动规划后的全量验收结果为 `221 passed, 1 warning`。

阶段 B 第三批加入天气解释 UI、控制变量实验和服务级演示后的全量验收结果为 `230 passed, 1 warning`。

阶段 C0 将可选 `pace` 接入正式规划、活动属性、天气及预算联合约束后的全量验收结果为 `240 passed, 1 warning`。未指定 `pace` 的旧请求继续使用历史三时段行为。

阶段 C1 增加指定目的地复用入口和双目的地独立编排后的全量验收结果为 `262 passed, 1 warning`。当前能力是内部 Python 编排服务，不是正式双方案 HTTP API 或比较页面。

## 双目的地独立规划（C1）

`ComparisonOrchestrator` 只执行一次 `DestinationAgent`，按原有稳定顺序选择最多两个不同目的地。每个目的地随后使用新的 `TravelPlanningPipeline` 和独立深拷贝状态执行：

```text
PreferenceAgent → DestinationAgent（一次）
                         ├─ 目的地 A：Flight / Hotel / Weather → Activity → BudgetLoop
                         └─ 目的地 B：Flight / Hotel / Weather → Activity → BudgetLoop
```

`TravelPlanningPipeline.run_for_destination(preferences, destination)` 不会重新执行 `DestinationAgent`，但复用原有航班、酒店、天气、活动和预算实现。每个方案分别使用完整用户预算作为上限，不会把预算拆成两份。

数据契约位于 `models/multi_plan.py`：

- `MultiPlanRequest`：已验证偏好、方案数量（MVP 最多 2）及确定性 Mock/执行配置。
- `DestinationPlan`：目的地稳定 ID、完整 `TravelPlanState`、业务状态、结构化故障、业务问题及无评分的事实数据。
- `MultiPlanResult`：原始候选摘要、各自独立方案、成功/不可行计数，以及“编排已结束”标记。

`orchestration_completed=true` 只表示双方案编排过程已经有确定结果，不表示所有目的地都为 `COMPLETED`。候选不足时不会编造城市；一个方案失败也不会丢弃另一个已完成方案。串行和最多并发 2 个目的地的执行具有相同规范化业务结果。

可复现内部演示：

```bash
cd python
python experiments/run_multi_plan_demo.py
```

原始输出保存在 `experiments/results/multi_plan_c1_demo.json`。该脚本使用独立 C1 验收 Fixture，价格和属性不是实时或实测数据。C1 只保留费用、预算历史、兴趣匹配、天气适配、活动数量、强度、时长、休息时段、状态和来源版本等 C2 输入，不计算综合评分或推荐排序。

## 天气控制变量实验

`experiments/weather_awareness_scenarios.json` 定义 13 个固定场景，覆盖晴天、普通/强降雨、混合天气、逐日变化、天气不可用、室内替代成本、预算重选、无更低天气合规候选、无可行活动、多人多日和极低预算。当前天气契约为日级，不虚构同一天上午/下午不同天气；时段级天气列为未来扩展。

原始逐场景结果和汇总指标位于 `experiments/results/weather_awareness_results.json`：

| 指标 | Control | Treatment |
|---|---:|---:|
| M1 天气冲突率 | 33/63，52.38% | 0/60，0% |
| M2 兴趣匹配率 | 63/69，91.30% | 48/66，72.73% |
| M3 预算合规率 | 12/13，92.31% | 11/13，84.62% |
| M4 天气可验证可行率 | 1/13，7.69% | 11/13，84.62% |
| M5 实际天气变化解释覆盖率 | 不适用 | 34/34，100% |

M6 保留每个场景的基础/天气方案初始总费用、最终总费用、活动费用和状态变化，未仅报告平均值。天气优化消除了可评价活动的硬冲突，但在当前固定候选池中也降低了兴趣匹配率，并可能增加费用或导致无解；这些是实验结果而非需要隐藏的“坏数据”。天气未知或缺少活动不会被计为零冲突。

`experiments/results/weather_b3_service_demo.json` 保存真实 Uvicorn 与 Streamlit 进程的服务级演示摘要。验收专用 `/api/demo/weather/plan` 默认返回 404；仅当服务端显式设置 `ENABLE_WEATHER_DEMO_ENDPOINTS=1` 时启用。Streamlit 固定 Mock 验收入口同样要求 `ENABLE_WEATHER_DEMO_CONTROLS=1`，并醒目标识其不是实时天气。普通用户入口不显示这些测试控件。

## 固定场景产品效果实验

`experiments/preference_clarification_scenarios.json` 保存 8 个固定中文场景，`experiments/run_preference_experiment.py` 通过实际 FastAPI 路由分别执行自然语言澄清和结构化入口。固定 `reference_date=2026-09-24`、Parser 配置及 Mock 数据版本后，本次实测为：

| 指标 | 实测结果 |
|---|---|
| M1 必要字段收集完成率 | 8/8，100% |
| M2 已标注支持字段提取准确率 | 51/51，100%；漏提取 0、错误 0、误报 0；预期歧义识别 7/7 |
| M3 澄清轮次 | 总计 5，平均 0.625 轮/场景 |
| M4 原子交互步骤 | 自然语言 34（平均 4.25），结构化 61（平均 7.625） |
| M5 规划结果一致性 | 8/8，100% |
| M6 异常恢复成功率 | 7/7，100% |

M4 的固定口径是：自然语言首次输入和解析各 1 步，每轮补充的输入与提交各 1 步，确认 1 步；结构化入口按 6 个必填字段编辑、可选兴趣编辑和提交计数。该实验是有限规则、固定语料下的自动化交互模拟，不代表真实 LLM 泛化能力、真人满意度或实际操作耗时。

## 目录

| 目录 | 职责 |
|---|---|
| `agents/` | 业务评分、推荐和预算评估 |
| `tools/` | Mock Provider、稳定 ID 和确定性候选生成 |
| `orchestrator/` | 单方案 Pipeline、并行执行、候选重选预算循环及双目的地独立编排 |
| `models/` | Pydantic 数据契约和统一费用计算 |
| `api/` | FastAPI 接口 |
| `preferences/` | 偏好草稿、规则 Parser、日期协调和多轮澄清服务 |
| `ui/` | Streamlit 结构化规划，以及通过 FastAPI 完成的自然语言澄清、确认和规划 |
| `experiments/` | 固定产品场景、可重复统计脚本及原始结果 |
| `tests/` | 单元、API 和集成回归测试 |

## 当前阶段边界

尚未实现：

- 真实 LLM、实时航班、酒店、活动或天气服务。
- 用户账户、身份认证、数据库会话及跨设备续聊。
- 真实机票或酒店预订、支付和订单管理。
- 真人可用性测试（已提供 3～5 人执行方案与记录模板，尚无真实参与者数据）与生产部署评估。

当前偏好解析器是声明范围有限的确定性 Mock Parser，不应表述为真实 LLM 能力。已确认的 `pace` 会进入 `UserPreferences` 并通过集中策略影响活动密度、强度和明确休息时段；未指定时保持历史行为。

## 阶段 C 双方案比较

阶段 C 已提供正式双方案产品链路：`ComparisonOrchestrator` 只生成一次
`MultiPlanResult`，`PlanComparator` 和确定性 `ExplanationService` 在该只读结果上完成
四维评价与解释。主评分策略为 `plan-scoring-v1`（预算 30%、兴趣 30%、节奏
25%、天气 15%）；API 还在同一规划结果上执行声明过的替代权重
`plan-scoring-sensitivity-v1`，仅用于披露结论是否对权重敏感。

正式接口：

- `POST /api/plans/compare`：接收 `MultiPlanRequest`，供结构化偏好入口使用。
- `POST /api/preferences/compare`：接收完整、已签名且用户显式确认的
  `PreferencesDraft`，继续验证 HMAC、日期基准和 Parser 配置后再比较。

结构化请求示例：

```json
{
  "preferences": {
    "budget": 10000,
    "departure_city": "上海",
    "start_date": "2026-10-01",
    "end_date": "2026-10-06",
    "travel_style": "comfort",
    "pace": "balanced",
    "num_travelers": 2,
    "interests": ["美食", "摄影"]
  },
  "plan_count": 2,
  "config": {"execution_mode": "concurrent", "max_concurrency": 2}
}
```

响应中的 `request_status` 只描述本次编排请求，不能替代每个方案自己的业务
`status`。`multi_plan` 保存两套独立完整方案，`comparison` 保存服务端计算的四维
评价、有效权重、证据、解释和 `recommended / tie / only_feasible /
no_recommendation` 决策；`sensitivity` 保存替代权重检查。只有一座有效目的地时
会如实返回一套方案，不会制造第二套。客户端不能提交综合分、推荐 ID 或候选数据
覆盖服务端结论。

Streamlit 的自然语言和结构化入口均可选择“单方案规划”或“双方案比较”。比较页
显示偏好摘要、并列方案卡片、C2 原始解释和完整行程；前端不重新计算评分。用户在
页面选择方案仅代表产品内查看选择，不是预订。修改偏好后旧比较结果立即失效；网络
错误不会清空已确认偏好。

专项测试与固定 Mock 产品实验：

```bash
# 在 python/ 目录执行
python -m pytest -q tests/test_comparison_api.py tests/test_comparison_ui.py
python -m pytest -q tests/test_comparison_product_experiment.py
python experiments/run_comparison_product_experiment.py
```

实验原始结果位于
`experiments/results/comparison_product_c3_results.json`。Control 是同一条件下首个
目的地的完整单方案，Treatment 是双方案生成、C2 评价、解释与敏感性披露。该实验
只测固定 Mock 场景的契约覆盖、状态识别、证据追溯和本机执行时间，不代表真实用户
偏好、满意度、生产性能或实时旅行市场。

当前仍未实现真实 LLM、实时航班/酒店/活动/天气服务、用户身份认证、持久化会话和
真实预订。阶段 D 的视觉与交互升级继续复用现有 API 数据契约，前端不另建评分逻辑。

## 正式 Classic UI 与备选五步工作台

正式默认入口 `ui/streamlit_classic_app.py` 延续简洁左右分栏布局，左侧统一处理结构化
偏好、额外备注解析和主动澄清，右侧使用航班、酒店、行程和预算四个标签展示单方案或
当前选中的对比方案。默认端口为 `8785`，页面复用同一 FastAPI 结果，不在前端重新计算
天气、预算、pace 或综合评分。

以下 D2/D3 五步工作台仍作为备选界面完整保留：

Streamlit 已按 D1 原型实现为五步工作台：需求对话、偏好确认、规划执行、方案比较和
行程详情。`ui/streamlit_app.py` 只负责编排页面；`ui/components/` 提供偏好、导航、
状态、比较和完整方案只读组件，`ui/preference_api_client.py` 负责 HTTP，
`ui/workbench_state.py` 负责每个浏览器会话的状态契约，`ui/theme.py` 提供本地视觉样式。

关键交互约束：

- 修改偏好或结构化字段会立即使旧规划、评分及目的地选择失效。
- 规划请求先进入真实第 3 步再调用 API；后端没有细粒度事件，因此只展示整体加载，
  不显示计时器或伪造 Agent 百分比。
- API 超时或网络失败保留有效草稿和用户输入，可返回确认页或表单安全重试。
- 比较页直接显示 C2 的评分、有效权重、证据和推荐类型，不在浏览器重新计算。
- 从比较页切换目的地详情只读取既有 `MultiPlanResult`，不会再次执行 Pipeline。
- Mock、非实时、非预订标识在所有核心页面持续可见；状态同时使用文字而非仅靠颜色。

D2 专项测试：

```bash
python -m pytest -q tests/test_workbench_state.py tests/test_preference_ui.py \
  tests/test_comparison_ui.py tests/test_weather_demo.py
```

阶段 D2 完整回归实测为 `317 passed, 1 warning`；该 warning 来自 Starlette TestClient
对 AnyIO 旧别名的上游弃用提示。真实浏览器验收截图位于 `../docs/assets/stage-d2/`，
其中桌面与 390×844 窄屏均完成从自然语言输入到查看每日行程的实际服务链路。

## 阶段 D3 最终验收与证据复现

D3 不新增业务 Agent，也未调整天气、pace、预算或评分算法。正式页面仅做两项定向可读性
修复：偏好明细默认折叠以减少重复信息；长业务状态使用短文本展示并同时保留完整状态代码。

```bash
# 在 python/ 目录执行，重算 A/B/C 固定 Mock 实验证据汇总
python experiments/summarize_d3_evidence.py

# 真实参与者数据录入后再统计；空模板会明确返回 pending_real_participants
python experiments/analyze_usability_results.py \
  ../docs/templates/D3-用户可用性测试记录模板.csv

# D3 证据脚本自动化测试
python -m pytest -q tests/test_d3_evidence.py
```

汇总结果写入 `experiments/results/d3_product_evidence_summary.json`，并附带原始实验文件的
SHA-256，避免只抄报告数字。真实 HTTP、受控 Agent 故障和天气状态探针分别保存在
`d3_service_acceptance.json`、`d3_agent_failure_http.json` 与
`d3_weather_status_http.json`；真实 Edge 桌面及 390×844 验收结果在
`d3_browser_acceptance.json`，截图位于 `../docs/assets/stage-d3/`。

最终产品文件入口：

- `../docs/22-产品UIUX最终检查报告.md`
- `../docs/23-真实用户可用性测试方案.md`
- `../docs/24-最终产品实验与证据汇总.md`
- `../docs/25-Multi-Agent-Travel-Copilot最终PRD.md`
- `../docs/26-最终系统架构API与复现指南.md`
- `../docs/27-AI产品经理求职作品集.md`
- `../docs/28-AI产品经理简历项目经历与演示脚本.md`
- `../docs/29-阶段D3最终产品验收报告.md`

上述固定语料、Mock 对照和浏览器功能验收不等于真实 LLM 泛化、真实市场准确率或用户
满意度。真人可用性研究只有在获得参与者同意并录入真实记录后才可宣称完成。
