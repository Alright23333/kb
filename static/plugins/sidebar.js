/* ══════════════════════════════════════════════════════════════════════
   插件：sidebar —— 侧栏导航
   ─────────────────────────────────────────────────────────────────────
   lazygit 在 nvim 里必须开终端，是因为 nvim 只有终端能塞富内容。
   网页没有这个限制，所以这里用 dock 面板：贴左边、可拖宽、读 KB API。
   面板内容比 TUI 能做的多：标签带计数、最近更新、统计、快捷入口。
   ══════════════════════════════════════════════════════════════════════ */
KB.define('sidebar', (api, opts) => {
  const W = (opts && opts.width) || 248;
  const SIDE = (opts && opts.side) || 'left';
  const STORE = 'kb-sidebar-open';

  let panel = null;
  let loading = false;

  function el(tag, cls, txt) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (txt !== undefined) e.textContent = txt;
    return e;
  }

  function section(title) {
    const wrap = el('div', 'sb-sec');
    const h = el('div', 'sb-sec-h', title);
    wrap.appendChild(h);
    const body = el('div', 'sb-sec-b');
    wrap.appendChild(body);
    return { wrap, body };
  }

  async function render(root) {
    root.innerHTML = '';
    const inner = el('div', 'sb-wrap');

    // ── 快捷入口 ──
    const nav = section('快捷');
    [
      ['首页', () => api.ui.navigate('home')],
      ['所有页面', () => api.ui.navigate('all')],
      ['任务', () => api.ui.navigate('todo')],
      ['知识图谱', () => api.ui.navigate('graph')],
      ['标签云', () => api.ui.navigate('tags')],
      ['日记', () => api.ui.navigate('journal')],
    ].forEach(([label, fn]) => {
      const b = el('button', 'sb-item', label);
      b.addEventListener('click', fn);
      nav.body.appendChild(b);
    });
    inner.appendChild(nav.wrap);

    // ── 统计 ──
    const stat = section('统计');
    stat.body.innerHTML = '<div class="sb-dim">加载中…</div>';
    inner.appendChild(stat.wrap);

    // ── 标签 ──
    const tags = section('标签');
    tags.body.innerHTML = '<div class="sb-dim">加载中…</div>';
    inner.appendChild(tags.wrap);

    // ── 最近 ──
    const recent = section('最近更新');
    recent.body.innerHTML = '<div class="sb-dim">加载中…</div>';
    inner.appendChild(recent.wrap);

    root.appendChild(inner);

    // 并发拉数据，失败不阻塞面板
    try {
      const [count, tagList, pages] = await Promise.all([
        api.data.jfetch(api.API + '/pages/count').catch(() => ({ count: '?' })),
        api.data.getTags().catch(() => []),
        api.data.jfetch(api.API + '/pages?limit=8').catch(() => []),
      ]);
      stat.body.innerHTML = '';
      const kv = (k, v) => {
        const row = el('div', 'sb-kv');
        row.appendChild(el('span', 'sb-k', k));
        row.appendChild(el('span', 'sb-v', String(v)));
        stat.body.appendChild(row);
      };
      kv('页面', count.count !== undefined ? count.count : count);
      kv('标签', (tagList || []).length);
      if (pages && pages.length) kv('最新', pages[0].updated_at || '');

      tags.body.innerHTML = '';
      (tagList || []).slice(0, 18).forEach(t => {
        const name = typeof t === 'string' ? t : (t.name || t.tag);
        const n = typeof t === 'string' ? '' : (t.count !== undefined ? t.count : '');
        const b = el('button', 'sb-tag', '#' + name);
        if (n !== '') b.appendChild(el('span', 'sb-tag-n', String(n)));
        b.addEventListener('click', () => api.ui.navigate('tag', name));
        tags.body.appendChild(b);
      });
      if (!(tagList || []).length) tags.body.appendChild(el('div', 'sb-dim', '没有标签'));

      recent.body.innerHTML = '';
      (pages || []).forEach(p => {
        const b = el('button', 'sb-item sb-recent', p.name);
        b.title = p.name;
        b.addEventListener('click', () => api.ui.navigate('page', p.name));
        recent.body.appendChild(b);
      });
    } catch (e) {
      stat.body.innerHTML = '<div class="sb-dim">读取失败：' + api.ui.escapeHtml(e.message) + '</div>';
    }
  }

  function ensurePanel() {
    if (panel) return panel;
    panel = api.ui.panel({
      id: 'kb-sidebar',
      mode: 'dock',
      side: SIDE,
      width: W,
      title: '导航',
      render: (root) => render(root),
      onMount: () => {
        localStorage.setItem(STORE, '1');
        const btn = document.querySelector('#kbp-sidebar .kbp-head .kbp-title');
        if (btn) btn.textContent = '导航';
      },
      onUnmount: () => localStorage.setItem(STORE, '0'),
    });
    return panel;
  }

  /* 自己记「用户想要的可见状态」，别用 panel.isOpen() 反推：
   * dock 面板 mount 后默认就是打开状态，反推会立刻把它关掉。 */
  let wanted = false;

  function toggle(force) {
    const p = ensurePanel();
    wanted = force === undefined ? !wanted : !!force;
    if (wanted) {
      p.open();
      if (!loading) { loading = true; render(p.body).finally(() => { loading = false; }); }
    } else {
      p.close();
    }
    return p;
  }

  api.css('plugins/sidebar.css');

  api.command('sidebar', '侧栏导航开关', () => toggle());

  /* 首屏恢复上次状态（lazy 的代价是启动慢一点，但这点很轻） */
  api.on('boot', () => {
    if (localStorage.getItem(STORE) === '1') toggle(true);
  });

  /* 页面切换时，如果侧栏开着就轻量刷新「最近」 */
  api.on('navigate', () => {
    if (panel && panel.isOpen()) {
      const recent = panel.body.querySelector('.sb-sec:last-child .sb-sec-b');
      if (recent) {
        api.data.jfetch(api.API + '/pages?limit=8').then(list => {
          recent.innerHTML = '';
          (list || []).forEach(p => {
            const b = el('button', 'sb-item sb-recent', p.name);
            b.title = p.name;
            b.addEventListener('click', () => api.ui.navigate('page', p.name));
            recent.appendChild(b);
          });
        }).catch(() => {});
      }
    }
  });
});
