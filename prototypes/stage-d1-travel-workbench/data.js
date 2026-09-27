window.PROTOTYPE_DATA = {
  meta: {
    label: "示例 · 确定性 Mock",
    source: "C3 固定 Fixture",
    providerVersion: "c1-multi-plan-fixture-v1",
    scoringVersion: "plan-scoring-v1",
    notice: "所有价格、天气、活动、时长与评分均为示例 Mock 数据，不是实时库存、预报或预订结果。"
  },
  preferences: {
    departure: "上海",
    startDate: "2026-10-01",
    endDate: "2026-10-06",
    activityRange: "2026-10-01 — 2026-10-05",
    nights: 5,
    travelers: 2,
    budget: 15000,
    style: "舒适",
    pace: "轻松",
    interests: ["摄影", "美食"],
    parser: "mock-preference-parser-v1"
  },
  conversation: {
    initial: "国庆想和朋友从上海出去玩五天，预算一万五，不想太累，喜欢拍照和吃东西。",
    question: "还需要确认两项信息：一共几位旅行者？具体哪天出发？",
    answer: "我们两个人，2026 年 10 月 1 日出发，舒适游。"
  },
  scenarios: {
    recommended: {
      label: "正常推荐",
      decision: "recommended",
      tone: "positive",
      headline: "山城更符合已声明的文化兴趣",
      reason: "在 plan-scoring-v1 的共同可评价指标与已声明权重下，山城综合分高 4.0 分；这是条件化规则结论，不是绝对排序。",
      sensitivity: "替代权重下结论可能变化，应同时查看费用取舍。",
      plans: [
        {id:"sea", name:"海城", country:"Fixtureland", status:"completed", cost:2100, remaining:1500, score:90, metrics:{budget:100, interest:66.67, pace:100, weather:100}, match:"4 / 6", weather:"6 / 6", accent:"sea"},
        {id:"mountain", name:"山城", country:"Fixtureland", status:"completed", cost:3600, remaining:0, score:94, metrics:{budget:80, interest:100, pace:100, weather:100}, match:"6 / 6", weather:"6 / 6", accent:"mountain"}
      ]
    },
    tie: {
      label: "并列",
      decision: "tie",
      tone: "neutral",
      headline: "两套方案没有明显差异",
      reason: "综合分差 0.0000 未超过并列阈值 2.0000，系统不会强行指定胜出方案。",
      sensitivity: "当前结论在已声明替代权重下保持并列。",
      plans: [
        {id:"sea", name:"海城", country:"Fixtureland", status:"completed", cost:2100, remaining:2900, score:100, metrics:{budget:100, interest:100, pace:100, weather:100}, match:"6 / 6", weather:"6 / 6", accent:"sea"},
        {id:"mountain", name:"山城", country:"Fixtureland", status:"completed", cost:3600, remaining:1400, score:100, metrics:{budget:100, interest:100, pace:100, weather:100}, match:"6 / 6", weather:"6 / 6", accent:"mountain"}
      ]
    },
    onlyFeasible: {
      label: "仅一套可行",
      decision: "only_feasible",
      tone: "warning",
      headline: "仅海城满足当前硬约束",
      reason: "海城为完整可行方案；山城保留 BUDGET_INFEASIBLE，不会被当作普通低分方案。",
      sensitivity: "硬约束优先于权重，当前不计算可比综合分。",
      plans: [
        {id:"sea", name:"海城", country:"Fixtureland", status:"completed", cost:2100, remaining:400, score:null, metrics:{budget:96, interest:100, pace:100, weather:100}, match:"6 / 6", weather:"6 / 6", accent:"sea"},
        {id:"mountain", name:"山城", country:"Fixtureland", status:"budget_infeasible", cost:2660, remaining:-160, score:null, metrics:{budget:null, interest:null, pace:null, weather:null}, match:"—", weather:"—", accent:"mountain"}
      ]
    },
    noRecommendation: {
      label: "无法推荐",
      decision: "no_recommendation",
      tone: "muted",
      headline: "必要证据不足，暂不做综合推荐",
      reason: "两套规划已完成，但天气不可用，且用户未声明兴趣与 pace；系统只展示可验证事实。",
      sensitivity: "缺失指标不会自动记为 0，也不会对两套方案使用不同有效权重。",
      plans: [
        {id:"sea", name:"海城", country:"Fixtureland", status:"completed", cost:2100, remaining:2900, score:null, metrics:{budget:100, interest:null, pace:null, weather:null}, match:"未声明", weather:"不可用", accent:"sea"},
        {id:"mountain", name:"山城", country:"Fixtureland", status:"completed", cost:3600, remaining:1400, score:null, metrics:{budget:100, interest:null, pace:null, weather:null}, match:"未声明", weather:"不可用", accent:"mountain"}
      ]
    }
  },
  details: {
    sea: {
      title:"海城 · 2 日示例行程", weather:[{date:"10 月 1 日", condition:"晴间多云", rain:"20%", temp:"24℃", status:"可用"},{date:"10 月 2 日", condition:"小雨", rain:"65%", temp:"21℃", status:"可用"}],
      days:[
        {date:"10 月 1 日", weather:"晴间多云 · 24℃", items:[{time:"09:00",slot:"上午",name:"滨海文化街区",meta:"2h · 低强度 · 户外",price:100,reason:"文化兴趣匹配；低降雨风险。"},{time:"14:00",slot:"下午",name:"城市设计馆",meta:"2h · 低强度 · 室内",price:100,reason:"天气适配且节奏合规。"},{time:"19:00",slot:"晚上",name:"港湾夜游",meta:"2h · 低强度 · 混合",price:100,reason:"天气适配评分通过。"}]},
        {date:"10 月 2 日", weather:"小雨 · 21℃", items:[{time:"09:30",slot:"上午",name:"海洋历史馆",meta:"2h · 低强度 · 室内",price:100,reason:"雨天优先室内；文化兴趣匹配。"},{time:"14:30",slot:"下午",name:"独立摄影展",meta:"2h · 低强度 · 室内",price:100,reason:"摄影兴趣匹配；天气硬约束通过。"},{time:"晚上",slot:"休息",name:"留白休息时段",meta:"relaxed · 0h",price:0,reason:"轻松节奏保留恢复时间，不是虚构活动。"}]}
      ],
      transport:{outbound:"MU 518 · 08:20",return:"MU 519 · 18:40",hotel:"潮汐设计酒店 · 5 晚"},
      budget:{flight:1000,hotel:500,activity:600,total:2100,limit:3600,rounds:0}
    },
    mountain: {
      title:"山城 · 2 日示例行程", weather:[{date:"10 月 1 日", condition:"多云", rain:"15%", temp:"20℃", status:"可用"},{date:"10 月 2 日", condition:"阵雨", rain:"70%", temp:"18℃", status:"可用"}],
      days:[
        {date:"10 月 1 日", weather:"多云 · 20℃", items:[{time:"09:00",slot:"上午",name:"山地博物馆",meta:"2h · 低强度 · 室内",price:100,reason:"文化兴趣匹配；天气适配。"},{time:"14:00",slot:"下午",name:"老城工坊",meta:"2h · 低强度 · 室内",price:100,reason:"文化兴趣匹配；节奏合规。"},{time:"19:00",slot:"晚上",name:"地方剧场",meta:"2h · 低强度 · 室内",price:100,reason:"兴趣与天气证据完整。"}]},
        {date:"10 月 2 日", weather:"阵雨 · 18℃", items:[{time:"09:30",slot:"上午",name:"民俗档案馆",meta:"2h · 低强度 · 室内",price:100,reason:"阵雨下排除高敏感户外候选。"},{time:"14:30",slot:"下午",name:"山城美术馆",meta:"2h · 低强度 · 室内",price:100,reason:"文化兴趣匹配；天气硬约束通过。"},{time:"19:00",slot:"晚上",name:"传统音乐会",meta:"2h · 低强度 · 室内",price:100,reason:"天气适宜度 100。"}]}
      ],
      transport:{outbound:"CA 312 · 07:55",return:"CA 313 · 20:10",hotel:"云阶文化酒店 · 5 晚"},
      budget:{flight:1600,hotel:1400,activity:600,total:3600,limit:3600,rounds:0}
    }
  }
};
