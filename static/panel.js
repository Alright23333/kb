/* ══════════════════════════════════════════════════════════════════════
   KBPanel —— 插件用的富面板容器
   ─────────────────────────────────────────────────────────────────────
   lazygit 在 nvim 里的做法是「开个终端跑 TUI」，因为终端是 nvim 里
   唯一能塞进富内容的地方。网页没有这个限制，所以这里的等价物不是
   伪终端，而是真正的 DOM 面板：

     · 支持任意 HTML / canvas / iframe
     · overlay（盖住主视图）/ dock（贴边挤窄主视图）/ full
     · 标题栏、关闭键、可拖拽宽度、Esc 关闭
     · 生命周期 onMount / onUnmount / onResize

   插件只拿到 api.ui.panel(opts) 这一个入口，不碰全局 DOM，
   所以卸载插件时面板一起消失，不留垃圾节点。

    用法（插件内）：
      const dock = api.ui.panel({
        id: 'sidebar', mode: 'dock', side: 'left', width: 240,
        title: '导航',
        render: (root, ctx) => { ... },
      });
      dock.toggle();  dock.close();  dock.setWidth(300);
   ══════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  const panels = new Map();   // id -> handle

  const STYLE_ID = 'kb-panel-style';
  function ensureStyle() {
    if (document.getElementById(STYLE_ID)) return;
    const st = document.createElement('style');
    st.id = STYLE_ID;
    st.textContent = `
/* ── KBPanel 基础样式（全用主题变量，自动跟随 data-scheme）── */
.kbp {
  background: var(--bg-deep); color: var(--fg);
  border: 1px solid var(--line);
  display: flex; flex-direction: column; min-height: 0; min-width: 0;
  font-family: var(--mono); font-size: 13px;
}
.kbp-head {
  display: flex; align-items: center; gap: .6em; flex: 0 0 auto;
  padding: .38em .7em; border-bottom: 1px solid var(--line-soft);
  font-size: 12px; color: var(--muted); user-select: none;
}
.kbp-title { color: var(--fg); font-weight: 600; }
.kbp-head .spacer { flex: 1 1 auto; }
.kbp-x {
  background: none; border: 0; color: var(--faint); cursor: pointer;
  font-size: 15px; line-height: 1; padding: 0 .25em; font-family: var(--mono);
}
.kbp-x:hover { color: var(--err); }
.kbp-body { flex: 1 1 auto; overflow: auto; min-height: 0; position: relative; }

/* overlay：居中浮层 */
.kbp-overlay {
  position: fixed; z-index: 90;
  top: 5vh; bottom: 5vh; left: 50%; transform: translateX(-50%);
  width: min(94vw, 920px); border-radius: var(--r-md);
  box-shadow: 0 24px 70px rgba(0,0,0,.55);
}
.kbp-scrim {
  position: fixed; inset: 0; z-index: 89;
  background: rgba(0,0,0,.38); backdrop-filter: blur(1.5px);
}

/* dock：贴边，用 padding 把主视图挤窄而不是盖住 */
.kbp-dock {
  position: fixed; top: 0; width: 240px; z-index: 40;
  border-radius: 0; border-top: 0; border-bottom: 0;
}
.kbp-dock.left { left: 0; border-left: 0; }
.kbp-dock.right { right: 0; border-right: 0; }
.kbp-resize {
  position: absolute; top: 0; bottom: 0; width: 6px; cursor: col-resize;
}
.kbp-dock.left .kbp-resize { right: -3px; }
.kbp-dock.right .kbp-resize { left: -3px; }

/* dock 打开时给主视图留位置（tabline/statusline 也一起缩进） */
body.kbp-dock-left #tabline, body.kbp-dock-left #view, body.kbp-dock-left #editor, body.kbp-dock-left #statusline, body.kbp-dock-left #cmdline { margin-left: var(--kbp-dock-w, 0px); }
body.kbp-dock-right #tabline, body.kbp-dock-right #view, body.kbp-dock-right #editor, body.kbp-dock-right #statusline, body.kbp-dock-right #cmdline { margin-right: var(--kbp-dock-w, 0px); }

@media (max-width: 760px) {
  .kbp-overlay { top: 0; bottom: 0; left: 0; right: 0; width: 100%; transform: none; border-radius: 0; }
  .kbp-dock { width: 84% !important; }
}
`;
    document.head.appendChild(st);
  }

  function mount(opts) {
    ensureStyle();
    const id = opts.id || ('kbp-' + Math.random().toString(36).slice(2, 8));
    if (panels.has(id)) return panels.get(id);

    const mode = opts.mode || 'overlay';
    const side = opts.side || 'left';
    const el = document.createElement('div');
    el.className = 'kbp kbp-' + mode + (mode === 'dock' ? ' ' + side : '') + (opts.className ? ' ' + opts.className : '');
    el.id = id;              // 让 #id 选择器（插件自己与调试）都能找到
    el.dataset.kbpId = id;

    const head = document.createElement('div');
    head.className = 'kbp-head';
    head.innerHTML = `<span class="kbp-title"></span><span class="spacer"></span>
      <button class="kbp-x" title="关闭 (Esc)">×</button>`;
    head.querySelector('.kbp-title').textContent = opts.title || '';

    const body = document.createElement('div');
    body.className = 'kbp-body';

    el.appendChild(head);
    el.appendChild(body);

    let scrim = null;
    if (mode === 'overlay') {
      scrim = document.createElement('div');
      scrim.className = 'kbp-scrim';
      document.body.appendChild(scrim);
      scrim.addEventListener('click', () => handle.close());
      scrim.style.display = 'none';
    }

    document.body.appendChild(el);

    if (mode === 'dock') {
      const w = opts.width || 240;
      el.style.width = w + 'px';
      el.style.top = (opts.top || 0) + 'px';
      el.style.bottom = (opts.bottom === undefined ? 26 : opts.bottom) + 'px';

      if (opts.resizable !== false) {
        const grip = document.createElement('div');
        grip.className = 'kbp-resize';
        el.appendChild(grip);
        let startX = 0, startW = 0, dragging = false;
        const onMove = (ev) => {
          if (!dragging) return;
          const dx = (side === 'right') ? (startX - ev.clientX) : (ev.clientX - startX);
          const nw = Math.max(160, Math.min(560, startW + dx));
          el.style.width = nw + 'px';
          document.body.style.setProperty('--kbp-dock-w', nw + 'px');
        };
        const onUp = () => {
          dragging = false;
          document.removeEventListener('mousemove', onMove);
          document.removeEventListener('mouseup', onUp);
          if (opts.onResize) opts.onResize(parseInt(el.style.width, 10), ctx);
        };
        grip.addEventListener('mousedown', (ev) => {
          ev.preventDefault();
          dragging = true; startX = ev.clientX; startW = parseInt(el.style.width, 10) || 240;
          document.addEventListener('mousemove', onMove);
          document.addEventListener('mouseup', onUp);
        });
      }
    }

    const ctx = {
      id, mode, side, opts,
      get el() { return el; },
      get body() { return body; },
      get head() { return head; },
    };

    let mounted = false;
    const handle = {
      id, mode, el, body, ctx,
      isOpen() { return el.style.display !== 'none'; },
      open() {
        el.style.display = '';
        if (scrim) scrim.style.display = '';
        if (mode === 'dock') {
          document.body.classList.add('kbp-dock-' + side);
          document.body.style.setProperty('--kbp-dock-w', el.style.width);
        }
        if (!mounted) {
          mounted = true;
          if (opts.render) {
            try { opts.render(body, ctx); }
            catch (e) { body.innerHTML = '<div style="padding:1em;color:var(--err)">' + e.message + '</div>'; }
          }
        }
        if (opts.onMount) opts.onMount(body, ctx);
        return handle;
      },
      close() {
        el.style.display = 'none';
        if (scrim) scrim.style.display = 'none';
        document.body.classList.remove('kbp-dock-' + side);
        if (mode === 'dock' && !document.querySelector('.kbp-dock:not([style*="display: none"])')) {
          document.body.style.setProperty('--kbp-dock-w', '0px');
        }
        if (opts.onUnmount) opts.onUnmount(body, ctx);
        return handle;
      },
      toggle() { return handle.isOpen() ? handle.close() : handle.open(); },
      setWidth(w) {
        el.style.width = w + 'px';
        if (handle.isOpen()) document.body.style.setProperty('--kbp-dock-w', w + 'px');
        if (opts.onResize) opts.onResize(w, ctx);
      },
      setTitle(t) { head.querySelector('.kbp-title').textContent = t; },
      destroy() {
        if (mounted && opts.onUnmount) opts.onUnmount(body, ctx);
        if (scrim) scrim.remove();
        el.remove();
        document.body.classList.remove('kbp-dock-' + side);
        document.body.style.setProperty('--kbp-dock-w', '0px');
        panels.delete(id);
      },
    };

    head.querySelector('.kbp-x').addEventListener('click', () => handle.close());

    /* Esc 关闭最上层的 panel */
    if (!window.__KBPANEL_ESC__) {
      window.__KBPANEL_ESC__ = true;
      document.addEventListener('keydown', (ev) => {
        if (ev.key !== 'Escape') return;
        const open = [...panels.values()].filter(p => p.isOpen() && p.mode === 'overlay');
        if (!open.length) return;
        ev.stopPropagation();
        open[open.length - 1].close();
      }, true);
    }

    panels.set(id, handle);
    if (mode === 'dock') handle.open();
    return handle;
  }

  window.KBPanel = { mount, panels, ensureStyle };
})();
