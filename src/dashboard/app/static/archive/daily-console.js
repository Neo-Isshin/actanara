/*
 * Living Archive · Daily Decision Console
 *
 * This file is deliberately standalone.  The existing Archive page can load
 * it after archive-api.js, but it also works without that helper (for example
 * in a smaller embedded view).  It only mounts when one of the documented
 * containers exists, so adding the script to a page is safe before the host
 * markup is ready.
 */
(() => {
  'use strict';

  const DEFAULTS = Object.freeze({
    contentSelector: '#dailyConsoleContent',
    summarySelector: '#dailyConsoleSummary',
    rootSelector: '#dailyConsole',
    dateSelector: '#dailyConsoleDate',
    limit: 50,
    assetLimit: 200,
    timeoutMs: 12000,
    endpoints: Object.freeze({
      pipeline: '/api/foundation/ops/daily-pipeline-summary',
      assets: '/api/archive/v1/assets',
      taskProposals: '/api/tasks/l1-review',
      lessonProposals: '/api/ai-assets/skill-assets',
      taskConfirm: '/api/tasks/candidates/{id}/confirm',
      taskReject: '/api/tasks/candidates/{id}/reject',
      taskDefer: '/api/tasks/candidates/{id}/defer',
      lessonCrystallize: '/api/ai-assets/skill-assets/crystallize',
      skillRegister: '/api/ai-assets/skill-assets/register',
      messages: '/api/msgbox',
      messageRead: '/api/msgbox/{id}/read',
      businessDay: '/api/token-clock'
    })
  });

  const TEXT_LIMITS = Object.freeze({
    title: 180,
    summary: 520,
    reason: 520,
    agent: 80,
    id: 120,
    error: 280
  });

  const STATUS_LABELS = Object.freeze({
    ready: '正常',
    empty: '暂无数据',
    degraded: '部分可用',
    stale: '需要更新',
    unavailable: '暂不可用',
    loading: '加载中',
    pending: '待审核',
    pending_review: '待审核',
    queued: '排队中',
    running: '运行中',
    completed: '已完成',
    failed: '失败',
    info: '信息',
    warn: '提醒',
    error: '错误',
    incomplete: '不完整',
    attention: '需要关注',
    rejected: '已拒绝',
    deferred: '已延期',
    confirmed: '已确认',
    documented: '已记录',
    verified: '已验证',
    draft: '草稿',
    active: '已启用',
    unknown: '未知'
  });

  const BLOCK_COPY = Object.freeze({
    assets: {
      eyebrow: 'ASSET ADDITIONS',
      title: '资产增加',
      description: '当天由公开 Archive 投影确认的经验、实践与 Skill。'
    },
    tasks: {
      eyebrow: 'NOVA-TASK PROPOSALS',
      title: 'Nova-Task 提案',
      description: '需要人工确认的 L1 项目提案；页面不会直接改写任务投影。'
    },
    lessons: {
      eyebrow: 'LESSON PROPOSALS',
      title: '经验教训提案',
      description: '当前最新 Skill Pass 批次中待复核的 Lesson 与 Skill，可选择结晶或注册。'
    }
  });

  const state = {
    config: null,
    root: null,
    eventRoot: null,
    content: null,
    summary: null,
    dateInput: null,
    hostLists: { assets: null, tasks: null, lessons: null },
    hostMode: false,
    data: { pipeline: null, assets: null, tasks: null, lessons: null, messages: null },
    errors: [],
    loading: false,
    selectedLessons: new Set(),
    itemMap: new Map(),
    loadedDate: '',
    requestSerial: 0,
    observers: [],
    liveTimer: null,
    toastTimer: null,
    mounted: false
  };

  const $ = (selector, root = document) => {
    if (!selector || !root || typeof root.querySelector !== 'function') return null;
    if (selector && selector.nodeType === 1) return selector;
    try {
      return root.querySelector(selector);
    } catch (_) {
      return null;
    }
  };

  const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, char => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    "'": '&#39;',
    '"': '&quot;'
  })[char]);

  const isRecord = value => Boolean(value && typeof value === 'object' && !Array.isArray(value));

  const cssEscape = value => {
    const text = String(value ?? '');
    if (window.CSS?.escape) return window.CSS.escape(text);
    return text.replace(/[^A-Za-z0-9_-]/g, char => `\\${char}`);
  };

  function mergeConfig(options = {}) {
    const source = isRecord(options) ? options : {};
    const endpointOverrides = isRecord(source.endpoints) ? source.endpoints : {};
    return {
      ...DEFAULTS,
      ...source,
      endpoints: { ...DEFAULTS.endpoints, ...endpointOverrides }
    };
  }

  function clampInteger(value, fallback, minimum, maximum) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return fallback;
    return Math.max(minimum, Math.min(maximum, Math.floor(parsed)));
  }

  function today() {
    const current = new Date();
    const year = current.getFullYear();
    const month = String(current.getMonth() + 1).padStart(2, '0');
    const day = String(current.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
  }

  function validDate(value) {
    const text = String(value || '').trim();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) return false;
    const parsed = new Date(`${text}T00:00:00`);
    return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === text;
  }

  function readDate() {
    const value = state.dateInput?.value;
    return validDate(value) ? value : today();
  }

  async function resolveBusinessDate(requested) {
    if (validDate(requested)) return requested;
    try {
      const clock = await localRequest(state.config.endpoints.businessDay);
      if (validDate(clock?.today)) return clock.today;
    } catch (_) {
      // The rest of the daily request still reports source errors.  The
      // browser date is only a last-resort placeholder for the loading state.
    }
    return today();
  }

  function setDate(value) {
    const selected = validDate(value) ? value : today();
    if (state.dateInput) state.dateInput.value = selected;
    state.content?.querySelectorAll('[data-dc-date-label]').forEach(element => {
      element.textContent = selected;
    });
    return selected;
  }

  /*
   * Public responses are expected to be path-free, but the console keeps its
   * own boundary too.  This protects an embedded page if an upstream endpoint
   * accidentally includes a workspace path, token, cookie or email address.
   */
  function redactText(value, maximum = TEXT_LIMITS.summary) {
    let text;
    if (value === null || value === undefined) return '';
    if (typeof value === 'string') text = value;
    else if (typeof value === 'number' || typeof value === 'boolean') text = String(value);
    else if (Array.isArray(value)) text = value.slice(0, 8).map(item => redactText(item, 100)).filter(Boolean).join(' · ');
    else if (isRecord(value)) {
      const preferred = ['title', 'name', 'summary', 'reason', 'problem', 'rootCause', 'suggestion', 'message'];
      text = preferred.map(key => value[key]).find(item => typeof item === 'string' && item.trim()) || '';
    } else text = String(value);

    text = text
      .replace(/\x00/g, ' ')
      .replace(/[\u0001-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, ' ')
      // Unix/macOS/Linux and Windows local paths, including file:// URLs.
      .replace(/(?<![A-Za-z0-9:])(?:file:\/\/)?(?:~|\/(?:Users|home|Volumes|private|var|tmp|opt|etc|root|workspace))(?:[\\/][^\s,;，；。)\]}>'"]+)+/gi, '[local path]')
      .replace(/(?<![A-Za-z0-9])[A-Za-z]:\\(?:[^\s,;，；。)\]}>'"]+\\?)+/g, '[local path]')
      // Secret-like assignments and bearer/JWT/API-key forms.
      .replace(/\b(password|passwd|pwd|token|api[_-]?key|secret|private[_-]?key|authorization|cookie)(\s*[:=]\s*)[^\s,;]+/gi, '$1$2[redacted]')
      .replace(/\bBearer\s+[A-Za-z0-9._~+/=-]+/gi, 'Bearer [redacted]')
      .replace(/\b(?:sk|rk|ghp|github_pat|xox[baprs])[-_][A-Za-z0-9._-]{12,}/gi, '[redacted]')
      .replace(/\bAKIA[0-9A-Z]{12,}\b/g, '[redacted]')
      .replace(/\beyJ[A-Za-z0-9_-]{16,}\.[A-Za-z0-9._-]{8,}\.[A-Za-z0-9._-]{8,}\b/g, '[redacted]')
      // Avoid putting personal email addresses into a shared dashboard view.
      .replace(/\b([A-Z0-9._%+-]{1,64})@([A-Z0-9.-]+\.[A-Z]{2,})\b/gi, '[contact redacted]')
      .replace(/\s+/g, ' ')
      .trim();

    if (text.length > maximum) text = `${text.slice(0, Math.max(0, maximum - 1)).trimEnd()}…`;
    return text;
  }

  function safeIdentifier(value, pattern = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$/) {
    const text = String(value || '').trim();
    return pattern.test(text) ? text : '';
  }

  function safeCandidateId(value) {
    return safeIdentifier(value, /^NTC-[A-Za-z0-9][A-Za-z0-9_-]{0,119}$/);
  }

  function safeReviewId(value) {
    return safeIdentifier(value, /^review-\d{3}$/);
  }

  function statusLabel(value) {
    const key = String(value || '').toLowerCase();
    return STATUS_LABELS[key] || redactText(value, 50) || STATUS_LABELS.unknown;
  }

  function confidenceLabel(value) {
    const labels = { unknown: '待评估', high: '高', medium: '中', low: '低' };
    return labels[String(value || '').toLowerCase()] || redactText(value, 40) || '待评估';
  }

  function statusTone(value) {
    const key = String(value || '').toLowerCase();
    if (['ready', 'completed', 'verified', 'active', 'confirmed'].includes(key)) return 'good';
    if (['degraded', 'stale', 'pending', 'pending_review', 'draft', 'queued', 'running', 'incomplete', 'attention'].includes(key)) return 'warn';
    if (['error', 'failed', 'rejected', 'unavailable'].includes(key)) return 'bad';
    return 'neutral';
  }

  function numberOrNull(value) {
    if (value === '' || value === null || value === undefined || typeof value === 'boolean') return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
  }

  function formatNumber(value) {
    const parsed = numberOrNull(value);
    return parsed === null ? '—' : parsed.toLocaleString('zh-CN');
  }

  function formatDateTime(value) {
    const raw = String(value || '').trim();
    if (!raw) return '时间未知';
    const parsed = new Date(raw);
    if (Number.isNaN(parsed.getTime())) return redactText(raw, 40) || '时间未知';
    return parsed.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
  }

  function firstText(item, keys, maximum = TEXT_LIMITS.summary) {
    if (!isRecord(item)) return '';
    for (const key of keys) {
      const value = item[key];
      if (typeof value === 'string' && value.trim()) return redactText(value, maximum);
      if (typeof value === 'number' || typeof value === 'boolean') return redactText(value, maximum);
    }
    return '';
  }

  function countFrom(payload, keys) {
    if (!isRecord(payload)) return null;
    for (const key of keys) {
      const direct = numberOrNull(payload[key]);
      if (direct !== null) return direct;
      const nested = key.split('.').reduce((current, part) => current?.[part], payload);
      const parsed = numberOrNull(nested);
      if (parsed !== null) return parsed;
    }
    return null;
  }

  function asArray(payload, keys = ['items']) {
    if (Array.isArray(payload)) return payload;
    if (!isRecord(payload)) return [];
    for (const key of keys) {
      if (Array.isArray(payload[key])) return payload[key];
    }
    return [];
  }

  function appendQuery(path, params) {
    const raw = String(path || '').trim();
    // The module intentionally accepts same-origin API paths only.  This keeps
    // future config from turning a data card into an exfiltration primitive.
    if (!raw.startsWith('/api/')) throw new Error('每日决策台只允许请求同源 API。');
    const query = new URLSearchParams();
    Object.entries(params || {}).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') query.set(key, String(value));
    });
    const suffix = query.toString();
    return suffix ? `${raw}${raw.includes('?') ? '&' : '?'}${suffix}` : raw;
  }

  function cookie(name) {
    const encoded = `${encodeURIComponent(name)}=`;
    const value = String(document.cookie || '').split('; ').find(item => item.startsWith(encoded));
    return value ? decodeURIComponent(value.slice(encoded.length)) : '';
  }

  async function localRequest(path, options = {}) {
    if (!String(path || '').startsWith('/api/')) throw new Error('每日决策台只允许请求同源 API。');
    if (window.LivingArchiveApi?.request && options.useArchiveClient !== false) {
      return window.LivingArchiveApi.request(path, {
        method: options.method,
        body: options.body,
        timeoutMs: options.timeoutMs || state.config.timeoutMs
      });
    }

    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), options.timeoutMs || state.config.timeoutMs);
    const method = String(options.method || 'GET').toUpperCase();
    const headers = new Headers(options.headers || {});
    headers.set('Accept', 'application/json');
    if (options.body !== undefined) headers.set('Content-Type', 'application/json');
    if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
      const csrf = cookie('actanara_dashboard_csrf');
      if (csrf) headers.set('X-Actanara-CSRF', csrf);
    }
    try {
      const response = await fetch(path, {
        method,
        headers,
        body: options.body === undefined ? undefined : JSON.stringify(options.body),
        credentials: 'same-origin',
        signal: controller.signal
      });
      let payload = null;
      try { payload = await response.json(); } catch (_) { payload = null; }
      if (!response.ok) {
        const error = new Error(`请求失败（HTTP ${response.status}）`);
        error.status = response.status;
        error.payload = payload;
        throw error;
      }
      if (!payload || typeof payload !== 'object') throw new Error('本地数据服务返回了无法识别的数据。');
      return payload;
    } catch (error) {
      if (error?.name === 'AbortError') throw new Error('本地数据服务响应超时，请稍后重试。');
      throw error;
    } finally {
      window.clearTimeout(timeout);
    }
  }

  function publicError(error) {
    const message = redactText(error?.message || error || '读取失败', TEXT_LIMITS.error);
    return message || '读取失败';
  }

  function sourceError(source, error) {
    return { source, message: publicError(error) };
  }

  function normalizeAssets(payload, selectedDate) {
    const raw = asArray(payload, ['items', 'assets']);
    return raw.map((item, index) => {
      if (!isRecord(item)) return null;
      const type = String(item.type || item.assetType || item.assetClass || 'asset').toLowerCase();
      const id = safeIdentifier(item.id || item.assetId || `asset-${index + 1}`);
      return {
        key: `asset-${index}`,
        id,
        type: ['experience', 'practice', 'skill'].includes(type) ? type : 'asset',
        title: firstText(item, ['title', 'name', 'skillName'], TEXT_LIMITS.title) || '未命名资产',
        summary: firstText(item, ['summary', 'description', 'reason', 'completionReason'], TEXT_LIMITS.summary) || '没有公开摘要。',
        status: String(item.status || 'unknown').toLowerCase(),
        date: validDate(item.businessDate) ? item.businessDate : selectedDate,
        source: firstText(item, ['source.kind', 'source', 'agent', 'origin'], TEXT_LIMITS.agent),
        updatedAt: item.updatedAt || item.generatedAt || null,
        metrics: isRecord(item.metrics) ? item.metrics : null
      };
    }).filter(Boolean);
  }

  function normalizeTasks(payload, selectedDate) {
    const raw = asArray(payload, ['items', 'candidates']);
    return raw.map((item, index) => {
      if (!isRecord(item)) return null;
      const metadata = isRecord(item.metadata) ? item.metadata : {};
      const workspace = isRecord(item.workspace) ? item.workspace : (isRecord(metadata.workspace) ? metadata.workspace : {});
      const itemDate = item.businessDate || metadata.businessDate || item.date || metadata.date;
      // The current L1 endpoint is global.  If an item has an explicit date,
      // honor it; date-less pending candidates remain visible for review.
      if (validDate(itemDate) && itemDate !== selectedDate) return null;
      return {
        key: `task-${index}`,
        id: safeCandidateId(item.candidateId || item.id),
        title: firstText(item, ['proposedTitle', 'title', 'name'], TEXT_LIMITS.title) || '未命名任务提案',
        reason: firstText(item, ['reason', 'summary', 'description'], TEXT_LIMITS.reason) || '没有公开理由。',
        status: String(item.status || item.reviewStatus || 'pending_review').toLowerCase(),
        confidence: firstText(item, ['confidence'], 40),
        parentId: safeIdentifier(item.proposedParentNodeId || item.parentNodeId),
        matchedId: safeIdentifier(item.matchedNodeId),
        source: firstText(item, ['source', 'sourceEventId', 'origin'], TEXT_LIMITS.agent),
        workspace: firstText(workspace, ['displayName', 'name'], TEXT_LIMITS.agent),
        date: validDate(itemDate) ? itemDate : selectedDate,
        createdAt: item.createdAt || item.updatedAt || null,
        evidenceCount: Array.isArray(item.evidence) ? item.evidence.length : numberOrNull(item.evidenceCount)
      };
    }).filter(item => item && item.id);
  }

  function normalizeLessons(ledgerPayload, pipelinePayload, selectedDate) {
    const ledgerItems = asArray(ledgerPayload, ['items', 'lessons', 'proposals']);
    const ledgerDate = validDate(ledgerPayload?.businessDate) ? ledgerPayload.businessDate : selectedDate;
    const ledgerPromptVersion = firstText(ledgerPayload, ['promptVersion'], 40);
    const projected = ledgerItems.map((item, index) => {
      if (!isRecord(item)) return null;
      const assetClass = String(item.assetClass || item.originalDecision || item.type || '').toLowerCase();
      if (assetClass && !['lesson', 'experience', 'skill'].includes(assetClass)) return null;
      const reviewId = safeReviewId(item.reviewId);
      return {
        key: `lesson-${reviewId || index}`,
        reviewId,
        kind: assetClass === 'skill' ? 'skill' : 'lesson',
        libraryAction: firstText(item, ['libraryAction'], 40),
        title: firstText(item, assetClass === 'skill' ? ['skillName', 'title', 'name'] : ['title', 'problem', 'name'], TEXT_LIMITS.title) || (assetClass === 'skill' ? '未命名 Skill 提案' : '未命名经验教训'),
        summary: firstText(item, assetClass === 'skill' ? ['skillDescription', 'summary', 'reason', 'title'] : ['summary', 'reason', 'problem', 'rootCause'], TEXT_LIMITS.summary) || '没有公开摘要。',
        suggestion: firstText(item, ['suggestion', 'completionReason', 'recommendation'], TEXT_LIMITS.summary),
        agent: firstText(item, ['agent', 'source'], TEXT_LIMITS.agent),
        status: String(item.status || item.completion || 'pending').toLowerCase(),
        date: validDate(item.businessDate) ? item.businessDate : ledgerDate,
        promptVersion: firstText(item, ['promptVersion'], 40) || ledgerPromptVersion,
        evidenceCount: numberOrNull(item.evidenceCount) ?? (Array.isArray(item.evidence) ? item.evidence.length : null),
        scores: isRecord(item.scores) ? item.scores : null,
        source: firstText(item, ['source', 'sourceDocumentKey'], TEXT_LIMITS.agent)
      };
    }).filter(Boolean);

    // When Skill Pass has no ledger for a date, the read-only daily pipeline
    // lesson projection is still useful.  It has no review ID, so it cannot be
    // sent to the crystallize mutation and is displayed as an informational row.
    if (projected.length) return projected;
    const dailyLessons = asArray(pipelinePayload?.lessons, ['items']);
    return dailyLessons.map((item, index) => {
      if (!isRecord(item)) return null;
      return {
        key: `lesson-pipeline-${index}`,
        reviewId: '',
        kind: 'lesson',
        libraryAction: '',
        title: firstText(item, ['problem', 'title', 'rootCause'], TEXT_LIMITS.title) || '未命名经验教训',
        summary: firstText(item, ['rootCause', 'problem', 'summary'], TEXT_LIMITS.summary) || '没有公开摘要。',
        suggestion: firstText(item, ['suggestion', 'recommendation'], TEXT_LIMITS.summary),
        agent: firstText(item, ['agent', 'source'], TEXT_LIMITS.agent),
        status: 'documented',
        date: validDate(item.date) ? item.date : selectedDate,
        promptVersion: '',
        evidenceCount: null,
        scores: null,
        source: firstText(item, ['sourceDocumentKey', 'source'], TEXT_LIMITS.agent)
      };
    }).filter(Boolean);
  }

  function assetCounts(items, payload) {
    const counts = isRecord(payload?.counts) ? payload.counts : {};
    return {
      all: numberOrNull(counts.all) ?? items.length,
      experience: numberOrNull(counts.experience) ?? items.filter(item => item.type === 'experience').length,
      practice: numberOrNull(counts.practice) ?? items.filter(item => item.type === 'practice').length,
      skill: numberOrNull(counts.skill) ?? items.filter(item => item.type === 'skill').length
    };
  }

  function lessonReviewable(item) {
    return Boolean(item?.reviewId && safeReviewId(item.reviewId) && item.promptVersion);
  }

  function skillRegisterable(item) {
    return Boolean(item?.kind === 'skill' && lessonReviewable(item) && ['create', 'extend'].includes(item.libraryAction));
  }

  function mapItem(kind, item) {
    const key = `${kind}:${item.key}`;
    state.itemMap.set(key, item);
    return key;
  }

  function itemFromKey(key, kind) {
    const item = state.itemMap.get(`${kind}:${key}`);
    return item || null;
  }

  function loadingMarkup() {
    return `
      <div class="daily-console-loading" role="status" aria-live="polite">
        <span class="daily-console-spinner" aria-hidden="true"></span>
        <span>正在读取 ${escapeHtml(readDate())} 的决策数据…</span>
      </div>`;
  }

  function emptyMarkup(message = '当天没有可显示的记录。') {
    return `<div class="daily-console-empty">${escapeHtml(message)}</div>`;
  }

  function errorMarkup(message) {
    return `<div class="daily-console-error" role="alert">${escapeHtml(message)}</div>`;
  }

  function badgeMarkup(value, extraClass = '') {
    const tone = statusTone(value);
    return `<span class="daily-console-badge ${tone} ${escapeHtml(extraClass)}">${escapeHtml(statusLabel(value))}</span>`;
  }

  function updateHostStatus(id, value) {
    const element = document.getElementById(id);
    if (!element) return;
    const key = String(value || 'unknown').toLowerCase();
    element.textContent = statusLabel(key);
    element.className = `status-pill ${['ready', 'completed', 'verified', 'active'].includes(key) ? 'good' : ['stale', 'degraded', 'pending', 'incomplete', 'unavailable'].includes(key) ? 'warning' : ''}`.trim();
  }

  function isTaskProposalMessage(item) {
    return /task[_-]?candidate|l1.*提案/i.test(`${item?.type || ''} ${item?.title || ''}`);
  }

  function renderControls(selectedDate) {
    const externalDate = state.dateInput && (!state.content?.contains(state.dateInput) || state.hostMode);
    const dateControl = externalDate
      ? `<span class="daily-console-date-readout" aria-live="polite">业务日 <b data-dc-date-label>${escapeHtml(selectedDate)}</b></span>`
      : `<label class="daily-console-date-field"><span>业务日</span><input type="date" data-dc-date value="${escapeHtml(selectedDate)}" aria-label="选择业务日期"></label>`;
    return `
      <div class="daily-console-toolbar">
        <div class="daily-console-toolbar-copy"><span class="daily-console-eyebrow">DAILY DECISION CONSOLE</span><b>每日决策台</b><span>事实来自本机只读投影；写入动作需要明确确认。</span></div>
        <div class="daily-console-toolbar-actions">
          ${dateControl}
          <button class="daily-console-button secondary" type="button" data-dc-action="previous-date" aria-label="查看前一天">←</button>
          <button class="daily-console-button secondary" type="button" data-dc-action="next-date" aria-label="查看后一天">→</button>
          <button class="daily-console-button primary" type="button" data-dc-action="refresh">刷新</button>
        </div>
      </div>`;
  }

  function renderSummary(selectedDate) {
    if (!state.summary) return;
    const pipeline = state.data.pipeline || {};
    const assets = state.data.assets?.items || [];
    const tasks = state.data.tasks?.items || [];
    const lessons = state.data.lessons?.items || [];
    updateHostStatus('dailyConsoleAssetsStatus', assets.length ? (state.data.assets?.payload?.status || 'ready') : 'empty');
    updateHostStatus('dailyConsoleTasksStatus', tasks.length ? 'pending' : (state.data.tasks?.payload?.enabled === false ? 'unavailable' : 'empty'));
    updateHostStatus('dailyConsoleLessonsStatus', lessons.length ? (state.data.lessons?.payload?.status || 'ready') : 'empty');
    const messages = (state.data.messages?.items || []).filter(item => !isTaskProposalMessage(item));
    const counts = assetCounts(assets, state.data.assets);
    const reviewableLessons = lessons.filter(item => item.kind !== 'skill' && lessonReviewable(item)).length;
    const registerableSkills = lessons.filter(skillRegisterable).length;
    const navCount = document.getElementById('navConsoleCount');
    if (navCount) {
      const pending = tasks.length + lessons.filter(item => lessonReviewable(item) || skillRegisterable(item)).length + messages.length;
      navCount.textContent = pending ? `${pending} 待办` : '无待办';
    }
    const status = state.loading ? 'loading' : (pipeline.status || (state.errors.length ? 'degraded' : 'ready'));
    const errorsText = state.errors.length ? ` · ${state.errors.length} 个来源不可用` : '';
    const dateNote = document.getElementById('dailyConsoleDateNote');
    if (dateNote) dateNote.textContent = state.loading ? '正在读取本地业务日…' : '资产按选定业务日；提案显示当前待审核批次。';
    state.summary.innerHTML = `
      <div class="daily-console-summary-head">
        <div><span class="daily-console-eyebrow">DECISION SNAPSHOT</span><h2>日决策摘要</h2><p>${escapeHtml(selectedDate)} · ${escapeHtml(state.loading ? '正在读取' : '截至当前可见投影')}${escapeHtml(errorsText)}</p></div>
        ${badgeMarkup(status)}
      </div>
      <div class="daily-console-summary-grid" aria-label="每日决策摘要指标">
        <div class="daily-console-summary-card assets"><span>资产增加</span><strong>${escapeHtml(formatNumber(counts.all))}</strong><small>经验 ${escapeHtml(formatNumber(counts.experience))} · 实践 ${escapeHtml(formatNumber(counts.practice))} · Skill ${escapeHtml(formatNumber(counts.skill))}</small></div>
        <div class="daily-console-summary-card tasks"><span>Nova-Task 待审</span><strong>${escapeHtml(formatNumber(tasks.length))}</strong><small>仅展示待人工审核的 L1 提案</small></div>
        <div class="daily-console-summary-card lessons"><span>经验教训</span><strong>${escapeHtml(formatNumber(lessons.length))}</strong><small>${escapeHtml(reviewableLessons)} 条可结晶 · ${escapeHtml(registerableSkills)} 条可注册</small></div>
        <div class="daily-console-summary-card pipeline"><span>其他提醒</span><strong>${escapeHtml(formatNumber(messages.length))}</strong><small>${escapeHtml(pipeline.activityState === 'empty' ? '当天没有管线活动' : `管线状态：${statusLabel(status)}`)}</small></div>
      </div>`;
  }

  function messageActionMarkup(item) {
    const action = isRecord(item?.action) ? item.action : {};
    const label = firstText(item, ['actionLabel'], 80) || '打开相关页面';
    const url = String(action.url || '');
    if (action.kind === 'openUrl' && url.startsWith('/') && !url.startsWith('//')) return `<a class="daily-console-link-button" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)} ↗</a>`;
    if (action.kind === 'openPage') return `<a class="daily-console-link-button" href="/dashboard-classic#page-${encodeURIComponent(String(action.page || ''))}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)} ↗</a>`;
    return '';
  }

  function renderAttention(selectedDate) {
    const panel = document.getElementById('dailyConsoleAttention');
    const list = document.getElementById('dailyConsoleAttentionList');
    if (!panel || !list) return;
    const items = (state.data.messages?.items || []).filter(item => !isTaskProposalMessage(item));
    if (!items.length) {
      panel.hidden = true;
      list.innerHTML = emptyMarkup(`${selectedDate} 没有其他未读提醒。`);
      return;
    }
    panel.hidden = false;
    list.innerHTML = items.slice(0, 12).map((item, index) => {
      const safeId = safeIdentifier(item.id, /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,180}$/);
      const normalized = { ...item, key: safeId || `message-${index}` };
      mapItem('message', normalized);
      return `<article class="daily-console-attention-item"><div><span class="daily-console-item-kicker">${escapeHtml(statusLabel(item.severity || 'info'))}</span><h4>${escapeHtml(firstText(item, ['title', 'type'], TEXT_LIMITS.title) || '系统提醒')}</h4></div><p>${escapeHtml(firstText(item, ['summary', 'message'], TEXT_LIMITS.summary) || '没有摘要。')}</p><div class="daily-console-item-meta"><span>${escapeHtml(formatDateTime(item.createdAt))}</span>${messageActionMarkup(item)}${safeId ? `<button class="daily-console-link-button" type="button" data-dc-action="message-read" data-dc-key="${escapeHtml(normalized.key)}">标记已读</button>` : '<span class="daily-console-muted">提醒标识不可用</span>'}</div></article>`;
    }).join('');
  }

  function renderAssetItem(item) {
    const key = mapItem('asset', item);
    const typeLabel = { experience: '经验', practice: '实践', skill: 'Skill', asset: '资产' }[item.type] || '资产';
    return `
      <article class="daily-console-item daily-console-asset-item ${escapeHtml(item.type)}" data-dc-item="asset" data-dc-key="${escapeHtml(item.key)}">
        <div class="daily-console-item-marker" aria-hidden="true">${escapeHtml(item.type === 'skill' ? 'S' : item.type === 'practice' ? 'P' : 'E')}</div>
        <div class="daily-console-item-main">
          <div class="daily-console-item-head"><div><span class="daily-console-item-kicker">${escapeHtml(typeLabel)}</span><h4>${escapeHtml(item.title)}</h4></div>${badgeMarkup(item.status)}</div>
          <p>${escapeHtml(item.summary)}</p>
          <div class="daily-console-item-meta"><span>${escapeHtml(item.source || 'Archive projection')}</span><span>${escapeHtml(formatDateTime(item.updatedAt))}</span></div>
          <button class="daily-console-link-button" type="button" data-dc-action="toggle-detail" data-dc-kind="asset" data-dc-key="${escapeHtml(item.key)}" aria-expanded="false">查看详情</button>
          <div class="daily-console-detail" data-dc-detail="asset:${escapeHtml(item.key)}" hidden><dl><div><dt>公开标识</dt><dd>${escapeHtml(item.id || '未提供')}</dd></div><div><dt>业务日期</dt><dd>${escapeHtml(item.date)}</dd></div><div><dt>来源</dt><dd>${escapeHtml(item.source || '—')}</dd></div></dl></div>
        </div>
      </article>`;
  }

  function renderTaskItem(item) {
    const key = mapItem('task', item);
    const actions = item.id
      ? `<div class="daily-console-item-actions"><button class="daily-console-button primary small" type="button" data-dc-action="task-confirm" data-dc-key="${escapeHtml(item.key)}">确认加入</button><button class="daily-console-button secondary small" type="button" data-dc-action="task-defer" data-dc-key="${escapeHtml(item.key)}">稍后处理</button><button class="daily-console-button danger small" type="button" data-dc-action="task-reject" data-dc-key="${escapeHtml(item.key)}">拒绝</button></div>`
      : '<span class="daily-console-muted">提案标识无效，已禁用写入动作。</span>';
    return `
      <article class="daily-console-item daily-console-task-item" data-dc-item="task" data-dc-key="${escapeHtml(item.key)}">
        <div class="daily-console-item-marker" aria-hidden="true">T</div>
        <div class="daily-console-item-main">
          <div class="daily-console-item-head"><div><span class="daily-console-item-kicker">L1 TASK PROPOSAL</span><h4>${escapeHtml(item.title)}</h4></div>${badgeMarkup(item.status)}</div>
          <p>${escapeHtml(item.reason)}</p>
          <div class="daily-console-item-meta"><span>${escapeHtml(item.workspace || item.source || 'Nova-Task')}</span><span>置信度 ${escapeHtml(confidenceLabel(item.confidence))}</span><span>${escapeHtml(item.evidenceCount === null ? '证据数量未知' : `${item.evidenceCount} 条证据`)}</span></div>
          <div class="daily-console-task-boundary"><span>候选 ${escapeHtml(item.id)}</span><span>页面只提交审核决定，不直接改写任务树。</span></div>
          ${actions}
        </div>
      </article>`;
  }

  function renderLessonItem(item) {
    const key = mapItem('lesson', item);
    const canSelect = lessonReviewable(item) || skillRegisterable(item);
    const checked = state.selectedLessons.has(item.key);
    const selector = canSelect
      ? `<label class="daily-console-check"><input type="checkbox" data-dc-lesson-select data-dc-key="${escapeHtml(item.key)}"${checked ? ' checked' : ''}><span>选择</span></label>`
      : '<span class="daily-console-muted">只读记录</span>';
    const proposalLabel = item.kind === 'skill' ? 'SKILL PROPOSAL' : 'LESSON PROPOSAL';
    return `
      <article class="daily-console-item daily-console-lesson-item ${escapeHtml(item.kind === 'skill' ? 'skill' : 'lesson')}" data-dc-item="lesson" data-dc-key="${escapeHtml(item.key)}">
        <div class="daily-console-item-marker" aria-hidden="true">${item.kind === 'skill' ? 'S' : 'L'}</div>
        <div class="daily-console-item-main">
          <div class="daily-console-item-head"><div><span class="daily-console-item-kicker">${proposalLabel}</span><h4>${escapeHtml(item.title)}</h4></div><div class="daily-console-item-head-right">${badgeMarkup(item.status)}${selector}</div></div>
          <p>${escapeHtml(item.summary)}</p>
          ${item.suggestion ? `<p class="daily-console-suggestion"><b>建议</b>${escapeHtml(item.suggestion)}</p>` : ''}
          <div class="daily-console-item-meta"><span>${escapeHtml(item.agent || item.source || 'Skill Pass')}</span><span>${escapeHtml(item.evidenceCount === null ? '证据数量未知' : `${item.evidenceCount} 条证据`)}</span><span>${escapeHtml(item.reviewId || '无 review ID')}</span></div>
          <button class="daily-console-link-button" type="button" data-dc-action="toggle-detail" data-dc-kind="lesson" data-dc-key="${escapeHtml(item.key)}" aria-expanded="false">查看详情</button>
          <div class="daily-console-detail" data-dc-detail="lesson:${escapeHtml(item.key)}" hidden><dl><div><dt>业务日期</dt><dd>${escapeHtml(item.date)}</dd></div><div><dt>提示版本</dt><dd>${escapeHtml(item.promptVersion || '—')}</dd></div><div><dt>来源</dt><dd>${escapeHtml(item.source || '—')}</dd></div></dl></div>
        </div>
      </article>`;
  }

  function blockMarkup(kind, body, count, action = '') {
    const copy = BLOCK_COPY[kind];
    return `
      <section class="daily-console-block daily-console-block-${escapeHtml(kind)}" data-dc-block="${escapeHtml(kind)}" aria-labelledby="daily-console-${escapeHtml(kind)}-title">
        <header class="daily-console-block-head"><div><span class="daily-console-eyebrow">${escapeHtml(copy.eyebrow)}</span><h3 id="daily-console-${escapeHtml(kind)}-title">${escapeHtml(copy.title)} <small>${escapeHtml(formatNumber(count))}</small></h3><p>${escapeHtml(copy.description)}</p></div>${action}</header>
        <div class="daily-console-list">${body}</div>
      </section>`;
  }

  function renderContent(selectedDate) {
    if (!state.content) return;
    state.itemMap.clear();
    const assets = state.data.assets?.items || [];
    const tasks = state.data.tasks?.items || [];
    const lessons = state.data.lessons?.items || [];
    if (state.loading) {
      if (state.hostMode) {
        ensureHostChrome(selectedDate);
        Object.values(state.hostLists).forEach(list => {
          if (list) {
            list.setAttribute('aria-busy', 'true');
            list.innerHTML = loadingMarkup();
          }
        });
      } else {
        state.content.innerHTML = `${renderControls(selectedDate)}${loadingMarkup()}`;
      }
      state.content.setAttribute('aria-busy', 'true');
      return;
    }
    const assetsBody = assets.length ? assets.map(renderAssetItem).join('') : emptyMarkup('当天没有新增的公开资产。');
    const tasksBody = tasks.length ? tasks.map(renderTaskItem).join('') : emptyMarkup('当天没有待处理的 Nova-Task L1 提案。');
    const lessonsBody = lessons.length ? lessons.map(renderLessonItem).join('') : emptyMarkup('当天没有经验教训提案。');
    const reviewableCount = lessons.filter(lessonReviewable).length;
    const registerableCount = lessons.filter(skillRegisterable).length;
    const lessonAction = reviewableCount || registerableCount
      ? `<div class="daily-console-proposal-actions">${reviewableCount ? '<button class="daily-console-button primary small" type="button" data-dc-action="crystallize-lessons" disabled>结晶已选 Lesson（0）</button>' : ''}${registerableCount ? '<button class="daily-console-button secondary small" type="button" data-dc-action="register-skills" disabled>注册已选 Skill（0）</button>' : ''}</div>`
      : '';
    const errors = state.errors.length
      ? `<div class="daily-console-source-errors" role="status"><b>部分数据源未返回</b>${state.errors.map(item => `<span>${escapeHtml(redactText(item.source, 60))}：${escapeHtml(item.message)}</span>`).join('')}</div>`
      : '';
    if (state.hostMode) {
      ensureHostChrome(selectedDate, errors);
      renderHostList('assets', assetsBody);
      renderHostList('tasks', tasksBody);
      renderHostList('lessons', `${lessonAction ? `<div class="daily-console-host-action" data-dc-generated="lesson-action">${lessonAction}</div>` : ''}${lessonsBody}`);
    } else {
      state.content.innerHTML = `${renderControls(selectedDate)}${errors}<div class="daily-console-grid">${blockMarkup('assets', assetsBody, assets.length)}${blockMarkup('tasks', tasksBody, tasks.length)}${blockMarkup('lessons', lessonsBody, lessons.length, lessonAction)}</div>`;
    }
    state.content.setAttribute('aria-busy', 'false');
    Object.values(state.hostLists).forEach(list => list?.setAttribute('aria-busy', 'false'));
    updateLessonAction();
  }

  /*
   * The integrated Archive page supplies three list slots.  Keep those host
   * nodes intact so the module can be embedded inside a pre-existing card
   * without replacing its heading, date input, or other controls.
   */
  function ensureHostChrome(selectedDate, errors = '') {
    if (!state.content) return;
    // The integrated Archive page already owns the title, date field and
    // refresh control.  Do not add a second “每日决策台” toolbar there; the
    // standalone embed path below still receives its generated toolbar.
    if (state.hostMode) {
      let sourceErrors = state.content.querySelector('[data-dc-generated="errors"]');
      if (errors) {
        if (!sourceErrors) {
          sourceErrors = document.createElement('div');
          sourceErrors.dataset.dcGenerated = 'errors';
          state.content.insertBefore(sourceErrors, state.content.firstChild);
        }
        sourceErrors.innerHTML = errors;
      } else if (sourceErrors) {
        sourceErrors.remove();
      }
      return;
    }
    let toolbar = state.content.querySelector('[data-dc-generated="toolbar"]');
    if (!toolbar) {
      const holder = document.createElement('div');
      holder.dataset.dcGenerated = 'toolbar';
      holder.innerHTML = renderControls(selectedDate);
      toolbar = holder.firstElementChild;
      toolbar.dataset.dcGenerated = 'toolbar';
      state.content.insertBefore(toolbar, state.content.firstChild);
    } else {
      const dateLabel = toolbar.querySelector('[data-dc-date-label]');
      if (dateLabel) dateLabel.textContent = selectedDate;
      const internalDate = toolbar.querySelector('[data-dc-date]');
      if (internalDate && internalDate.value !== selectedDate) internalDate.value = selectedDate;
    }
    let sourceErrors = state.content.querySelector('[data-dc-generated="errors"]');
    if (errors) {
      if (!sourceErrors) {
        sourceErrors = document.createElement('div');
        sourceErrors.dataset.dcGenerated = 'errors';
        toolbar.insertAdjacentElement('afterend', sourceErrors);
      }
      sourceErrors.innerHTML = errors;
    } else if (sourceErrors) {
      sourceErrors.remove();
    }
  }

  function renderHostList(kind, body) {
    const list = state.hostLists[kind];
    if (!list) return;
    list.innerHTML = body;
  }

  function renderAll(selectedDate = readDate()) {
    setDate(selectedDate);
    renderSummary(selectedDate);
    renderContent(selectedDate);
    renderAttention(selectedDate);
  }

  function payloadStatus(payload) {
    return String(payload?.status || '').toLowerCase();
  }

  async function fetchLessonProposals(selectedDate) {
    const endpoint = state.config.endpoints.lessonProposals;
    let dated;
    try {
      dated = await localRequest(appendQuery(endpoint, { businessDate: selectedDate }));
    } catch (error) {
      // Older hosts may expose the original no-query Skill Pass endpoint only.
      // Retry that safe read before reporting the source as unavailable.
      try {
        return await localRequest(endpoint);
      } catch (_) {
        throw error;
      }
    }
    const datedItems = asArray(dated, ['items', 'lessons', 'proposals']);
    // A daily ledger is optional.  The Skill Pass API's no-date form returns
    // the latest safe ledger, which keeps pending review visible on quiet days.
    if (datedItems.length || !['', 'empty'].includes(payloadStatus(dated))) return dated;
    return localRequest(endpoint);
  }

  async function fetchDailyData(selectedDate, serial) {
    const config = state.config;
    const limit = clampInteger(config.limit, DEFAULTS.limit, 1, 200);
    const assetLimit = clampInteger(config.assetLimit, DEFAULTS.assetLimit, 1, 200);
    const requests = {
      pipeline: localRequest(appendQuery(config.endpoints.pipeline, { date: selectedDate, limit })),
      assets: localRequest(appendQuery(config.endpoints.assets, { businessDate: selectedDate, limit: assetLimit, offset: 0 })),
      tasks: localRequest(appendQuery(config.endpoints.taskProposals, { status: 'pending_review', limit })),
      lessons: fetchLessonProposals(selectedDate),
      messages: localRequest(appendQuery(config.endpoints.messages, { limit: 20 }))
    };
    const settled = await Promise.allSettled(Object.values(requests));
    if (serial !== state.requestSerial) return null;
    const keys = Object.keys(requests);
    const result = { pipeline: null, assets: null, tasks: null, lessons: null, messages: null };
    const errors = [];
    settled.forEach((entry, index) => {
      const key = keys[index];
      if (entry.status === 'fulfilled') result[key] = entry.value;
      else errors.push(sourceError(key, entry.reason));
    });
    const normalized = {
      pipeline: result.pipeline,
      assets: { payload: result.assets, items: normalizeAssets(result.assets, selectedDate), counts: assetCounts(normalizeAssets(result.assets, selectedDate), result.assets) },
      tasks: { payload: result.tasks, items: normalizeTasks(result.tasks, selectedDate) },
      lessons: { payload: result.lessons, items: normalizeLessons(result.lessons, result.pipeline, selectedDate) },
      messages: { payload: result.messages, items: asArray(result.messages, ['items', 'messages']) }
    };
    // Some deployments expose the same projection under a custom endpoint and
    // return an explicit unavailable marker rather than a non-2xx response.
    [
      ['pipeline', result.pipeline],
      ['assets', result.assets],
      ['tasks', result.tasks],
      ['lessons', result.lessons],
      ['messages', result.messages]
    ].forEach(([key, payload]) => {
      if (payload && ['error', 'unavailable'].includes(payloadStatus(payload))) errors.push({ source: key, message: redactText(payload.error || payload.reason || '来源暂不可用', TEXT_LIMITS.error) });
    });
    return { data: normalized, errors };
  }

  async function refresh(options = {}) {
    if (!state.mounted || !state.content) return { status: 'not-mounted' };
    const requestedDate = options.forceBusinessDay ? '' : validDate(options.date) ? options.date : (validDate(state.dateInput?.value) ? state.dateInput.value : '');
    const selectedDate = await resolveBusinessDate(requestedDate);
    if (state.loadedDate && state.loadedDate !== selectedDate) state.selectedLessons.clear();
    state.loadedDate = selectedDate;
    setDate(selectedDate);
    state.loading = true;
    state.errors = [];
    const serial = ++state.requestSerial;
    renderAll(selectedDate);
    try {
      const loaded = await fetchDailyData(selectedDate, serial);
      if (!loaded || serial !== state.requestSerial) return { status: 'superseded' };
      state.data = loaded.data;
      state.errors = loaded.errors;
      state.loading = false;
      state.selectedLessons = new Set(Array.from(state.selectedLessons).filter(key => state.data.lessons.items.some(item => item.key === key && lessonReviewable(item))));
      renderAll(selectedDate);
      return { status: state.errors.length ? 'degraded' : 'ready', errors: state.errors.slice() };
    } catch (error) {
      if (serial !== state.requestSerial) return { status: 'superseded' };
      state.loading = false;
      state.errors = [sourceError('daily-console', error)];
      renderAll(selectedDate);
      return { status: 'error', errors: state.errors.slice() };
    }
  }

  function showToast(message, tone = 'neutral') {
    if (!state.content) return;
    let toast = state.content.querySelector('[data-dc-toast]');
    if (!toast) {
      toast = document.createElement('div');
      toast.dataset.dcToast = 'true';
      toast.className = 'daily-console-toast';
      toast.setAttribute('role', 'status');
      state.content.appendChild(toast);
    }
    toast.className = `daily-console-toast ${tone}`;
    toast.textContent = redactText(message, TEXT_LIMITS.error);
    window.clearTimeout(state.toastTimer);
    state.toastTimer = window.setTimeout(() => toast.remove(), 5000);
  }

  function updateLessonAction() {
    const selected = Array.from(state.selectedLessons).map(key => itemFromKey(key, 'lesson')).filter(Boolean);
    const lessons = selected.filter(item => item.kind !== 'skill' && lessonReviewable(item));
    const skills = selected.filter(skillRegisterable);
    const crystallize = state.content?.querySelector('[data-dc-action="crystallize-lessons"]');
    const register = state.content?.querySelector('[data-dc-action="register-skills"]');
    if (crystallize) {
      crystallize.disabled = lessons.length === 0 || state.loading;
      crystallize.textContent = `结晶已选 Lesson（${lessons.length}）`;
    }
    if (register) {
      register.disabled = skills.length === 0 || state.loading;
      register.textContent = `注册已选 Skill（${skills.length}）`;
    }
  }

  function toggleDetail(target) {
    const kind = String(target.dataset.dcKind || '');
    const key = String(target.dataset.dcKey || '');
    if (!['asset', 'lesson'].includes(kind) || !key) return;
    const detail = state.content?.querySelector(`[data-dc-detail="${cssEscape(`${kind}:${key}`)}"]`);
    if (!detail) return;
    const open = detail.hidden;
    detail.hidden = !open;
    target.setAttribute('aria-expanded', String(open));
    target.textContent = open ? '收起详情' : '查看详情';
  }

  function shiftDate(days) {
    const base = new Date(`${readDate()}T00:00:00`);
    base.setDate(base.getDate() + days);
    const next = base.toISOString().slice(0, 10);
    if (state.dateInput) state.dateInput.value = next;
    refresh({ date: next });
  }

  async function postTaskDecision(item, action) {
    const candidateId = safeCandidateId(item?.id);
    if (!candidateId || !['confirm', 'reject', 'defer'].includes(action)) {
      showToast('提案标识无效，未执行写入。', 'bad');
      return;
    }
    const endpointKey = action === 'confirm' ? 'taskConfirm' : action === 'reject' ? 'taskReject' : 'taskDefer';
    const endpoint = String(state.config.endpoints[endpointKey] || '').replace('{id}', encodeURIComponent(candidateId));
    if (!endpoint.startsWith('/api/')) {
      showToast('写入接口配置无效，未执行写入。', 'bad');
      return;
    }
    let reason = '';
    if (action !== 'confirm') {
      reason = window.prompt(action === 'reject' ? '可选：填写拒绝理由' : '可选：填写延期理由', '') || '';
      reason = redactText(reason, 500);
    }
    if (!window.confirm(action === 'confirm' ? `确认将“${item.title}”加入 Nova-Task？` : action === 'reject' ? `确认拒绝“${item.title}”？` : `确认暂缓“${item.title}”？`)) return;
    const button = state.content?.querySelector(`[data-dc-action="task-${action}"][data-dc-key="${cssEscape(item.key)}"]`);
    if (button) button.disabled = true;
    try {
      await localRequest(endpoint, { method: 'POST', body: reason ? { reason } : {} });
      showToast(`已${action === 'confirm' ? '确认' : action === 'reject' ? '拒绝' : '延期'} Nova-Task 提案。`, 'good');
      await refresh();
    } catch (error) {
      showToast(`Nova-Task 提案操作失败：${publicError(error)}`, 'bad');
      if (button) button.disabled = false;
    }
  }

  async function crystallizeLessons() {
    const items = Array.from(state.selectedLessons).map(key => itemFromKey(key, 'lesson')).filter(item => item && item.kind !== 'skill' && lessonReviewable(item));
    const reviewIds = Array.from(new Set(items.map(item => safeReviewId(item.reviewId)).filter(Boolean)));
    if (!reviewIds.length) {
      showToast('请选择带 review ID 的 Lesson。', 'bad');
      return;
    }
    const promptVersion = items.map(item => item.promptVersion).find(Boolean) || '';
    if (!/^minimal-v\d+$/.test(promptVersion)) {
      showToast('当前 Lesson 的提示版本不支持结晶。', 'bad');
      return;
    }
    if (!window.confirm(`确认请求结晶 ${reviewIds.length} 条 Lesson？这会调用现有 Skill Pass 写入链路。`)) return;
    const button = state.content?.querySelector('[data-dc-action="crystallize-lessons"]');
    if (button) button.disabled = true;
    try {
      await localRequest(state.config.endpoints.lessonCrystallize, {
        method: 'POST',
        body: { businessDate: items[0]?.date || readDate(), promptVersion, reviewIds }
      });
      state.selectedLessons.clear();
      showToast('Lesson 结晶请求已提交。', 'good');
      await refresh();
    } catch (error) {
      showToast(`Lesson 结晶失败：${publicError(error)}`, 'bad');
      updateLessonAction();
    }
  }

  async function registerSkills() {
    const items = Array.from(state.selectedLessons).map(key => itemFromKey(key, 'lesson')).filter(skillRegisterable);
    const reviewIds = Array.from(new Set(items.map(item => safeReviewId(item.reviewId)).filter(Boolean)));
    if (!reviewIds.length) {
      showToast('请选择可注册的 Skill 提案。', 'bad');
      return;
    }
    const promptVersion = items.map(item => item.promptVersion).find(Boolean) || '';
    if (!/^minimal-v\d+$/.test(promptVersion)) {
      showToast('当前 Skill 提案的提示版本不支持注册。', 'bad');
      return;
    }
    if (!window.confirm(`确认注册 ${reviewIds.length} 个 Skill？系统会写入 Actanara 主库并同步支持的 Agent。`)) return;
    const button = state.content?.querySelector('[data-dc-action="register-skills"]');
    if (button) button.disabled = true;
    try {
      await localRequest(state.config.endpoints.skillRegister, {
        method: 'POST',
        body: { businessDate: items[0]?.date || readDate(), promptVersion, reviewIds }
      });
      items.forEach(item => state.selectedLessons.delete(item.key));
      showToast('Skill 注册请求已提交。', 'good');
      await refresh();
    } catch (error) {
      showToast(`Skill 注册失败：${publicError(error)}`, 'bad');
      updateLessonAction();
    }
  }

  async function markMessageRead(item) {
    const id = safeIdentifier(item?.id, /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,180}$/);
    if (!id) {
      showToast('提醒标识无效，未执行写入。', 'bad');
      return;
    }
    try {
      const endpoint = String(state.config.endpoints.messageRead || '').replace('{id}', encodeURIComponent(id));
      await localRequest(endpoint, { method: 'POST', body: {} });
      showToast('提醒已标记为已读。', 'good');
      await refresh();
    } catch (error) {
      showToast(`标记提醒失败：${publicError(error)}`, 'bad');
    }
  }

  function handleClick(event) {
    const target = event.target.closest?.('[data-dc-action]');
    const extendedRefresh = event.target.closest?.('[data-extended-action="daily-console-refresh"]');
    if (extendedRefresh && state.eventRoot?.contains(extendedRefresh)) {
      event.preventDefault();
      refresh();
      return;
    }
    if (!target || !state.eventRoot?.contains(target)) return;
    const action = String(target.dataset.dcAction || '');
    if (action === 'refresh') refresh();
    else if (action === 'previous-date') shiftDate(-1);
    else if (action === 'next-date') shiftDate(1);
    else if (action === 'toggle-detail') toggleDetail(target);
    else if (action === 'crystallize-lessons') crystallizeLessons();
    else if (action === 'register-skills') registerSkills();
    else if (action === 'message-read') {
      const message = itemFromKey(target.dataset.dcKey, 'message');
      if (message) markMessageRead(message);
    }
    else if (action.startsWith('task-')) {
      const task = itemFromKey(target.dataset.dcKey, 'task');
      if (task) postTaskDecision(task, action.slice('task-'.length));
    }
  }

  function handleChange(event) {
    const target = event.target;
    if (target.matches?.('[data-dc-date], #dailyConsoleDate, [data-daily-console-date]')) {
      if (target === state.dateInput) state.dateInput.value = target.value;
      if (!validDate(target.value)) {
        target.value = readDate();
        showToast('请选择有效的业务日期。', 'bad');
        return;
      }
      if (state.dateInput && state.dateInput !== target) state.dateInput.value = target.value;
      refresh({ date: target.value });
      return;
    }
    if (target.matches?.('[data-dc-lesson-select]')) {
      const key = String(target.dataset.dcKey || '');
      const item = itemFromKey(key, 'lesson');
      if (!item || !lessonReviewable(item)) {
        target.checked = false;
        return;
      }
      if (target.checked) state.selectedLessons.add(item.key);
      else state.selectedLessons.delete(item.key);
      updateLessonAction();
    }
  }

  function resolveContainers(config) {
    const content = $(config.contentSelector);
    const summary = $(config.summarySelector);
    const configuredRoot = $(config.rootSelector);
    const root = configuredRoot || content?.closest('[data-daily-console]') || summary?.closest('[data-daily-console]') || content?.parentElement || summary?.parentElement;
    const queryRoot = root || document;
    return {
      root,
      content,
      summary,
      lists: {
        assets: $('#dailyConsoleAssets', queryRoot),
        tasks: $('#dailyConsoleTasks', queryRoot),
        lessons: $('#dailyConsoleLessons', queryRoot)
      },
      dateInput: $(config.dateSelector) || $('[data-daily-console-date]', queryRoot)
    };
  }

  function mount(options = {}) {
    state.config = mergeConfig(options);
    const containers = resolveContainers(state.config);
    state.root = containers.root;
    state.eventRoot = containers.root || containers.content || containers.summary;
    state.content = containers.content;
    state.summary = containers.summary;
    state.hostLists = containers.lists;
    state.hostMode = Boolean(state.hostLists.assets || state.hostLists.tasks || state.hostLists.lessons);
    const alreadyMounted = state.mounted && state.content === containers.content && state.summary === containers.summary;
    if (!state.content && state.root) {
      state.content = document.createElement('div');
      state.content.id = 'dailyConsoleContent';
      state.root.appendChild(state.content);
    }
    if (!state.summary && state.root) {
      state.summary = document.createElement('div');
      state.summary.id = 'dailyConsoleSummary';
      state.root.insertBefore(state.summary, state.content || null);
    }
    if (!state.content && !state.summary) return { status: 'not-mounted' };
    state.dateInput = containers.dateInput || state.root?.querySelector('[data-daily-console-date]') || null;
    const requestedDate = validDate(options.date) ? options.date : state.dateInput?.value;
    // The visible date input wins.  Otherwise ask TokenClock for the
    // configured business day (the local calendar date is only a fallback for
    // hosts that do not expose that read-only endpoint).
    const selectedDate = setDate(validDate(requestedDate) ? requestedDate : today());
    if (!state.mounted) {
      state.eventRoot?.addEventListener('click', handleClick);
      state.eventRoot?.addEventListener('change', handleChange);
      state.mounted = true;
    }
    renderAll(selectedDate);
    // The page is mounted after app.js has selected the initial route.  Start
    // the first API read here; otherwise the three host lists would remain in
    // their static loading state until the user manually presses refresh.
    if (options.autoRefreshMs || state.config.autoRefreshMs) {
      const interval = clampInteger(options.autoRefreshMs || state.config.autoRefreshMs, 0, 0, 3600000);
      window.clearInterval(state.liveTimer);
      if (interval > 0) state.liveTimer = window.setInterval(() => refresh(), interval);
    }
    const loadPromise = options.load === false || alreadyMounted
      ? Promise.resolve({ status: 'mounted' })
      : (validDate(requestedDate)
        ? refresh({ date: selectedDate })
        : refresh({ forceBusinessDay: true }));
    return { status: 'mounted', refresh, loadPromise };
  }

  function destroy() {
    window.clearInterval(state.liveTimer);
    state.liveTimer = null;
    state.eventRoot?.removeEventListener('click', handleClick);
    state.eventRoot?.removeEventListener('change', handleChange);
    state.mounted = false;
    state.requestSerial += 1;
    state.itemMap.clear();
    state.selectedLessons.clear();
    state.loadedDate = '';
  }

  function getState() {
    const assets = state.data.assets?.items || [];
    const tasks = state.data.tasks?.items || [];
    const lessons = state.data.lessons?.items || [];
    return Object.freeze({
      mounted: state.mounted,
      loading: state.loading,
      date: readDate(),
      counts: {
        assets: assets.length,
        tasks: tasks.length,
        lessons: lessons.length,
        selectedLessons: state.selectedLessons.size
      },
      errors: state.errors.map(item => ({ source: redactText(item.source, 60), message: redactText(item.message, TEXT_LIMITS.error) }))
    });
  }

  const api = Object.freeze({
    init: mount,
    mount,
    refresh,
    destroy,
    getState,
    sanitize: redactText
  });
  window.LivingArchiveDailyConsole = api;

  function autoMount() {
    const hasHost = Boolean($('#dailyConsoleContent') || $('#dailyConsoleSummary') || $('[data-daily-console]'));
    if (hasHost) {
      mount();
      return;
    }
    if (typeof MutationObserver === 'undefined' || !document.documentElement) return;
    const observer = new MutationObserver(() => {
      if ($('#dailyConsoleContent') || $('#dailyConsoleSummary') || $('[data-daily-console]')) {
        observer.disconnect();
        mount();
      }
    });
    observer.observe(document.documentElement, { childList: true, subtree: true });
    window.setTimeout(() => observer.disconnect(), 30000);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', autoMount, { once: true });
  else autoMount();
})();
