# AGENTS.md — KB 项目开发指南

> 本文件面向 AI 开发助手。修改本仓库前请阅读本文件，避免违背设计原则的修改。

## 技能小结（先读这段）

本项目当前**只有一份前端**（`ui-lazyvim` 分支，已接管生产 `8080`）：

| 层 | 文件 | 规模 |
|---|---|---|
| 页面外壳 | `templates/index.html` | 954 行（CSS 63-773、DOM 773-954）|
| 应用逻辑 | `static/app.js` | 4895 行（最后约 300 行是 vim 层）|
| 插件内核 | `static/plugin.js` | 475 行（lazy.nvim 模型）|
| 面板容器 | `static/panel.js` | 247 行 |
| 插件实现 | `static/plugins/` | 8 个（1330 行）|
| 主题 | `static/themes/` | 结构层 + 4 flavour |
| 离线壳 | `static/sw.js` | 135 行（**改外壳必须 bump VERSION**）|

旧版单文件模板（5234 行）在 `main` 分支，**不在**当前工作区 —— 本文里
凡是提到 `applyCustomCSS`、`insertRef`、`openPalette`、`resetMobilePreview`、
`updateFABVisibility`、`sidebar.classList.add('open')` 这类名字的条目，都是
**旧版的历史教训**，对应代码已经不存在了；教训本身仍然有效，但要按新的
位置去找（见「旧教训在新代码里的落点」一节）。

## 项目定位

KB 是一个**人能用、AI 也能用**的双链笔记系统。
核心设计：**Markdown 是唯一权威源，存储引擎只做「内容 → 结构化索引」。**

## 不可违背的设计原则

1. **单一命名空间**：页面名 = 链接目标 = URL 键。不要引入 `slug`/`title` 分离。
2. **大小写不敏感**：所有 `name` 和 `target_name` 查询必须 `COLLATE NOCASE`。Python 侧用 `casefold()` 去重。
3. **FTS 触发器是自动同步的**：不要手动维护 `pages_fts`，插入/更新/删除 `pages` 后 SQLite 触发器会自动更新索引。
4. **refs 写入必须 delete-then-insert**：不要只插不删，否则「删掉的引用」会残留在 `refs` 表里。
5. **前端是单页应用**：所有视图切换靠 CSS `display`，不重新加载页面。

## 各层职责

### `database.py` — 存储引擎

- Schema 定义、FTS5 虚拟表、自动同步触发器
- 连接管理：`get_db()` 返回 WAL + foreign_keys 的 aiosqlite 连接
- 只读操作（查询）走 `aiosqlite.Row`，写操作走 `await db.commit()`

### `parse.py` — Markdown 解析

- 纯函数，无 I/O，可被 API 层和导入器共用
- 返回 `{"links": set, "tags": set, "properties": dict}`
- 标签提取：先剥离代码块（``` / ~~~ 围栏）与行内代码，再剥离 `[[..]]`；`TAG_RE` 要求 `#` 前是词边界，字符集限定 `[\w-]`（2026-10-24 收紧，防止全角标点/URL 锚点/代码内容混入标签）
- `find_link_lines(content, target)`：反链预览取引用原文行（大小写不敏感、含别名），api.py 与 mcp_server.py 共用
- 前端 `TAG_TRIGGER_RE`（`#` 补全触发）与后端 `TAG_RE` 规则保持一致，改一处要同步另一处
- 正则不要动，改了要跑解析单测

### `routers/api.py` — REST API

- `_sync(db, page_id, content)`：解析内容 → delete 旧 refs/properties → insert 新的
- `_search(db, q, limit, offset)`：trigram FTS 优先，<3 字符走 LIKE 兜底
- 所有端点 try/finally 确保 `db.close()`
- `update_page` 的改名逻辑：新名不存在才更新 name + 重写 refs.target_name

### 前端（`templates/index.html` + `static/`）— 新架构

页面外壳只放三样东西：设计 token（`<style>` 63-773）、DOM 骨架（773-954）、
vendor 加载（`document.write`，base 前缀先算）。**没有任何应用逻辑**。

应用逻辑全在 `static/app.js`，插件逻辑在 `static/plugin.js` + `static/plugins/`：

- CodeMirror 5（不是 6，CDN `6.65.7` 实际是 `5.65.7`），连同 markdown-it /
  Frappe Gantt / mermaid 全部 vendored 到 `static/vendor/`，不走 CDN
- markdown-it 自定义 `wikilink` 内联规则：`[[target]]` → `<a data-page="target">`
  （规则在 `app.js` 的 `md.renderer.rules.kb_*`，不在模板里）
- 全局事件委托在 `app.js` 底部：`[data-page]` / `[data-tag]` / `[data-nav]`
  / `[data-close]` / `[data-buffer]` …
- `navigate(target, arg)` 是唯一路由入口 → `dispatch()` → 各 `show*()`
- **主题**：`html[data-scheme="catppuccin-<flavour>"]`，四个 flavour 的
  语义别名与 CodeMirror 分组全在 `static/themes/catppuccin.css`（结构层），
  每个 flavour 只写 15 行色板（latte 例外 48 行）。**不要为某个 flavour
  单独重写规则** —— 加口味只需加一个色板文件 + 在 `themes/index.js` 登记一行
- **自定义 CSS**（`data/custom.css`）**只覆盖变量**，注入 `<style id="custom-css">`。
  不要在 JS 里逐元素写 inline style —— 后渲染的卡片会漏、切主题会残留
- **不要新增 `@media (prefers-color-scheme)` 的第二套变量**：媒体查询块
  特异性 (0,2,0) 会静默压过用户显式选择的 `[data-scheme]`（(0,1,0)）
- 移动端不是补丁层，是一等公民：`:touchui` / `:vimui` / `:autoinput` 切换
  输入模式，动作坞 + 数字盘 + tabline 摘要条 + 横向滑切缓冲。设计见
  `docs/MOBILE-INPUT.md`
- 面板一律走 `KBPanel.mount(...)`（`static/panel.js`），它自带 760px
  响应式（overlay 全屏、dock 84% 宽），**新面板不需要自己写媒体查询**
- 编辑器复用同一套 CodeMirror：页面编辑、日记、新建页共用一个实例，
  靠 `state.editing` + `state.current` 区分
- 图谱力导向在 `app.js`（rAF + alpha 冷却，收敛即停），拖动节点置 `_fixed=true`
- 键盘：桌面走 vim（CodeMirror vim keymap）+ `space` which-key + `:` 命令行；
  列表导航（`j`/`k`/`Enter`/计数前缀）是 document 级 capture handler，
  **必须过滤输入焦点、`.CodeMirror-focused`、`isComposing`、touch 模式**
- 体检报告是插件 `health`（`space uh` / `:health` / `/health`），不是视图里的折叠区

### `import_logseq.py` — Logseq 导入

- 全量重建：`_reset_db()` → `init_db()` → 逐页 `import_page()`
- `journals/2026_6_18.md` → 页面名 `2026-06-18`
- `pages/子目录/页面.md` → 页面名 `子目录/页面`
- `_reset_db()` 会删除 `kb.db`、`kb.db-wal`、`kb.db-shm` 三个文件

### `mcp_server.py` — MCP Server（工具定义）

- 17 个工具（读/写/行级修改/版本恢复），直连 SQLite 不走 FastAPI
- **两种传输共用同一份工具定义**：
  - stdio：`python mcp_server.py`（本地 Claude Desktop / 跨设备 over SSH）
  - Streamable HTTP：`python mcp_http.py`（服务端常驻，一份进程服务多个客户端）
  - 选 HTTP 的理由：stdio 下每个客户端都拉起一份 python（本机一度 4 份，各 ~80MB PSS）
- HTTP 端点**不鉴权** → 必须绑 `127.0.0.1`，对外只走 SSH / 受控代理
- `search` 返回体每条带内容摘要片段（`_snippet()`）；追加类工具走共用 `_append()` helper，改追加逻辑只动它
- `get_page` 返回 JSON，`content` 为独立字段——元信息不再混入正文，返回值可安全喂回 `update_page`
- 行级修改用 `append_to_page`（追加）与 `delete_lines`（1-based 行号区间删除），减少全文覆盖风险
- `_sync_refs()` 与 api.py 的 `_sync()` 逻辑对等，改解析逻辑时两边同步改
- 注意陷阱 #12：环境变量白名单问题
- **`frowid` ≠ `f.rowid`**：FTS5 表的行标识是 `f.rowid`，写成 `frowid` 会被当成不存在的列，`OperationalError` 被 LIKE 兜底静默吞掉（[[2026-11-20]] 真踩过，见陷阱 #38）

### `mcp_public.py` — 公开只读子集（对外开放专用）

- 8 个 `public_*` 工具，**没有任何写操作**；给 AI 机器人（QQ 群等）用的第二入口
- 隔离三层，缺一不可：
  1. **无写工具** —— 越权也改不了
  2. **SQL 白名单** —— 每条查询追加 `AND p.id IN (SELECT source_id FROM refs WHERE kind='tag' AND lower(target_name)='public')`。不是靠 prompt 约束模型"不要看"
  3. **统一话术** —— 私有页与不存在页都返回 `Page 'x' not found`，堵存在性探测
- **元数据也要过筛**：`links` / `backlinks` / `tags` 每个都加同样子查询。曾实测私有页名从 `links` 泄露（`树莓-小组进阶` 链向 4 个私有页）
- 只读性在**连接层**强制：`file:...?mode=ro` + `PRAGMA query_only=ON`
- ⚠️ `data/` **不能**挂 `:ro`：KB 是 WAL 模式，SQLite 打开 WAL 库必须能创建/写 `-shm`，只读挂载直接 `unable to open database file`
- 工具名一律带 `public_` 前缀 —— AstrBot 的 `FuncToolManager` 以工具名做 key，同名会互相覆盖
- 标签名由 `KB_PUBLIC_TAG` 决定（默认 `public`）；网页给某页正文加 `#public` 后**立刻生效**，不需重启也不需重建索引（写入时 api.py / mcp_server.py 都会重算 refs）

### `timeutil.py` — 时区固定

- import 即固定进程时区（`TZ` env + `time.tzset()`），默认 `Asia/Shanghai`，`KB_TZ` 可覆盖
- **每个写库的入口都要 import**（`database.py` / `export.py` / `import_logseq.py` / `mcp_server.py`）
- 容器里还需 `TZ=Asia/Shanghai`（compose 已配）——两者缺一不可，见陷阱 #31

### `tests/` / `scripts/`

- `tests/` 单元测试（32 例）：标签/URL 边界、FK 级联、改名只改 link、孤儿 tag 不计入
- 改解析正则后必须跑：`python -m unittest discover -s tests -v`
- `scripts/` 运维脚本：`fix_tz_utc_to_local.py`（历史数据时区修复）、`reindex.py`（重建 refs/properties）

### `export.py` — 导出 CLI

- 三种格式：`markdown`（目录）、`json`（单文件）、`zip`（归档）
- front matter 含 name/created/updated/tags/properties
- API 端点 `/api/export/*` 用相同逻辑（_build_front_matter）

### `main.py` — FastAPI 应用

- CORS 全开（`allow_origins=["*"]`），仅供 Tailscale 内网使用
- `startup` 事件调用 `init_db()` 确保表存在
- `GET /` 和 `GET /page/{slug}` 都返回同一个 `index.html`（SPA 路由）

### `search.py` — 搜索查询构建

- `build_search_query(query)` → `(fts_expr, like_patterns)` 元组
- `rerank_by_name(rows, query)` → 名称相关度重排（精确 > 前缀 > 包含 > 内容），api.py 与 mcp_server.py 共用
- jieba 可选依赖：`try/except import`，无 jieba 时降级为原始查询
- api.py (`_search`) 和 mcp_server.py (`search`) 都调用此函数
- 修改搜索逻辑时只改此函数，两处调用者自动生效

## 常见陷阱

### 1. refs UNIQUE 约束冲突

```python
# ❌ 错误：直接插入 set，大小写不同会冲突
for target in sig["links"]:
    await db.execute("INSERT INTO refs ...", (page_id, target))

# ✅ 正确：先 casefold 去重
seen = {}
for t in sig["links"]:
    if t.casefold() not in seen:
        seen[t.casefold()] = t
for target in seen.values():
    await db.execute("INSERT INTO refs ...", (page_id, target))
```

### 2. 忘记清除 `allNames` 缓存

前端 `savePage()` 成功后必须 `allNames = null`，否则新建页面会被标记为 unresolved。

### 3. FTS5 trigram 短查询

`MATCH` 对 <3 字符的 CJK 查询返回空，必须走 LIKE 兜底。这是 `_search()` 函数已实现的行为。

### 4. CodeMirror 5 vs 6

CDN 上 `6.65.7` 实际是 `5.65.7`。v6 是完全不同的 ESM 架构，不要尝试"升级"。

### 5. 日记是页面

日记不是独立表，是 `name = YYYY-MM-DD` 的页面。不要为日记建单独的表或 API。
日记编辑器复用页面编辑器的 CodeMirror 实例，通过 `currentSaveMode = 'journal' | 'page'` 区分保存目标。`showJournal()` 会替换 `#editor-header` 为日期选择器，`newPage()`/`editPage()` 必须用 `originalHeaderHTML` 恢复。日记保存是「PUT 404 → POST 创建」两级（每天首篇日记页面不存在，纯 PUT 必失败）；日期选择器切换走 `journalSwitchDate()`（存草稿 + 重载内容），**不要**改回只赋值 `journalDate`——那会把 A 日未保存内容写进 B 日。

### 6. refs 写入不完整

只插不删会导致「删掉的引用」残留。每次 save 必须 `DELETE FROM refs WHERE source_id=?` 再重插。

### 7. `[[` 补全的坐标不能依赖「点菜单后的 getCursor()」

旧版 `insertRef` 通过 `lastIndexOf('[[')` 找到行内最后一个 `[[`，替换整行文本；
**不要改回 cursor 范围方式**（`replaceRange(from, cursor)`）—— 点击补全菜单时
CodeMirror 失焦，`getCursor()` 可能返回错误坐标，导致吞字符。

这条教训现在落在 **`static/plugins/hint.js`**。它另外踩过两个坑（已修）：

1. **判定用整行搜 `]]` 永远失败** —— 打完第二个 `[` 时文档里已经是 `[[]]`，
   光标停在两个 `]` 之间。必须只取**光标之前**的子串（`getLine().slice(0, ch)`）
   再找 `[[`
2. **`#` 标签的触发正则写成了 `/#[^\s]/`（要求 `#` 后有空白）** —— 等于永远
   不匹配。正确是 `/(^|[^\p{L}\p{N}_#])#([\p{L}\p{N}_-]*)$/u`

### 8. CodeMirror.Pass 必须 return

```javascript
// ❌ 错误：表达式语句，不返回，CodeMirror 认为 key 已处理
'Up': (cm) => {
  if (...) { ...; return; }
  CodeMirror.Pass;  // 吞掉上下键！
},

// ✅ 正确：必须 return
'Up': (cm) => {
  if (...) { ...; return; }
  return CodeMirror.Pass;
},
```

### 9. Frappe Gantt 日期范围

Frappe Gantt 的 `refresh()` 不重算视图日期范围。切换日期筛选必须销毁旧实例
再重建，否则甘特图仍显示全年时长。

这条现在落在 **`static/plugins/gantt.js`**（lazy 插件，`:gantt` / `/gantt` /
`space tg` 才加载）。旧版的 `ganttChart` 全局变量已不存在。

### 10. 主题切换联动 CodeMirror

`setTheme()` 调用 `cm.setOption('theme', ...)` 时，如果 `cm` 尚未初始化（用户还没打开编辑器），会报错。必须判空：`if (cm) { ... }`。

### 11. FastAPI 路由顺序：path 通配会吞掉子路径

`/pages/{name:path}` 会匹配 `/pages/foo/versions`（name="foo/versions"）。所有 `/pages/{name:path}/xxx` 子路由（versions、rename）**必须注册在通配路由之前**。新增子路由时检查 `@router.get` 出现顺序。

### 12. MCP SDK 只继承白名单环境变量

MCP 客户端 spawn 子进程时只传 PATH、HOME 等白名单变量，**自定义 `KB_DB_PATH` 不会被继承**。必须：客户端配置里显式传 `env`，或依赖默认回退路径 `<脚本目录>/data/kb.db`。

### 13. page_versions 按页面名追踪，不用 FK

版本表故意不用外键（FK + CASCADE 会在删页面时连带删版本）。改名后旧版本仍挂在旧名下，UI 查不到但数据还在——这是设计取舍，不是 bug。

### 14. 改名时 refs 主键冲突预处理

页面 A 同时链接 `[[Old]]` 和 `[[New]]` 时，直接 `UPDATE refs SET target_name='New'` 会撞 `(source_id, target_name, kind)` 主键。必须先删旧名 refs（`_rename_page()` 已处理），PUT 和 PATCH 都走同一辅助函数。

### 15. 前端视图缓存必须写后失效

`cachedFetch()` 缓存 60s。任何写操作（保存/删除/导入/恢复/任务勾选/快速添加）后必须调 `invalidateCache()`，否则界面显示旧数据。新增写操作时记得加。

### 16. URL 路由的 suppressPush 模式

`navigate()` 会 pushState，但 popstate 恢复时不能再次 push（否则历史栈并嗂）。`navigateFromUrl()` 设置 `suppressPush=true` 再调 navigate。newPage/editPage 自己推送路由，navigate('new'/'edit') 不重复推。

### 17. 任务勾选写回用 raw 行匹配

`toggleTaskDone` 不能用行号定位（内容可能已变），必须用 `task.raw` 全行匹配。写回前 bypass 缓存 fetch 最新内容。循环任务勾选不加 done: 而是滚动 due:（recalcRecurring 含追赶逻辑）。

### 18. 甘特图幻影条与颜色索引对齐

循环任务渲染未来 2 个周期为额外幻影条，DOM bar 数量 > 过滤后任务数。
**上色的索引必须用「含幻影元数据的那份任务表」，不能用过滤后的任务表**，
否则颜色错位（现在的实现在 `static/plugins/gantt.js` 里）。任务条颜色从
CSS 变量 `--p1..--p4` 读（改优先级色只改变量，列表圆点和甘特条同时生效）。

### 19. IP 白名单只信任可信代理的 X-Forwarded-For

`ip_whitelist` 中间件仅在**直连 peer 属于 `KB_TRUSTED_PROXIES`**（默认 loopback + `172.16.0.0/12`，即 nginx 本机与 Docker 网桥）时才采信 XFF 第一跳，否则一律用 peer 地址。这样即使端口被直接访问，伪造 XFF 也无法绕过白名单。容器端口另绑 `127.0.0.1`（见 docker-compose.yml）做纵深防御。中间件在 CORS 之后注册（最外层），被拦截请求不会到达 API。

### 20. 移动端适配的 CSS/JS 分离

移动端适配使用 `@media (max-width: 768px)` 断点。新增移动端样式时：
- **CSS 放在 media query 块内**：不要写 `!important` 覆盖桌面样式
- **JS 用 `isMobile()` 判断**：触摸事件、sidebar 模式、FAB 显示等都需要
- **新增面板响应式**：所有固定宽度面板（palette、version、settings、link-preview）都需要在 media query 中加移动样式
- **不要修改桌面行为**：桌面端 sidebar 仍是 narrow 模式，不要改成 drawer
- **触摸事件不替代鼠标事件**：触摸事件和鼠标事件是并存的，都要支持

### 21. mermaid 是懒加载 + 幂等重渲染，v9 别升 v10+

- mermaid 9.4.3（UMD，自托管 `static/vendor/mermaid/`）首次遇到 `.mermaid` 块才加载（`ensureMermaidLoaded`）。**不要升级 v10/v11**：v10+ 只发 ESM 构建，内嵌浏览器加载不了 ESM（实测动态 import 超时），除非先把 ESM bundle 自托管到 static/。
- `renderMermaidBlocks(root)` 是幂等的：原文存在 `dataset.src`，重渲染前必须 `textContent = src` 重置 + **`removeAttribute('data-processed')`**（v9 init 跳过已处理节点，漏了这步主题切换后图表会变成纯文本）。
- 主题切换（setTheme）会 `initMermaidTheme()` 重新注入 CSS 变量并全量重渲染；新增主题变量只在 `initMermaidTheme` 里改。
- 编辑预览走 300ms 防抖（updatePreview._mmd），别改成同步渲染——v9 init 是重同步操作。
- 语法错误的块由 v9 就地渲染错误图，不要用 try/catch 替换成静默失败。

### 22. markdown-it 规则注册顺序 + 嵌入引用机制

- `md.inline.ruler.before(名字, ...)` 在**名字对应的规则不存在时直接 throw**，会杀死整个 `<script>`（症状：首载白屏但函数手动调用正常——函数声明提升仍在）。新增规则注册时必须放在被引用规则**注册语句之后**（`kbembed` 的注册在 wikilink 之后，靠 `before('wikilink')` 插到它前面——先定义 `kbEmbedRule` 函数再注册，就是这个原因）。
- 嵌入引用 `![[页面]]`：inline 规则只输出 `.kb-embed` 占位 div，内容由 `fillEmbeds(root, depth)` 异步拉取填充（`cachedFetch`），depth ≥ 2 显示"层级上限"防循环；缺失页占位 `.kb-embed-create` 走全局 `data-create` 委托（newPage + 预填名字）。
- 首页看板：`showHome` 先试 `getDashboardPage()`（localStorage `kb-dashboard-page`，空 = 禁用），存在则渲染 + `fillEmbeds` + `renderMermaidBlocks`。看板就是普通页面，没有专用 API。

### 23. 日记保存必须 PUT 失败后 POST

日记是“以日期为名的页面”，可能尚不存在。`PUT /api/pages/{name}` 对不存在的页面返回 404，所以 `saveJournal()` 必须先 PUT、`resp.status === 404` 时回退 POST `/api/pages`。页面编辑器（`savePage()`）靠 `currentName` 区分 POST/PUT，日记没有这个名字，不能直接复用那条路径。

### 24. pushRoute 的参数必须是数组

`routeToUrl()` 用 `args[0]` 取参，所以 `pushRoute()` 内部要把单值包装成数组：

```js
const arr = Array.isArray(args) ? args : (args == null ? [] : [args]);
```

否则 `navigate('page', 'Ideas')` 会把字符串当字符数组，URL 被截成 `/page/I`，刷新/分享全 404。`navigate`、`editPage` 都传单值。

### 25. Ctrl/Cmd-S 要绑在 document 而非 CodeMirror

CodeMirror 的 `extraKeys` 只在编辑器有焦点时触发。用户常在页面名输入框里按 Ctrl-S，此时会静默无反应。统一绑在 `document` 的 keydown 上，并加 `editor.style.display === 'block'` 守卫，不要再在 `extraKeys` 里重复绑（会双写）。

### 26. 前端依赖必须自托管（vendor），不能依赖 CDN

所有库在 `static/vendor/`（CodeMirror / markdown-it / Frappe Gantt / Mermaid），由应用自己提供。**不要改回 CDN**：

- CDN 不可达时（离线/内网/墙）`<script>` 加载失败，`window.markdownit is not a function` 会在 `let md = window.markdownit({...})` 处**中断整个内联脚本**，页面白屏且无任何提示
- 依赖是**浏览器**加载的，服务器能访问 CDN 不代表客户端能

前缀处理：`templates/index.html` 头部的 bootstrap 用 `document.write` 注入带前缀的资源 URL，并把结果写入 `window.__KB_BASE__`；`detectBase()` 优先读它。后端 `main.py` 的 `strip_static_prefix` 中间件把 `/<prefix>/static/...` 和 `/<prefix>/api/...` 重写为 `/static/...`、`/api/...`。检测必须在**未解码**的 `raw_path` 上做——`scope["path"]` 已解码，页面名里的 `%2F` 会变回 `/`，名为 `api/x`、`static/y` 的页面会被误重写（在 path 上检测 `/static/` 的旧版就有这个潜在 bug）。`raw_path` 保留 `%2F`，字面 `/static/`、`/api/` 只可能是部署前缀。两种部署都不用改 nginx。

### 27. 异步视图加载必须防竞态

视图切换都是 `async + await fetch`。若不加守卫，慢请求会覆盖后点的页面（已实测复现：先点 A 后点 B，A 的响应后到就把 B 盖掉）。

机制：`hideAll()` 递增 `viewToken` 并**返回**它；各加载器捕获后，在每个 `await` 之后校验：

```js
async function loadPage(name) {
  const token = hideAll();          // 递增并返回新令牌
  page = await cachedFetch(...);
  if (token !== viewToken) return;  // 已有更新的视图，丢弃本次结果
  ...渲染...
}
```

已覆盖：`showHome` / `showAllPages` / `loadPage` / `loadAndEdit` /
`showJournal` / `showTagPages` / `doSearch` / `showGraph`。
`renderTodo` 因 tab 切换也会重叠，用独立的 `todoToken`。

新增异步视图时照抄这个模式，否则会重新引入这个 bug。

### 28. 不要用 monkey-patch 包装函数（会吞返回值）

移动端适配曾这样给 `hideAll` 挂 FAB 刷新（旧单文件模板时代，
`updateFABVisibility` 已随重构消失，但结论不变）：

```js
// ❌ 反面教材
const _origHideAll = hideAll;
window.hideAll = function() { _origHideAll(); updateFABVisibility(); };
```

`_origHideAll()` 的返回值被丢掉，于是 `hideAll()` 永远返回 `undefined`，
而 `const token = hideAll()` 就全是 `undefined` —— 上一条的防线因此全部失效，
且**不报任何错**（表现为页面永远不渲染）。

**正确做法**：直接在函数体内显式调用副作用（函数声明会提升，定义在后面
也能调），不要包装。新增类似"副作用"时同理，写进目标函数而不是套壳。

新架构里同类风险是**插件的 `api.on(...)` 钩子**：如果钩子内部抛异常，
别让它污染主流程 —— 插件应该自己 try/catch 并 `api.ui.toast` 报错。

### 29. 不要静默吞异常

`except Exception: pass` 曾用在 FTS 查询上（查询写错时无从发现）。
正确做法：捕窄异常类型 + 记日志 + 明确降级：

```python
except sqlite3.OperationalError as e:
    log.warning("FTS match failed for %r: %s — falling back to LIKE", fts_expr, e)
```

注意：MCP 走 stdio，**stdout 是协议通道**，日志一律用 `logging`（默认 stderr），
绝不能用 `print`。

### 30. 容器 uid 必须与 data/ 属主一致

Dockerfile 用 `USER ${KB_UID}:${KB_GID}`（默认 1000）降权。uid 不匹配时写入会失败，
所以 `main.py` 启动时用 `_assert_data_dir_writable()` 自检并**报错退出**，
提示正确命令：

```bash
KB_UID=$(id -u) KB_GID=$(id -g) docker compose up -d --build
```

不要用 `useradd` 创建命名用户再 `USER name` —— 直接用数字 uid:gid 更简单，
且不依赖基础镜像里是否有 `useradd`。

### 31. sw.js / manifest 必须放在应用根路径

Service Worker 的**作用域由其自身 URL 推导**，manifest 的 `start_url` /
`scope` / 图标路径也都是**相对 manifest URL**解析。若放在 `/static/` 下：
SW 作用域会变成 `/static/`（控制不到应用），manifest 的 `start_url` 也会指错。

所以 `main.py` 的 SPA catch-all 里特判这两个文件名，在**任意前缀**下从应用根
提供（`/sw.js`、`/kb/sw.js`）。`_is_app_root_file()` 会排除
`<已知路由>/sw.js`（如 `/page/sw.js`），避免真的有页面叫这个名字时被劫持。

manifest 里的路径全部用相对形式（`start_url: "."`、图标 `static/icons/...`），
这样 `/` 和 `/kb/` 共用同一份文件，不需要服务端注入前缀。

改了外壳内容记得递增 `static/sw.js` 的 `VERSION`。

### 32. 容器必须显式设时区，否则时间戳差 8 小时

数据库存的是**无时区的本地时刻**（`datetime('now','localtime')`），前端按浏览器
本地渲染。容器**没有 `TZ` env 时 glibc 回落 UTC** → SQLite 写入的是 UTC 时刻
→ 浏览器按本地渲染 → 全站时间戳差 8 小时。

修复是**两处都要**：
1. compose 加 `TZ=Asia/Shanghai`
2. 各写库入口 `import timeutil`（见 #33）

光设 `TZ` 不够——进程内的 SQLite 连接仍可能不跟随；光 import 不够——容器
启动时 glibc 已经是 UTC。

实测：缺 `TZ` 时 SQLite 写 `06:53`，`import timeutil` 后写 `14:53`（= 真实 CST）。

### 33. 写库的入口都要 import timeutil

```python
import timeutil   # noqa: F401 — import 即固定时区
```

`timeutil.py` 在 import 时设 `TZ` env 并调 `time.tzset()`，让 Python 与 SQLite
的 `localtime` 一致（SQLite 的 `localtime` 跟 C 库走）。默认 `Asia/Shanghai`，
`KB_TZ` 可覆盖。

已覆盖的入口：`database.py` / `export.py` / `import_logseq.py` / `mcp_server.py`。
**新增写库脚本时别忘了**——历史数据可用 `scripts/fix_tz_utc_to_local.py` 修。

### 34. SQLite 默认不启用外键

`PRAGMA foreign_keys` **默认 OFF**，此时 schema 里的
`REFERENCES ... ON DELETE CASCADE` 被忽略 → 删页后 `refs` 残留 → 孤儿行
仍出现在标签列表（「无人使用的标签没有自动删除」）。

- `database.py:get_db()` 已设 `PRAGMA foreign_keys=ON`
- `mcp_server.py:_db()` 也必须设（它用原生 `sqlite3` 直连，不走 `get_db`）
- 新增裸 sqlite3 连接时记得带上

### 35. 标签规则前后端必须一致（Python \\w 匹配 CJK，JS \\w 不匹配）

`extract_tags()` 的 lookbehind 必须是 **ASCII-only**：

```python
TAG_RE = re.compile(r"(?<![0-9A-Za-z_#])#(?!\+)([\w-]+)")   # 对
```

若写成 `(?<![\w#])`，Python 的 `\w` 会匹配 CJK → `第12周#Monday` 被后端拒，
而前端（JS `\w` 是 ASCII-only）仍然补全 → 前后端规则漂移。

前端对应：`TAG_TRIGGER_RE = /(^|[^#0-9A-Za-z_])#([\p{L}\p{N}_-]*)$/u`
（正文用 `\p{L}\p{N}` 以支持 CJK 标签名如 `#采购`）

另外 URL 里的 `#fragment` 不是标签——`extract_tags()` 先用 `URL_RE` 剥离 URL。

改任何一侧都要同步另一侧，并跑 `python -m unittest discover -s tests`。

### 36. 改名只重写 kind='link'，不能碰 tag

```sql
UPDATE refs SET target_name = ? WHERE target_name = ? AND kind = 'link'
```

标签来自**页面内容**（`#画画`），不是页面名。之前没加 `kind` 限定，改名时
把标签也一起改写了 → 页面「画画」改名后，内容里的 `#画画` 变成幻影标签
「绘画-练习日志」。

### 37. MCP 传输选型：stdio vs HTTP

- **stdio**（`python mcp_server.py`）：每个客户端拉起一份进程。适合本地
  Claude Desktop、跨设备 over SSH。
- **HTTP**（`python mcp_http.py`）：一份常驻服务多个客户端。**生产用这个**——
  stdio 下本机一度同时有 4 份，各 ~80MB PSS。

HTTP 端点**不鉴权**，所以必须绑 `127.0.0.1`（compose 里 `127.0.0.1:8092:8081`），
对外只走 SSH / 受控代理。接入方式见 `MCP_REMOTE.md`。

三个端点共存（[[2026-11-20]] 起）：

| 端点 | 端口 | 工具 | 用途 |
|---|---|---|---|
| `kb` (uvicorn) | `127.0.0.1:8080` | — | 网页 UI + REST |
| `kb-mcp` (`mcp_http.py`) | `127.0.0.1:8092` | 17 读写 | 主人用（AstrBot admin） |
| `kb-mcp-public` (`mcp_public.py`) | `127.0.0.1:8100` | 8 只读 | 给别人用（AstrBot member），只返回 `#public` 页 |

### 38. `frowid` 不是列名（FTS5 的 rowid 要写成 `f.rowid`）

```sql
-- ✗ 报 no such column: frowid
JOIN pages p ON p.id = frowid
-- ✓
JOIN pages p ON p.id = f.rowid
```

FTS5 虚拟表的隐式 `rowid` 必须带表别名限定。写错了会抛 `sqlite3.OperationalError`，
而 `_search()` / MCP `search()` 都有 `except OperationalError → LIKE 兜底`，
**错误被静默吞掉**，表现为「搜索能出结果但一直走慢路径、不走索引」。[[2026-11-20]] 实测得知。

排查这一类"静默降级"的正确手法：单独建一个临时库跑原 SQL，看它到底抛不抛。

### 39. FastMCP HTTP 必须 `stateless_http=True`

```python
mcp.run(transport="http", host=..., port=..., path=..., stateless_http=True)
```

默认**有状态**：服务端给每个客户端发 `mcp-session-id`，之后每个请求都要带它。
服务端一重启，客户端手里的旧 id 就换来 `404 Session not found`。
AstrBot 的 MCP 客户端只在 `anyio.ClosedResourceError` 时重连，404 被包成
`McpError("Session terminated")` **不触发重连** → 工具永久坏掉，
必须手动重启 AstrBot 才能恢复。

即"改 KB 侧任何东西都要连带重启机器人"，耦合很蠢。

改 stateless 后：`initialize` 不返回 session-id，带垃圾 id 或不带 id 都返回 200。
回归测试：只重启 `kb-mcp-public`、不重启 AstrBot，公开工具 7 次 0 失败。

### 40. 对外开放的只读端点：SQL 白名单，不是 prompt 约束

`mcp_public.py` 的隔离必须落在 SQL 层，让模型**物理上查不到**私有页：

```sql
AND p.id IN (SELECT source_id FROM refs WHERE kind='tag' AND lower(target_name)=?)
```

不能只写一句「请不要读取私有页面」的 system prompt —— 换个模型就失效。

另外 **`links` / `backlinks` / `tags` 三个元数据字段都要过同样的子查询**。
只过滤 `content` 是不够的：曾实测 `树莓-小组进阶` 的 `links` 字段把 4 个
私有页名全列出来了。页面作者授权的是内容，不是"它链向了谁"。

话术也要统一：私有页与不存在页都返回 `Page 'x' not found`，
否则这一端点就成了存在性探测接口。

## 修改流程

1. **读相关文件**：改哪层读哪层，不要凭记忆改
2. **保持设计原则**：单一命名空间、大小写不敏感、delete-then-insert
3. **验证**：改完后跑 ad-hoc 验证脚本（见下方）
4. **更新 README**：如果改了 API 或交互行为，同步更新 README.md
5. **git commit**：`git add -A && git commit -m "type: description"`

## 验证脚本模板

```python
# /tmp/hermes-verify-*.py — 临时验证脚本，用完即删
import sys
sys.path.insert(0, "/home/user/pi-working/kb-ui")   # 分支 worktree（生产挂的就是它）
from parse import parse

# 1. 解析测试
content = "[[Foo]] [[foo]] #tag"
sig = parse(content)
assert len(sig["links"]) == 2  # Python set 不去重大小写

# 2. 去重逻辑
seen = {}
for t in sig["links"]:
    if t.casefold() not in seen:
        seen[t.casefold()] = t
assert len(seen) == 1  # 归并后只有 1 个

# 3. 数据库约束（如需）
# import sqlite3, tempfile
# ... 建表 → 插入 → 验证 UNIQUE 约束
```

## Git 规范

- 提交信息格式：`type: description`
  - `feat:` 新功能
  - `fix:` 修复
  - `refactor:` 重构
  - `docs:` 文档
  - `chore:` 杂项
- 不要提交 `data/kb.db`（已在 `.gitignore`）
- 不要提交 `__pycache__/`、`venv/`

## 部署注意事项

- **推荐 Docker Compose**：`docker compose up -d --build` 更新，数据库挂载到宿主机 `./data`
- 或直接 `uvicorn main:app --host 0.0.0.0 --port 8080`
- 通过 Tailscale 内网访问，CORS `*` 仅供内网使用（不要暴露公网）
- 数据库备份：`sqlite3 data/kb.db "PRAGMA wal_checkpoint" && cp data/kb.db backup/`
- 环境变量 `KB_DB_PATH` 指定数据库路径。`database.py` 里的默认值是
  `/home/user/services/kb/data/kb.db`，但**那个目录不存在** —— 真实路径是
  生产 `~/services/kb-docker/data/kb.db`，容器内是 `/app/data/kb.db`。
  所有容器都用 `KB_DB_PATH=/app/data/kb.db` 覆盖，别照默认值找文件

## 不要做的事

- ❌ 不要引入 slug/title 分离
- ❌ 不要手动维护 pages_fts（触发器会处理）
- ❌ 不要在 refs 写入时跳过 delete
- ❌ 不要尝试"升级" CodeMirror 6
- ❌ 不要为日记建独立表
- ❌ 不要提交数据库文件到 git
- ❌ 不要在 API 层做大小写敏感比较
- ❌ 不要在 extraKeys 里写 `CodeMirror.Pass;` 而不 return（会吞上下键）
- ❌ 不要在 `@media` 外写移动端专属样式（触屏入口/动作坞等）
- ❌ 不要忘记新增面板的移动响应式（用 `KBPanel.mount` 就自带，自己写 div 就不会）
- ❌ 不要让「bottom 锚定、向上生长」的浮层没有 `max-height` —— 装不下就长到
  屏幕外，选项等于不存在（`#fxdock` / `#whichkey` / `#numgrid` 都栽过）；
  限高必须用 `dvh` 覆盖 `vh`，否则软键盘弹起时照样顶出去
- ❌ 不要把触屏浮层的关闭只做在「按 Esc」上（手机没有 Esc），必须有遮罩点击/×
- ❌ 不要在顶层 `<span>` 里用 `innerHTML` 拼未转义的页面名（`escapeHtml` 是必须的）
- ❌ 不要改 `static/sw.js` 的静态资源策略而不 bump `VERSION`（客户端会一直跑旧壳）
- ❌ 不要直接修改 `_search()` 的 SQL（搜索逻辑在 `search.py` 的 `build_search_query()` 中，改了要同时改 API 和 MCP 两处调用者）
- ❌ 不要在 `search.py` 里假设 jieba 一定存在（必须 try/except import，无 jieba 时降级）
