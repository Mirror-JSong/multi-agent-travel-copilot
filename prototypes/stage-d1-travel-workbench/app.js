(function () {
  "use strict";

  const DATA = window.PROTOTYPE_DATA;
  const root = document.getElementById("root");
  const PAGES = [
    ["需求对话", "说出想法", "chat"],
    ["偏好确认", "核对约束", "check"],
    ["规划执行", "等待结果", "spark"],
    ["方案比较", "理解取舍", "compare"],
    ["行程详情", "查看每天", "calendar"]
  ];
  const state = {
    page: Number(localStorage.getItem("d1-page") || 0),
    phase: 0,
    structured: false,
    planMode: "compare",
    scenario: "recommended",
    selectedPlan: "mountain",
    detailTab: "itinerary",
    override: "normal",
    drawer: false,
    editModal: false,
    toast: "",
    draftInput: DATA.conversation.initial
  };

  const iconPaths = {
    route: '<path d="M4 19c4-1 4-7 8-8s4 4 8-6"></path><circle cx="4" cy="19" r="2"></circle><circle cx="12" cy="11" r="2"></circle><circle cx="20" cy="5" r="2"></circle>',
    chat: '<path d="M5 18 3 21v-6a8 8 0 1 1 3 3"></path><path d="M8 9h8M8 13h5"></path>',
    check: '<circle cx="12" cy="12" r="9"></circle><path d="m8 12 2.7 2.7L16 9"></path>',
    spark: '<path d="m12 3 1.4 4.4L18 9l-4.6 1.6L12 15l-1.4-4.4L6 9l4.6-1.6L12 3Z"></path><path d="m18 15 .7 2.3L21 18l-2.3.7L18 21l-.7-2.3L15 18l2.3-.7L18 15Z"></path>',
    compare: '<path d="M8 4H4v16h4"></path><path d="M16 4h4v16h-4"></path><path d="M9 8h6M9 12h6M9 16h6"></path>',
    calendar: '<rect x="3" y="5" width="18" height="16" rx="2"></rect><path d="M8 3v4M16 3v4M3 10h18"></path>',
    arrow: '<path d="M5 12h14"></path><path d="m14 7 5 5-5 5"></path>',
    alert: '<path d="M10.3 4.1 2.6 18a2 2 0 0 0 1.8 3h15.2a2 2 0 0 0 1.8-3L13.7 4.1a2 2 0 0 0-3.4 0Z"></path><path d="M12 9v4M12 17h.01"></path>',
    close: '<path d="m6 6 12 12M18 6 6 18"></path>',
    wallet: '<path d="M4 6h14a2 2 0 0 1 2 2v11H4a2 2 0 0 1-2-2V6a3 3 0 0 1 3-3h12"></path><path d="M15 12h5"></path>'
  };
  const icon = (name) => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${iconPaths[name] || iconPaths.alert}</svg>`;
  const money = (value) => value == null ? "—" : `¥${Number(value).toLocaleString("zh-CN")}`;
  const statusPill = (status, label) => `<span class="status-pill ${status}">${label}</span>`;
  const mockBadge = () => '<span class="badge mock">示例 Mock 数据</span>';

  function heading(step, title, description, action = "") {
    return `<div class="screen-heading"><div><p class="eyebrow">Step ${String(step).padStart(2, "0")}</p><h1>${title}</h1><p class="lede">${description}</p></div>${action}</div>`;
  }

  function preferenceSummary(compact) {
    const p = DATA.preferences;
    const items = [
      ["出发地", p.departure, "用户提供"], ["日期", `${p.startDate.slice(5)} — ${p.endDate.slice(5)}`, "用户提供"],
      ["人数", `${p.travelers} 人`, "用户提供"], ["预算", money(p.budget), "用户提供"],
      ["风格", p.style, "用户提供"], ["节奏", p.pace, "规则推导"],
      ["兴趣", p.interests.join("、"), "用户提供"], ["住宿", `${p.nights} 晚`, "规则推导"]
    ];
    return `<div class="preference-grid">${items.slice(0, compact ? 6 : items.length).map(([label, value, source]) => `<div class="preference-item"><span>${label}<small class="source">${source}</small></span><strong>${value}</strong></div>`).join("")}</div>`;
  }

  function sideRail() {
    const routes = PAGES.map((item, index) => `<button class="route-button ${state.page === index ? "active" : ""} ${state.page > index ? "done" : ""}" data-page="${index}" ${state.page === index ? 'aria-current="step"' : ""}><span class="route-node">${state.page > index ? icon("check") : index + 1}</span><span class="route-label"><strong>${item[0]}</strong><span>${item[1]}</span></span></button>`).join("");
    return `<aside class="side-rail"><div class="brand"><svg class="brand-mark" viewBox="0 0 48 48" aria-hidden="true"><path d="M8 35c8-2 7-15 16-15s7 10 16-7" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"></path><circle cx="8" cy="35" r="5" fill="currentColor"></circle><circle cx="24" cy="20" r="5" fill="#d85a43"></circle><circle cx="40" cy="13" r="5" fill="#e3ad37"></circle></svg><div><strong>旅迹</strong><small>Travel Copilot</small></div></div><nav class="route-nav" aria-label="规划步骤">${routes}</nav><div class="side-bottom"><div class="mock-stamp">MOCK / NOT LIVE<br>${DATA.meta.providerVersion}</div><button class="ghost-link" data-action="open-states">预览异常与特殊状态</button></div></aside>`;
  }

  function topbar() {
    return `<header class="topbar"><div class="crumb">${icon(PAGES[state.page][2])}<span>旅行工作台</span><span>/</span><strong>${PAGES[state.page][0]}</strong></div><div class="top-actions">${mockBadge()}<button class="button secondary small" data-action="open-states">${icon("alert")}状态预览</button></div></header>`;
  }

  function conversationScreen() {
    const messages = state.phase === 0 ? '<div class="notice"><strong>示例输入已准备好。</strong><br>点击“发送并解析”查看多轮澄清状态；原型不会调用真实 LLM。</div>' : `
      <div class="message user"><div class="message-bubble">${DATA.conversation.initial}<span class="message-meta">用户 · 已发送</span></div><div class="avatar">你</div></div>
      <div class="message"><div class="avatar">${icon("spark")}</div><div class="message-bubble">已识别上海、5 天、预算 ¥15,000、摄影与美食、轻松节奏。<br><strong>${DATA.conversation.question}</strong><span class="message-meta">澄清助手 · 规则 v1</span></div></div>
      ${state.phase >= 2 ? `<div class="message user"><div class="message-bubble">${DATA.conversation.answer}<span class="message-meta">用户 · 已发送</span></div><div class="avatar">你</div></div><div class="message"><div class="avatar">${icon("check")}</div><div class="message-bubble">必要信息已完整。已推导返程日为 10 月 6 日、住宿 5 晚，请在下一步确认。<span class="message-meta">澄清助手 · 可确认</span></div></div>` : ""}`;
    const structuredForm = `<div class="panel-body stack"><div class="split-equal"><label class="stack"><span class="hint">出发城市</span><input value="上海" readonly></label><label class="stack"><span class="hint">总预算</span><input value="15000" readonly></label></div><div class="split-equal"><label class="stack"><span class="hint">出发日期</span><input value="2026-10-01" readonly></label><label class="stack"><span class="hint">返程日期</span><input value="2026-10-06" readonly></label></div><div class="notice">结构化表单与自然语言入口最终使用同一份 UserPreferences 和规划规则。</div><button class="button primary" data-action="validate-structured">校验并继续</button></div>`;
    const chat = `<div class="chat-area">${messages}</div><div class="composer"><textarea id="conversation-input" aria-label="旅行需求或补充信息" placeholder="描述预算、日期、人数、兴趣与旅行节奏">${state.draftInput}</textarea><div class="composer-footer"><span class="hint">字段来源会区分用户提供、规则推导和未知。</span><button class="button primary" data-action="submit-conversation">${state.phase === 0 ? "发送并解析" : state.phase === 1 ? "提交补充" : "信息已完整"}${icon("arrow")}</button></div></div>`;
    return `<main class="screen" data-screen-label="01 旅行需求与 AI 对话">${heading(1, "先说想去怎样的地方", "AI 只提取你明确表达的信息；人数、精确日期或风格不清楚时会继续追问，不会用默认值替你决定。", `<button class="button secondary" data-action="toggle-structured">${state.structured ? "返回对话输入" : "使用结构化表单"}</button>`)}<div class="two-column"><section class="panel"><div class="panel-header"><div><h2>${state.structured ? "结构化输入" : "偏好澄清对话"}</h2><p>确定性 Mock Parser · 固定日期基准 2026-09-26</p></div>${statusPill(state.phase === 2 ? "completed" : "neutral", state.phase === 2 ? "信息完整" : "收集中")}</div>${state.structured ? structuredForm : chat}</section><aside class="panel summary-sticky"><div class="panel-header"><div><h2>实时偏好摘要</h2><p>缺失字段不会被默认值掩盖</p></div></div><div class="panel-body stack">${preferenceSummary(state.phase < 2)}${state.phase < 2 ? '<div class="notice warning">仍需确认：旅行人数、精确出发日、旅行风格。</div>' : '<div class="notice">字段已通过服务端完整性检查；下一步仍需用户主动确认。</div>'}<div class="split-equal"><button class="button secondary" data-action="reset">重新开始</button><button class="button primary" data-action="continue-confirm" ${state.phase < 2 ? "disabled" : ""}>核对并确认</button></div></div></aside></div></main>`;
  }

  function confirmationScreen() {
    const p = DATA.preferences;
    return `<main class="screen" data-screen-label="02 偏好确认与规划配置">${heading(2, "确认以后，再开始规划", "这里显示实际进入 UserPreferences 的内容。日期采用结束日排除语义：活动安排到返程日前一天，住宿晚数等于日期差。", '<button class="button secondary" data-action="edit-preferences">修改偏好</button>')}<div class="stack"><section class="panel"><div class="panel-header"><div><h2>日期与人数</h2><p>请重点核对推导结果</p></div>${statusPill("completed", "服务端可确认")}</div><div class="panel-body"><div class="date-strip"><div class="date-cell"><span>出发日期</span><strong>${p.startDate}</strong></div><div class="date-cell"><span>返程日期</span><strong>${p.endDate}</strong></div><div class="date-cell"><span>活动范围</span><strong>10/01 — 10/05</strong></div><div class="date-cell"><span>住宿晚数</span><strong>${p.nights} 晚</strong></div></div></div></section><div class="split-equal"><section class="panel"><div class="panel-header"><div><h2>完整偏好</h2><p>来自签名草稿</p></div></div><div class="panel-body">${preferenceSummary(false)}</div></section><section class="panel"><div class="panel-header"><div><h2>选择规划模式</h2><p>每个方案独立使用完整预算上限</p></div></div><div class="panel-body stack"><div class="choice-group"><button class="choice-card ${state.planMode === "single" ? "selected" : ""}" data-mode="single"><strong>单方案规划</strong><span>生成一套完整方案，适合快速查看行程。</span></button><button class="choice-card ${state.planMode === "compare" ? "selected" : ""}" data-mode="compare"><strong>双方案比较</strong><span>生成两个独立目的地方案，再进行四维评价。</span></button></div><div class="notice"><strong>${DATA.meta.label}</strong><br>确认动作只表示客户端提交确认；当前没有身份认证，也不代表预订。</div><div class="split-equal"><button class="button secondary" data-page="0">返回对话</button><button class="button primary" data-action="start-plan">确认并开始${state.planMode === "compare" ? "双方案比较" : "规划"}${icon("arrow")}</button></div></div></section></div></div></main>`;
  }

  function planningScreen() {
    const isError = state.override === "agentError" || state.override === "networkError";
    const errorContent = `<div><div class="recommendation-icon" style="margin:0 auto 20px;background:var(--danger)">${icon("alert")}</div><h2>${state.override === "networkError" ? "无法连接规划服务" : "必要 Agent 执行失败"}</h2><p class="lede" style="margin:0 auto 18px">${state.override === "networkError" ? "网络请求超时。草稿、签名回执与确认状态没有被清空。" : "FlightAgent 返回 agent_execution_error / RuntimeError，系统没有继续生成虚假的完整预算。"}</p><div class="notice error">${state.override === "networkError" ? "连接失败与业务不可行不同；恢复服务后可直接重试。" : "业务状态：FAILED。缺失费用不会按 0 元计入。"}</div><div class="top-actions" style="justify-content:center;margin-top:20px"><button class="button secondary" data-page="1">返回确认</button><button class="button primary" data-action="retry">保留偏好并重试</button></div></div>`;
    const loadingContent = `<div><div class="route-orbit"><div class="orbit-mark">${icon("route")}</div></div><h2>整体规划请求进行中</h2><div class="indeterminate"></div><div class="process-map"><span>目的地候选</span><span>航班 / 酒店 / 天气并行</span><span>天气明确后安排活动</span><span>预算闭环</span>${state.planMode === "compare" ? "<span>独立比较与解释</span>" : ""}</div><p class="hint" style="margin-top:18px">以上是固定执行顺序说明，不代表实时 Agent 状态。</p><button class="button primary" style="margin-top:22px" data-action="complete-plan">查看规划完成示例${icon("arrow")}</button></div>`;
    return `<main class="screen" data-screen-label="03 规划执行与状态反馈">${heading(3, isError ? "这次规划没有完成" : "正在生成可核对的旅行方案", isError ? "错误不会被伪装成成功；已确认偏好仍保留，可以安全重试。" : "后端当前返回整体响应，没有细粒度进度事件。因此这里只显示真实的整体加载状态，不伪造每个 Agent 的实时百分比。") }<section class="panel loading-stage">${isError ? errorContent : loadingContent}</section></main>`;
  }

  function metricRow(label, value) {
    const safe = value == null ? 0 : value;
    return `<div class="metric-row"><span>${label}</span><div class="metric-track" aria-label="${label} ${value == null ? "不可评价" : value}"><i style="width:${safe}%"></i></div><em>${value == null ? "—" : value.toFixed(0)}</em></div>`;
  }

  function planCard(plan) {
    const feasible = plan.status === "completed";
    const score = plan.score == null ? '<div class="score-empty">不显示<br>综合分</div>' : `<div class="score-dial" style="--score-angle:${plan.score * 3.6}deg"><strong>${plan.score.toFixed(0)}</strong></div>`;
    return `<article class="plan-card ${plan.accent}"><div class="plan-card-head"><div>${statusPill(plan.status, feasible ? "完整可行" : "预算不可满足")}<h2>${plan.name}</h2><p>${plan.country} · 独立规划状态</p></div>${score}</div><div class="cost-row"><div class="cost-block"><span>最终总费用</span><strong>${money(plan.cost)}</strong></div><div class="cost-block"><span>预算余量</span><strong class="${plan.remaining < 0 ? "negative" : ""}">${plan.cost == null ? "—" : `${plan.remaining < 0 ? "−" : "+"}${money(Math.abs(plan.remaining))}`}</strong></div></div><div class="metrics">${metricRow("预算匹配", plan.metrics.budget)}${metricRow("兴趣匹配", plan.metrics.interest)}${metricRow("节奏适配", plan.metrics.pace)}${metricRow("天气适宜", plan.metrics.weather)}</div><div class="plan-actions"><button class="button secondary" data-details="${plan.id}">查看详情</button><button class="button primary" data-select="${plan.id}" ${!feasible ? "disabled" : ""}>${feasible ? "选择此方案" : "不可选择"}</button></div></article>`;
  }

  function comparisonScreen() {
    const scenario = DATA.scenarios[state.scenario];
    const tabs = Object.entries(DATA.scenarios).map(([key, value]) => `<button class="scenario-tab ${key === state.scenario ? "active" : ""}" data-scenario="${key}">${value.label}</button>`).join("");
    const evidence = [
      ["费用差异", `${scenario.plans[0].name} 与 ${scenario.plans[1] ? scenario.plans[1].name : "—"} 的最终费用来自各自独立预算明细。`],
      ["兴趣依据", `${scenario.plans[0].name} ${scenario.plans[0].match}；${scenario.plans[1] ? `${scenario.plans[1].name} ${scenario.plans[1].match}` : "仅一套方案"}。休息时段不进入分母。`],
      ["天气依据", `${scenario.plans[0].weather}；${scenario.plans[1] ? scenario.plans[1].weather : "—"}。未知天气不计为零冲突。`],
      ["决策边界", scenario.reason]
    ];
    return `<main class="screen" data-screen-label="04 双方案比较">${heading(4, "比较事实，也解释取舍", "两套方案来自独立 TravelPlanState。前端直接展示后端评价和 ExplanationService 证据，不重新计算分数。", `<div class="scenario-tabs" aria-label="比较状态预览">${tabs}</div>`)}<div class="recommendation-banner ${scenario.tone}"><div class="recommendation-icon">${icon(scenario.decision === "recommended" ? "spark" : scenario.decision === "tie" ? "compare" : "alert")}</div><div><h3>${scenario.headline}</h3><p>${scenario.reason}</p></div>${statusPill(scenario.tone, scenario.decision)}</div><div class="plan-grid">${scenario.plans.map(planCard).join("")}</div><div class="explanation-grid"><section class="panel"><div class="panel-header"><div><h2>可追溯解释</h2><p>由实际字段与候选证据生成</p></div><span class="badge mock">C2 规则模板</span></div><div class="panel-body evidence-list">${evidence.map(([title, text]) => `<div class="evidence-item"><strong>${title}</strong><p>${text}</p></div>`).join("")}</div></section><aside class="panel"><div class="panel-header"><div><h2>权重与局限</h2><p>${DATA.meta.scoringVersion}</p></div></div><div class="panel-body"><div class="weight-row"><span>预算匹配</span><code>30%</code></div><div class="weight-row"><span>兴趣匹配</span><code>30%</code></div><div class="weight-row"><span>节奏适配</span><code>25%</code></div><div class="weight-row"><span>天气适宜</span><code>15%</code></div><div class="notice warning" style="margin-top:16px">${scenario.sensitivity}</div><p class="hint" style="margin-top:14px">权重是版本化产品假设，未经真人偏好研究验证。</p></div></aside></div></main>`;
  }

  function detailsScreen() {
    const detail = DATA.details[state.selectedPlan];
    const b = detail.budget;
    const max = Math.max(b.flight, b.hotel, b.activity);
    const weatherUnavailable = state.override === "weatherUnavailable";
    const days = detail.days.map(day => `<div class="day-card"><div class="day-heading"><h3>${day.date}</h3><span>${weatherUnavailable ? "天气不可用" : day.weather}</span></div>${day.items.map(item => `<div class="timeline-item"><div class="timeline-time">${item.time}</div><div><h3>${item.slot} · ${item.name}</h3><p>${item.meta}</p><p>${item.reason}</p></div><div class="timeline-price">${item.price === 0 ? "免费" : money(item.price)}</div></div>`).join("")}</div>`).join("");
    const itinerary = `<div class="detail-grid"><section class="panel">${days}</section><aside class="stack"><section class="panel"><div class="panel-header"><div><h2>每日天气</h2><p>示例 Mock · 日级数据</p></div></div><div class="panel-body weather-list">${weatherUnavailable ? '<div class="notice warning">没有可展示的逐日天气记录。</div>' : detail.weather.map(w => `<div class="weather-card"><div><strong>${w.date} · ${w.condition}</strong><span>降雨 ${w.rain} · ${w.status}</span></div><div class="weather-value">${w.temp}</div></div>`).join("")}</div></section><section class="panel"><div class="panel-header"><div><h2>交通与住宿</h2><p>候选 ID 可追溯</p></div></div><div class="panel-body stack"><div><span class="hint">去程</span><strong style="display:block">${detail.transport.outbound}</strong></div><div><span class="hint">返程</span><strong style="display:block">${detail.transport.return}</strong></div><div><span class="hint">住宿</span><strong style="display:block">${detail.transport.hotel}</strong></div></div></section></aside></div>`;
    const budget = `<div class="split-equal"><section class="panel"><div class="panel-header"><div><h2>费用明细</h2><p>与同一后端结果对账</p></div></div><div class="panel-body budget-bars">${[["航班", b.flight], ["酒店", b.hotel], ["活动", b.activity]].map(([label, value]) => `<div class="budget-line"><span>${label}</span><div class="metric-track"><i style="width:${value / max * 100}%"></i></div><strong>${money(value)}</strong></div>`).join("")}<div class="date-strip" style="margin-top:12px;grid-template-columns:repeat(2,1fr)"><div class="date-cell"><span>最终总费用</span><strong>${money(b.total)}</strong></div><div class="date-cell"><span>预算上限</span><strong>${money(b.limit)}</strong></div></div></div></section><section class="panel"><div class="panel-header"><div><h2>预算调整历史</h2><p>候选价格没有被修改</p></div></div><div class="panel-body"><div class="notice">初始方案已在预算内，adjustment_round = 0，没有执行降级。</div></div></section></div>`;
    const evidence = `<div class="split-equal"><section class="panel"><div class="panel-header"><div><h2>天气与节奏</h2><p>选择依据来自后端记录</p></div></div><div class="panel-body evidence-list"><div class="evidence-item"><strong>天气硬约束</strong><p>${weatherUnavailable ? "不可评价，已明确降级。" : "实际入选活动均通过天气硬约束；未知属性不会当作雨天安全。"}</p></div><div class="evidence-item"><strong>轻松节奏</strong><p>允许明确休息时段；预算优化不会重新填入收费活动。</p></div></div></section><section class="panel"><div class="panel-header"><div><h2>数据来源</h2><p>可追溯但不是实时数据</p></div></div><div class="panel-body stack"><div class="mock-stamp">${DATA.meta.providerVersion}<br>${DATA.meta.scoringVersion}</div><p class="hint">${DATA.meta.notice}</p></div></section></div>`;
    const body = state.detailTab === "itinerary" ? itinerary : state.detailTab === "budget" ? budget : evidence;
    return `<main class="screen" data-screen-label="05 每日行程及预算详情">${heading(5, detail.title, `当前为${state.planMode === "compare" ? "双方案中的独立详情" : "单方案详情"}。选择方案仅用于产品内查看，不代表真实航班、酒店或活动预订。`, statusPill(weatherUnavailable ? "warning" : "completed", weatherUnavailable ? "天气不可用 · 基础规划" : "COMPLETED · 天气可用"))}<div class="detail-toolbar"><div class="segmented" aria-label="切换方案"><button class="${state.selectedPlan === "sea" ? "active" : ""}" data-plan="sea">海城</button><button class="${state.selectedPlan === "mountain" ? "active" : ""}" data-plan="mountain">山城</button></div><div class="segmented" aria-label="详情视图"><button class="${state.detailTab === "itinerary" ? "active" : ""}" data-tab="itinerary">每日行程</button><button class="${state.detailTab === "budget" ? "active" : ""}" data-tab="budget">预算详情</button><button class="${state.detailTab === "evidence" ? "active" : ""}" data-tab="evidence">决策依据</button></div></div>${weatherUnavailable ? '<div class="notice warning" style="margin-bottom:18px"><strong>天气数据不可用。</strong> 当前采用基础活动规划，不会把未知天气显示为晴天，也不声明活动已经天气验证。</div>' : ""}${body}</main>`;
  }

  const stateOptions = [
    ["normal", "正常流程", "恢复标准推荐演示。"], ["tie", "并列", "两套综合评价接近，不强行推荐。"],
    ["onlyFeasible", "仅一套可行", "另一套保留预算不可满足状态。"], ["noRecommendation", "无法推荐", "天气或偏好证据不足。"],
    ["weatherUnavailable", "天气不可用", "采用基础规划，不显示晴天。"], ["budgetInfeasible", "预算不可满足", "当前搜索策略未找到预算内方案。"],
    ["agentError", "Agent 故障", "必要 Agent 异常，状态为 FAILED。"], ["networkError", "网络错误", "保留偏好并允许重试。"]
  ];

  function drawer() {
    if (!state.drawer) return "";
    return `<div class="state-drawer" role="dialog" aria-modal="true" aria-label="状态预览"><div class="drawer-panel"><div class="drawer-header"><div><p class="eyebrow">Prototype states</p><h2>异常与特殊状态</h2><p class="lede">每个状态都有明确文案与下一步，不只依赖颜色。</p></div><button class="icon-button" aria-label="关闭" data-action="close-states">${icon("close")}</button></div>${stateOptions.map(([key, title, desc]) => `<button class="state-option ${state.override === key ? "active" : ""}" data-state="${key}"><strong>${title}</strong><span>${desc}</span></button>`).join("")}</div></div>`;
  }

  function modal() {
    if (!state.editModal) return "";
    return `<div class="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="edit-title"><div class="modal"><p class="eyebrow">Preference update</p><h2 id="edit-title">修改偏好会使旧结果失效</h2><p class="lede">任何必要字段变化都需要经过已有澄清逻辑重新签名和确认；不能继续复用旧评分。</p><label class="stack" style="margin-top:18px"><span class="hint">示例：总预算</span><input value="15000"></label><div class="modal-actions"><button class="button secondary" data-action="cancel-edit">取消</button><button class="button primary" data-action="confirm-edit">确认修改并重新验证</button></div></div></div>`;
  }

  function render() {
    localStorage.setItem("d1-page", String(state.page));
    const screens = [conversationScreen, confirmationScreen, planningScreen, comparisonScreen, detailsScreen];
    root.innerHTML = `<div class="app-shell">${sideRail()}<div class="workspace">${topbar()}${screens[state.page]()}</div>${drawer()}${modal()}${state.toast ? `<div class="toast" role="status">${state.toast}</div>` : ""}</div>`;
    window.scrollTo(0, 0);
  }

  function toast(message) {
    state.toast = message;
    render();
    window.setTimeout(() => { state.toast = ""; render(); }, 3000);
  }

  function chooseState(key) {
    state.override = key;
    state.drawer = false;
    if (["tie", "onlyFeasible", "noRecommendation"].includes(key)) { state.scenario = key; state.page = 3; }
    else if (key === "weatherUnavailable") { state.scenario = "noRecommendation"; state.selectedPlan = "sea"; state.page = 4; }
    else if (key === "budgetInfeasible") { state.scenario = "onlyFeasible"; state.page = 3; }
    else if (key === "agentError" || key === "networkError") state.page = 2;
    else { state.scenario = "recommended"; state.page = 0; }
    render();
  }

  root.addEventListener("input", (event) => {
    if (event.target.id === "conversation-input") state.draftInput = event.target.value;
  });

  root.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button || button.disabled) return;
    if (button.dataset.page != null) { state.page = Number(button.dataset.page); render(); return; }
    if (button.dataset.mode) { state.planMode = button.dataset.mode; render(); return; }
    if (button.dataset.scenario) { state.scenario = button.dataset.scenario; state.override = button.dataset.scenario; render(); return; }
    if (button.dataset.details) { state.selectedPlan = button.dataset.details; state.page = 4; render(); return; }
    if (button.dataset.select) { state.selectedPlan = button.dataset.select; state.page = 4; toast(`已选择${DATA.details[state.selectedPlan].title.split(" · ")[0]}用于进一步查看；这不是预订。`); return; }
    if (button.dataset.plan) { state.selectedPlan = button.dataset.plan; render(); return; }
    if (button.dataset.tab) { state.detailTab = button.dataset.tab; render(); return; }
    if (button.dataset.state) { chooseState(button.dataset.state); return; }
    const action = button.dataset.action;
    if (action === "open-states") state.drawer = true;
    else if (action === "close-states") state.drawer = false;
    else if (action === "toggle-structured") state.structured = !state.structured;
    else if (action === "validate-structured") { state.phase = 2; state.structured = false; toast("结构化示例已完成校验。"); return; }
    else if (action === "submit-conversation") {
      if (!state.draftInput.trim()) { toast("请先输入旅行需求或补充信息。"); return; }
      if (state.phase === 0) { state.phase = 1; state.draftInput = DATA.conversation.answer; }
      else if (state.phase === 1) { state.phase = 2; state.draftInput = ""; }
    }
    else if (action === "reset") { state.phase = 0; state.draftInput = DATA.conversation.initial; toast("已清除本次临时草稿与对话历史。"); return; }
    else if (action === "continue-confirm") state.page = 1;
    else if (action === "edit-preferences") state.editModal = true;
    else if (action === "cancel-edit") state.editModal = false;
    else if (action === "confirm-edit") { state.editModal = false; state.phase = 2; state.scenario = "recommended"; state.override = "normal"; state.page = 0; toast("旧规划与评分已失效；请重新确认偏好。"); return; }
    else if (action === "start-plan") { state.override = "normal"; state.page = 2; }
    else if (action === "complete-plan") state.page = state.planMode === "compare" ? 3 : 4;
    else if (action === "retry") { state.override = "normal"; toast("已保留确认偏好，正在重新发起整体请求。"); return; }
    render();
  });

  render();
}());
