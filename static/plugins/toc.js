/* ══════════════════════════════════════════════════════════════════════
   插件：toc —— 页面目录
   ─────────────────────────────────────────────────────────────────────
   读已渲染的 h1/h2/h3，右侧浮一个小目录（overlay 面板，可收起）。
   标题少于 opts.minHeadings 个就不显示，避免短文也挂个目录。
   ══════════════════════════════════════════════════════════════════════ */
KB.define('toc', (api, opts) => {
  const MIN = (opts && opts.minHeadings) || 3;
  let panel = null;

  function build() {
    const view = api.ui.view();
    if (!view) return null;
    const heads = [...view.querySelectorAll('.doc h1, .doc h2, .doc h3, .doc h4')];
    if (heads.length < MIN) return null;
    heads.forEach((h, i) => { if (!h.id) h.id = 'kb-h-' + i; });
    const root = document.createElement('div');
    root.className = 'toc-list';
    heads.forEach((h) => {
      const b = document.createElement('button');
      b.className = 'toc-item toc-' + h.tagName.toLowerCase();
      b.textContent = h.textContent.replace(/^#+\s*/, '').trim().slice(0, 60);
      b.addEventListener('click', () => {
        h.scrollIntoView({ behavior: 'smooth', block: 'start' });
        b.blur();
      });
      root.appendChild(b);
    });
    return root;
  }

  function refresh() {
    const list = build();
    if (!list) { if (panel) panel.close(); return; }
    if (!panel) {
      panel = api.ui.panel({
        id: 'kb-toc',
        mode: 'overlay',
        title: '目录',
        render: (body) => { body.appendChild(build() || document.createTextNode('')); },
      });
      // 目录用浮层太打扰，改成右侧窄浮块
      panel.el.style.width = 'min(80vw, 240px)';
      panel.el.style.top = '8vh';
      panel.el.style.bottom = 'auto';
      panel.el.style.maxHeight = '60vh';
      panel.el.style.right = '1.2em';
      panel.el.style.left = 'auto';
      panel.el.style.transform = 'none';
    }
    panel.body.innerHTML = '';
    panel.body.appendChild(list);
    panel.open();
  }

  api.css('plugins/toc.css');
  api.command('toc', '目录开关', () => {
    if (panel && panel.isOpen()) return panel.close();
    refresh();
  });
  api.keys([{ lhs: 'uT', desc: '目录开关', run: () => { if (panel && panel.isOpen()) panel.close(); else refresh(); } }]);

  api.on('page', () => setTimeout(refresh, 500));
});
