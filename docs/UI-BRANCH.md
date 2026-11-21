# `ui-lazyvim` 分支：前端重构的独立开发区

## 一句话

后端一行没改，前端整个重写。这个分支只动**前端 + 一个服务端缓存头**，
`main` 上的业务随时可以合进来。

## 两种 UI 长期共存（2026-11-21 决定）

**不合并，两个分支各自独立部署，长期并存。**

| | 旧 UI（`main`） | 新 UI（`ui-lazyvim`） |
|---|---|---|
| 形态 | 单文件 5234 行内联 | 866 行模板 + `static/app.js` 3317 行 + 插件 |
| 容器 | `kb` | `kb-ui-test` |
| 端口 | `127.0.0.1:8080`（经 nginx `/kb/`） | `127.0.0.1:8099`（经 tailscale serve :8447） |
| 数据 | `~/services/kb-docker/data/kb.db` | `~/pi-working/kb-ui/data/kb.db`（独立副本） |
| 后端 | 17 个业务文件**共用同一 blob**，零差异 | 同左 |
| 镜像 | 共用 `kb-docker-kb:latest`（requirements.txt / Dockerfile 零差异） | 同左 |

**为什么能共存**：后端完全一样，只有前端和入口不同。两个容器读两份独立
的数据库（不同 inode），互不干扰；同一份 Docker 镜像因为依赖没变所以
两边都能复用，不必重建。

**数据是两份，会漂移**：新 UI 里的写入只进 `~/pi-working/kb-ui/data/`，
生产看不到；反过来也一样。想让测试库追上生产：

```sh
sudo sqlite3 /home/user/services/kb-docker/data/kb.db \
  ".backup '/home/user/pi-working/kb-ui/data/kb.db'"
```

（`.backup` 是 SQLite 官方在线一致快照，不会像 `cp` 那样漏 WAL 里没
 checkpoint 的事务。想要两边看同一份数据就改 `run-container.sh` 的
 挂载指向生产 `data/`，但那样测试写入会进生产库，慎用。）

## 切换前端的三种办法

### 办法 A：临时切到新 UI 看一眼（不改任何东西）

直接打开新 UI 的地址即可，生产容器不动：

- tailnet 内：`https://fajita.tail117f80.ts.net:8447/`
- 本机：`http://127.0.0.1:8099/`

### 办法 B：把**生产入口**切到新 UI（换默认前端）

生产容器 `kb` 通过 `docker-compose.override.yml` 挂载
`./templates/index.html`，所以**换模板 = 换这个文件指向哪份源码**。
注意：仅换模板是不够的，新 UI 还需要 `static/app.js` 等 44 个文件，
而生产容器里的 `static/` 来自镜像（旧版内容）。所以正确步骤是：

```sh
cd /home/user/services/kb-docker

# 1. 把新前端文件也挂进生产容器（只读，不动镜像）
#    在 docker-compose.override.yml 的 kb.volumes 里加：
#      - ./static:/app/static:ro
#      - ./main.py:/app/main.py:ro   # 新 UI 的 no-cache 中间件 + /search 路由词

# 2. 把 main 分支的模板换成新 UI 的（两种方式二选一）
#    方式一（推荐，可随时回退）：worktree 挂载，不 commit
#      git worktree add /tmp/lazy-ui ui-lazyvim
#      # 然后把 override 里的挂载源改成 /tmp/lazy-ui/templates/index.html
#    方式二（正式合并）：merge ui-lazyvim 进 main（见下面「合并」节）

# 3. 重启生产容器
docker compose up -d --no-build kb

# 回退旧 UI：
git checkout main -- templates/index.html static/sw.js
docker compose up -d --no-build kb
```

### 办法 C：日常开发时在两个 UI 之间切换（不碰生产）

在分支 worktree 里起停容器即可，**生产始终跑旧 UI**：

```sh
# 切到新 UI（已起的容器重建）
cd /home/user/pi-working/kb-ui
./run-container.sh                  # 容器 kb-ui-test → :8099

# 切回旧 UI：停掉测试容器即可，生产 kb 一直在跑
docker stop kb-ui-test
```

### 容器↔代码对应关系（`git worktree list` 一眼看清）

```
/home/user/services/kb-docker  758578f [main]        → 容器 kb（生产，旧 UI）
/home/user/pi-working/kb-ui    4ffb7b8 [ui-lazyvim]  → 容器 kb-ui-test（新 UI）
```

两个目录共享同一份 `.git`（kb-ui/.git 只是 4KB gitdir 指针）。
**分支互斥**：`main` 只能在 `kb-docker`，`ui-lazyvim` 只能在 `kb-ui`。

## 这个分支相对 `main` 动了什么

```
main.py                  +2 处（其余 229 行逐字节相同）
templates/index.html       -5234 行（旧内联单文件）
                             +866 行（新模板：只有设计 token + DOM 骨架）
static/app.js              +3292 行（外置的 SPA 主程序）
static/plugin.js           +474 行（插件内核，lazy.nvim 模型）
static/panel.js           +236 行（面板容器 overlay / dock / full）
static/themes/               +3 文件（Catppuccin Mocha / Latte + 注册表）
static/plugins/             +12 文件（6 个插件）
static/sw.js               重写（分策略缓存，VERSION → kb-shell-v16）
static/vendor/codemirror/  +18 个 addon（vim / show-hint / search / fold …）
docs/*.md                  +3 篇（架构对比 / 移动端输入 / 插件模型）
```

`main.py` 唯一的两处改动：

1. `nocache_first_party_static` 中间件 —— `/static/` 下**非 vendor** 资源
   发 `Cache-Control: no-cache, must-revalidate`。不加这个，改了 `app.js`
   浏览器还在跑旧版（这个坑真踩过）。
2. `_SPA_ROUTE_WORDS` 加 `search` —— 新前端有 `/search/<q>` 真路由。
   注意 `theme` **故意不加**：它只是 `:` 命令词，加了会把名为 `theme`
   的页面永久遮住。

**`routers/api.py` / `database.py` / `search.py` / `parse.py` / `timeutil.py` /
`export.py` / `backup.py` / `mcp_*.py` / `Dockerfile` / `requirements.txt`
在这个分支上与 `main` 二进制相同。**

## 怎么开发

```sh
cd /home/user/pi-working/kb-ui          # 分支 worktree，不是生产目录
git pull --rebase origin main           # 把 main 的进展带进来
./run-container.sh                      # 起/重建本地容器（端口 8099）
```

分支自带 `run-container.sh`：复用生产镜像 `kb-docker-kb:latest`
（Python 依赖没变，不必重建），源码只读挂载，数据用**本分支自己的
`data/kb.db` 副本**（首次运行从生产库 `.backup` 快照）。它绝不会写入
`~/services/kb-docker/data`。

生产目录 `/home/user/services/kb-docker` 始终在 `main`，两边互不干扰
（`git worktree list` 能看到）。

生产容器 `kb` 只挂了 `./templates/index.html`（见 `docker-compose.override.yml`），
所以这个分支**不会**影响线上。它自己有一套：

- 容器 `kb-ui-test` → `127.0.0.1:8099`
- 预览 `https://fajita.tail117f80.ts.net:8447/`（tailnet 内）
- 数据 `~/pi-working/kb-ui/data/kb.db`（独立副本，579 页）

`kb-ui-test` 容器现在挂的就是**这个 worktree** 的源码
（`/home/user/pi-working/kb-ui`），所以改分支代码 = 刷新页面即可看到，
不用重建镜像。

旧的 `/home/user/pi-working/kb-ui-test/` 是开发时的临时副本，
已不再被容器使用，可以随时删（它的 `data/` 有 459MB 备份，删前确认）。

## 加一个插件（不改 `app.js`）

1. 写 `static/plugins/<name>.js`：

```js
KB.define('<name>', (api, opts) => {
  api.css('plugins/<name>.css');
  api.command('<cmd>', '<描述>', () => { /* ... */ });
  api.keys([{ lhs: '<space 后的键>', desc: '<描述>', run: () => {} }]);
});
```

2. 在 `static/plugins/index.js` 的 `KB.spec([...])` 里加一条：

```js
{
  name: '<name>', file: '<name>.js', desc: '...', priority: 50,
  lazy: true,              // 不写 true 就 boot 时加载
  cmd: '<cmd>',            // `:` 命令触发
  routes: ['<route>'],     // 视图路由触发（可选）
  event: 'page',           // boot/verylazy/page/editor/navigate
  keys: [{ lhs: '<k>', desc: '...' }],
  opts: { ... },
}
```

字段语义与 lazy.nvim 一一对应，详见 `docs/PLUGIN-MODEL.md`。

## 加一个主题（不改 `app.js`、不改模板）

1. 写 `static/themes/<name>.css`，选择器 `:root[data-scheme="<name>"]`，
   内部定义全部语义变量（`--bg/--fg/--accent/...`）与 CodeMirror 规则。
2. 在 `static/themes/index.js` 的 `THEMES` 表里登记一行。
3. `:colorscheme <name>`（前缀唯一即可）。

切换只改 `<html data-scheme>` 一个属性，不重解析 CSS、不重建 CodeMirror。

## 合并回 `main` 的步骤（用户主导，勿擅自执行）

> **当前决定是「不合并，长期共存」**——这一节留作将来改变主意的操作手册。

```sh
# 在生产目录（main 分支）
cd /home/user/services/kb-docker
git merge --no-ff ui-lazyvim
# 冲突面：只有 templates/index.html 与 static/sw.js（两边都改过），
# 其余都是新增文件，不会冲突。
```

回滚：`git checkout main -- templates/index.html static/sw.js` 即可恢复旧前端，
`main` 上的旧模板一直在 git 历史里（不需要另存 `index.classic.html`）。

## 已做的验证（2026-11-21）

| 项 | 结果 |
|---|---|
| 9 条路由 | `/ /all /todo /graph /journal /page/KB /tag/public /search/开源 /gantt` 全 200，0 控制台错误 |
| which-key | 22 个顶层项（含插件 `P/bs/tg/uH/uT`），组展开正确 |
| 插件 lazy | boot 只加载 `manager`；`/page/KB` 才拉 `toc`；`/gantt` 才拉 `gantt` |
| 插件开关 | `:Plugins` 关闭后键位/命令/路由立刻收回，localStorage 持久化，重载后仍关 |
| 甘特图 | 15 条 bar，`/gantt` 首屏直达、`/kb/gantt` 前缀部署都正确 |
| `[[` 补全 | 40 项候选，选中写入 `[[页面名]]`；输入 `[[` 自动弹 |
| 主题 | `:colorscheme` 列全部/切换/缩写匹配，CM 配色随主题变 |
| 面板 | dock 248px 可拖宽，宿主 `margin` 跟着让位，关闭归零；overlay Esc 关最上层 |
| 移动端 | 390×844 无横向溢出，动作坞 18 键，数字盘 1–9，滑动切缓冲 |
| 生产未受影响 | `kb` 仍 27h healthy / 579 页，`/static/app.js` 仍 404（旧模板内联） |
| 合并预演 | `git merge-tree` 0 冲突标记；分支已包含 `main` 最新 commit |
| 业务代码 | 12 个后端文件与 `main` **0 行差异**（逐文件 `git diff --quiet` 验证） |

## 修掉的两个老 bug（2026-11-22）

用户报：「主页可以 space 开快捷键，编辑页 normal 模式就不行」「[[ 补全有时候有、有时候没有」。
两个都是真的，根因都不在「页面逻辑混乱」，而是**同一件事有两份 code 在抢**。

### ① leader（space）被两个 handler 争用

原来 `<space>` 在 CodeMirror 的 `extraKeys` 里打开 which-key，同时
document 上还有一个顶层 keydown listener 也在处理 leader。同一个事件：

```
编辑器聚焦 + normal 模式 + 按 space
  → CodeMirror extraKeys['Space'] 执行 openWhichKey()   ✓ 面板开了
  → 事件继续冒泡到 document
  → 顶层 listener 看到 state.leaderOpen == true，
     把它当成「leader 之后的第一个按键」→ closeWhichKey()  ✗ 面板立刻关了
```

**净效果：面板开了又关，肉眼看不见。** 非编辑器视图没有 CodeMirror 抢键，
所以那边一直是好的 —— 这就是「主页能开、编辑页不能」的全部原因。

懒人包（LazyVim）的做法相反：`<Space>` 被 Vim 的 keymap 吃掉，编辑器
根本看不到它，leader 只有一个出口。所以现在改成：

- 删掉 `extraKeys['Space']`，CodeMirror 不再碰这个键
- 在 **document capture 阶段** 一次性判定：`state.leaderOpen` → 所有键归
  which-key（含 Esc / 方向键 / Enter / 字符键），并 `stopPropagation()`
- 未打开时，只有 `<space>` 且「不在输入框」且「不在 vim insert/visual 模式」
  才 `openWhichKey()`
- 冒泡阶段那份重复的 leaderOpen 分支改成一句兜底 `return`

顺带修好的连带症状：which-key 打开后按键原来先到 CodeMirror
（`f` 被当成 vim 的 find motion 吃掉），现在 capture 阶段就截住了。

### ② `[[` 补全有三处独立缺陷，叠起来变成「时好时坏」

`static/plugins/hint.js`：

| # | 问题 | 为什么表现成「有时候」 |
|---|---|---|
| a | 只挂 `inputRead` | 该事件只在文本经隐藏 textarea 进来时触发（**输入法组字/粘贴**）。直接键入 ASCII/CJK 走 `replaceSelection`，只发 `change`。所以用输入法打中文时会弹，直接敲 `[[` 不弹 |
| b | 判定写成「整行里没有 `]]`」 | `closebrackets` 插件在你打第一个 `[` 时就把 `[]` 补好了，打完第二个 `[` 文档里已经是 `[[]]`。整行永远含 `]]` → 判定恒失败 |
| c | 选中时补 `]]` | 同上，`]]` 已经存在，补完变 `[[名字]]]]` |

`#` 标签还有第 4 个独立 bug：触发判定是 `#` 后面**必须**有空白，等于永不匹配。

现在的写法：`contextOf` 只看**光标之前**那一段；`change` 和 `inputRead`
都挂（去重）；`alreadyClosed` 决定候选里要不要带 `]]`；标签触发用
`/(^|[^\p{L}\p{N}_#])#([\p{L}\p{N}_-]*)$/u`。

### 实测（Playwright + 系统 chromium，走真实 nginx `/kb/` 前缀）

```
space 开 which-key   首页 ✓  /page/KB ✓  /all 筛选框（不改输入）✓  编辑器 normal ✓
which-key 下钻       space g → 4 行「跳转」 → d 执行 → 浏览器跳到首页 ✓
space e → space p    预览 on → 再按 off ✓（用户报的那条链路）
space w / space h    保存 ✓ / 帮助浮层 ✓
insert 模式 space    仍是空格，不抢 ✓        visual 模式 ✓
输入框 space         仍是空格 ✓（设置页实测 'a b'）
[[ 自动弹            '[[' → 40 候选；再打「树」→ 9 候选（全是树莓系列）✓
Enter 选中           [[树]] → [[树莓]]，只有一个 ]] ✓
# 标签               '#' → 40 候选；'#周' → 1 候选「周记」✓
普通文字 # + 空格     不误弹 ✓
0 console / page 错误
```

> 为什么不能只测「主页 space 能开」：那是唯一本来就好用的路径。
> 回归时要专门测**编辑器 normal 模式**，那才是坏的那条。

## 生产当前跑的就是这个分支（2026-11-22）

用户决定：新前端接管生产端口 `8080`，旧 UI 不再对外。

```
kb-docker/docker-compose.override.yml  （本机覆盖，已 gitignore）
  kb.volumes:
    - ./data:/app/data                              ← 生产库不变
    - /home/user/pi-working/kb-ui/main.py:ro        ← 本 worktree
    - /home/user/pi-working/kb-ui/routers:ro
    - /home/user/pi-working/kb-ui/static:ro
    - /home/user/pi-working/kb-ui/templates:ro
```

- 端口不变（`127.0.0.1:8080`），nginx / Tailscale Serve / IP 白名单**全没动**
- 数据目录仍是生产 `~/services/kb-docker/data`（测试副本没被用上）
- MCP 两个容器（`kb-mcp` / `kb-mcp-public`）**没重启**，仍在跑镜像里的代码
- 之前只挂 `templates/index.html` 单文件 —— 新前端资源在 `static/` 下，
  必须整目录挂进去，否则 `/static/app.js` 404

### ⚠️ 脆弱点：生产现在依赖 worktree 目录

`kb` 容器挂的是 `/home/user/pi-working/kb-ui/`，**不是仓库 main 分支**。
若该目录被删、被 `git worktree remove`、或被切到别的分支，生产会立刻挂掉。

两种消除方式（择一）：

1. **把 `ui-lazyvim` 合并进 `main`**，override 改成挂 `./main.py` 等仓库路径
   —— 生产只依赖 git 仓库，最稳
2. **把 worktree 钉死**：`git worktree lock ~/pi-working/kb-ui`（防误删）
   —— 最小改动，但仍是「目录在才有服务」

### `kb-ui-test` 已停（2026-11-22）

生产已接管 8080，测试容器 `kb-ui-test`（8099）已 `docker stop`。
- 8099 不再监听，`tailscale serve` 的 `:8447` 映射保留但会 502
  （8445/8446/8448 是别的服务，没动）
- 省出约 50MB 内存
- 要重新开测试环境：`cd ~/pi-working/kb-ui && ./run-container.sh`

### 回滚

```sh
cd /home/user/services/kb-docker
cp /tmp/override-backup-*.yml docker-compose.override.yml   # 旧版单文件挂载
docker compose up -d --no-build kb
```

（备份在 `/tmp/override-backup-<时间>.yml`；`/tmp` 会随重启清空，
长期建议把回滚步骤抄到本节而不是依赖那个文件。）

### 部署前做的检查

| 项 | 结果 |
|---|---|
| 生产 DB 备份 | `VACUUM INTO` → `data/manual-backup-pre-ui-deploy.db`（18.1M） |
| `main.py` 差异 | 仅 2 处（no-cache 中间件 + `_SPA_ROUTE_WORDS` 加 `search`） |
| `routers/api.py` | 与 main **同一 blob**（`3310da36`），零差异 |
| `static/` 文件集 | ui-lazyvim 是 main 的**超集**（无旧版独有文件被删） |
| `b2ea3cb..main` 是否动过 .py | 没动过（`git log --quiet`） |
| `data/custom.css` | 0 字节 —— 无用户定制主题，不存在主题丢失风险 |

### 部署后的实测

```
/kb/ /kb/page/KB /kb/all /kb/todo /kb/graph /kb/journal /kb/search/x
  → 全 200，BASE 推导 = /kb，API = /kb/api
/static/app.js /static/plugin.js /static/panel.js /static/themes/index.js
/static/plugins/{index,health,toc,gantt,hint,sidebar}.js
/static/vendor/mermaid/... /static/vendor/frappe-gantt/...
/manifest.webmanifest /sw.js                       → 全 200
/api/pages/count → 614        /api/graph /api/tags → 200
插件 8 个加载正常；:health 面板 → 614 页 / 3229 链接 / 92 活跃
写路径：:w → prompt 输名 → 落库 ✓  改名 ✓  删除 ✓（测试页已清理干净）
MCP：8092 / 8100 握手 OK，search 实测有结果
0 JS 错误
```

> 发现的坑：新前端编辑器默认 **vim keymap**，CodeMirror 里直接打字会被当命令吞掉
> （测试时 "部署测试页 ui" 变成了 "-lazyvim"）。这不是 bug，是 nvim 式交互的
> 代价 —— 但普通用户第一次用会被咬。移动端会自动切到 default keymap。

## 旧版功能补齐（2026-11-21 晚）

旧版相对新版仅剩的两个缺口，现已全部以插件形式补齐：

| 功能 | 插件 | 命令 / 键位 | 说明 |
|---|---|---|---|
| 知识库体检报告 | `health` | `:health` / `space uh` | dock 右侧面板：孤立页 290 / 断链 157 / 统计；断链按引用数排序；每组超 25 条折叠 |
| 保存搜索 | `saved-search` | `:Searches` / `space uS` | 存储键沿用 `kb-saved-searches`（两套 UI 切换不丢收藏）；搜索页注入「☆ 固定」；首页注入常用搜索卡片 |

> `saved-search` 必须 `lazy: false` —— 它靠 MutationObserver 给每个页面注入，
> 懒加载没人触发它，等于没装。

### 顺手修掉的真 bug（Playwright 实测发现）

1. **TOC 遮罩层吃掉全页点击**：`toc` 插件用 overlay 模式却自己改了定位成
   页角浮块，scrim 仍在（z-index 89 + pointer-events auto 覆盖全屏）→
   打开任何有 ≥3 个标题的页面后，正文链接和按钮**全部点不动**。
   修法：`panel.js` 加 `opts.scrim === false`（`.kbp-scrim-pass` 不吃点击），
   `toc.js` 传 `scrim:false`，并把 `right` 改成
   `calc(var(--kbp-dock-w, 0px) + 1.2em)` 自动让开右侧 dock。

2. **MutationObserver 无限循环**：注入改 DOM 又触发 observer → 浏览器卡死。
   修法：注入必须幂等（内容签名不变就完全不碰 DOM）+ observer 回调用 rAF 节流。

3. **overlay 面板「创建即可见」**：`isOpen()` 首次就返回 true，插件的 toggle
   逻辑把首次调用误判成「已打开」→ 直接 close，面板永远出不来。
   修法：`panel.js` 里创建一律 `display:none`，由 `open()` 决定。

## 还没做的

- 合并到 `main` 前需要决定这 2 个新插件是「合并时一起带过去」还是
  「先在分支上留着」
- `ai-native` 分支的 776 行 AI 功能要迁到新架构（塞进旧单文件模板的，
  拆成 `static/app.js` 后需重写）—— 见下节
