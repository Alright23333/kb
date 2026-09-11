# Knowledge Base (KB)

一个自托管的双链笔记系统，SQLite 后端 + FastAPI REST API + 原生 JS 单页 Web UI。
设计目标：**人能用，AI 也能用**——多设备通过 Tailscale 访问，FastAPI 让 AI agent 直接读写知识库。

## 设计原则

- **单一命名空间**：页面名 = 链接目标 = URL 键。`[[Foo]]` 指向的页面就叫 `Foo`，不存在 slug/title 分离。
- **Markdown 是唯一权威源**：存储引擎只做「内容 → 结构化索引」，不引入 block tree。
- **大小写不敏感**：`name` 和 `refs.target_name` 都是 `COLLATE NOCASE`，`[[Foo]]` 和 `[[foo]]` 指向同一页。
- **无冲突写入**：每次 save 对当前页 delete + 重插 refs/properties，确保「删掉的引用」也被清除。

## 项目结构

```
kb/
├── main.py              # FastAPI 应用、路由、启动事件
├── database.py          # SQLite schema、连接池、FTS 触发器
├── routers/
│   └── api.py           # REST API 端点（pages/tags/graph/search）
├── parse.py             # Markdown 解析：[[links]]、#tags、key:: value
├── templates/
│   └── index.html       # 单页 Web UI（CodeMirror 5 + markdown-it）
├── static/              # 静态资源（预留）
├── import_logseq.py     # 从 Logseq 文件版导入（全量重建）
├── run.sh               # 启动脚本
├── data/
│   └── kb.db            # SQLite 数据库（首次启动自动创建）
└── venv/                # Python 虚拟环境
```

## 数据模型

### 表结构

```sql
-- 页面：name 是唯一键（标题 = 链接目标 = URL 键）
CREATE TABLE pages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL COLLATE NOCASE UNIQUE,
    content    TEXT NOT NULL DEFAULT '',          -- 权威 markdown
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- 引用：链接和标签统一落这张表
CREATE TABLE refs (
    source_id   INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    target_name TEXT NOT NULL COLLATE NOCASE,     -- 目标页名（可不存在 = 未解析）
    kind        TEXT NOT NULL DEFAULT 'link',      -- 'link' | 'tag'
    PRIMARY KEY (source_id, target_name, kind)
);
CREATE INDEX idx_refs_target ON refs(target_name);  -- 反向链接走这里

-- 属性：key:: value
CREATE TABLE properties (
    page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    key     TEXT NOT NULL,
    value   TEXT,
    PRIMARY KEY (page_id, key)
);

-- 全文搜索：FTS5 + trigram tokenizer（支持 CJK n-gram）
CREATE VIRTUAL TABLE pages_fts USING fts5(
    name, content,
    content='pages',
    content_rowid='id',
    tokenize='trigram'
);
```

### 触发器（自动同步 FTS 索引）

```sql
CREATE TRIGGER pages_ai AFTER INSERT ON pages BEGIN
    INSERT INTO pages_fts(rowid, name, content) VALUES (new.id, new.name, new.content);
END;
CREATE TRIGGER pages_ad AFTER DELETE ON pages BEGIN
    INSERT INTO pages_fts(pages_fts, rowid, name, content)
    VALUES ('delete', old.id, old.name, old.content);
END;
CREATE TRIGGER pages_au AFTER UPDATE ON pages BEGIN
    INSERT INTO pages_fts(pages_fts, rowid, name, content)
    VALUES ('delete', old.id, old.name, old.content);
    INSERT INTO pages_fts(rowid, name, content) VALUES (new.id, new.name, new.content);
END;
```

### 核心查询模式

| 查询 | SQL |
|------|-----|
| 正向链接 | `SELECT target_name FROM refs WHERE source_id=? AND kind='link'` |
| 反向链接 | `SELECT p.name FROM refs r JOIN pages p ON p.id=r.source_id WHERE r.kind='link' AND r.target_name=? COLLATE NOCASE` |
| 标签聚合 | `SELECT target_name, COUNT(*) FROM refs WHERE kind='tag' GROUP BY target_name` |
| 未解析判定 | `SELECT 1 FROM pages WHERE name=? COLLATE NOCASE` 查不到就灰显 |
| FTS 搜索 | `SELECT p.name FROM pages_fts f JOIN pages p ON p.id=f.rowid WHERE pages_fts MATCH ? ORDER BY rank` |
| 短查询兜底 | `WHERE name LIKE ? OR content LIKE ?`（<3 字符的 CJK 查询） |

## 交互实现

### 导航系统

```
┌─────────────────────────────────────────────────────┐
│ Sidebar                    │ Main                    │
│ ┌──────────────────────┐   │ ┌─────────────────────┐ │
│ │ 📚 Knowledge Base    │   │ │ Search │ Search │ + │ │
│ ├──────────────────────┤   │ ├─────────────────────┤ │
│ │ 🏠 首页              │   │ │                     │ │
│ │ 📓 日记              │   │ │  (content area)     │ │
│ │ 🕸️ 图谱              │   │ │                     │ │
│ ├──────────────────────┤   │ │                     │ │
│ │ Tags                 │   │ │                     │ │
│ │ #tag1 (5)            │   │ │                     │ │
│ │ #tag2 (3)            │   │ │                     │ │
│ └──────────────────────┘   │ └─────────────────────┘ │
└─────────────────────────────────────────────────────┘
```

- **全局事件委托**：`document.addEventListener('click')` 拦截 `[data-page]`、`[data-tag]`、`[data-nav]` 点击
- **`navigate(target, ...args)`**：唯一路由入口，控制 `home-view` / `page-view` / `editor` 三个容器的 `display`
- **搜索栏**：输入 `[[页面名]]` 直达页面；其他文本走 FTS 搜索

### 页面查看

1. `loadPage(name)` → `GET /api/pages/{name}` → 返回页面 + tags + links + backlinks
2. `renderContent(content)` → `md.render()` → markdown-it 渲染 HTML
3. `markUnresolved()` → 对比 `allNames` Set，给不存在的页面链接加 `.unresolved` 类（灰色虚线下划线）
4. 反向链接区域：列出所有 `[[name]]` 指向当前页的页面

### 编辑器

```
┌─────────────────────────────────────────────────────┐
│ Editor Header: [ 页面名输入框 (即 [[链接目标]]) ]    │
├──────────────────────┬──────────────────────────────┤
│ CodeMirror 5         │ Live Preview                 │
│ (markdown mode,      │ (markdown-it rendered)       │
│  dracula theme,      │                              │
│  line numbers,       │                              │
│  bracket matching)   │                              │
├──────────────────────┴──────────────────────────────┤
│ Footer: 正文中用 [[链接]] 和 #标签  [保存 (Ctrl+S)]  │
└─────────────────────────────────────────────────────┘
```

- **初始化**：`CodeMirror.fromTextArea()` 配置 markdown 模式 + dracula 主题
- **实时预览**：`cm.on('change')` → `updatePreview()` → `renderContent(cm.getValue())`
- **保存**：`Ctrl+S` 或按钮 → `savePage()` → POST/PUT → 清除 `allNames` 缓存 → `navigate('page', name)`
- **重命名**：`PUT /api/pages/{old_name}` + body `{name: new_name, content}` → API 更新 name + 重写 refs.target_name

### 斜杠命令

- **触发**：在编辑器行首输入 `/`，按 Enter 时检测 `/(\w*)$`
- **菜单**：`slashCommands` 数组（h1-h3、ul、ol、todo、quote、code、inline、bold、italic、link、hr）
- **插入逻辑**：
  - `insertPrefix(cm, prefix)`：删除 `/`，在行首插入前缀（如 `# `、`- `）
  - `insertBlock(cm, before, after)`：删除 `/`，在光标处插入 `before + after` 并定位光标到中间

### 引用补全

- **触发**：输入 `[[` 后检测 `/\[\[([^\]]*)$/`
- **数据源**：首次触发时 `GET /api/pages?limit=200` 缓存到 `allPages`
- **过滤**：`p.name.toLowerCase().includes(q)`，最多显示 8 条
- **插入**：`insertRef(name)` → 替换 `[[` 到光标的内容为 `name]]`

### 日记

- **本质**：页面名是日期（`YYYY-MM-DD`）的页面
- **UI**：`home-view` 内渲染日期选择器 + 纯 `<textarea>`（无 CodeMirror）
- **保存**：`saveJournal(exists)` → POST/PUT `/api/pages/{date}`

### 知识图谱

- **数据**：`GET /api/graph` → `{nodes: [{name}], edges: [{source, target}]}`
- **布局**：力导向模拟（100 次迭代，弹簧模型 + 随机扰动 + 边界约束）
- **渲染**：内联 SVG，节点可点击导航

## 解析层 (`parse.py`)

```python
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")        # [[target]] 或 [[target|alias]]
TAG_RE = re.compile(r"(?<!#)#(?!\+)([^\s#\[\]()]+)")  # #tag（排除标题和 org 标记）
PROPERTY_RE = re.compile(r"^\s*-?\s*([^\s:：]+)::\s*(.*)$")  # key:: value
```

- `extract_links(content)` → `set[str]`：解析 `[[...]]`，取 `|` 前的目标
- `extract_tags(content)` → `set[str]`：先剥离 `[[...]]` 避免重复计数，再匹配 `#tag`
- `extract_properties(content)` → `dict[str, str|None]`：逐行匹配 `key:: value`
- `parse(content)` → `{"links": set, "tags": set, "properties": dict}`

**大小写去重**：`refs.target_name` 是 `COLLATE NOCASE`，Python `set` 不去重大小写。
插入前用 `casefold()` 字典归并，防止 `[[Foo]]` + `[[foo]]` 触发 UNIQUE 约束。

## API 参考

### Pages

| Method | Path | Body | Description |
|--------|------|------|-------------|
| `GET` | `/api/pages` | — | 列表（`?tag=`、`?search=`、`?limit=`、`?offset=`） |
| `GET` | `/api/pages/names` | — | 所有页面名（轻量，用于未解析检测） |
| `GET` | `/api/pages/{name}` | — | 页面详情 + tags + links + backlinks + properties |
| `POST` | `/api/pages` | `{name, content}` | 创建页面 |
| `PUT` | `/api/pages/{name}` | `{name?, content?}` | 更新页面（name 变更时重写引用） |
| `DELETE` | `/api/pages/{name}` | — | 删除页面 |

### Tags

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/tags` | 所有标签 + 计数 |
| `GET` | `/api/tags/{name}/pages` | 按标签筛选页面 |

### Graph

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/graph` | 全图（nodes + edges） |

## 启动

```bash
cd /home/user/services/kb
./run.sh
# 或
uvicorn main:app --host 0.0.0.0 --port 8080
```

访问 `http://100.88.251.64:8080`（Tailscale 内网）。

## 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python 3.11+, FastAPI, aiosqlite |
| 前端 | Vanilla JS (ES2020+), CodeMirror 5, markdown-it 13 |
| 数据库 | SQLite 3.35+ (WAL mode, FTS5 trigram) |
| 服务器 | Uvicorn (ASGI) |

## 后续优化路线图

### P0 — 体验阻塞

| 优化 | 影响 | 复杂度 |
|------|------|--------|
| **无认证**：CORS `*` + API 裸奔。暴露到公网即被社工。需加 token 中间件或 IP 白名单。 | 安全 | 低 |
| **无持久化**：uvicorn 非 systemd 服务，重启后需手动拉起。需写 `.service` 文件或 Docker compose。 | 可靠性 | 低 |
| **日记体验割裂**：纯 `<textarea>`，无 CodeMirror、无斜杠命令、无引用补全。需把编辑器组件抽成复用函数。 | 体验 | 中 |

### P1 — 功能补全

| 优化 | 影响 | 复杂度 |
|------|------|--------|
| **重命名脆弱**：`PUT` 改 name 靠 `PageUpdate.name` 可选字段，中途失败会出重复页。需独立 `PATCH /pages/{name}/rename` 端点 + 事务。 | 数据完整性 | 中 |
| **无分页**：`pages/names` 返回全部名称，416 页时 ~10KB，千页时需改分页或懒加载。 | 性能 | 低 |
| **图谱无交互**：无缩放/拖拽/固定位置。需引入 D3.js 或 cytoscape.js。 | 体验 | 中 |
| **无 block reference**：`((uuid))` 不支持。需加 `blocks` 表 + 解析 `((...))`。 | 功能 | 高 |
| **无页面历史**：无 undo、无版本回溯。需加 `page_versions` 表 + 触发器。 | 安全 | 中 |

### P2 — 进阶

| 优化 | 影响 | 复杂度 |
|------|------|--------|
| **CodeMirror 5 → 6**：v6 是 ESM 架构，需打包器。当前 v5 够用，但 v6 有更好的 Vim 模式、语言包。 | 编辑器 | 高 |
| **无移动端适配**：侧边栏不固定、触摸体验差。需加 viewport meta + 媒体查询 + 触摸事件。 | 移动 | 中 |
| **无导入/导出**：只有 Logseq 导入，无 Markdown/JSON 导出。需写 `export_markdown.py` + `export_json.py`。 | 互操作 | 低 |
| **无实时协同**：多设备同时编辑会覆盖。需 WebSocket + Yjs 或 OT。 | 协作 | 高 |
| **FTS 边缘情况**：<3 字符走 LIKE 全表扫。可加 jieba 分词辅助或强制最小长度。 | 搜索 | 中 |
| **无 PWA**：不能离线访问、不能添加到主屏幕。需加 manifest + service worker。 | 离线 | 中 |

### P3 — 架构演进

| 优化 | 影响 | 复杂度 |
|------|------|--------|
| **AI Agent 协作**：MCP server 暴露 `get_page` / `create_page` / `search` 工具。需加 `mcp.py` 或用 `fastmcp`。 | AI 集成 | 中 |
| **多用户**：当前单用户，无 owner 概念。需加 `users` 表 + JWT 认证。 | 多租户 | 高 |
| **插件系统**：slash commands 硬编码，需改为注册制。 | 可扩展性 | 中 |

## 导入 Logseq 数据

```bash
cd /home/user/services/kb
LOGSEQ_DIR=/home/user/Documents/DocumentsNya python import_logseq.py
```

- 读取 `journals/*.md`（文件名 `2026_6_18.md` → 页面名 `2026-06-18`）
- 读取 `pages/**/*.md`（相对路径 → 页面名）
- 全量重建：先删库 → 重新初始化 → 逐页解析写入
- 当前数据量：416 页面 / 2380 链接 / 288 标签 / 107 属性

## License

MIT
