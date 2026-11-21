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

  const THEMES = [
    {
      name: 'catppuccin-mocha',
      label: 'Catppuccin Mocha',
      dark: true,
      file: 'catppuccin-mocha.css',
    },
    {
      name: 'catppuccin-latte',
      label: 'Catppuccin Latte',
      dark: false,
      file: 'catppuccin-latte.css',
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

  /* 按名字把主题 CSS 挂上去（幂等） */
  function loadTheme(name, base) {
    const entry = themeEntry(name);
    if (!entry) return Promise.reject(new Error('未知主题：' + name));
    if (loaded.has(name)) return loaded.get(name);

    const p = new Promise((resolve) => {
      const link = document.createElement('link');
      link.rel = 'stylesheet';
      link.href = (base || '') + '/static/themes/' + entry.file;
      link.dataset.kbTheme = name;
      link.addEventListener('load', () => resolve(name));
      link.addEventListener('error', () => resolve(name)); // 失败也别卡启动
      document.head.appendChild(link);
    });
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

  window.KBThemes = { THEMES, themeEntry, themeNames, loadTheme, applyTheme, currentScheme, defaultTheme };
})();
