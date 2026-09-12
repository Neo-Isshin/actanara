const conceptButtons = [...document.querySelectorAll('[data-concept-target]')];
const conceptScreens = [...document.querySelectorAll('[data-concept-screen]')];

function selectConcept(id) {
  const screen = conceptScreens.find((item) => item.dataset.conceptScreen === id);
  if (!screen) return;
  document.body.dataset.concept = id;
  conceptScreens.forEach((item) => item.classList.toggle('is-active', item === screen));
  conceptButtons.forEach((button) => {
    const selected = button.dataset.conceptTarget === id;
    button.classList.toggle('is-active', selected);
    button.setAttribute('aria-pressed', String(selected));
  });
  history.replaceState(null, '', `#${id}`);
}

conceptButtons.forEach((button) => {
  button.addEventListener('click', () => selectConcept(button.dataset.conceptTarget));
});

document.querySelectorAll('.obs-segments, [data-demo-tabs]').forEach((group) => {
  group.addEventListener('click', (event) => {
    const button = event.target.closest('button');
    if (!button) return;
    group.querySelectorAll('button').forEach((item) => item.classList.toggle('is-active', item === button));
  });
});

let toastTimer;
const toast = document.querySelector('[data-demo-toast]');

function showToast(message) {
  if (!toast) return;
  toast.textContent = message;
  toast.classList.add('is-visible');
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => toast.classList.remove('is-visible'), 2400);
}

document.querySelectorAll('[data-nav-toggle]').forEach((button) => {
  button.addEventListener('click', () => {
    const expanded = button.getAttribute('aria-expanded') === 'true';
    button.setAttribute('aria-expanded', String(!expanded));
    const children = button.parentElement.querySelector('[data-nav-children]');
    if (children) children.hidden = expanded;
  });
});

document.querySelectorAll('[data-demo-route]').forEach((link) => {
  link.addEventListener('click', (event) => {
    event.preventDefault();
    const primaryLink = link.closest('.obs-nav') && link.classList.contains('obs-nav-link');
    if (primaryLink) {
      document.querySelectorAll('.obs-nav-link').forEach((item) => item.classList.toggle('is-active', item === link));
    }
    showToast(`${link.dataset.demoRoute}：正式接入时沿用现有业务页面与数据接口`);
  });
});

document.querySelectorAll('[data-dialog-open]').forEach((button) => {
  button.addEventListener('click', () => {
    const dialog = document.getElementById(button.dataset.dialogOpen);
    if (dialog && typeof dialog.showModal === 'function') dialog.showModal();
  });
});

document.querySelectorAll('dialog').forEach((dialog) => {
  dialog.addEventListener('click', (event) => {
    if (event.target === dialog) dialog.close();
  });
});

const historyForm = document.querySelector('[data-history-form]');
if (historyForm) {
  historyForm.addEventListener('submit', (event) => {
    if (event.submitter?.value !== 'queue') return;
    const taskCount = document.querySelector('[data-task-count]');
    if (taskCount) taskCount.textContent = String(Number(taskCount.textContent || 0) + 1);
    showToast('历史数据计划已加入后台队列');
  });
}

const actionMessages = {
  'asset-refresh': 'AI 资产快照已刷新',
  'asset-update': '资产投影更新已加入后台队列',
  'asset-share': 'AI 资产分享图片已生成',
  'asset-backup': '数据备份检查已打开',
};

document.querySelectorAll('[data-demo-action]').forEach((button) => {
  button.addEventListener('click', (event) => {
    if (button.tagName === 'A') event.preventDefault();
    const action = button.dataset.demoAction;
    if (action === 'asset-refresh') {
      const snapshotTime = document.querySelector('[data-snapshot-time]');
      if (snapshotTime) snapshotTime.textContent = '刚刚';
      button.disabled = true;
      window.setTimeout(() => { button.disabled = false; }, 500);
    }
    if (action === 'asset-update') {
      const taskCount = document.querySelector('[data-task-count]');
      if (taskCount) taskCount.textContent = String(Number(taskCount.textContent || 0) + 1);
    }
    showToast(actionMessages[action] || action);
  });
});

const demoSearch = document.querySelector('[data-demo-search]');
if (demoSearch) {
  demoSearch.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter') return;
    const query = demoSearch.value.trim();
    showToast(query ? `正在跨任务、资产与记忆检索“${query}”` : '请输入任务、资产或记忆关键词');
  });
}

const initialConcept = location.hash.slice(1);
if (initialConcept) selectConcept(initialConcept);
