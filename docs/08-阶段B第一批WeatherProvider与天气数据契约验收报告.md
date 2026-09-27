# 阶段 B 第一批：WeatherProvider 与天气数据契约验收报告

## 1. 验收结论

阶段 B 第一批已完成并通过当前范围内的必要验收，可以作为 B2 的天气数据基础。

- 开发前全量基线：`179 passed, 1 warning in 14.59s`。
- B1 最终天气专项：`28 passed in 1.47s`。
- 原 Flight、Hotel、Activity Provider 与输入校验专项：`37 passed in 1.43s`。
- 开发后最终全量回归：`207 passed, 1 warning in 16.34s`。
- 已建立严格的天气 Pydantic 契约、可替换 `WeatherProvider` Protocol 和确定性 `MockWeatherProvider`。
- 原 `weather_api.py` 已成为新 Provider 的兼容包装，不再维护第二套随机天气 Mock。
- 未注册 WeatherAgent，未修改 Pipeline/ParallelExecutor 顺序，未实现天气感知活动评分。

唯一 warning 仍来自 Starlette `TestClient` 对 AnyIO 旧别名的第三方弃用提示，与天气实现无关。

## 2. 工作区与安全版本边界

执行开始时：

- 分支：`main`。
- HEAD：`8132dfcded4539a4ca28b1c1fc316b93001c0b08`。
- 已跟踪累计差异：19 个文件；另有阶段 0、A1、A2、A3 的重要未跟踪文件。
- 开发前全量测试与阶段 A3 报告一致，无新增失败。

已有阶段 0、A1、A2 归档均可读取并校验哈希。B1 修改前新增 A3 完整工作区快照：

```text
.stage-backups/stage-a3-worktree-main-8132dfc-20260925.tar.gz
size: 194782 bytes
entries: 169
SHA-256: BE6726B5F1F0900DABBE3A9AB8601E37DA61F068C3CBD5A918828E77CE0F0C18
```

关键文件已从归档读取并与工作区逐字节比较，全部一致；归档不包含 `.git`、旧归档、测试缓存、字节码或本地 `.env`。Windows tar 首次显示中文条目时出现终端编码乱码，随后使用 Python `tarfile` 按内容完成校验，归档本身有效。

本次没有执行 `restore`、`reset`、`clean`、`checkout`、`stage`、`commit`、`merge` 或 `push`，也没有读取或输出任何环境变量密钥。

建议的版本整理方式：由所有者先审阅并按归档边界整理阶段 0 与阶段 A 的可恢复提交或标签，再从阶段 A 验收点创建如 `feature/stage-b-weather` 的独立分支。当前 `.stage-backups/` 仅用于本地恢复，应保持未跟踪且不得提交。

## 3. Provider 架构

```text
WeatherSearchRequest（严格 Pydantic）
  │
  ▼
WeatherProvider Protocol
  ├─ MockWeatherProvider（当前实现）
  │    ├─ SHA-256 稳定摘要
  │    ├─ 每个日期独立局部 RNG
  │    ├─ generated / sunny / rainy / mixed / unavailable
  │    └─ WeatherResult（严格 Pydantic）
  └─ RealWeatherProvider（未来实现，同一输入输出契约）

旧 get_weather(city, date)
  └─ 单日 WeatherSearchRequest
       └─ 同一个 MockWeatherProvider 生成函数
```

Provider 只负责查询/生成天气数据，不做活动过滤、评分、时间段选择或 Pipeline 降级决策。业务策略保留给 B2 的 WeatherAgent、ActivityAgent 和编排器。

## 4. 数据契约

### 4.1 `WeatherSearchRequest`

| 字段 | 类型 | 约束 |
|---|---|---|
| `destination` | `str` | 非空，去除首尾空格，最长 100 |
| `start_date` | ISO 日期字符串 | 活动首日，严格 `YYYY-MM-DD` |
| `end_date` | ISO 日期字符串 | 排除式结束日期，必须晚于开始日期 |
| `scenario` | `WeatherScenario` | 默认 `generated`；另有 `sunny/rainy/mixed/unavailable` |
| `config` | `WeatherQueryConfig` | 当前冻结为 `celsius`、`zh-CN`，禁止未知字段 |

数据版本与其他 Mock Provider 一致，由 Provider 构造参数控制，默认使用 `settings.MOCK_DATA_VERSION`，不接受客户端伪造响应来源版本。

### 4.2 `DailyWeather`

| 字段 | 类型 | 说明 |
|---|---|---|
| `date` | ISO 日期字符串 | 对应一个活动日 |
| `weather_condition` | 枚举或 `None` | sunny/cloudy/rain/heavy_rain/thunderstorm/snow |
| `rain_probability` | 严格整数或 `None` | 0–100，百分比 |
| `temperature` | float 或 `None` | 摄氏度，-80 至 60，不允许 NaN/Infinity |
| `severe_weather` | bool 或 `None` | 恶劣天气标记 |
| `availability_status` | available/unavailable | 每日数据可用状态 |

可用记录必须具有全部天气字段；不可用记录必须让这些字段全部为 `None`。因此未知天气不能被静默表示为晴天、0% 降雨或 0℃。

### 4.3 `WeatherResult`

包含：

- `destination`、`start_date`、`end_date` 和 `scenario`。
- 按日期顺序排列的 `daily_weather`。
- `source`、`source_version`、`is_mock`。
- 结果级 `availability_status`：available/partial/unavailable。
- 明确的 `availability_note`。

模型会计算 `[start_date, end_date)` 的期望日期，并要求每日列表不缺失、不重复、不乱序。结果级状态必须与每日状态一致；`source=mock` 必须同时满足 `is_mock=true`。

## 5. 确定性生成与版本管理

`MockWeatherProvider` 复用 `tools/deterministic.py` 的 SHA-256 稳定摘要和局部 `random.Random`，不使用 Python `hash()`、全局 `random.seed()` 或共享随机状态。

每日随机身份由以下内容组成：

```text
namespace = weather-day
destination
date
scenario
query config
dataset_version
```

查询区间本身故意不参与某一天的种子，因此两个区间的重叠日期会得到完全相同的天气。每次查询重新创建 Pydantic 对象，修改返回结果不会污染后续查询。

版本变化进入稳定摘要并写入 `source_version`。未来 RealProvider 需返回 `source=real`、`is_mock=false` 和其真实来源版本，不能复用 Mock 标识冒充实时预报。

## 6. 固定 Fixture 与实测数据

固定 Fixture 位于 `python/tests/fixtures/weather_scenarios.json`，目的地为东京，日期区间为 2026-10-01 至 2026-10-05（结束日期排除）。使用 `weather-demo-v1` 实际执行：

| 情景 | 日期 | 条件 | 降雨概率 | 状态 |
|---|---|---|---|---|
| sunny | 10-01..10-04 | sunny / sunny / sunny / sunny | 6 / 7 / 6 / 8 | available |
| rainy | 10-01..10-04 | rain / heavy_rain / rain / heavy_rain | 93 / 88 / 88 / 95 | available |
| mixed | 10-01..10-04 | rain / thunderstorm / sunny / cloudy | 80 / 95 / 5 / 30 | available |
| unavailable | 10-01..10-04 | 全部 `None` | 全部 `None` | unavailable |

四种结果均为 `source=mock`、`source_version=weather-demo-v1`、`is_mock=true`。这只是固定模拟情景，不是东京的真实天气预报。

## 7. Acceptance Criteria 测试矩阵

| 编号 | 测试证据 | 结果 |
|---|---|---|
| T1 连续五次一致 | `test_t1_same_weather_request_is_identical_across_five_calls` | 通过 |
| T2 跨进程一致 | `test_t2_weather_is_stable_across_processes_and_hash_seeds` | 通过 |
| T3 并发一致 | `test_t3_concurrent_calls_do_not_change_weather` | 通过 |
| T4 重叠区间一致 | `test_t4_overlapping_ranges_share_identical_daily_weather` | 通过 |
| T5 城市/日期/情景/版本变化 | `test_t5_city_date_scenario_and_version_change_applicable_results` | 通过 |
| T6 固定晴天 | `test_t6_fixed_sunny_fixture_is_always_sunny` | 通过 |
| T7 固定雨天 | `test_t7_fixed_rainy_fixture_is_always_rainy` | 通过 |
| T8 固定混合天气 | `test_t8_fixed_mixed_fixture_has_multiple_conditions` | 通过 |
| T9 明确不可用 | `test_t9_unavailable_is_explicit_and_never_fabricates_sun` | 通过 |
| T10 非法查询/概率/覆盖/来源状态 | 四组 `test_t10_*` | 通过 |
| T11 Pydantic 契约 | `test_t11_t12_provider_result_matches_schema_and_source_contract` | 通过 |
| T12 来源标识 | 同上 | 通过 |
| T13 返回对象隔离 | `test_t13_mutating_returned_result_does_not_pollute_next_query` | 通过 |
| T14 结束日期排除 | `test_t14_weather_uses_same_end_exclusive_dates_as_activity_agent` | 通过 |
| T15 原 Provider 不受影响 | `test_mock_providers.py` 与 `test_validation.py` | 37 passed |
| T16 阶段 0/A 回归 | 全量 pytest | 207 passed |

额外验证包括 Activity 天气字段默认 unknown/None、三类 Provider 错误可区分以及旧单日天气入口使用新确定性实现。

## 8. 活动契约与候选池审计

`Activity` 新增四个向后兼容字段：

- `environment`：indoor/outdoor/mixed/unknown，默认 unknown。
- `weather_sensitivity`：none/low/medium/high/unknown，默认 unknown。
- `weather_compatible`：`bool | None`，默认 `None`，由未来具体天气上下文计算。
- `recommendation_reason`：默认空字符串，用于未来记录选择依据。

默认值不会改变当前 ActivityAgent 的排序或预算逻辑。特别是 unknown 不等于适合雨天，`weather_compatible=None` 不得按 `True` 使用。

当前候选数量：

| 候选池 | morning | afternoon | evening |
|---|---:|---:|---:|
| 东京 | 4 | 4 | 2 |
| 曼谷 | 3 | 2 | 3 |
| default | 2 | 2 | 2 |

数量上三个时间段均有候选；但现有模板尚未填充室内外和天气敏感度，所以契约层面可证明的雨天兼容候选数量为 0，当前数据不足以验收天气驱动规划。

B2 最小数据扩充方案：

1. 为所有现有模板显式标注 `environment` 和 `weather_sensitivity`。
2. 每个城市/默认池的 morning、afternoon、evening 至少保留一个明确 indoor 且低天气敏感的候选。
3. 东京可优先标注既有和服体验、teamLab、居酒屋；仍需业务复核地点属性。
4. 曼谷至少补充雨天上午的室内文化场馆和晚间室内演出。
5. default 池至少补充晚间室内文化演出；博物馆和特色午餐需显式标注。
6. 若兴趣被定义为硬约束，还需保证各必需兴趣类别在三个时段有可用替代，不能为了天气静默放宽兴趣条件。

## 9. 天气异常与后续降级规则

Provider 层已区分：

| 情况 | B1 表达 | B2 建议处理 |
|---|---|---|
| 正常数据 | available `WeatherResult` | WeatherAgent 正常写入状态，Activity 使用天气 |
| 正常不可用 | unavailable，每日字段为 `None` | 允许基础活动规划，但明确标记未做天气优化 |
| 超时 | `WeatherProviderTimeoutError` 类型 | WeatherAgent 记录结构化超时；按配置决定显式回退 |
| 非法数据 | Pydantic 拒绝或 `WeatherProviderDataError` | 记录数据契约失败，不伪造晴天 |
| 内部代码故障 | 原异常继续传播 | 结构化记录程序故障，不降格成普通不可用 |

B1 不改变 Pipeline 故障传播。上述回退只有在 B2 明确注册 WeatherAgent 和状态字段后才能生效。

## 10. B2 模块依赖与数据流

建议的数据流：

```text
DestinationAgent
  ├─ FlightAgent ───────┐
  ├─ HotelAgent ────────┼─ 并发
  └─ WeatherAgent ──────┘
           │ WeatherResult 或显式 fallback decision
           ▼
      ActivityAgent
           │ 天气过滤/评分、兼容标记、选择原因
           ▼
        BudgetAgent / BudgetLoop
```

B2 最小实现依赖：

1. 新增 WeatherAgent 输入/输出契约，并向 `TravelPlanState` 增加天气结果和回退标记。
2. ParallelExecutor 只并发 Flight、Hotel、Weather；Activity 必须等天气结果或回退决策。
3. 将活动模板的天气元数据填实，不按名称临时猜测。
4. ActivityAgent 明确硬过滤与降权边界，并保留 `weather_compatible` 和 `recommendation_reason`。
5. 天气不可用可继续基础规划，但最终 API/UI 必须显示“未做天气优化”。
6. 超时、非法数据和代码异常分别形成结构化故障；不得统一转换成 sunny。
7. 预算优化重选活动时必须保留天气硬约束，避免降价后选回不兼容活动。

`pace` 的业务消费仍不属于 B1；若 B2 同时启用，必须先定义独立权重和测试，不能将其混入天气规则后无法追溯。

## 11. 修改与新增文件

新增：

- `python/tools/weather_provider.py`
- `python/tests/fixtures/weather_scenarios.json`
- `python/tests/test_weather_provider.py`
- `docs/08-阶段B第一批WeatherProvider与天气数据契约验收报告.md`

修改：

- `python/models/schemas.py`：天气请求、每日结果、整体结果及 Activity 天气元数据契约。
- `python/tools/providers.py`：WeatherProvider Protocol 和可区分的 Provider 错误类型。
- `python/tools/weather_api.py`：移除全局随机 Mock，改为统一 Provider 的兼容包装。
- `python/README.md`：同步 B1 能力、测试命令和明确边界。

没有修改预算优化、Preference Parser、阶段 A API/Streamlit 交互、ActivityAgent 推荐算法、Pipeline 或 ParallelExecutor。

## 12. Git 差异与保护结果

最终仓库仍为 dirty `main`，相对 HEAD 的累计已跟踪差异为：

```text
19 files changed, 1861 insertions(+), 554 deletions(-)
```

该数据包含阶段 0、阶段 A、用户原有修改及 B1，且不包含未跟踪新增文件，不能作为 B1 单独代码量。B1 独立边界以第 11 节和 A3 快照为准。

最终 `git diff --check` 返回 0；输出仅包含既存的 LF 将来可能转为 CRLF 提示，没有 whitespace error。工作区共有 43 个未跟踪文件，其中包含各阶段文档、代码、测试和本地归档；既有成果、阶段归档和未跟踪文件均未清理、暂存或提交。

## 13. 遗留风险

1. WeatherAgent、TravelPlanState 天气字段和 Pipeline 依赖尚未实现，这是明确的 B2 范围。
2. 活动元数据目前默认 unknown，尚不能进行有依据的天气适配选择。
3. 当前天气是远期确定性 Mock 情景，不具有预测精度或实时性。
4. `WeatherProviderTimeoutError` 的实际超时边界需由 B2 编排器或 RealProvider 实现。
5. 目前只有 available/unavailable Mock 情景；Schema 已支持 partial，B2 可增加固定部分缺失 Fixture。
6. Activity 新字段是向后兼容可选字段；未来评分前必须避免把缺失值当作零风险。

阶段 B 第一批到此停止，不自动开发 B2、B3 或阶段 C。
