# Knowledge Base (KB)

一个自托管的双链笔记系统，SQLite 后端 + FastAPI REST API + 原生 JS 单页 Web UI。
设计目标：**人能用，AI 也能用**——多设备通过 Tailscale 访问，FastAPI 让 AI agent 直接读写知识库。

功能亮点：双链 `[[wikilinks]]`、标签、全文搜索、实时预览编辑器、引用补全、知识图谱、任务管理（甘特图/列表）、深浅主题。

## 设计原则

- **单一命名空间**：页面名 = 链接目标 = URL 键。`[[Foo]]` 指向的页面就叫 `Foo`。
- **Markdown 是唯一权威源**：存储引擎只做「内容 → 结构化索引」，不引入 block tree。
- **大小写不敏感**：`name` 和 `refs.target_name` 都是 `COLLATE NOCASE`。
- **无冲突写入**：每次 save 对当前页 delete + 重插 refs/properties。

## 项目结构

```
kb/
├── main.py              # FastAPI 应用、路由、启动事件
├── database.py          # SQLite schema、连接池、FTS 触发器
├── routers/api.py       # REST API 端点
├── parse.py             # Markdown 解析：[[links]]、#tags、key:: value
├── templates/index.html  # 单页 Web UI（CodeMirror 5 + markdown-it + Frappe Gantt）
├── static/              # 静态资源（预留）
├── import_logseq.py     # 从 Logseq 文件版导入
├── run.sh               # 启动脚本
├── data/kb.db           # SQLite 数据库（首次启动自动创建）
└── venv/                # Python 虚拟环境
```

## 数据模型

### 表结构

```sql
CREATE TABLE pages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL COLLATE NOCASE UNIQUE,
    content    TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE refs (
    source_id   INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    target_name TEXT NOT NULL COLLATE NOCASE,
    kind        TEXT NOT NULL DEFAULT 'link',  -- 'link' | 'tag'
    PRIMARY KEY (source_id, target_name, kind)
);
CREATE INDEX idx_refs_target ON refs(target_name);

CREATE TABLE properties (
    page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    key     TEXT NOT NULL,
    value   TEXT,
    PRIMARY KEY (page_id, key)
);

CREATE VIRTUAL TABLE pages_fts USING fts5(
    name, content, content='pages', content_rowid='id', tokenize='trigram'
);
```

触发器自动同步 FTS 索引（insert/update/delete），无需手动维护。

## 已实现功能

### 编辑器

- CodeMirror 5 + dracula/default 主题（随深浅主题切换）
- 实时预览（markdown-it 渲染）
- **同步滚动**：编辑器与预览区百分比同步（可关闭）
- **拖拽分界线**：中间 6px 分界线可拖拽调整左右比例（20%-80%）
- 斜杠命令（`/` 触发：标题、列表、代码块等）
- **引用补全**：`[[` 触发，上下箭头导航，Enter/Tab 选中，失焦安全
- 保存 `Ctrl+S`，重命名支持

### 导航与侧边栏

- **收窄模式**：`◀` 收窄为 56px 图标栏（保留图标 + tooltip），`▶` 展开
- **深浅主题**：`🌙/☀️` 切换，CSS 变量驱动，跟随系统偏好，localStorage 持久化
- 全局事件委托（`[data-page]`、`[data-tag]`、`[data-nav]`）

### 搜索

- FTS5 trigram 全文搜索（支持 CJK）
- 搜索结果按**名称相关度排序**：精确匹配 > 开头匹配 > 包含匹配 > 内容匹配
- 匹配部分**高亮标记**
- `<3` 字符走 LIKE 兜底

### 页面浏览

- **所有页面**：字母排序，实时筛选，DocumentFragment 批量渲染
- 页面详情：反向链接、标签、属性

### 任务管理（TODO）

- 侧边栏入口 → 任务页面
- **列表视图**：优先级圆点、逾期红字、关联链接可点击
- **甘特图视图**（Frappe Gantt）：
  - 日/周/月视图切换
  - 日期范围筛选
  - 优先级颜色（p1 红 / p2 橙 / p3 黄 / p4 绿）
  - 悬浮弹窗：日期、优先级、关联链接、重复语法
- 任务来源：指定 KB 页面，解析语法：

```
p1 2024-01-15 [[相关页面]] 任务内容 due:2024-01-20 rec:1w
```

重复语法：
| 语法 | 含义 |
|------|------|
| `rec:1w` | 普通每周：完成日 + 7 天 |
| `rec:+1m` | 严格每月：固定日期（如每月 15 号） |
| `rec:3b` | 每 3 个工作日（跳过周末） |
| `rec:+1y` | 严格每年（生日等） |
| `rec:Nd` / `rec:Nw` | 通用：N 天/周 |

### 知识图谱

- 力导向布局，内联 SVG，节点可点击导航

## 解析层 (`parse.py`)

```python
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
TAG_RE = re.compile(r"(?<!#)#(?!\+)([^\s#\[\]()]+)")
PROPERTY_RE = re.compile(r"^\s*-?\s*([^\s:：]+)::\s*(.*)$")
```

- `extract_links()` → `set[str]`
- `extract_tags()` → `set[str]`
- `extract_properties()` → `dict[str, str|None]`
- `parse()` → `{"links", "tags", "properties"}`

大小写去重：`casefold()` 字典归并，防止 `[[Foo]]` + `[[foo]]` 触发 UNIQUE 约束。

## API 参考

### Pages

| Method | Path | Body | Description |
|--------|------|------|-------------|
| `GET` | `/api/pages` | — | 列表（`?tag=`、`?search=`、`?limit=`、`?offset=`） |
| `GET` | `/api/pages/names` | — | 所有页面名 |
| `GET` | `/api/pages/{name}` | — | 详情 + tags + links + backlinks + properties |
| `POST` | `/api/pages` | `{name, content}` | 创建 |
| `PUT` | `/api/pages/{name}` | `{name?, content?}` | 更新（改名时重写引用） |
| `DELETE` | `/api/pages/{name}` | — | 删除 |

### Tags

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/tags` | 所有标签 + 计数 |
| `GET` | `/api/tags/{name}/pages` | 按标签筛选 |

### Graph

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/graph` | 全图（nodes + edges） |

## 启动

### 本地

```bash
pip install fastapi uvicorn aiosqlite
python3 -m uvicorn main:app --host 0.0.0.0 --port 8080
```

### Docker Compose

```yaml
services:
  kb:
    build: .
    ports:
      - "8080:8080"
    volumes:
      - ./data:/app/data
    restart: unless-stopped
```

```bash
docker compose up -d        # 启动
docker compose up -d --build  # 更新后重建
docker compose logs -f        # 查看日志
```

数据库 `./data/kb.db` 挂载到宿主机，重建容器数据不丢。

### 备份

```bash
# 直接复制 SQLite 文件（WAL 模式下先 checkpoint）
sqlite3 data/kb.db "PRAGMA wal_checkpoint"
cp data/kb.db backup/kb-$(date +%Y%m%d).db
```

## 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python 3.11+, FastAPI, aiosqlite |
| 前端 | Vanilla JS (ES2020+), CodeMirror 5, markdown-it 13, Frappe Gantt |
| 数据库 | SQLite 3.35+ (WAL mode, FTS5 trigram) |
| 服务器 | Uvicorn (ASGI) |

## 路线图

### 已完成

- [x] 侧边栏收窄 + 展开按钮
- [x] 深浅主题切换
- [x] 引用补全增强（上下键导航、失焦安全）
- [x] 编辑器同步滚动 + 拖拽分界线
- [x] 所有页面浏览 + 筛选
- [x] 搜索结果按相关度排序 + 高亮
- [x] 任务管理（甘特图 + 列表 + 日期范围筛选）
- [x] Docker 部署方案
- [x] 性能优化（列表渲染、日期格式化、HTML 转义）

### 待办

#### 体验优化

| 优化 | 影响 | 复杂度 |
|------|------|--------|
| **无认证**：CORS `*` + API 裸奔，需 token 或 IP 白名单 | 安全 | 低 |
| **日记编辑器升级**：纯 textarea → CodeMirror | 体验 | 中 |
| **导入/导出**：Markdown/JSON 导出 | 互操作 | 低 |
| **移动端适配**：触摸事件 + 响应式布局 | 移动 | 中 |
| **PWA**：离线访问 + 添加到主屏幕 | 离线 | 中 |

#### 功能增强

| 优化 | 影响 | 复杂度 |
|------|------|--------|
| **重命名事务**：独立 `PATCH /pages/{name}/rename` + 事务 | 数据完整性 | 中 |
| **页面历史**：`page_versions` 表 + 版本回溯 | 安全 | 中 |
| **block reference**：`((uuid))` 支持 | 功能 | 高 |
| **图谱交互**：D3.js/cytoscape.js 缩放/拖拽 | 体验 | 中 |
| **FTS 优化**：jieba 分词辅助 CJK 短查询 | 搜索 | 中 |

#### 架构演进

| 优化 | 影响 | 复杂度 |
|------|------|--------|
| **MCP Server**：暴露 `get_page`/`create_page`/`search` 工具 | AI 集成 | 中 |
| **多用户**：`users` 表 + JWT | 多租户 | 高 |
| **插件系统**：slash commands 注册制 | 可扩展性 | 中 |
| **实时协同**：WebSocket + Yjs/OT | 协作 | 高 |

## 导入 Logseq 数据

```bash
LOGSEQ_DIR=/path/to/logseq python import_logseq.py
```

全量重建：先删库 → 重新初始化 → 逐页解析写入。

## License

MIT
