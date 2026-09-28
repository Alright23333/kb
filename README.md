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
├── database.py          # SQLite schema、连接池、FTS/版本触发器
├── routers/api.py       # REST API 端点
├── parse.py             # Markdown 解析：[[links]]、#tags、key:: value
├── mcp_server.py        # MCP Server（stdio，13 个工具，供 AI agent 使用）
├── export.py            # 导出 CLI（markdown / json / zip）
├── import_logseq.py     # 从 Logseq 文件版导入
├── templates/index.html  # 单页 Web UI（CodeMirror 5 + markdown-it + Frappe Gantt）
├── static/              # 静态资源（预留）
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

CREATE TABLE page_versions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    page_name  TEXT NOT NULL COLLATE NOCASE,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX idx_versions_name ON page_versions(page_name);

CREATE VIRTUAL TABLE pages_fts USING fts5(
    name, content, content='pages', content_rowid='id', tokenize='trigram'
);
```

触发器自动同步：
- FTS 索引（insert/update/delete）
- 版本快照：内容变更时自动将旧内容存入 `page_versions`；删除页面时快照全部内容（回收站数据源）

## 已实现功能

### 编辑器

- CodeMirror 5 + dracula/default 主题（随深浅主题切换）
- 实时预览（markdown-it 渲染）
- **同步滚动**：编辑器与预览区百分比同步（可关闭）
- **拖拽分界线**：中间 6px 分界线可拖拽调整左右比例（20%-80%）
- 斜杠命令（`/` 触发：标题、列表、代码块等）
- **引用补全**：`[[` 触发，上下箭头导航，Enter/Tab 选中，失焦安全
- 保存 `Ctrl+S`，重命名支持
- **🕘 版本历史**：编辑已有页面时头部按钮，侧滑面板预览 + 一键恢复
- **🗑️ 删除**：确认弹窗，删除后可从回收站恢复

### 版本历史与回收站

- 内容变更/删除时自动快照（SQL 触发器，零成本）
- 按页面名追踪，删除后版本仍可恢复
- 恢复动作本身也先快照（双保险，恢复错了还能再恢复回来）
- **回收站**：「所有页面」底部折叠区，一键恢复最新版本

### 导入/导出

- **📤 导出**：「所有页面」顶部按钮，zip 格式（每页一个 .md，front matter 含 tags/properties/时间戳）
- **📥 导入**：支持 .md 和 .zip，冲突策略可选跳过/覆盖
- CLI 工具：`python export.py markdown|json|zip`

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
- **全屏模式**：填充整个内容区
- **缩放**：滚轮缩放（以鼠标位置为中心，0.1x-5x）+ +/-/⟲ 按钮
- **平移**：拖拽背景平移（grab 光标）
- **节点拖动**：拖拽单个节点重新布局，连线跟随
- 节点大小随连接数变化，悬浮 tooltip

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
| `PATCH` | `/api/pages/{name}/rename` | `{name}` | 原子改名（含 refs 冲突预处理） |
| `DELETE` | `/api/pages/{name}` | — | 删除（自动快照到版本历史） |

### Versions（版本历史）

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/pages/{name}/versions` | 版本列表（最新 100 条，含大小/时间） |
| `GET` | `/api/pages/{name}/versions/{id}` | 查看版本内容 |
| `POST` | `/api/pages/{name}/versions/{id}/restore` | 恢复（已删除页面会重建） |
| `DELETE` | `/api/pages/{name}/versions` | 清空该页历史 |
| `GET` | `/api/trash` | 回收站：已删除页面列表 |

### Export / Import

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/export/zip` | 导出 zip（每页一个 .md + front matter） |
| `GET` | `/api/export/json` | 全量 JSON 导出（pages + refs） |
| `POST` | `/api/import` | 导入 .md 或 .zip（`?conflict=skip\|overwrite`） |

### Tags

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/tags` | 所有标签 + 计数 |
| `GET` | `/api/tags/{name}/pages` | 按标签筛选 |

### Graph

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/graph` | 全图（nodes + edges） |

## MCP Server（AI Agent 接入）

`mcp_server.py` 通过 stdio transport 暴露 13 个工具，供 Claude Desktop / Cursor 等 AI agent 直接读写知识库：

| 类别 | 工具 | 说明 |
|------|------|------|
| 读 | `search(query)` | FTS 全文搜索 |
| 读 | `get_page(name)` | 页面详情（内容+tags+links+backlinks） |
| 读 | `list_pages()` | 所有页面名 |
| 读 | `get_recent_pages(limit)` | 最近更新页面 |
| 读 | `get_tags()` | 所有标签 + 计数 |
| 读 | `get_pages_by_tag(tag)` | 按标签查页面 |
| 读 | `get_backlinks(name)` | 反向链接 |
| 写 | `create_page(name, content)` | 创建页面 |
| 写 | `update_page(name, content)` | 更新页面 |
| 写 | `rename_page(old, new)` | 原子改名 + 重写入链 |
| 写 | `delete_page(name)` | 删除（版本快照保留，可恢复） |
| 保险 | `get_page_versions(name)` | 版本历史（已删除页面也可查） |
| 保险 | `restore_page_version(name, id)` | 恢复旧版本 |

Claude Desktop 配置（`claude_desktop_config.json`）：

```json
{
  "mcpServers": {
    "kb": {
      "command": "python3",
      "args": ["/path/to/kb/mcp_server.py"],
      "env": { "KB_DB_PATH": "/path/to/kb/data/kb.db" }
    }
  }
}
```

> ⚠️ MCP SDK 只继承白名单环境变量（PATH、HOME 等），`KB_DB_PATH` **必须在客户端配置里显式设置**，否则回退到 `<脚本目录>/data/kb.db`。

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

### nginx 反向代理

支持两种部署方式，前端自动检测 base 路径：

**方式 A：子域名（推荐）**

```nginx
server {
    server_name kb.example.com;
    location / {
        proxy_pass http://kb:8080;
    }
}
```

**方式 B：路径前缀**

```nginx
# 注意：proxy_pass 结尾不加斜杠——前缀 /kb 原样传给应用，前端自动识别
location /kb/ {
    proxy_pass http://kb:8080;
}
```

> ⚠️ 不要用 `proxy_pass http://kb:8080/;`（尾斜杠会剥掉前缀，API 请求会 404）。

两种方式下 URL 路由、API 调用、静态资源全部自动适配，无需配置。

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
- [x] 日记编辑器升级 CodeMirror（复用 initCM）
- [x] 知识图谱缩放/平移/节点拖动（原生 SVG，无需库）
- [x] 重命名事务（`PATCH /rename` + refs 冲突预处理）
- [x] 导入/导出（zip + JSON + CLI）
- [x] 页面历史（自动快照 + 恢复 + 回收站）
- [x] MCP Server（13 个工具：读/写/版本恢复闭环）

### 待办

#### 体验优化

| 优化 | 影响 | 复杂度 |
|------|------|--------|
| **无认证**：CORS `*` + API 裸奔，需 token 或 IP 白名单 | 安全 | 低 |
| **移动端适配**：触摸事件 + 响应式布局 | 移动 | 中 |
| **PWA**：离线访问 + 添加到主屏幕 | 离线 | 中 |

#### 功能增强

| 优化 | 影响 | 复杂度 |
|------|------|--------|
| **block reference**：`((uuid))` 支持 | 功能 | 高 |
| **FTS 优化**：jieba 分词辅助 CJK 短查询 | 搜索 | 中 |

#### 架构演进

| 优化 | 影响 | 复杂度 |
|------|------|--------|
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
