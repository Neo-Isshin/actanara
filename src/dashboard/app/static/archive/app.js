(() => {
  'use strict';

  const ROUTES = new Set(['home', 'console', 'assets', 'agents', 'memory', 'usage', 'reports', 'foundation', 'operations', 'rag', 'settings']);
  const routeTitles = {
    home: '今日概览',
    console: '每日决策台',
    assets: 'AI 资产',
    agents: 'Agent 数据源',
    memory: '记忆搜索',
    usage: '历史用量',
    reports: '日记与报告',
    foundation: '数据与维护',
    operations: '运行与任务',
    rag: 'RAG 与索引',
    settings: '系统设置'
  };

  // Charts are populated only from local API responses.  Empty initial
  // collections prevent a stale prototype snapshot from appearing while the
  // runtime is loading or when a source is unavailable.
  const evidenceData = [];
  const reportsDaily = [];
  const usage30d = [];
  const hourlyUsage = [];

  const memoryRecords = [
    {
      id: 'memory-identity',
      type: 'diary',
      source: '技术日记',
      date: '2026-07-18',
      title: '通用 Agent 框架：持久化员工身份，而不是模型进程',
      text: '核心原则是持久化“员工身份”而非模型进程。事实来源由事件流、状态库和产物库共同组成，确定性服务负责状态迁移。',
      citation: '智慧沉淀与基建审计 · 架构原则'
    },
    {
      id: 'memory-install',
      type: 'diary',
      source: '学习日记',
      date: '2026-07-18',
      title: '不完整数据目录需要先做完整性预检',
      text: '安装前应校验 runtime manifest、source 与虚拟环境三要素；对不完整目录先备份，再进入可恢复的重建流程。',
      citation: '黄金教训 · 安装可靠性'
    },
    {
      id: 'memory-native',
      type: 'native',
      source: 'Memory policy',
      date: '2026-07-29',
      title: 'Codex 与 Claude Code 原生记忆纳入本地索引',
      text: '原生记忆与 instructions 已授权作为 Local FTS 来源。轻量检索保留全文、模糊匹配、元数据过滤与可定位引用。',
      citation: 'Native memory policy · local index'
    },
    {
      id: 'memory-task',
      type: 'task',
      source: 'Nova-Task event',
      date: '2026-08-08',
      title: '任务图谱事件已进入记忆来源集合',
      text: 'Nova-Task work graph events 作为独立 source set 被索引，使任务进展能够与日记和 Agent 记忆交叉检索。',
      citation: 'nova-task-work-graph-events'
    }
  ];

  const globalCatalog = [
    { type: 'diary', label: '日记', title: '2026-07-18 智慧沉淀与基建审计', meta: '3 documents · 64 sections', route: 'reports' },
    { type: 'memory', label: '记忆', title: '通用 Agent 框架架构原则', meta: 'Local FTS · citation ready', detail: 'memory-identity' },
    { type: 'skills', label: 'Skill', title: 'unified context', meta: 'custom · reusable capability', detail: 'skills-unified' },
    { type: 'agents', label: 'Agent', title: 'Codex runtime source', meta: '489 artifacts · native memory allowed', agent: 'Codex' },
    { type: 'agents', label: 'Agent', title: 'Antigravity runtime source', meta: 'variants merged · local partial', agent: 'Antigravity' },
    { type: 'memory', label: '索引', title: 'Local FTS memory catalog', meta: '2,019 documents · 81 sources', route: 'memory' }
  ];

  const agents = {
    'Codex': {
      monogram: 'CX', tone: 'violet', status: '今日活跃', summary: '当前最活跃的本地来源；结构化贡献与原生记忆均可用。',
      stats: [['源产物','489'],['Foundation Sessions','34'],['技能实例','8']],
      details: [['检测状态','已在本机检测到'],['最近活动','2026-08-09 00:19'],['原生记忆','已授权'],['Instructions','纳入索引'],['用量协议','完整'],['数据形态','rollout JSONL']],
      notes: 'Token 历史保留在“历史用量”页面，不作为此来源的资产评分。'
    },
    'OpenClaw': {
      monogram: 'OC', tone: 'rust', status: '最近有记录', summary: '源产物数量最多，同时拥有较大的跨工具技能目录。',
      stats: [['源产物','879'],['Foundation Sessions','218'],['技能实例','92']],
      details: [['检测状态','已在本机检测到'],['最近活动','2026-08-08 21:04'],['原生记忆','未纳入'],['Agent 身份','6 个可见身份'],['用量协议','完整'],['数据形态','session JSONL']],
      notes: '来源规模较大；需要继续区分原始 Session 与可复用结构化成果。'
    },
    'Claude Code': {
      monogram: 'CL', tone: 'teal', status: '已有本地记录', summary: '已识别本地项目记录、原生记忆和 Skills。',
      stats: [['源产物','109'],['Foundation Sessions','11'],['技能实例','19']],
      details: [['检测状态','已在本机检测到'],['最近活动','2026-08-05'],['原生记忆','已授权'],['Instructions','纳入索引'],['用量协议','完整'],['数据形态','session JSONL']],
      notes: '原生记忆只在用户授权范围内进入 Local FTS。'
    },
    'Gemini CLI': {
      monogram: 'GM', tone: 'ochre', status: '已有本地记录', summary: '已识别本地项目配置与历史 Session。',
      stats: [['源产物','9'],['Sessions','8'],['活跃日','19']],
      details: [['检测状态','已在本机检测到'],['最近活动','2026-05-23'],['原生记忆','未启用'],['用量协议','完整'],['数据形态','session JSON'],['Token 可用','是']],
      notes: '当前没有近期活动，但历史记录仍可查看。'
    },
    'Hermes': {
      monogram: 'HE', tone: 'green', status: '已有本地记录', summary: '主要可用数据是可复用 Skill 目录。',
      stats: [['Inventory','1'],['Sessions','4'],['技能实例','243']],
      details: [['检测状态','已在本机检测到'],['最近活动','2026-08-06'],['原生记忆','未启用'],['用量协议','完整'],['数据形态','本地清单'],['技能覆盖','最大']],
      notes: '技能实例数量不等于独立技能数量；跨工具安装会重复计数。'
    },
    'Antigravity': {
      monogram: 'AG', tone: 'plum', status: '本地部分数据', summary: '多个 variant 在呈现层合并为一个工具来源。',
      stats: [['Inventory','1'],['Sessions','4'],['Variants','Merged']],
      details: [['检测状态','已在本机检测到'],['最近活动','2026-08-05'],['Variant 呈现','统一合并'],['用量协议','本地部分数据'],['Token 可用','部分'],['API 请求','不需要']],
      notes: 'CLI、IDE 或 App 变体仅作为 provenance，不拆成多个顶层卡片。'
    },
    'OpenCode': {
      monogram: 'OP', tone: 'blue', status: '已识别', summary: '从本地 SQLite 与 runtime inventory 获取可用信息。',
      stats: [['Inventory','1'],['Sessions','2'],['消息','2']],
      details: [['检测状态','已在本机检测到'],['最近活动','2026-06-11'],['原生记忆','未启用'],['用量协议','完整'],['数据形态','SQLite'],['Token 可用','少量']],
      notes: '当前样本较少，页面明确标注数据稀疏。'
    },
    'Cursor': {
      monogram: 'CR', tone: 'neutral', status: '仅 Session', summary: '只展示本地可获得的信息，不调用 Cursor API。',
      stats: [['Inventory','1'],['Sessions','5'],['Token','N/A']],
      details: [['检测状态','已在本机检测到'],['最近活动','2026-06-12'],['本地 Session','可用'],['API 请求','未使用'],['用量协议','不可用'],['Token 可用','否']],
      notes: '在获得可靠本地或官方数据前，不推断 Token 消耗。'
    },
    'Other sources': {
      monogram: '+', tone: 'blue', status: '聚合视图', summary: 'Cron、Gemini CLI、Hermes、OpenCode、Antigravity 与 Cursor 的聚合来源。',
      stats: [['源产物','39'],['来源','6'],['Inventory','3']],
      details: [['Cron JSONL','26'],['Gemini JSON','9'],['SQLite sessions','1'],['Local inventory','3'],['解析错误','0'],['覆盖状态','Mixed']],
      notes: '点击 Agent 卡片可以查看该工具提供了哪些本地数据。'
    }
  };

  const reportContent = {
    narrative: {
      kicker: 'NARRATIVE DIARY · 21 SECTIONS',
      title: '系统演进与工作脉络',
      lede: '这一天完成了 Actanara 的品牌与运行时迁移，也把“持续存在的 Agent 员工”从想法收敛为可审计的工作流内核。',
      sections: [
        ['当天发生了什么', '旧系统完成退役，新运行时开始监听本地 Dashboard。安装、迁移、品牌与发布流程被组织为同一条可追溯事件链。'],
        ['生成的内容', '三类日记、结构化章节、基础设施实体和事件被写入 Foundation；重要架构决策进入可检索记忆。'],
        ['下一步', '缩短日记数据的更新延迟，并让更多 Runtime 的本地信息进入统一来源目录。']
      ],
      provenance: ['narrative · ready', '21 parsed sections', 'Foundation diary document']
    },
    technical: {
      kicker: 'TECHNICAL DIARY · 26 SECTIONS',
      title: '智慧沉淀与基建审计',
      lede: '事实来源被明确为事件流、状态库与产物库。Agent runtime 可以变化，但身份、状态和审计结论必须持续存在。',
      sections: [
        ['合同驱动的工作流内核', 'Worker 提交候选，审计者发布结论，确定性服务负责状态迁移。模型调用被抽象为能力档位，而不是绑定单一供应商。'],
        ['数据边界', '结构化结果必须保留来源、日期、工具与可验证引用；消费数据只能解释历史活动，不能替代知识资产。'],
        ['基础设施变更', 'Dashboard、LaunchAgent、仓库与数据目录的状态变化被整理为实体与事件，便于后续审计。']
      ],
      provenance: ['technical · ready', '26 parsed sections', 'content hash verified']
    },
    learning: {
      kicker: 'LEARNING DIARY · 17 SECTIONS',
      title: '黄金教训与可复用建议',
      lede: '把一次性的故障处理转化为可复用规则：发布门禁需要契约，安装目录需要完整性预检，危险迁移需要可恢复边界。',
      sections: [
        ['发布门禁漂移', '跨模块硬编码字符串会让重命名变成发布阻断。应将关键入口纳入单元测试契约。'],
        ['不完整安装目录', '仅检查路径存在不足以判断安装健康。应校验 runtime manifest、source 与虚拟环境，并先备份再重建。'],
        ['机械替换风险', '品牌重命名必须区分产品标识、测试数据、用户内容与文档引用，避免语义污染。']
      ],
      provenance: ['learning · ready', '17 parsed sections', 'lessons indexed locally']
    },
    period: {
      kicker: 'PERIOD PROJECTION · 08-01 — 08-08',
      title: '八月第一周资产摘要',
      lede: 'Foundation 已生成周期资产摘要：1,406 个来源进入缓存，39 条工作区归属记录完成解析，最近一次任务没有错误。',
      sections: [
        ['新增与覆盖', '周期报告持续生成；8 种 runtime 均已检测，其中 Cursor 只使用本地 Session 信息，Antigravity variants 在展示层合并。'],
        ['搜索状态', 'Local FTS 仍可搜索 2,019 篇文档，但索引最后更新于 07-29，需要重新同步。nova-RAG 尚未启用。'],
        ['维护提示', 'AI Assets 数据已生成；Period Assets 与 Period Page 尚未请求，当前只完成 1/3 个必需步骤。']
      ],
      provenance: ['custom period · ready', '6 projected views', 'Job #694 completed']
    }
  };

  // The restored prototype carried demonstration search rows.  The production
  // preview starts empty and only adds records returned by local APIs.
  memoryRecords.length = 0;
  globalCatalog.length = 0;

  const state = {
    route: 'home',
    assetFilter: 'all',
    archive: { bootstrap: null, assets: null, runs: null, activity: null, memory: null, diaries: null, errors: [] },
    diaryCache: new Map(),
    memorySource: 'all',
    globalFilter: 'all',
    calendar: { year: 2026, month: 6, selected: '2026-07-18' },
    reportTab: 'narrative',
    toastTimer: null,
    syncTimer: null,
    previousFocus: null
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
  const formatNumber = (value) => Number(value || 0).toLocaleString('en-US');

  const STATUS_LABELS = {
    ready: '正常', empty: '暂无数据', degraded: '部分可用', unsupported: '暂不支持',
    stale: '需要更新', error: '读取失败', loading: '加载中', available: '可用',
    'local-partial': '本地部分可用', partial: '部分可用', missing: '缺少数据'
  };
  const TYPE_LABELS = { experience: '经验', practice: '实践', skill: 'Skill' };
  const TYPE_TAGS = { experience: 'rust-tag', practice: 'teal-tag', skill: 'violet-tag' };
  const ASSET_STATUS_LABELS = {
    verified: '已验证', documented: '已记录', draft: '草稿', active: '已启用',
    ready: '待审核', incomplete: '不完整', unknown: '状态未知'
  };

  function numeric(value) {
    if (typeof value === 'boolean' || value === null || value === '' || value === undefined) return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
  }

  function displayNumber(value, compact = false) {
    const parsed = numeric(value);
    if (parsed === null) return '—';
    if (!compact) return parsed.toLocaleString('en-US');
    return new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: parsed >= 1e9 ? 2 : 1 }).format(parsed);
  }

  function put(id, value) {
    const element = document.getElementById(id);
    if (element) element.textContent = value;
  }

  function shortDate(value) {
    if (!value) return '—';
    const match = String(value).match(/^(\d{4})-(\d{2})-(\d{2})/);
    return match ? `${match[2]}-${match[3]}` : String(value).slice(0, 10);
  }

  function timestamp(value) {
    const parsed = value ? new Date(value) : null;
    return parsed && !Number.isNaN(parsed.getTime()) ? parsed : null;
  }

  function freshnessText(value) {
    const source = value?.skillPass || value?.foundation || value;
    const date = timestamp(source?.generatedAt);
    if (!date) return '时间未知';
    const age = numeric(source?.ageSeconds);
    if (age !== null && age < 3600) return `${Math.max(1, Math.round(age / 60))} 分钟前`;
    if (age !== null && age < 86400) return `${Math.round(age / 3600)} 小时前`;
    return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
  }

  function assetStatusLabel(value) {
    const normalized = String(value || 'unknown').toLowerCase();
    return ASSET_STATUS_LABELS[normalized] || value || ASSET_STATUS_LABELS.unknown;
  }

  function agentStatusLabel(value) {
    const normalized = String(value || '').toLowerCase();
    if (normalized === 'available' || normalized === 'detected') return '本地数据可用';
    if (normalized === 'local-partial' || normalized === 'partial') return '只有部分本地数据';
    if (normalized === 'unavailable') return '本地数据不可用';
    return value || '状态未知';
  }

  function setArchiveState(status, title, detail, canRetry = false) {
    const panel = $('#archiveState');
    if (!panel) return;
    const normalized = STATUS_LABELS[status] ? status : 'degraded';
    panel.className = `archive-state ${normalized}`;
    panel.querySelector(':scope > div > b').textContent = title;
    panel.querySelector(':scope > div > span').textContent = detail;
    const retry = panel.querySelector('[data-action="retry-archive"]');
    if (retry) retry.hidden = !canRetry;
    const liveLabels = {
      ready: '本地数据已连接',
      loading: '正在连接本地数据',
      empty: '本地数据已连接',
      stale: '本地数据已连接（需要更新）',
      degraded: '部分数据读取失败',
      unsupported: '部分数据暂不支持',
      error: '本地数据读取失败'
    };
    put('archiveLiveLabel', liveLabels[normalized] || liveLabels.degraded);
    $('#archiveLiveDot')?.classList.toggle('warn', !['ready', 'loading'].includes(normalized));
  }

  function setStatusPill(element, status) {
    if (!element) return;
    const normalized = STATUS_LABELS[status] ? status : 'unsupported';
    element.textContent = STATUS_LABELS[normalized];
    element.className = `status-pill ${normalized === 'ready' ? 'good' : ['stale', 'degraded'].includes(normalized) ? 'warning' : ''}`.trim();
  }

  function initializeArchiveDate() {
    const now = new Date();
    const dateLabel = new Intl.DateTimeFormat('zh-CN', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' }).format(now);
    put('archiveTodayLabel', `${dateLabel} · 本地数据`);
    put('mobileArchiveDate', `${String(now.getMonth() + 1).padStart(2, '0')}.${String(now.getDate()).padStart(2, '0')}.${now.getFullYear()}`);
    put('archiveVolume', `第 ${String(Math.floor((now - new Date(now.getFullYear(), 0, 0)) / 86400000))} 天`);
    put('shareDate', new Intl.DateTimeFormat('zh-CN', { day: 'numeric', month: 'long', year: 'numeric' }).format(now));
  }

  function hydrateQuote(quote) {
    const fallback = [
      ['把今天的工作，变成明天可以复用的能力。', 'ACTANARA · LIVING ARCHIVE'],
      ['经验记录因果，实践记录验证过程，Skill 封装可调用的方法。', 'ACTANARA · SKILL PASS'],
      ['Token 消耗记录历史，经验、实践和 Skill 才能被再次复用。', 'ACTANARA · DAILY NOTE']
    ];
    const selected = quote?.text ? [quote.text, quote.source || quote.author || '今日摘录'] : fallback[new Date().getDate() % fallback.length];
    put('dailyQuoteText', selected[0]);
    put('dailyQuoteAuthor', selected[1]);
    const shareQuote = $('#shareQuote');
    if (shareQuote) shareQuote.textContent = `“${selected[0]}”`;
  }

  function inventoryCount(bootstrap, name) {
    return numeric(bootstrap?.inventory?.[name]?.count);
  }

  function hydrateBootstrap(bootstrap) {
    if (!bootstrap) return;
    const summary = bootstrap.summary || {};
    const experience = numeric(summary.experienceCount);
    const practice = numeric(summary.practiceCount);
    const skill = numeric(summary.skillCount);
    const draft = numeric(summary.draftSkillCount);
    const active = numeric(summary.activeSkillCount);
    const all = numeric(summary.assetCount);
    const memory = inventoryCount(bootstrap, 'memory');
    const reports = inventoryCount(bootstrap, 'reports');
    const diary = inventoryCount(bootstrap, 'diary');
    const infra = inventoryCount(bootstrap, 'infrastructure');
    [['homeExperienceCount', experience], ['assetExperienceCount', experience], ['formationExperienceCount', experience], ['pipelineExperienceCount', experience], ['shareExperienceCount', experience],
      ['homePracticeCount', practice], ['assetPracticeCount', practice], ['formationPracticeCount', practice], ['pipelinePracticeCount', practice], ['sharePracticeCount', practice],
      ['homeSkillCount', skill], ['assetSkillCount', skill], ['formationSkillCount', skill], ['shareSkillCount', skill],
      ['assetAllCount', all], ['pipelineDraftCount', draft], ['pipelineActiveCount', active], ['activeSkillCount', active], ['activeSkillLegend', active], ['draftSkillLegend', draft],
      ['supportingReportCount', reports], ['supportingMemoryCount', memory], ['supportingDiaryCount', diary], ['supportingInfraCount', infra], ['homeReportCount', reports], ['homeReportLegend', reports], ['homeDiaryLegend', diary], ['homeMemoryLegend', memory]
    ].forEach(([id, value]) => put(id, displayNumber(value)));
    put('homeSkillMeta', `草稿 ${displayNumber(draft)} · 已启用 ${displayNumber(active)}`);
    put('assetSkillBreakdown', `草稿 ${displayNumber(draft)} · 已启用 ${displayNumber(active)}`);
    put('skillStateTotal', `${displayNumber(skill)} 个草稿`);
    put('navAssetCount', all === null ? STATUS_LABELS[bootstrap.status] || '未知' : `${displayNumber(all)} 项`);
    put('navAgentCount', `${displayNumber((bootstrap.agents || []).length)} 个`);
    put('navMemoryCount', displayNumber(memory));
    put('navFoundationStatus', STATUS_LABELS[bootstrap.status] || '未知');
    put('reportDiaryDays', displayNumber(diary));
    put('reportDocumentCount', displayNumber(reports));
    put('reportAssetCount', displayNumber(all));
    put('archiveAssetFreshness', `AI 资产 · 更新于 ${freshnessText(bootstrap.dataFreshness)}`);
    const totalState = (active || 0) + (draft || 0);
    const share = totalState ? ((active || 0) / totalState) * 100 : 0;
    $('#skillStateDonut')?.style.setProperty('--active-share', `${share}%`);
    setStatusPill($('#assetCatalogStatus'), bootstrap.status);
    hydrateQuote(bootstrap.dailyQuote || bootstrap.quote);
    $('#homeAssetMetrics')?.setAttribute('aria-busy', 'false');
    $('#assetTypeMetrics')?.setAttribute('aria-busy', 'false');
    hydrateFoundation(bootstrap, state.archive.memory);
  }

  function hydrateFoundation(bootstrap, memory) {
    if (!bootstrap) return;
    const inventory = bootstrap.inventory || {};
    const documents = numeric(inventory.reports?.count);
    const infrastructure = numeric(inventory.infrastructure?.count);
    const runtimes = numeric(inventory.agents?.count);
    const installedSkills = numeric(bootstrap.summary?.installedSkillInstanceCount ?? inventory.skills?.count);
    const assetTotal = numeric(bootstrap.summary?.assetCount);
    put('foundationDocumentCount', displayNumber(documents));
    put('foundationInfraCount', displayNumber(infrastructure));
    put('foundationRuntimeCount', displayNumber(runtimes));
    put('foundationInstalledSkills', displayNumber(installedSkills));
    put('foundationAssetTotal', displayNumber(assetTotal));
    const status = bootstrap.status || 'unsupported';
    put('foundationSeal', status === 'ready' ? '正常' : status === 'stale' ? '需更新' : status === 'degraded' ? '检查中' : '无数据');
    put('foundationStatusTitle', status === 'ready' ? 'Foundation 数据可用' : status === 'stale' ? 'Foundation 数据需要更新' : status === 'degraded' ? 'Foundation 部分数据不可用' : 'Foundation 暂无可显示数据');
    put('foundationStatusDetail', `最后更新：${freshnessText(bootstrap.dataFreshness)}`);
    setStatusPill($('#foundationFreshnessPill'), status);
    setStatusPill($('#foundationSourcePill'), (bootstrap.sourceErrors || []).length ? 'degraded' : status);
    const skillStatus = bootstrap.dataFreshness?.skillPass?.status || 'unsupported';
    const foundationStatus = bootstrap.dataFreshness?.foundation?.status || status;
    const local = memory?.backends?.local || {};
    const localStatus = local.available ? (local.backend?.stale ? 'stale' : 'ready') : 'unsupported';
    const timeline = $('#foundationProjectionTimeline');
    if (timeline) timeline.innerHTML = [
      ['01', 'Archive API', status],
      ['02', 'Skill Pass 运行记录', skillStatus],
      ['03', 'Foundation 数据摘要', foundationStatus],
      ['04', 'Local FTS 索引', localStatus]
    ].map(([step, label, stepStatus]) => `<div class="${stepStatus === 'ready' ? 'done' : ''}"><i></i><b>${step}</b><span>${label}<small>${STATUS_LABELS[stepStatus] || stepStatus}</small></span></div>`).join('');
    const health = $('#foundationSourceHealth');
    if (health) health.innerHTML = [
      ['AI 资产数据', '经验、实践与 Skill', status],
      ['Skill Pass 运行记录', `${displayNumber(bootstrap.summary?.assetCount)} 项资产`, skillStatus],
      ['Foundation 文档', `${displayNumber(documents)} 份结构化文档`, foundationStatus],
      ['Local FTS 索引', `${displayNumber(local.documentCount)} 篇文档`, localStatus]
    ].map(([label, detail, sourceStatus]) => `<div><b>${label}</b><span>${detail}</span><i class="status-dot ${sourceStatus === 'ready' ? 'good' : ['stale', 'degraded'].includes(sourceStatus) ? 'warn' : 'muted'}"></i><strong>${STATUS_LABELS[sourceStatus] || sourceStatus}</strong></div>`).join('');
  }

  function renderQuality(items) {
    const keys = ['evidence', 'value', 'reuse', 'program'];
    const samples = items.map(item => item?.metrics?.scores).filter(scores => scores && typeof scores === 'object');
    const rows = $$('#qualityBars > div');
    let available = false;
    keys.forEach((key, index) => {
      const values = samples.map(scores => numeric(scores[key])).filter(value => value !== null);
      const average = values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
      if (average !== null) available = true;
      rows[index]?.querySelector('i b')?.style.setProperty('width', `${average === null ? 0 : Math.min(100, average / 5 * 100)}%`);
      if (rows[index]?.querySelector('strong')) rows[index].querySelector('strong').textContent = average === null ? '—' : average.toFixed(1);
    });
    setStatusPill($('#qualitySignalStatus'), available ? 'ready' : 'unsupported');
  }

  function renderAssetLedger() {
    const payload = state.archive.assets;
    const ledger = $('#assetLedger');
    if (!ledger) return;
    const header = ledger.querySelector('.ledger-head')?.outerHTML || '';
    const allItems = Array.isArray(payload?.items) ? payload.items : [];
    const items = state.assetFilter === 'all' ? allItems : allItems.filter(item => item.type === state.assetFilter);
    put('assetFilterResult', `${TYPE_LABELS[state.assetFilter] || '全部'} · ${displayNumber(items.length)} 项`);
    if (!payload) {
      ledger.innerHTML = `${header}<div class="ledger-state-row">暂时无法读取 AI 资产，请稍后重试。</div>`;
      return;
    }
    if (!items.length) {
      const message = payload.status === 'degraded' ? '部分 AI 资产读取失败，请稍后重试。' : '当前筛选条件下暂无 AI 资产。';
      ledger.innerHTML = `${header}<div class="ledger-state-row">${message}</div>`;
      return;
    }
    ledger.innerHTML = header + items.map(item => {
      const evidence = numeric(item?.metrics?.evidenceCount);
      return `<button type="button" data-asset-id="${escapeHtml(item.id)}"><span class="type-tag ${TYPE_TAGS[item.type] || 'blue-tag'}">${escapeHtml(TYPE_LABELS[item.type] || item.type)}</span><b>${escapeHtml(item.title || '未命名资产')}</b><span class="status-text ${item.status === 'verified' ? 'good-text' : item.status === 'draft' ? 'warn-text' : ''}">${escapeHtml(assetStatusLabel(item.status))}</span><time>${escapeHtml(shortDate(item.businessDate))}</time><span>${evidence === null ? '证据 —' : `${displayNumber(evidence)} 条证据`}</span></button>`;
    }).join('');
  }

  function hydrateAssets(payload) {
    state.archive.assets = payload;
    const items = Array.isArray(payload?.items) ? payload.items : [];
    const counts = payload?.counts || {};
    [['assetAllCount', counts.all], ['assetExperienceCount', counts.experience], ['assetPracticeCount', counts.practice], ['assetSkillCount', counts.skill]].forEach(([id, value]) => {
      if (numeric(value) !== null) put(id, displayNumber(value));
    });
    renderQuality(items);
    renderAssetLedger();
    const remote = items.map(item => ({ type: item.type === 'skill' ? 'skills' : 'memory', label: TYPE_LABELS[item.type], title: item.title, meta: `${item.businessDate || '日期未知'} · ${assetStatusLabel(item.status)}`, assetId: item.id, remote: true }));
    for (let index = globalCatalog.length - 1; index >= 0; index -= 1) if (globalCatalog[index].remote) globalCatalog.splice(index, 1);
    globalCatalog.push(...remote);
    renderGlobalResults($('#globalSearchInput')?.value || '');
    const arrivals = $('#latestArrivals');
    if (arrivals) arrivals.innerHTML = items.length ? items.slice(0, 5).map(item => `<li><time>${escapeHtml(shortDate(item.businessDate))}</time><i class="event-dot ${item.type === 'skill' ? 'violet-bg' : item.type === 'practice' ? 'teal-bg' : 'rust-bg'}"></i><div><b>${escapeHtml(item.title || '未命名资产')}</b><span>${escapeHtml(TYPE_LABELS[item.type] || item.type)} · ${escapeHtml(assetStatusLabel(item.status))}</span></div></li>`).join('') : '<li><time>—</time><i class="event-dot neutral-bg"></i><div><b>暂无 AI 资产</b><span>Archive API</span></div></li>';
  }

  function renderSkillPassRuns(payload) {
    const runs = Array.isArray(payload?.items) ? payload.items : [];
    const chart = $('#skillPassChart');
    if (chart) {
      if (!runs.length) chart.innerHTML = `<div class="empty-state"><b>${payload?.status === 'empty' ? '暂无 Skill Pass 运行记录' : '暂时无法读取 Skill Pass 运行记录'}</b><p>当前没有可显示的运行趋势。</p></div>`;
      else {
        const max = Math.max(1, ...runs.flatMap(run => ['experience', 'practice', 'skill'].map(key => numeric(run?.counts?.[key]) || 0)));
        chart.innerHTML = runs.slice().reverse().map(run => `<div class="skill-run-column" title="${escapeHtml(run.businessDate)} · ${escapeHtml(run.promptVersion)}"><i style="height:${Math.max(2, (numeric(run?.counts?.experience) || 0) / max * 100)}%"></i><i style="height:${Math.max(2, (numeric(run?.counts?.practice) || 0) / max * 100)}%"></i><i style="height:${Math.max(2, (numeric(run?.counts?.skill) || 0) / max * 100)}%"></i><small>${escapeHtml(shortDate(run.businessDate))}</small></div>`).join('');
      }
    }
    put('skillPassRunRange', runs.length ? `${shortDate(runs[runs.length - 1]?.businessDate)} — ${shortDate(runs[0]?.businessDate)}` : STATUS_LABELS[payload?.status] || '暂不可用');
    const home = $('#homeEvidenceBars');
    if (home) {
      const ordered = runs.slice().reverse().slice(-20);
      const totals = ordered.map(run => ['experience', 'practice', 'skill'].reduce((sum, key) => sum + (numeric(run?.counts?.[key]) || 0), 0));
      const max = Math.max(1, ...totals);
      home.style.gridTemplateColumns = `repeat(${Math.max(1, ordered.length)}, minmax(8px, 1fr))`;
      home.innerHTML = ordered.length ? ordered.map((run, index) => `<span class="bar-wrap" title="${escapeHtml(run.businessDate)} · ${totals[index]} 项资产"><i style="height:${Math.max(3, totals[index] / max * 100)}%"></i><small>${escapeHtml(shortDate(run.businessDate))}</small></span>`).join('') : '<span class="empty-inline">暂无运行记录</span>';
      const peak = totals.length ? totals.indexOf(max) : -1;
      put('homeRunTotal', `${displayNumber(runs.length)} 次`);
      put('homeRunPeak', peak >= 0 ? `最多 ${shortDate(ordered[peak]?.businessDate)} · ${max}` : '最多 —');
      put('homeRunLatest', ordered.length ? `最近 ${shortDate(ordered.at(-1)?.businessDate)} · ${totals.at(-1)}` : '最近 —');
    }
  }

  function mergeDetectedAgents(list) {
    const merged = new Map();
    (Array.isArray(list) ? list : []).forEach(agent => {
      if (!agent || agent.detected === false || /not[- ]?detected|absent/i.test(String(agent.status || ''))) return;
      const rawName = String(agent.name || '').trim();
      if (!rawName) return;
      const antigravity = /antigravity/i.test(rawName);
      const name = antigravity ? 'Antigravity' : rawName;
      const key = name.toLowerCase();
      const current = merged.get(key);
      if (!current) {
        merged.set(key, { ...agent, name, variantCount: 1 });
        return;
      }
      const existing = current;
      existing.variantCount += 1;
      ['sessionCount', 'messageCount', 'tokenCount'].forEach(field => {
        const previous = numeric(existing[field]);
        const incoming = numeric(agent[field]);
        existing[field] = previous === null ? incoming : incoming === null ? previous : previous + incoming;
      });
      const currentDate = timestamp(existing.lastActive);
      const candidateDate = timestamp(agent.lastActive);
      if (candidateDate && (!currentDate || candidateDate > currentDate)) existing.lastActive = agent.lastActive;
      merged.set(key, existing);
    });
    return Array.from(merged.values());
  }

  function agentTone(name) {
    if (/codex/i.test(name)) return 'violet';
    if (/claude/i.test(name)) return 'teal';
    if (/openclaw/i.test(name)) return 'rust';
    if (/antigravity/i.test(name)) return 'plum';
    if (/hermes/i.test(name)) return 'green';
    if (/gemini/i.test(name)) return 'ochre';
    return 'blue';
  }

  function hydrateAgents(source) {
    const list = mergeDetectedAgents(source);
    for (let index = globalCatalog.length - 1; index >= 0; index -= 1) if (globalCatalog[index].remoteAgent) globalCatalog.splice(index, 1);
    globalCatalog.push(...list.map(agent => ({
      type: 'agents', label: 'Agent', title: `${agent.name} 数据来源`, meta: `${agentStatusLabel(agent.status)} · 最近活动 ${shortDate(agent.lastActive)}`, agent: agent.name, remoteAgent: true
    })));
    renderGlobalResults($('#globalSearchInput')?.value || '');
    put('detectedToolCount', displayNumber(list.length));
    put('agentLocalCount', displayNumber(list.filter(agent => agent.lastActive).length));
    put('agentCatalogEyebrow', `AGENT SOURCES · 已检测 ${displayNumber(list.length)} 个`);
    put('activeAgentStamp', `已检测 ${displayNumber(list.length)} 个 Agent`);
    const latest = list.map(agent => timestamp(agent.lastActive)).filter(Boolean).sort((a, b) => b - a)[0];
    put('agentLatestActivity', latest ? shortDate(latest.toISOString()) : '—');
    setStatusPill($('#agentCoverageStatus'), list.length ? 'ready' : 'empty');
    const grid = $('#agentCardGrid');
    const bars = $('#agentSourceBars');
    if (!list.length) {
      if (grid) grid.innerHTML = '<div class="empty-state"><b>未检测到本地 Agent 数据</b><p>检测到本地数据后，会在这里显示对应工具。</p></div>';
      if (bars) bars.innerHTML = '<div class="empty-inline">暂无本地 Agent 数据。</div>';
      return;
    }
    const now = Date.now();
    const byRecent = list.slice().sort((a, b) => (timestamp(b.lastActive)?.getTime() || 0) - (timestamp(a.lastActive)?.getTime() || 0));
    if (bars) bars.innerHTML = byRecent.slice(0, 6).map(agent => {
      const date = timestamp(agent.lastActive);
      const days = date ? Math.max(0, Math.floor((now - date.getTime()) / 86400000)) : null;
      const width = days === null ? 3 : Math.max(5, 100 - Math.min(95, days));
      const tone = agentTone(agent.name);
      return `<button type="button" data-agent="${escapeHtml(agent.name)}"><span><b>${escapeHtml(agent.name.split(/\s+/).map(part => part[0]).join('').slice(0, 2).toUpperCase())}</b>${escapeHtml(agent.name)}</span><i><b style="width:${width}%;--bar:var(--${tone === 'ochre' ? 'ochre' : tone})"></b></i><strong>${escapeHtml(shortDate(agent.lastActive))}</strong><small>${days === null ? '时间未知' : days === 0 ? '今天' : `${days} 天前`}</small></button>`;
    }).join('');
    const homeBars = $('#homeAgentFreshnessBars');
    if (homeBars) homeBars.innerHTML = byRecent.slice(0, 5).map(agent => {
      const date = timestamp(agent.lastActive);
      const days = date ? Math.max(0, Math.floor((now - date.getTime()) / 86400000)) : null;
      const width = days === null ? 3 : Math.max(5, 100 - Math.min(95, days));
      const tone = agentTone(agent.name);
      return `<button type="button" data-agent="${escapeHtml(agent.name)}"><span>${escapeHtml(agent.name)}</span><i><b style="width:${width}%;--bar:var(--${tone === 'ochre' ? 'ochre' : tone})"></b></i><strong>${escapeHtml(shortDate(agent.lastActive))}</strong></button>`;
    }).join('') || '<div class="empty-inline">暂无本地 Agent 数据。</div>';
    if (grid) grid.innerHTML = byRecent.map(agent => {
      const tone = agentTone(agent.name);
      const monogram = agent.name.split(/\s+/).map(part => part[0]).join('').slice(0, 2).toUpperCase();
      const normalized = /antigravity/i.test(agent.name) ? 'antigravity' : /cursor/i.test(agent.name) ? 'cursor' : '';
      const boundary = normalized === 'antigravity' ? '多个客户端的数据已合并' : normalized === 'cursor' ? '只读取本地数据，不请求 API' : '只显示脱敏后的本地摘要';
      agents[agent.name] = { monogram, tone, status: agentStatusLabel(agent.status), summary: `已在这台设备上检测到 ${agent.name} 的本地数据。`, stats: [['检测','已检测'],['最近活动',shortDate(agent.lastActive)],['范围','仅本机']], details: [['检测状态','已在本机检测到'],['最近活动', agent.lastActive || '时间未知'],['数据范围', boundary],['Token / 消息','请查看历史用量']], notes: normalized === 'antigravity' ? 'Antigravity 的 CLI、IDE 与 App 变体已合并为一个数据来源。' : normalized === 'cursor' ? 'Cursor 仅显示本地可获得的信息，不调用 Cursor API。' : '这里只显示脱敏后的来源摘要；Token 和消息统计请查看“历史用量”。' };
      return `<button class="agent-card" type="button" data-agent="${escapeHtml(agent.name)}" data-agent-normalized="${normalized}"><span class="agent-monogram ${tone === 'ochre' ? 'gold' : tone}-bg">${escapeHtml(monogram)}</span><div><span class="agent-state good-text">已在本机检测到</span><h3>${escapeHtml(agent.name)}</h3><p>${escapeHtml(boundary)}</p></div><dl><div><dt>最近活动</dt><dd>${escapeHtml(shortDate(agent.lastActive))}</dd></div><div><dt>数据状态</dt><dd>${escapeHtml(agentStatusLabel(agent.status))}</dd></div><div><dt>显示范围</dt><dd>${normalized === 'antigravity' ? '已合并' : normalized === 'cursor' ? '仅本地数据' : '仅显示摘要'}</dd></div></dl><span class="card-arrow">→</span></button>`;
    }).join('');
    grid?.setAttribute('aria-busy', 'false');
  }

  function trendRows(activity) {
    return Array.isArray(activity?.trend30d) ? activity.trend30d.filter(row => row && row.date) : [];
  }

  function trendTotal(row) {
    return Object.values(row?.slots || {}).reduce((sum, value) => sum + (numeric(value) || 0), 0);
  }

  function renderDailyActivity(rows) {
    const container = $('#hourlyUsageChart');
    if (!container) return;
    if (!rows.length) {
      container.innerHTML = '<div class="empty-inline">当前数据源不提供每日趋势。</div>';
      put('dailyActivityTotal', '暂不支持');
      return;
    }
    const values = rows.map(trendTotal);
    const width = 600, height = 190;
    const points = pointsFor(values, width, height - 22, 10);
    const line = points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
    const area = `10,${height - 31} ${line} ${width - 10},${height - 31}`;
    const circles = points.map(([x, y], index) => `<circle class="point" cx="${x}" cy="${y}" r="3"><title>${escapeHtml(rows[index].date)} · ${displayNumber(values[index])}</title></circle>`).join('');
    container.innerHTML = `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="每日活动趋势"><polygon class="area" points="${area}"></polygon><polyline class="line" points="${line}"></polyline>${circles}</svg>`;
    const peak = Math.max(...values);
    put('dailyActivityTotal', displayNumber(values.reduce((sum, value) => sum + value, 0), true));
    put('dailyActivityPeak', `最高 ${shortDate(rows[values.indexOf(peak)]?.date)} · ${displayNumber(peak, true)}`);
    put('dailyActivityLatest', `最近 ${shortDate(rows.at(-1)?.date)} · ${displayNumber(values.at(-1), true)}`);
  }

  function hydrateActivity(activity) {
    if (!activity) {
      put('usageTokenTotal', '—');
      put('usageMessageTotal', '—');
      put('usageSessionTotal', '—');
      put('usageActiveDays', '—');
      put('homeUsageSummary', '历史用量暂不可用');
      put('archiveActivityFreshness', '历史用量 · 暂不可用');
      put('usageRangeLabel', '暂不可用');
      const usageChart = $('#usage30dChart');
      if (usageChart) usageChart.innerHTML = '<div class="empty-inline">历史用量趋势暂不可用；请检查本地数据服务。</div>';
      const dailyChart = $('#hourlyUsageChart');
      if (dailyChart) dailyChart.innerHTML = '<div class="empty-inline">每日活动趋势暂不可用；请检查本地数据服务。</div>';
      put('dailyActivityTotal', '暂不可用');
      put('dailyActivityPeak', '最高 —');
      put('dailyActivityLatest', '最近 —');
      const agentBars = $('#usageAgentBars');
      if (agentBars) agentBars.innerHTML = '<div class="empty-inline">历史 Agent 用量暂不可用。</div>';
      const modelBars = $('#usageModelBars');
      if (modelBars) modelBars.innerHTML = '<div class="empty-inline">历史模型用量暂不可用。</div>';
      put('usageModelCount', '暂不可用');
      return;
    }
    const totals = activity.totals || {};
    put('usageTokenTotal', displayNumber(totals.tokens, true));
    put('usageMessageTotal', displayNumber(totals.messages));
    put('usageSessionTotal', displayNumber(totals.sessions));
    put('usageActiveDays', displayNumber(totals.activeDays));
    put('homeUsageSummary', `${displayNumber(totals.tokens, true)} Token · ${displayNumber(totals.messages)} 条消息 · ${displayNumber(totals.sessions)} 个 Session`);
    put('archiveActivityFreshness', `历史用量 · 更新于 ${freshnessText(activity.dataFreshness)}`);
    const rows = trendRows(activity);
    put('usageRangeLabel', rows.length ? `${shortDate(rows[0].date)} — ${shortDate(rows.at(-1).date)}` : '暂不支持');
    if (rows.length) {
      usage30d.splice(0, usage30d.length, ...rows.map(row => {
        const parts = Object.values(row.slots || {}).map(value => numeric(value) || 0).slice(0, 4);
        while (parts.length < 4) parts.push(0);
        return [shortDate(row.date), ...parts];
      }));
      renderUsage30d();
    } else if ($('#usage30dChart')) $('#usage30dChart').innerHTML = '<div class="empty-inline">当前数据源不提供 30 天趋势。</div>';
    renderDailyActivity(rows);
    const usageAgents = mergeDetectedAgents(activity.agents);
    const agentBars = $('#usageAgentBars');
    if (agentBars) {
      const max = Math.max(1, ...usageAgents.map(agent => numeric(agent.tokenCount) || 0));
      agentBars.innerHTML = usageAgents.length ? usageAgents.sort((a, b) => (numeric(b.tokenCount) || -1) - (numeric(a.tokenCount) || -1)).map(agent => `<div><span>${escapeHtml(agent.name)}</span><i><b style="width:${numeric(agent.tokenCount) === null ? 0 : numeric(agent.tokenCount) / max * 100}%;--bar:var(--violet)"></b></i><strong>${displayNumber(agent.tokenCount, true)}</strong></div>`).join('') : '<div class="empty-inline">当前数据源不提供 Agent 用量分布。</div>';
    }
    const models = Array.isArray(activity.models) ? activity.models.filter(model => model?.name) : [];
    put('usageModelCount', `${displayNumber(models.length)} 个模型`);
    const modelBars = $('#usageModelBars');
    if (modelBars) {
      const top = models.slice().sort((a, b) => (numeric(b.messages) || 0) - (numeric(a.messages) || 0)).slice(0, 8);
      const max = Math.max(1, ...top.map(model => numeric(model.messages) || 0));
      modelBars.style.gridTemplateColumns = `repeat(${Math.max(1, top.length)}, minmax(50px, 1fr))`;
      modelBars.innerHTML = top.length ? top.map(model => `<div><span>${escapeHtml(model.name)}</span><i><b style="height:${Math.max(3, (numeric(model.messages) || 0) / max * 100)}%"></b></i><strong>${displayNumber(model.messages)}</strong></div>`).join('') : '<div class="empty-inline">当前数据源不提供模型消息分布。</div>';
    }
    const coverage = $('#activityCoverage');
    if (coverage) coverage.innerHTML = [['历史用量汇总', activity.status], ['Agent 用量分布', usageAgents.length ? 'ready' : 'unsupported'], ['模型消息分布', models.length ? 'ready' : 'unsupported']].map(([label, status]) => `<div><span><i class="status-dot ${status === 'ready' ? 'good' : ['stale', 'degraded'].includes(status) ? 'warn' : 'muted'}"></i>${label}</span><b>${STATUS_LABELS[status] || status || '未知'}</b></div>`).join('');
  }

  function hydrateMemoryStatus(memory) {
    if (!memory) return;
    const local = memory?.backends?.local || {};
    const rag = memory?.backends?.rag || {};
    const count = numeric(local.documentCount);
    const sources = numeric(local.sourceCount);
    for (let index = globalCatalog.length - 1; index >= 0; index -= 1) if (globalCatalog[index].remoteMemoryIndex) globalCatalog.splice(index, 1);
    globalCatalog.push({
      type: 'memory', label: '索引', title: 'Local FTS 本地记忆索引',
      meta: `${count === null ? '文档数未知' : `${displayNumber(count)} 篇文档`} · ${sources === null ? '来源数未知' : `${displayNumber(sources)} 个来源`}`,
      route: 'memory', remoteMemoryIndex: true
    });
    renderGlobalResults($('#globalSearchInput')?.value || '');
    if (count !== null) {
      put('homeMemoryCount', displayNumber(count));
      put('homeMemoryLegend', displayNumber(count));
      put('supportingMemoryCount', displayNumber(count));
      put('navMemoryCount', displayNumber(count));
    }
    put('homeMemoryMeta', `${local.available ? '可用' : '不可用'} · ${sources === null ? '数据来源 —' : `${displayNumber(sources)} 个数据来源`}`);
    put('memoryIndexDocumentCount', displayNumber(count));
    put('memoryIndexSourceCount', displayNumber(sources));
    put('memoryMapDocumentCount', `${displayNumber(count)} 篇文档`);
    put('memoryMapSourceCount', displayNumber(sources));
    put('memoryLocalBackendStatus', local.available ? (local.backend?.stale ? 'Local FTS · 可用但需要更新' : 'Local FTS · 可用') : 'Local FTS · 不可用');
    const ragEnabled = Boolean(rag?.status?.enabled || rag?.status?.productEnabled);
    put('memoryRagBackendStatus', ragEnabled && rag.available ? 'nova-RAG · 可用' : ragEnabled ? 'nova-RAG · 尚未就绪' : 'nova-RAG · 尚未启用');
    put('memorySearchBackendLabel', `本地全文搜索 · ${local.backend?.stale ? '索引需要更新' : '索引可用'}`);
    const ticket = $('.memory-status-ticket');
    if (ticket) {
      const ready = Boolean(memory.available && local.available);
      ticket.querySelector('.status-seal').textContent = ready ? '正常' : local.status === 'missing' ? '无数据' : '检查中';
      ticket.querySelector(':scope > div > b').textContent = ready ? 'Local FTS（本地全文搜索）可用' : 'Local FTS 当前不可用';
      ticket.querySelector(':scope > div > span').textContent = `${count === null ? '文档数未知' : `${displayNumber(count)} 篇文档`} · ${local.indexedAt ? `更新于 ${shortDate(local.indexedAt)}` : '更新时间未知'}`;
    }
    const capabilities = memory.capabilities || local.capabilities || {};
    const matrix = $('.capability-matrix');
    if (matrix) {
      const rows = [
        ['全文搜索', capabilities.fts5 ?? capabilities.lexical],
        ['模糊匹配', capabilities.trigram],
        ['按来源和类型筛选', capabilities.metadataFilters],
        ['语义搜索', capabilities.semantic],
        ['Agent 自动规划', rag?.capabilities?.agenticPlanning]
      ];
      matrix.innerHTML = rows.map(([label, enabled]) => `<div><span>${label}</span><b class="${enabled ? 'good-text' : 'muted-text'}">${enabled ? '可用' : '未启用'}</b></div>`).join('');
    }
    const bootstrapStatus = state.archive.bootstrap?.status || 'unsupported';
    const health = $('#homeHealthRows');
    if (health) health.innerHTML = [
      ['AI 资产数据', bootstrapStatus],
      ['Local FTS 搜索索引', local.available ? (local.backend?.stale ? 'stale' : 'ready') : 'unsupported'],
      ['nova-RAG 语义搜索', ragEnabled ? (rag.available ? 'ready' : 'degraded') : 'unsupported']
    ].map(([label, status]) => `<div><span><i class="status-dot ${status === 'ready' ? 'good' : ['stale', 'degraded'].includes(status) ? 'warn' : 'muted'}"></i>${label}</span><b>${STATUS_LABELS[status] || status}</b></div>`).join('');
  }

  async function loadArchiveData({ announce = false } = {}) {
    if (!window.LivingArchiveApi) {
      setArchiveState('error', '本地数据客户端未加载', '页面框架仍可浏览，但动态数据暂不可用。', true);
      return;
    }
    setArchiveState('loading', '正在加载本地数据', '正在同时读取 AI 资产、Skill Pass 运行记录、历史用量和记忆索引。');
    try {
      const data = await window.LivingArchiveApi.load();
      state.archive = data;
      hydrateBootstrap(data.bootstrap);
      hydrateAssets(data.assets);
      renderSkillPassRuns(data.runs);
      hydrateActivity(data.activity);
      hydrateMemoryStatus(data.memory);
      hydrateAgents(data.bootstrap?.agents || data.activity?.agents || []);
      hydrateDiaryIndex(data.diaries);
      const status = data.errors.length ? (data.bootstrap ? 'degraded' : 'error') : data.bootstrap?.status || data.assets?.status || 'empty';
      const sourceErrors = (data.bootstrap?.sourceErrors || []).length + data.errors.length;
      const messages = {
        ready: ['本地数据已加载', 'AI 资产、运行记录和历史用量均来自本地只读接口。'],
        empty: ['本地数据已加载，但暂无可显示内容', '尚未找到 AI 资产、日记或活动记录。'],
        stale: ['数据已加载，但部分内容需要更新', '页面显示的是较早数据，可重新读取以获取最新状态。'],
        degraded: ['部分本地数据读取失败', `${sourceErrors} 个数据来源读取失败，其余内容仍可查看。`],
        unsupported: ['部分数据暂不支持显示', '无法获取的字段显示为“—”。'],
        error: ['无法读取本地数据服务', '导航和静态说明仍可浏览，动态数据暂不显示。']
      };
      const [title, detail] = messages[status] || messages.degraded;
      setArchiveState(status, title, detail, ['degraded', 'error'].includes(status));
      if (announce) showToast(status === 'ready' ? '本地数据已更新' : `数据状态：${STATUS_LABELS[status] || status}`, status === 'ready' ? 'success' : 'warning');
    } catch (error) {
      state.archive.errors = [{ source: 'archive', message: String(error?.message || error) }];
      hydrateAssets(null);
      renderSkillPassRuns(null);
      hydrateActivity(null);
      hydrateAgents([]);
      setArchiveState('error', '无法读取本地数据服务', '动态数据暂不显示，请稍后重试。', true);
      if (announce) showToast('本地数据服务暂不可用', 'danger');
    }
  }

  function navigate(route, options = {}) {
    if (!ROUTES.has(route)) return;
    closeOverlays();
    state.route = route;
    $$('.page[data-page]').forEach(page => {
      const active = page.dataset.page === route;
      page.hidden = !active;
      page.classList.toggle('active', active);
    });
    $$('.nav-link[data-route]').forEach(link => {
      const active = link.dataset.route === route;
      link.classList.toggle('active', active);
      if (active) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    });
    $$('.mobile-bottom-nav button[data-route]').forEach(link => link.classList.toggle('active', link.dataset.route === route));
    if (route === 'reports' && options.period) setArchivePeriod(options.period);
    if (route === 'assets' && options.focus) applyAssetFilter(options.focus);
    const hash = `#${route}`;
    if (location.hash !== hash) history.pushState({ route }, '', hash);
    document.title = `Actanara · ${routeTitles[route]} · Living Archive`;
    window.dispatchEvent(new CustomEvent('archive-route-change', { detail: { route } }));
    window.scrollTo({ top: 0, behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
    setTimeout(() => $('.page:not([hidden]) h1')?.focus?.({ preventScroll: true }), 10);
  }

  function routeFromHash() {
    const hash = location.hash.replace(/^#/, '');
    const aliases = { 'archive-top': 'home', 'daily-console': 'console', 'archive-console': 'console', 'archive-agents': 'agents', 'archive-memory': 'memory', 'archive-foundation': 'foundation' };
    const route = aliases[hash] || hash;
    if (ROUTES.has(route)) navigate(route, { replace: true });
  }

  function openModal(name) {
    const panel = $(`[data-modal-panel="${CSS.escape(name)}"]`);
    if (!panel) return;
    closeOverlays(false);
    state.previousFocus = document.activeElement;
    $('#modalBackdrop').hidden = false;
    panel.hidden = false;
    document.body.classList.add('modal-open');
    requestAnimationFrame(() => panel.querySelector('input, button, select, a')?.focus());
  }

  function closeOverlays(restoreFocus = true) {
    $$('.modal[data-modal-panel]').forEach(modal => { modal.hidden = true; });
    $('#detailDrawer').hidden = true;
    $('#modalBackdrop').hidden = true;
    document.body.classList.remove('modal-open');
    if (restoreFocus && state.previousFocus instanceof HTMLElement) state.previousFocus.focus({ preventScroll: true });
    state.previousFocus = null;
  }

  function openDrawer(title, kicker, html) {
    closeOverlays(false);
    state.previousFocus = document.activeElement;
    $('#drawerTitle').textContent = title;
    $('#drawerKicker').textContent = kicker;
    $('#drawerContent').innerHTML = html;
    $('#modalBackdrop').hidden = false;
    $('#detailDrawer').hidden = false;
    document.body.classList.add('modal-open');
    $('#detailDrawer .modal-close').focus();
  }

  function showToast(message, tone = 'success') {
    const toast = $('#toast');
    toast.querySelector('span').textContent = message;
    toast.querySelector('i').style.background = tone === 'warning' ? 'var(--ochre)' : tone === 'danger' ? 'var(--danger)' : 'var(--moss)';
    toast.hidden = false;
    clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(() => { toast.hidden = true; }, 3200);
  }

  function renderHomeEvidence() {
    const container = $('#homeEvidenceBars');
    if (!container) return;
    if (!evidenceData.length) {
      container.innerHTML = '<span class="empty-inline">正在读取 Skill Pass 运行记录…</span>';
      return;
    }
    const max = Math.max(...evidenceData.map(([, value]) => value));
    container.innerHTML = evidenceData.map(([date, value], index) => {
      const height = Math.max(2.5, (value / max) * 100);
      const label = index % 3 === 0 || index === evidenceData.length - 1 ? `<small>${date}</small>` : '';
      return `<span class="bar-wrap" title="${date} · ${formatNumber(value)} 条证据"><i style="height:${height}%"></i>${label}</span>`;
    }).join('');
  }

  function pointsFor(values, width, height, padding = 12) {
    const max = Math.max(...values, 1);
    return values.map((value, index) => {
      const x = padding + (index / Math.max(1, values.length - 1)) * (width - padding * 2);
      const y = height - padding - (value / max) * (height - padding * 2);
      return [x, y];
    });
  }

  function renderAssetGrowth(mode = 'evidence') {
    const container = $('#assetGrowthChart');
    if (!container) return;
    if (!evidenceData.length || !reportsDaily.length) {
      container.innerHTML = '<div class="empty-inline">当前没有结构化活动趋势。</div>';
      return;
    }
    const width = 1000;
    const height = 260;
    const evidenceMap = new Map(evidenceData);
    const labels = reportsDaily.map(([date]) => date);
    const evidence = labels.map(date => evidenceMap.get(date) || 0);
    const reports = reportsDaily.map(([, value]) => value);
    const points = pointsFor(evidence, width, height - 26, 18);
    const line = points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
    const area = `18,${height - 44} ${line} ${width - 18},${height - 44}`;
    const maxReports = Math.max(...reports);
    const sticks = reports.map((value, index) => {
      const x = 18 + (index / Math.max(1, reports.length - 1)) * (width - 36);
      const barHeight = (value / maxReports) * 74;
      return `<rect class="report-stick" x="${x - 5}" y="${height - 44 - barHeight}" width="10" height="${barHeight}" rx="2"><title>${labels[index]} · ${value} reports</title></rect>`;
    }).join('');
    const labelsSvg = labels.map((label, index) => index % 4 === 0 || index === labels.length - 1 ? `<text x="${18 + (index / (labels.length - 1)) * (width - 36)}" y="251" text-anchor="middle" font-size="12" fill="#5e686b" font-family="monospace">${label}</text>` : '').join('');
    container.innerHTML = `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="结构化活动与报告产出"><polygon class="evidence-area" points="${area}" style="opacity:${mode === 'evidence' ? 1 : .35}"></polygon>${sticks}<polyline class="evidence-line" points="${line}" style="opacity:${mode === 'evidence' ? 1 : .32}"></polyline>${points.map(([x,y],i) => `<circle cx="${x}" cy="${y}" r="3" fill="#267883"><title>${labels[i]} · ${formatNumber(evidence[i])} evidence</title></circle>`).join('')}${labelsSvg}</svg>`;
    $$('.report-stick', container).forEach(stick => { stick.style.opacity = mode === 'reports' ? '.95' : '.46'; });
  }

  function renderUsage30d() {
    const container = $('#usage30dChart');
    if (!container) return;
    if (!usage30d.length) {
      container.innerHTML = '<div class="empty-inline">正在读取历史用量趋势…</div>';
      return;
    }
    const totals = usage30d.map(row => row.slice(1).reduce((sum, value) => sum + value, 0));
    const max = Math.max(...totals);
    const colors = ['plum-bg','violet-bg','teal-bg','rust-bg'];
    container.innerHTML = usage30d.map((row, index) => {
      const [date, ...parts] = row;
      const total = totals[index];
      const height = total ? Math.max(3, Math.sqrt(total / max) * 100) : 1;
      const segments = parts.map((value, partIndex) => `<i class="${colors[partIndex]}" style="height:${total ? (value / total) * 100 : 0}%"></i>`).join('');
      const label = (index % 4 === 0 && index < usage30d.length - 2) || index === usage30d.length - 1 ? `<small>${date}</small>` : '';
      return `<span class="stacked-day" style="height:${height}%" title="${date} · ${formatNumber(total)} Token">${segments}${label}</span>`;
    }).join('');
  }

  function renderHourlyUsage() {
    const container = $('#hourlyUsageChart');
    if (!container) return;
    if (!hourlyUsage.length) {
      container.innerHTML = '<div class="empty-inline">正在读取每日活动趋势…</div>';
      return;
    }
    const width = 600;
    const height = 190;
    const points = pointsFor(hourlyUsage, width, height - 22, 10);
    const line = points.map(([x,y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
    const area = `10,${height - 31} ${line} ${width - 10},${height - 31}`;
    const labels = [0,6,12,18,23].map(hour => `<text x="${10 + (hour / 23) * (width - 20)}" y="185" text-anchor="middle" font-size="8" fill="#5e686b" font-family="monospace">${String(hour).padStart(2,'0')}:00</text>`).join('');
    const circles = points.map(([x,y],index) => hourlyUsage[index] > 0 ? `<circle class="point" cx="${x}" cy="${y}" r="3"><title>${String(index).padStart(2,'0')}:00 · ${formatNumber(hourlyUsage[index])}</title></circle>` : '').join('');
    container.innerHTML = `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="08-08 小时 Token 活动"><polygon class="area" points="${area}"></polygon><polyline class="line" points="${line}"></polyline>${circles}${labels}</svg>`;
  }

  function applyAssetFilter(filter) {
    if (!filter) filter = 'all';
    $$('.filter-chip[data-asset-filter], .metric-card[data-asset-filter]').forEach(button => button.classList.toggle('active', button.dataset.assetFilter === filter));
    $$('[data-asset-group]').forEach(card => {
      const groups = card.dataset.assetGroup.split(/\s+/);
      card.hidden = filter !== 'all' && !groups.includes(filter);
    });
    if (state.route === 'assets') showToast(filter === 'all' ? '已显示全部 AI 资产类别' : `已筛选：${TYPE_LABELS[filter] || filter}`, 'success');
  }

  function renderMemoryResults(query = '') {
    const container = $('#memorySearchResults');
    if (!container) return;
    const normalized = query.trim().toLowerCase();
    let records = memoryRecords.filter(record => state.memorySource === 'all' || record.type === state.memorySource);
    if (normalized && !['持久化员工身份','actanara','agent','记忆','架构'].some(term => normalized.includes(term) || term.includes(normalized))) {
      records = records.filter(record => `${record.title} ${record.text} ${record.source}`.toLowerCase().includes(normalized));
    }
    const heading = $('#search-results-title');
    heading.textContent = records.length ? `找到 ${records.length} 条相关记忆` : '没有找到匹配记忆';
    if (!records.length) {
      container.innerHTML = `<div class="search-result"><h3>尝试缩短关键词或切换数据来源</h3><p>Local FTS 支持全文搜索、模糊匹配和条件筛选；当前未启用 nova-RAG 语义搜索。</p></div>`;
      return;
    }
    container.innerHTML = records.map((record, index) => `<article class="search-result" tabindex="0" data-detail="${record.id}"><div class="result-meta"><span>${escapeHtml(record.source)}</span><span>${escapeHtml(record.date)}</span><span>匹配度 ${(0.93 - index * .07).toFixed(2)}</span></div><h3>${escapeHtml(record.title)}</h3><p>${escapeHtml(record.text)}</p><div class="result-meta"><span>↳ ${escapeHtml(record.citation)}</span></div></article>`).join('');
  }

  function renderLiveMemoryResults(payload, query = '') {
    const container = $('#memorySearchResults');
    if (!container) return;
    const rows = Array.isArray(payload?.results) ? payload.results : [];
    for (let index = memoryRecords.length - 1; index >= 0; index -= 1) {
      if (memoryRecords[index].remote) memoryRecords.splice(index, 1);
    }
    const citations = Array.isArray(payload?.citationPack) ? payload.citationPack : [];
    rows.forEach((item, index) => {
      const provenance = item?.provenance && typeof item.provenance === 'object' ? item.provenance : {};
      const governance = item?.governance && typeof item.governance === 'object' ? item.governance : {};
      const citation = citations.find(value => value?.resultId === (item.resultId || item.id)) || {};
      const text = item.text || item.textPreview || item.excerpt || citation.excerpt || '';
      memoryRecords.push({
        id: `remote-memory-${index}`,
        remote: true,
        type: item.sourceType || item.workType || 'memory',
        source: item.sourceSet || provenance.sourceSet || 'Local memory',
        date: item.date || provenance.date || '',
        title: item.title || String(text).slice(0, 90) || `检索结果 ${index + 1}`,
        text,
        citation: citation.citationId || item.citationId || governance.lifecycle || 'local citation',
        score: numeric(item.score)
      });
    });
    for (let index = globalCatalog.length - 1; index >= 0; index -= 1) if (globalCatalog[index].remoteMemoryResult) globalCatalog.splice(index, 1);
    globalCatalog.push(...rows.map((item, index) => ({
      type: 'memory', label: '记忆', title: item.title || `记忆检索结果 ${index + 1}`,
      meta: `${item.sourceSet || item.provenance?.sourceSet || '本地索引'} · ${item.date || '日期未知'}`,
      detail: `remote-memory-${index}`, remoteMemoryResult: true
    })));
    renderGlobalResults($('#globalSearchInput')?.value || '');
    const heading = $('#search-results-title');
    if (heading) heading.textContent = rows.length ? `找到 ${rows.length} 条相关记忆` : '没有找到匹配记忆';
    const backend = payload?.backend?.kind || payload?.backend?.collection || payload?.requestedMode || 'local';
    const available = payload?.available !== false;
    if (!available) {
      container.innerHTML = '<div class="search-result"><h3>记忆搜索当前不可用</h3><p>其他页面仍可浏览，请稍后重试搜索。</p></div>';
      return;
    }
    if (!rows.length) {
      container.innerHTML = `<div class="search-result"><h3>没有匹配“${escapeHtml(query)}”的本地记忆</h3><p>${escapeHtml(backend)} 已完成检索；可尝试更短的关键词或切换来源。</p></div>`;
      return;
    }
    container.innerHTML = memoryRecords.filter(record => record.remote).map(record => `<article class="search-result" tabindex="0" data-detail="${record.id}"><div class="result-meta"><span>${escapeHtml(record.source)}</span><span>${escapeHtml(record.date || '日期未知')}</span><span>${record.score === null ? escapeHtml(backend) : `匹配度 ${record.score.toFixed(3)}`}</span></div><h3>${escapeHtml(record.title)}</h3><p>${escapeHtml(record.text)}</p><div class="result-meta"><span>↳ ${escapeHtml(record.citation)}</span></div></article>`).join('');
  }

  function renderGlobalResults(query = '') {
    const container = $('#globalResults');
    if (!container) return;
    const normalized = query.trim().toLowerCase();
    const results = globalCatalog.filter(item => {
      const typeMatch = state.globalFilter === 'all' || item.type === state.globalFilter;
      const textMatch = !normalized || `${item.title} ${item.meta} ${item.label}`.toLowerCase().includes(normalized) || normalized === 'actanara';
      return typeMatch && textMatch;
    });
    container.innerHTML = results.length ? results.map(item => `<article class="global-result" tabindex="0" ${item.route ? `data-result-route="${item.route}"` : ''} ${item.period ? `data-result-period="${escapeHtml(item.period)}"` : ''} ${item.detail ? `data-detail="${item.detail}"` : ''} ${item.assetId ? `data-asset-id="${escapeHtml(item.assetId)}"` : ''} ${item.agent ? `data-agent="${item.agent}"` : ''}><span>${escapeHtml(item.label)}</span><div><b>${escapeHtml(item.title)}</b><small>${escapeHtml(item.meta)}</small></div><i>→</i></article>`).join('') : `<div class="global-result"><span>无结果</span><div><b>没有匹配结果</b><small>尝试更短的关键词或选择“全部”类型。</small></div><i>·</i></div>`;
  }

  async function openArchiveAsset(assetId) {
    if (!window.LivingArchiveApi) return;
    openDrawer('正在读取资产', 'ASSET DETAIL', '<div class="drawer-hero"><p>正在读取资产详情…</p></div>');
    try {
      const payload = await window.LivingArchiveApi.asset(assetId);
      const item = payload?.item;
      if (!item) throw new Error('资产详情为空');
      const scores = item?.metrics?.scores || {};
      const scoreRows = [
        ['evidence', '证据完整度'],
        ['value', '实用价值'],
        ['reuse', '可复用性'],
        ['program', '可执行性']
      ].map(([key, label]) => `<div><span>${label}</span><b>${numeric(scores[key]) === null ? '—' : `${scores[key]}/5`}</b></div>`).join('');
      const relationCount = Object.values(item.relations || {}).reduce((sum, values) => sum + (Array.isArray(values) ? values.length : 0), 0);
      openDrawer(item.title || '资产详情', `${TYPE_LABELS[item.type] || 'AI 资产'} · ${assetStatusLabel(item.status)}`, `<div class="drawer-hero"><p>${escapeHtml(item.summary || '暂无可显示的摘要。')}</p><div class="drawer-stats"><div><b>${escapeHtml(shortDate(item.businessDate))}</b><span>记录日期</span></div><div><b>${escapeHtml(item.promptVersion || '—')}</b><span>提示词版本</span></div><div><b>${displayNumber(relationCount)}</b><span>关联项</span></div></div></div><section class="drawer-section"><h3>质量评分</h3><div class="drawer-table">${scoreRows}</div></section><section class="drawer-section"><h3>数据说明</h3><p>此处仅显示可展示的摘要、评分和关联信息。为保护隐私，不显示绝对路径、原始 Session 或 Skill 正文。</p></section>`);
    } catch (error) {
      openDrawer('资产详情不可用', 'READ ERROR', `<div class="drawer-hero"><p>${escapeHtml(error?.message || '无法读取资产详情')}</p></div>`);
    }
  }

  function openAgentDrawer(name) {
    const agent = agents[name];
    if (!agent) return;
    const stats = agent.stats.map(([label,value]) => `<div><b>${escapeHtml(value)}</b><span>${escapeHtml(label)}</span></div>`).join('');
    const rows = agent.details.map(([label,value]) => `<div><span>${escapeHtml(label)}</span><b>${escapeHtml(value)}</b></div>`).join('');
    openDrawer(name, `AGENT 数据源 · ${agent.status}`, `<div class="drawer-hero"><p>${escapeHtml(agent.summary)}</p><div class="drawer-stats">${stats}</div></div><section class="drawer-section"><h3>来源详情</h3><div class="drawer-table">${rows}</div></section><section class="drawer-section"><h3>说明</h3><p>${escapeHtml(agent.notes)}</p></section><section class="drawer-section"><button class="button secondary full-button" type="button" data-drawer-route="usage">查看历史用量</button></section>`);
  }

  function openDetail(id) {
    const memory = memoryRecords.find(record => record.id === id);
    if (memory) {
      openDrawer(memory.title, '记忆引用', `<div class="drawer-hero"><p>${escapeHtml(memory.text)}</p><div class="drawer-stats"><div><b>${escapeHtml(memory.date.slice(5))}</b><span>记录日期</span></div><div><b>${escapeHtml(memory.type)}</b><span>来源类型</span></div><div><b>FTS</b><span>搜索方式</span></div></div></div><section class="drawer-section"><h3>引用信息</h3><div class="drawer-table"><div><span>数据来源</span><b>${escapeHtml(memory.source)}</b></div><div><span>引用位置</span><b>${escapeHtml(memory.citation)}</b></div><div><span>搜索能力</span><b>关键词匹配 · 可引用</b></div></div></section>`);
      return;
    }
    const detailMap = {
      'diary-narrative': ['叙事日记 · 07-18','STRUCTURED HOLDING','21 个章节把当天的工作脉络、成果与下一步组织为可引用的叙事。'],
      'diary-technical': ['技术日记 · 07-18','STRUCTURED HOLDING','26 个章节记录架构决策、实现边界和基础设施变化。'],
      'diary-learning': ['学习日记 · 07-18','REUSABLE LESSONS','17 个章节把故障、根因与建议转换为可复用经验。'],
      'period-0808': ['08-01 — 08-08 资产摘要','PERIOD SUMMARY','最近一次周期资产摘要由 Job #694 生成，状态正常。'],
      'skills-hermes': ['Hermes Skills','CAPABILITY CATALOG','243 个技能实例；跨工具重复安装不等同于 243 个独立技能。'],
      'skills-openclaw': ['OpenClaw Skills','CAPABILITY CATALOG','92 个技能实例，来自系统与自定义能力目录。'],
      'skills-claude': ['Claude Code Skills','CAPABILITY CATALOG','19 个技能实例，保留来源类型与层级信息。'],
      'skills-codex': ['Codex Skills','CAPABILITY CATALOG','8 个技能实例，可与其他工具的同名能力对照。'],
      'skills-unified': ['unified context','CUSTOM SKILL','跨 Agent 上下文能力，属于 3 个自定义技能之一。'],
      'memory-sources': ['记忆数据来源','INDEX SOURCES','81 个来源覆盖日记、筛选后的对话、原生记忆、任务事件与 Foundation 数据摘要。'],
      'source-diaries': ['日记与报告来源','MEMORY SOURCE SET','解析后的 Markdown sections 与 embedded JSON 进入本地索引。'],
      'source-dialogue': ['过滤对话来源','MEMORY SOURCE SET','清洗后的跨 Agent 对话用于轻量全文与模糊检索。'],
      'source-native': ['原生记忆来源','MEMORY SOURCE SET','Codex 与 Claude Code 的原生记忆已获授权；保留来源边界。'],
      'source-tasks': ['Nova-Task 事件','MEMORY SOURCE SET','任务图谱事件作为独立来源进入检索，不改变 Nova-Task 页面设计。'],
      'source-foundation': ['Foundation 来源','MEMORY SOURCE SET','历史用量汇总、周期摘要和 Dashboard 数据副本提供结构化上下文。'],
      'model-gpt56': ['gpt-5.6-sol','MODEL HISTORY','128,144 条历史消息，23 个来源 Session。只作为历史活动元数据。'],
      'model-gpt55': ['gpt-5.5','MODEL HISTORY','38,857 条历史消息，60 个来源 Session。'],
      'model-minimax': ['MiniMax-M2.7-highspeed','MODEL HISTORY','19,744 条历史消息，101 个来源 Session。'],
      'model-glm': ['glm-5-turbo','MODEL HISTORY','8,078 条历史消息，46 个来源 Session。'],
      'model-gemini': ['gemini-3-flash-preview','MODEL HISTORY','4,802 条历史消息，13 个来源 Session。'],
      'workspace-actanara': ['actanara-linux','WORKSPACE HISTORY','08-08 的主要活动工作区；名称仅在本机原型中显示。'],
      'workspace-tokenclock': ['tokenclock-main-perf','WORKSPACE HISTORY','08-08 记录 18.2M Token 与 115 条消息。'],
      'workspace-other': ['其他工作区','WORKSPACE HISTORY','合并呈现低占比工作区，避免图表标签拥挤。'],
      'job-694': ['Foundation Job #694','REFRESH JOB','08-08 04:34:35 开始，04:36:20 完成；1,406 sources cached，0 errors。'],
      'job-693': ['Foundation Job #693','REFRESH JOB','周期资产刷新已完成；同一时间窗口的先前运行记录。'],
      'job-692': ['Foundation Job #692','REFRESH JOB','08-03 — 08-08 周期资产刷新已完成。'],
      'job-691': ['Foundation Job #691','REFRESH JOB','数据摘要写入完成，没有错误摘要。'],
      'job-690': ['Foundation Job #690','REFRESH JOB','最近调度批次中的较早刷新记录。']
    };
    const detail = detailMap[id] || ['详细信息','DETAIL','当前显示的是静态示例详情；正式版本将从对应接口读取完整的来源与处理记录。'];
    openDrawer(detail[0], detail[1], `<div class="drawer-hero"><p>${escapeHtml(detail[2])}</p></div><section class="drawer-section"><h3>资产属性</h3><div class="drawer-table"><div><span>已持久化</span><b>是</b></div><div><span>可追溯</span><b>是</b></div><div><span>可搜索与复用</span><b>可用</b></div><div><span>数据来源</span><b>Foundation 数据摘要</b></div></div></section><section class="drawer-section"><p>预览版不显示绝对路径、Session 原文或私有记忆正文。</p></section>`);
  }

  function diaryItems(payload = state.archive.diaries) {
    const rows = Array.isArray(payload) ? payload : Array.isArray(payload?.items) ? payload.items : [];
    return rows.filter(item => /^\d{4}-\d{2}-\d{2}$/.test(String(item?.fullDate || item?.date || ''))).map(item => ({ ...item, fullDate: item.fullDate || item.date }));
  }

  function renderSafeProse(value) {
    const text = safeDiaryText(value)
      .replace(/\s+\*\s+\*\*/g, '\n* **')
      .replace(/\s+-\s+(?=[^>])/g, '\n- ')
      .trim();
    if (!text) return '<p class="lede">这一页暂无可显示的正文。</p>';
    const clean = source => String(source || '').replace(/\*\*(.*?)\*\*/g, '$1').replace(/`([^`]+)`/g, '$1').trim();
    return text.split(/\n+/).filter(Boolean).map(line => {
      const heading = /^\*\s+\*\*(.+?)\*\*[：:]?\s*(.*)$/.exec(line);
      if (heading) return `<section><h3>${escapeHtml(clean(heading[1]))}</h3>${heading[2] ? `<p>${escapeHtml(clean(heading[2]))}</p>` : ''}</section>`;
      const bullet = /^[-*]\s+(.*)$/.exec(line);
      if (bullet) return `<p class="diary-bullet">${escapeHtml(clean(bullet[1]))}</p>`;
      return `<p>${escapeHtml(clean(line))}</p>`;
    }).join('');
  }

  // Diary projections are generated from private runtime material.  Keep the
  // reading view useful while removing absolute paths and credential-shaped
  // values that should never be copied into the public preview DOM.
  function safeDiaryText(value) {
    return String(value || '')
      .replace(/`(?:\/Users|\/Volumes|\/home|\/root)[^`]*`/g, '`[本机路径已隐藏]`')
      .replace(/(?:\/Users|\/Volumes|\/home|\/root)\/[^\s`"'<>]+/g, '[本机路径已隐藏]')
      .replace(/\b(api[_-]?key|access[_-]?token|password|secret)\b\s*[:=]\s*[^\s,;]+/gi, '$1=[已隐藏]');
  }

  function diaryKpiMarkup(payload) {
    const source = payload?.parsedKpi && typeof payload.parsedKpi === 'object' ? payload.parsedKpi : payload?.tokenStats && typeof payload.tokenStats === 'object' ? payload.tokenStats : {};
    const labels = {
      active_sessions: '活跃 Session', sessions_total: 'Session 总数', sessions_count: 'Session 数量',
      messages_count: '消息数', total_tokens: 'Token 总量', input_tokens: '输入 Token', output_tokens: '输出 Token',
      cache_read: '缓存读取', api_calls: 'API 调用', total: 'Token 总量', input: '输入 Token', output: '输出 Token', cache: '缓存读取'
    };
    const entries = Object.entries(source).filter(([, value]) => value !== null && value !== undefined && value !== '').slice(0, 8);
    if (!entries.length) return '';
    return `<section class="diary-detail-section"><h3>当天统计</h3><div class="diary-kpi-grid">${entries.map(([key, value]) => `<div><span>${escapeHtml(labels[key] || key)}</span><b>${escapeHtml(displayNumber(value, key.includes('token') || ['total', 'input', 'output', 'cache'].includes(key)))} </b></div>`).join('')}</div></section>`;
  }

  function diaryAgentWorkMarkup(payload) {
    const source = payload?.agentWorkNew && typeof payload.agentWorkNew === 'object' ? payload.agentWorkNew : payload?.agentWork && typeof payload.agentWork === 'object' ? payload.agentWork : {};
    const groups = Object.entries(source).flatMap(([agent, items]) => (Array.isArray(items) ? items : []).map(item => ({ agent, item })));
    if (!groups.length) return '<section class="diary-detail-section"><h3>Agent 工作</h3><p class="lede">当天没有结构化 Agent 工作条目。</p></section>';
    return `<section class="diary-detail-section"><h3>Agent 工作</h3><div class="diary-work-list">${groups.slice(0, 12).map(({ agent, item }) => `<article><div><b>${escapeHtml(agent)}</b><span>${escapeHtml(item.period || '未分时段')}</span></div><h4>${escapeHtml(safeDiaryText(item.main_task || item.mainTask || '未命名工作'))}</h4>${Array.isArray(item.sub_items || item.subItems) ? `<ul>${(item.sub_items || item.subItems).slice(0, 5).map(entry => `<li>${escapeHtml(safeDiaryText(entry))}</li>`).join('')}</ul>` : ''}</article>`).join('')}</div></section>`;
  }

  function diaryHourlyMarkup(payload) {
    const values = Object.entries(payload?.hourlyTokens || {}).map(([hour, value]) => [hour, numeric(value) || 0]).sort((a, b) => Number(a[0]) - Number(b[0]));
    if (!values.length) return '';
    const max = Math.max(1, ...values.map(([, value]) => value));
    return `<section class="diary-detail-section"><h3>小时 Token 活动</h3><div class="diary-hour-bars">${values.map(([hour, value]) => `<div title="${escapeHtml(hour)}:00 · ${escapeHtml(displayNumber(value, true))} Token"><i style="height:${Math.max(3, value / max * 100)}%"></i><span>${escapeHtml(String(hour).padStart(2, '0'))}</span></div>`).join('')}</div></section>`;
  }

  function diaryListMarkup(title, items, mapper) {
    if (!Array.isArray(items) || !items.length) return '';
    return `<section class="diary-detail-section"><h3>${escapeHtml(title)}</h3><ul class="diary-bullet-list">${items.slice(0, 12).map(item => `<li>${escapeHtml(safeDiaryText(mapper(item)))}</li>`).join('')}</ul></section>`;
  }

  function hydrateDiaryIndex(payload) {
    state.archive.diaries = payload;
    const items = diaryItems(payload);
    for (let index = globalCatalog.length - 1; index >= 0; index -= 1) if (globalCatalog[index].remoteDiary) globalCatalog.splice(index, 1);
    globalCatalog.push(...items.slice(0, 80).map(item => ({
      type: 'diary', label: '日记', title: `${item.fullDate} 日记`, meta: 'Foundation 结构化日记 · 可按日期查看', route: 'reports', period: item.fullDate.slice(0, 7), remoteDiary: true
    })));
    renderGlobalResults($('#globalSearchInput')?.value || '');
    const months = new Map();
    items.forEach(item => {
      const month = item.fullDate.slice(0, 7);
      months.set(month, (months.get(month) || 0) + 1);
    });
    const cabinet = $('#archiveCabinetMonths');
    if (cabinet) {
      cabinet.innerHTML = '<div class="nav-label">日记月份</div>' + (months.size ? Array.from(months.entries()).sort((a, b) => b[0].localeCompare(a[0])).slice(0, 8).map(([month, count]) => `<button class="cabinet-row" type="button" data-route="reports" data-period="${month}"><b>${month.replace('-', ' · ')}</b><span>${displayNumber(count)} 天有日记</span></button>`).join('') : '<span class="cabinet-loading">暂无日记</span>');
    }
    put('reportDiaryDays', displayNumber(items.length));
    if (!items.length) {
      renderCalendar();
      $('#reportSheet').innerHTML = '<div class="empty-inline">Foundation 当前没有可读取的日记文档。</div>';
      put('selectedReportDate', '暂无日记');
      return;
    }
    const latest = items.slice().sort((a, b) => b.fullDate.localeCompare(a.fullDate))[0];
    const [year, month] = latest.fullDate.split('-').map(Number);
    state.calendar = { year, month: month - 1, selected: latest.fullDate };
    state.reportTab = 'narrative';
    renderCalendar();
    loadDiary(latest.fullDate);
  }

  async function loadDiary(businessDate) {
    if (!window.LivingArchiveApi || !businessDate) return;
    state.calendar.selected = businessDate;
    renderCalendar();
    put('selectedReportDate', `${businessDate} · 读取中`);
    $('#reportSheet').innerHTML = '<div class="empty-inline">正在读取 Foundation 日记数据…</div>';
    try {
      let payload = state.diaryCache.get(businessDate);
      if (!payload) {
        payload = await window.LivingArchiveApi.diary(businessDate);
        state.diaryCache.set(businessDate, payload);
      }
      renderReportSheet();
    } catch (error) {
      put('selectedReportDate', `${businessDate} · 不可用`);
      $('#reportSheet').innerHTML = `<div class="empty-state"><b>无法读取这一天的日记</b><p>${escapeHtml(error?.message || '日记服务暂不可用')}</p></div>`;
    }
  }

  function renderLiveDiarySheet(payload) {
    const tab = state.reportTab;
    const dateLabel = `${payload.date || state.calendar.selected}${payload.dayOfWeek ? ` · ${payload.dayOfWeek}` : ''}`;
    put('selectedReportDate', dateLabel);
    if (tab === 'narrative') {
      const weather = payload.weather ? `<section class="diary-detail-section diary-weather"><h3>天气</h3><p>${escapeHtml(safeDiaryText(typeof payload.weather === 'string' ? payload.weather : payload.weather.description || payload.weather.summary || '已记录'))}</p></section>` : '';
      $('#reportSheet').innerHTML = `<div class="report-eyebrow">WORK DIARY · FOUNDATION</div><h2>${escapeHtml(payload.displayDate || payload.date || '日记')}</h2>${weather}<div class="live-diary-prose">${renderSafeProse(payload.summary)}</div>${diaryKpiMarkup(payload)}<div class="report-provenance"><span>Foundation 日记数据</span><span>${escapeHtml(payload.languageProfile || '语言未知')}</span><span>${escapeHtml(STATUS_LABELS[payload.dashboardState?.status] || payload.dashboardState?.status || '状态未知')}</span></div>`;
      return;
    }
    if (tab === 'technical') {
      const topics = Array.isArray(payload.summaryTopics) ? payload.summaryTopics : [];
      const topicMarkup = topics.length ? topics.map(topic => `<section><h3>${escapeHtml(safeDiaryText(topic.title || '未命名主题'))}</h3>${(topic.items || []).map(item => `<p>${escapeHtml(safeDiaryText(item))}</p>`).join('')}</section>`).join('') : '<p class="lede">这一天没有结构化主题。</p>';
      const infra = diaryListMarkup('基础设施变更', payload.infraChanges, item => `${item.target || item.entityType || '对象'}：${item.change || item.current || '状态已更新'}`);
      $('#reportSheet').innerHTML = `<div class="report-eyebrow">STRUCTURED TOPICS · ${displayNumber(topics.length)}</div><h2>工程主题与成果</h2>${topicMarkup}${diaryAgentWorkMarkup(payload)}${diaryHourlyMarkup(payload)}${infra}`;
      return;
    }
    if (tab === 'learning') {
      const lessons = Array.isArray(payload.lessons) ? payload.lessons : [];
      const lessonMarkup = lessons.length ? lessons.map(item => `<section><h3>${escapeHtml(safeDiaryText(item.problem || '经验'))}</h3>${item.rootCause ? `<p><b>根因：</b>${escapeHtml(safeDiaryText(item.rootCause))}</p>` : ''}${item.suggestion ? `<p><b>可复用建议：</b>${escapeHtml(safeDiaryText(item.suggestion))}</p>` : ''}</section>`).join('') : '<p class="lede">这一天没有可显示的经验条目；Skill Pass 生成的资产可在“AI 资产”页查看。</p>';
      const reminders = diaryListMarkup('重要提醒', payload.reminders, item => `${item.title || '提醒'}${Array.isArray(item.items) && item.items.length ? `：${item.items.join('；')}` : ''}`);
      const notes = diaryListMarkup('备注', payload.notes, item => `${item.title || '备注'}${Array.isArray(item.items) && item.items.length ? `：${item.items.join('；')}` : item.text || ''}`);
      $('#reportSheet').innerHTML = `<div class="report-eyebrow">EXPERIENCE · ${displayNumber(lessons.length)}</div><h2>经验与实践</h2>${lessonMarkup}${reminders}${notes}`;
      return;
    }
    const assets = (state.archive.assets?.items || []).filter(item => item.businessDate === (payload.date || state.calendar.selected));
    const assetMarkup = assets.length ? assets.map(item => `<section><h3>${escapeHtml(TYPE_LABELS[item.type] || item.type)} · ${escapeHtml(item.title)}</h3><p>${escapeHtml(item.summary || '暂无可显示的摘要')}</p></section>`).join('') : '<p class="lede">这一天暂无 Skill Pass 资产记录。</p>';
    const dailyStats = payload.dailyStats && typeof payload.dailyStats === 'object' ? diaryKpiMarkup({ parsedKpi: payload.dailyStats }) : '';
    const knowledge = payload.ragStatsSnapshot || payload.memoryStatsSnapshot ? `<section class="diary-detail-section"><h3>记忆与索引</h3><div class="diary-kpi-grid"><div><span>Local FTS 文档</span><b>${escapeHtml(displayNumber(payload.memoryStatsSnapshot?.sessionFiles ?? payload.memoryStatsSnapshot?.diaryCount))}</b></div><div><span>RAG 索引条目</span><b>${escapeHtml(displayNumber(payload.ragStatsSnapshot?.entries))}</b></div></div></section>` : '';
    $('#reportSheet').innerHTML = `<div class="report-eyebrow">AI ASSETS · ${displayNumber(assets.length)}</div><h2>当天生成的 AI 资产</h2>${assetMarkup}${dailyStats}${knowledge}`;
  }

  function renderCalendar() {
    const { year, month, selected } = state.calendar;
    const monthLabel = `${year} · ${String(month + 1).padStart(2,'0')}`;
    $('#calendarMonth').textContent = monthLabel;
    const available = new Set(diaryItems().map(item => item.fullDate));
    const assetDates = new Set((state.archive.assets?.items || []).map(item => item.businessDate).filter(Boolean));
    const monthPrefix = `${year}-${String(month + 1).padStart(2, '0')}`;
    put('calendarMonthCount', `${displayNumber(Array.from(available).filter(value => value.startsWith(monthPrefix)).length)} 天有日记`);
    const first = new Date(year, month, 1);
    const days = new Date(year, month + 1, 0).getDate();
    const mondayOffset = (first.getDay() + 6) % 7;
    const cells = [];
    for (let index = 0; index < mondayOffset; index += 1) cells.push('<span aria-hidden="true"></span>');
    for (let day = 1; day <= days; day += 1) {
      const date = `${year}-${String(month + 1).padStart(2,'0')}-${String(day).padStart(2,'0')}`;
      const hasDiary = available.has(date);
      const hasAsset = assetDates.has(date);
      const classes = [date === selected ? 'active' : '', hasDiary ? 'has-diary' : '', hasAsset ? 'has-report' : ''].filter(Boolean).join(' ');
      cells.push(`<button class="${classes}" type="button" data-calendar-date="${date}" aria-label="${date}${hasDiary ? '，有日记' : hasAsset ? '，有资产记录' : ''}">${day}</button>`);
    }
    $('#archiveCalendar').innerHTML = cells.join('');
  }

  function setArchivePeriod(period) {
    const match = /^(\d{4})-(\d{2})$/.exec(period || '');
    if (!match) return;
    state.calendar.year = Number(match[1]);
    state.calendar.month = Number(match[2]) - 1;
    const matchInMonth = diaryItems().filter(item => item.fullDate.startsWith(period)).sort((a, b) => b.fullDate.localeCompare(a.fullDate))[0];
    state.calendar.selected = matchInMonth?.fullDate || `${period}-01`;
    state.reportTab = 'narrative';
    renderCalendar();
    if (matchInMonth) loadDiary(matchInMonth.fullDate);
    else renderReportSheet();
  }

  function renderReportSheet() {
    const selected = state.calendar.selected;
    const payload = state.diaryCache.get(selected);
    const availableDiary = diaryItems().some(item => item.fullDate === selected);
    if (!availableDiary) {
      $('#selectedReportDate').textContent = `${selected} · 无日记`;
      $('#reportSheet').innerHTML = `<div class="report-eyebrow">NO DATA</div><h2>这一天没有可显示的日记或报告</h2><p class="lede">当天可能有活动记录，但尚未生成可显示的结构化日记或 AI 资产。</p>`;
      return;
    }
    $$('.report-tabs button').forEach(button => {
      button.disabled = false;
      button.classList.toggle('active', button.dataset.reportTab === state.reportTab);
    });
      if (!payload) {
        put('selectedReportDate', `${selected} · 读取中`);
        $('#reportSheet').innerHTML = '<div class="empty-inline">正在读取 Foundation 日记数据…</div>';
        return;
      }
      renderLiveDiarySheet(payload);
      window.dispatchEvent(new CustomEvent('archive-diary-loaded', { detail: payload }));
  }

  function switchTabs(selector, panelSelector, key, value) {
    $$(selector).forEach(button => button.classList.toggle('active', button.dataset[key] === value));
    $$(panelSelector).forEach(panel => {
      const active = panel.dataset[key.replace('Tab','Panel')] === value;
      panel.hidden = !active;
      panel.classList.toggle('active', active);
    });
  }

  function startSync(button) {
    const panel = $('#syncProgress');
    const bar = panel.querySelector('div');
    const list = panel.querySelector('ol');
    panel.hidden = false;
    button.disabled = true;
    const stages = [
      [12,'读取已检测 Agent Runtime…'],
      [38,'检查 Foundation 来源状态…'],
      [67,'核对结构化日记与报告…'],
      [88,'检查 Local FTS 索引更新时间…'],
      [100,'流程预览完成；未修改本机数据。']
    ];
    let index = 0;
    clearInterval(state.syncTimer);
    const advance = () => {
      const [progress, message] = stages[index];
      bar.style.setProperty('--progress', `${progress}%`);
      list.innerHTML = `<li>${escapeHtml(message)}</li>`;
      index += 1;
      if (index >= stages.length) {
        clearInterval(state.syncTimer);
        button.disabled = false;
        button.textContent = '再次演示';
        showToast('同步演示完成，未修改本机数据。');
      }
    };
    advance();
    state.syncTimer = setInterval(advance, 520);
  }

  function drawShareCanvas() {
    const canvas = document.createElement('canvas');
    canvas.width = 1080;
    canvas.height = 1560;
    const ctx = canvas.getContext('2d');
    const night = $('#sharePreview').classList.contains('night');
    ctx.fillStyle = night ? '#092036' : '#f4efe6';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = night ? '#f4efe6' : '#092036';
    ctx.font = '700 28px sans-serif';
    ctx.fillText('ACTANARA · LIVING ARCHIVE', 88, 105);
    ctx.fillStyle = '#b6523c';
    ctx.fillRect(88, 134, 904, 7);
    ctx.font = '700 22px monospace';
    ctx.fillText($('#shareDate')?.textContent || new Date().toLocaleDateString('en-GB'), 88, 220);
    ctx.fillStyle = night ? '#f4efe6' : '#092036';
    ctx.font = '500 82px serif';
    const headline = ($('#sharePreview h3')?.innerText || '当前共有\n可复用的经验、实践\n与 Skill。').split(/\n+/).filter(Boolean).slice(0, 3);
    headline.forEach((line, index) => ctx.fillText(line, 88, 365 + index * 102));
    const stats = [
      [$('#shareExperienceCount')?.textContent || '—', '经验 Experience'],
      [$('#sharePracticeCount')?.textContent || '—', '实践 Practice'],
      [$('#shareSkillCount')?.textContent || '—', 'Skill 草案']
    ];
    stats.forEach(([value,label], index) => {
      const x = 88 + index * 306;
      ctx.strokeStyle = night ? '#7f8e99' : '#c8c1b5';
      ctx.strokeRect(x, 750, 282, 170);
      ctx.font = '500 54px serif';
      ctx.fillText(value, x + 20, 825);
      ctx.font = '400 23px sans-serif';
      ctx.fillText(label, x + 20, 870);
    });
    ctx.strokeStyle = '#6652e8';
    ctx.lineWidth = 5;
    ctx.beginPath(); ctx.moveTo(88, 1045); ctx.lineTo(88, 1290); ctx.stroke();
    ctx.font = '500 34px serif';
    const quote = `“${$('#dailyQuoteText')?.textContent || '把今天的工作，变成明天可以复用的能力。'}”`;
    const quoteLines = quote.match(/.{1,25}/g) || [quote];
    quoteLines.slice(0, 3).forEach((line, index) => ctx.fillText(line, 125, 1110 + index * 48));
    ctx.font = '400 20px monospace';
    ctx.fillStyle = night ? '#c5cdd2' : '#5e686b';
    ctx.fillText('LOCAL FIRST · PRIVACY SAFE AGGREGATE', 125, 1190);
    return canvas;
  }

  function downloadShare() {
    const canvas = drawShareCanvas();
    canvas.toBlob(blob => {
      if (!blob) return showToast('无法生成分享图片', 'danger');
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = `actanara-living-archive-${new Date().toISOString().slice(0, 10)}.png`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 1000);
      showToast('分享图片已下载');
    }, 'image/png');
  }

  async function copyShare() {
    const canvas = drawShareCanvas();
    const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
    try {
      if (!navigator.clipboard || !window.ClipboardItem || !blob) throw new Error('clipboard unavailable');
      await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
      showToast('分享图片已复制到剪贴板');
    } catch (_) {
      showToast('浏览器未授予图片剪贴板权限，请改用“下载图片”。', 'warning');
    }
  }

  function reorderProvider(button) {
    const row = button.closest('[data-provider]');
    const direction = button.dataset.move;
    if (direction === 'up' && row.previousElementSibling) row.parentNode.insertBefore(row, row.previousElementSibling);
    if (direction === 'down' && row.nextElementSibling) row.parentNode.insertBefore(row.nextElementSibling, row);
    const providers = $$('#providerList > div');
    providers.forEach((item, index) => {
      item.querySelector('span').textContent = String(index + 1).padStart(2,'0');
      item.querySelector('small').textContent = `${index === 0 ? 'Primary' : `Fallback ${index}`} · ${item.dataset.configured === 'true' ? 'configured' : 'not configured'}`;
      item.querySelector('[data-move="up"]').disabled = index === 0;
      item.querySelector('[data-move="down"]').disabled = index === providers.length - 1;
    });
    showToast('备用服务顺序已调整（仅当前预览有效）。');
  }

  function handleAction(action, button) {
    switch (action) {
      case 'retry-archive': loadArchiveData({ announce: true }); break;
      case 'start-sync': startSync(button); break;
      case 'copy-share': copyShare(); break;
      case 'download-share': downloadShare(); break;
      case 'create-backup':
        button.disabled = true; button.textContent = '正在创建并校验备份…';
        setTimeout(() => { button.disabled = false; button.textContent = '创建并校验备份'; showToast('备份演示完成，未创建实际文件。'); }, 1100);
        break;
      case 'restore-backup': $('#restoreWarning').hidden = false; showToast('已显示恢复范围，请确认后继续。', 'warning'); break;
      case 'confirm-restore': showToast('恢复演示完成，未修改本机数据。'); closeOverlays(); break;
      case 'save-schedule': showToast('自动备份计划仅保存于当前预览，刷新后会重置。'); break;
      case 'save-settings': showToast('设置已暂存，刷新页面后会重置。'); closeOverlays(); break;
      case 'configure-rag': showToast('nova-RAG 尚未启用；仍可使用 Local FTS 关键词搜索。', 'warning'); break;
      case 'browse-path': showToast('当前仅演示路径管理；正式版将通过受限的路径选择器选取目录。'); break;
      case 'refresh-foundation':
        button.disabled = true; button.textContent = '读取中…';
        loadArchiveData({ announce: true }).finally(() => { button.disabled = false; button.textContent = '重新读取'; });
        break;
      default: showToast('这是预览功能，未执行实际操作。');
    }
  }

  document.addEventListener('click', event => {
    const target = event.target.closest('button, a, article[data-detail]');
    if (!target) return;

    if (target.matches('[data-close]')) { closeOverlays(); return; }
    if (target.matches('[data-close-drawer]')) { closeOverlays(); return; }
    if (target.dataset.route) {
      event.preventDefault();
      navigate(target.dataset.route, { period: target.dataset.period, focus: target.dataset.focus });
      return;
    }
    if (target.dataset.resultRoute) { closeOverlays(); navigate(target.dataset.resultRoute, { period: target.dataset.resultPeriod }); return; }
    if (target.dataset.assetId) { event.preventDefault(); openArchiveAsset(target.dataset.assetId); return; }
    if (target.dataset.modal) { event.preventDefault(); openModal(target.dataset.modal); return; }
    if (target.dataset.agent) { event.preventDefault(); openAgentDrawer(target.dataset.agent); return; }
    if (target.dataset.detail) { event.preventDefault(); openDetail(target.dataset.detail); return; }
    if (target.dataset.drawerRoute) { closeOverlays(); navigate(target.dataset.drawerRoute); return; }
    if (target.dataset.action) { handleAction(target.dataset.action, target); return; }
    if (target.dataset.assetFilter) { applyAssetFilter(target.dataset.assetFilter); return; }
    if (target.dataset.chartMode) {
      $$('.chart-switch [data-chart-mode]').forEach(button => button.classList.toggle('active', button === target));
      renderAssetGrowth(target.dataset.chartMode);
      return;
    }
    if (target.dataset.searchSource) {
      state.memorySource = target.dataset.searchSource;
      $$('[data-search-source]').forEach(button => button.classList.toggle('active', button === target));
      renderMemoryResults($('#memorySearchInput').value);
      return;
    }
    if (target.dataset.backend) {
      $$('.backend-switch [data-backend]').forEach(button => button.classList.toggle('active', button === target));
      if (target.dataset.backend === 'rag') showToast('nova-RAG 尚未启用；当前仍使用 Local FTS 关键词搜索。', 'warning');
      else showToast('已切换到 Local FTS 本地全文搜索。');
      return;
    }
    if (target.matches('[data-toggle]')) {
      target.classList.toggle('is-on');
      showToast(`开关已${target.classList.contains('is-on') ? '开启' : '关闭'}（只影响当前预览）`);
      return;
    }
    if (target.dataset.globalFilter) {
      state.globalFilter = target.dataset.globalFilter;
      $$('[data-global-filter]').forEach(button => button.classList.toggle('active', button === target));
      renderGlobalResults($('#globalSearchInput').value);
      return;
    }
    if (target.dataset.shareTemplate) {
      $$('[data-share-template]').forEach(button => button.classList.toggle('active', button === target));
      const headings = { daily: '当前共有<br>可复用的经验、实践<br>与 Skill。', weekly: '本周工作已整理为<br>可搜索、可复用的<br>经验与 Skill。', memory: '跨 Agent 记忆<br>无需启用语义搜索<br>也能按关键词查找。' };
      $('#sharePreview h3').innerHTML = headings[target.dataset.shareTemplate];
      return;
    }
    if (target.dataset.shareTheme) {
      $$('[data-share-theme]').forEach(button => button.classList.toggle('active', button === target));
      $('#sharePreview').classList.toggle('night', target.dataset.shareTheme === 'night');
      return;
    }
    if (target.dataset.backupTab) {
      switchTabs('[data-backup-tab]', '[data-backup-panel]', 'backupTab', target.dataset.backupTab);
      return;
    }
    if (target.dataset.settingsTab) {
      switchTabs('[data-settings-tab]', '[data-settings-panel]', 'settingsTab', target.dataset.settingsTab);
      return;
    }
    if (target.dataset.move) { reorderProvider(target); return; }
    if (target.dataset.palette) {
      $$('[data-palette]').forEach(button => button.classList.toggle('active', button === target));
      document.documentElement.dataset.palette = target.dataset.palette;
      showToast(`已预览 ${target.querySelector('b').textContent} 配色`);
      return;
    }
    if (target.dataset.range) {
      $$('[data-range]').forEach(button => button.classList.toggle('active', button === target));
      const labels = { today: '已显示今天的小时活动', '30d': '已显示最近 30 天的趋势', all: '已显示按 Agent 和模型汇总的全部历史' };
      showToast(labels[target.dataset.range]);
      if (target.dataset.range === 'today') $('#hourlyUsageChart').scrollIntoView({ behavior: 'smooth', block: 'center' });
      if (target.dataset.range === 'all') $('.token-source-bars').scrollIntoView({ behavior: 'smooth', block: 'center' });
      return;
    }
    if (target.dataset.calendarShift) {
      state.calendar.month += target.dataset.calendarShift === 'next' ? 1 : -1;
      if (state.calendar.month > 11) { state.calendar.month = 0; state.calendar.year += 1; }
      if (state.calendar.month < 0) { state.calendar.month = 11; state.calendar.year -= 1; }
      renderCalendar();
      return;
    }
    if (target.dataset.calendarDate) {
      state.calendar.selected = target.dataset.calendarDate;
      state.reportTab = 'narrative';
      renderCalendar();
      if (diaryItems().some(item => item.fullDate === target.dataset.calendarDate)) loadDiary(target.dataset.calendarDate);
      else renderReportSheet();
      return;
    }
    if (target.dataset.reportTab) {
      if (target.disabled) return;
      state.reportTab = target.dataset.reportTab;
      renderReportSheet();
      return;
    }
    if (target.dataset.reportType) {
      $$('[data-report-type]').forEach(button => button.classList.toggle('active', button === target));
      showToast(`报告类型：${target.textContent}`);
    }
  });

  $('#modalBackdrop').addEventListener('click', () => closeOverlays());
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !$('#modalBackdrop').hidden) closeOverlays();
    if ((event.key === 'Enter' || event.key === ' ') && event.target.matches('article[data-detail], .global-result')) event.target.click();
  });

  $('#memorySearchForm').addEventListener('submit', async event => {
    event.preventDefault();
    const query = $('#memorySearchInput').value.trim();
    const submit = event.currentTarget.querySelector('[type="submit"]');
    if (!query || !window.LivingArchiveApi) return;
    submit.disabled = true;
    submit.textContent = '搜索中…';
    try {
      const payload = await window.LivingArchiveApi.searchMemory(query, state.memorySource);
      renderLiveMemoryResults(payload, query);
      showToast(payload?.available === false ? '本地记忆当前不可用' : '本地记忆搜索完成', payload?.available === false ? 'warning' : 'success');
    } catch (error) {
      const container = $('#memorySearchResults');
      if (container) container.innerHTML = `<div class="search-result"><h3>搜索失败</h3><p>${escapeHtml(error?.message || 'Local FTS 暂不可用')}</p></div>`;
      showToast('本地记忆搜索失败', 'danger');
    } finally {
      submit.disabled = false;
      submit.textContent = '搜索';
    }
  });
  $('#globalSearchForm').addEventListener('submit', event => {
    event.preventDefault();
    renderGlobalResults($('#globalSearchInput').value);
  });
  window.addEventListener('popstate', routeFromHash);

  initializeArchiveDate();
  renderHomeEvidence();
  renderAssetGrowth();
  renderUsage30d();
  renderHourlyUsage();
  $('#memorySearchResults').innerHTML = '<div class="search-result"><h3>输入关键词开始搜索</h3><p>默认使用 Local FTS 本地全文搜索；启用 nova-RAG 后也可使用语义搜索。</p></div>';
  renderGlobalResults($('#globalSearchInput').value);
  renderCalendar();
  renderReportSheet();
  routeFromHash();
  loadArchiveData();
})();
