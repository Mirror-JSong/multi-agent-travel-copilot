# 阶段 A 第二批：Preference Clarification API 与 Streamlit 多轮交互验收报告

> 验收日期：2026-09-24<br>
> 分支与 HEAD：`main` / `8132dfcded4539a4ca28b1c1fc316b93001c0b08`<br>
> 实施范围：Preference Clarification API、无服务端会话状态、Streamlit 多轮交互及回归测试<br>
> 明确不包含：确认后的正式旅游规划调用、真实 LLM、WeatherAgent、多方案比较

## 1. 结论

阶段 A 第二批必要验收项通过：

- 新增 `POST /api/preferences/parse` 和 `POST /api/preferences/clarify`。
- 两个接口使用明确的 Pydantic 请求/响应模型，复用第一批 `ClarificationManager`，没有复制解析或合并算法。
- API 无会话数据库；每轮由客户端携带上一轮完整草稿。
- 草稿通过服务端 HMAC 回执绑定其内容、日期基准和解析配置，客户端不能伪造字段来源或完成状态。
- Streamlit 新增自然语言澄清模式，同时保留原结构化表单和完整 Pipeline 展示。
- 每个 Streamlit 浏览器会话使用独立 `session_state` 保存草稿、回执、历史、问题、日期基准和确认状态。
- 日期确认卡片展示出发日、返程日、活动日期范围和住宿晚数。
- `pace` 被保留并展示，同时明确提示尚未进入 `UserPreferences` 或影响 `ActivityAgent`。
- API 超时或连接异常不会清空已有有效草稿及历史。

最终实际测试：

```text
阶段 0 回归：81 passed, 1 warning in 3.28s
阶段 A 第一批核心：61 passed in 0.43s
阶段 A 第二批 API：14 passed, 1 warning in 0.79s
阶段 A 第二批 Streamlit/AppTest：7 passed in 7.76s
全量：163 passed, 1 warning in 10.45s
```

唯一警告仍为 Starlette TestClient 使用 AnyIO 旧别名的依赖弃用警告。

## 2. 工作区保护与版本边界

### 2.1 开发前检查

- 当前仍在 `main`，HEAD 为 `8132dfc`。
- 阅读了阶段 0 最终验收报告、阶段 A 第一批报告和 PRD。
- 开发前全量基线实际为 `141 passed, 1 warning in 3.32s`。
- 工作区继续包含阶段 0、阶段 A 第一批、PRD、审计文档及原有用户修改。
- 没有执行 `restore`、`reset`、`clean`、`checkout`、`stage`、`commit`、`merge` 或 `push`。

阶段 0 归档重新验证：

```text
.stage-backups/stage0-worktree-main-8132dfc-20260924.tar.gz
entries: 141
sha256: 6665F37DC3E8835DBF2B5C8828A726A2203FEEE8260B4CF3A89132258E3343DB
tar list exit: 0
```

### 2.2 阶段 A 第一批保护

在第二批写代码前，新建当前完整工作树归档：

```text
.stage-backups/stage-a1-worktree-main-8132dfc-20260924.tar.gz
size: 159452 bytes
entries: 153
sha256: 9605399E758D84974181F67C67209515F1513ECF6AB5FC4C87D8EDC7F4C6EBEB
tar list exit: 0
```

阶段 A 第一批完整文件及开发前 SHA-256：

| 文件 | SHA-256 |
|---|---|
| `python/preferences/__init__.py` | `BB5AAABBAD3B69818D4BE7F65485F1514FEEB305D49F136EC9805C5087172154` |
| `python/preferences/contracts.py` | `1BC02BF16345F9E1FC7122EC4FAF5ACAD96C8ED4A81CEDA41FBB7BD8E417FF19` |
| `python/preferences/date_semantics.py` | `F7C5169D80CE5A3402B7DE40E96DCCAEED78C765FB368FA2671A4B154D42ADD8` |
| `python/preferences/parser.py` | `1EE1287375DF0A15F814E82097100B7970A97DBC7B0B6AB772481DE21B2E64AC` |
| `python/preferences/clarification.py` | `FE2901265EBCFBCD6C6AE38C2CAC11B94ED1616D2C28B4D82DFFCA81A7B09BAC` |
| `python/tests/fixtures/preference_utterances.json` | `4E4141B3DFDD26B91274FE65E35BDF923CB83C68E0E253DC695036CE4A6C8DF0` |
| `python/tests/test_preference_clarification.py` | `443CE7174A17BA3639BE455932E57B94217B5A772CEB2F6F84F57EFC87D77C2F` |
| `python/examples/preference_clarification_demo.py` | `9FDA97E396E7AD68E6456602BDF0AEE23EAF928A5CFDB3C11FF59571A7B5828E` |
| `docs/08-阶段A第一批偏好澄清设计与验收报告.md` | `58A1B8AD5D453799E747ADC8E41D7DF4AA5C8D3264FD8D03CD0EF91A37FE64FE` |
| `python/orchestrator/pipeline.py` | `66F9DF91740DE608CE04BA435BF95CBB0B85EE15F6E5282C86C8F5D5AB51E08C` |

第二批只对 `contracts.py` 增加拒绝未知字段配置，对 `clarification.py` 补充已有日期冲突的明确追问；Parser、日期推导算法、Pipeline 闸门和 36 条语料均未重写。

### 2.3 原有用户修改与版本整理建议

阶段 0 审计记录的原始未提交用户修改为：

- `python/agents/flight_agent.py`：解释性注释。
- `python/models/schemas.py`：解释性注释。
- `python/ui/streamlit_app.py`：修复引号导致的语法错误。

前两处本批没有修改。`streamlit_app.py` 是本批授权范围，已在保留结构化表单、预算结果和故障展示的前提下增加自然语言模式；开发前版本完整保存在阶段 0 和阶段 A1 两份归档中。

待用户授权后，建议按以下逻辑整理，而不是直接把当前累计 diff 当成单一提交：

1. 以审计记录重建并确认原始用户修复层。
2. 以阶段 0 归档和报告建立阶段 0 基线提交。
3. 以阶段 A1 归档和哈希表建立第一批提交。
4. 从 A1 提交创建 `feature/stage-a2-preference-ui`，提交本报告所列第二批文件。
5. `streamlit_app.py`、`pipeline.py` 等跨阶段文件需要按归档逐层审阅，不能使用整文件覆盖来模拟拆分历史。

上述方案尚未执行任何 Git 写操作。

## 3. FastAPI 数据契约

### 3.1 解析配置

`PreferenceParserConfig` 当前冻结为：

```json
{
  "provider": "mock",
  "locale": "zh-CN",
  "version": "mock-preference-parser-v1"
}
```

未授权的 Provider、语言、版本和额外字段返回 HTTP 422。该配置明确当前不是实际 LLM。

### 3.2 `POST /api/preferences/parse`

请求：

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

`text` 会去除首尾空白，长度为 1～4000；`reference_date` 必须为有效日期。普通字段缺失、歧义或用户在自然语言中给出非法业务值，不是程序异常，返回 HTTP 200 的未完成澄清结果。

### 3.3 `POST /api/preferences/clarify`

请求至少包含：

```json
{
  "draft": {"...": "上一轮完整 PreferencesDraft"},
  "draft_receipt": "上一轮 64 位签名回执",
  "answer": "我们一共3人，2026年10月1日出发，舒适游。",
  "reference_date": "2026-09-24",
  "config": {
    "provider": "mock",
    "locale": "zh-CN",
    "version": "mock-preference-parser-v1"
  }
}
```

`draft_receipt` 使用服务端 HMAC-SHA256，绑定草稿完整 JSON、日期基准和 Parser 配置。修改候选值、字段来源、日期基准或配置都会返回 HTTP 422。接口不接受客户端传入的 `is_complete` 或 `can_confirm`，完成状态由 Manager 重新计算。

未配置 `PREFERENCE_DRAFT_SIGNING_KEY` 时服务进程使用随机临时密钥；不保存任何用户会话，但服务重启会使旧回执失效。多进程部署或要求重启续聊时必须配置相同的私有签名密钥。

### 3.4 统一响应

`PreferenceApiResponse` 包含：

- `draft`：当前完整 `PreferencesDraft`。
- `clarification_result`：完整领域结果。
- `missing_fields`、`questions`、`is_complete`、`can_confirm`。
- `date_summary`：出发、返程、活动首末日和住宿晚数。
- `pipeline_preferences_preview`：仅完整时返回，表示确认后能进入现有 Pipeline 的字段。
- `draft_only_fields`：当前主要为 `pace`。
- `draft_receipt` 和 `parser_config`。
- `is_mock=true` 及 Mock Parser 明示文案。

## 4. 会话与 Streamlit 交互

交互链路：

```text
浏览器 session_state
  → PreferenceApiClient
  → /api/preferences/parse 或 /api/preferences/clarify
  → ClarificationManager
  → 带签名回执的 PreferenceApiResponse
  → 更新当前浏览器 session_state
```

Streamlit 保存：

- `preference_response`：草稿、问题、回执和确认预览。
- `preference_history`：用户和澄清助手的历史。
- `preference_reference_date`：首轮确定后锁定。
- `preference_confirmed`：只由当前页面在完整结果上设置。

只有 HTTP 成功后才原子更新这些字段。超时、连接失败或非 2xx 响应只显示错误，不覆盖现有草稿和历史。“重新开始”仅清除当前浏览器会话的临时字段。

页面模式：

1. `自然语言澄清`：真实调用 FastAPI，展示字段、来源、错误、追问、日期确认卡和 `pace` 边界。
2. `结构化表单`：保留阶段 0 表单、Pipeline、三种状态、费用明细和预算调整历史。

偏好确认不会在本批调用正式旅游规划，页面明确提示该连接属于阶段 A 第三批。

## 5. T1–T15 验收矩阵

| 条目 | 实际测试证据 | 结果 |
|---|---|---|
| T1 首次解析 | TestClient 和 AppTest 调用 `/api/preferences/parse` | 通过 |
| T2 不完整追问 | 上海示例返回 4 个缺失字段及定向问题 | 通过 |
| T3 多轮澄清 | 回传上一轮草稿和回执，revision 0→1 | 通过 |
| T4 字段保护 | 第二轮保留城市、预算、兴趣和 pace | 通过 |
| T5 修改预算 | 完整结果中 15000→20000，其他日期和城市不变 | 通过 |
| T6 会话隔离 | 两个 API 会话和两个独立 AppTest 实例分别保持上海/北京 | 通过 |
| T7 重新开始 | 清空草稿、历史、确认状态并重建日期基准 | 通过 |
| T8 非法输入 | 空文本/坏基准/坏配置 HTTP 422；负预算、0 人和非法日期进入正常错误澄清 | 通过 |
| T9 日期歧义 | 3 天与 5 天日期区间冲突时显示 `duration_days` 问题 | 通过 |
| T10 完成确认 | 不完整时按钮禁用；完整时出现确认卡并可确认 | 通过 |
| T11 正常/异常 UI | HTTP 正常更新；连接异常和超时明确提示且保留状态 | 通过 |
| T12 结构化表单 | AppTest 切换原模式并完整运行 Pipeline，显示 COMPLETED | 通过 |
| T13 全量回归 | 36 条语料继续通过；全量 163 项通过 | 通过 |
| T14 真实 Uvicorn | 独立 Uvicorn 进程上完成两个接口、多轮及 422 请求 | 通过 |
| T15 两组 AppTest | 缺失字段流程和日期冲突流程均完整到确认状态 | 通过 |

## 6. 两组真实多轮演示

实际启动 Uvicorn：

```text
http://127.0.0.1:8775
GET /api/health -> 200, status=ok
```

通过 `httpx` 对该真实进程运行 `python/examples/preference_http_demo.py`。

### 演示一：典型缺失字段

第一轮缺失：

```text
start_date, end_date, num_travelers, travel_style
```

第二轮补充后：

```text
is_complete=true
departure_city=上海
budget=15000
num_travelers=3
start_date=2026-10-01
end_date=2026-10-06 (inferred)
activity range=2026-10-01..2026-10-05
hotel_nights=5
pace=relaxed（仅草稿）
```

### 演示二：日期与时长冲突

输入日期为 2026-10-01 至 2026-10-06，但同时声明 3 天：

```text
is_complete=false
missing_fields=[]
questions=[duration_days]
```

回答“旅行天数改成五天”后：

```text
is_complete=true
duration_days=5
return_date=2026-10-06
hotel_nights=5
```

同一真实 HTTP 演示中，`reference_date=bad-date` 返回 HTTP 422。Uvicorn 演示进程在完成后停止。

## 7. Streamlit 实际验证

真实启动结果：

```text
http://127.0.0.1:8776/_stcore/health -> 200, ok
http://127.0.0.1:8776/ -> 200, 7260 bytes
```

Streamlit 进程在验证后停止。

AppTest 使用实际 Uvicorn 线程和真实 HTTP 客户端完成：

- 上海/国庆示例：首次追问、第二轮完成、日期卡、pace 提示、确认和重新开始。
- 日期冲突示例：显示旅行天数问题，第二轮修正到 5 天，再修改预算为 20000。
- 两个独立 AppTest 实例没有共享草稿和历史。
- 断开 API 后页面显示连接错误，原草稿和历史保持不变。
- 原结构化表单仍可运行完整 Pipeline 并显示完成状态。

## 8. 本批文件清单

新增：

- `python/api/preference_models.py`
- `python/ui/preference_api_client.py`
- `python/ui/preference_session.py`
- `python/tests/test_preference_api.py`
- `python/tests/test_preference_ui.py`
- `python/examples/preference_http_demo.py`
- `docs/09-阶段A第二批API与Streamlit多轮交互验收报告.md`

修改：

- `python/api/app.py`：增加两个偏好接口、响应构造、日期摘要和草稿回执验证。
- `python/ui/streamlit_app.py`：新增自然语言模式，保留结构化模式。
- `python/preferences/contracts.py`：草稿拒绝未知字段，防止客户端注入未定义状态。
- `python/preferences/clarification.py`：日期/天数冲突即使不属于缺失必填字段，也生成明确追问。
- `python/tests/test_preference_clarification.py`：补充上述冲突追问回归。
- `python/.env.example`：增加 Streamlit API 地址和可选草稿签名密钥。
- `python/README.md`：同步 API、启动、Mock、会话和当前范围。

没有修改预算循环核心、确定性 Provider、Agent 业务选择、WeatherAgent 或多方案逻辑。

## 9. Git 状态与差异说明

最终仍为 dirty `main`，未执行任何 Git 写操作。相对 HEAD 的累计已跟踪差异为：

```text
19 files changed, 1509 insertions(+), 498 deletions(-)
git diff --check -> 0
```

仅有既存 LF→CRLF 提示。该统计包含阶段 0、阶段 A 第一批和第二批的累计内容，且不包含所有未跟踪新增文件，不能作为第二批单独代码量。第二批边界应以本报告文件清单和阶段 A1 归档为准。

## 10. 遗留项与阶段 A 第三批接口

当前未实现：

- 用户确认后的正式 Pipeline 调用。
- 自然语言流程与结构化流程的产品前后对比实验。
- 会话数据库、跨设备续聊和用户身份。
- 真实 LLM Parser。
- `pace` 对正式 ActivityAgent 的业务影响。
- WeatherAgent 和双方案比较。

阶段 A 第三批可以基于以下已验收接口继续：

1. 复用完整结果中的 `draft`、`draft_receipt` 和 `pipeline_preferences_preview`。
2. 新增确认操作时必须再次验证草稿回执并由服务端调用 `to_user_preferences`，不能信任前端预览。
3. 确认后的规划请求继续使用现有 `/api/plan` 或明确新增组合端点，保留原 API 兼容性。
4. 明确决定 `pace` 是向后兼容扩展 `UserPreferences`，还是进入独立只读规划上下文；决策前不得宣称它影响活动。
5. 若部署多 Worker，所有 Worker 必须配置相同的 `PREFERENCE_DRAFT_SIGNING_KEY`；否则进程间回执无法互认。
6. 产品实验应分别记录结构化表单和澄清模式的完成交互数、字段修订数及失败原因，不能把固定语料结果外推为真实 LLM 能力。

阶段 A 第二批到此停止，不自动进入第三批、WeatherAgent 或多方案比较。
