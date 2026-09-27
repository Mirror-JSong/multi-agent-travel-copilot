# Multi-Agent Travel Copilot 产品 UI/UX 最终检查报告

## 1. 结论

D3 对 D2 正式 Streamlit 工作台进行了代码审计、真实 Edge 浏览器操作和桌面/390×844
窄屏截图复核。五步流程、Mock 标识、业务状态、双方案评价和详情数据均来自正式 FastAPI
响应，不是静态原型。发现的两个明确缺陷已经定向修复：偏好摘要重复展示，以及窄屏详情页
长状态值被截断。

本轮没有修改 Destination、Weather、Activity、Budget、Comparison 或评分算法。视觉验收通过；
完整键盘无障碍和屏幕阅读器验收尚未由真实使用者执行，应作为后续可访问性专项，而不能由
代码审查代替。

## 2. 检查方法与证据

- 规范基线：D1 信息架构、视觉规范与异常状态设计。
- 代码：`python/ui/streamlit_app.py`、`python/ui/components/`、API Client 和
  Session State 契约。
- 正式服务：Uvicorn `127.0.0.1:8765`、Streamlit `127.0.0.1:8766`。
- 浏览器：Microsoft Edge 153，无头真实 Chromium DOM；桌面 1440×1200、窄屏
  390×844。
- 自动化：Streamlit AppTest、FastAPI API 测试和工作台状态测试。
- 浏览器记录：`python/experiments/results/d3_browser_acceptance.json`。
- 截图：`docs/assets/stage-d3/`。

自动化浏览器脚本输入自然语言需求、完成一次澄清、确认偏好、调用双方案 API、进入首个
目的地详情，再切换窄屏并返回比较页。脚本不注入静态结果，不绕过 Streamlit 对 FastAPI
的调用。

## 3. 五步信息架构检查

| 步骤 | 真实检查结果 | 状态 |
|---|---|---|
| 需求对话 | 自然语言为主入口；结构化表单仍可切换；字段来源和缺失项可见 | 通过 |
| 偏好确认 | 日期、返程日、活动日期、住宿晚数、pace 和模式选择清晰；必须主动确认 | 通过 |
| 规划执行 | 只显示真实整体加载状态，无虚构 Agent 百分比 | 通过 |
| 方案比较 | 服务端评分、有效权重、推荐类型和证据直接展示；不在前端重算 | 通过 |
| 行程详情 | 天气、活动、休息、交通住宿、预算与调整历史来自同一方案结果 | 通过 |

页面切换保留会话数据；切换两个目的地只读取既有 `MultiPlanResult`，不会再次执行
Pipeline。修改偏好会清除旧规划、评价和推荐；网络失败保留有效偏好并允许重试。

## 4. 已修复缺陷

### UX-01 偏好摘要重复

实际页面原先在右栏连续渲染紧凑摘要与完整字段表，桌面重复信息，窄屏增加不必要滚动。
现在默认只展示六个关键字段卡片，完整字段及来源收进“查看全部识别字段与来源”折叠区。
缺失字段、来源和完整详情仍可访问。

### UX-02 窄屏状态截断

D2 的 390×844 截图中，`COMPLETED（预算内完成）` 在指标组件内被截断。现在主状态显示
短值（例如 `COMPLETED`、`预算不可满足`），下一行单独显示精确业务状态代码。状态说明仍
由 success/warning/error 文本完整呈现，因此不会只依赖颜色。

修复测试：`tests/test_preference_ui.py` 验证折叠区存在、完成状态短值和精确状态代码；
UI 专项实测 `26 passed`。

## 5. 视觉与响应式检查

- 统一使用 D1 的深海军蓝、青绿色、珊瑚红和纸张网格语言。
- 标题、正文、标签、金额和状态层级在桌面与窄屏均可辨认。
- 双方案在窄屏自然纵向排列，不强行保持双列。
- Mock / Not Live、非预订提示持续可见；页面没有“购买”“下单”等误导动作。
- 使用 Streamlit 原生输入、单选、按钮、Tab 和 Expander，保留基础键盘焦点语义。
- 页面未引入外部字体或运行时前端依赖。

已确认的技术边界：Streamlit 不能提供真正的客户端路由和精细 Agent 事件流；当前使用
Session State 与整体加载状态是诚实实现。390 px 下长表格仍可能需要横向浏览，后续可按
数据表可访问性专项逐列折叠，但不影响核心流程。

## 6. 数据一致性抽查

| 页面 | 核对字段 | 结果 |
|---|---|---|
| 比较卡 | 目的地、状态、最终费用、预算余量 | 与 `multi_plan.plans` 一致 |
| 推荐解释 | 推荐类型、政策版本、证据 | 与 `comparison` 一致 |
| 详情 | 天气、活动 ID、pace、休息时段 | 与当前 `DestinationPlan.plan` 一致 |
| 预算 | 航班、酒店、活动、总计、调整历史 | 使用后端明细，不在前端重算 |
| 异常 | unavailable、constraints、budget、failed | 保留不同业务语义 |

## 7. 截图索引

- [需求输入](assets/stage-d3/01-intake-desktop.png)
- [多轮澄清](assets/stage-d3/02-clarification-desktop.png)
- [偏好确认](assets/stage-d3/03-confirmation-desktop.png)
- [双方案比较](assets/stage-d3/04-comparison-desktop.png)
- [桌面详情](assets/stage-d3/05-detail-desktop.png)
- [窄屏详情](assets/stage-d3/06-detail-narrow.png)
- [窄屏比较](assets/stage-d3/07-comparison-narrow.png)

这些截图只证明本地固定 Mock 产品流程可运行，不是生产部署或真人体验证据。
