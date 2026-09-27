# 多Agent智能旅游行程规划系统

---

## 这个项目是什么？

Python 主力版本当前是一个 **7 Agent、Mock First** 的智能旅游规划 MVP。用户既可填写结构化表单，也可用中文自然语言经过多轮澄清和签名确认；系统随后完成目的地、航班、酒店、天气感知活动与预算规划。

**核心亮点**:
- 7 个 Agent 各司其职，通过 Pipeline + 并行 + 累积式预算循环协作
- 航班、酒店、天气并行；活动必须等待天气结果或明确回退状态
- `relaxed / balanced / packed / 未指定` 旅行节奏进入正式规划；未指定保持旧三时段行为
- 超预算按活动、酒店、航班顺序从固定候选快照重选，不修改候选原价
- 所有航班、酒店、活动、天气、时长、强度和价格均为明确标记的确定性 Mock 数据

---

## 目录导航

| 内容 | 链接 | 说明 |
|------|------|------|
| **Python 实现** | [python/](python/) | 主力版本，FastAPI + Streamlit |
| **架构设计** | [docs/01-架构设计详解.md](docs/01-架构设计详解.md) | 架构图 + 设计决策 |
| **代码讲解** | [docs/02-代码讲解.md](docs/02-代码讲解.md) | 逐模块代码详解 |
| **最终 PRD** | [docs/22-Multi-Agent-Travel-Copilot最终PRD.md](docs/22-Multi-Agent-Travel-Copilot最终PRD.md) | 已实现范围、状态与后续路线 |
| **架构与复现** | [docs/23-最终系统架构API与复现指南.md](docs/23-最终系统架构API与复现指南.md) | API、数据流、启动与实验复现 |
| **D3 最终验收** | [docs/24-阶段D3最终产品验收报告.md](docs/24-阶段D3最终产品验收报告.md) | 技术、浏览器、实验与待办结论 |
| **Classic UI 验收** | [docs/25-ClassicUI前端改造验收报告.md](docs/25-ClassicUI前端改造验收报告.md) | 原版布局复用、双方案、天气与备注澄清验收 |

---

## 系统架构

```
用户输入
  │
  ▼
┌────────────────┐
│ Preference     │  收集用户偏好（预算/风格/时间/禁忌）
│ Agent          │
└───────┬────────┘
        │
        ▼
┌────────────────┐
│ Destination    │  推荐目的地（季节/签证/安全/性价比评分）
│ Agent          │
└───────┬────────┘
        │
        ├──────────────────┬──────────────────┐
        ▼                  ▼                  ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ Flight Agent │  │ Hotel Agent  │  │ Weather Agent│  ← 三者并行
└──────┬───────┘  └──────┬───────┘  └──────┬───────┘
       └─────────────────┼─────────────────┘
                         ▼
               ┌────────────────┐
               │ Activity Agent │  天气 + 兴趣 + pace 硬约束后确定性选择
               └───────┬────────┘
                       ▼
               ┌────────────────┐
               │ Budget Agent   │  预算校验
               └───────┬────────┘
                       │
                ┌──────┴──────┐
                │             │
             通过？         超预算？
                │             │
                ▼             ▼
            输出行程     固定候选重选（活动→酒店→航班）
```

**编排模式**: Pipeline（串行）+ 并行（asyncio.gather）+ 预算循环（while loop）

---

## 快速开始（5分钟上手）

### 前置条件

- Python 3.10+（已使用 Python 3.11.16 验证）

### Python 版本

```bash
# 1. 克隆项目
git clone https://github.com/Mirror-JSong/multi-agent-travel-copilot.git
cd multi-agent-travel-copilot

# 2. 安装依赖
cd python
pip install -r requirements.txt

# 3. 运行 CLI 演示（不需要任何 API Key！）
python main.py

# 4. 自定义参数
python main.py --budget 15000 --departure 上海 --start 2026-06-01 --end 2026-06-07 --style luxury --travelers 2

# 5. 启动 FastAPI（终端 1）
python -m uvicorn api.app:app --host 127.0.0.1 --port 8000

# 6. 启动正式 Classic UI（终端 2，默认网页：http://127.0.0.1:8785）
python -m streamlit run ui/streamlit_classic_app.py --server.address 127.0.0.1 --server.port 8785

# 可选：启动保留的 D2/D3 五步工作台（备选界面：http://127.0.0.1:8501）
python -m streamlit run ui/streamlit_app.py --server.address 127.0.0.1 --server.port 8501

# 7. 运行测试
python -m pytest tests/ -v
```

Windows PowerShell 如果系统 `python` 没有安装 Streamlit，可直接使用当前已验收的
`travel-agent` 环境解释器，并分别在两个终端运行：

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

Classic UI 是正式默认网页，保留原版左右分栏、航班/酒店/行程/预算标签，并接入自然语言
澄清、天气感知活动、预算优化和双方案比较。`ui/streamlit_app.py` 的 D2/D3 五步工作台继续
作为备选界面保留。两套界面使用同一个正式 FastAPI 后端；当前 Parser、航班、酒店、活动和
天气数据仍为确定性 Mock，不是实时 LLM、实时价格、库存或天气预报。

# 终端 1 和终端 2 需要都开着哦！

---

## 运行效果展示

### CLI 输出示例

```
============================================================
📋 行程规划结果
============================================================

🌍 目的地: 首尔, 韩国
   潮流时尚与历史文化交汇
   亮点: 景福宫, 明洞, 北村韩屋村, 南山塔

✈️  去程: 东方航空 MU1903 ¥1578
✈️  返程: 南方航空 CZ6372 ¥1703

🏨 酒店: 首尔精品设计酒店 (4.0星)
   ¥395/晚 × 4 晚
   设施: WiFi, 早餐, 酒吧

📅 每日行程:

  2026-05-01 (日花费: ¥730)
    [morning  ] 博物馆参观 (3.0h) ¥80
    [afternoon] 温泉/SPA体验 (2.0h) ¥350
    [evening  ] 文化演出 (2.0h) ¥300

  2026-05-02 (日花费: ¥530)
    [morning  ] 博物馆参观 (3.0h) ¥80
    [afternoon] 特色午餐 (1.5h) ¥150
    [evening  ] 文化演出 (2.0h) ¥300

💰 预算明细:
   航班: ¥3281
   酒店: ¥1580
   活动: ¥2270
   ─────────────
   总计: ¥7131 / 预算: ¥10000
   ✅ 预算内
```

---

## 项目结构

```
.
├── README.md                    ← 你正在看的文件
│
├── docs/                        ← 项目文档（共 26 份）
│   ├── 01-架构设计详解.md        ← 架构图与设计决策
│   ├── 02-代码讲解.md            ← 逐模块代码讲解
│   ├── 03～21                   ← 各阶段审计、设计、实验与验收文档
│   ├── 22-Multi-Agent-Travel-Copilot最终PRD.md
│   ├── 23-最终系统架构API与复现指南.md
│   ├── 24-阶段D3最终产品验收报告.md
│   ├── 25-ClassicUI前端改造验收报告.md
│   └── Multi_Agent_Travel_Copilot_PRD_v1.md ← 初版 PRD
│
└── python/                      ← Python 实现（主力版本）
    ├── main.py                  ← CLI 入口
    ├── requirements.txt
    ├── config/settings.py       ← 配置管理
    ├── models/schemas.py        ← Pydantic 数据模型
    ├── agents/                  ← 7 个 Agent
    │   ├── base_agent.py        ← Agent 基类（模板方法模式）
    │   ├── preference_agent.py  ← 偏好收集
    │   ├── destination_agent.py ← 目的地推荐
    │   ├── flight_agent.py      ← 航班搜索
    │   ├── hotel_agent.py       ← 酒店搜索
    │   ├── activity_agent.py    ← 活动推荐
    │   ├── weather_agent.py     ← 天气获取与可用性契约
    │   └── budget_agent.py      ← 预算校验
    ├── orchestrator/            ← 编排层
    │   ├── pipeline.py          ← Pipeline 编排器
    │   ├── parallel.py          ← 并行执行器
    │   └── budget_loop.py       ← 预算循环控制
    ├── tools/                   ← Mock 搜索工具
    ├── api/app.py               ← FastAPI 后端
    ├── ui/streamlit_classic_app.py ← 正式默认 Classic UI
    ├── ui/streamlit_app.py      ← 备选 D2/D3 五步工作台
    └── tests/test_agents.py     ← 10个单元测试
```

---

## 7 个 Agent 详解

| # | Agent | 职责 | 输入 | 输出 | 重点 |
|---|-------|------|------|------|---------|
| 1 | **Preference** | 收集/补充用户偏好 | UserPreferences | enriched UserPreferences | 为什么需要单独的偏好Agent？ |
| 2 | **Destination** | 推荐目的地 | UserPreferences | Top3 城市 + 推荐理由 | 多维度评分算法设计 |
| 3 | **Flight** | 航班搜索比价 | 出发城市+目的地+日期 | 航班列表 + 推荐航班 | 并行执行、评分函数 |
| 4 | **Hotel** | 酒店匹配 | 目的地+入住日期+风格 | 酒店列表 + 推荐酒店 | 风格匹配、房间数计算 |
| 5 | **Weather** | 提供逐日 Mock 天气 | 目的地+活动日期 | 天气结果/明确不可用状态 | 确定性与降级边界 |
| 6 | **Activity** | 生成每日行程 | 目的地+兴趣+天气+pace | 活动或显式休息时段 | 天气/节奏硬约束 |
| 7 | **Budget** | 预算校验与调整 | 所有费用汇总 | 预算明细 + 调整历史 | **候选重选闭环** |

---

## 技术栈

| 维度 | Python |
|------|--------|
| **框架** | FastAPI + asyncio |
| **并行** | asyncio.gather |
| **数据模型** | Pydantic v2 |
| **状态安全** | 不同字段无冲突 |
| **测试** | pytest |
| **部署** | uvicorn |

---

## API 接口文档

### POST /api/plan

**请求**:
```json
{
  "budget": 10000,
  "departure_city": "北京",
  "start_date": "2026-05-01",
  "end_date": "2026-05-05",
  "travel_style": "comfort",
  "pace": "relaxed",
  "num_travelers": 1,
  "interests": ["美食", "历史"],
  "notes": ""
}
```

**响应**:
```json
{
  "destination": "首尔",
  "country": "韩国",
  "flight_cost": 3281,
  "hotel_cost": 1580,
  "activity_cost": 2270,
  "total_cost": 7131,
  "budget": 10000,
  "within_budget": true,
  "adjustment_rounds": 0,
  "hotel_name": "首尔精品设计酒店",
  "days": 4,
  "highlights": ["景福宫", "明洞", "北村韩屋村", "南山塔"]
}
```

### GET /api/health

```json
{"status": "ok", "service": "travel-planner", "agents": 7}
```

### 双方案比较（Python）

- `POST /api/plans/compare`：结构化 `UserPreferences` → 两套独立方案 → 四维评价与解释。
- `POST /api/preferences/compare`：验证已签名、完整且显式确认的偏好草稿后执行同一比较服务。

响应保留每套方案各自的业务状态、费用、天气、pace、预算调整历史，以及服务端生成的
`recommended`、`tie`、`only_feasible` 或 `no_recommendation`。HTTP 200 只表示请求被
处理，不表示两个旅行方案都成功。当前所有旅行候选、天气、活动时长及强度均为带版本
标识的确定性 Mock 数据，不是实时价格、库存或预报。

Streamlit 同时保留单方案入口，并在自然语言和结构化模式下提供双方案比较。完整契约、
实验方法和限制见 [Python README](python/README.md) 与阶段 C 最终验收报告。

---

## 常见问题

### Q: 需要 API Key 吗？

**不需要！** 系统默认使用 Mock 模式，所有数据都是模拟生成的，可以零成本完整运行。
如果你想接入真实 LLM，设置环境变量即可：

```bash
export LLM_PROVIDER=minimax
export LLM_API_KEY=your-api-key
```

### Q: 数据是真实的吗？

Mock 模式下的航班、酒店、活动、天气、活动时长和强度均为模拟数据。它们用于验证数据契约与规则，不代表真实市场价格、真实预报或景点实测属性。

---

## 参考的企业级项目

本项目的架构设计参考了以下优秀开源项目：

| 项目 | 框架 | 特点 |
|------|------|------|
| [Y-66/Traveler](https://github.com/Y-66/Traveler) | Agno | MCP + RAG + Memory，最完整 |
| [agno-langfuse-travel-planner](https://github.com/mcikalmerdeka/agno-langfuse-travel-planner) | Agno + Langfuse | 可观测性标杆 |
| [langgraph_travel_planner](https://github.com/sergio11/langgraph_travel_planner_assistant) | LangGraph | Supervisor 架构 |
| [Ninja-Navigator-AI](https://github.com/happyrao78/Ninja-Navigator-AI) | LangChain | FastAPI + Streamlit |
| [tripsage-ai](https://github.com/BjornMelin/tripsage-ai) | LangGraph | 70% 复杂度降低案例 |

---

## 学术参考

- [HiMAP-Travel](https://arxiv.org/html/2603.04750v1) - 分层多Agent旅行规划，52.78% 验证通过率
- [ATLAS](https://arxiv.org/html/2509.25586v1) - 约束感知多Agent协作，84% 最终通过率
