# AGENTS.md — KB 项目开发指南

> 本文件面向 AI 开发助手。修改本仓库前请阅读本文件，避免违背设计原则的修改。

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
- 正则不要动，改了要跑解析单测

### `routers/api.py` — REST API

- `_sync(db, page_id, content)`：解析内容 → delete 旧 refs/properties → insert 新的
- `_search(db, q, limit, offset)`：trigram FTS 优先，<3 字符走 LIKE 兜底
- 所有端点 try/finally 确保 `db.close()`
- `update_page` 的改名逻辑：新名不存在才更新 name + 重写 refs.target_name

### `templates/index.html` — 前端

- CodeMirror 5（不是 6，CDN 6.65.7 实际是 5.65.7）
- markdown-it 自定义 `wikilink` 内联规则：`[[target]]` → `<a data-page="target">`
- Frappe Gantt 0.6.1（甘特图渲染，CDN）
- 全局事件委托处理所有点击（`[data-page]`、`[data-tag]`、`[data-nav]`）
- `navigate(target, ...args)` 是唯一路由入口
- 主题：CSS 变量 `[data-theme="dark"/"light"]`，localStorage 持久化
- 日记编辑器复用页面编辑器，`currentSaveMode` 区分保存目标
- 任务管理：`parseTaskLine()` 解析任务语法，Frappe Gantt 渲染甘特图

### `import_logseq.py` — Logseq 导入

- 全量重建：`_reset_db()` → `init_db()` → 逐页 `import_page()`
- `journals/2026_6_18.md` → 页面名 `2026-06-18`
- `pages/子目录/页面.md` → 页面名 `子目录/页面`
- `_reset_db()` 会删除 `kb.db`、`kb.db-wal`、`kb.db-shm` 三个文件

### `mcp_server.py` — MCP Server（stdio）

- 13 个工具（读/写/版本恢复），直连 SQLite 不走 FastAPI
- `_sync_refs()` 与 api.py 的 `_sync()` 逻辑对等，改解析逻辑时两边同步改
- 注意陷阱 #12：环境变量白名单问题

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
日记编辑器复用页面编辑器的 CodeMirror 实例，通过 `currentSaveMode = 'journal' | 'page'` 区分保存目标。`showJournal()` 会替换 `#editor-header` 为日期选择器，`newPage()`/`editPage()` 必须用 `originalHeaderHTML` 恢复。

### 6. refs 写入不完整

只插不删会导致「删掉的引用」残留。每次 save 必须 `DELETE FROM refs WHERE source_id=?` 再重插。

### 7. insertRef 用整行替换

`insertRef` 通过 `lastIndexOf('[[')` 找到行内最后一个 `[[`，替换整行文本。**不要改回 cursor 范围方式**（`replaceRange(from, cursor)`）——点击补全菜单时 CodeMirror 失焦，`getCursor()` 可能返回错误坐标，导致吞字符。

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

Frappe Gantt 的 `refresh()` 不重算视图日期范围。切换日期筛选必须 `ganttChart = null` 后重建实例，否则甘特图仍显示全年时长。

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

### 18. 甘特图幻影条与 colorGanttBars 索引对齐

循环任务渲染未来 2 个周期为额外幻影条，DOM bar 数量 > 过滤后任务数。`colorGanttBars` 必须用 `ganttRenderTasks`（含幻影元数据）而非 filterGanttTasks()，否则颜色错位。

### 19. IP 白名单信任 X-Forwarded-For

`ip_whitelist` 中间件优先读 XFF 第一跳作为真实 IP。这要求应用只能从受信代理（nginx/Tailscale）访问——如果应用端口直接暴露且攻击者可伪造 XFF，白名单可被绕过。生产环境确保端口不直接对公网开放。中间件在 CORS 之后注册（最外层），被拦截请求不会到达 API。

### 20. 移动端适配的 CSS/JS 分离

移动端适配使用 `@media (max-width: 768px)` 断点。新增移动端样式时：
- **CSS 放在 media query 块内**：不要写 `!important` 覆盖桌面样式
- **JS 用 `isMobile()` 判断**：触摸事件、sidebar 模式、FAB 显示等都需要
- **新增面板响应式**：所有固定宽度面板（palette、version、settings、link-preview）都需要在 media query 中加移动样式
- **不要修改桌面行为**：桌面端 sidebar 仍是 narrow 模式，不要改成 drawer
- **触摸事件不替代鼠标事件**：触摸事件和鼠标事件是并存的，都要支持

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
sys.path.insert(0, "/home/user/services/kb")
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
- 环境变量 `KB_DB_PATH` 指定数据库路径（默认 `/home/user/services/kb/data/kb.db`）

## 不要做的事

- ❌ 不要引入 slug/title 分离
- ❌ 不要手动维护 pages_fts（触发器会处理）
- ❌ 不要在 refs 写入时跳过 delete
- ❌ 不要尝试"升级" CodeMirror 6
- ❌ 不要为日记建独立表
- ❌ 不要提交数据库文件到 git
- ❌ 不要在 API 层做大小写敏感比较
- ❌ 不要把 insertRef 改回 cursor 范围方式（会吞字符）
- ❌ 不要在 extraKeys 里写 `CodeMirror.Pass;` 而不 return（会吞上下键）
- ❌ 不要用 `ganttChart.refresh()` 换日期范围（不重算视图，需销毁重建）
- ❌ 不要在桌面端使用 `sidebar.classList.add('open')`（drawer 模式仅限移动端）
- ❌ 不要在 `@media` 外写移动端专属样式（FAB、preview tab 等）
- ❌ 不要忘记新增面板的移动响应式（所有固定宽度面板都需要）
- ❌ 不要直接修改 `_search()` 的 SQL（搜索逻辑在 `search.py` 的 `build_search_query()` 中，改了要同时改 API 和 MCP 两处调用者）
- ❌ 不要在 `search.py` 里假设 jieba 一定存在（必须 try/except import，无 jieba 时降级）
