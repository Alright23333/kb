/* ══════════════════════════════════════════════════════════════════════
   插件：toc —— 页面目录
   ─────────────────────────────────────────────────────────────────────
   读已渲染的 h1/h2/h3，右侧浮一个小目录（overlay 面板，可收起）。
   标题少于 opts.minHeadings 个就不显示，避免短文也挂个目录。
   ══════════════════════════════════════════════════════════════════════ */
KB.define('toc', (api, opts) => {
  const MIN = (opts && opts.minHeadings) || 3;
  /* 关掉之后要记住 —— 否则每切一页 `api.on('page')` 又把它弹出来，
   * 用户在一个本来就不需要目录的页面上得反复关同一个东西。
   * 浏览器里「用户手动关掉的东西不该自己回来」是一条基本约定。 */
  const OFF_KEY = 'kb-toc-off';
  const isOff = () => { try { return localStorage.getItem(OFF_KEY) === '1'; } catch (e) { return false; } };
  const setOff = (v) => { try { v ? localStorage.setItem(OFF_KEY, '1') : localStorage.removeItem(OFF_KEY); } catch (e) { } };
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
    if (isOff()) return;                 // 用户明确关过，就别再弹
    const list = build();
    if (!list) { if (panel) panel.close(); return; }
    if (!panel) {
      panel = api.ui.panel({
        id: 'kb-toc',
        mode: 'overlay',
        /* 目录是浮在页角的小块，不是模态：有 scrim 会把整页点击吃光，
         * 正文里的链接和按钮全都点不动。 */
        scrim: false,
        title: '目录',
        render: (body) => { body.appendChild(build() || document.createTextNode('')); },
      });
      // 目录用浮层太打扰，改成右侧窄浮块
      panel.el.style.width = 'min(80vw, 240px)';
      panel.el.style.top = '8vh';
      panel.el.style.bottom = 'auto';
      panel.el.style.maxHeight = '60vh';
      /* 让开右侧 dock（如体检面板）：--kbp-dock-w 在 dock 关闭时归 0 */
      panel.el.style.right = 'calc(var(--kbp-dock-w, 0px) + 1.2em)';
      panel.el.style.left = 'auto';
      panel.el.style.transform = 'none';
      /* 点面板上的 × 也算「用户关掉了」—— 只在 close 时记，open 时不记，
       * 免得把「用命令打开」也当成关闭。 */
      const origClose = panel.close.bind(panel);
      panel.close = function () { setOff(true); return origClose(); };
    }
    panel.body.innerHTML = '';
    panel.body.appendChild(list);
    panel.open();
  }

  api.css('plugins/toc.css');
  /* 命令式开关是**显式用户意图**：关的时候记下来，开的时候清掉记忆。 */
  api.command('toc', '目录开关', () => {
    if (panel && panel.isOpen()) { setOff(true); return panel.close(); }
    setOff(false);
    refresh();
  });
  api.keys([{ lhs: 'uT', desc: '目录开关', run: () => {
    if (panel && panel.isOpen()) { setOff(true); panel.close(); } else { setOff(false); refresh(); }
  } }]);

  api.on('page', () => setTimeout(refresh, 500));
});
