(() => {
  'use strict';

  const api = () => window.LivingArchiveApi;
  const state = {
    loaded: new Set(),
    liveTimer: null,
    review: null,
    reviewSelected: new Set(),
    backfill: { plan: null, payload: null },
    llmChain: null,
  };

  const $ = selector => document.querySelector(selector);
  const $$ = selector => Array.from(document.querySelectorAll(selector));
  const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
  const number = value => Number.isFinite(Number(value)) ? Number(value) : null;
  const formatNumber = value => number(value) === null ? '—' : Number(value).toLocaleString('zh-CN');
  const formatTokens = value => {
    const parsed = number(value);
    if (parsed === null) return '—';
    if (parsed >= 1e9) return `${(parsed / 1e9).toFixed(2)}B`;
    if (parsed >= 1e6) return `${(parsed / 1e6).toFixed(1)}M`;
    if (parsed >= 1e3) return `${(parsed / 1e3).toFixed(1)}K`;
    return String(parsed);
  };
  const today = () => new Date().toISOString().slice(0, 10);
  const dateBefore = days => {
    const value = new Date(`${today()}T00:00:00`);
    value.setDate(value.getDate() - days);
    return value.toISOString().slice(0, 10);
  };
  const dateTime = value => value ? String(value).replace('T', ' ').slice(0, 19) : '—';
  const statusLabels = {
    ready: '正常', empty: '暂无数据', degraded: '部分可用', unsupported: '暂不支持', unknown: '未知',
    stale: '需要更新', error: '读取失败', loading: '加载中', queued: '排队中', running: '运行中',
    completed: '已完成', failed: '失败', cancelled: '已取消', configured: '已配置', 'never-run': '尚未运行',
    incomplete: '不完整', blocked: '已阻止', pending: '等待处理'
  };
  const assetLabels = { skill: 'Skill', lesson: '经验', experience: '经验', practice: '实践', reference: '参考', discard: '已舍弃' };
  const assetStatus = { draft: '草稿', verified: '已验证', documented: '已记录', active: '已启用', ready: '待审核', unknown: '状态未知' };
  const libraryActionLabels = { create: '新建 Skill', extend: '扩展已有 Skill', covered: '已有 Skill 已覆盖', conflict: '与已有 Skill 冲突', reject: '拒绝进入 Skill 库' };
  const scoreLabels = { evidence: '证据', value: '价值', reuse: '复用', program: '可执行' };
  const fieldLabels = {
    status: '状态', businessDate: '业务日期', diaryStatus: '日记状态', foundationStatus: 'Foundation 状态',
    pipelineStatus: '管线状态', issueCount: '问题数', repairCount: '修复记录', ready: '可用状态', runCount: '运行次数',
    generatedFileCount: '生成文件数', experienceCount: '经验数', practiceCount: '实践数', skillCount: 'Skill 数',
    taskEvidenceCount: '任务证据数', errorCount: '错误数', activityState: '活动状态', generatedAt: '生成时间'
  };
  const jobScopeLabels = {
    'period-assets': '周期资产', 'period-summary': '周期总结', 'dashboard-projection-refresh': 'Dashboard 数据刷新',
    'history-backfill': '历史数据补齐', 'daily-pipeline': '每日处理', 'dashboard-aggregation': 'Dashboard 汇总'
  };

  function statusLabel(value) {
    return statusLabels[String(value || '').toLowerCase()] || assetStatus[String(value || '').toLowerCase()] || value || '未知';
  }

  function jobField(job, ...keys) {
    for (const key of keys) {
      const value = key.split('.').reduce((current, part) => current?.[part], job);
      if (value !== undefined && value !== null && value !== '') return value;
    }
    return null;
  }

  function jobScopeLabel(value) {
    const key = String(value || '').toLowerCase();
    return jobScopeLabels[key] || value || '周期数据';
  }

  async function request(path, options = {}) {
    if (!api()?.request) throw new Error('本地数据客户端未加载');
    return api().request(path, options);
  }

  function result(id, message, tone = '') {
    const target = document.getElementById(id);
    if (!target) return;
    target.className = `extended-result ${tone}`.trim();
    target.innerHTML = message ? `<div>${escapeHtml(message)}</div>` : '';
  }

  function errorMessage(error) {
    return String(error?.message || error || '读取失败');
  }

  function safeValue(key, value) {
    if (/(?:path|secret|password|apikey|credential|markdown|raw|locator|recordid|session)/i.test(String(key))) return '已隐藏';
    if (Array.isArray(value)) return `${value.length} 项`;
    if (value && typeof value === 'object') return `包含 ${Object.keys(value).length} 个字段`;
    if (value === null || value === undefined || value === '') return '—';
    if (typeof value === 'boolean') return value ? '是' : '否';
    return String(value);
  }

  function summaryMarkup(payload, keys = []) {
    if (!payload || typeof payload !== 'object') return '<div class="empty-inline">没有可显示的数据。</div>';
    const source = payload.summary && typeof payload.summary === 'object' ? payload.summary : payload;
    const selected = keys.length ? keys : Object.keys(source).slice(0, 10);
    const rows = selected.filter(key => Object.prototype.hasOwnProperty.call(source, key)).map(key => {
      const raw = source[key];
      const value = typeof raw === 'string' && statusLabels[raw.toLowerCase()] ? statusLabel(raw) : safeValue(key, raw);
      return `<div><span>${escapeHtml(fieldLabels[key] || key)}</span><b>${escapeHtml(value)}</b></div>`;
    }).join('');
    return rows ? `<div class="extended-summary-list">${rows}</div>` : '<div class="empty-inline">没有可显示的摘要。</div>';
  }

  function renderLive(data) {
    const status = $('#operationsStatus');
    if (status) {
      status.innerHTML = `<span class="status-seal">${data?.degraded ? '部分' : '正常'}</span><div><b>${data?.degraded ? '实时数据部分可用' : '实时数据可用'}</b><span>TokenClock · 更新时间 ${escapeHtml(dateTime(data?.timestamp))}</span></div>`;
    }
    const values = [
      ['liveTokenTotal', formatTokens(data?.totalTokens)],
      ['liveMessageTotal', formatNumber(data?.totalMessages)],
      ['liveCacheRate', number(data?.overallCacheRate) === null ? '—' : `${Number(data.overallCacheRate).toFixed(1)}%`],
      ['liveHourlyRate', '—'],
      ['liveActiveTools', '—']
    ];
    const timeline = Array.isArray(data?.hourlyTimeline) ? data.hourlyTimeline : [];
    const currentHour = String(new Date().getHours()).padStart(2, '0');
    const current = timeline.find(item => String(item?.hour).padStart(2, '0') === currentHour);
    values[3][1] = `${formatTokens(current?.tokens)} / 小时`;
    const tools = Array.isArray(data?.tools) ? data.tools : [];
    values[4][1] = `${tools.filter(item => item?.isActive).length} / ${tools.length}`;
    values.forEach(([id, value]) => { const element = document.getElementById(id); if (element) element.textContent = value; });
    const max = Math.max(1, ...timeline.map(item => number(item?.tokens) || 0));
    const grid = $('#liveHourGrid');
    if (grid) grid.innerHTML = timeline.length ? timeline.map(item => {
      const hour = String(item?.hour ?? '').padStart(2, '0');
      const tokens = number(item?.tokens) || 0;
      return `<div class="live-hour-cell ${hour === currentHour ? 'current' : ''}" title="${escapeHtml(hour)}:00 · ${escapeHtml(formatTokens(tokens))} Token"><i style="height:${Math.max(4, tokens / max * 100)}%"></i><span>${escapeHtml(hour)}</span></div>`;
    }).join('') : '<div class="empty-inline">当前没有小时活动数据。</div>';
    const table = $('#liveWorkspaceTable tbody');
    const workspaces = Array.isArray(data?.workspaceUsage) ? data.workspaceUsage : [];
    if (table) table.innerHTML = workspaces.length ? workspaces.slice(0, 30).map(item => `<tr><td>${escapeHtml(item.name || '未命名工作区')}</td><td>${escapeHtml(item.tool || '—')}</td><td>${escapeHtml(formatTokens(item.tokens))}</td><td>${escapeHtml(formatNumber(item.messages))}</td><td><span class="status-text ${item.isActive ? 'good-text' : ''}">${item.isActive ? '当前活跃' : '今日有记录'}</span></td></tr>`).join('') : '<tr><td colspan="5">今天没有工作区活动记录。</td></tr>';
    const updated = $('#liveTokenUpdatedAt');
    if (updated) updated.textContent = `TokenClock 更新时间：${dateTime(data?.timestamp)}；业务日按本机配置计算。`;
  }

  async function loadLive() {
    try {
      const data = await request('/api/token-clock');
      renderLive(data);
    } catch (error) {
      result('liveHourGrid', `无法读取 TokenClock：${errorMessage(error)}`, 'error');
      ['liveTokenTotal', 'liveMessageTotal', 'liveCacheRate', 'liveHourlyRate', 'liveActiveTools'].forEach(id => { const el = document.getElementById(id); if (el) el.textContent = '—'; });
      if ($('#operationsStatus')) $('#operationsStatus').innerHTML = `<span class="status-seal">失败</span><div><b>无法读取实时数据</b><span>${escapeHtml(errorMessage(error))}</span></div>`;
    }
  }

  function severityLabel(value) {
    return value === 'error' ? '错误' : value === 'warn' ? '提醒' : '信息';
  }

  function messageActionMarkup(item) {
    const action = item?.action && typeof item.action === 'object' ? item.action : {};
    const label = item?.actionLabel || '打开相关页面';
    if (action.kind === 'openUrl' && String(action.url || '').startsWith('/')) return `<a class="button small secondary" href="${escapeHtml(action.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)}</a>`;
    if (action.kind === 'openPage') return `<a class="button small secondary" href="/dashboard-classic#page-${encodeURIComponent(String(action.page || ''))}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)}</a>`;
    return '';
  }

  async function loadMessages() {
    const panel = $('#archiveMessageList');
    if (!panel) return;
    try {
      const payload = await request('/api/msgbox?limit=20');
      const items = Array.isArray(payload?.items) ? payload.items : [];
      panel.innerHTML = items.length ? items.map(item => `<article class="message-item ${escapeHtml(item.severity || 'info')}"><div class="message-item-head"><b>${escapeHtml(item.title || item.type || '消息')}</b><span>${escapeHtml(severityLabel(item.severity))}</span></div><p>${escapeHtml(item.summary || '没有摘要')}</p><small>${escapeHtml(dateTime(item.createdAt))}</small><div class="extended-button-row message-actions">${messageActionMarkup(item)}<button class="button small secondary" type="button" data-extended-action="message-read" data-message-id="${escapeHtml(item.id || '')}">标记已读</button></div></article>`).join('') : '<div class="empty-inline">暂无待处理消息。</div>';
    } catch (error) {
      panel.innerHTML = `<div class="extended-error">无法读取消息：${escapeHtml(errorMessage(error))}</div>`;
    }
  }

  async function markMessageRead(id) {
    if (!id) return;
    try {
      await request(`/api/msgbox/${encodeURIComponent(id)}/read`, { method: 'POST' });
      await loadMessages();
    } catch (error) {
      result('archiveMessageList', `消息未能标记为已读：${errorMessage(error)}`, 'error');
    }
  }

  async function loadTasks() {
    const panel = $('#archiveTaskList');
    if (!panel) return;
    try {
      const payload = await request('/api/background-tasks?limit=20');
      const items = Array.isArray(payload?.tasks) ? payload.tasks : [];
      panel.innerHTML = items.length ? items.slice(0, 8).map(item => {
        const actions = Array.isArray(item.actions) ? item.actions.filter(action => action?.kind === 'apiPost' && String(action.url || '').startsWith('/api/')).slice(0, 2) : [];
        const actionHtml = actions.map(action => `<button class="button small secondary" type="button" data-extended-action="task-action" data-task-url="${escapeHtml(action.url)}" data-task-confirm="${escapeHtml(action.confirm || '')}">${escapeHtml(action.label || '执行')}</button>`).join('');
        return `<article class="task-item"><div class="task-item-head"><b>${escapeHtml(item.title || item.id || '后台任务')}</b><span>${escapeHtml(statusLabel(item.status))}</span></div><p>${escapeHtml(item.subtitle || '')}</p><div class="task-progress"><i style="width:${Math.max(0, Math.min(100, Number(item.progress || 0)))}%"></i></div><small>${escapeHtml(item.source || '来源未知')} · ${escapeHtml(dateTime(item.startedAt))}</small>${actionHtml ? `<div class="extended-button-row">${actionHtml}</div>` : ''}</article>`;
      }).join('') : '<div class="empty-inline">暂无后台任务。</div>';
    } catch (error) {
      panel.innerHTML = `<div class="extended-error">无法读取后台任务：${escapeHtml(errorMessage(error))}</div>`;
    }
  }

  async function taskAction(target) {
    const url = String(target.dataset.taskUrl || '');
    if (!url.startsWith('/api/')) return;
    const confirmText = target.dataset.taskConfirm;
    if (confirmText && !window.confirm(`确认执行此后台任务？\n\n${confirmText}`)) return;
    target.disabled = true;
    try {
      await request(url, { method: 'POST' });
      await loadTasks();
    } catch (error) {
      result('archiveTaskList', `后台任务操作失败：${errorMessage(error)}`, 'error');
    } finally {
      target.disabled = false;
    }
  }

  function setDateDefaults() {
    ['opsQaDate', 'opsPipelineDate'].forEach(id => { const input = document.getElementById(id); if (input && !input.value) input.value = today(); });
    const start = $('#opsBackfillStart');
    const end = $('#opsBackfillEnd');
    if (start && !start.value) start.value = dateBefore(7);
    if (end && !end.value) end.value = today();
  }

  async function loadQa() {
    const date = $('#opsQaDate')?.value || today();
    const panel = $('#opsQaResult');
    if (!panel) return;
    panel.innerHTML = '<div class="empty-inline">正在读取 QA…</div>';
    try {
      const payload = await request(`/api/foundation/ops/daily-qa?date=${encodeURIComponent(date)}&limit=30`);
      panel.innerHTML = summaryMarkup(payload, ['status', 'businessDate', 'diaryStatus', 'foundationStatus', 'pipelineStatus', 'issueCount', 'repairCount', 'ready']);
    } catch (error) {
      panel.innerHTML = `<div class="extended-error">无法读取 QA：${escapeHtml(errorMessage(error))}</div>`;
    }
  }

  async function loadPipeline() {
    const date = $('#opsPipelineDate')?.value || today();
    const panel = $('#opsPipelineResult');
    if (!panel) return;
    panel.innerHTML = '<div class="empty-inline">正在读取当天处理结果…</div>';
    try {
      const payload = await request(`/api/foundation/ops/daily-pipeline-summary?date=${encodeURIComponent(date)}&limit=30`);
      panel.innerHTML = summaryMarkup(payload, ['status', 'businessDate', 'runCount', 'generatedFileCount', 'experienceCount', 'practiceCount', 'skillCount', 'taskEvidenceCount', 'errorCount']);
    } catch (error) {
      panel.innerHTML = `<div class="extended-error">无法读取处理结果：${escapeHtml(errorMessage(error))}</div>`;
    }
  }

  async function pollRefreshJob(runId, targetId) {
    for (let attempt = 0; attempt < 90; attempt += 1) {
      const payload = await request(`/api/foundation/refresh-jobs/${encodeURIComponent(runId)}`);
      const status = payload?.status || 'unknown';
      result(targetId, `刷新任务 #${runId}：${statusLabel(status)}`, status === 'failed' ? 'error' : '');
      if (['completed', 'failed', 'cancelled'].includes(status)) return payload;
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
    throw new Error('刷新任务等待超时');
  }

  async function foundationRefresh(target) {
    const days = Number(target.dataset.days || 7);
    const start = days === 1 ? today() : dateBefore(days - 1);
    if (!window.confirm(`确认刷新 ${start} 至 ${today()} 的数据？\n\n系统会在后台生成或更新周期数据。`)) return;
    target.disabled = true;
    result('opsRefreshResult', '正在提交刷新任务…');
    try {
      const queued = await request('/api/weekly-report/refresh', { method: 'POST', body: { start, days } });
      if (queued?.runId === undefined) throw new Error('刷新服务没有返回任务编号');
      await pollRefreshJob(queued.runId, 'opsRefreshResult');
      await loadQa();
      await loadPipeline();
    } catch (error) {
      result('opsRefreshResult', `刷新失败：${errorMessage(error)}`, 'error');
    } finally {
      target.disabled = false;
    }
  }

  async function loadRefreshJobs() {
    const panel = $('#opsRefreshJobs');
    if (!panel) return;
    try {
      const payload = await request('/api/foundation/refresh-jobs?limit=10');
      const jobs = Array.isArray(payload?.jobs) ? payload.jobs : Array.isArray(payload?.items) ? payload.items : [];
      panel.innerHTML = jobs.length ? jobs.map(job => {
        const id = jobField(job, 'runId', 'run_id', 'id') || '—';
        const started = jobField(job, 'startedAt', 'started_at', 'createdAt', 'created_at');
        return `<div class="extended-list-row"><b>#${escapeHtml(id)}</b><span>${escapeHtml(statusLabel(job.status))}</span><small>${escapeHtml(dateTime(started))}</small></div>`;
      }).join('') : '<div class="empty-inline">暂无刷新任务记录。</div>';
    } catch (error) {
      panel.innerHTML = `<div class="extended-error">无法读取刷新任务：${escapeHtml(errorMessage(error))}</div>`;
    }
  }

  async function loadFoundationJobs() {
    const table = $('#foundationJobs');
    if (!table) return;
    try {
      const payload = await request('/api/foundation/refresh-jobs?limit=10');
      const jobs = Array.isArray(payload?.jobs) ? payload.jobs : Array.isArray(payload?.items) ? payload.items : [];
      const header = '<div class="job-head"><span>运行编号</span><span>处理范围</span><span>日期范围</span><span>耗时</span><span>状态</span></div>';
      table.innerHTML = header + (jobs.length ? jobs.map(job => {
        const id = jobField(job, 'runId', 'run_id', 'id') || '—';
        const scope = jobScopeLabel(jobField(job, 'scope', 'type', 'metadata.scope', 'trigger_type'));
        const start = jobField(job, 'startDate', 'start_date', 'metadata.periodStart', 'businessDate', 'business_date');
        const end = jobField(job, 'endDate', 'end_date', 'metadata.periodEnd', 'businessDate', 'business_date');
        const dateRange = start && end && String(start) !== String(end) ? `${start} — ${end}` : start || '—';
        const started = jobField(job, 'startedAt', 'started_at');
        const completed = jobField(job, 'completedAt', 'completed_at');
        const explicitDuration = jobField(job, 'durationSeconds', 'duration_seconds', 'metadata.durationSeconds');
        const duration = explicitDuration !== null ? `${explicitDuration} 秒` : started && completed ? `${Math.max(0, (new Date(completed) - new Date(started)) / 1000).toFixed(1)} 秒` : '—';
        return `<div class="extended-job-row"><b>#${escapeHtml(id)}</b><span>${escapeHtml(scope)}</span><span>${escapeHtml(dateRange)}</span><span>${escapeHtml(duration)}</span><strong>${escapeHtml(statusLabel(job.status))}</strong></div>`;
      }).join('') : '<div class="ledger-state-row">暂无 Foundation 运行记录。</div>');
    } catch (error) {
      table.innerHTML = `<div class="ledger-state-row">无法读取 Foundation 运行记录：${escapeHtml(errorMessage(error))}</div>`;
    }
  }

  function periodStart(days) {
    if (Number(days) === 30) {
      const now = new Date(`${today()}T00:00:00`);
      return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-01`;
    }
    if (Number(days) === 7) {
      const now = new Date(`${today()}T00:00:00`);
      const mondayOffset = (now.getDay() + 6) % 7;
      now.setDate(now.getDate() - mondayOffset);
      return now.toISOString().slice(0, 10);
    }
    return dateBefore(Math.max(0, Number(days || 7) - 1));
  }

  function periodRequestDays(days) {
    if (Number(days) !== 30) return Number(days || 7);
    const now = new Date(`${today()}T00:00:00`);
    // The current month is still in progress.  Request the elapsed portion so
    // future empty dates do not make the report look like a zeroed snapshot.
    return now.getDate();
  }

  function renderPeriodReport(payload, days) {
    const panel = $('#periodReportContent');
    if (!panel) return;
    const kpi = payload?.kpi || {};
    const metrics = [
      ['Token', formatTokens(kpi.totalTokens)],
      ['消息', formatNumber(kpi.totalMessages)],
      ['缓存命中率', number(kpi.cacheHitRate) === null ? '—' : `${Number(kpi.cacheHitRate).toFixed(1)}%`],
      ['活跃会话', formatNumber(kpi.activeSessions ?? kpi.totalSessions)],
      ['有数据的天数', formatNumber(payload?.days)]
    ];
    const series = Array.isArray(payload?.dailyTokenSeries) ? payload.dailyTokenSeries : [];
    const max = Math.max(1, ...series.map(item => number(item.tokens) || 0));
    const bars = series.slice(-days).map(item => `<div title="${escapeHtml(item.date || '')} · ${escapeHtml(formatTokens(item.tokens))} Token"><i style="height:${Math.max(3, (number(item.tokens) || 0) / max * 100)}%"></i><small>${escapeHtml(String(item.displayDate || item.date || '').slice(5))}</small></div>`).join('');
    const models = Array.isArray(payload?.models) && payload.models.length ? payload.models.slice(0, 8) : Array.isArray(payload?.modelUsage) ? payload.modelUsage.slice(0, 8) : [];
    const modelRows = models.map(item => `<div><span>${escapeHtml(item.model || item.name || '模型')}</span><b>${escapeHtml(formatTokens(item.tokens))}</b></div>`).join('');
    const topics = Array.isArray(payload?.highFrequencyTopics || payload?.topTopics) ? (payload.highFrequencyTopics || payload.topTopics).slice(0, 8) : [];
    const topicText = topics.map(item => escapeHtml(typeof item === 'string' ? item : item.title || item.name || '')).filter(Boolean).join('、');
    const summary = typeof payload?.periodSummary === 'string' ? payload.periodSummary : payload?.periodSummary?.text || payload?.periodSummary?.summary || '';
    const freshness = payload?.dataFreshness?.periodPage?.generatedAt || payload?.dataFreshness?.periodSummary?.generatedAt;
    const agentActivity = payload?.agentActivity && typeof payload.agentActivity === 'object' ? Object.entries(payload.agentActivity).filter(([, item]) => item && typeof item === 'object').slice(0, 8) : [];
    const agentRows = agentActivity.map(([name, item]) => `<div class="extended-list-row"><b>${escapeHtml(name)}</b><span>${escapeHtml(formatTokens(item.tokens))} Token</span><small>${escapeHtml(formatNumber(item.messages))} 条消息 · 活跃 ${escapeHtml(formatNumber(item.days_active ?? item.daysActive))} 天</small></div>`).join('');
    const workspaceRows = (Array.isArray(payload?.workspaceUsage) ? payload.workspaceUsage : []).slice(0, 8).map(item => `<div class="extended-list-row"><b>${escapeHtml(item.name || '未命名工作区')}</b><span>${escapeHtml(formatTokens(item.tokens))} Token</span><small>${escapeHtml(item.tool || '—')} · ${escapeHtml(formatNumber(item.messages))} 条消息</small></div>`).join('');
    const task = payload?.taskStats || {};
    const cron = payload?.cronStats || {};
    const rag = payload?.ragStats || payload?.knowledgePeriod?.rag || {};
    const memory = payload?.memoryStats || payload?.knowledgePeriod?.memory || {};
    const heatmap = payload?.hourlyHeatmap && Array.isArray(payload.hourlyHeatmap.dates) && Array.isArray(payload.hourlyHeatmap.periods) ? (() => {
      const values = payload.hourlyHeatmap.dates.map((date, index) => ({ date, value: payload.hourlyHeatmap.periods.reduce((sum, period) => sum + (Number(period?.values?.[index]) || 0), 0) }));
      const maxValue = Math.max(1, ...values.map(item => item.value));
      return `<div class="period-heatmap">${values.map(item => `<i data-level="${item.value === 0 ? 0 : Math.min(4, Math.ceil(item.value / maxValue * 4))}" title="${escapeHtml(item.date)} · ${escapeHtml(formatTokens(item.value))} Token"></i>`).join('')}</div>`;
    })() : '';
    const lessonCount = Array.isArray(payload?.lessons) ? payload.lessons.length : null;
    const detailCards = `<div class="period-detail-grid"><section class="period-detail-card"><h4>AGENT 活动</h4>${agentRows || '<p>暂无 Agent 活动数据。</p>'}</section><section class="period-detail-card"><h4>工作区活动</h4>${workspaceRows || '<p>暂无工作区活动数据。</p>'}</section><section class="period-detail-card"><h4>任务、定时任务与知识库</h4><div class="extended-summary-list"><div><span>任务完成</span><b>${escapeHtml(formatNumber(task.completed))} / ${escapeHtml(formatNumber(task.total))}</b></div><div><span>定时任务成功率</span><b>${escapeHtml(cron.rate === undefined ? '—' : `${cron.rate}%`)}</b></div><div><span>Local FTS 文档</span><b>${escapeHtml(formatNumber(memory.currentCount ?? memory.sessionFiles))}</b></div><div><span>RAG 索引条目</span><b>${escapeHtml(formatNumber(rag.currentCount ?? rag.entries))}</b></div><div><span>经验条目</span><b>${escapeHtml(formatNumber(lessonCount))}</b></div></div></section></div>${heatmap ? `<section class="period-detail-card"><h4>每日小时活动热力</h4>${heatmap}</section>` : ''}`;
    panel.innerHTML = `<div class="period-kpi-grid">${metrics.map(([label, value]) => `<div><span>${escapeHtml(label)}</span><b>${escapeHtml(value)}</b></div>`).join('')}</div><div class="period-report-columns"><section><div class="extended-subheading">每日 Token 趋势 · ${escapeHtml(payload?.period || `${days} 天`)}</div><div class="period-bars">${bars || '<div class="empty-inline">暂无每日趋势数据。</div>'}</div></section><section><div class="extended-subheading">模型 Token 用量</div><div class="period-model-list">${modelRows || '<div class="empty-inline">暂无模型用量数据。</div>'}</div></section></div>${detailCards}${summary ? `<div class="period-summary-text"><b>周期总结</b><p>${escapeHtml(summary)}</p></div>` : ''}${topicText ? `<div class="period-summary-text"><b>高频主题</b><p>${topicText}</p></div>` : ''}<div class="period-report-footnote">${freshness ? `数据摘要最后更新：${escapeHtml(dateTime(freshness))}` : '该周期的部分数据可能尚未生成。'}</div>`;
  }

  async function loadPeriodReport(days = 7) {
    const panel = $('#periodReportContent');
    if (!panel) return;
    panel.innerHTML = '<div class="empty-inline">正在读取周期报告…</div>';
    $$('.period-report-toolbar [data-extended-action="period-load"]').forEach(button => button.classList.toggle('active', Number(button.dataset.days) === Number(days)));
    state.periodDays = Number(days);
    const requestDays = periodRequestDays(days);
    try {
      const payload = await request(`/api/weekly-report?days=${requestDays}&start=${encodeURIComponent(periodStart(days))}&include_assets=true`);
      renderPeriodReport(payload, days);
    } catch (error) {
      panel.innerHTML = `<div class="extended-error">无法读取周期报告：${escapeHtml(errorMessage(error))}</div>`;
    }
  }

  async function refreshPeriod(kind) {
    const days = Number(state.periodDays || 7);
    const requestDays = periodRequestDays(days);
    const start = periodStart(days);
    const endpoint = kind === 'summary' ? '/api/weekly-report/summary/refresh' : '/api/weekly-report/refresh';
    if (!window.confirm(`确认${kind === 'summary' ? '重新生成周期总结' : '更新周期资产'}？\n\n范围：${start} 至 ${today()}`)) return;
    const panel = $('#periodReportContent');
    if (panel) panel.innerHTML = '<div class="empty-inline">正在提交周期任务…</div>';
    try {
      const queued = await request(endpoint, { method: 'POST', body: { start, days: requestDays } });
      if (queued?.runId !== undefined) await pollRefreshJob(queued.runId, 'periodReportContent');
      await loadPeriodReport(days);
    } catch (error) {
      if (panel) panel.innerHTML = `<div class="extended-error">周期任务失败：${escapeHtml(errorMessage(error))}</div>`;
    }
  }

  function backfillPayload(dryRun) {
    const start = $('#opsBackfillStart')?.value || dateBefore(7);
    const end = $('#opsBackfillEnd')?.value || today();
    if (start > end) throw new Error('开始日期不能晚于结束日期');
    return { start, end, grain: 'selected', includeSummaries: true, skipReady: true, overwriteDaily: false, dryRun: Boolean(dryRun) };
  }

  async function backfillPreview() {
    const panel = $('#opsBackfillResult');
    if (!panel) return;
    panel.innerHTML = '<div class="empty-inline">正在生成补齐计划…</div>';
    try {
      const payload = backfillPayload(true);
      const plan = await request('/api/foundation/history-backfill', { method: 'POST', body: payload });
      state.backfill = { plan, payload };
      const pending = Array.isArray(plan?.pendingItems) ? plan.pendingItems : [];
      panel.innerHTML = `<div class="extended-summary-list"><div><span>待处理项目</span><b>${escapeHtml(formatNumber(plan.pendingItemCount ?? pending.length))}</b></div><div><span>缺少日记天数</span><b>${escapeHtml(formatNumber(plan.pendingDiaryDays))}</b></div><div><span>缺少周期报告</span><b>${escapeHtml(formatNumber(plan.pendingSummaryReports))}</b></div><div><span>预计 LLM 调用</span><b>${escapeHtml(formatNumber(plan.llmCallCount))}</b></div></div>${pending.length ? `<div class="backfill-pending-list">${pending.slice(0, 20).map(item => `<label><input type="checkbox" data-backfill-item="${escapeHtml(JSON.stringify(item))}" checked><span>${escapeHtml(item.label || item.date || item.start || '待处理项目')}</span></label>`).join('')}</div>` : '<p>当前日期范围不需要补齐。</p>'}`;
      const run = document.querySelector('[data-extended-action="backfill-run"]');
      if (run) run.disabled = pending.length === 0;
    } catch (error) {
      state.backfill = { plan: null, payload: null };
      panel.innerHTML = `<div class="extended-error">无法生成补齐计划：${escapeHtml(errorMessage(error))}</div>`;
    }
  }

  async function backfillRun(target) {
    if (!state.backfill.plan || !state.backfill.payload) {
      result('opsBackfillResult', '请先生成补齐预览。', 'error');
      return;
    }
    if (!window.confirm('确认提交历史数据补齐？\n\n系统会按预览计划在后台运行。')) return;
    target.disabled = true;
    try {
      const selected = $$('[data-backfill-item]').filter(input => input.checked).map(input => {
        try { return JSON.parse(input.dataset.backfillItem); } catch (_) { return null; }
      }).filter(Boolean);
      if (!selected.length) throw new Error('至少选择一个待处理项目');
      const payload = { ...state.backfill.payload, dryRun: false, periods: selected.map(item => item.kind === 'diary' ? { kind: 'day', start: item.date, end: item.date, label: item.label || item.date } : { kind: item.kind === 'week-summary' ? 'week' : 'month', start: item.start, end: item.end, label: item.label || item.start }) };
      const queued = await request('/api/foundation/history-backfill', { method: 'POST', body: payload });
      result('opsBackfillResult', `补齐任务已提交：#${queued.runId || '—'} · ${statusLabel(queued.status || 'queued')}`);
      await loadTasks();
    } catch (error) {
      result('opsBackfillResult', `提交失败：${errorMessage(error)}`, 'error');
    } finally {
      target.disabled = false;
    }
  }

  function reviewStatus(item) {
    if (item.assetClass === 'skill' && ['create', 'extend'].includes(item.libraryAction)) return '可注册';
    if (item.assetClass === 'lesson') return '待结晶';
    if (item.assetClass === 'reference') return '仅供参考';
    if (item.assetClass === 'discard') return '不纳入资产';
    return '待审核';
  }

  function renderSkillReview(payload) {
    const panel = $('#skillPassReviewPanel');
    if (!panel) return;
    const items = Array.isArray(payload?.items) ? payload.items : [];
    if (!payload || !items.length) {
      panel.innerHTML = '<div class="empty-inline">当前没有 Skill Pass 提案。</div>';
      return;
    }
    const counts = payload.counts || {};
    const cards = items.map(item => {
      const type = String(item.assetClass || 'unknown');
      const selectable = ['skill', 'lesson'].includes(type);
      const checked = selectable && state.reviewSelected.has(item.reviewId) ? ' checked' : '';
      const scores = item.scores || {};
      const scoreText = ['evidence', 'value', 'reuse', 'program'].map(key => `${scoreLabels[key]} ${scores[key] ?? '—'}/5`).join(' · ');
      return `<article class="skill-review-item ${escapeHtml(type)}"><header>${selectable ? `<label><input type="checkbox" data-skill-review="${escapeHtml(item.reviewId || '')}"${checked}><span>选择</span></label>` : '<span class="skill-review-bullet">•</span>'}<div><span class="type-tag">${escapeHtml(assetLabels[type] || type)}</span><b>${escapeHtml(reviewStatus(item))}</b></div><small>${escapeHtml(item.reviewId || '')}</small></header><h4>${escapeHtml(item.title || item.skillName || '未命名提案')}</h4><p>${escapeHtml(item.summary || '暂无摘要')}</p><details><summary>查看审核信息</summary><dl><div><dt>原始判断</dt><dd>${escapeHtml(assetLabels[item.originalDecision] || item.originalDecision || '—')}</dd></div><div><dt>处理原因</dt><dd>${escapeHtml(item.reason || item.completionReason || '—')}</dd></div><div><dt>完成状态</dt><dd>${escapeHtml(statusLabel(item.completion || 'unknown'))}</dd></div><div><dt>质量评分</dt><dd>${escapeHtml(scoreText)}</dd></div><div><dt>Skill 库动作</dt><dd>${escapeHtml(libraryActionLabels[item.libraryAction] || item.libraryAction || '—')}</dd></div>${item.existingSkillName ? `<div><dt>已有 Skill</dt><dd>${escapeHtml(item.existingSkillName)}</dd></div>` : ''}</dl></details></article>`;
    }).join('');
    panel.innerHTML = `<div class="skill-review-counts"><span>经验 ${escapeHtml(counts.lesson ?? 0)}</span><span>Skill ${escapeHtml(counts.skill ?? 0)}</span><span>参考 ${escapeHtml(counts.reference ?? 0)}</span><span>已舍弃 ${escapeHtml(counts.discard ?? 0)}</span><span>日期 ${escapeHtml(payload.businessDate || '—')}</span><span>规则 ${escapeHtml(payload.promptVersion || '—')}</span></div><div class="skill-review-list">${cards}</div><div class="skill-review-actions"><span id="skillReviewStatus" role="status"></span><button class="button small primary" type="button" data-extended-action="skill-crystallize-register">结晶并注册所选提案</button></div>`;
  }

  function renderAssetRuntimeDetails(payload) {
    const panel = $('#assetRuntimeDetails');
    if (!panel) return;
    const kpi = payload?.kpi || {};
    const tools = Array.isArray(payload?.tools) ? payload.tools : [];
    const models = Array.isArray(payload?.models) ? payload.models : [];
    const skills = Array.isArray(payload?.skills) ? payload.skills : [];
    const storage = payload?.storage && typeof payload.storage === 'object' ? payload.storage : {};
    const rag = payload?.rag && typeof payload.rag === 'object' ? payload.rag : {};
    const infrastructure = payload?.infrastructure && typeof payload.infrastructure === 'object' ? payload.infrastructure : {};
    const toolRows = tools.slice(0, 8).map(item => `<div class="extended-list-row"><b>${escapeHtml(item.name || item.tool || 'Agent')}</b><span>${escapeHtml(formatTokens(item.tokens ?? item.todayTokens))} Token</span><small>${escapeHtml(formatNumber(item.messages ?? item.todayMessages))} 条消息</small></div>`).join('');
    const modelRows = models.slice(0, 8).map(item => `<div class="extended-list-row"><b>${escapeHtml(item.name || item.model || '模型')}</b><span>${escapeHtml(formatTokens(item.tokens))} Token</span><small>${escapeHtml(formatNumber(item.messages))} 条消息</small></div>`).join('');
    const metricRows = [
      ['累计 Token', formatTokens(kpi.totalTokens ?? payload.totalTokens)],
      ['累计消息', formatNumber(kpi.totalMessages ?? payload.totalMessages)],
      ['活跃工具', formatNumber(kpi.activeTools ?? tools.filter(item => item.isActive).length)],
      ['Skill 实例', formatNumber(payload.skillInstances ?? payload.installedSkillInstanceCount)],
      ['存储占用', storage.totalSizeMB === undefined ? '—' : `${storage.totalSizeMB} MB`],
      ['RAG 索引', rag.entries === undefined ? '—' : `${formatNumber(rag.entries)} 条`],
      ['基础设施', infrastructure.total === undefined ? '—' : `${formatNumber(infrastructure.total)} 条记录`],
      ['Skill 目录', skills.length ? `${formatNumber(skills.length)} 项` : '—']
    ];
    panel.innerHTML = `<div class="asset-runtime-metrics">${metricRows.map(([label, value]) => `<div><span>${escapeHtml(label)}</span><b>${escapeHtml(value)}</b></div>`).join('')}</div><div class="asset-runtime-columns"><section><div class="extended-subheading">Agent / 工作区 Token 用量</div>${toolRows || '<div class="empty-inline">暂无工具用量数据。</div>'}</section><section><div class="extended-subheading">模型 Token 用量</div>${modelRows || '<div class="empty-inline">暂无模型用量数据。</div>'}</section></div>`;
  }

  async function loadAssetRuntimeDetails() {
    const panel = $('#assetRuntimeDetails');
    if (!panel) return;
    try {
      const payload = await request('/api/ai-assets');
      renderAssetRuntimeDetails(payload);
    } catch (error) {
      panel.innerHTML = `<div class="extended-error">无法读取运行时资产明细：${escapeHtml(errorMessage(error))}</div>`;
    }
  }

  async function refreshAssetRuntime() {
    const panel = $('#assetRuntimeDetails');
    if (panel) panel.innerHTML = '<div class="empty-inline">正在提交 AI 资产后台更新…</div>';
    try {
      const payload = await request('/api/ai-assets/refresh', { method: 'POST', body: {} });
      if (panel) panel.innerHTML = `<div class="extended-success">AI 资产更新任务已提交：${escapeHtml(payload.runId || payload.jobId || '—')}。</div>`;
      await loadTasks();
    } catch (error) {
      if (panel) panel.innerHTML = `<div class="extended-error">AI 资产更新失败：${escapeHtml(errorMessage(error))}</div>`;
    }
  }

  async function loadSkillReview() {
    const panel = $('#skillPassReviewPanel');
    if (!panel) return;
    panel.innerHTML = '<div class="empty-inline">正在读取 Skill Pass 提案…</div>';
    try {
      const payload = await request('/api/ai-assets/skill-assets');
      const items = Array.isArray(payload?.items) ? payload.items : [];
      state.review = { ...payload, items };
      if (!state.reviewSelected.size) items.filter(item => item.selectedByDefault === true && item.assetClass === 'skill').forEach(item => state.reviewSelected.add(item.reviewId));
      renderSkillReview(state.review);
    } catch (error) {
      panel.innerHTML = `<div class="extended-error">无法读取 Skill Pass 提案：${escapeHtml(errorMessage(error))}</div>`;
    }
  }

  async function crystallizeAndRegister() {
    if (!state.review) return;
    const selected = Array.from(state.reviewSelected).filter(Boolean);
    const items = state.review.items.filter(item => selected.includes(item.reviewId));
    const lessons = items.filter(item => item.assetClass === 'lesson');
    const skillItems = items.filter(item => item.assetClass === 'skill');
    const status = $('#skillReviewStatus');
    if (!items.length) { if (status) status.textContent = '请先选择经验或 Skill 提案。'; return; }
    try {
      if (lessons.length) {
        if (!window.confirm(`确认将 ${lessons.length} 条经验重新结晶为 Skill？\n\n这会调用 Skill Pass 的人工结晶接口。`)) return;
        if (status) status.textContent = '正在结晶经验…';
        const review = await request('/api/ai-assets/skill-assets/crystallize', { method: 'POST', body: { businessDate: state.review.businessDate, promptVersion: state.review.promptVersion, reviewIds: lessons.map(item => item.reviewId) } });
        state.review = { ...review.review, items: Array.isArray(review.review?.items) ? review.review.items : [] };
        renderSkillReview(state.review);
      }
      const finalized = state.review.items.filter(item => item.assetClass === 'skill' && selected.includes(item.reviewId));
      if (!finalized.length) { if ($('#skillReviewStatus')) $('#skillReviewStatus').textContent = '结晶完成，但没有可注册的 Skill。'; return; }
      if (!window.confirm(`确认注册 ${finalized.length} 个 Skill？\n\n系统会写入 Actanara 主库，并同步到支持写入的已检测 Agent。`)) return;
      if ($('#skillReviewStatus')) $('#skillReviewStatus').textContent = '正在注册 Skill…';
      const registration = await request('/api/ai-assets/skill-assets/register', { method: 'POST', body: { businessDate: state.review.businessDate, promptVersion: state.review.promptVersion, reviewIds: finalized.map(item => item.reviewId) } });
      const count = Array.isArray(registration?.registeredTools) ? registration.registeredTools.length : 0;
      if ($('#skillReviewStatus')) $('#skillReviewStatus').textContent = `注册完成：${count} 个 Agent 数据源收到更新。`;
      state.reviewSelected = new Set();
      await loadSkillReview();
    } catch (error) {
      if ($('#skillReviewStatus')) $('#skillReviewStatus').textContent = `操作失败：${errorMessage(error)}`;
    }
  }

  async function loadRag() {
    try {
      const [status, settings, memory] = await Promise.all([
        request('/api/rag/status?probe=false'),
        request('/api/rag/settings'),
        request('/api/memory/status?probe=false')
      ]);
      state.rag = { status, settings, memory };
      const local = memory?.backends?.local || {};
      const ragStatus = $('#extendedRagStatus');
      if (ragStatus) ragStatus.innerHTML = `<span class="status-seal">${local.available ? '正常' : '检查'}</span><div><b>${local.available ? '本地搜索可用' : '本地搜索不可用'}</b><span>Local FTS：${escapeHtml(formatNumber(local.documentCount))} 篇文档</span></div>`;
      if ($('#extendedLocalStatus')) { $('#extendedLocalStatus').textContent = local.available ? (local.backend?.stale ? '需要更新' : '正常') : '读取失败'; $('#extendedLocalStatus').className = `status-pill ${local.available && !local.backend?.stale ? 'good' : 'warning'}`; }
      if ($('#extendedLocalDetails')) $('#extendedLocalDetails').innerHTML = `<div class="extended-summary-list"><div><span>索引是否可用</span><b>${local.available ? '可用' : '不可用'}</b></div><div><span>已索引文档</span><b>${escapeHtml(formatNumber(local.documentCount))} 篇</b></div><div><span>数据来源</span><b>${escapeHtml(formatNumber(local.sourceCount))} 个</b></div><div><span>最后更新时间</span><b>${escapeHtml(dateTime(local.indexedAt))}</b></div></div>`;
      const productEnabled = status?.productEnabled === true;
      const searchAvailable = status?.searchAvailable === true;
      if ($('#extendedRagPill')) { $('#extendedRagPill').textContent = searchAvailable ? '正常' : productEnabled ? '未就绪' : '未启用'; $('#extendedRagPill').className = `status-pill ${searchAvailable ? 'good' : 'warning'}`; }
      const server = status?.server || {};
      const profile = status?.profile?.active || status?.profile?.configured || {};
      if ($('#extendedRagDetails')) $('#extendedRagDetails').innerHTML = `<div class="extended-summary-list"><div><span>功能开关</span><b>${productEnabled ? '已启用' : '未启用'}</b></div><div><span>语义搜索</span><b>${searchAvailable ? '可用' : '不可用'}</b></div><div><span>服务进程</span><b>${server.running === true || status?.serving === true ? '运行中' : '未运行'}</b></div><div><span>Embedding 模型</span><b>${escapeHtml(profile.model || '未配置')}</b></div></div>`;
      const topK = settings?.rag?.retrieval?.topK || settings?.retrieval?.topK || 8;
      const halfLife = settings?.rag?.retrieval?.recencyHalfLifeDays || settings?.retrieval?.recencyHalfLifeDays || 7;
      if ($('#extendedRagSettingTopK')) $('#extendedRagSettingTopK').value = topK;
      if ($('#extendedRagTopK')) $('#extendedRagTopK').value = topK;
      if ($('#extendedRagHalfLife')) $('#extendedRagHalfLife').value = halfLife;
      if ($('#extendedRagStatus')) $('#extendedRagStatus').dataset.loaded = 'true';
    } catch (error) {
      if ($('#extendedRagDetails')) $('#extendedRagDetails').innerHTML = `<div class="extended-error">无法读取 RAG 状态：${escapeHtml(errorMessage(error))}</div>`;
    }
  }

  async function ragOperator(action) {
    const endpoint = { 'rag-start': '/api/rag/server/start', 'rag-stop': '/api/rag/server/stop', 'rag-sync': '/api/rag/sync/run', 'rag-index': '/api/rag/index/run', 'local-sync': '/api/memory/local/sync', 'local-rebuild': '/api/memory/local/rebuild' }[action];
    if (!endpoint) return;
    if (!window.confirm(`确认执行“${action === 'local-sync' ? '增量同步本地索引' : action === 'local-rebuild' ? '重建本地索引' : action === 'rag-start' ? '启动 nova-RAG' : action === 'rag-stop' ? '停止 nova-RAG' : '更新 RAG 索引'}”？`)) return;
    const target = document.querySelector(`[data-extended-action="${action}"]`);
    if (target) target.disabled = true;
    try {
      const body = action === 'rag-start' ? { confirmationText: 'START ACTANARA RAG SERVER' } : action === 'rag-stop' ? { confirmationText: 'STOP ACTANARA RAG SERVER' } : undefined;
      const payload = await request(endpoint, { method: 'POST', ...(body ? { body } : {}) });
      result('extendedRagSettingsResult', `操作已提交：${statusLabel(payload?.status || payload?.action || 'queued')}`);
      await loadRag();
    } catch (error) {
      result('extendedRagSettingsResult', `操作失败：${errorMessage(error)}`, 'error');
    } finally {
      if (target) target.disabled = false;
    }
  }

  async function saveRagSettings() {
    const current = state.rag?.settings?.rag || state.rag?.settings || {};
    const retrieval = { ...(current.retrieval || {}), topK: Number($('#extendedRagSettingTopK')?.value || 8), recencyHalfLifeDays: Number($('#extendedRagHalfLife')?.value || 7) };
    try {
      await request('/api/rag/settings', { method: 'PUT', body: { rag: { ...current, retrieval } } });
      result('extendedRagSettingsResult', '检索参数已保存。');
      await loadRag();
    } catch (error) {
      result('extendedRagSettingsResult', `参数保存失败：${errorMessage(error)}`, 'error');
    }
  }

  async function ragSearch(event) {
    event.preventDefault();
    const query = $('#extendedRagQuery')?.value.trim();
    if (!query) { result('extendedRagResults', '请输入搜索关键词。', 'error'); return; }
    result('extendedRagResults', '正在执行语义搜索…');
    const sourceSets = String($('#extendedRagSourceSets')?.value || '').split(',').map(value => value.trim()).filter(Boolean);
    const lifecycle = String($('#extendedRagLifecycle')?.value || '').split(',').map(value => value.trim()).filter(Boolean);
    try {
      const payload = { query, topK: Number($('#extendedRagTopK')?.value || 8), mode: 'rag', caller: 'dashboard', project: $('#extendedRagProject')?.value.trim() || undefined, sourceSets, lifecycle };
      const data = await request('/api/memory/search', { method: 'POST', body: payload });
      const rows = Array.isArray(data?.results) ? data.results : [];
      $('#extendedRagResults').innerHTML = `<div class="extended-result-meta">搜索方式：${escapeHtml(data?.backend?.kind || 'RAG')} · ${rows.length} 条结果</div>${rows.length ? rows.map(item => `<article class="rag-result-item"><div><b>${escapeHtml(item.title || item.sourceSet || '记忆')}</b><span>${escapeHtml(item.date || '日期未知')}</span></div><p>${escapeHtml(item.textPreview || item.excerpt || item.text || '没有摘要')}</p></article>`).join('') : '<div class="empty-inline">没有找到匹配的记忆。</div>'}`;
    } catch (error) {
      result('extendedRagResults', `语义搜索失败：${errorMessage(error)}`, 'error');
    }
  }

  async function loadRagCoverage() {
    const panel = $('#extendedRagCoverage');
    if (!panel) return;
    panel.innerHTML = '<div class="empty-inline">正在检查索引覆盖…</div>';
    try {
      const payload = await request('/api/rag/coverage');
      const summary = payload?.summary || {};
      panel.innerHTML = summaryMarkup({ summary: { '已配置来源数': summary.configuredSourceSetCount, '已索引来源数': summary.indexedSourceSetCount, '已索引分块数': summary.indexedChunkCount, '缺少来源': Array.isArray(summary.missingConfiguredSourceSets) ? summary.missingConfiguredSourceSets.join(', ') || '无' : '—' } });
    } catch (error) {
      panel.innerHTML = `<div class="extended-error">无法读取覆盖情况：${escapeHtml(errorMessage(error))}</div>`;
    }
  }

  async function loadRagEval() {
    const panel = $('#extendedRagEval');
    if (!panel) return;
    panel.innerHTML = '<div class="empty-inline">正在读取最近评估…</div>';
    try {
      const payload = await request('/api/rag/eval/latest');
      panel.innerHTML = summaryMarkup(payload);
    } catch (error) {
      panel.innerHTML = `<div class="extended-error">无法读取评估：${escapeHtml(errorMessage(error))}</div>`;
    }
  }

  async function planRagExternalSources() {
    const panel = $('#extendedRagSettingsResult');
    const paths = String($('#extendedRagExternalPaths')?.value || '').split(/\r?\n/).map(value => value.trim()).filter(Boolean);
    if (!paths.length) { result('extendedRagSettingsResult', '请先填写至少一个自定义数据路径。', 'error'); return; }
    if (panel) panel.innerHTML = '<div class="empty-inline">正在分析自定义路径…</div>';
    try {
      const payload = await request('/api/rag/external-sources/plan', { method: 'POST', body: { rag: { indexing: { externalSources: { enabled: true, paths, recursive: true, symlinkPolicy: 'reject' } } } } });
      const summary = payload?.summary || {};
      if (panel) panel.innerHTML = `<div class="extended-summary-list"><div><span>发现的数据源</span><b>${escapeHtml(formatNumber(summary.sourceRecordCount))}</b></div><div><span>解析分块</span><b>${escapeHtml(formatNumber(summary.chunkCount))}</b></div><div><span>解析错误</span><b>${escapeHtml(formatNumber(summary.parseErrorCount))}</b></div></div><p class="chart-note">这是预览计划，不会写入设置或索引。</p>`;
    } catch (error) {
      result('extendedRagSettingsResult', `路径分析失败：${errorMessage(error)}`, 'error');
    }
  }

  async function planRagMigration() {
    const panel = $('#extendedRagEval');
    if (!panel) return;
    panel.innerHTML = '<div class="empty-inline">正在生成基座迁移计划…</div>';
    try {
      const payload = await request('/api/rag/profile/migrate/plan', { method: 'POST', body: { targetProfile: { mode: 'local', providerId: 'local', model: 'intfloat/multilingual-e5-small', dimension: 384, languageProfile: 'zh' }, initMode: false, autoPromote: false } });
      panel.innerHTML = `<div class="extended-summary-list"><div><span>当前配置</span><b>${escapeHtml(JSON.stringify(payload.currentProfile || {}))}</b></div><div><span>目标配置</span><b>${escapeHtml(JSON.stringify(payload.targetProfile || {}))}</b></div><div><span>执行步骤</span><b>${escapeHtml(formatNumber((payload.steps || []).length))}</b></div><div><span>风险</span><b>${escapeHtml(payload.risk?.level || payload.risk || '请查看计划')}</b></div></div><p class="chart-note">已生成只读计划；执行迁移仍需在经典版设置中确认。</p>`;
    } catch (error) {
      panel.innerHTML = `<div class="extended-error">无法生成迁移计划：${escapeHtml(errorMessage(error))}</div>`;
    }
  }

  async function loadMemorySkillPlan() {
    const panel = $('#extendedMemorySkillPlan');
    if (!panel) return;
    panel.innerHTML = '<div class="empty-inline">正在读取可注册工具…</div>';
    try {
      const payload = await request('/api/settings/external-tools/memory-skill-registration/plan');
      const candidates = Array.isArray(payload?.candidates) ? payload.candidates : Array.isArray(payload?.tools) ? payload.tools : [];
      panel.innerHTML = candidates.length ? `<div class="memory-skill-targets">${candidates.map(tool => `<label><input type="checkbox" data-memory-skill-tool="${escapeHtml(tool.id || tool.tool || tool.name || '')}" checked><span>${escapeHtml(tool.name || tool.tool || tool.id || '工具')}</span><small>${escapeHtml(tool.status || '可注册')}</small></label>`).join('')}</div><div class="extended-form-row"><label>确认文本<input id="extendedMemorySkillConfirmation" type="text" placeholder="输入接口返回的确认文本"></label></div><button class="button small primary" type="button" data-extended-action="memory-skill-register">注册记忆 Skill</button>` : '<div class="empty-inline">当前没有可注册的 Agent 工具。</div>';
      state.memorySkillPlan = payload;
    } catch (error) {
      panel.innerHTML = `<div class="extended-error">无法读取注册计划：${escapeHtml(errorMessage(error))}</div>`;
    }
  }

  async function registerMemorySkill() {
    const tools = $$('[data-memory-skill-tool]').filter(input => input.checked).map(input => input.dataset.memorySkillTool).filter(Boolean);
    if (!tools.length) { result('extendedMemorySkillPlan', '请至少选择一个 Agent 工具。', 'error'); return; }
    const confirmation = $('#extendedMemorySkillConfirmation')?.value || '';
    if (!confirmation) { result('extendedMemorySkillPlan', '请填写接口返回的确认文本。', 'error'); return; }
    try {
      const payload = await request('/api/settings/external-tools/memory-skill-registration', { method: 'POST', body: { dryRun: false, overwrite: false, confirmationText: confirmation, tools } });
      $('#extendedMemorySkillPlan').innerHTML = `<div class="extended-success">已提交注册：${escapeHtml(formatNumber((payload.results || []).length))} 个工具。</div>${(payload.results || []).map(item => `<div class="extended-list-row"><b>${escapeHtml(item.tool || '工具')}</b><span>${escapeHtml(item.result || item.status || '已完成')}</span></div>`).join('')}`;
    } catch (error) {
      result('extendedMemorySkillPlan', `注册失败：${errorMessage(error)}`, 'error');
    }
  }

  function redactSettings(settings) {
    const source = settings && typeof settings === 'object' ? settings : {};
    const backup = source.backup || {};
    const rag = source.rag || {};
    return {
      dashboard: { host: source.dashboard?.host || '—', port: source.dashboard?.port || '—' },
      schedule: { enabled: source.schedule?.enabled ?? '—', frequency: source.schedule?.frequency || '—' },
      backup: { targetDirectory: backup.targetDirectory ? '已配置（路径已隐藏）' : '未配置', maxBackups: backup.retention?.maxBackups || '—' },
      rag: { enabled: rag.enabled ?? '—', mode: rag.mode || '—' },
      memorySearch: { nativeMemory: source.memorySearch ? '已配置' : '—' }
    };
  }

  async function loadSettings() {
    try {
      const [settings, chain, tools, scheduler] = await Promise.all([
        request('/api/settings'), request('/api/llm-provider-chain'), request('/api/settings/external-tools/rediscover', { method: 'POST' }), request('/api/settings/scheduler')
      ]);
      state.settings = { settings, tools, scheduler };
      state.llmChain = chain;
      const status = $('#extendedSettingsStatus');
      if (status) status.innerHTML = `<span class="status-seal">正常</span><div><b>当前设置已读取</b><span>修改前会显示确认范围</span></div>`;
      const summary = $('#extendedSettingsSummary');
      if (summary) summary.textContent = JSON.stringify(redactSettings(settings), null, 2);
      renderLlmChain(chain);
      renderToolConfigs(tools);
      renderPathServices(settings, scheduler);
    } catch (error) {
      if ($('#extendedSettingsStatus')) $('#extendedSettingsStatus').innerHTML = `<span class="status-seal">失败</span><div><b>无法读取设置</b><span>${escapeHtml(errorMessage(error))}</span></div>`;
    }
  }

  function providerPayload(entry) {
    return { entryId: entry.entryId, mode: entry.mode, provider: entry.provider, model: entry.model, endpoint: entry.endpoint, api: entry.api, apiKey: '', timeoutSeconds: entry.timeoutSeconds };
  }

  function renderLlmChain(chain) {
    const panel = $('#extendedLlmChain');
    if (!panel) return;
    const providers = Array.isArray(chain?.providers) ? chain.providers : [];
    if (!providers.length) { panel.innerHTML = '<div class="empty-inline">当前没有模型服务配置。</div>'; return; }
    panel.innerHTML = providers.map((entry, index) => `<div class="extended-provider-row"><span class="provider-order">${String(index + 1).padStart(2, '0')}</span><div><b>${escapeHtml(entry.provider || entry.presetProvider || '自定义服务')}</b><small>${escapeHtml(entry.model || '模型未设置')} · ${escapeHtml(entry.readiness?.ready ? '可用' : statusLabel(entry.readiness?.status || 'unknown'))}</small></div><button class="button small secondary" type="button" data-extended-action="llm-move" data-llm-index="${index}" data-llm-offset="-1" ${index === 0 ? 'disabled' : ''}>↑</button><button class="button small secondary" type="button" data-extended-action="llm-move" data-llm-index="${index}" data-llm-offset="1" ${index === providers.length - 1 ? 'disabled' : ''}>↓</button><button class="button small secondary" type="button" data-extended-action="llm-test" data-llm-index="${index}">测试</button></div>`).join('');
  }

  async function moveLlm(index, offset) {
    const providers = Array.isArray(state.llmChain?.providers) ? state.llmChain.providers.slice() : [];
    const target = index + offset;
    if (target < 0 || target >= providers.length) return;
    [providers[index], providers[target]] = [providers[target], providers[index]];
    try {
      const saved = await request('/api/llm-provider-chain', { method: 'PUT', body: { providers: providers.map(providerPayload) } });
      state.llmChain = saved;
      renderLlmChain(saved);
      result('extendedLlmResult', '模型服务优先级已保存。');
    } catch (error) {
      result('extendedLlmResult', `优先级保存失败：${errorMessage(error)}`, 'error');
    }
  }

  async function testLlm(index) {
    const provider = state.llmChain?.providers?.[index];
    if (!provider) return;
    result('extendedLlmResult', `正在测试 ${provider.provider || '模型服务'}…`);
    try {
      const payload = await request('/api/llm-provider-chain/test', { method: 'POST', body: providerPayload(provider) });
      result('extendedLlmResult', payload?.ok ? `${provider.provider || '模型服务'} 测试通过。` : `${provider.provider || '模型服务'} 测试未通过：${payload?.error || statusLabel(payload?.status)}`, payload?.ok ? '' : 'error');
    } catch (error) {
      result('extendedLlmResult', `模型服务测试失败：${errorMessage(error)}`, 'error');
    }
  }

  function renderToolConfigs(payload) {
    const panel = $('#extendedToolConfigs');
    if (!panel) return;
    const items = Array.isArray(payload?.toolConfigs) ? payload.toolConfigs
      : Array.isArray(payload?.discoveries) ? payload.discoveries.map(item => ({ ...item, id: item.tool, detected: true, instanceCount: 1 }))
      : Array.isArray(payload?.tools) ? payload.tools
      : Array.isArray(payload?.items) ? payload.items : [];
    const detected = items.filter(item => item.detected !== false && !['absent', 'not-detected', 'not_detected', 'unavailable'].includes(String(item.status || '').toLowerCase()));
    if (!detected.length) {
      panel.innerHTML = '<div class="empty-inline">当前没有检测到本机 Agent 工具。</div>';
      return;
    }
    panel.innerHTML = detected.slice(0, 12).map(item => `<div class="extended-list-row"><b>${escapeHtml(item.name || item.tool || item.id || '工具')}</b><span>已在本机检测到</span><small>${escapeHtml(item.instanceCount === undefined ? '' : `${item.instanceCount} 个实例`)}</small></div>`).join('');
  }

  function renderPathServices(settings, scheduler) {
    const panel = $('#extendedPathServices');
    if (!panel) return;
    const paths = settings?.paths || settings?.runtime || {};
    const schedulerStatus = scheduler?.running === true ? '运行中' : scheduler?.effectiveEnabled === true || scheduler?.enabled === true ? '已启用' : scheduler?.running === false ? '未运行' : '未知';
    const attribution = settings?.workspaceAttribution || settings?.workspace_attribution;
    panel.innerHTML = `<div class="extended-summary-list"><div><span>Runtime 目录</span><b>${paths.runtime || paths.actanaraHome ? '已配置（路径已隐藏）' : '未读取'}</b></div><div><span>调度状态</span><b>${schedulerStatus}</b></div><div><span>Workspace 归因</span><b>${attribution ? '已配置' : '未配置'}</b></div></div>`;
  }

  async function loadSchedulerStatus() {
    const panel = $('#extendedScheduler');
    if (!panel) return;
    panel.innerHTML = '<div class="empty-inline">正在读取调度状态…</div>';
    try {
      const payload = await request('/api/settings/scheduler');
      const timer = payload?.systemTimer || {};
      panel.innerHTML = `<div class="extended-summary-list"><div><span>调度开关</span><b>${payload?.effectiveEnabled === true || payload?.enabled === true ? '已启用' : '未启用'}</b></div><div><span>当前运行</span><b>${payload?.running === true ? '运行中' : '未运行'}</b></div><div><span>调度方式</span><b>${escapeHtml(payload?.mode || '—')}</b></div><div><span>时区</span><b>${escapeHtml(payload?.timezone || '—')}</b></div><div><span>系统定时器</span><b>${timer.actualRegistered === true ? '已登记' : timer.actualRegistered === false ? '未登记' : '未知'}</b></div></div>`;
    } catch (error) {
      panel.innerHTML = `<div class="extended-error">无法读取调度状态：${escapeHtml(errorMessage(error))}</div>`;
    }
  }

  async function loadServicePreview(kind) {
    const panel = $('#extendedScheduler');
    if (!panel) return;
    panel.innerHTML = '<div class="empty-inline">正在读取服务状态…</div>';
    try {
      const payload = await request(`/api/settings/services/${encodeURIComponent(kind)}/preview`);
      panel.innerHTML = `<div class="extended-summary-list"><div><span>服务</span><b>${escapeHtml(kind === 'dashboard' ? 'Dashboard' : 'RAG')}</b></div><div><span>是否已登记</span><b>${payload?.registered === true ? '是' : payload?.registered === false ? '否' : '未知'}</b></div><div><span>运行状态</span><b>${escapeHtml(statusLabel(payload?.status || payload?.runtimeProbe?.status || 'unknown'))}</b></div><div><span>服务管理器</span><b>${escapeHtml(payload?.serviceManager || payload?.provider || '—')}</b></div></div>`;
    } catch (error) {
      panel.innerHTML = `<div class="extended-error">无法读取服务状态：${escapeHtml(errorMessage(error))}</div>`;
    }
  }

  async function rediscoverTools() {
    const panel = $('#extendedToolConfigs');
    if (panel) panel.innerHTML = '<div class="empty-inline">正在重新检测 Agent 工具…</div>';
    try {
      await request('/api/settings/external-tools/rediscover', { method: 'POST' });
      const payload = await request('/api/ai-assets/tool-configs/discover', { method: 'POST' });
      renderToolConfigs(payload);
      result('extendedToolConfigs', '工具检测已完成。');
    } catch (error) {
      result('extendedToolConfigs', `工具检测失败：${errorMessage(error)}`, 'error');
    }
  }

  async function diaryConsistency() {
    try {
      const payload = await request('/api/settings/diary-path/consistency');
      result('extendedPathServices', `日记路径检查完成：${statusLabel(payload?.status || 'ready')}。${payload?.message || ''}`);
    } catch (error) {
      result('extendedPathServices', `日记路径检查失败：${errorMessage(error)}`, 'error');
    }
  }

  async function sqliteRebuild() {
    const confirmationText = 'REBUILD ACTANARA SQLITE CACHE';
    if (!window.confirm(`确认重建 SQLite 缓存？\n\n下一步会要求输入：${confirmationText}`)) return;
    const typed = window.prompt(`请输入确认文本：${confirmationText}`) || '';
    if (typed !== confirmationText) { result('extendedPathServices', '确认文本不匹配，已取消。', 'error'); return; }
    try {
      const payload = await request('/api/settings/sqlite-cache/rebuild', { method: 'POST', body: { dryRun: false, confirmationText: typed } });
      result('extendedPathServices', `SQLite 缓存重建已提交：${statusLabel(payload?.status || 'completed')}。`);
    } catch (error) {
      result('extendedPathServices', `SQLite 缓存重建失败：${errorMessage(error)}`, 'error');
    }
  }

  async function backupRun() {
    const directory = $('#archiveBackupDirectory')?.value.trim();
    if (!directory) { result('archiveBackupStatus', '请先填写备份目标目录。', 'error'); return; }
    const include = {};
    $$('[data-backup-scope]').forEach(input => { include[input.dataset.backupScope] = input.checked; });
    const settingsPayload = { backup: { targetDirectory: directory, include, retention: { maxBackups: Number($('#archiveBackupRetentionCount')?.value || 7), maxAgeDays: Number($('#archiveBackupRetentionDays')?.value || 30) } } };
    if (!window.confirm('确认保存备份设置并立即创建备份？\n\n目标目录将写入本机配置。')) return;
    const button = document.querySelector('[data-extended-action="backup-run"]');
    if (button) button.disabled = true;
    try {
      await request('/api/ai-assets/backups/settings', { method: 'PUT', body: settingsPayload });
      const queued = await request('/api/ai-assets/backups/run', { method: 'POST', body: { confirmationText: 'BACK UP ACTANARA DATA' } });
      result('archiveBackupStatus', `备份任务已提交：${queued.jobId || '—'} · ${statusLabel(queued.status || 'queued')}。`);
    } catch (error) {
      result('archiveBackupStatus', `备份失败：${errorMessage(error)}`, 'error');
    } finally {
      if (button) button.disabled = false;
    }
  }

  async function loadBackupStatus() {
    const panel = $('#archiveBackupLatest');
    if (!panel) return;
    panel.innerHTML = '<div class="empty-inline">正在读取备份状态…</div>';
    try {
      const payload = await request('/api/ai-assets/backups/status');
      const latest = payload?.latestRun;
      const readiness = payload?.targetReadiness || {};
      const settings = payload?.settings || {};
      const include = settings.include && typeof settings.include === 'object' ? settings.include : {};
      $$('[data-backup-scope]').forEach(input => {
        if (Object.prototype.hasOwnProperty.call(include, input.dataset.backupScope)) input.checked = include[input.dataset.backupScope] === true;
      });
      const directory = $('#archiveBackupDirectory');
      const count = $('#archiveBackupRetentionCount');
      const days = $('#archiveBackupRetentionDays');
      if (directory && settings.targetDirectory) directory.value = settings.targetDirectory;
      if (count && settings.retention?.maxBackups) count.value = settings.retention.maxBackups;
      if (days && settings.retention?.maxAgeDays) days.value = settings.retention.maxAgeDays;
      const verify = latest?.backupId ? `<button class="button small" type="button" data-extended-action="backup-verify" data-backup-id="${escapeHtml(latest.backupId)}">校验这份备份</button>` : '';
      panel.innerHTML = `<div class="extended-summary-list"><div><span>备份目标</span><b>${readiness.configured ? (readiness.ready ? '已配置且可用' : '已配置但不可用') : '尚未配置'}</b></div><div><span>最近一次备份</span><b>${latest ? escapeHtml(statusLabel(latest.status)) : '暂无记录'}</b></div><div><span>文件数量</span><b>${latest?.fileCount === undefined ? '—' : escapeHtml(formatNumber(latest.fileCount))}</b></div><div><span>完成时间</span><b>${escapeHtml(dateTime(latest?.completedAt || latest?.startedAt))}</b></div></div><p class="chart-note">当前服务没有提供覆盖恢复接口；此处只显示备份状态并支持完整性校验，恢复请按备份清单手动执行。</p>${readiness.error ? `<div class="extended-error">目标目录检查：${escapeHtml(readiness.error)}</div>` : ''}<div class="extended-button-row">${verify}</div><div id="archiveBackupVerifyResult" class="extended-result"></div>`;
    } catch (error) {
      panel.innerHTML = `<div class="extended-error">无法读取备份状态：${escapeHtml(errorMessage(error))}</div>`;
    }
  }

  async function verifyBackup(id) {
    if (!id) return;
    const panel = $('#archiveBackupVerifyResult');
    if (panel) panel.textContent = '正在校验备份…';
    try {
      const payload = await request(`/api/ai-assets/backups/${encodeURIComponent(id)}/verify`, { method: 'POST' });
      if (panel) panel.textContent = payload?.valid ? '备份校验通过。' : `备份校验未通过：${(payload?.errors || []).map(item => item.message || item).join('；') || '未提供原因'}`;
    } catch (error) {
      if (panel) panel.textContent = `备份校验失败：${errorMessage(error)}`;
    }
  }

  async function loadBackupSchedule() {
    try {
      const payload = await request('/api/ai-assets/backups/status');
      const settings = payload?.settings || {};
      const schedule = settings.schedule || {};
      const directory = $('#archiveBackupScheduleDirectory');
      const frequency = $('#archiveBackupScheduleFrequency');
      const time = $('#archiveBackupScheduleTime');
      const count = $('#archiveBackupScheduleCount');
      const days = $('#archiveBackupScheduleDays');
      if (directory) directory.value = settings.targetDirectory || '';
      if (frequency) frequency.value = schedule.frequency || 'weekly';
      if (time) time.value = schedule.timeOfDay || '05:00';
      if (count) count.value = settings.retention?.maxBackups || 7;
      if (days) days.value = settings.retention?.maxAgeDays || 30;
      const toggle = $('#archiveBackupScheduleEnabled');
      if (toggle) toggle.classList.toggle('is-on', schedule.enabled === true);
      const status = $('#archiveBackupScheduleStatus');
      if (status) status.textContent = schedule.enabled ? '已启用' : '未启用';
    } catch (error) {
      result('archiveBackupScheduleResult', `无法读取自动备份设置：${errorMessage(error)}`, 'error');
    }
  }

  async function saveBackupSchedule() {
    const toggle = $('#archiveBackupScheduleEnabled');
    const payload = {
      backup: {
        targetDirectory: $('#archiveBackupScheduleDirectory')?.value.trim() || '',
        retention: { maxBackups: Number($('#archiveBackupScheduleCount')?.value || 7), maxAgeDays: Number($('#archiveBackupScheduleDays')?.value || 30) },
        schedule: { enabled: Boolean(toggle?.classList.contains('is-on')), frequency: $('#archiveBackupScheduleFrequency')?.value || 'weekly', timeOfDay: $('#archiveBackupScheduleTime')?.value || '05:00' }
      }
    };
    if (!window.confirm('确认保存自动备份计划？\n\n目标目录和计划会写入本机配置。')) return;
    try {
      await request('/api/ai-assets/backups/settings', { method: 'PUT', body: payload });
      result('archiveBackupScheduleResult', '自动备份计划已保存。');
    } catch (error) {
      result('archiveBackupScheduleResult', `保存自动备份计划失败：${errorMessage(error)}`, 'error');
    }
  }

  async function syncData(target) {
    if (!window.confirm('确认同步本地数据？\n\n系统会更新 AI 资产、日记与本地索引，运行期间可在“运行与任务”查看状态。')) return;
    target.disabled = true;
    target.textContent = '正在同步…';
    try {
      const queued = await request('/api/ai-assets/refresh', { method: 'POST', body: {} });
      result('archiveBackupStatus', `数据刷新任务已提交：${queued.runId || queued.jobId || '—'}。`);
      await loadTasks();
    } catch (error) {
      result('archiveBackupStatus', `同步失败：${errorMessage(error)}`, 'error');
    } finally {
      target.disabled = false;
      target.textContent = '开始同步';
    }
  }

  function onRoute(route) {
    if (route === 'assets' && !state.loaded.has('assets')) { state.loaded.add('assets'); loadSkillReview(); loadAssetRuntimeDetails(); }
    if (route === 'operations') {
      setDateDefaults();
      loadLive(); loadMessages(); loadTasks(); loadQa(); loadPipeline(); loadRefreshJobs();
      if (!state.liveTimer) state.liveTimer = window.setInterval(loadLive, 30000);
    }
    if (route === 'reports' && !state.loaded.has('reports')) { state.loaded.add('reports'); loadPeriodReport(7); }
    if (route === 'foundation') loadFoundationJobs();
    if (route === 'rag' && !state.loaded.has('rag')) { state.loaded.add('rag'); loadRag(); }
    if (route === 'settings' && !state.loaded.has('settings')) { state.loaded.add('settings'); loadSettings(); }
  }

  async function handleAction(target, action) {
    if (action === 'skill-review-refresh') return loadSkillReview();
    if (action === 'skill-crystallize-register') return crystallizeAndRegister();
    if (action === 'assets-detail-refresh') return loadAssetRuntimeDetails();
    if (action === 'assets-refresh') return refreshAssetRuntime();
    if (action === 'live-refresh') return loadLive();
    if (action === 'messages-refresh') return loadMessages();
    if (action === 'tasks-refresh') return loadTasks();
    if (action === 'qa-load') return loadQa();
    if (action === 'pipeline-load') return loadPipeline();
    if (action === 'foundation-refresh') return foundationRefresh(target);
    if (action === 'jobs-refresh') return loadRefreshJobs();
    if (action === 'foundation-jobs-refresh') return loadFoundationJobs();
    if (action === 'backfill-preview') return backfillPreview();
    if (action === 'backfill-run') return backfillRun(target);
    if (action === 'rag-coverage') return loadRagCoverage();
    if (action === 'rag-eval') return loadRagEval();
    if (action === 'rag-settings-save') return saveRagSettings();
    if (action === 'rag-external-plan') return planRagExternalSources();
    if (action === 'rag-migration') return planRagMigration();
    if (['rag-start', 'rag-stop', 'rag-sync', 'rag-index', 'local-sync', 'local-rebuild'].includes(action)) return ragOperator(action);
    if (action === 'memory-skill-plan') return loadMemorySkillPlan();
    if (action === 'memory-skill-register') return registerMemorySkill();
    if (action === 'settings-refresh') return loadSettings();
    if (action === 'settings-llm-refresh') return loadSettings();
    if (action === 'scheduler-refresh') return loadSchedulerStatus();
    if (action === 'dashboard-service-preview') return loadServicePreview('dashboard');
    if (action === 'rag-service-preview') return loadServicePreview('rag');
    if (action === 'period-load') return loadPeriodReport(Number(target.dataset.days || 7));
    if (action === 'period-refresh-assets') return refreshPeriod('assets');
    if (action === 'period-refresh-summary') return refreshPeriod('summary');
    if (action === 'period-print') return window.print();
    if (action === 'llm-move') return moveLlm(Number(target.dataset.llmIndex), Number(target.dataset.llmOffset));
    if (action === 'llm-test') return testLlm(Number(target.dataset.llmIndex));
    if (action === 'tools-rediscover') return rediscoverTools();
    if (action === 'diary-consistency') return diaryConsistency();
    if (action === 'sqlite-rebuild') return sqliteRebuild();
    if (action === 'backup-run') return backupRun();
    if (action === 'backup-status') return loadBackupStatus();
    if (action === 'backup-verify') return verifyBackup(target.dataset.backupId);
    if (action === 'backup-schedule-save') return saveBackupSchedule();
    if (action === 'sync-data') return syncData(target);
    if (action === 'message-read') return markMessageRead(target.dataset.messageId);
    if (action === 'task-action') return taskAction(target);
  }

  document.addEventListener('click', event => {
    const target = event.target.closest('[data-extended-action], [data-skill-review]');
    if (!target) return;
    if (target.dataset.skillReview) {
      if (target.checked) state.reviewSelected.add(target.dataset.skillReview);
      else state.reviewSelected.delete(target.dataset.skillReview);
      return;
    }
    event.preventDefault();
    handleAction(target, target.dataset.extendedAction).catch(error => console.error('Archive extended action failed:', error));
  });

  document.addEventListener('submit', event => {
    if (event.target.id === 'extendedRagSearchForm') ragSearch(event).catch(error => console.error('RAG search failed:', error));
  });
  document.addEventListener('click', event => {
    const target = event.target.closest('[data-backup-tab]');
    if (!target) return;
    if (target.dataset.backupTab === 'restore') window.setTimeout(loadBackupStatus, 0);
    if (target.dataset.backupTab === 'schedule') window.setTimeout(loadBackupSchedule, 0);
  });
  window.addEventListener('archive-route-change', event => onRoute(event.detail?.route));
  window.addEventListener('archive-diary-loaded', event => {
    const payload = event.detail;
    const sheet = $('#reportSheet');
    if (!sheet || !payload) return;
    const extras = [];
    if (payload.weather) extras.push(`<div><span>天气</span><b>${escapeHtml(payload.weather.description || payload.weather || '已记录')}</b></div>`);
    if (payload.kpi && typeof payload.kpi === 'object') extras.push(...Object.entries(payload.kpi).slice(0, 4).map(([key, value]) => `<div><span>${escapeHtml(key)}</span><b>${escapeHtml(safeValue(key, value))}</b></div>`));
    if (extras.length) sheet.insertAdjacentHTML('beforeend', `<section class="diary-detail-extras"><h3>当天数据摘要</h3><div>${extras.join('')}</div></section>`);
  });

  const initialRoute = String(location.hash || '').replace(/^#/, '') || 'home';
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => onRoute(initialRoute), { once: true });
  else window.setTimeout(() => onRoute(initialRoute), 0);

  window.ArchiveExtended = Object.freeze({ onRoute });
})();
