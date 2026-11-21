# 前后端结构：旧版 vs 新版

## 一句话

**后端一行没改，前端整个重写。** 旧前端是 5234 行、210KB 的单个
`templates/index.html`（CSS + HTML + 全部 JS 内联）；新前端是
866 行模板 + 3292 行独立 `static/app.js`。

## 后端（两版完全相同）

```
                    ┌─────────────────────────────┐
  浏览器 ──HTTP──▶  │  main.py  (FastAPI, 229 行) │
                    │  ├ ip_whitelist 中间件       │
                    │  ├ strip_static_prefix 中间件│
                    │  ├ /api  → routers/api.py    │
                    │  ├ /static → StaticFiles     │
                    │  └ /*     → index.html (SPA) │
                    └──────────┬──────────────────┘
                               │
                    ┌──────────┴──────────┐
                    │ routers/api.py       │  838 行，22 个端点
                    │ search.py  166 行    │  FTS5 + jieba + rerank
                    │ database.py 125 行   │  SQLite WAL, aiosqlite
                    │ parse.py   102 行    │  [[链接]] / #tag 解析
                    │ timeutil.py          │  UTC→本地
                    │ export.py backup.py  │  zip/json 导出、定时备份
                    └──────────┬──────────┘
                               │
                        data/kb.db  (SQLite WAL)
```

**后端是纯数据层 + REST API，不知道前端长什么样。** 它只做：

| 端点组 | 作用 |
|---|---|
| `GET/PUT/POST/PATCH/DELETE /api/pages...` | 页面的读写改名删除 |
| `/api/pages/{name}/versions...` | 版本历史（快照/恢复/清理） |
| `/api/tags`, `/api/tags/{t}/pages` | 标签 |
| `/api/trash` | 回收站 |
| `/api/pages?search=&tag=&limit=&offset=` | 列表/搜索（limit ≤ 500） |
| `/api/export/{zip,json}`, `/api/import` | 导入导出 |
| `/api/graph` | 图谱节点+边 |
| `/api/custom.css` | 自定义样式 |
| `/api/pages/count`, `/api/pages/names` | 计数/名字 |

`main.py` 的 SPA 兜底逻辑：任何不匹配 `/api`/`/static`/已知路由词的路径
都返回 `index.html`。**所以后端对前端唯一的要求是"前端得有路由"**，
内置路由词表 `_SPA_ROUTE_WORDS` 要跟前端 regex 对齐。

插件路由（如 `/gantt`）**不需要**进 `_SPA_ROUTE_WORDS`：兜底本身就会把
未知路径喂给 `index.html`，前端再靠 `KB.declaredRoutes()` 认识它。
后端不该知道有哪些插件 —— 加插件不需要改 `main.py`。

> 测试副本 vs 生产的后端差异只有两处：
> 1. `nocache_first_party_static` 中间件（`/static/*` 除 vendor 外 `no-cache`，
>    否则改 `app.js` 浏览器仍用旧版）
> 2. `_SPA_ROUTE_WORDS` 多了 `search`
>
> `routers/api.py`、`database.py`、`search.py`、`parse.py`、
> `mcp_*.py`、`export.py`、`backup.py` **全部二进制相同**（`diff` 0 行）。

## 旧前端（生产 `~/services/kb-docker/templates/index.html`）

单文件 5234 行 / 210KB：

```
行 1-1337     <style>  —— 全部 CSS 内联
行 1338-1575  HTML 骨架 —— 所有视图写死在一个 #content 里
行 1576-5234  <script> —— 163 个函数，全部全局作用域
```

### 视图模型：切换 `display`

```js
function hideAll() {                       // 行 4405
  document.getElementById('home-view').style.display  = 'none';
  document.getElementById('todo-view').style.display  = 'none';
  document.getElementById('graph-view').style.display = 'none';
  document.getElementById('page-view').style.display  = 'none';
  document.getElementById('editor').style.display      = 'none';
  return ++viewToken;
}
```

五个预置容器常驻 DOM，切 `display` 换视图。路由 = 改 URL + 调 `navigate()`。
好处是切换零延迟；代价是全部视图的 HTML 都在模板里写死，加上侧栏、
顶栏、命令面板、设置面板、版本面板、回收站…… 44 个固定 `id` 区块。

### 功能清单（163 个函数，按模块）

| 模块 | 代表函数 | 是否在新版 |
|---|---|---|
| 首页看板 | `showHome`, `editDashboard` | ✅ 有（`showHome`）|
| 页面视图 | `showPage`, `buildToc`, `fillEmbeds` | ⚠️ 部分（无 TOC）|
| 编辑器 | `initCM`, `savePage`, `showVersionHistory` | ✅ 有 |
| 任务/待办 | `parseTasks`, `computeTaskStats`, `switchTodoTab` | ✅ 有（无甘特）|
| **甘特图** | `filterGanttTasks`, `colorGanttBars`, `copyTasksAsMermaidGantt` | ❌ **缺** |
| 图谱 | `graphForceTick`, `graphSetFocus`, `applyGraphTransform` | ✅ 有（简化）|
| 搜索 | `doSearch`, `doSavedSearch`, `getSavedSearches` | ⚠️ 无保存搜索 |
| 命令面板 | `openPalette`, `handlePaletteInput` | ✅ 换成 which-key + `:` |
| 侧栏 | `toggleSidebar`, `closeSidebar` | ❌ 去掉了 |
| 链接预览 | `link-preview-card` | ✅ 有（hover/长按）|
| 草稿 | `draftSave`, `draftRestore`, `checkDraft` | ✅ 有 |
| 体检 | `computeHealth`, `loadHealth` | ❌ **缺** |
| 引用补全 | `insertRef`, `#ref-autocomplete` | ⚠️ 靠 CodeMirror 自身 |
| 移动端 | `resetMobilePreview`, `updateFABVisibility` | ✅ 重做（动作坞）|

### 旧版的问题（重写的理由）

1. **单文件 210KB**：CSS/HTML/JS 混在一起，改一行要读 5000 行上下文
2. **163 个全局函数**：无模块边界，`viewToken`/`suppressPush` 这类可变全局散布
3. **浏览器缓存无法控制**：JS 内联在 HTML 里，改 JS 必然改 HTML 的 ETag
4. **视图写死**：加一个视图要在模板里加 `<div>` + `hideAll()` 加一行 + `navigate()` 加分支
5. **移动端是补丁**：`resetMobilePreview` / `updateFABVisibility` 这类"事后撑住"的函数
6. **没有统一的状态对象**：每个视图自己管自己的一堆全局变量

## 新前端（`~/pi-working/kb-ui-test/`）

拆成三层，各管各的：

```
templates/index.html   866 行   只有设计 token + DOM 骨架（无任何色值）
  ├ 行 45-720    <style>   组件样式、3 个 @media
  ├ 行 12-38     资源加载  document.write 一批 vendor（base 前缀先算）
  └ 行 700-866   DOM      18 个区块（vs 旧的 44 个）
static/app.js        3292 行   全部应用逻辑 + 桥（window.KB 对面）
static/plugin.js      474 行   插件内核（lazy.nvim 模型）
static/panel.js       236 行   面板容器 overlay / dock / full
static/sw.js          128 行   离线壳
static/themes/        129 行   catppuccin-mocha / latte 各一个 CSS + 注册表
static/plugins/       ~700 行   6 个插件（manager/sidebar/gantt/hint/toc/autosave）
```

主题与插件是**可扩展层**，不受 `app.js` 约束 —— 见
[PLUGIN-MODEL.md](PLUGIN-MODEL.md)。

### 视图模型：一个 `#view` 容器 + 每次重渲

```js
function dispatch(target, arg) {
  const token = ++navToken;
  if (target !== 'edit' && target !== 'new') state.editing = false;
  switch (target) {
    case 'home':   showHome(token);   break;
    case 'all':    showAll(token);    break;
    case 'tag':    showTag(arg, token); break;
    case 'search': showSearch(arg, token); break;
    case 'todo':   showTodo(token);   break;
    case 'journal':showJournal(arg, token); break;
    case 'graph':  showGraph(token);  break;
    case 'new':    openEditor('', '', token); break;
    case 'edit':   openEditor(arg, null, token); break;
    default:       showPage(arg, token); break;
  }
}
```

`#view` 每次清空重写 innerHTML，`navToken` 用于防止竞态（旧请求返回时
token 已变就丢弃）。好处是视图不残留状态、加视图只需加一个 `case`；
代价是切换有 ~10ms 重渲（实测无感）。

### 新的分层

| 层 | 内容 | 对照旧版 |
|---|---|---|
| 路由 | `routeUrl` / `parseRoute` / `navigate` / `dispatch` | 有，但语义更明确 |
| 状态 | 单一 `state` 对象 + `STATUS` DOM 引用表 | 旧版散落全局 |
| 数据 | `jfetch` / `cached` / `invalidateCache` / `fetchAllPages` | 旧版 `cachedFetch` |
| 渲染 | `renderMd`（markdown-it）/ `renderMermaid` / `fillEmbeds` | 同 |
| 能力表 | `LEADER_SPEC`（which-key 树）+ `ACTION_SPEC`（移动端坞）+ `KB.spec`（插件）| 旧版无 |
| 主题 | 模板内两段 CSS 变量 | `static/themes/*.css` + 注册表，`:colorscheme` 切换 |
| 可扩展性 | 改 app.js 才能加功能 | 加插件 = 加文件 + 清单一行 |
| 编辑器 | CodeMirror 5 + vim keymap + `CodeMirror.Vim.defineEx` | 同，但 Ex 命令更多 |
| 持久化 | 缓冲/主题/设置/草稿/最近访问 → localStorage | 同 |

### 关键差异表

| 维度 | 旧版 | 新版 |
|---|---|---|
| 模板体积 | 5234 行 / 210KB | 866 行 / 37KB（无内联 JS、无色值）|
| JS 位置 | 内联 | 独立 `static/app.js` |
| 全局函数 | 163 个 | 0（全在模块作用域） |
| 视图容器 | 5 个常驻 + `display` 切换 | 1 个 `#view` + 重渲 |
| 固定 DOM 区块 | 44 | 18 |
| 键盘系统 | 命令面板（`Ctrl-K` 风格） | `:` 命令行 + `space` which-key + vim 键位 |
| 主题 | 自研 light/dark | Catppuccin Mocha/Latte（AA 校验过） |
| 移动端 | FAB + `resetMobilePreview` | touch 模式：动作坞 + 数字盘 + 手势 |
| 状态栏 | 无 | 26px 底栏：模式/页名/光标/字数/页数/主题/网络/时钟 |
| 缓存 | HTML ETag | `sw.js` 分策略（vendor cache-first，自家 network-first） |

## 迁移时要补的东西

原先"故意不做"的 6 项里，4 项已经以**插件**形式补回来了（不占启动成本）：

| 功能 | 旧版实现 | 现在 |
|---|---|---|
| 甘特图 | `frappe-gantt` + 4 个函数 | 插件 `gantt`，`/gantt` 或 `space tg`，懒加载库 |
| 目录 TOC | `buildToc` | 插件 `toc`，`page` 事件自动挂，`space uT` 开关 |
| 侧栏导航 | `toggleSidebar` | 插件 `sidebar`，`space bs` 开 dock 面板，比旧版多标签云/统计 |
| 引用补全 `[[` | `insertRef` + 浮动框 | 插件 `hint`，换成 CodeMirror 官方 `show-hint` |
| 体检报告 | `computeHealth` | 仍未做（可写成插件，`api.data` 已够用） |
| 保存搜索 | `getSavedSearches` | 仍未做（`#tag` + 收藏页可替代） |

`templates/index.classic.html` 是旧模板的完整副本，随时可对照。

## 为什么这样拆

1. **后端稳定**：API 是 838 行成熟代码，22 个端点，有测试；重写它没有收益
2. **前端是瓶颈**：210KB 单文件让每次改动都高风险
3. **分离后可以并存**：旧 `kb` 容器跑旧模板，`kb-ui-test` 跑新模板，
   共用同一份 DB schema，合并只需换 `templates/` + `static/app.js`
4. **可回退**：`index.classic.html` + 生产容器原封不动
