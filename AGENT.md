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
- 全局事件委托处理所有点击（`[data-page]`、`[[data-tag]`、`[data-nav]`）
- `navigate(target, ...args)` 是唯一路由入口

### `import_logseq.py` — Logseq 导入

- 全量重建：`_reset_db()` → `init_db()` → 逐页 `import_page()`
- `journals/2026_6_18.md` → 页面名 `2026-06-18`
- `pages/子目录/页面.md` → 页面名 `子目录/页面`
- `_reset_db()` 会删除 `kb.db`、`kb.db-wal`、`kb.db-shm` 三个文件

### `main.py` — FastAPI 应用

- CORS 全开（`allow_origins=["*"]`），仅供 Tailscale 内网使用
- `startup` 事件调用 `init_db()` 确保表存在
- `GET /` 和 `GET /page/{slug}` 都返回同一个 `index.html`（SPA 路由）

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

### 6. refs 写入不完整

只插不删会导致「删掉的引用」残留。每次 save 必须 `DELETE FROM refs WHERE source_id=?` 再重插。

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

- 服务绑定 `0.0.0.0:8080`，通过 Tailscale 访问
- 重启后需手动拉起（无 systemd、无 Docker）
- 数据库备份：直接复制 `data/kb.db`（WAL 模式下需先 `PRAGMA wal_checkpoint`）
- nftables 需放行 tailscale0 入站流量

## 不要做的事

- ❌ 不要引入 slug/title 分离
- ❌ 不要手动维护 pages_fts（触发器会处理）
- ❌ 不要在 refs 写入时跳过 delete
- ❌ 不要尝试"升级" CodeMirror 6
- ❌ 不要为日记建独立表
- ❌ 不要提交数据库文件到 git
- ❌ 不要在 API 层做大小写敏感比较
