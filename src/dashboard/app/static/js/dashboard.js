/* Asset-first Dashboard. Uses authenticated production services and the existing
 * action handlers. This file owns presentation, never runtime configuration. */
'use strict';
let assetDashboardPayload = null;
let assetDashboardPending = null;
let assetDashboardLastRead = 0;

const ASSET_DASHBOARD_COPY = {
  zh: {
    workspace:'工作台', skillRecords:'技能与提案', searchSkills:'搜索主技能库', previous:'上一页', next:'下一页', resultLimit:'返回结果数量', truncated:'文档较长，仅显示前 200,000 个字符。', indexManagement:'索引与服务管理', indexSettings:'索引设置', shareUsage:'分享用量图片', home:'资产总览', homeDescription:'找回已完成的工作，复用经验与技能。',
    localWorkspace:'本地工作资产', reviewDate:'复核日期', installedSkills:'已安装技能', agentSettings:'Agent 配置', storageTools:'存储与工具', refresh:'刷新', loading:'正在读取已保存的资产…',
    recent:'最近保存的成果', browse:'浏览资产与技能', searchAssets:'搜索成果标题', assetType:'资产类型',
    all:'全部类型', tasks:'任务成果', lessons:'学习经验', skills:'已保存技能', reports:'日记与报告',
    recentNote:'仅展示最近保存的记录；任务、经验与文档可能关联，不相加为一个资产总数。',
    review:'复核与处理', quickAccess:'继续工作', searchMemory:'检索过去的经验', searchMemoryNote:'按关键词查找来源与相关记录',
    readReports:'回顾日记与报告', readReportsNote:'按日、周、月查看工作记录', backup:'备份本地资产', backupNote:'选择备份范围并验证备份',
    sourceStatus:'数据来源状态', maintenance:'数据维护', historyUsage:'累计用量与近 30 天活动', periodUsage:'日记与报告中的用量',
    selectReport:'选择报告', periodUsageNote:'打开日记、周报或月报后，相应的使用统计将在此保留。', memoryNav:'记忆检索 · nova-RAG',
    error:'部分资产暂时无法读取。已知记录仍然可用，请刷新或到“数据维护”检查来源。',
    unavailable:'暂不可用', empty:'尚无记录', ready:'可读取', degraded:'读取不完整', saved:'已保存', draft:'技能提案',
    noResults:'没有匹配的近期记录。试试其他关键词或类型。', noAssets:'还没有可展示的成果。可先生成历史记录，或打开数据维护检查生成状态。',
    taskNote:'已完成的任务节点；不含分组与子步骤', lessonNote:'学习记录；Skill Pass 经验独立列示', skillNote:'Actanara 主技能库；不重复计入各工具副本',
    reportNote:'已就绪的日记、技术进展与学习文档', savedScope:'已保存记录', bounded:'统计范围有限', days:'个业务日期',
    proposals:'技能提案', proposalsNote:'查看依据与草案；提案数不等于待注册数', taskProposals:'任务提案', taskProposalsNote:'打开任务看板确认提案',
    messages:'消息与待处理事项', messagesNote:'查看需要确认或恢复的事项', details:'查看记录', source:'来源', date:'业务日期',
    state:'状态', openRelated:'打开相关页面', reportUsage:'查看此报告的用量', backReport:'返回报告', usage:'用量与活动',
    reportIndexEmpty:'暂无日期索引。生成历史记录后可按日、周、月查看。', generate:'生成历史数据',
    showInactive:'显示无活动日期', reportSearch:'搜索日期或报告', detailNote:'本页展示记录摘要；完整内容与操作位于相关业务页面。',
    updated:'读取于', noTotal:'按类别分别统计', reportInventory:'报告与文档', sourceTasks:'任务记录', sourceLessons:'经验与技能提案', sourceLearning:'学习记录', sourceSkills:'主技能库', sourceReports:'生成文档',
  },
  en: {
    workspace:'Workspace', skillRecords:'Skills & proposals', searchSkills:'Search canonical skills', previous:'Previous', next:'Next', resultLimit:'Number of results', truncated:'Showing the first 200,000 characters of this document.', indexManagement:'Index & service management', indexSettings:'Index settings', shareUsage:'Share usage image', home:'Asset overview', homeDescription:'Find completed work and reuse lessons and skills.',
    localWorkspace:'Local work assets', reviewDate:'Review date', installedSkills:'Installed skills', agentSettings:'Agent settings', storageTools:'Storage & tools', refresh:'Refresh', loading:'Reading saved assets…', recent:'Recently saved work', browse:'Browse assets & skills',
    searchAssets:'Search work titles', assetType:'Asset type', all:'All types', tasks:'Task outcomes', lessons:'Lessons', skills:'Saved skills', reports:'Diaries & reports',
    recentNote:'Recent saved records only. Tasks, lessons and documents may overlap and are not summed into one asset total.',
    review:'Review & follow up', quickAccess:'Continue working', searchMemory:'Find past experience', searchMemoryNote:'Search records and their sources',
    readReports:'Review diaries & reports', readReportsNote:'Browse daily, weekly and monthly records', backup:'Back up local assets', backupNote:'Choose scope and verify backups',
    sourceStatus:'Data source status', maintenance:'Data maintenance', historyUsage:'Lifetime usage & 30-day activity', periodUsage:'Usage from diaries & reports',
    selectReport:'Select a report', periodUsageNote:'Usage statistics appear here after opening a diary, weekly or monthly report.', memoryNav:'Memory search · nova-RAG',
    error:'Some assets could not be read. Available records remain visible. Refresh or check Data maintenance.',
    unavailable:'Unavailable', empty:'No records', ready:'Available', degraded:'Partial read', saved:'Saved', draft:'Skill proposal',
    noResults:'No matching recent records. Try a different title or type.', noAssets:'No saved outcomes yet. Generate historical records or check Data maintenance.',
    taskNote:'Completed task nodes; excludes groups and substeps', lessonNote:'Learning notes; Skill Pass lessons listed separately', skillNote:'Canonical Actanara skills; installed copies excluded',
    reportNote:'Ready diaries, technical progress and learning documents', savedScope:'Saved records', bounded:'Limited scope', days:'business dates',
    proposals:'Skill proposals', proposalsNote:'Review evidence and drafts; this is not a registration backlog', taskProposals:'Task proposals', taskProposalsNote:'Review proposals in the task board',
    messages:'Messages & follow-ups', messagesNote:'Review items requiring a decision or recovery', details:'Record details', source:'Source', date:'Business date', state:'Status',
    openRelated:'Open related page', reportUsage:'View usage for this report', backReport:'Back to report', usage:'Usage & activity', reportIndexEmpty:'No dates available yet. Generate historical records to browse reports.',
    generate:'Generate history', showInactive:'Show inactive dates', reportSearch:'Search dates or reports', detailNote:'This view shows a record summary. Full content and actions are on its related page.',
    updated:'Read at', noTotal:'Counted separately by type', reportInventory:'Reports & documents', sourceTasks:'Task records', sourceLessons:'Lessons & proposals', sourceLearning:'Learning notes', sourceSkills:'Canonical skills', sourceReports:'Generated documents',
  },
};

function assetDashboardText() {
  const language = typeof dashboardLanguageProfile === 'function' ? dashboardLanguageProfile() : 'zh';
  return ASSET_DASHBOARD_COPY[language === 'en' ? 'en' : 'zh'];
}

const DASH_ICONS = {
  home:'<path d="m3 10 9-7 9 7v10H3Z"/><path d="M9 20v-7h6v7"/>',
  layers:'<path d="m3 7 9-4 9 4-9 4Z"/><path d="m3 12 9 4 9-4M3 17l9 4 9-4"/>',
  task:'<path d="m3 6 2 2 3-4M11 6h10M3 13h5m3 0h10M3 20h5m3 0h10"/>',
  book:'<path d="M4 3h13a3 3 0 0 1 3 3v15H7a3 3 0 0 1-3-3Zm3 0v15M10 8h6m-6 4h6"/>',
  activity:'<path d="M2 12h4l3-8 5 16 3-8h5"/>',
  search:'<circle cx="10.5" cy="10.5" r="7.5"/><path d="m16 16 5 5"/>',
  settings:'<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3"/><circle cx="16" cy="17" r="3"/>',
  refresh:'<path d="M20 4v6h-6M4 20v-6h6M18.5 8A7 7 0 0 0 6 6L4 8m2 8a7 7 0 0 0 12 2l2-2"/>',
  save:'<path d="M4 3h13l3 3v15H4Zm4 0v6h8V3M8 21v-8h8v8"/>',
};
function dashIcon(name) {
  return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+(DASH_ICONS[name] || DASH_ICONS.layers)+'</svg>';
}
function dashEscape(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
}
function dashNumber(value) {
  return typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString() : '—';
}
function dashState(value) {
  const t=assetDashboardText();
  return ({ready:t.ready,empty:t.empty,degraded:t.degraded,unavailable:t.unavailable,error:t.unavailable,documented:t.saved,completed:t.saved,done:t.saved,settled:t.saved,draft:t.draft})[value] || t.unavailable;
}
function dashSource(value) {
  const t=assetDashboardText();
  return ({'nova-task-v2-sqlite':t.sourceTasks,'skill-pass-ledger':t.sourceLessons,'canonical-learning-lessons':t.sourceLearning,'canonical-skill-library':t.sourceSkills,'foundation-report-inventory':t.sourceReports})[value] || value;
}

function dashMetricMarkup(payload) {
  const t=assetDashboardText();
  return [
    ['completedTasks',t.tasks,t.taskNote,'task'],['learningNotes',t.lessons,t.lessonNote,'book'],
    ['skills',t.skills,t.skillNote,'layers'],['reports',t.reportInventory,t.reportNote,'save'],
  ].map(([key,label,note,icon]) => {
    const metric=payload?.assets?.[key] || {}; const scope=metric.scope || {};
    const limited=scope.complete === false ? ' · '+t.bounded : '';
    const scopeNote=key==='learningNotes' ? ' · Skill Pass: '+dashNumber(payload?.assets?.lessons?.count) : scope.kind==='latest-business-days' ? ' · '+(scope.includedDays ?? '—')+' '+t.days : '';
    return '<button type="button" class="dash-metric" data-dash-metric="'+key+'">'+dashIcon(icon)+'<span>'+dashEscape(label)+'</span><strong>'+dashNumber(metric.count)+'</strong><small>'+dashEscape(note+scopeNote+limited)+'</small><span class="dash-metric-state" data-state="'+dashEscape(metric.status || 'unavailable')+'">'+dashEscape(dashState(metric.status))+'</span></button>';
  }).join('');
}

async function loadAssetDashboard(force=false) {
  if(assetDashboardPending) return assetDashboardPending;
  if(!force && assetDashboardPayload && Date.now()-assetDashboardLastRead<30000) { renderAssetDashboard(); return assetDashboardPayload; }
  const status=document.getElementById('dashboardSummaryStatus');
  status?.removeAttribute('data-dash-text');
  if(status) { status.textContent=assetDashboardText().loading; status.dataset.state='loading'; }
  document.getElementById('dashboardMetrics')?.setAttribute('aria-busy','true');
  const button=document.getElementById('dashboardRefresh'); if(button) button.disabled=true;
  assetDashboardPending=(async()=>{
    try {
      const response=await fetch('/api/dashboard/summary',{signal:AbortSignal.timeout(20000)});
      if(!response.ok) throw new Error('HTTP '+response.status);
      const payload=await response.json();
      if(payload.schemaVersion!=='actanara.dashboard-summary.v1') throw new Error('Invalid summary contract');
      assetDashboardPayload=payload;assetDashboardLastRead=Date.now();renderAssetDashboard();return payload;
    } catch(error) {
      if(status) { status.textContent=assetDashboardText().error+' ('+error.message+')';status.dataset.state='error'; }
      if(!assetDashboardPayload) {
        document.getElementById('dashboardMetrics').innerHTML=dashMetricMarkup(null);
        document.getElementById('dashboardRecent').textContent=assetDashboardText().unavailable;
      }
    } finally {
      document.getElementById('dashboardMetrics')?.setAttribute('aria-busy','false');if(button) button.disabled=false;assetDashboardPending=null;
    }
  })();return assetDashboardPending;
}

function renderAssetDashboard() {
  if(!assetDashboardPayload) return;
  const t=assetDashboardText();const payload=assetDashboardPayload;
  document.getElementById('dashboardMetrics').innerHTML=dashMetricMarkup(payload);
  const status=document.getElementById('dashboardSummaryStatus');
  status.dataset.state=payload.status;
  status.textContent=payload.status==='degraded' ? t.error : t.savedScope+' · '+t.updated+' '+new Date(payload.generatedAt).toLocaleString(document.documentElement.lang)+' · '+t.noTotal;
  renderDashboardRecent();
  const draft=payload.review?.skillDrafts;const task=payload.review?.taskCandidates;
  document.getElementById('dashboardReview').innerHTML='<button type="button" class="dash-review-row" data-dash-section="aaSkillAssetReview"><span><b>'+dashEscape(t.proposals)+'</b><small>'+dashEscape(t.proposalsNote)+'</small></span><strong>'+dashNumber(draft?.count)+'</strong></button>'+
    '<a class="dash-review-row" href="/tasks"><span><b>'+dashEscape(t.taskProposals)+'</b><small>'+dashEscape(t.taskProposalsNote)+'</small></span><strong>'+dashNumber(task?.count)+'</strong></a>'+
    '<button type="button" class="dash-review-row" onclick="openMsgboxModal()"><span><b>'+dashEscape(t.messages)+'</b><small>'+dashEscape(t.messagesNote)+'</small></span><span aria-hidden="true">→</span></button>';
  document.getElementById('dashboardSources').innerHTML=(payload.sources || []).map(source=>'<div><span class="dash-status-dot" data-state="'+dashEscape(source.status)+'"></span><span>'+dashEscape(dashSource(source.id))+'</span><b>'+dashEscape(dashState(source.status))+'</b>'+(source.scope?.complete===false ? '<small>'+dashEscape(t.bounded)+'</small>':'')+'</div>').join('');
  renderAssetInventorySummary();decorateDashboardUi();
}
function renderAssetInventorySummary() {
  const target=document.getElementById('assetInventorySummary');
  if(target) target.innerHTML=dashMetricMarkup(assetDashboardPayload);
}
function renderDashboardRecent() {
  const t=assetDashboardText();const search=(document.getElementById('dashboardAssetSearch')?.value || '').trim().toLocaleLowerCase();
  const type=document.getElementById('dashboardAssetType')?.value || 'all';
  const items=(assetDashboardPayload?.recent || []).filter(item=>(type==='all'||item.type===type)&&String(item.title).toLocaleLowerCase().includes(search));
  const target=document.getElementById('dashboardRecent');if(!target) return;
  target.innerHTML=items.length ? items.map(item=>'<button type="button" class="dash-asset-row" data-dash-record="'+dashEscape(item.id)+'">'+dashIcon(({task:'task',lesson:'book',skill:'layers',report:'save'})[item.type])+'<span class="dash-asset-copy"><span class="dash-asset-label">'+dashEscape(item.status==='draft'?t.draft:({task:t.tasks,lesson:t.lessons,skill:t.skills,report:t.reports})[item.type])+'</span><b>'+dashEscape(item.title)+'</b><small>'+dashEscape(dashSource(item.source))+'</small></span><span class="dash-asset-meta"><span>'+dashEscape(item.businessDate || '—')+'</span><small>'+dashEscape(dashState(item.status))+'</small></span><span aria-hidden="true">→</span></button>').join('') : '<div class="dash-empty">'+dashIcon('layers')+'<p>'+dashEscape(search||type!=='all'?t.noResults:t.noAssets)+'</p><button type="button" class="dash-button" onclick="openHistoryBackfillModal()">'+dashEscape(t.generate)+'</button></div>';
}

let dashboardSkillItems=null;
let dashboardSkillPage=1;
let dashboardSkillsPending=null;
let dashboardSkillScope=null;
async function loadCanonicalSkills(force=false) {
  if(dashboardSkillsPending) return dashboardSkillsPending;
  if(dashboardSkillItems&&!force){renderCanonicalSkills();return;}
  const target=document.getElementById('dashboardCanonicalSkillList');if(!target) return;
  target.textContent=assetDashboardText().loading;
  dashboardSkillsPending=(async()=>{
    try {
      const response=await fetch('/api/dashboard/skills',{signal:AbortSignal.timeout(20000)});const payload=await response.json();
      if(!response.ok||!['ready','empty'].includes(payload.status)) throw new Error(payload.error || payload.status || 'HTTP '+response.status);
      dashboardSkillItems=Array.isArray(payload.items)?payload.items:[];dashboardSkillScope=payload.scope;dashboardSkillPage=1;renderCanonicalSkills();
    } catch(error) {
      target.innerHTML='<p role="alert">'+dashEscape(assetDashboardText().unavailable+': '+error.message)+'</p>';
    } finally {dashboardSkillsPending=null;}
  })();return dashboardSkillsPending;
}
function renderCanonicalSkills() {
  const t=assetDashboardText();const target=document.getElementById('dashboardCanonicalSkillList');if(!target||!dashboardSkillItems) return;
  const query=(document.getElementById('dashboardSkillSearch')?.value || '').trim().toLocaleLowerCase();
  const items=dashboardSkillItems.filter(item=>(item.name+' '+(item.description || '')).toLocaleLowerCase().includes(query));
  const pages=Math.max(1,Math.ceil(items.length/12));dashboardSkillPage=Math.min(dashboardSkillPage,pages);
  target.innerHTML=items.length?items.slice((dashboardSkillPage-1)*12,dashboardSkillPage*12).map(item=>'<button type="button" class="dash-asset-row" data-dash-skill="'+dashEscape(item.id)+'">'+dashIcon('layers')+'<span class="dash-asset-copy"><b>'+dashEscape(item.name)+'</b><small>'+dashEscape(item.description || t.skillNote)+'</small></span><span class="dash-asset-meta">'+dashEscape(t.saved)+'</span><span aria-hidden="true">→</span></button>').join(''):'<p class="dash-empty">'+dashEscape(query?t.noResults:t.empty)+'</p>';
  document.getElementById('dashboardCanonicalSkillPages').innerHTML='<span>'+dashEscape(t.sourceSkills)+' · '+dashNumber(items.length)+' · '+dashboardSkillPage+'/'+pages+(dashboardSkillScope?.complete===false?' · '+dashEscape(t.bounded):'')+'</span> <button type="button" class="dash-text-button" data-dash-skill-page="-1" '+(dashboardSkillPage<=1?'disabled':'')+'>'+dashEscape(t.previous)+'</button> <button type="button" class="dash-text-button" data-dash-skill-page="1" '+(dashboardSkillPage>=pages?'disabled':'')+'>'+dashEscape(t.next)+'</button>';
}
async function openCanonicalSkill(id) {
  const item=(dashboardSkillItems || []).find(row=>row.id===id);if(!item)return;
  const generation=openModal(item.name,'<p>'+dashEscape(assetDashboardText().loading)+'</p>');
  try {
    const response=await fetch('/api/dashboard/skills/'+encodeURIComponent(id),{signal:AbortSignal.timeout(20000)});const payload=await response.json();
    if(!response.ok) throw new Error(payload.error || 'HTTP '+response.status);
    if(!dashboardModalGenerationIsCurrent(generation))return;
    document.getElementById('modal-body').innerHTML=(payload.truncated?'<p role="status">'+dashEscape(assetDashboardText().truncated)+'</p>':'')+'<article class="report-body">'+renderSafeMarkdown(payload.content || '')+'</article>';
  } catch(error) {if(dashboardModalGenerationIsCurrent(generation))document.getElementById('modal-body').innerHTML='<p role="alert">'+dashEscape(assetDashboardText().unavailable+': '+error.message)+'</p><button type="button" class="dash-button" data-dash-skill="'+dashEscape(id)+'">'+dashEscape(assetDashboardText().refresh)+'</button>';}
}

async function openAssetDashboardSection(id) {
  const allowed=['aaSkillAssetReview','dashboardCanonicalSkills','aaSkillLib','aaDiary','aaAgentPanel','aaStorage','aaRag','aaToolConfigs'];
  if(!allowed.includes(id)) return;
  showPage('static');
  if(id==='dashboardCanonicalSkills') { await loadCanonicalSkills();if(!document.getElementById('page-static').classList.contains('active'))return false;document.getElementById(id)?.scrollIntoView({block:'start'});return true; }
  if(typeof loadAiAssets==='function') await loadAiAssets();
  if(!document.getElementById('page-static').classList.contains('active')) return false;
  const target=document.getElementById(id);
  if(target) { target.setAttribute('tabindex','-1'); target.scrollIntoView({block:'start',behavior:'smooth'});target.focus({preventScroll:true}); }
  return true;
}
function openDashboardRecord(id) {
  const item=assetDashboardPayload?.recent?.find(row=>row.id===id);if(!item) return;
  const t=assetDashboardText();
  const metadata=[[t.date,item.businessDate || '—'],[t.state,dashState(item.status)],[t.source,dashSource(item.source)]];
  openModal(item.title,'<div class="dash-record-detail">'+(item.summary?'<p class="dash-record-summary">'+dashEscape(item.summary)+'</p>':'')+'<dl>'+metadata.map(([key,value])=>'<div><dt>'+dashEscape(key)+'</dt><dd>'+dashEscape(value)+'</dd></div>').join('')+'</dl><p>'+dashEscape(t.detailNote)+'</p><button type="button" class="dash-button primary" data-dash-open-record="'+dashEscape(id)+'">'+dashEscape(t.openRelated)+'</button></div>');
}
async function followDashboardRecord(id) {
  const item=assetDashboardPayload?.recent?.find(row=>row.id===id);if(!item) return;
  closeModal();
  if(item.type==='task') { location.href='/tasks';return; }
  if(item.type==='report'&&/^\d{4}-\d{2}-\d{2}$/.test(item.businessDate || '')) {
    const generation=openModal(item.title,'<p>'+dashEscape(assetDashboardText().loading)+'</p>');
    try {
      const response=await fetch('/api/dashboard/document?'+new URLSearchParams({businessDate:item.businessDate,type:item.reportType}),{signal:AbortSignal.timeout(20000)});
      const result=await response.json();
      if(!response.ok) throw new Error(result.error || 'HTTP '+response.status);
      if(!dashboardModalGenerationIsCurrent(generation)) return;
      document.getElementById('modal-body').innerHTML=(result.truncated?'<p role="status">'+dashEscape(assetDashboardText().truncated)+'</p>':'')+'<article class="report-body">'+renderSafeMarkdown(result.content || '')+'</article>';
    } catch(error) {
      if(dashboardModalGenerationIsCurrent(generation)) document.getElementById('modal-body').innerHTML='<p role="alert">'+dashEscape(assetDashboardText().unavailable+': '+error.message)+'</p><button type="button" class="dash-button" data-dash-open-record="'+dashEscape(id)+'">'+dashEscape(assetDashboardText().refresh)+'</button>';
    }
    return;
  }
  if(item.source==='canonical-learning-lessons') {
    showPage('rag-search');
    document.getElementById('ragPageSearchQuery').value=item.title;
    document.getElementById('ragPageSearchSourceSets').value='';
    document.getElementById('ragPageSearchLifecycle').value='canonical';
    await runRagPageSearch();return;
  }
  if(item.type==='lesson'||item.status==='draft') {
    if(!await openAssetDashboardSection('aaSkillAssetReview')) return;
    await loadSkillAssetReview(item.businessDate);
    if(item.reviewId) {
      const target=Array.from(document.querySelectorAll('#aaSkillAssetReview [data-review-id]')).find(node=>node.dataset.reviewId===item.reviewId);
      if(target) { target.querySelector('details').open=true;target.tabIndex=-1;target.scrollIntoView({block:'center'});target.focus({preventScroll:true}); }
    }
    return;
  }
  await openAssetDashboardSection('aaSkillLib');
}

async function openDashboardReportIndex() {
  const t=assetDashboardText();
  const generation=openModal(t.reports,'<p>'+dashEscape(t.loading)+'</p>');
  if(typeof ACTANARA_DIARY_NAV_READY!=='undefined') await ACTANARA_DIARY_NAV_READY;
  if(!dashboardModalGenerationIsCurrent(generation)) return;
  const nodes=Array.from(document.querySelectorAll('#month-nav .nav-item'));
  const body=document.getElementById('modal-body');if(!body) return;
  body.innerHTML='<label class="dash-search"><span>'+dashIcon('search')+'</span><input type="search" id="dashboardReportSearch" placeholder="'+dashEscape(t.reportSearch)+'" aria-label="'+dashEscape(t.reportSearch)+'"></label><div id="dashboardReportChoices" class="dash-report-choices"></div>';
  const choices=document.getElementById('dashboardReportChoices');
  nodes.forEach((node,index)=>{
    const button=document.createElement('button');button.type='button';button.textContent=[node.dataset.monthId || node.dataset.reportId || node.dataset.diaryDate,node.textContent.trim()].filter(Boolean).join(' · ');
    button.dataset.reportChoice=String(index);
    button.addEventListener('click',()=>{closeModal();node.click();});choices.appendChild(button);
  });
  if(!nodes.length) choices.textContent=t.reportIndexEmpty;
  document.getElementById('dashboardReportSearch').addEventListener('input',event=>{
    for(const button of choices.querySelectorAll('button')) button.hidden=!button.textContent.toLocaleLowerCase().includes(event.target.value.toLocaleLowerCase());
  });
}

/* A report keeps its narrative and decisions. Its measured usage is moved,
 * not copied, so the original IDs, chart instances and detail handlers survive. */
function organizeDashboardUsage() {
  const destination=document.getElementById('usagePeriodContent');if(!destination) return;
  for(const page of document.querySelectorAll('#page-monthly-overview, #diary-pages .page')) {
    if(page.id==='page-monthly-overview' && (!page.dataset.monthRenderedId || page.dataset.monthRenderedId!==page.dataset.monthRequestedId)) continue;
    const prefix=page.id==='page-monthly-overview'?'mr':page.id.startsWith('page-report-')?'wr_'+page.id.slice(12).replace(/[^a-zA-Z0-9]/g,'_'):null;
    const candidates=[];
    if(prefix) {
      for(const suffix of ['kpi','work','trendChart','modelChart','usageTokens','workload']) {
        const el=document.getElementById(prefix+'_'+suffix);
        if(el&&page.contains(el)) {
          let section=el.closest('.wr-section');
          // Monthly workload shares a grid with knowledge; preserve the knowledge card.
          if(suffix==='workload' && section?.querySelector('[id$="_knowledge"]')) section=el.closest('.wr-card');
          if(section) candidates.push(section);
        }
      }
    } else {
      for(const selector of ['.stat-grid','.dash-diary-usage','.hourly-heatmap','.diary-heatmap-wrap','.diary-agent-stats']) {
        const el=page.querySelector(selector);if(el) candidates.push(selector==='.stat-grid'?el:el.closest('.section')||el);
      }
    }
    const unique=Array.from(new Set(candidates)).filter(node=>!candidates.some(parent=>parent!==node&&parent.contains(node)));
    const targetId='usage-for-'+page.id;
    let target=document.getElementById(targetId);
    if(target) {
      target.querySelector('summary').textContent=(page.querySelector('.page-title')?.textContent || page.id)+(page.dataset.monthRequestedId?' · '+page.dataset.monthRequestedId:'');
      target.dataset.reportDay=page.dataset.diaryRequestedDate || '';
      target.dataset.reportMonth=page.dataset.monthRequestedId || '';
    }
    if(!unique.length) continue;
    if(!target) {
      target=document.createElement('details');target.id=targetId;target.className='dash-period-usage';
      const summary=document.createElement('summary');summary.textContent=page.querySelector('.page-title')?.textContent || page.id;target.appendChild(summary);
      const back=document.createElement('button');back.type='button';back.className='dash-text-button';back.dataset.usageBack=page.id;back.textContent=assetDashboardText().backReport;
      back.addEventListener('click',()=>{if(target.dataset.reportDay) showDiaryByDate(target.dataset.reportDay);else if(target.dataset.reportMonth) loadMonthlyReportById(target.dataset.reportMonth);else location.hash=page.id;});target.appendChild(back);destination.prepend(target);
      target.addEventListener('toggle',()=>{if(target.open) resizeDashboardCharts();});
    }
    // Refresh only this report's usage, never accumulate old duplicate IDs.
    for(const child of Array.from(target.children)) if(!['SUMMARY','BUTTON'].includes(child.tagName)) child.remove();
    for(const node of unique) target.appendChild(node);
    const periodTitle=page.querySelector('.page-title')?.textContent || page.id;
    target.querySelector('summary').textContent=periodTitle+(page.dataset.monthRequestedId?' · '+page.dataset.monthRequestedId:'');
    target.dataset.reportDay=page.dataset.diaryRequestedDate || '';
    target.dataset.reportMonth=page.dataset.monthRequestedId || '';
    if(!page.querySelector('[data-view-period-usage]')) {
      const link=document.createElement('button');link.type='button';link.className='dash-button dash-report-usage-link';link.dataset.viewPeriodUsage=targetId;link.textContent=assetDashboardText().reportUsage;
      link.addEventListener('click',()=>{const current=document.getElementById(link.dataset.viewPeriodUsage);if(!current||current.hidden)return;showPage('overview');current.open=true;current.scrollIntoView({block:'start'});resizeDashboardCharts();});
      page.querySelector('.page-header')?.appendChild(link);
    }
  }
}
function resizeDashboardCharts() {
  requestAnimationFrame(()=>{if(window.Chart?.instances) Object.values(Chart.instances).forEach(chart=>{if(chart.canvas?.getClientRects().length) chart.resize();});});
}
function decorateDashboardUi() {
  const t=assetDashboardText();
  const english=typeof dashboardLanguageProfile==='function'&&dashboardLanguageProfile()==='en';
  for(const el of document.querySelectorAll('[data-dash-text]')) if(t[el.dataset.dashText]) el.textContent=t[el.dataset.dashText];
  for(const el of document.querySelectorAll('[data-dash-label]')) if(t[el.dataset.dashLabel]) {el.setAttribute('aria-label',t[el.dataset.dashLabel]);el.title=t[el.dataset.dashLabel];}
  for(const el of document.querySelectorAll('[data-dash-placeholder]')) if(t[el.dataset.dashPlaceholder]) {el.placeholder=t[el.dataset.dashPlaceholder];el.setAttribute('aria-label',t[el.dataset.dashPlaceholder]);}
  for(const el of document.querySelectorAll('[data-dash-icon]:not([data-icon-ready])')) {el.innerHTML=dashIcon(el.dataset.dashIcon);el.dataset.iconReady='1';}
  for(const [id,en,zh] of [['historyBackfillButton','History','生成历史'],['taskMonitorButton','Tasks','后台任务'],['msgboxButton','Messages','消息']]) {
    const button=document.getElementById(id);if(button){button.dataset.mobileLabel=english?en:zh;button.setAttribute('aria-label',button.title || button.textContent.trim());}
  }
  for(const button of document.querySelectorAll('.mobile-bottom-nav [data-mobile-page]')) {
    const labels={home:english?'Assets':'总览',overview:english?'Usage':'用量',static:english?'Library':'资产库','rag-search':english?'Search':'检索','foundation-ops':english?'Maintain':'维护'};
    button.dataset.mobileLabel=labels[button.dataset.mobilePage] || '';
  }
  for(const el of document.querySelectorAll('.asset-dashboard .page [onclick]:not(button):not(a):not(input):not(select):not(textarea),.aa-skill-capsule,[data-aa-agent-row],[data-aa-agent-item]')) {
    if(el.matches('option,summary')||el.querySelector('input,button,select,textarea')) continue;
    if(!el.hasAttribute('tabindex')) el.tabIndex=0;
    if(!el.hasAttribute('role')) el.setAttribute('role','button');
  }
  for(const link of document.querySelectorAll('[data-view-period-usage]')) link.textContent=t.reportUsage;
  for(const link of document.querySelectorAll('[data-usage-back]')) link.textContent=t.backReport;
}

document.addEventListener('click',event=>{
  const skill=event.target.closest('[data-dash-skill]');if(skill){openCanonicalSkill(skill.dataset.dashSkill);return;}
  const pagination=event.target.closest('[data-dash-skill-page]');if(pagination){dashboardSkillPage+=Number(pagination.dataset.dashSkillPage);renderCanonicalSkills();return;}
  const record=event.target.closest('[data-dash-record]');if(record){openDashboardRecord(record.dataset.dashRecord);return;}
  const follow=event.target.closest('[data-dash-open-record]');if(follow){followDashboardRecord(follow.dataset.dashOpenRecord);return;}
  const section=event.target.closest('[data-dash-section]');if(section){openAssetDashboardSection(section.dataset.dashSection);return;}
  const metric=event.target.closest('[data-dash-metric]');if(metric){
    if(metric.dataset.dashMetric==='completedTasks') location.href='/tasks';
    else if(metric.dataset.dashMetric==='reports') openDashboardReportIndex();
    else if(metric.dataset.dashMetric==='learningNotes' && document.getElementById('page-home').classList.contains('active')) {document.getElementById('dashboardAssetType').value='lesson';renderDashboardRecent();document.getElementById('dashboardAssetType').focus();}
    else openAssetDashboardSection(metric.dataset.dashMetric==='skills'?'dashboardCanonicalSkills':'aaSkillAssetReview');
  }
});
document.addEventListener('DOMContentLoaded',()=>{
  document.getElementById('dashboardAssetSearch')?.addEventListener('input',renderDashboardRecent);
  document.getElementById('dashboardAssetType')?.addEventListener('change',renderDashboardRecent);
  document.getElementById('dashboardSkillSearch')?.addEventListener('input',()=>{dashboardSkillPage=1;renderCanonicalSkills();});
  decorateDashboardUi();
  if(document.getElementById('page-home')?.classList.contains('active')) loadAssetDashboard();
  let scheduled=false;
  new MutationObserver(()=>{
    if(scheduled) return;scheduled=true;
    requestAnimationFrame(()=>{scheduled=false;organizeDashboardUsage();});
  }).observe(document.getElementById('diary-pages'),{childList:true,subtree:true});
  setInterval(()=>{if(!document.hidden&&document.getElementById('page-home')?.classList.contains('active')) loadAssetDashboard(true);},60000);
});
