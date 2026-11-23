# `ui-lazyvim` 分支：前端重构的独立开发区

## 一句话

后端一行没改，前端整个重写。这个分支只动**前端 + 一个服务端缓存头**，
`main` 上的业务随时可以合进来。

## 两种 UI：曾经的并存 → 现在只有一个（2026-11-23）

**历史上（2026-11-21 ~ 11-22）两个分支独立部署、长期共存。那段已结束。**

| | 旧 UI（`main`） | 新 UI（`ui-lazyvim`） |
|---|---|---|
| 形态 | 单文件 5234 行内联 | 954 行模板 + `static/app.js` 4895 行 + 8 个插件 |
| 容器 | `kb`（当时跑旧模板） | `kb-ui-test`（**已 Exited**） |
| 端口 | `127.0.0.1:8080`（经 nginx `/kb/`） | `127.0.0.1:8099`（**已无人监听**） |
| 数据 | `~/services/kb-docker/data/kb.db` | `~/pi-working/kb-ui/data/`（独立副本，不再使用） |
| 后端 | 17 个业务文件**共用同一 blob**，零差异 | 同左 |
| 镜像 | 共用 `kb-docker-kb:latest`（requirements.txt / Dockerfile 零差异） | 同左 |

**现状（2026-11-22 起）**：新前端接管生产 `8080`，旧 UI 不再对外，
`kb-ui-test` 已停。所以下表里"两个容器读两份数据库"那段不再适用 ——
现在**只有一份部署、一份数据**（`~/services/kb-docker/data/kb.db`）。

**分支仍未合并进 `main`**：用户 2026-11-23 明确决定先不合。生产 `kb`
容器挂的是 worktree `~/pi-working/kb-ui` 的源码，worktree 已 lock。

> 历史备注（并存期的数据同步方式，现在不需要了）：两份库会漂移，
> 用 `sqlite3 … ".backup '…'"` 做在线一致快照（`cp` 会漏 WAL 里未
> checkpoint 的事务）。

## 切换前端：当时的三种办法（已过时，仅存档）

> **2026-11-22 之后不需要切换了** —— 生产跑的就是新前端。下面这段是并存期
> 的操作记录，留作回退参考。现在的日常操作在「办法 D」。

### 办法 A：临时切到新 UI 看一眼（并存期用）

直接打开新 UI 的地址即可，生产容器不动：

- 本机：`http://127.0.0.1:8099/`（**容器已停，现在打不开**）
- tailnet 内：`https://fajita.tail117f80.ts.net:8447/`（映射保留，会 502）

### 办法 B：把生产入口切到新 UI（这是实际上线的做法）

生产容器 `kb` 通过 `docker-compose.override.yml` 挂载 worktree 的源码。
仅换模板是不够的 —— 新 UI 还需要 `static/` 下几十个文件，而镜像里的
`static/` 是旧版内容。所以正确做法是整个目录挂进去：

```sh
cd /home/user/services/kb-docker

# 在 docker-compose.override.yml 的 kb.volumes 里加（这就是现在生效的配置）：
#   - /home/user/pi-working/kb-ui/main.py:ro
#   - /home/user/pi-working/kb-ui/routers:ro
#   - /home/user/pi-working/kb-ui/static:ro
#   - /home/user/pi-working/kb-ui/templates:ro

docker compose up -d --no-build kb
```

**回退旧 UI**：把 override 的四个挂载去掉（或指回 `./main.py` 等仓库路径），
`docker compose up -d --no-build kb` 即可 —— 旧模板在 `main` 分支，
镜像里本来就有一份，不需要 `git checkout` 任何东西。

### 办法 C：并存期在两个 UI 之间切换（已停用）

```sh
cd /home/user/pi-working/kb-ui
./run-container.sh                  # 起 kb-ui-test → :8099（已 Exited）
docker stop kb-ui-test              # 停掉
```

### 办法 D：现在怎么干活（唯一有效的方式）

```
生产 = 容器 kb = worktree ~/pi-working/kb-ui（branch ui-lazyvim）
```

| 改了什么 | 怎么生效 |
|---|---|
| `static/**`、`templates/**` | 刷新页面（`sw.js` 会 network-first；若外壳变了要先 bump `VERSION`）|
| `main.py`、`routers/**` | `docker restart kb` |
| `mcp_server.py`、`mcp_http.py` | `docker restart kb-mcp`（`:ro` 挂载，不用重建镜像）|
| `Dockerfile`、`requirements.txt` | 真需要重建镜像（很少见）|

### 容器↔代码对应关系（`git worktree list` 一眼看清）

```
/home/user/services/kb-docker  87fd3ed [main]        → 仓库本体（pull-only，不跑）
/home/user/pi-working/kb-ui    ad322c3 [ui-lazyvim]  → 容器 kb（生产）locked
```

（提交号会走，看 `git worktree list` 的实际输出为准。）

## 这个分支相对 `main` 动了什么

```
main.py                  +2 处（其余 280 行逐字节相同）
templates/index.html       -5234 行（旧内联单文件）
                             +954 行（新模板：只有设计 token + DOM 骨架）
static/app.js              +4895 行（外置的 SPA 主程序）
static/plugin.js           +475 行（插件内核，lazy.nvim 模型）
static/panel.js           +247 行（面板容器 overlay / dock / full）
static/themes/               +6 文件（catppuccin.css 结构层 + 4 flavour + index.js）
static/plugins/             +16 文件（8 个插件 ×(js+css) + index.js 清单）
static/sw.js               重写（分策略缓存，VERSION 现在 kb-shell-v20）
static/vendor/codemirror/  +20 个文件（vim / show-hint / search / fold …）
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

生产目录 `/home/user/services/kb-docker` 始终在 `main`（pull-only），
分支 worktree 在 `/home/user/pi-working/kb-ui`（已 `git worktree lock`）。

**2026-11-22 之后：这个分支就是线上。** 生产容器 `kb` 通过
`docker-compose.override.yml` 挂载 worktree 的 `main.py` / `routers/` /
`static/` / `templates/`（`./data` 仍是生产库），所以：

- 改 `static/`、`templates/` → 刷新页面即生效，**不用重启**
- 改 `main.py`、`routers/` → `docker restart kb`
- 改 `mcp_server.py` / `mcp_http.py` → `docker restart kb-mcp`（`:ro` 挂载，不用重建镜像）

`kb-ui-test` 容器（`:8099` / tailserve `:8447`）**已 Exited(0)**，
它的 `catppuccin` 时代预览不再需要。旧的 `/home/user/pi-working/kb-ui-test/`
是开发时的临时副本（484MB，`data/` 里有备份），已不再被任何容器使用，
可以随时删 —— 删前确认里面没有还没进仓库的东西。

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
| 生产未受影响 | 并存期：`kb` 仍 healthy、`/static/app.js` 仍 404（旧模板内联）。**现在相反，`kb` 就是新前端** |
| 合并预演 | `git merge-tree` 0 冲突标记；分支已包含 `main` 最新 commit |
| 业务代码 | 12 个后端文件与 `main` **0 行差异**（逐文件 `git diff --quiet` 验证） |

## 配色：Catppuccin 四口味 + 单一覆盖层（2026-11-22）

用户要求「参考本机 nvim config 的 catppuccin」并「作为单独覆盖的东西，而非之前那种
一步一个改颜色」。这两点决定了实现的形状。

### nvim 是怎么做的

`~/.local/share/nvim/lazy/catppuccin/` 是一个插件：**一套**高亮组
（`lua/catppuccin/groups/syntax.lua`、`editor.lua` …），flavour 只是参数。
`:colorscheme catppuccin-mocha` / `-macchiato` / `-frappe` / `-latte` 换的只是
`palettes/<flavour>.lua` 里那 26 个色值，高亮逻辑一行没重写。

顺带一个细节：nvim 里 `Comment = { fg = C.overlay2 }`，不是 overlay0 —— 这条
被抄过来了，原来用 overlay0 时注释只有 3.36:1，糊。

### 本仓库改成同构的三层

```
static/themes/
  catppuccin.css              ← 结构层：语义别名 + CodeMirror 分组，只写一次
  catppuccin-mocha.css        ← 参数层：15 行官方色板（26 个变量）
  catppuccin-macchiato.css    ← 同上
  catppuccin-frappe.css       ← 同上
  catppuccin-latte.css        ← 同上 + 浅色对比度修正 + 语法色压暗
```

- 选择器一律 `:root[data-scheme^="catppuccin"]` 前缀匹配 → 加第五个 flavour
  **不需要动结构层**
- `themes/index.js` 的 spec 里多了 `dep`（结构层）与 `group`（同一族）：
  `loadTheme` 先挂 dep 再挂色板，`toggleTheme` 只在同组内轮转，
  `:colorscheme catppuccin` 报这一族有哪几个
- 没有 `dep` 的主题（以后接 tokyonight / kanagawa）仍是自包含单文件，
  两种形式共存，不强制

### 顺手修掉的三个真问题

| 问题 | 之前 | 现在 |
|---|---|---|
| `--bg-mantle` **被引用 3 处但从未定义**（卡片背景是透明的） | undefined | 结构层定义，模板里补首帧回退值 |
| `--bg-hover` / `--accent-soft` 只在插件里带 fallback，主题不给值 | 永远走 fallback | 结构层用 `color-mix` 派生，四口味自动跟随 |
| 四个 flavour **各自抄 30 条硬编码色值的 CM 规则**，且已漂移（latte 缺 `cm-header-1/2/3`、缺 `formatting-header/link`、缺 `activeline-gutter`） | 改一次配色改四遍 | 结构层一套，颜色全走 `var()` |

### 语法色独立成一层（`--syn-*`）

Latte 的官方 pink/peach/yellow/sky/lavender 在 `#eff1f5` 上只有 **2.3–2.8:1**，
在 CodeMirror 里等于看不见。所以高亮色不能直接吃 `--blue`/`--green` 这些色板名：

- 结构层定义 `--syn-heading` / `--syn-strong` / `--syn-comment` … 共 15 个，
  深色 flavour 直接映射到色板（`var(--blue)` 等）
- `catppuccin-latte.css` 里把这 15 个压暗到 ≥4.5:1（每行注明实测对比度）
- CM 规则只引用 `--syn-*`，不再出现色板名

### 实测（Playwright + 系统 chromium）

```
注册表   mocha / macchiato / frappe / latte 四个都在
变量     --bg-mantle --bg-hover --accent-soft 四口味均非空（之前 3 个是 undefined）
CM 一套规则生效：
  mocha      bg=#1e1e2e  gutter=#181825  h1=#b4befe
  macchiato  bg=#24273a  gutter=#1e2030  h1=#b7bdf8
  frappe     bg=#303446  gutter=#292c3c  h1=#babbf1
  latte      bg=#eff1f5  gutter=#e6e9ef  h1=#5767c0
对比度（正文/辅助/链接）
  mocha 11.34 / 7.37 / 10.54        macchiato 9.92 / 6.62 / 9.14
  frappe 8.06 / 5.55 / 7.33         latte     7.06 / 5.53 / 5.63
语法色 15 个 token × 4 flavour：**全部 ≥4.5:1**（最低 frappe 4.53、latte 4.51）
  （修前 mocha 有 4 个不达标，最低 2.46:1）
四 flavour × 6 个视图（/ /all /todo /graph /page /search）全绿
frappe 下插件：TOC 20 条 / health 50 条 / :Searches 面板 ✓
which-key 面板在 frappe 下 bg=#292c3c fg=#c6d0f5，22 行
0 console / page 错误，0 HTTP>=400
```

> 回归注意：`/edit/KB` 的 `#view` 是空的 —— 编辑器内容在 `#editor` 里，
> 拿 `#view` 判空会误报。这是正交行为，不是 bug。

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
