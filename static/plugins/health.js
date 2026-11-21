/* ══════════════════════════════════════════════════════════════════════
   插件：health —— 知识库体检报告
   ─────────────────────────────────────────────────────────────────────
   旧版 computeHealth()/loadHealth() 的插件化移植。三个数据源：
     /api/graph  nodes+edges  → 孤立页（零入链）、断链（指向不存在的页）
     /api/pages  updated_at   → 近 7 天活跃
     /api/tags                → TOP 标签
   界面：dock 右侧面板（不挡正文），:health 或 space uh 开关。
   密度：每组超过 opts.fold 条就折叠 —— 旧版 155 项直下 22880px 的教训。
   ══════════════════════════════════════════════════════════════════════ */
KB.define('health', (api, opts) => {
  const FOLD = (opts && opts.fold) || 25;       // 每组超过这个数就折叠
  let panel = null;

  async function compute() {
    const [graph, pages, tags] = await Promise.all([
      api.data.getGraph(),
      api.data.jfetch(api.API + '/pages?limit=500'),
      api.data.getTags().catch(() => []),
    ]);
    const lower = (s) => String(s).toLowerCase();
    const nodeSet = new Set(graph.nodes.map(n => lower(n.name)));
    const updated = {};
    for (const p of pages) updated[lower(p.name)] = p.updated_at;

    // 孤立页：零入链（自链不算救活）
    const inbound = {};
    for (const e of graph.edges) {
      if (lower(e.source) === lower(e.target)) continue;
      inbound[lower(e.target)] = (inbound[lower(e.target)] || 0) + 1;
    }
    const orphans = graph.nodes
      .filter(n => !inbound[lower(n.name)])
      .map(n => ({ name: n.name, updated_at: updated[lower(n.name)] || '' }))
      .sort((a, b) => (a.updated_at < b.updated_at ? -1 : 1)); // 最旧的孤岛排前

    // 断链：目标不存在，按被引用次数排序（修复收益最高的排前）
    const brokenMap = {};
    for (const e of graph.edges) {
      if (nodeSet.has(lower(e.target))) continue;
      const k = lower(e.target);
      (brokenMap[k] = brokenMap[k] || { target: e.target, sources: [] }).sources.push(e.source);
    }
    const broken = Object.values(brokenMap).sort((a, b) => b.sources.length - a.sources.length);

    // 统计
    const weekAgo = Date.now() - 7 * 864e5;
    const active7 = pages.filter(p => new Date(String(p.updated_at).replace(' ', 'T')).getTime() >= weekAgo).length;
    const links = graph.edges.filter(e => lower(e.source) !== lower(e.target)).length;
    return {
      orphans, broken,
      stats: { pages: graph.nodes.length, links, active7, topTags: (tags || []).slice(0, 8) },
    };
  }

  /* 一组：标题 + 条目。超过 FOLD 条折叠，点「展开全部」只展开这一组
     （旧版两组都展开 = 447 条直下，又回到 22880px 的老问题）。 */
  function section(group, title, items, rowHtml, expanded) {
    const head = `<div class="hp-group-head">${title}（${items.length}）</div>`;
    if (!items.length) return head + '<div class="hp-empty">无</div>';
    const shown = expanded ? items : items.slice(0, FOLD);
    let html = head + shown.map(rowHtml).join('');
    if (!expanded && items.length > FOLD) {
      html += `<button class="hp-more" data-more="${group}">展开全部 ${items.length} 条</button>`;
    }
    return html;
  }

  function render(h, expanded) {
    const esc = api.ui.escapeHtml;
    const rowOrphan = (o) => `<div class="hp-item" data-page="${esc(o.name)}">
        <span class="hp-name">${esc(o.name)}</span></div>`;
    const rowBroken = (b) => `<div class="hp-item">
        <span class="hp-name">${esc(b.target)}</span>
        <button class="hp-create" data-create="${esc(b.target)}">创建此页</button>
        <div class="hp-src">被引用 ${b.sources.length} 次 ← ${[...new Set(b.sources)].slice(0, 3).map(s => esc(s)).join('、')}${b.sources.length > 3 ? ' 等' : ''}</div>
      </div>`;
    return `
      <div class="hp-overview">${h.stats.pages} 页 · ${h.stats.links} 条链接 · 近 7 天活跃 ${h.stats.active7} 页</div>
      ${h.stats.topTags.length ? `<div class="hp-tags">${h.stats.topTags.map(t =>
        `<span class="hp-tag" data-tag="${esc(t.name)}">#${esc(t.name)} ×${t.count}</span>`).join('')}</div>` : ''}
      <div class="hp-body">
        ${section('orphans', '孤立页 —— 没有任何页面链接到它们', h.orphans, rowOrphan, expanded.orphans)}
        ${section('broken', '断链 —— 指向不存在的页面', h.broken, rowBroken, expanded.broken)}
      </div>`;
  }

  function bind(h, expanded, paint) {
    const body = panel.body;
    body.querySelectorAll('.hp-more').forEach(btn => {
      btn.addEventListener('click', () => {
        expanded[btn.dataset.more] = true;    // 只展开这一组
        paint();
      });
    });
    body.querySelectorAll('[data-page]').forEach(el => {
      el.addEventListener('click', (ev) => {
        if (ev.target.closest('[data-create]')) return;
        api.ui.navigate('page', el.dataset.page);
      });
    });
    body.querySelectorAll('[data-create]').forEach(btn => {
      btn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        api.ui.navigate('edit', btn.dataset.create);   // 缺失页 → 直接进编辑器
      });
    });
    body.querySelectorAll('[data-tag]').forEach(el => {
      el.addEventListener('click', () => api.ui.navigate('search', '#' + el.dataset.tag));
    });
  }

  async function refresh() {
    panel.body.innerHTML = '<div class="hp-loading">体检中…</div>';
    try {
      const h = await compute();
      const expanded = { orphans: false, broken: false };
      const paint = () => { panel.body.innerHTML = render(h, expanded); bind(h, expanded, paint); };
      paint();
    } catch (e) {
      panel.body.innerHTML = `<div class="hp-loading">体检失败：${api.ui.escapeHtml(e.message)}</div>`;
    }
  }

  /* :health 是 toggle —— 再按一次收起（dock 面板不受 Esc 影响，给一个明确出口）。
   * 已打开时再按会重新体检，数据是最新的。 */
  function toggle() {
    if (panel && panel.isOpen()) { panel.close(); return; }
    if (!panel) {
      panel = api.ui.panel({
        id: 'kb-health', mode: 'dock', side: 'right', width: 340, title: '体检',
      });
    }
    panel.open();
    refresh();
  }

  api.css('plugins/health.css');
  api.command('health', '知识库体检（孤立页/断链）', toggle);
  api.keys([{ lhs: 'uh', desc: '知识库体检', run: toggle }]);
});
