(() => {
  'use strict';

  const ROUTES = new Set(['home', 'assets', 'agents', 'memory', 'usage', 'reports', 'foundation']);
  const routeTitles = {
    home: '今日档案',
    assets: 'AI 资产',
    agents: 'Agent 来源',
    memory: '记忆索引',
    usage: '历史用量',
    reports: '档案柜',
    foundation: 'Foundation'
  };

  const evidenceData = [
    ['07-18',1122], ['07-19',1075], ['07-20',1885], ['07-21',743], ['07-22',4145],
    ['07-23',490], ['07-24',320], ['07-25',96], ['07-26',158], ['07-27',130],
    ['07-28',2785], ['07-30',479], ['07-31',126], ['08-01',765], ['08-02',3042],
    ['08-03',335], ['08-04',1020], ['08-05',2068], ['08-06',3622], ['08-07',395]
  ];

  const reportsDaily = [
    ['07-19',6], ['07-20',4], ['07-21',6], ['07-22',6], ['07-23',6], ['07-24',6], ['07-25',6],
    ['07-26',6], ['07-27',4], ['07-28',6], ['07-29',6], ['07-30',6], ['07-31',6], ['08-01',4],
    ['08-02',6], ['08-03',4], ['08-04',6], ['08-05',6], ['08-06',6], ['08-07',6], ['08-08',6]
  ];

  const usage30d = [
    ['07-10',222045,4210457,238638,225337], ['07-11',223202,223931,332748,247012],
    ['07-12',223198,270975,270982,135940], ['07-13',202035,176376,266373,223446],
    ['07-14',274090,292959,248820,272372], ['07-15',0,199679,269108,110591],
    ['07-16',84487,0,0,0], ['07-17',1014531,17460294,0,30611770],
    ['07-18',57927885,126611972,582500,57704534], ['07-19',53423473,24713442,88519046,39986900],
    ['07-20',6885719,115212166,8198683,84736178], ['07-21',193007470,181056,608369,363525794],
    ['07-22',92191956,14541630,365877,51537239], ['07-23',4520842,2431832,353170,35714886],
    ['07-24',237920,176520,338887,6384410], ['07-25',174683,251660,325206,6034673],
    ['07-26',12114419,6437739,254339,5000683], ['07-27',1934557101,0,0,890191783],
    ['07-28',2373276036,1079822789,317793,2709898], ['07-29',214401,2515882445,52375974,4884425],
    ['07-30',172827,235865,206035,1077154097], ['07-31',6239581,131786,705312,5286201],
    ['08-01',530017220,249956,372115,209478], ['08-02',14466719,3809141749,562212,19730344],
    ['08-03',2032423,43243449,286608,76395416], ['08-04',113394924,26753319,191219,18436017],
    ['08-05',63993751,242091788,7684881,126077004], ['08-06',51703,1131105105,232336577,540992],
    ['08-07',47261154,51686,51661,2728343], ['08-08',0,350209719,74616707,0]
  ];

  const hourlyUsage = [1242427385,0,0,0,0,0,0,0,0,50985,1149360,349009374,431165292,638469899,140239620,653378495,0,0,0,0,0,25569,0,0];

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
      details: [['检测状态','Detected'],['最近活动','2026-08-09 00:19'],['原生记忆','已授权'],['Instructions','纳入索引'],['用量协议','完整'],['数据形态','rollout JSONL']],
      notes: 'Token 历史保留在“历史用量”页面，不作为此来源的资产评分。'
    },
    'OpenClaw': {
      monogram: 'OC', tone: 'rust', status: '最近有记录', summary: '源产物数量最多，同时拥有较大的跨工具技能目录。',
      stats: [['源产物','879'],['Foundation Sessions','218'],['技能实例','92']],
      details: [['检测状态','Detected'],['最近活动','2026-08-08 21:04'],['原生记忆','未纳入'],['Agent identities','6 visible'],['用量协议','完整'],['数据形态','session JSONL']],
      notes: '来源规模较大；需要继续区分原始 Session 与可复用结构化成果。'
    },
    'Claude Code': {
      monogram: 'CL', tone: 'teal', status: '馆藏', summary: '本地项目记录、原生记忆和 Skills 均已被识别。',
      stats: [['源产物','109'],['Foundation Sessions','11'],['技能实例','19']],
      details: [['检测状态','Detected'],['最近活动','2026-08-05'],['原生记忆','已授权'],['Instructions','纳入索引'],['用量协议','完整'],['数据形态','session JSONL']],
      notes: '原生记忆只在用户授权范围内进入 Local FTS。'
    },
    'Gemini CLI': {
      monogram: 'GM', tone: 'ochre', status: '馆藏', summary: '已识别本地项目配置与历史 Session。',
      stats: [['源产物','9'],['Sessions','8'],['活跃日','19']],
      details: [['检测状态','Detected'],['最近活动','2026-05-23'],['原生记忆','未启用'],['用量协议','完整'],['数据形态','session JSON'],['Token 可用','是']],
      notes: '当前没有近期活动，仍保留其馆藏来源位置。'
    },
    'Hermes': {
      monogram: 'HE', tone: 'green', status: '馆藏', summary: '主要资产贡献是可复用技能目录。',
      stats: [['Inventory','1'],['Sessions','4'],['技能实例','243']],
      details: [['检测状态','Detected'],['最近活动','2026-08-06'],['原生记忆','未启用'],['用量协议','完整'],['数据形态','local inventory'],['技能覆盖','最大']],
      notes: '技能实例数量不等于独立技能数量；跨工具安装会重复计数。'
    },
    'Antigravity': {
      monogram: 'AG', tone: 'plum', status: '本地部分数据', summary: '多个 variant 在呈现层合并为一个工具来源。',
      stats: [['Inventory','1'],['Sessions','4'],['Variants','Merged']],
      details: [['检测状态','Detected'],['最近活动','2026-08-05'],['Variant 呈现','统一合并'],['用量协议','Local partial'],['Token 可用','部分'],['API 请求','不需要']],
      notes: 'CLI、IDE 或 App 变体仅作为 provenance，不拆成多个顶层卡片。'
    },
    'OpenCode': {
      monogram: 'OP', tone: 'blue', status: '已识别', summary: '从本地 SQLite 与 runtime inventory 获取可用信息。',
      stats: [['Inventory','1'],['Sessions','2'],['消息','2']],
      details: [['检测状态','Detected'],['最近活动','2026-06-11'],['原生记忆','未启用'],['用量协议','完整'],['数据形态','SQLite'],['Token 可用','少量']],
      notes: '当前样本较少，页面明确标注数据稀疏。'
    },
    'Cursor': {
      monogram: 'CR', tone: 'neutral', status: '仅 Session', summary: '只展示本地可获得的信息，不调用 Cursor API。',
      stats: [['Inventory','1'],['Sessions','5'],['Token','N/A']],
      details: [['检测状态','Detected'],['最近活动','2026-06-12'],['本地 Session','可用'],['API 请求','未使用'],['用量协议','不可用'],['Token 可用','否']],
      notes: '在获得可靠本地或官方数据前，不推断 Token 消耗。'
    },
    'Other sources': {
      monogram: '+', tone: 'blue', status: '聚合视图', summary: 'Cron、Gemini CLI、Hermes、OpenCode、Antigravity 与 Cursor 的聚合来源。',
      stats: [['源产物','39'],['来源','6'],['Inventory','3']],
      details: [['Cron JSONL','26'],['Gemini JSON','9'],['SQLite sessions','1'],['Local inventory','3'],['解析错误','0'],['覆盖状态','Mixed']],
      notes: '可点击单独的 Agent 卡片查看每种来源的可靠性边界。'
    }
  };

  const reportContent = {
    narrative: {
      kicker: 'NARRATIVE DIARY · 21 SECTIONS',
      title: '系统演进与工作脉络',
      lede: '这一天完成了 Actanara 的品牌与运行时迁移，也把“持续存在的 Agent 员工”从想法收敛为可审计的工作流内核。',
      sections: [
        ['当天发生了什么', '旧系统完成退役，新运行时开始监听本地 Dashboard。安装、迁移、品牌与发布流程被组织为同一条可追溯事件链。'],
        ['形成的资产', '三类日记、结构化章节、基础设施实体和事件被写入 Foundation；重要架构决策进入可检索记忆。'],
        ['下一步', '继续提高日记投影的新鲜度，并让更多 Runtime 的本地信息进入统一来源目录。']
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
      lede: 'Foundation 完成周期资产投影：1,406 个来源进入缓存，39 条工作区归属观察完成解析，最近一次任务以零错误结束。',
      sections: [
        ['新增与覆盖', '周期报告持续生成；8 种 runtime 均已检测，其中 Cursor 只使用本地 Session 信息，Antigravity variants 在展示层合并。'],
        ['检索状态', 'Local FTS 仍可检索 2,019 条文档，但索引停留在 07-29，需要重新同步。nova-RAG 仍为 Not Now。'],
        ['维护提示', 'AI Assets 投影已 Ready；Period Assets 与 Period Page 尚未请求，完整度显示为 1/3 required。']
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
    ready: 'Ready', empty: 'Empty', degraded: 'Degraded', unsupported: 'Unsupported',
    stale: 'Stale', error: 'Error', loading: 'Loading'
  };
  const TYPE_LABELS = { experience: '经验', practice: '实践', skill: 'Skill' };
  const TYPE_TAGS = { experience: 'rust-tag', practice: 'teal-tag', skill: 'violet-tag' };

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

  function setArchiveState(status, title, detail, canRetry = false) {
    const panel = $('#archiveState');
    if (!panel) return;
    const normalized = STATUS_LABELS[status] ? status : 'degraded';
    panel.className = `archive-state ${normalized}`;
    panel.querySelector(':scope > div > b').textContent = title;
    panel.querySelector(':scope > div > span').textContent = detail;
    const retry = panel.querySelector('[data-action="retry-archive"]');
    if (retry) retry.hidden = !canRetry;
    put('archiveLiveLabel', normalized === 'ready' ? '本地档案在线' : normalized === 'loading' ? '正在连接本地档案' : '本地档案部分可用');
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
    const dateLabel = new Intl.DateTimeFormat('en-US', { weekday: 'long', day: '2-digit', month: 'long', year: 'numeric' }).format(now);
    put('archiveTodayLabel', `${dateLabel} · LOCAL ARCHIVE`);
    put('mobileArchiveDate', `${String(now.getMonth() + 1).padStart(2, '0')}.${String(now.getDate()).padStart(2, '0')}.${now.getFullYear()}`);
    put('archiveVolume', `VOL. ${String(Math.floor((now - new Date(now.getFullYear(), 0, 0)) / 86400000)).padStart(3, '0')}`);
    put('shareDate', new Intl.DateTimeFormat('en-US', { day: '2-digit', month: 'long', year: 'numeric' }).format(now).toUpperCase());
  }

  function hydrateQuote(quote) {
    const fallback = [
      ['把今天的工作，变成明天可以复用的能力。', 'ACTANARA · LIVING ARCHIVE'],
      ['经验保存因果，实践保存验证，Skill 保存可调用的方法。', 'ACTANARA · SKILL PASS'],
      ['真正的馆藏不是消耗，而是下一次仍然有效的判断。', 'ACTANARA · DAILY NOTE']
    ];
    const selected = quote?.text ? [quote.text, quote.source || quote.author || '馆藏摘录'] : fallback[new Date().getDate() % fallback.length];
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
    put('homeSkillMeta', `Draft ${displayNumber(draft)} · Active ${displayNumber(active)}`);
    put('assetSkillBreakdown', `Draft ${displayNumber(draft)} · Active ${displayNumber(active)}`);
    put('skillStateTotal', `${displayNumber(skill)} drafts`);
    put('navAssetCount', all === null ? bootstrap.status : `${displayNumber(all)} assets`);
    put('navAgentCount', `${displayNumber((bootstrap.agents || []).length)} tools`);
    put('navMemoryCount', displayNumber(memory));
    put('navFoundationStatus', bootstrap.status || 'unknown');
    put('reportDiaryDays', displayNumber(diary));
    put('reportDocumentCount', displayNumber(reports));
    put('reportAssetCount', displayNumber(all));
    put('archiveAssetFreshness', `资产快照 · ${freshnessText(bootstrap.dataFreshness)}`);
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
    put('foundationSeal', status === 'ready' ? 'READY' : status === 'stale' ? 'STALE' : status === 'degraded' ? 'CHECK' : 'EMPTY');
    put('foundationStatusTitle', status === 'ready' ? 'Foundation 安全投影可用' : status === 'stale' ? 'Foundation 可读但快照较旧' : status === 'degraded' ? 'Foundation 部分可用' : 'Foundation 暂无投影');
    put('foundationStatusDetail', `Archive API · ${freshnessText(bootstrap.dataFreshness)}`);
    setStatusPill($('#foundationFreshnessPill'), status);
    setStatusPill($('#foundationSourcePill'), (bootstrap.sourceErrors || []).length ? 'degraded' : status);
    const skillStatus = bootstrap.dataFreshness?.skillPass?.status || 'unsupported';
    const foundationStatus = bootstrap.dataFreshness?.foundation?.status || status;
    const local = memory?.backends?.local || {};
    const localStatus = local.available ? (local.backend?.stale ? 'stale' : 'ready') : 'unsupported';
    const timeline = $('#foundationProjectionTimeline');
    if (timeline) timeline.innerHTML = [
      ['01', 'Archive API', status],
      ['02', 'Skill Pass ledger', skillStatus],
      ['03', 'Foundation snapshot', foundationStatus],
      ['04', 'Local FTS', localStatus]
    ].map(([step, label, stepStatus]) => `<div class="${stepStatus === 'ready' ? 'done' : ''}"><i></i><b>${step}</b><span>${label}<small>${STATUS_LABELS[stepStatus] || stepStatus}</small></span></div>`).join('');
    const health = $('#foundationSourceHealth');
    if (health) health.innerHTML = [
      ['Archive asset projection', 'Experience · Practice · Skill', status],
      ['Skill Pass ledger', `${displayNumber(bootstrap.summary?.assetCount)} projected assets`, skillStatus],
      ['Foundation inventory', `${displayNumber(documents)} structured documents`, foundationStatus],
      ['Local FTS', `${displayNumber(local.documentCount)} documents`, localStatus]
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
      ledger.innerHTML = `${header}<div class="ledger-state-row">资产 API 暂不可用；没有用样稿数字替代。</div>`;
      return;
    }
    if (!items.length) {
      const message = payload.status === 'degraded' ? '这一层数据读取不完整，请稍后重试。' : '这层书架目前为空；空库存不会显示成未知错误。';
      ledger.innerHTML = `${header}<div class="ledger-state-row">${message}</div>`;
      return;
    }
    ledger.innerHTML = header + items.map(item => {
      const evidence = numeric(item?.metrics?.evidenceCount);
      return `<button type="button" data-asset-id="${escapeHtml(item.id)}"><span class="type-tag ${TYPE_TAGS[item.type] || 'blue-tag'}">${escapeHtml(TYPE_LABELS[item.type] || item.type)}</span><b>${escapeHtml(item.title || '未命名资产')}</b><span class="status-text ${item.status === 'verified' ? 'good-text' : item.status === 'draft' ? 'warn-text' : ''}">${escapeHtml(item.status || 'unknown')}</span><time>${escapeHtml(shortDate(item.businessDate))}</time><span>${evidence === null ? '证据 —' : `${displayNumber(evidence)} evidence`}</span></button>`;
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
    const remote = items.map(item => ({ type: item.type === 'skill' ? 'skills' : 'memory', label: TYPE_LABELS[item.type], title: item.title, meta: `${item.businessDate || 'date unknown'} · ${item.status || 'unknown'}`, assetId: item.id, remote: true }));
    for (let index = globalCatalog.length - 1; index >= 0; index -= 1) if (globalCatalog[index].remote) globalCatalog.splice(index, 1);
    globalCatalog.push(...remote);
    renderGlobalResults($('#globalSearchInput')?.value || '');
    const arrivals = $('#latestArrivals');
    if (arrivals) arrivals.innerHTML = items.length ? items.slice(0, 5).map(item => `<li><time>${escapeHtml(shortDate(item.businessDate))}</time><i class="event-dot ${item.type === 'skill' ? 'violet-bg' : item.type === 'practice' ? 'teal-bg' : 'rust-bg'}"></i><div><b>${escapeHtml(item.title || '未命名资产')}</b><span>${escapeHtml(TYPE_LABELS[item.type] || item.type)} · ${escapeHtml(item.status || 'unknown')}</span></div></li>`).join('') : '<li><time>—</time><i class="event-dot neutral-bg"></i><div><b>书架目前为空</b><span>Archive API</span></div></li>';
  }

  function renderSkillPassRuns(payload) {
    const runs = Array.isArray(payload?.items) ? payload.items : [];
    const chart = $('#skillPassChart');
    if (chart) {
      if (!runs.length) chart.innerHTML = `<div class="empty-state"><b>${payload?.status === 'empty' ? '尚无 Skill Pass 运行' : '运行账簿不可用'}</b><p>缺失状态不会用虚构柱图填充。</p></div>`;
      else {
        const max = Math.max(1, ...runs.flatMap(run => ['experience', 'practice', 'skill'].map(key => numeric(run?.counts?.[key]) || 0)));
        chart.innerHTML = runs.slice().reverse().map(run => `<div class="skill-run-column" title="${escapeHtml(run.businessDate)} · ${escapeHtml(run.promptVersion)}"><i style="height:${Math.max(2, (numeric(run?.counts?.experience) || 0) / max * 100)}%"></i><i style="height:${Math.max(2, (numeric(run?.counts?.practice) || 0) / max * 100)}%"></i><i style="height:${Math.max(2, (numeric(run?.counts?.skill) || 0) / max * 100)}%"></i><small>${escapeHtml(shortDate(run.businessDate))}</small></div>`).join('');
      }
    }
    put('skillPassRunRange', runs.length ? `${shortDate(runs[runs.length - 1]?.businessDate)} — ${shortDate(runs[0]?.businessDate)}` : STATUS_LABELS[payload?.status] || 'Unavailable');
    const home = $('#homeEvidenceBars');
    if (home) {
      const ordered = runs.slice().reverse().slice(-20);
      const totals = ordered.map(run => ['experience', 'practice', 'skill'].reduce((sum, key) => sum + (numeric(run?.counts?.[key]) || 0), 0));
      const max = Math.max(1, ...totals);
      home.style.gridTemplateColumns = `repeat(${Math.max(1, ordered.length)}, minmax(8px, 1fr))`;
      home.innerHTML = ordered.length ? ordered.map((run, index) => `<span class="bar-wrap" title="${escapeHtml(run.businessDate)} · ${totals[index]} assets"><i style="height:${Math.max(3, totals[index] / max * 100)}%"></i><small>${escapeHtml(shortDate(run.businessDate))}</small></span>`).join('') : '<span class="empty-inline">尚无运行记录</span>';
      const peak = totals.length ? totals.indexOf(max) : -1;
      put('homeRunTotal', `${displayNumber(runs.length)} RUNS`);
      put('homeRunPeak', peak >= 0 ? `峰值 ${shortDate(ordered[peak]?.businessDate)} · ${max}` : '峰值 —');
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
    put('detectedToolCount', displayNumber(list.length));
    put('agentLocalCount', displayNumber(list.filter(agent => agent.lastActive).length));
    put('agentCatalogEyebrow', `SOURCE CATALOG · ${displayNumber(list.length)} DETECTED`);
    put('activeAgentStamp', `${displayNumber(list.length)} AGENTS DETECTED`);
    const latest = list.map(agent => timestamp(agent.lastActive)).filter(Boolean).sort((a, b) => b - a)[0];
    put('agentLatestActivity', latest ? shortDate(latest.toISOString()) : '—');
    setStatusPill($('#agentCoverageStatus'), list.length ? 'ready' : 'empty');
    const grid = $('#agentCardGrid');
    const bars = $('#agentSourceBars');
    if (!list.length) {
      if (grid) grid.innerHTML = '<div class="empty-state"><b>没有检测到 Agent 来源</b><p>页面不会展示预设工具卡片。</p></div>';
      if (bars) bars.innerHTML = '<div class="empty-inline">没有本地来源可显示。</div>';
      return;
    }
    const now = Date.now();
    const byRecent = list.slice().sort((a, b) => (timestamp(b.lastActive)?.getTime() || 0) - (timestamp(a.lastActive)?.getTime() || 0));
    if (bars) bars.innerHTML = byRecent.slice(0, 6).map(agent => {
      const date = timestamp(agent.lastActive);
      const days = date ? Math.max(0, Math.floor((now - date.getTime()) / 86400000)) : null;
      const width = days === null ? 3 : Math.max(5, 100 - Math.min(95, days));
      const tone = agentTone(agent.name);
      return `<button type="button" data-agent="${escapeHtml(agent.name)}"><span><b>${escapeHtml(agent.name.split(/\s+/).map(part => part[0]).join('').slice(0, 2).toUpperCase())}</b>${escapeHtml(agent.name)}</span><i><b style="width:${width}%;--bar:var(--${tone === 'ochre' ? 'ochre' : tone})"></b></i><strong>${escapeHtml(shortDate(agent.lastActive))}</strong><small>${days === null ? 'unknown' : days === 0 ? 'today' : `${days}d ago`}</small></button>`;
    }).join('');
    const homeBars = $('#homeAgentFreshnessBars');
    if (homeBars) homeBars.innerHTML = byRecent.slice(0, 5).map(agent => {
      const date = timestamp(agent.lastActive);
      const days = date ? Math.max(0, Math.floor((now - date.getTime()) / 86400000)) : null;
      const width = days === null ? 3 : Math.max(5, 100 - Math.min(95, days));
      const tone = agentTone(agent.name);
      return `<button type="button" data-agent="${escapeHtml(agent.name)}"><span>${escapeHtml(agent.name)}</span><i><b style="width:${width}%;--bar:var(--${tone === 'ochre' ? 'ochre' : tone})"></b></i><strong>${escapeHtml(shortDate(agent.lastActive))}</strong></button>`;
    }).join('') || '<div class="empty-inline">没有本地来源可显示。</div>';
    if (grid) grid.innerHTML = byRecent.map(agent => {
      const tone = agentTone(agent.name);
      const monogram = agent.name.split(/\s+/).map(part => part[0]).join('').slice(0, 2).toUpperCase();
      const normalized = /antigravity/i.test(agent.name) ? 'antigravity' : /cursor/i.test(agent.name) ? 'cursor' : '';
      const boundary = normalized === 'antigravity' ? 'Variants merged · local summary' : normalized === 'cursor' ? 'Local only · no API inference' : 'Local safe summary';
      agents[agent.name] = { monogram, tone, status: agent.status || 'Detected', summary: `${agent.name} 是这台机器实际检测到的本地来源。`, stats: [['检测','Yes'],['最近',shortDate(agent.lastActive)],['范围','Local']], details: [['检测状态','Detected'],['最近活动', agent.lastActive || 'Unknown'],['数据边界', boundary],['Token / 消息','请前往历史用量']], notes: normalized === 'antigravity' ? 'Antigravity 的 CLI、IDE 与 App 变体已合并为一个来源。' : normalized === 'cursor' ? 'Cursor 仅展示本地可获得的信息，不调用 Cursor API。' : '这里只展示安全来源摘要；消耗统计位于独立历史用量页。' };
      return `<button class="agent-card" type="button" data-agent="${escapeHtml(agent.name)}" data-agent-normalized="${normalized}"><span class="agent-monogram ${tone === 'ochre' ? 'gold' : tone}-bg">${escapeHtml(monogram)}</span><div><span class="agent-state good-text">Detected locally</span><h3>${escapeHtml(agent.name)}</h3><p>${escapeHtml(boundary)}</p></div><dl><div><dt>最近可见</dt><dd>${escapeHtml(shortDate(agent.lastActive))}</dd></div><div><dt>数据状态</dt><dd>${escapeHtml(agent.status || 'available')}</dd></div><div><dt>呈现策略</dt><dd>${normalized === 'antigravity' ? 'Merged' : normalized === 'cursor' ? 'Local only' : 'Safe summary'}</dd></div></dl><span class="card-arrow">→</span></button>`;
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
      container.innerHTML = '<div class="empty-inline">每日趋势不受支持。</div>';
      put('dailyActivityTotal', 'Unsupported');
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
    put('dailyActivityPeak', `峰值 ${shortDate(rows[values.indexOf(peak)]?.date)} · ${displayNumber(peak, true)}`);
    put('dailyActivityLatest', `最近 ${shortDate(rows.at(-1)?.date)} · ${displayNumber(values.at(-1), true)}`);
  }

  function hydrateActivity(activity) {
    if (!activity) return;
    const totals = activity.totals || {};
    put('usageTokenTotal', displayNumber(totals.tokens, true));
    put('usageMessageTotal', displayNumber(totals.messages));
    put('usageSessionTotal', displayNumber(totals.sessions));
    put('usageActiveDays', displayNumber(totals.activeDays));
    put('homeUsageSummary', `${displayNumber(totals.tokens, true)} Token · ${displayNumber(totals.messages)} messages · ${displayNumber(totals.sessions)} sessions`);
    put('archiveActivityFreshness', `活动账簿 · ${freshnessText(activity.dataFreshness)}`);
    const rows = trendRows(activity);
    put('usageRangeLabel', rows.length ? `${shortDate(rows[0].date)} — ${shortDate(rows.at(-1).date)}` : 'Unsupported');
    if (rows.length) {
      usage30d.splice(0, usage30d.length, ...rows.map(row => {
        const parts = Object.values(row.slots || {}).map(value => numeric(value) || 0).slice(0, 4);
        while (parts.length < 4) parts.push(0);
        return [shortDate(row.date), ...parts];
      }));
      renderUsage30d();
    } else if ($('#usage30dChart')) $('#usage30dChart').innerHTML = '<div class="empty-inline">30 天趋势不受支持。</div>';
    renderDailyActivity(rows);
    const usageAgents = mergeDetectedAgents(activity.agents);
    const agentBars = $('#usageAgentBars');
    if (agentBars) {
      const max = Math.max(1, ...usageAgents.map(agent => numeric(agent.tokenCount) || 0));
      agentBars.innerHTML = usageAgents.length ? usageAgents.sort((a, b) => (numeric(b.tokenCount) || -1) - (numeric(a.tokenCount) || -1)).map(agent => `<div><span>${escapeHtml(agent.name)}</span><i><b style="width:${numeric(agent.tokenCount) === null ? 0 : numeric(agent.tokenCount) / max * 100}%;--bar:var(--violet)"></b></i><strong>${displayNumber(agent.tokenCount, true)}</strong></div>`).join('') : '<div class="empty-inline">Agent 用量分布不受支持。</div>';
    }
    const models = Array.isArray(activity.models) ? activity.models.filter(model => model?.name) : [];
    put('usageModelCount', `${displayNumber(models.length)} labels`);
    const modelBars = $('#usageModelBars');
    if (modelBars) {
      const top = models.slice().sort((a, b) => (numeric(b.messages) || 0) - (numeric(a.messages) || 0)).slice(0, 8);
      const max = Math.max(1, ...top.map(model => numeric(model.messages) || 0));
      modelBars.style.gridTemplateColumns = `repeat(${Math.max(1, top.length)}, minmax(50px, 1fr))`;
      modelBars.innerHTML = top.length ? top.map(model => `<div><span>${escapeHtml(model.name)}</span><i><b style="height:${Math.max(3, (numeric(model.messages) || 0) / max * 100)}%"></b></i><strong>${displayNumber(model.messages)}</strong></div>`).join('') : '<div class="empty-inline">模型分布不受支持。</div>';
    }
    const coverage = $('#activityCoverage');
    if (coverage) coverage.innerHTML = [['活动账簿', activity.status], ['Agent 分布', usageAgents.length ? 'ready' : 'unsupported'], ['模型分布', models.length ? 'ready' : 'unsupported']].map(([label, status]) => `<div><span><i class="status-dot ${status === 'ready' ? 'good' : ['stale', 'degraded'].includes(status) ? 'warn' : 'muted'}"></i>${label}</span><b>${STATUS_LABELS[status] || status || 'Unknown'}</b></div>`).join('');
  }

  function hydrateMemoryStatus(memory) {
    if (!memory) return;
    const local = memory?.backends?.local || {};
    const rag = memory?.backends?.rag || {};
    const count = numeric(local.documentCount);
    const sources = numeric(local.sourceCount);
    if (count !== null) {
      put('homeMemoryCount', displayNumber(count));
      put('homeMemoryLegend', displayNumber(count));
      put('supportingMemoryCount', displayNumber(count));
      put('navMemoryCount', displayNumber(count));
    }
    put('homeMemoryMeta', `${local.status || 'unknown'} · ${sources === null ? 'sources —' : `${displayNumber(sources)} sources`}`);
    put('memoryIndexDocumentCount', displayNumber(count));
    put('memoryIndexSourceCount', displayNumber(sources));
    put('memoryMapDocumentCount', `${displayNumber(count)} docs`);
    put('memoryMapSourceCount', displayNumber(sources));
    put('memoryLocalBackendStatus', local.available ? (local.backend?.stale ? '可用 · stale' : '可用') : local.status || '不可用');
    const ragEnabled = Boolean(rag?.status?.enabled || rag?.status?.productEnabled);
    put('memoryRagBackendStatus', ragEnabled && rag.available ? '可用' : ragEnabled ? '未就绪' : 'Not Now');
    put('memorySearchBackendLabel', `${local.backend?.kind || 'local-fts'} · ${local.backend?.stale ? 'stale' : 'ready'}`);
    const ticket = $('.memory-status-ticket');
    if (ticket) {
      const ready = Boolean(memory.available && local.available);
      ticket.querySelector('.status-seal').textContent = ready ? 'READY' : local.status === 'missing' ? 'EMPTY' : 'CHECK';
      ticket.querySelector(':scope > div > b').textContent = ready ? 'Local FTS 可用' : 'Local FTS 当前不可用';
      ticket.querySelector(':scope > div > span').textContent = `${count === null ? '文档数未知' : `${displayNumber(count)} documents`} · ${local.indexedAt ? `indexed ${shortDate(local.indexedAt)}` : '索引时间未知'}`;
    }
    const capabilities = memory.capabilities || local.capabilities || {};
    const matrix = $('.capability-matrix');
    if (matrix) {
      const rows = [
        ['全文检索', capabilities.fts5 ?? capabilities.lexical],
        ['模糊匹配', capabilities.trigram],
        ['元数据过滤', capabilities.metadataFilters],
        ['语义召回', capabilities.semantic],
        ['Agentic Planning', rag?.capabilities?.agenticPlanning]
      ];
      matrix.innerHTML = rows.map(([label, enabled]) => `<div><span>${label}</span><b class="${enabled ? 'good-text' : 'muted-text'}">${enabled ? '可用' : '未启用'}</b></div>`).join('');
    }
    const bootstrapStatus = state.archive.bootstrap?.status || 'unsupported';
    const health = $('#homeHealthRows');
    if (health) health.innerHTML = [
      ['Archive 资产投影', bootstrapStatus],
      ['Local FTS 索引', local.available ? (local.backend?.stale ? 'stale' : 'ready') : 'unsupported'],
      ['nova-RAG 语义索引', ragEnabled ? (rag.available ? 'ready' : 'degraded') : 'unsupported']
    ].map(([label, status]) => `<div><span><i class="status-dot ${status === 'ready' ? 'good' : ['stale', 'degraded'].includes(status) ? 'warn' : 'muted'}"></i>${label}</span><b>${STATUS_LABELS[status] || status}</b></div>`).join('');
  }

  async function loadArchiveData({ announce = false } = {}) {
    if (!window.LivingArchiveApi) {
      setArchiveState('error', 'Archive 客户端未加载', '页面主体仍可浏览，但真实数据不可用。', true);
      return;
    }
    setArchiveState('loading', '正在打开馆藏', '并行读取资产、Skill Pass、历史用量与本地记忆状态。');
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
        ready: ['馆藏已装订', '核心资产、运行与活动账簿均来自本地只读 API。'],
        empty: ['馆藏已经打开，书架仍为空', '这是有效空态；未用设计稿数字替代。'],
        stale: ['馆藏可读，但快照较旧', '页面保留新鲜度提示，不把旧数据标成实时。'],
        degraded: ['馆藏部分可用', `${sourceErrors} 个来源读取失败；可用区域仍保持真实数据。`],
        unsupported: ['部分馆藏尚不支持', '未知字段显示“—”，不会按 0 计算。'],
        error: ['无法读取 Archive API', '保留导航、每日名言和档案阅读；动态数字保持未知。']
      };
      const [title, detail] = messages[status] || messages.degraded;
      setArchiveState(status, title, detail, ['degraded', 'error'].includes(status));
      if (announce) showToast(status === 'ready' ? 'Archive 数据已更新' : `Archive 状态：${STATUS_LABELS[status] || status}`, status === 'ready' ? 'success' : 'warning');
    } catch (error) {
      state.archive.errors = [{ source: 'archive', message: String(error?.message || error) }];
      hydrateAssets(null);
      renderSkillPassRuns(null);
      hydrateAgents([]);
      setArchiveState('error', '无法读取 Archive API', '动态数字保持“—”；不会回退到虚构快照。', true);
      if (announce) showToast('Archive API 暂不可用', 'danger');
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
    window.scrollTo({ top: 0, behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
    setTimeout(() => $('.page:not([hidden]) h1')?.focus?.({ preventScroll: true }), 10);
  }

  function routeFromHash() {
    const hash = location.hash.replace(/^#/, '');
    const aliases = { 'archive-top': 'home', 'archive-agents': 'agents', 'archive-memory': 'memory', 'archive-foundation': 'foundation' };
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
    if (state.route === 'assets') showToast(filter === 'all' ? '已显示全部 AI 资产类别' : `已筛选：${filter}`, 'success');
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
      container.innerHTML = `<div class="search-result"><h3>尝试缩短关键词或切换来源</h3><p>Local FTS 支持全文、模糊匹配和元数据过滤；当前 nova-RAG 未启用。</p></div>`;
      return;
    }
    container.innerHTML = records.map((record, index) => `<article class="search-result" tabindex="0" data-detail="${record.id}"><div class="result-meta"><span>${escapeHtml(record.source)}</span><span>${escapeHtml(record.date)}</span><span>score ${(0.93 - index * .07).toFixed(2)}</span></div><h3>${escapeHtml(record.title)}</h3><p>${escapeHtml(record.text)}</p><div class="result-meta"><span>↳ ${escapeHtml(record.citation)}</span></div></article>`).join('');
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
    const heading = $('#search-results-title');
    if (heading) heading.textContent = rows.length ? `找到 ${rows.length} 条相关记忆` : '没有找到匹配记忆';
    const backend = payload?.backend?.kind || payload?.backend?.collection || payload?.requestedMode || 'local';
    const available = payload?.available !== false;
    if (!available) {
      container.innerHTML = '<div class="search-result"><h3>记忆检索当前不可用</h3><p>索引与档案页仍可浏览；没有使用样稿结果替代。</p></div>';
      return;
    }
    if (!rows.length) {
      container.innerHTML = `<div class="search-result"><h3>没有匹配“${escapeHtml(query)}”的本地记忆</h3><p>${escapeHtml(backend)} 已完成检索；可尝试更短的关键词或切换来源。</p></div>`;
      return;
    }
    container.innerHTML = memoryRecords.filter(record => record.remote).map(record => `<article class="search-result" tabindex="0" data-detail="${record.id}"><div class="result-meta"><span>${escapeHtml(record.source)}</span><span>${escapeHtml(record.date || 'date unknown')}</span><span>${record.score === null ? escapeHtml(backend) : `score ${record.score.toFixed(3)}`}</span></div><h3>${escapeHtml(record.title)}</h3><p>${escapeHtml(record.text)}</p><div class="result-meta"><span>↳ ${escapeHtml(record.citation)}</span></div></article>`).join('');
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
    container.innerHTML = results.length ? results.map(item => `<article class="global-result" tabindex="0" ${item.route ? `data-result-route="${item.route}"` : ''} ${item.detail ? `data-detail="${item.detail}"` : ''} ${item.assetId ? `data-asset-id="${escapeHtml(item.assetId)}"` : ''} ${item.agent ? `data-agent="${item.agent}"` : ''}><span>${escapeHtml(item.label)}</span><div><b>${escapeHtml(item.title)}</b><small>${escapeHtml(item.meta)}</small></div><i>→</i></article>`).join('') : `<div class="global-result"><span>EMPTY</span><div><b>没有匹配结果</b><small>尝试更短的关键词或选择“全部”类型。</small></div><i>·</i></div>`;
  }

  async function openArchiveAsset(assetId) {
    if (!window.LivingArchiveApi) return;
    openDrawer('正在读取资产', 'ARCHIVE DETAIL', '<div class="drawer-hero"><p>正在读取安全资产投影…</p></div>');
    try {
      const payload = await window.LivingArchiveApi.asset(assetId);
      const item = payload?.item;
      if (!item) throw new Error('资产详情为空');
      const scores = item?.metrics?.scores || {};
      const scoreRows = ['evidence', 'value', 'reuse', 'program'].map(key => `<div><span>${key}</span><b>${numeric(scores[key]) === null ? '—' : `${scores[key]}/5`}</b></div>`).join('');
      const relationCount = Object.values(item.relations || {}).reduce((sum, values) => sum + (Array.isArray(values) ? values.length : 0), 0);
      openDrawer(item.title || '资产详情', `${String(item.type || 'asset').toUpperCase()} · ${String(item.status || 'unknown').toUpperCase()}`, `<div class="drawer-hero"><p>${escapeHtml(item.summary || '没有公开摘要。')}</p><div class="drawer-stats"><div><b>${escapeHtml(shortDate(item.businessDate))}</b><span>business date</span></div><div><b>${escapeHtml(item.promptVersion || '—')}</b><span>source contract</span></div><div><b>${displayNumber(relationCount)}</b><span>relations</span></div></div></div><section class="drawer-section"><h3>质量信号</h3><div class="drawer-table">${scoreRows}</div></section><section class="drawer-section"><h3>边界</h3><p>这里只展示安全摘要、评分和关系；绝对路径、原始 Session 与 Skill 正文不会由 Archive API 返回。</p></section>`);
    } catch (error) {
      openDrawer('资产详情不可用', 'ARCHIVE ERROR', `<div class="drawer-hero"><p>${escapeHtml(error?.message || '无法读取资产详情')}</p></div>`);
    }
  }

  function openAgentDrawer(name) {
    const agent = agents[name];
    if (!agent) return;
    const stats = agent.stats.map(([label,value]) => `<div><b>${escapeHtml(value)}</b><span>${escapeHtml(label)}</span></div>`).join('');
    const rows = agent.details.map(([label,value]) => `<div><span>${escapeHtml(label)}</span><b>${escapeHtml(value)}</b></div>`).join('');
    openDrawer(name, `AGENT SOURCE · ${agent.status.toUpperCase()}`, `<div class="drawer-hero"><p>${escapeHtml(agent.summary)}</p><div class="drawer-stats">${stats}</div></div><section class="drawer-section"><h3>来源覆盖</h3><div class="drawer-table">${rows}</div></section><section class="drawer-section"><h3>说明</h3><p>${escapeHtml(agent.notes)}</p></section><section class="drawer-section"><button class="button secondary full-button" type="button" data-drawer-route="usage">查看历史活动账簿</button></section>`);
  }

  function openDetail(id) {
    const memory = memoryRecords.find(record => record.id === id);
    if (memory) {
      openDrawer(memory.title, 'MEMORY CITATION', `<div class="drawer-hero"><p>${escapeHtml(memory.text)}</p><div class="drawer-stats"><div><b>${escapeHtml(memory.date.slice(5))}</b><span>business date</span></div><div><b>${escapeHtml(memory.type)}</b><span>source kind</span></div><div><b>FTS</b><span>retrieval backend</span></div></div></div><section class="drawer-section"><h3>引用</h3><div class="drawer-table"><div><span>来源</span><b>${escapeHtml(memory.source)}</b></div><div><span>定位</span><b>${escapeHtml(memory.citation)}</b></div><div><span>能力</span><b>Lexical · citation</b></div></div></section>`);
      return;
    }
    const detailMap = {
      'diary-narrative': ['叙事日记 · 07-18','STRUCTURED HOLDING','21 个章节把当天的工作脉络、成果与下一步组织为可引用的叙事。'],
      'diary-technical': ['技术日记 · 07-18','STRUCTURED HOLDING','26 个章节记录架构决策、实现边界和基础设施变化。'],
      'diary-learning': ['学习日记 · 07-18','REUSABLE LESSONS','17 个章节把故障、根因与建议转换为可复用经验。'],
      'period-0808': ['08-01 — 08-08 资产快照','PERIOD PROJECTION','最近一次周期资产投影由 Job #694 生成，状态 Ready。'],
      'skills-hermes': ['Hermes Skills','CAPABILITY CATALOG','243 个技能实例；跨工具重复安装不等同于 243 个独立技能。'],
      'skills-openclaw': ['OpenClaw Skills','CAPABILITY CATALOG','92 个技能实例，来自系统与自定义能力目录。'],
      'skills-claude': ['Claude Code Skills','CAPABILITY CATALOG','19 个技能实例，保留来源类型与层级信息。'],
      'skills-codex': ['Codex Skills','CAPABILITY CATALOG','8 个技能实例，可与其他工具的同名能力对照。'],
      'skills-unified': ['unified context','CUSTOM SKILL','跨 Agent 上下文能力，属于 3 个自定义技能之一。'],
      'memory-sources': ['记忆来源清单','INDEX PROVENANCE','81 个来源覆盖日记、过滤对话、原生记忆、任务事件与 Foundation 快照。'],
      'source-diaries': ['日记与报告来源','MEMORY SOURCE SET','解析后的 Markdown sections 与 embedded JSON 进入本地索引。'],
      'source-dialogue': ['过滤对话来源','MEMORY SOURCE SET','清洗后的跨 Agent 对话用于轻量全文与模糊检索。'],
      'source-native': ['原生记忆来源','MEMORY SOURCE SET','Codex 与 Claude Code 的原生记忆已获授权；保留来源边界。'],
      'source-tasks': ['Nova-Task 事件','MEMORY SOURCE SET','任务图谱事件作为独立来源进入检索，不改变 Nova-Task 页面设计。'],
      'source-foundation': ['Foundation 来源','MEMORY SOURCE SET','Usage rollups、period projections 与 dashboard snapshots 提供结构化上下文。'],
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
      'job-691': ['Foundation Job #691','REFRESH JOB','投影写入完成，无 error summary。'],
      'job-690': ['Foundation Job #690','REFRESH JOB','最近调度批次中的较早刷新记录。']
    };
    const detail = detailMap[id] || ['馆藏详情','CATALOG DETAIL','这是一个可检查的静态详情状态。正式实现将从对应 API 读取完整 provenance。'];
    openDrawer(detail[0], detail[1], `<div class="drawer-hero"><p>${escapeHtml(detail[2])}</p></div><section class="drawer-section"><h3>资产属性</h3><div class="drawer-table"><div><span>持久化</span><b>Yes</b></div><div><span>可追溯</span><b>Yes</b></div><div><span>可检索 / 复用</span><b>Available</b></div><div><span>数据来源</span><b>Foundation snapshot</b></div></div></section><section class="drawer-section"><p>原型未展示绝对路径、Session 原文或私有记忆正文。</p></section>`);
  }

  function diaryItems(payload = state.archive.diaries) {
    const rows = Array.isArray(payload) ? payload : Array.isArray(payload?.items) ? payload.items : [];
    return rows.filter(item => /^\d{4}-\d{2}-\d{2}$/.test(String(item?.fullDate || item?.date || ''))).map(item => ({ ...item, fullDate: item.fullDate || item.date }));
  }

  function renderSafeProse(value) {
    const text = String(value || '')
      .replace(/\s+\*\s+\*\*/g, '\n* **')
      .replace(/\s+-\s+(?=[^>])/g, '\n- ')
      .trim();
    if (!text) return '<p class="lede">这一页没有公开正文。</p>';
    const clean = source => String(source || '').replace(/\*\*(.*?)\*\*/g, '$1').replace(/`([^`]+)`/g, '$1').trim();
    return text.split(/\n+/).filter(Boolean).map(line => {
      const heading = /^\*\s+\*\*(.+?)\*\*[：:]?\s*(.*)$/.exec(line);
      if (heading) return `<section><h3>${escapeHtml(clean(heading[1]))}</h3>${heading[2] ? `<p>${escapeHtml(clean(heading[2]))}</p>` : ''}</section>`;
      const bullet = /^[-*]\s+(.*)$/.exec(line);
      if (bullet) return `<p class="diary-bullet">${escapeHtml(clean(bullet[1]))}</p>`;
      return `<p>${escapeHtml(clean(line))}</p>`;
    }).join('');
  }

  function hydrateDiaryIndex(payload) {
    state.archive.diaries = payload;
    const items = diaryItems(payload);
    const months = new Map();
    items.forEach(item => {
      const month = item.fullDate.slice(0, 7);
      months.set(month, (months.get(month) || 0) + 1);
    });
    const cabinet = $('#archiveCabinetMonths');
    if (cabinet) {
      cabinet.innerHTML = '<div class="nav-label">档案柜</div>' + (months.size ? Array.from(months.entries()).sort((a, b) => b[0].localeCompare(a[0])).slice(0, 8).map(([month, count]) => `<button class="cabinet-row" type="button" data-route="reports" data-period="${month}"><b>${month.replace('-', ' · ')}</b><span>${displayNumber(count)} diary days</span></button>`).join('') : '<span class="cabinet-loading">当前没有装订日记</span>');
    }
    put('reportDiaryDays', displayNumber(items.length));
    if (!items.length) {
      renderCalendar();
      $('#reportSheet').innerHTML = '<div class="empty-inline">Foundation 当前没有可读取的日记文档。</div>';
      put('selectedReportDate', '档案柜为空');
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
    $('#reportSheet').innerHTML = '<div class="empty-inline">正在读取 Foundation 日记投影…</div>';
    try {
      let payload = state.diaryCache.get(businessDate);
      if (!payload) {
        payload = await window.LivingArchiveApi.diary(businessDate);
        state.diaryCache.set(businessDate, payload);
      }
      renderReportSheet();
    } catch (error) {
      put('selectedReportDate', `${businessDate} · 不可用`);
      $('#reportSheet').innerHTML = `<div class="empty-state"><b>无法读取这一天的档案</b><p>${escapeHtml(error?.message || 'Diary API unavailable')}</p></div>`;
    }
  }

  function renderLiveDiarySheet(payload) {
    const tab = state.reportTab;
    const dateLabel = `${payload.date || state.calendar.selected}${payload.dayOfWeek ? ` · ${payload.dayOfWeek}` : ''}`;
    put('selectedReportDate', dateLabel);
    if (tab === 'narrative') {
      $('#reportSheet').innerHTML = `<div class="report-eyebrow">NARRATIVE · FOUNDATION</div><h2>${escapeHtml(payload.displayDate || payload.date || '日记')}</h2><div class="live-diary-prose">${renderSafeProse(payload.summary)}</div><div class="report-provenance"><span>Foundation diary projection</span><span>${escapeHtml(payload.languageProfile || 'language unknown')}</span><span>${escapeHtml(payload.dashboardState?.status || 'unknown')}</span></div>`;
      return;
    }
    if (tab === 'technical') {
      const topics = Array.isArray(payload.summaryTopics) ? payload.summaryTopics : [];
      $('#reportSheet').innerHTML = `<div class="report-eyebrow">STRUCTURED TOPICS · ${displayNumber(topics.length)}</div><h2>工程主题与成果</h2>${topics.length ? topics.map(topic => `<section><h3>${escapeHtml(topic.title || '未命名主题')}</h3>${(topic.items || []).map(item => `<p>${escapeHtml(item)}</p>`).join('')}</section>`).join('') : '<p class="lede">这一天没有结构化主题。</p>'}`;
      return;
    }
    if (tab === 'learning') {
      const lessons = Array.isArray(payload.lessons) ? payload.lessons : [];
      $('#reportSheet').innerHTML = `<div class="report-eyebrow">EXPERIENCE · ${displayNumber(lessons.length)}</div><h2>经验与实践线索</h2>${lessons.length ? lessons.map(item => `<section><h3>${escapeHtml(item.problem || '经验')}</h3>${item.rootCause ? `<p><b>根因：</b>${escapeHtml(item.rootCause)}</p>` : ''}${item.suggestion ? `<p><b>可复用建议：</b>${escapeHtml(item.suggestion)}</p>` : ''}</section>`).join('') : '<p class="lede">这一天没有兼容 Learning 经验；Skill Pass 资产请前往 AI 资产页。</p>'}`;
      return;
    }
    const assets = (state.archive.assets?.items || []).filter(item => item.businessDate === (payload.date || state.calendar.selected));
    $('#reportSheet').innerHTML = `<div class="report-eyebrow">ASSET BINDING · ${displayNumber(assets.length)}</div><h2>当天形成的可复用资产</h2>${assets.length ? assets.map(item => `<section><h3>${escapeHtml(TYPE_LABELS[item.type] || item.type)} · ${escapeHtml(item.title)}</h3><p>${escapeHtml(item.summary || '没有公开摘要')}</p></section>`).join('') : '<p class="lede">这一天没有 Skill Pass 核心资产记录。</p>'}`;
  }

  function renderCalendar() {
    const { year, month, selected } = state.calendar;
    const monthLabel = `${year} · ${String(month + 1).padStart(2,'0')}`;
    $('#calendarMonth').textContent = monthLabel;
    const available = new Set(diaryItems().map(item => item.fullDate));
    const assetDates = new Set((state.archive.assets?.items || []).map(item => item.businessDate).filter(Boolean));
    const monthPrefix = `${year}-${String(month + 1).padStart(2, '0')}`;
    put('calendarMonthCount', `${displayNumber(Array.from(available).filter(value => value.startsWith(monthPrefix)).length)} diary days`);
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
      $('#selectedReportDate').textContent = `${selected} · 无装订档案`;
      $('#reportSheet').innerHTML = `<div class="report-eyebrow">EMPTY SHELF</div><h2>这一天没有可展示的结构化档案</h2><p class="lede">原始活动不等于已经形成资产。只有完成持久化与结构化处理的结果才进入档案柜。</p>`;
      return;
    }
    $$('.report-tabs button').forEach(button => {
      button.disabled = false;
      button.classList.toggle('active', button.dataset.reportTab === state.reportTab);
    });
    if (!payload) {
      put('selectedReportDate', `${selected} · 读取中`);
      $('#reportSheet').innerHTML = '<div class="empty-inline">正在读取 Foundation 日记投影…</div>';
      return;
    }
    renderLiveDiarySheet(payload);
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
      [88,'检查 Local FTS 新鲜度…'],
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
        showToast('同步流程演示完成；没有执行真实写入');
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
    const headline = ($('#sharePreview h3')?.innerText || '今天新增的\n不是消耗，\n而是可复用资产。').split(/\n+/).filter(Boolean).slice(0, 3);
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
      if (!blob) return showToast('无法生成预览', 'danger');
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = `actanara-living-archive-${new Date().toISOString().slice(0, 10)}.png`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 1000);
      showToast('分享预览 PNG 已生成');
    }, 'image/png');
  }

  async function copyShare() {
    const canvas = drawShareCanvas();
    const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
    try {
      if (!navigator.clipboard || !window.ClipboardItem || !blob) throw new Error('clipboard unavailable');
      await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
      showToast('分享预览已复制到剪贴板');
    } catch (_) {
      showToast('浏览器未授权图片剪贴板；可使用“下载预览”', 'warning');
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
    showToast('Fallback 顺序已在原型中调整');
  }

  function handleAction(action, button) {
    switch (action) {
      case 'retry-archive': loadArchiveData({ announce: true }); break;
      case 'start-sync': startSync(button); break;
      case 'copy-share': copyShare(); break;
      case 'download-share': downloadShare(); break;
      case 'create-backup':
        button.disabled = true; button.textContent = '正在封存并校验…';
        setTimeout(() => { button.disabled = false; button.textContent = '创建并校验备份'; showToast('备份流程演示完成；未写入磁盘'); }, 1100);
        break;
      case 'restore-backup': $('#restoreWarning').hidden = false; showToast('已展开恢复范围检查', 'warning'); break;
      case 'confirm-restore': showToast('恢复演示完成；没有覆盖任何本机数据'); closeOverlays(); break;
      case 'save-schedule': showToast('自动备份计划已保存在原型状态'); break;
      case 'save-settings': showToast('原型设置已保存（刷新后重置）'); closeOverlays(); break;
      case 'configure-rag': showToast('nova-RAG 仍为 Not Now；Local FTS 保持可用', 'warning'); break;
      case 'browse-path': showToast('路径管理页已模拟；正式版将使用安全路径选择器'); break;
      case 'refresh-foundation':
        button.disabled = true; button.textContent = '读取中…';
        loadArchiveData({ announce: true }).finally(() => { button.disabled = false; button.textContent = '重新读取'; });
        break;
      default: showToast('该操作已在静态原型中响应');
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
    if (target.dataset.resultRoute) { closeOverlays(); navigate(target.dataset.resultRoute); return; }
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
      if (target.dataset.backend === 'rag') showToast('nova-RAG 尚未启用；当前仍使用 Local FTS', 'warning');
      else showToast('已切换到 Local FTS');
      return;
    }
    if (target.matches('[data-toggle]')) {
      target.classList.toggle('is-on');
      showToast(`开关已${target.classList.contains('is-on') ? '启用' : '关闭'}（仅原型状态）`);
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
      const headings = { daily: '今天新增的<br>不是消耗，<br>而是可复用资产。', weekly: '这一周，<br>工作轨迹被装订成<br>可复用馆藏。', memory: '跨 Agent 记忆，<br>即使没有 RAG<br>仍然可以检索。' };
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
      const labels = { today: '今日视图已聚焦小时活动', '30d': '已显示 30 天活动历史', all: '累计视图已聚焦来源与模型' };
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
      showToast(`档案类型筛选：${target.textContent}`);
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
    submit.textContent = '检索中…';
    try {
      const payload = await window.LivingArchiveApi.searchMemory(query, state.memorySource);
      renderLiveMemoryResults(payload, query);
      showToast(payload?.available === false ? '本地记忆当前不可用' : '本地记忆检索完成', payload?.available === false ? 'warning' : 'success');
    } catch (error) {
      const container = $('#memorySearchResults');
      if (container) container.innerHTML = `<div class="search-result"><h3>检索失败</h3><p>${escapeHtml(error?.message || 'Local FTS 暂不可用')}</p></div>`;
      showToast('本地记忆检索失败', 'danger');
    } finally {
      submit.disabled = false;
      submit.textContent = '检索';
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
  $('#memorySearchResults').innerHTML = '<div class="search-result"><h3>输入关键词开始本地检索</h3><p>结果来自 Local FTS 或已启用的 nova-RAG；不会使用样稿记录替代。</p></div>';
  renderGlobalResults($('#globalSearchInput').value);
  renderCalendar();
  renderReportSheet();
  routeFromHash();
  loadArchiveData();
})();
