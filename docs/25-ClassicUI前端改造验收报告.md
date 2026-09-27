# Multi-Agent Travel Copilot Classic UI 前端改造验收报告

## 1. 验收结论

Classic UI 已作为独立 Streamlit 入口完成，当前正式五步工作台
`python/ui/streamlit_app.py` 未被替换。新入口复用已经验收的 FastAPI、偏好签名、单方案、
双方案、天气感知活动、pace、预算循环和四维评价能力，没有复制或改写后端业务算法。

最终自动化结果为 `335 passed, 1 warning in 42.67s`，其中原有 320 项全部通过，新增
Classic UI 专项 15 项全部通过。唯一 warning 仍是 Starlette `TestClient` 使用 AnyIO 旧别名
产生的第三方弃用提示。

真实 Edge 浏览器经正式 FastAPI 和 Streamlit 完成：

> 额外备注 → 规则解析 → 表单冲突确认 → 主动澄清 → HMAC 草稿确认 → 双方案比较 →
> 切换目的地 → 天气感知每日行程 → 390×844 窄屏复核。

本次严格停止在独立预览版；是否替换正式入口应由用户体验后另行决定。

## 2. 原版 UI 定位结论

本次比较了三类来源：

1. Git HEAD `8132dfc` 中的 `python/ui/streamlit_app.py`：具有最初左右分栏和四标签结构，
   但包含过时的 6 Agent 文案及早期代码问题，不能直接恢复为当前入口。
2. `.stage-backups/stage0-worktree-main-8132dfc-20260924.tar.gz`：保留了用户复现后实际使用的
   左右分栏、严格日期校验、业务状态和预算调整历史，是最接近“原版使用体验”的代码依据。
3. D2/D3 正式入口：包含五步导航、自然语言对话、双方案和完整解释，是当前已验收产品，继续保留。

因此 Classic UI 采用阶段 0 页面作为视觉与交互骨架，同时把架构说明更新为当前真实的 7 Agent
流程。没有使用 `git checkout`、`git reset` 或历史文件整体覆盖当前工作区。

## 3. Classic UI 架构

```text
streamlit_classic_app.py
  ├─ 左侧原版结构化表单
  ├─ 备注解析 / 澄清 / 冲突确认
  ├─ 单方案或双方案开关
  └─ 右侧原版四标签
       ├─ 航班
       ├─ 酒店
       ├─ 行程（天气、环境、强度、休息、解释）
       └─ 预算（真实费用与调整历史）

PreferenceApiClient
  ├─ /api/plan/full
  ├─ /api/plans/compare
  ├─ /api/preferences/parse
  ├─ /api/preferences/clarify
  ├─ /api/preferences/plan
  └─ /api/preferences/compare
```

前端不计算预算、天气分、pace 规则或综合评分。所有业务状态、候选、费用、评价和解释均来自
正式 API 响应。

## 4. 页面与状态规则

### 4.1 原版布局

- 顶部保留标题、副标题、Mock/非实时/非预订提示和分隔线。
- 左侧保留预算、出发城市、日期、风格、人数、兴趣、备注和规划按钮；pace 采用最小兼容扩展。
- 右侧保留目的地简介和航班、酒店、行程、预算四个标签。
- 初始为空时只显示简洁的 7 Agent 数据流，不使用五步导航或大面积产品卡片。

### 4.2 双方案

- 复选框默认关闭，保持原版单方案习惯。
- 开启后调用 `/api/plans/compare` 或签名草稿入口 `/api/preferences/compare`。
- 显示两套方案的业务状态、实际费用、四维评价、数据完整度、综合分资格和正式解释。
- `recommended`、`tie`、`only_feasible`、`no_recommendation` 使用服务端正式语义。
- 切换当前目的地只更换已有结果视图，不再次调用 Pipeline；AppTest 已记录请求数量不变。
- 不可行或证据不足的方案不会填充虚假综合分。

### 4.3 天气行程

- 行程标签显示每日天气、降雨概率、温度、来源、版本及 Mock 标识。
- 每项活动显示时间段、室内/室外、强度、预计时长、价格、pace 依据和天气评价。
- relaxed 的休息时段显示为明确的 ¥0 休息，不伪造成活动。
- 只有决策记录确认发生替换时才显示天气驱动替换；普通天气参与评价不会被描述为替换。
- 天气 unavailable 显示无法验证适配性，不转换为晴天。

### 4.4 备注解析与安全边界

- 默认表单值不会被当作用户明确声明。
- 备注解析结果保留 `explicit / inferred / unknown` 来源。
- 备注与表单冲突时必须选择“采用备注”或“以当前表单为准并重新验证”，不会静默覆盖。
- 选择当前表单时，系统将表单生成规范文本后重新调用 `/api/preferences/parse`，得到新的服务端签名；
  不在客户端篡改旧草稿。该策略也能正确清除用户从表单删除的可选 pace 或兴趣。
- 用户补充回答继续调用 `/api/preferences/clarify`。
- 修改已确认表单会使旧确认、规划、评分和目的地选择失效；旧回执保留用于审计，但不能继续规划。
- 只有识别表中展示且经服务端验证的字段会成为规划约束；无法识别的备注原文保留，但不假装执行。

## 5. 自动化测试

新增 `python/tests/test_classic_ui.py`，覆盖：

| 场景 | 证据 | 结果 |
|---|---|---|
| 原版结构化单方案 | `/api/plan/full`、四标签和天气来源 | 通过 |
| 双方案与切换 | 两套状态、服务端评分；切换无新 HTTP 请求 | 通过 |
| 备注识别与显式冲突处理 | 解析、采用备注、确认、签名规划 | 通过 |
| 主动澄清 | 不完整输入、追问、补充、再次采用与确认 | 通过 |
| 偏好修改失效 | 旧确认、规划与评分清空，签名标记过期 | 通过 |
| 四类比较结果 | recommended / tie / only_feasible / no_recommendation | 通过 |
| 天气不可用 | 不显示伪造晴天 | 通过 |
| 必要 Agent 故障 | FAILED 和结构化原因，不沿用旧成功结果 | 通过 |
| API 失败恢复 | 有效表单输入保留，可重试 | 通过 |
| 会话隔离 | 两个 AppTest 会话不共享表单或结果 | 通过 |
| HMAC 篡改 | 被现有服务端边界以 HTTP 422 拒绝 | 通过 |
| 状态助手 | 默认值、冲突和派生结果失效规则 | 通过 |

专项执行：`15 passed, 1 warning in 9.21s`。

全量执行：`335 passed, 1 warning in 42.67s`。

第一次全量运行人为设置了 `PYTHONIOENCODING=utf-8`，导致一个原有跨进程测试由父进程按 GBK
解码子进程 UTF-8 输出而失败。未修改测试或产品代码；恢复项目原始环境后完整重跑为 335 项全通过。

## 6. 真实服务与浏览器验收

实际启动：

- FastAPI：`127.0.0.1:8765`，`/api/health` 返回 HTTP 200 和 7 Agent。
- D3 正式工作台：`127.0.0.1:8766`。
- Classic UI：`127.0.0.1:8786`。
- 阶段 0 原版参考页：`127.0.0.1:8787`，仅用于截图对照。

真实 Edge 通过 DevTools 协议操作 Streamlit DOM，并由 Classic UI 通过真实 TCP 调用 FastAPI。
机器可读结果位于：

- `python/experiments/results/classic_ui_browser_acceptance.json`
- `python/experiments/run_classic_ui_browser_acceptance.mjs`

截图位于 `docs/assets/classic-ui/`：

1. `01-original-reference.png`：阶段 0 原版参考。
2. `02-d3-workbench-reference.png`：当前五步工作台参考。
3. `03-classic-initial-desktop.png`：Classic 左右分栏初始页。
4. `04-classic-note-conflicts.png`：备注与表单冲突。
5. `05-classic-confirmed-preferences.png`：澄清和 HMAC 确认完成。
6. `06-classic-comparison-desktop.png`：双方案、四维评价和推荐。
7. `07-classic-itinerary-weather-desktop.png`：桌面天气行程与依据。
8. `08-classic-itinerary-narrow.png`：390×844 天气行程。

浏览器断言全部为 true。自动化浏览器验收不是实际用户可用性研究；所有数据均为明确标记的
确定性 Mock，不是实时价格、天气、库存或预订结果。

验收结束后仅停止本次记录的 FastAPI、三个 Streamlit 和专用 headless Edge PID，端口
8765、8766、8786、8787、9234 均确认无监听。

## 7. 修改与新增文件

本次新增：

- `python/ui/streamlit_classic_app.py`
- `python/ui/classic_state.py`
- `python/tests/test_classic_ui.py`
- `python/experiments/run_classic_ui_browser_acceptance.mjs`
- `python/experiments/results/classic_ui_browser_acceptance.json`
- `docs/assets/classic-ui/01-original-reference.png` 至 `08-classic-itinerary-narrow.png`
- `docs/25-ClassicUI前端改造验收报告.md`

本次文档性修改：

- `README.md`
- `python/README.md`

未修改：

- Agent、Provider、Pipeline、预算、天气、pace、比较和评分核心代码

Classic UI 的补丁没有编辑 `python/ui/streamlit_app.py`，该文件继续作为 D2/D3 五步工作台。
最终核查发现它在开发前备份完成后出现 20 处文案替换（文件时间 22:46，备份时间 22:43），
内容包括 `TravelMate 智能旅行助手` 等现有页面文案。该变化不是 Classic UI 补丁产生，按用户并行修改
处理并完整保留；没有用备份版本覆盖它。最终浏览器对照截图展示的是这份当前正式页面。

## 8. 工作区保护

开发前在仓库外建立完整备份：

`E:\Files\Documents\002_Mirror\AI\multi-agent-travel-planner-backups\stage-d3-complete-worktree-main-8132dfc-20260926-before-classic-ui.tar.gz`

- 大小：2,770,897 bytes
- SHA-256：`CD2A0EFCC02BB923EC10427D3A0F339097368576C6FA1D2BD7746EC444D7E12D`
- 已在仓库外抽样解包并逐字节验证当前正式 UI、D3 报告和一个重要未跟踪测试文件。
- 备份包含开发前已跟踪修改和重要未跟踪成果；没有删除原备份或工作区文件。

由于验收过程中检测到正式 D3 页面存在一份备份后出现的并行文案修改，收尾时又建立了包含该变化
和全部 Classic UI 交付物的最终仓库外备份：

`E:\Files\Documents\002_Mirror\AI\multi-agent-travel-planner-backups\stage-classic-ui-complete-worktree-main-8132dfc-20260926-final.tar.gz`

该最终归档的大小与 SHA-256 记录在本次最终交付中；抽样验证覆盖正式 D3 UI、Classic UI、
Classic 测试和浏览器验收 JSON。

本次未执行 `git add`、`commit`、`switch`、`checkout`、`reset`、`clean`、`merge`、`tag` 或
`push`。仓库仍为 main / HEAD `8132dfc`，累计工作区差异仍属于多个既有阶段，不能误认为本次改造。

## 9. 已知限制与后续决策

- Classic UI 是独立预览入口，尚未替换正式入口。
- 窄屏使用 Streamlit 原生列折叠：左侧表单先显示，结果区随后显示；可操作且可读，但不是自定义移动端导航。
- Mock Parser 只支持已定义语料，未识别的自由备注不会成为真实业务约束。
- 当前没有真实 LLM、实时天气、航班库存、酒店库存或预订能力。
- 下一步只需由用户实际体验两个入口，决定是否将 Classic 设为默认；本批不自动执行替换。
