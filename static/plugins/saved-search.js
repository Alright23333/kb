/* ══════════════════════════════════════════════════════════════════════
   插件：saved-search —— 保存常用搜索
   ─────────────────────────────────────────────────────────────────────
   旧版 getSavedSearches()/saveSearch()/removeSavedSearch() 的移植。
   存储键沿用 kb-saved-searches（与旧版同名同格式），所以两套 UI 之间
   切换时收藏不丢。

   注入点（插件不能改 app.js，所以用 MutationObserver 盯着 #view）：
     · 搜索结果页 → 顶部加「📌 固定当前搜索」按钮
     · 首页       → 加一张「常用搜索」卡片，chips 点一下就搜
   命令：:Searches 列出已固定的搜索（可删除）
   ══════════════════════════════════════════════════════════════════════ */
KB.define('saved-search', (api, opts) => {
  const KEY = 'kb-saved-searches';
  const MAX = (opts && opts.max) || 8;

  function list() {
    try { return JSON.parse(localStorage.getItem(KEY) || '[]'); }
    catch (e) { return []; }
  }
  function save(q) {
    if (!q) return;
    const items = list().filter(s => s.q !== q);
    items.unshift({ q, ts: Date.now() });
    localStorage.setItem(KEY, JSON.stringify(items.slice(0, MAX)));
  }
  function remove(q) {
    localStorage.setItem(KEY, JSON.stringify(list().filter(s => s.q !== q)));
  }

  /* ── 注入：搜索页的固定按钮 ── */
  function injectSearchButton() {
    const view = api.ui.view();
    if (!view) return;
    const head = view.querySelector('.list-head');
    if (!head || head.querySelector('.ss-pin')) return;
    const q = decodeURIComponent(location.pathname.split('/search/')[1] || '');
    if (!q) return;
    const btn = document.createElement('button');
    btn.className = 'ss-pin';
    const already = list().some(s => s.q === q);
    btn.textContent = already ? '★ 已固定' : '☆ 固定';
    btn.title = already ? '取消固定' : '固定这条搜索';
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      if (list().some(s => s.q === q)) { remove(q); btn.textContent = '☆ 固定'; }
      else { save(q); btn.textContent = '★ 已固定'; }
      api.ui.toast(list().some(s => s.q === q) ? '已固定到首页' : '已取消固定');
      homeSig = null; injectHome();
    });
    head.appendChild(btn);
  }

  /* ── 注入：首页的「常用搜索」卡片 ── */
  /* 关键：必须幂等。MutationObserver 盯着 #view，一旦我们改 DOM 就会
   * 再次触发自己 —— 不做幂等就是无限循环（浏览器直接卡死）。
   * 做法：内容签名不变就完全不碰 DOM。 */
  let homeSig = null;
  function injectHome() {
    const view = api.ui.view();
    if (!view) return;
    const dash = view.querySelector('.dash');
    if (!dash) { homeSig = null; return; }
    const items = list();
    const sig = items.map(s => s.q).join('\u0001');
    let card = dash.querySelector('.ss-card');
    if (!items.length) {
      if (card) { card.remove(); homeSig = ''; }
      return;
    }
    if (!card) {
      card = document.createElement('div');
      card.className = 'card ss-card';
      dash.querySelector('.dash-grid').appendChild(card);
    }
    if (sig === homeSig && card.dataset.sig === sig) return;   // 没变，别碰
    homeSig = sig;
    card.dataset.sig = sig;
    card.innerHTML = '<h3>常用搜索</h3><div class="tagcloud"></div>';
    const cloud = card.querySelector('.tagcloud');
    for (const s of items) {
      const chip = document.createElement('span');
      chip.className = 'chip ss-chip';
      chip.dataset.q = s.q;
      chip.textContent = s.q;
      chip.addEventListener('click', (e) => {
        if (e.target.classList.contains('ss-x')) return;
        api.ui.navigate('search', s.q);
      });
      const x = document.createElement('span');
      x.className = 'ss-x';
      x.textContent = '✕';
      x.title = '删除';
      x.addEventListener('click', (e) => {
        e.stopPropagation();
        remove(s.q);
        api.ui.toast('已删除');
        homeSig = null; injectHome();
      });
      chip.appendChild(x);
      cloud.appendChild(chip);
    }
  }

  /* ── :Searches 面板：列出 + 删除 ── */
  let panel = null;
  function openPanel() {
    if (!panel) {
      panel = api.ui.panel({
        id: 'kb-saved-search', mode: 'overlay', scrim: false, title: '常用搜索',
      });
    }
    if (panel.isOpen()) { panel.close(); return; }
    const items = list();
    panel.body.innerHTML = items.length
      ? items.map(s => `<div class="ss-row" data-q="${api.ui.escapeHtml(s.q)}">
            <span class="ss-q">${api.ui.escapeHtml(s.q)}</span>
            <button class="ss-del" data-del="${api.ui.escapeHtml(s.q)}">删除</button>
          </div>`).join('')
      : '<div class="hp-empty">还没有固定的搜索</div>';
    panel.body.querySelectorAll('[data-del]').forEach(btn => {
      btn.addEventListener('click', () => {
        remove(btn.dataset.del);
        api.ui.toast('已删除');
        homeSig = null;
        openPanel2();
        injectHome();
      });
    });
    panel.body.querySelectorAll('[data-q]').forEach(el => {
      el.addEventListener('click', (ev) => {
        if (ev.target.closest('[data-del]')) return;
        api.ui.navigate('search', el.dataset.q);
      });
    });
    panel.open();
  }
  function openPanel2() {
    const items = list();
    panel.body.innerHTML = items.length
      ? items.map(s => `<div class="ss-row" data-q="${api.ui.escapeHtml(s.q)}">
            <span class="ss-q">${api.ui.escapeHtml(s.q)}</span>
            <button class="ss-del" data-del="${api.ui.escapeHtml(s.q)}">删除</button>
          </div>`).join('')
      : '<div class="hp-empty">还没有固定的搜索</div>';
    panel.body.querySelectorAll('[data-del]').forEach(btn => {
      btn.addEventListener('click', () => { remove(btn.dataset.del); api.ui.toast('已删除'); openPanel2(); injectHome(); });
    });
    panel.body.querySelectorAll('[data-q]').forEach(el => {
      el.addEventListener('click', (ev) => {
        if (ev.target.closest('[data-del]')) return;
        api.ui.navigate('search', el.dataset.q);
      });
    });
  }

  /* ── 观察 #view，路由一变就补注入 ── */
  function watch() {
    const view = api.ui.view();
    if (!view) { setTimeout(watch, 200); return; }
    let raf = 0;
    const schedule = () => {
      if (raf) return;
      raf = requestAnimationFrame(() => { raf = 0; injectSearchButton(); injectHome(); });
    };
    const mo = new MutationObserver(schedule);
    mo.observe(view, { childList: true, subtree: true });
    injectSearchButton();
    injectHome();
  }

  api.css('plugins/saved-search.css');
  api.command('Searches', '常用搜索（固定/删除）', openPanel);
  api.keys([{ lhs: 'uS', desc: '常用搜索', run: openPanel }]);
  api.on('boot', () => setTimeout(watch, 100));
  api.on('navigate', () => { setTimeout(() => { injectSearchButton(); injectHome(); }, 60); });
});
