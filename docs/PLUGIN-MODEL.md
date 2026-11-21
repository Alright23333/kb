# 插件模型：把 lazy.nvim 搬到浏览器里

## 起因

侧栏导航、甘特图、`[[引用]]` 补全这三件事，在原版 `app.js` 里是散落的
三个函数夹在一堆 view 代码中间。它们的共同点：**不是核心功能，是可选能力**。
lazy.nvim 对这种东西有成熟的处理方式——插件 spec + lazy 触发；LazyVim 里
lazygit 插件就是典型：一个 `keys` 绑定，按键时在 nvim 里开个终端跑外部工具。

浏览器比 TUI 强的地方：那个"终端"可以是真 DOM 面板，能读 KB 自己的 API，
显示标签数量和更新时间，而不是模拟一个 80×24 的字符网格。

所以这里把 lazy.nvim 的**注册与触发模型**整套搬过来，媒体换成 DOM。

## 三个文件的分工

| 文件 | 行数 | 职责 |
|---|---|---|
| `static/plugin.js` | 474 | 内核：spec 登记、lazy 触发、能力表、生命周期 |
| `static/panel.js` | 236 | 面板容器：overlay / dock / full 三种模式 |
| `static/plugins/index.js` | 95 | 插件清单（= `lua/plugins/*.lua` 的合集） |

应用主程序 `app.js` 只提供**桥**（`window.KB` 对面的一批函数导出），
插件只通过 `api.*` 接触外部世界，不直接读全局变量。

## spec 字段（与 lazy.nvim 一一对应）

```js
{
  name:     'gantt',          // 唯一标识
  file:     'gantt.js',       // static/plugins/gantt.js
  desc:     '任务甘特图',      // which-key 与 :Plugins 里显示
  priority: 55,               // 数字大者先（同 lazy.nvim）
  lazy:     true,             // false = boot 时立即加载
  cmd:      'gantt',          // `:` 命令触发
  routes:   ['gantt'],        // 视图路由触发（/gantt）
  event:    'navigate',       // 事件触发
  ft:       'markdown',       // 文件类型（这里是编辑器里的 markdown）
  keys:     [{ lhs:'tg', desc:'任务甘特图' }],   // which-key 触发
  opts:     { mode:'Month' }, // 传给 config 的第二参数
  enabled:  true,             // 也可以给函数（cond）
}
```

加载时机：`lazy !== true` 的 boot 即 `load()`；`lazy` 的只装触发条件，
一行插件代码都不执行。

## 触发条件怎么翻译

| lazy.nvim | 这里 |
|---|---|
| `keys` | which-key 树里插一条**影子键**，按下时先 `load()` 再执行插件自己注册的同名键 |
| `cmd` | 命令表里插一条**影子命令**，`:xxx` 时先 `load()` 再执行 |
| `event` | 挂 `boot/verylazy/page/editor/navigate` 钩子；事件先到、插件后到时**补跑**它注册的处理器 |
| `ft` | 编辑器是 markdown 时才挂（`hint` 用） |
| （新增）`routes` | 视图路由 `/gantt` 也作为触发源；同时把路由词登记给 BASE 推导和 SPA 路由表 |

影子条目都带 `owner` 字段，`dispose(name)` 时精确回收，所以 `:Plugins`
里关掉一个插件，它的键位/命令/路由立刻消失，不需要刷新页面。

## 插件能注册什么（api）

```js
api.keys([...])          // which-key 键位（也可在 spec 里给）
api.command(name, desc, fn) // `:ex` 命令（自动进补全）
api.view(target, fn)     // 视图 + 路由
api.css(url)             // 额外样式表（lazy，插件加载时才注入）
api.editor(fn)           // CodeMirror 扩展
api.on(ev, fn)           // 钩子：boot/page/editor/navigate
api.data  // jfetch/getPage/getTags/tasks/parse… 只读数据句柄
api.ui    // toast/navigate/view/renderMd/overlay/confirm/prompt/pick/panel
api.dispose              // 卸载自己
```

所有注册写进 `registered.get(name)` 的能力表，`unsub` 里存撤销函数。

## 面板：浏览器版的 "开个终端"

`KBPanel.mount({id, mode, title, render, onMount, onUnmount, onResize})`

| mode | 表现 |
|---|---|
| `overlay` | 遮罩 + 居中浮层（Esc 关最上层），toc / manager 用 |
| `dock` | 贴左/右，可拖宽 160–560px，通过 CSS 变量 `--kbp-dock-w` 与 `margin-*` 让 tabline/view/statusline 让位，sidebar 用 |
| `full` | 整屏（留给未来的编辑器类插件） |

`el.id = id`，所以插件内部用 `#kb-sidebar` 这类选择器也能找到。

对应 lazygit：LazyVim 的 gitui 插件是 `Snacks.terminal("gitui")` 占满一个
float；这里是 `KBPanel.mount({mode:'dock'})` 里渲染真 DOM。宿主（KB 应用）
和外部工具（插件）的边界是同一个。

## 主题：`:colorscheme` 模型

nvim 里配色是 `runtimepath/colors/<name>.vim`；切配色是
`vim.cmd("colorscheme xxx")`，不重开窗口。这里同构：

| 文件 | 内容 |
|---|---|
| `static/themes/catppuccin-mocha.css` | `:root[data-scheme="catppuccin-mocha"] { 52 个变量 }` |
| `static/themes/catppuccin-latte.css` | 同上 |
| `static/themes/index.js` | 注册表 + `loadTheme/applyTheme`，暴露 `window.KBThemes` |

```sh
:colorscheme            # 列全部，当前的前面带 *
:colorscheme mocha      # 前缀唯一即可（同 nvim 的 Tab 补全语义）
:colo latte
```

切换 = 只改 `<html data-scheme>` 一个属性，浏览器重算所有 `var()`，
不重新解析 CSS、不闪屏。加主题 = 加一个 CSS 文件 + 注册表加一行。

模板里只剩一份**语义变量兜底**（`--bg/--fg/--accent…`），避免首屏闪白；
CodeMirror 的配色规则也折叠进了主题文件，前缀改写成
`:root[data-scheme="…"] .cm-s-catppuccin …`，所以**切主题时 CM 不需要重建**。

## 现有插件

| name | 触发 | 做的事 |
|---|---|---|
| `manager` | 常驻 | `:Plugins` / `<space>P` 面板，开关全部插件，状态写 localStorage |
| `sidebar` | `<space>bs` / `:sidebar` | dock 面板：快捷导航、579 页 / 124 标签、标签云、最近更新 |
| `gantt` | `/gantt` / `:gantt` / `<space>tg` / navigate | 懒加载 frappe-gantt，渲染任务时间轴，可导出 mermaid |
| `hint` | 编辑器 ft=markdown / `<space>uH` | CodeMirror `showHint` 的 `[[页面]]` / `#标签` 补全 |
| `toc` | `page` 事件 / `<space>uT` | 已渲染标题生成右侧目录（少于 3 个标题不显示） |
| `autosave-hint` | `editor` 事件 | 最小示范：不改 UI，只挂钩子，20 分钟提醒保存 |

## 与老前端的差别

老 `app.js` 里这些是硬编码在 `LEADER_SPEC` / `COMMANDS` / `showView` switch 里
的分支；关不掉、也加不了。新模型下：

- 加一个插件 = 放一个文件 + 清单加一条 spec，**不改 app.js**
- 关一个插件 = `:Plugins` 里点一下，键位/命令/路由立即收回
- 主题 = 一个 CSS 文件 + 一行注册，**不改 app.js、不改模板**

## 踩过的坑（都已在代码里修掉）

1. **`Object.assign(window, {get cm(){…}})` 会把 getter 求值一次拷成常量** ——
   `cm` 在桥执行时还是 `null`，之后编辑器建好也拿不到。改用
   `Object.defineProperties(window, {cm:{get}})`。
2. **lazy 插件的路由词必须静态可知** —— `BASE` 是「路径里第一个路由词之前的
   部分」，访问 `/gantt` 时若不认识 `gantt`，会把整个 `/gantt` 当部署前缀，
   base 一错所有路由退回首页。所以 `KB.spec()` 登记时就把 `routes` 收进
   `declaredRoutes()`，`app.js` 推导 BASE 时一并查。
3. **影子视图不能自己找真视图** —— 影子在 `collect('views')` 里排在真视图
   之后，`viewFor` 优先真视图；影子只负责「加载 + 重放 dispatch」。
4. **lazy 事件钩子会等不到第二次事件** —— 首屏直接 `/page/KB`，`page` 事件
   发出时 toc 还没加载，插件加载完必须**补跑**自己为该事件注册的处理器。
5. **`show-hint` 的契约是 `hint(editor) → {list, from, to}`**，选中项靠
   `list[i].text` 替换；想自动补 `]]` 就要写进候选文本，不能靠回调。
6. **`CodeMirror.showHint` 需要编辑器有焦点**，否则 widget 不定位、不上屏。
7. **`dispose` 必须连影子条目一起回收**，否则开关一次后 which-key 里会
   出现两条一样的键。
