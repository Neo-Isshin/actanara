/* Authored example records for the v1.8.0 public UI. No runtime export, secret,
 * local file contents or external service credentials are included. */
(function () {
  'use strict';
  const day = '2026-07-05';
  const timestamp = '2026-07-06T09:00:00-07:00';
  const state = { schemaVersion: 1, status: 'ready', sourceErrors: [] };
  const sources = ['nova-task-v2-sqlite', 'canonical-learning-lessons', 'skill-pass-ledger', 'canonical-skill-library', 'foundation-report-inventory'];
  const metric = (count, source, unit) => ({ count, source, unit, status: count ? 'ready' : 'empty', scope: { kind: 'demo-snapshot', complete: true } });
  const skills = [
    { id: 'asset-demo-restore', name: 'verify-restored-files', description: '恢复文件后检查清单与校验值；验证失败时保留原文件。',
      content: '# Verify restored files\n\n演示技能 · Sample Skill\n\n## 适用场景\n验证从备份恢复的文件是否完整。\n\n1. 对照备份清单，确认文件数量和相对路径。\n2. 比较每个文件的 SHA-256 校验值。\n3. 单独记录缺失或不匹配的文件，不覆盖原文件。\n4. 输出验证结果以及下一步处理建议。' },
    { id: 'asset-demo-evidence', name: 'review-release-evidence', description: '把测试、构建与发布记录整理成可以回溯的证据清单。',
      content: '# Review release evidence\n\n演示技能 · Sample Skill\n\n## 输出\n一个包含结果、版本和证据来源的发布检查清单。\n\n- 确认源码版本和产物版本一致。\n- 记录通过、失败和跳过的检查。\n- 校验发布文件并保留来源信息。\n- 将待确认的问题单独列出，不当作已完成。' },
  ];
  const lessons = [
    { id: 'demo-lesson-source', title: '先核对数据来源，再解释统计变化', summary: '当两个界面的数值不同，先比较周期、数据范围与缺失值含义；没有数据不应显示成零。' },
    { id: 'demo-lesson-backup', title: '恢复完成不等于恢复验证通过', summary: '恢复后核对清单和校验值，再判断是否可以替换原文件。未验证的恢复结果应单独保存。' },
    { id: 'demo-lesson-task', title: '给任务结论保留直接证据', summary: '记录结果时关联测试输出、文件变化和对应会话，方便其他 Agent 查找并复用。' },
  ];
  const documents = {};
  for (const date of ['2026-07-03', day]) {
    for (const [type, label] of [['narrative', '工作日记'], ['technical', '技术进展'], ['learning', '学习记录']]) {
      documents[date + '/' + type] = { status: 'ready', title: date + ' · ' + label, date, businessDate: date, type, truncated: false,
        content: '# ' + date + ' · ' + label + '\n\n> 在线演示文档，非真实运行记录。\n\n## 工作成果\n\n完成备份验证流程的梳理，并为任务结果补充可回溯的证据。\n\n## 可复用经验\n\n- 将任务成果、学习经验与技能分别保存。\n- Token 用量用于分析工作轨迹，不作为可复用资产计数。\n- 在发布前核对源码版本与产物校验值。\n\n## 后续工作\n\n复核技能提案，在确认适用范围后再注册到本机工具。', source: 'public-demo', dashboardState: state };
    }
  }
  const reviewItems = [
    { reviewId: 'review-001', candidateId: 'candidate-001', assetClass: 'skill', originalDecision: 'skill', disposition: 'create',
      title: '把发布验证步骤保存为可重复使用的技能', summary: '包含测试结果、版本对应关系和文件校验；适用于重复发布场景。', reason: '示例：重复步骤具有明确输入、输出与验证条件。',
      scores: { evidence: 4, value: 4, reuse: 4, program: 4 }, completion: 'verified', libraryAction: 'create', evidenceCount: 3,
      skillName: 'review-release-evidence', skillDescription: skills[1].description, skillMarkdown: skills[1].content, selectedByDefault: true, humanOverride: false },
    ...lessons.slice(0, 2).map((lesson, index) => ({ ...lesson, reviewId: 'review-00' + (index + 2), candidateId: 'candidate-00' + (index + 2),
      assetClass: 'lesson', originalDecision: 'lesson', disposition: 'documented', reason: '示例：已记录经验，尚未判断是否适合程序化。',
      scores: { evidence: 4, value: 4, reuse: 3, program: 2 }, completion: 'verified', evidenceCount: 2, selectedByDefault: false, humanOverride: false })),
  ];
  const recent = [
    ...lessons.map(item => ({ ...item, type: 'lesson', status: 'documented', businessDate: day, source: sources[1], href: '#page-rag-search' })),
    { id: 'demo-skill-proposal', type: 'skill', status: 'draft', title: reviewItems[0].title, summary: reviewItems[0].summary, businessDate: day, source: sources[2], reviewId: 'review-001', href: '#page-static' },
    ...Object.values(documents).map(item => ({ id: 'demo-doc-' + item.date + '-' + item.type, type: 'report', reportType: item.type, title: item.title, status: 'ready', businessDate: item.date, source: sources[4], href: '#page-home' })),
  ];
  const summary = { schemaVersion: 'actanara.dashboard-summary.v1', status: 'ready', generatedAt: timestamp,
    assets: { completedTasks: metric(0, sources[0], 'tasks'), learningNotes: metric(lessons.length, sources[1], 'lessons'), lessons: metric(2, sources[2], 'lessons'), skills: metric(skills.length, sources[3], 'canonicalSkills'), reports: metric(Object.keys(documents).length, sources[4], 'documents') },
    review: { skillDrafts: metric(1, sources[2], 'proposals'), taskCandidates: metric(0, sources[0], 'proposals') }, recent,
    sources: sources.map(id => ({ id, status: 'ready', scope: { kind: 'demo-snapshot', complete: true }, readOnly: true })), sourceErrors: [], dashboardState: state };
  const fixtures = {
    '/api/dashboard/summary': summary,
    '/api/dashboard/skills': { status: 'ready', items: skills.map(({ content, ...item }) => item), count: skills.length, scope: { complete: true }, source: 'public-demo', dashboardState: state },
    '/api/ai-assets/skill-assets': { status: 'ready', businessDate: day, promptVersion: 'minimal-v22', counts: { skill: 1, lesson: 2, reference: 0, discard: 0 }, items: reviewItems },
    '/api/background-tasks?limit=30': { activeCount: 0, tasks: [{ id: 'demo-job-report', title: '示例：周报已生成', source: 'pipeline', status: 'completed', progress: 100,
      subtitle: '用于展示任务详情的静态记录；没有后台进程在执行。', startedAt: timestamp, completedAt: timestamp }], active: [], summary: { total: 1, active: 0, byStatus: { completed: 1 }, bySource: { pipeline: 1 } }, sources: {}, generatedAt: timestamp },
    '/api/msgbox?limit=20': { count: 1, attentionCount: 1, items: [{ id: 'demo-msg-skill', type: 'skill_review', severity: 'info', title: '示例：查看技能提案的依据与草案', summary: '在线演示只展示复核内容。生成和注册技能需要安装 Actanara 后执行。', createdAt: timestamp, read: false, action: { kind: 'openUrl', url: '#page-static' } }], generatedAt: timestamp },
    '/api/rag/settings': { mode: 'v2', enabled: false, server: { host: '127.0.0.1', port: 3037 }, embedding: { mode: 'local', local: { model: 'intfloat/multilingual-e5-small' } }, indexing: { sourceSets: ['lessons', 'diary'] }, externalSources: { enabled: false, mode: 'supplement', paths: [] }, retrieval: { topK: 8 } },
    '/api/rag/status': { mode: 'v2', enabled: false, server: { status: 'disabled', healthy: false }, index: { entries: 0 }, demo: true },
    '/api/memory/status': { available: true, backend: { kind: 'demo-lexical', semantic: false }, local: { status: 'ready', documentCount: lessons.length }, demo: true },
    '/api/settings/services/rag/preview': { status: 'unsupported', supported: false, installed: false, running: false, reason: 'Public demo: no local service is connected.' },
    '/api/settings/services/dashboard/preview': { status: 'unsupported', supported: false, installed: false, running: false, reason: 'Public demo: no local service is connected.' },
    '/api/llm-provider-chain': { schemaVersion: 1, enabled: false, providers: [], readiness: { ready: false, status: 'not-configured' } },
  };
  for (const skill of skills) fixtures['/api/dashboard/skills/' + skill.id] = { ...skill, title: skill.name, status: 'ready', truncated: false, source: 'public-demo', dashboardState: state };
  window.ACTANARA_ASSET_DEMO_DATA = { version: '1.8.0', businessDate: day, fixtures, documents, lessons };
})();
