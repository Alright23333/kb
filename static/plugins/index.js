/* ══════════════════════════════════════════════════════════════════════
   插件清单 —— 相当于 lazy.nvim 里 lua/plugins/*.lua 的合集
   ─────────────────────────────────────────────────────────────────────
   加一个插件 = 在 static/plugins/ 里放一个文件，然后下面加一条 spec。
   减一个插件 = 删掉这条（文件可以留着，反正是 lazy 的）。
   临时关掉 = :Plugins → 选中回车（写进 localStorage，不重启）。

   字段语义与 lazy.nvim 一一对应，见 plugin.js 顶部注释。
   ══════════════════════════════════════════════════════════════════════ */

KB.spec([
  /* ── 侧栏导航 ─────────────────────────────────────────────
   * 对应 lazygit 那种「开一个新窗口跑工具」：这里是开一个 dock 面板，
   * 但它读的是 KB 的 API，所以能显示标签、最近、统计，比 TUI 强。
   * lazy：不点 space b s 或 :sidebar 就不占内存。
   */
  {
    name: 'sidebar',
    file: 'sidebar.js',
    desc: '侧栏导航（dock 面板）',
    priority: 60,
    lazy: true,
    cmd: 'sidebar',
    keys: [{ lhs: 'bs', desc: '侧栏导航' }],
    opts: { width: 248, side: 'left' },
  },

  /* ── 甘特图 ───────────────────────────────────────────────
   * frappe-gantt 是第三方库，用 api.css 挂进来（lazy 到用时才加载），
   * 视图注册成 /gantt，`:` 也能开。这正是 lazygit 的等价物：
   * 外部工具 + 宿主给它一块显示区域。
   */
  {
    name: 'gantt',
    file: 'gantt.js',
    desc: '任务甘特图（frappe-gantt）',
    priority: 55,
    lazy: true,
    cmd: 'gantt',
    routes: ['gantt'],
    keys: [{ lhs: 'tg', desc: '任务甘特图' }],
    opts: { mode: 'Month' },
  },

  /* ── [[引用]] 补全 ────────────────────────────────────────
   * CodeMirror 的 show-hint 是官方 addon；插件只负责算候选
   * （输入 [[ 时从页面名表里 fuzzy 匹配）。ft = markdown 时才生效。
   */
  {
    name: 'hint',
    file: 'hint.js',
    desc: '[[引用]] / #标签 补全',
    priority: 50,
    lazy: true,
    ft: 'markdown',
    event: 'editor',
    keys: [{ lhs: 'uH', desc: '手动触发补全' }],
  },

  /* ── 目录 TOC ─────────────────────────────────────────────
   * 页面视图右侧浮一个目录；纯前端，读已渲染的 h1/h2/h3。
   */
  {
    name: 'toc',
    file: 'toc.js',
    desc: '页面目录（TOC）',
    priority: 45,
    lazy: true,
    event: 'page',
    keys: [{ lhs: 'uT', desc: '目录开关' }],
    opts: { minHeadings: 3 },
  },

  /* ── 知识库体检 ───────────────────────────────────────────
   * 旧版 computeHealth()/loadHealth() 的插件化移植。
   * dock 右侧面板，不挡正文；孤立页/断链按引用数排序，
   * 超过 opts.fold 条折叠 —— 旧版 155 项直下 22880px 的教训。
   */
  {
    name: "health",
    file: "health.js",
    desc: "知识库体检（孤立页/断链）",
    priority: 48,
    lazy: true,
    cmd: "health",
    keys: [{ lhs: "uh", desc: "知识库体检" }],
    opts: { fold: 25 },
  },

  /* ── 常用搜索 ───────────────────────────────────────────────
   * 旧版 saveSearch()/getSavedSearches() 的插件化移植。
   * 存储键沿用 kb-saved-searches，两套 UI 之间切换不丢收藏。
   */
  {
    name: "saved-search",
    file: "saved-search.js",
    desc: "常用搜索（固定/删除）",
    priority: 47,
    /* 必须常驻：它靠 MutationObserver 给每个页面注入固定按钮/卡片，
     * 懒加载的话没人触发它，等于没装。文件只有 ~6KB。 */
    lazy: false,
    cmd: "Searches",
    keys: [{ lhs: "uS", desc: "常用搜索" }],
    opts: { max: 8 },
  },

  /* ── 插件管理面板（同 :Lazy）────────────────────────────── */
  {
    name: 'manager',
    file: 'manager.js',
    desc: '插件管理面板（:Plugins）',
    priority: 70,
    lazy: false,       // 面板本身要常在，才能开关别的插件
  },

  /* ── 备份提示 ─────────────────────────────────────────────
   * 一个最小的"插件"示范：不改任何 UI，只挂钩子。
   */
  {
    name: 'autosave-hint',
    file: 'autosave-hint.js',
    desc: '编辑超过 20 分钟提醒保存',
    priority: 40,
    lazy: true,
    event: 'editor',
  },
]);
