/* ══════════════════════════════════════════════════════════════════════
   KB 插件内核 —— 照 lazy.nvim 的 plugin spec 模型
   ─────────────────────────────────────────────────────────────────────
   为什么要有这一层：侧栏、甘特图、补全、主题这些东西的共同点是
   「可以整块加/整块删，且不该改主干代码」。这正是 lazy.nvim 的 spec
   要解决的问题，所以直接抄它的字段语义，只把触发条件翻译成浏览器事件。

   ┌─ lazy.nvim spec ──────────────┬─ 这里 ────────────────────────────┐
   │ name                          │ name（唯一，也是文件名）            │
   │ enabled / cond                │ enabled / cond（布尔或函数）         │
   │ priority（50 默认，配色 1000） │ 同                                  │
   │ lazy = true                   │ 同                                  │
   │ event = "BufEnter"            │ event = "page" / "editor" /          │
   │                               │         "boot" / "verylazy"          │
   │ cmd = "GrugFar"               │ cmd = "gantt"（`:` 命令名）          │
   │ ft = "markdown"               │ ft = "markdown"（编辑器语言）         │
   │ keys = { {lhs, desc} }        │ keys 同形（自动进 which-key）         │
   │ dependencies                  │ deps（名字数组，按序加载）            │
   │ opts / config(opts)           │ opts / config(api, opts)             │
   │ init()                        │ 无（浏览器没有启动前阶段）            │
   └───────────────────────────────┴─────────────────────────────────────┘

   一个插件能往 UI 里贡献什么（这是"网页比 TUI 能做更复杂"的部分）：
     api.keys(...)       → which-key 条目（自动重建树）
     api.command(...)    → `:` ex 命令（自动进补全表）
     api.view(...)       → 新路由 + 新视图渲染函数
     api.panel(...)      → 全屏/半屏面板（TUI 里只能开终端）
     api.editor(fn)      → CodeMirror 扩展（补全等）
     api.css(url)        → 额外样式
     api.on(event, fn)   → 钩子：boot / page / editor / navigate
     api.data / api.ui   → 数据层与 UI 原语的只读句柄

   所有注册都是「可撤销」的：disable 一个插件会把自己的键位/命令/视图
   一起收回，不需要重启。lazy.nvim 里这是 :Lazy disable，
   对应这里的 :Plugins → 切 enabled。
   ══════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  const LS_KEY = 'kb-plugins-disabled';

  /** @type {Array<PluginSpec>} */
  const specs = [];
  const declaredRoutes = [];
  const loaded = new Map();      // name -> resolved api（已加载）
  const loading = new Map();     // name -> Promise（并发去重）
  const registered = new Map();  // name -> { keys, cmds, views, css, panelIds, hooks }
  const hooks = new Map();       // event -> Set<fn>

  function toast(msg, kind) {
    if (typeof window.toast === 'function') window.toast(msg, kind);
    else console.log('[plugin]', msg, kind || '');
  }

  /* ── 禁用状态持久化（相当于 lazy.nvim 的 lockfile 里的 enabled）── */
  function disabledList() {
    try { return JSON.parse(localStorage.getItem(LS_KEY) || '[]'); } catch (e) { return []; }
  }
  function setDisabled(name, off) {
    const cur = disabledList().filter(n => n !== name);
    if (off) cur.push(name);
    localStorage.setItem(LS_KEY, JSON.stringify(cur));
  }
  function isEnabled(spec) {
    if (disabledList().indexOf(spec.name) >= 0) return false;
    const e = spec.enabled !== undefined ? spec.enabled : spec.cond;
    if (typeof e === 'function') { try { return !!e(); } catch (err) { return false; } }
    return e !== false;
  }

  /* ── 能力登记表 ────────────────────────────────────────── */
  function reg(name) {
    if (!registered.has(name)) {
      registered.set(name, { keys: [], cmds: [], views: [], css: [], cmExt: [], unsub: [], events: new Map() });
    }
    return registered.get(name);
  }

  /* ── 动态加载插件代码（lazy 的核心）───────────────────── */
  const BASE = (typeof window.__KB_BASE__ === 'string') ? window.__KB_BASE__ : '';

  function loadScript(url) {
    return new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = url;
      s.async = true;
      s.onload = () => resolve(url);
      s.onerror = () => reject(new Error('加载失败：' + url));
      document.head.appendChild(s);
    });
  }

  function rebuildLeader() {
    if (typeof window.rebuildLeaderTree === 'function') window.rebuildLeaderTree();
  }

  function rebuildCommands() {
    if (typeof window.rebuildCommandTable === 'function') window.rebuildCommandTable();
  }

  /** 插件的 api 对象。插件只通过它接触外部世界，不直接读全局变量。 */
  function makeApi(spec) {
    const name = spec.name;
    const r = reg(name);
    return {
      name,
      opts: spec.opts || {},

      /* ── 注册：which-key 键位（lazy 的 keys）── */
      keys(list) {
        (Array.isArray(list) ? list : [list]).forEach(k => {
          if (!k || !k.lhs) return;
          r.keys.push({ lhs: k.lhs, desc: k.desc || k.label || name, group: k.group, run: k.run });
        });
        rebuildLeader();
      },

      /* ── 注册：`:` 命令 ── */
      command(cmdName, desc, fn) {
        r.cmds.push({ name: cmdName, desc: desc || name, run: fn });
        rebuildCommands();
      },
      commands(list) {
        (list || []).forEach(c => r.cmds.push({ name: c.name, desc: c.desc || c.name, run: c.run }));
        rebuildCommands();
      },

      /* ── 注册：视图。show(arg, token) 负责渲染到 #view ── */
      view(target, show, meta) {
        r.views.push({ target, show, meta: meta || {} });
        if (typeof window.rebuildRouteTable === 'function') window.rebuildRouteTable();
      },

      /* ── 额外 CSS（主题、组件的样式表）── */
      css(url) {
        const href = url.indexOf('http') === 0 || url.indexOf('/') === 0 ? url : BASE + '/static/' + url;
        if (r.css.indexOf(href) >= 0) return;
        r.css.push(href);
        const link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = href;
        link.dataset.kbPlugin = name;
        document.head.appendChild(link);
      },

      /* ── CodeMirror 扩展：编辑器初始化后调用 fn(cm) ── */
      editor(fn) {
        r.cmExt.push(fn);
        if (window.cm) { try { fn(window.cm); } catch (e) {} }
      },

      /* ── 钩子 ── */
      on(event, fn) {
        if (!hooks.has(event)) hooks.set(event, new Set());
        hooks.get(event).add(fn);
        // 同时记在插件自己的表里：lazy 事件触发后要「补跑」这里注册的钩子
        const es = r.events;
        if (!es.has(event)) es.set(event, new Set());
        es.get(event).add(fn);
        r.unsub.push(() => { hooks.get(event).delete(fn); es.get(event).delete(fn); });
      },

      /* ── 数据层句柄（只读用；写操作统一走 api.jfetch 以保证缓存失效）── */
      get BASE() { return BASE; },
      get API() { return BASE + '/api'; },
      get state() { return window.state; },
      get cm() { return window.cm; },

      data: {
        jfetch: (...a) => window.jfetch(...a),
        getPage: (...a) => window.getPage(...a),
        getPages: () => window.fetchAllPages(),
        getNames: () => window.ensureNames(),
        getGraph: () => window.jfetch(BASE + '/api/graph'),
        getTags: () => window.getTags(),
        invalidate: () => window.invalidateCache(),
        tasks: { parse: window.parseTasks, pages: window.taskPages, current: window.taskPage },
        caches: () => window._cache,
      },

      ui: {
        toast,
        navigate: (...a) => window.navigate(...a),
        view: () => document.getElementById('view'),
        statusline: () => document.getElementById('statusline'),
        renderMd: (s) => window.renderMd(s),
        escapeHtml: (s) => window.escapeHtml(s),
        overlay: (id) => window.openOverlay(id),
        closeOverlays: () => window.closeOverlays(),
        setStatusName: (...a) => window.setStatusName(...a),
        confirm: (...a) => window.confirmText(...a),
        prompt: (...a) => window.promptText(...a),
        pick: (...a) => window.openPicker(...a),
        mount: function (panelId) { return window.KBPanel.panels.get(panelId) || null; },
        panel: function (opts) { return window.KBPanel.mount(opts); },
      },

      /* 允许插件自己收回自己的注册（相当于 spec 里删掉） */
      dispose() { dispose(name); },
    };
  }

  function dispose(name) {
    /* 先收回这个插件注册的影子键/命令（lazy 触发层） */
    const sh = registered.get('__shadow__');
    if (sh) {
      sh.keys = sh.keys.filter(x => x.owner !== name);
      sh.cmds = sh.cmds.filter(x => x.owner !== name);
      sh.views = sh.views.filter(x => x.owner !== name);
      window.__KB_SHADOW_CMDS__ = (window.__KB_SHADOW_CMDS__ || []).filter(c => c.shadowOf !== name);
    }
    const r = registered.get(name);
    if (!r) { rebuildLeader(); rebuildCommands(); return; }
    r.unsub.forEach(fn => { try { fn(); } catch (e) {} });
    r.views.length = 0; r.keys.length = 0; r.cmds.length = 0; r.cmExt.length = 0;
    r.css.forEach(href => {
      document.querySelectorAll('link[data-kb-plugin]').forEach(l => {
        if (l.href.indexOf(href.slice(0, 60)) >= 0) l.remove();
      });
    });
    r.css.length = 0;
    rebuildLeader(); rebuildCommands();
    if (typeof window.rebuildRouteTable === 'function') window.rebuildRouteTable();
    loaded.delete(name);
  }

  /* ── 加载一个插件（lazy.nvim 的 require + config）──────── */
  async function load(name) {
    const spec = specs.find(s => s.name === name);
    if (!spec) throw new Error('未注册插件：' + name);
    if (loaded.has(name)) return loaded.get(name);
    if (loading.has(name)) return loading.get(name);
    if (!isEnabled(spec)) return null;

    const p = (async () => {
      // deps 先按序加载
      for (const dep of (spec.deps || [])) await load(dep);

      // 代码：要么是独立文件（默认），要么内联在 spec.config
      if (spec.file) {
        const url = BASE + '/static/plugins/' + spec.file;
        window.__KB_PLUGIN_PENDING__ = name;
        await loadScript(url);
        // 插件文件必须调用 KB.define(name, fn)
        const factory = window.__KB_PLUGIN_DEFS__ && window.__KB_PLUGIN_DEFS__[name];
        if (!factory) throw new Error(name + ' 没有调用 KB.define');
        factory(makeApi(spec), spec.opts || {});
      } else if (spec.config) {
        spec.config(makeApi(spec), spec.opts || {});
      }
      const api = makeApi(spec);
      loaded.set(name, api);
      emit('plugin:loaded', { name });
      return api;
    })();

    loading.set(name, p);
    try { const v = await p; loading.delete(name); return v; }
    catch (e) { loading.delete(name); toast(name + ' 加载失败：' + e.message, 'error'); throw e; }
  }

  function emit(event, payload) {
    const set = hooks.get(event);
    if (!set) return;
    set.forEach(fn => { try { fn(payload); } catch (e) { console.warn('[plugin hook]', event, e); } });
  }

  /* ── 触发条件解析（把 lazy.nvim 的 event/cmd/keys/ft 翻译过来）── */
  function armed(spec) {
    return { event: [], cmd: [], keys: [], ft: [] };
  }

  function installTriggers(spec) {
    if (!isEnabled(spec)) return;
    // 非 lazy 的不挂触发，由 setup() 直接加载

    const t = armed(spec);
    const chunks = Array.isArray(spec.event) ? spec.event : (spec.event ? [spec.event] : []);
    chunks.forEach(ev => {
      const name = spec.name;
      if (ev === 'boot') load(name);
      else if (ev === 'verylazy') setTimeout(() => load(name), 400);
      else if (ev === 'page' || ev === 'editor' || ev === 'navigate') {
        /* lazy.nvim 语义：事件来了才加载插件，插件加载完要「补跑」它自己
         * 为这个事件注册的处理器 —— 否则事件早于插件代码，钩子永远等不到
         * 下一次同名事件（例如首屏直接 /page/KB 时 toc 永远不出现）。 */
        const fn = (payload) => {
          if (loaded.has(name)) return;
          load(name).then(() => {
            const r = registered.get(name);
            (r && r.events.get(ev) || new Set()).forEach(h => {
              try { h(payload); } catch (e) { console.warn('[plugin catch-up]', name, ev, e); }
            });
          }).catch(() => {});
        };
        if (!hooks.has(ev)) hooks.set(ev, new Set());
        hooks.get(ev).add(fn);
        t.event.push(ev);
      }
    });

    (Array.isArray(spec.cmd) ? spec.cmd : (spec.cmd ? [spec.cmd] : [])).forEach(c => {
      // cmd 触发 = 命令表里登记"影子命令"，首次调用时先加载再执行
      const shadow = {
        name: c,
        owner: spec.name,
        desc: spec.desc || (c + '（插件）'),
        run: async (args, arg) => {
          try { await load(spec.name); } catch (e) { return; }
          // 插件加载后自己注册了这条命令 → 直接调用它（避免再进 runCommand 递归）
          const own = reg(spec.name).cmds.find(x => x.name === c);
          if (own && own.run) return own.run(args || [], arg);
          return window.runCommand(c, args);
        },
        shadowOf: spec.name,
      };
      const sc = reg('__shadow__');
      if (!sc.cmds.some(x => x.owner === spec.name && x.name === c)) sc.cmds.push(shadow);
      window.__KB_SHADOW_CMDS__ = (window.__KB_SHADOW_CMDS__ || []).concat([shadow]);
      rebuildCommands();
      t.cmd.push(c);
    });

    (Array.isArray(spec.keys) ? spec.keys : (spec.keys ? [spec.keys] : [])).forEach(k => {
      const lhs = typeof k === 'string' ? k : k.lhs;
      if (!lhs) return;
      const sh = {
        lhs,
        owner: spec.name,
        desc: (typeof k === 'object' && (k.desc || k.label)) || spec.desc || spec.name,
        /* 影子键 = lazy 键：先加载插件，再把插件自己注册的同名键执行掉。
         * 这正是 lazy.nvim 的 keys 行为——按键立即生效，插件代码在那一刻才来。 */
        run: async () => {
          try { await load(spec.name); } catch (e) { return; }
          // 1) 插件自己注册了同 lhs 的键 → 用它（lazy.nvim 的正常路径）
          const own = reg(spec.name).keys.filter(x => x.lhs === lhs).pop();
          if (own && own.run) return own.run();
          // 2) spec 里直接给了 run
          if (typeof k === 'object' && k.run) return k.run();
          // 3) 否则退回该插件唯一的命令（spec.keys 只负责"加载并触发"）
          const cmds = reg(spec.name).cmds;
          if (cmds.length === 1 && cmds[0].run) return cmds[0].run([], '');
          toast('插件 ' + spec.name + ' 已加载，但按键 ' + lhs + ' 没有绑定动作', 'warn');
        },
      };
      const sk = reg('__shadow__');
      if (!sk.keys.some(x => x.owner === spec.name && x.lhs === lhs)) sk.keys.push(sh);
      rebuildLeader();
      t.keys.push(lhs);
    });

    /* 影子视图：lazy 插件的路由必须在注册表里先占位，
     * 否则首屏直接访问 /gantt 时 parseRoute 不认识这个词，会退回首页。
     * 这就是 lazy.nvim 的 cmd 触发在路由层面的对应物。 */
    (Array.isArray(spec.routes) ? spec.routes : (spec.routes ? [spec.routes] : [])).forEach(rw => {
      const sv = {
        target: rw,
        owner: spec.name,
        show: (arg) => {
          /* 插件代码还没到 → 先加载，然后重放一次 dispatch。
           * 重放会命中插件刚注册的真视图（真视图在 collect 里排在影子之前）。 */
          load(spec.name).then(() => {
            if (typeof window.dispatch === 'function' && window.state) {
              window.dispatch(rw, arg);
            }
          }).catch(() => {});
        },
        lazy: true,
      };
      const sv2 = reg('__shadow__');
      if (!sv2.views.some(x => x.target === rw)) sv2.views.push(sv);
      t.view = (t.view || []).concat([rw]);
    });

    spec.__armed = t;
  }

  /* ── 对外 API ─────────────────────────────────────────────── */
  const KB = {
    version: 1,

    /** 插件文件里调用：KB.define('gantt', function(api, opts){ ... }) */
    define(name, factory) {
      window.__KB_PLUGIN_DEFS__ = window.__KB_PLUGIN_DEFS__ || {};
      window.__KB_PLUGIN_DEFS__[name] = factory;
    },

    /** 加载清单（static/plugins/index.js 里调用） */
    spec(list) {
      (list || []).forEach(s => {
        if (!s || !s.name || specs.some(x => x.name === s.name)) return;
        specs.push(s);
        // 路由词在注册时就静态可知，供 app.js 推导 BASE（不需要先 armAll）
        if (s.routes) {
          const rs = Array.isArray(s.routes) ? s.routes : [s.routes];
          rs.forEach(r => { if (declaredRoutes.indexOf(r) < 0) declaredRoutes.push(r); });
        }
      });
      return KB;
    },

    /** 插件声明的路由词（静态，注册即知） */
    declaredRoutes() { return declaredRoutes.slice(); },

    /** 同步装触发条件（必须在首屏 dispatch 之前跑，
     * 否则 /gantt 这种 lazy 插件的路由词还不认识，会退回首页）。 */
    armAll() {
      const sorted = specs.slice().sort((a, b) => (b.priority || 50) - (a.priority || 50));
      for (const s of sorted) {
        if (s.__armed) continue;      // 幂等
        installTriggers(s);
      }
      return KB;
    },

    /** boot 时调用：非 lazy 的立即加载；lazy 的只等触发。 */
    async setup() {
      KB.armAll();
      const sorted = specs.slice().sort((a, b) => (b.priority || 50) - (a.priority || 50));
      for (const s of sorted.filter(x => x.lazy !== true)) {
        try { await load(s.name); } catch (e) {}
      }
      emit('boot', {});
      return KB;
    },

    load,
    isLoaded: (n) => loaded.has(n),
    isEnabled: (n) => { const s = specs.find(x => x.name === n); return !!s && isEnabled(s); },
    setEnabled(name, on) {
      setDisabled(name, !on);
      if (!on) dispose(name);
      else { const s = specs.find(x => x.name === name); if (s) installTriggers(s); }
    },
    list: () => specs.map(s => ({
      name: s.name,
      desc: s.desc || '',
      priority: s.priority || 50,
      lazy: s.lazy === true,
      loaded: loaded.has(s.name),
      enabled: isEnabled(s),
      triggers: s.__armed || null,
      routes: s.routes ? (Array.isArray(s.routes) ? s.routes : [s.routes]) : [],
    })),

    /* 供 app.js 查询：插件注册的键位 / 命令 / 视图 ── */
    keys() { return collect('keys'); },
    cmds() { return collect('cmds'); },
    views() { return collect('views'); },
    cmExt() { return collect('cmExt'); },
    viewFor(target) { const v = collect('views').find(x => x.target === target); return v ? v.show : null; },
    viewRoutes() { return collect('views').map(v => v.target); },
    routeWords() { return collect('views').map(v => v.target); },
    emit,

    /* 让 app.js 能主动喂事件（page/editor/navigate 触发） */
    trigger(ev, payload) { emit(ev, payload); },
  };

  function collect(kind) {
    const out = [];
    registered.forEach((r, name) => {
      if (name === '__shadow__') return;
      if (!isEnabled(name)) return;
      r[kind].forEach(x => out.push(x));
    });
    /* 影子（lazy 触发占位）排在后面：
     * which-key 里仍能看到，但 viewFor() 优先命中真视图，
     * 否则影子调用自己会无限递归。 */
    if (registered.has('__shadow__')) registered.get('__shadow__')[kind].forEach(x => out.push(x));
    return out;
  }

  window.KB = KB;
})();
