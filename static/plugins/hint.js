/* ══════════════════════════════════════════════════════════════════════
   插件：hint —— [[引用]] / #标签 补全
   ─────────────────────────────────────────────────────────────────────
   这正是你说的「引用补全如果是可以支持的，就一样是一个插件去适配
   CodeMirror 的补全功能」：CodeMirror 5 官方有 show-hint addon，
   插件只负责算候选（从页面名表 / 标签表 fuzzy 匹配），
   完全不碰编辑器本身的按键处理。

   触发：在编辑器里输入 `[[` 后自动弹；`space u H` 手动触发。
   ══════════════════════════════════════════════════════════════════════ */
KB.define('hint', (api, opts) => {
  let cm = null;
  let installing = false;

  function matchFuzzy(q, text) {
    if (!q) return 1;
    const ql = q.toLowerCase(), t = text.toLowerCase();
    const idx = t.indexOf(ql);
    if (idx >= 0) return 1000 - idx;
    let qi = 0, score = 0;
    for (let ti = 0; ti < t.length && qi < ql.length; ti++) {
      if (t[ti] === ql[qi]) { qi++; score++; }
    }
    return qi === ql.length ? score : -1;
  }

  /* 找光标前面是不是 [[ 或 #，决定候选类型和替换范围 */
  function contextOf(c) {
    const cur = c.getCursor();
    const line = c.getLine(cur.line).slice(0, cur.ch);
    const openBracket = line.lastIndexOf('[[');
    if (openBracket >= 0 && line.indexOf(']]', openBracket) === -1) {
      return { kind: 'page', start: openBracket + 2, text: line.slice(openBracket + 2) };
    }
    const hash = line.lastIndexOf('#');
    if (hash >= 0 && /\s/.test(line.slice(hash + 1))) {
      const rest = line.slice(hash + 1);
      if (/^[A-Za-z0-9_\-]*$/.test(rest)) return { kind: 'tag', start: hash + 1, text: rest };
    }
    return null;
  }

  function complete(c) {
    const ctx = contextOf(c);
    if (!ctx) {
      api.ui.toast('把光标放在 [[ 或 # 之后', 'warn');
      return;
    }

    const finish = (list) => {
      // show-hint 依赖编辑器有焦点（widget 用 focus 事件定位），先确保聚焦
      try { if (!c.hasFocus()) c.focus(); } catch (e) {}
      /* show-hint 的官方契约：hint(editor) 返回
       *   { list: [候选…], from: {line,ch}, to: {line,ch} }
       * 选中后 show-hint 自己用 list[i].text 替换 from..to。
       * 所以我们把「补 [[ 的右括号」写进候选的 text，而不是靠回调。 */
      CodeMirror.showHint(c, function () {
        const cur = c.getCursor();
        const line = c.getLine(cur.line).slice(0, cur.ch);
        const q = line.slice(ctx.start);
        const items = list
          .map(x => ({ text: x, score: matchFuzzy(q, x) }))
          .filter(x => x.score > 0)
          .sort((a, b) => b.score - a.score)
          .slice(0, 40)
          .map(x => ({
            text: ctx.kind === 'page' ? x.text + ']]' : x.text,
            displayText: x.text,
          }));
        return {
          list: items,
          from: { line: cur.line, ch: ctx.start },
          to: cur,
        };
      }, { completeSingle: false, alignWithWord: false });
    };

    if (ctx.kind === 'page') {
      api.data.getNames().then(names => finish(names)).catch(err => api.ui.toast('页面名读取失败：' + err.message, 'error'));
    } else {
      api.data.getTags().then(tags => finish(tags.map(t => (typeof t === 'string' ? t : (t.name || t.tag))))).catch(() => {});
    }
  }

  function attach(c) {
    if (!c || c.__kbHint) return;
    c.__kbHint = true;
    installing = true;

    /* 输入 [[ 自动弹（对应 LazyVim 的 cmp 在 markdown 里给链接补全）*/
    c.on('inputRead', (instance, change) => {
      if (change.origin !== '+input') return;
      const cur = instance.getCursor();
      const line = instance.getLine(cur.line).slice(0, cur.ch);
      if (line.endsWith('[[') || line.endsWith('#') || /\#[\w-]+$/.test(line)) {
        setTimeout(() => { try { complete(instance); } catch (e) {} }, 60);
      }
    });

    installing = false;
  }

  api.css('plugins/hint.css');

  /* 编辑器一出现就装上（事件由 app.js 在 initCM 后触发）*/
  api.on('editor', (payload) => {
    cm = (payload && payload.cm) || api.cm;
    if (!cm) return;
    attach(cm);
  });

  api.keys([
    { lhs: 'uH', desc: '手动触发 [[ 补全', run: () => { if (api.cm) complete(api.cm); } },
  ]);

  api.command('hint', '手动触发 [[ 补全', () => { if (api.cm) complete(api.cm); else api.ui.toast('编辑器未打开'); });
});
