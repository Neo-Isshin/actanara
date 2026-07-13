(() => {
  'use strict';
  const pages = [...document.querySelectorAll('[data-page]')];
  const navItems = [...document.querySelectorAll('[data-route]')];
  const modal = document.getElementById('settings-modal');
  const toast = document.getElementById('toast');

  function showToast(message) {
    toast.textContent = message;
    toast.classList.add('show');
    window.setTimeout(() => toast.classList.remove('show'), 1800);
  }

  function activate(route) {
    const selected = pages.some(page => page.dataset.page === route) ? route : 'overview';
    pages.forEach(page => page.classList.toggle('active', page.dataset.page === selected));
    navItems.forEach(item => item.classList.toggle('active', item.dataset.route === selected));
    history.replaceState(null, '', `#${selected}`);
    window.scrollTo({top: 0, behavior: 'smooth'});
  }

  navItems.forEach(item => item.addEventListener('click', event => {
    event.preventDefault();
    activate(item.dataset.route);
  }));
  document.getElementById('open-settings').addEventListener('click', () => { modal.hidden = false; });
  document.getElementById('close-settings').addEventListener('click', () => { modal.hidden = true; });
  modal.addEventListener('click', event => { if (event.target === modal) modal.hidden = true; });
  document.getElementById('demo-search').addEventListener('click', () => showToast('已从合成索引返回 2 条示例证据'));
  document.querySelectorAll('[data-demo-action]').forEach(button => button.addEventListener('click', () => showToast('审阅弹窗示例：静态 Demo 不会写入任务状态')));
  document.getElementById('language-toggle').addEventListener('click', event => {
    event.currentTarget.textContent = event.currentTarget.textContent === 'EN' ? '中' : 'EN';
    showToast('语言切换为交互示例；主要内容保持中文');
  });
  document.addEventListener('keydown', event => { if (event.key === 'Escape') modal.hidden = true; });
  activate(location.hash.slice(1) || 'overview');
})();
