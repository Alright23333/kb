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

  /* 中文输入法「全角标点」模式下按 `[` 得到的是 `［`(U+FF3B) 或 `【`(U+3010)，
   * 按 `]` 得到 `］`/`】`。用户报的原话是「两个中文【也可以打开页面引用」——
   * 在中文输入法里给 [[开始补全几乎是必须的，否则每次都要先切半角。
   *
   * 归一化只用于**判定**：全角↔半角是 1 字符对应 1 字符，下标不会错位，
   * 所以 start / q 的下标可以直接用在实际文本上；替换时 show-hint 从
   * from 到 to 覆盖的正是用户敲下的那几个全角字符，结果同样是正常的
   * `[[名字]]`（因为我们把右括号也一并写进候选 text）。 */
  const FW_MAP = { '\uFF3B': '[', '\u3010': '[', '\uFF3D': ']', '\u3011': ']' };
  function fwNorm(str) {
    return str.replace(/[\uFF3B\u3010\uFF3D\u3011]/g, ch => FW_MAP[ch] || ch);
  }

  /* 找光标前面是不是 [[ 或 #，决定候选类型和替换范围。
   *
   * 只看**光标之前**这一段：closebrackets 插件在输入 `[` 时就把 `[]` 补好了，
   * 打完第二个 `[` 文档里已经是 `[[]]`（光标停在两个 `]` 之间）。
   * 所以「整行里没有 ]]」这种判定永远失败 —— 原来的补全看起来「有时候能弹、
   * 弹出来也没有候选」就是这个原因。 */
  function contextOf(c) {
    const cur = c.getCursor();
    const raw = c.getLine(cur.line).slice(0, cur.ch);
    const before = fwNorm(raw);              // 仅判定用，下标与 raw 一一对应
    const openBracket = before.lastIndexOf('[[');
    if (openBracket >= 0 && before.slice(openBracket + 2).indexOf(']]') === -1) {
      return { kind: 'page', start: openBracket + 2, text: before.slice(openBracket + 2) };
    }
    // #标签：前面不能是 #（那是标题）或字母数字；# 之后只能是标签字符。
    // 原来的判定是 /\s/.test(...) —— 要求 # 后面有空白，等于永远不匹配。
    const m = /(^|[^\p{L}\p{N}_#])#([\p{L}\p{N}_-]*)$/u.exec(before);
    if (m) return { kind: 'tag', start: cur.ch - m[2].length, text: m[2] };
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
        const before = fwNorm(c.getLine(cur.line).slice(0, cur.ch));
        const q = before.slice(ctx.start);
        // closebrackets 已经把 ]] 补好了：光标后面若已经是 ]]（全角也认），
        // 候选只放名字，否则会变成 [[名字]]]]。
        const alreadyClosed = ctx.kind === 'page' &&
                              fwNorm(c.getLine(cur.line).slice(cur.ch, cur.ch + 2)) === ']]';
        const items = list
          .map(x => ({ text: x, score: matchFuzzy(q, x) }))
          .filter(x => x.score > 0)
          .sort((a, b) => b.score - a.score)
          .slice(0, 40)
          .map(x => ({
            text: (ctx.kind === 'page' && !alreadyClosed) ? x.text + ']]' : x.text,
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

    /* 输入 [[ 自动弹（对应 LazyVim 的 cmp 在 markdown 里给链接补全）
     *
     * 只挂 inputRead 是不够的：inputRead 只在文本经由隐藏 textarea 进来时
     * 才触发（输入法组字、粘贴）。直接键入走的是 CodeMirror 的
     * replaceSelection，只会发 change（origin = '+input'）。
     * 结果就是「有时候能弹（用输入法打中文时）、有时候不弹（直接敲 ASCII）」。
     * 两个事件都挂上，并去重。
     */
    function maybeComplete(instance, change) {
      if (!change || change.origin !== '+input') return;
      const cur = instance.getCursor();
      /* 全角 `［［` / `【【` 也要自动弹（中文输入法常态）。见上面 fwNorm 的说明。 */
      const before = fwNorm(instance.getLine(cur.line).slice(0, cur.ch));
      const wantPopup = before.slice(-2) === '[[' ||
                        /(^|[^\p{L}\p{N}_#])#[\p{L}\p{N}_-]*$/u.test(before);
      if (!wantPopup) return;
      if (instance.__kbHintTimer) clearTimeout(instance.__kbHintTimer);
      instance.__kbHintTimer = setTimeout(() => {
        instance.__kbHintTimer = null;
        // 候选框已经开着：交给 show-hint 自己更新，别重开一个
        if (document.querySelector('.CodeMirror-hints')) return;
        try { complete(instance); } catch (e) {}
      }, 80);
    }

    c.on('change', maybeComplete);      // 直接键入
    c.on('inputRead', maybeComplete);   // 输入法 / 粘贴

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
