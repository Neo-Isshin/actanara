(() => {
  'use strict';

  const BASE = '/api/archive/v1';
  const DEFAULT_TIMEOUT_MS = 12000;

  function cookie(name) {
    const key = `${encodeURIComponent(name)}=`;
    const value = document.cookie.split('; ').find(item => item.startsWith(key));
    return value ? decodeURIComponent(value.slice(key.length)) : '';
  }

  async function request(path, options = {}) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), options.timeoutMs || DEFAULT_TIMEOUT_MS);
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
      let payload;
      try {
        payload = await response.json();
      } catch (_) {
        payload = null;
      }
      if (!response.ok) {
        const error = new Error(payload?.error || `HTTP ${response.status}`);
        error.status = response.status;
        error.payload = payload;
        throw error;
      }
      if (!payload || typeof payload !== 'object') throw new Error('Archive API returned an invalid response.');
      return payload;
    } catch (error) {
      if (error?.name === 'AbortError') throw new Error('Archive API request timed out.');
      throw error;
    } finally {
      window.clearTimeout(timeout);
    }
  }

  function settledValue(result) {
    return result.status === 'fulfilled' ? result.value : null;
  }

  async function load() {
    const [bootstrap, assets, runs, activity, memory, diaries] = await Promise.allSettled([
      request(`${BASE}/bootstrap`),
      request(`${BASE}/assets?limit=200&offset=0`),
      request(`${BASE}/skill-pass/runs?limit=50&offset=0`),
      request(`${BASE}/activity`),
      request('/api/memory/status?probe=false'),
      request('/api/diary-list?envelope=true')
    ]);
    const results = { bootstrap, assets, runs, activity, memory, diaries };
    return {
      bootstrap: settledValue(bootstrap),
      assets: settledValue(assets),
      runs: settledValue(runs),
      activity: settledValue(activity),
      memory: settledValue(memory),
      diaries: settledValue(diaries),
      errors: Object.entries(results)
        .filter(([, result]) => result.status === 'rejected')
        .map(([source, result]) => ({ source, message: String(result.reason?.message || result.reason || 'unavailable') }))
    };
  }

  async function asset(assetId) {
    if (!assetId) throw new Error('Missing asset identifier.');
    return request(`${BASE}/assets/${encodeURIComponent(assetId)}`);
  }

  async function searchMemory(query, source = 'all') {
    const sourceKinds = source === 'all' ? undefined : [source];
    return request('/api/memory/search', {
      method: 'POST',
      body: { query, topK: 12, mode: 'local', filters: sourceKinds ? { sourceSets: sourceKinds } : {} }
    });
  }

  async function diary(businessDate) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(String(businessDate || ''))) throw new Error('Invalid diary date.');
    return request(`/api/diary/${encodeURIComponent(businessDate)}`);
  }

  window.LivingArchiveApi = Object.freeze({ load, asset, searchMemory, diary });
})();
