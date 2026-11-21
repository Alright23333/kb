/* ══════════════════════════════════════════════════════════════════════
   主题注册表 —— 完全照 nvim/lazyvim 的 :colorscheme 模型
   ─────────────────────────────────────────────────────────────────────
   nvim 里一个配色就是 runtimepath 里的一个 `colors/<name>.vim`；
   加主题 = 放一个文件进 colors/，然后 `:colorscheme <name>`。
   lazyvim 更进一步：把配色当插件写进 spec，priority=1000 保证最先加载，
   再用 `vim.cmd("colorscheme tokyonight")` 应用。

   这里同构：
     · 一个主题 = static/themes/<name>.css 一个文件
     · 文件内是 :root[data-scheme="<name>"] { 一堆 CSS 变量 }
     · 注册表 = 下面这张表（相当于 colors/ 目录的 ls）
     · 切换 = :colorscheme <name> → html 上的 data-scheme 换一个值，
       不重建任何组件、不重新加载样式表

   为什么改属性而不是换 <link> 的 href：
   换 href 会让浏览器重新解析 CSS、首屏闪一下；改一个属性是 O(1)，
   所有 var() 引用自动重算。这也正是 nvim 切配色不重开窗口的道理。

   加一个新主题（例如 kanagawa）：
     1. 写 static/themes/kanagawa.css，选择器 :root[data-scheme="kanagawa"]
     2. 在下面 THEMES 表里加一行
     3. :colorscheme kanagawa
   不需要改 app.js、不需要改模板、不需要重建镜像。
   ══════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  /* 一个主题 = 一个色板文件（+ 可选的 dep 结构文件）。
   *
   * dep 的用法来自 Catppuccin 自己的 nvim 插件：四个 flavour 共用**一套**
   * 高亮组，flavour 只换 26 个官方色值。这里把「结构」放 dep 里只加载一次，
   * 加第五个 flavour 就只是加 15 行色板 + 一行登记。
   *
   * 没有 dep 的主题（以后接 tokyonight / kanagawa 那种）就是自包含的
   * 单文件，和原来一样 —— 两种形式共存，不强制。
   */
  const THEMES = [
    {
      name: 'catppuccin-mocha',
      label: 'Catppuccin Mocha',
      group: 'catppuccin',
      dark: true,
      file: 'catppuccin-mocha.css',
      dep: 'catppuccin.css',
    },
    {
      name: 'catppuccin-macchiato',
      label: 'Catppuccin Macchiato',
      group: 'catppuccin',
      dark: true,
      file: 'catppuccin-macchiato.css',
      dep: 'catppuccin.css',
    },
    {
      name: 'catppuccin-frappe',
      label: 'Catppuccin Frappe',
      group: 'catppuccin',
      dark: true,
      file: 'catppuccin-frappe.css',
      dep: 'catppuccin.css',
    },
    {
      name: 'catppuccin-latte',
      label: 'Catppuccin Latte',
      group: 'catppuccin',
      dark: false,
      file: 'catppuccin-latte.css',
      dep: 'catppuccin.css',
    },
  ];

  /* 每个主题文件只加载一次；加载完成后 resolve */
  const loaded = new Map();

  function themeEntry(name) {
    return THEMES.find(t => t.name === name) || null;
  }

  function themeNames() {
    return THEMES.map(t => t.name);
  }

  /* 挂一个 CSS 文件（幂等，按 href 去重） */
  const cssLoaded = new Map();
  function loadCss(base, file, tag) {
    const href = (base || '') + '/static/themes/' + file;
    if (cssLoaded.has(href)) return cssLoaded.get(href);
    const p = new Promise((resolve) => {
      const link = document.createElement('link');
      link.rel = 'stylesheet';
      link.href = href;
      if (tag) link.dataset.kbTheme = tag;
      link.addEventListener('load', () => resolve(href));
      link.addEventListener('error', () => resolve(href)); // 失败也别卡启动
      document.head.appendChild(link);
    });
    cssLoaded.set(href, p);
    return p;
  }

  /* 按名字把主题 CSS 挂上去（幂等）。
   * 有 dep 时先挂结构层，再挂色板 —— 顺序保证 var() 已有定义，
   * 否则首帧会用回退值闪一下。 */
  function loadTheme(name, base) {
    const entry = themeEntry(name);
    if (!entry) return Promise.reject(new Error('未知主题：' + name));
    if (loaded.has(name)) return loaded.get(name);

    const p = (entry.dep
      ? loadCss(base, entry.dep, entry.group || null)
      : Promise.resolve()
    ).then(() => loadCss(base, entry.file, name));
    loaded.set(name, p);
    return p;
  }

  /* 应用主题：只改 data-scheme + 保证 CSS 已加载 */
  function applyTheme(name, base) {
    const entry = themeEntry(name);
    const target = entry ? entry.name : THEMES[0].name;
    const t = themeEntry(target);
    document.documentElement.setAttribute('data-scheme', target);
    // data-theme 保留一份 light/dark 语义，供 prefers-color-scheme 类逻辑用
    document.documentElement.setAttribute('data-theme', t.dark ? 'dark' : 'light');
    return loadTheme(target, base).then(() => target);
  }

  function currentScheme() {
    return document.documentElement.getAttribute('data-scheme') || THEMES[0].name;
  }

  /* 同一 group 里的成员（:colorscheme <group> 时轮流切换用） */
  function groupMembers(name) {
    const entry = themeEntry(name);
    if (!entry || !entry.group) return [name];
    return THEMES.filter(t => t.group === entry.group).map(t => t.name);
  }

  /* 按 prefers-color-scheme 选默认主题（仅用户没显式选过时用） */
  function defaultTheme() {
    try {
      if (window.matchMedia('(prefers-color-scheme: light)').matches) {
        const latte = THEMES.find(t => !t.dark);
        if (latte) return latte.name;
      }
    } catch (e) {}
    return THEMES[0].name;
  }

  window.KBThemes = {
    THEMES, themeEntry, themeNames, loadTheme, applyTheme,
    currentScheme, defaultTheme, groupMembers,
  };
})();
