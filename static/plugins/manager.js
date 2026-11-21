/* ══════════════════════════════════════════════════════════════════════
   插件：manager —— `:Plugins` 面板
   ─────────────────────────────────────────────────────────────────────
   相当于 :Lazy。列出所有插件、显示 lazy 触发条件、可现场开关。
   开关写进 localStorage（kb-plugins-disabled），不重启即时生效。
   ══════════════════════════════════════════════════════════════════════ */
KB.define('manager', (api) => {
  let panel = null;

  function render(root) {
    const list = api.list ? api.list() : window.KB.list();
    const wrap = document.createElement('div');
    wrap.className = 'pl-manager';

    const head = document.createElement('div');
    head.className = 'plm-head';
    head.innerHTML = `<span>${list.length} 个插件 · 关闭的写进 localStorage</span>`;
    wrap.appendChild(head);

    list.forEach(p => {
      const row = document.createElement('div');
      row.className = 'plm-row' + (p.enabled ? '' : ' off');

      const name = document.createElement('div');
      name.className = 'plm-name';
      name.textContent = p.name;
      if (p.lazy) {
        const tagEl = document.createElement('span');
        tagEl.className = 'plm-tag';
        tagEl.textContent = 'lazy';
        name.appendChild(tagEl);
      }
      if (p.loaded) {
        const tagEl = document.createElement('span');
        tagEl.className = 'plm-tag on';
        tagEl.textContent = 'loaded';
        name.appendChild(tagEl);
      }

      const desc = document.createElement('div');
      desc.className = 'plm-desc';
      desc.textContent = p.desc || '';

      const trig = document.createElement('div');
      trig.className = 'plm-trig';
      const t = p.triggers;
      trig.textContent = t
        ? ['keys:' + (t.keys || []).join(','), 'cmd:' + (t.cmd || []).join(','), 'event:' + (t.event || []).join(',')]
            .filter(s => s.indexOf(':') !== s.length - 1)
            .join('  ')
        : (p.lazy ? '（未挂触发）' : '启动即载');

      const sw = document.createElement('button');
      sw.className = 'plm-sw' + (p.enabled ? ' on' : '');
      sw.textContent = p.enabled ? '开' : '关';
      sw.addEventListener('click', () => {
        window.KB.setEnabled(p.name, !p.enabled);
        render(root);
        api.ui.toast(p.name + (p.enabled ? ' 已关闭' : ' 已启用'));
      });

      row.appendChild(name);
      row.appendChild(desc);
      row.appendChild(trig);
      row.appendChild(sw);
      wrap.appendChild(row);
    });

    root.innerHTML = '';
    root.appendChild(wrap);
  }

  api.css('plugins/manager.css');

  api.command('Plugins', '插件管理面板（同 :Lazy）', () => {
    if (!panel) {
      panel = api.ui.panel({
        id: 'kb-plugins',
        mode: 'overlay',
        title: '插件',
        render: (body) => render(body),
      });
    }
    panel.open();
    render(panel.body);
  });

  api.keys([{ lhs: 'P', desc: '插件管理 (:Plugins)', run: () => window.runCommand('Plugins', []) }]);
});
