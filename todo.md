> KB（自建笔记软件：FastAPI + SQLite + 速成前端 + MCP 接口，见 [[KB]]）——兼作**待办 + 开发更新日志**。
> 上半部是待办状态，底部「更新日志」是按日期的时间线（只追加不回改）。
> **同步说明**：本文件是**真本**，KB 知识库里的 `kb-issue` 页是它的副本（两者内容一致，改动以本文件为准）。
> **[[2026-11-21]] 状态快照**：P0–P4 全部清零；**P5（生产环境修复与工程化）7 项全部入库**；**P6（前端可访问性）8 项全部完成**；**P7（对外开放与稳定性）4 项全部上线**。✅ 生产 `fajita` 部署、4 个容器 healthy（`kb` / `kb-mcp` / `kb-mcp-public` / `kb-ui-test`）；✅ Git 三方一致（本地 = fajita = GitHub，HEAD 见 `git log -1`）。此前「尚未部署」「领先 8 个提交」「容器待重建」三条均已过时。
> **[[2026-11-22]] 状态快照**：**新前端（`ui-lazyvim`）已接管生产 `8080`**；本轮又做了一整轮「Vim 真实性」补齐（13 个 commit，见下方 checkpoint）。前端从此有 e2e 测试（`tests/test_frontend.py`，25 例），单测总数 **57 全绿**。
> **`ai-native` 分支**：课程作业形态（RAG 问答/划词写作/AI 建链 + 批量扫描/周报生成/引用 chips），mock 全链路 E2E 通过，待验收后决定是否合并，见 `docs/ai-native.md`。

## ✅ Checkpoint 2026-11-27（用户手报四条 + 中文输入法一整层）

用户在 `kb-issue` 的 `new_issue` 段里直接写了四条抱怨，这一轮全部修完并落测。
一句话结论：**四条里三条是「界面上看不出、按一次就暴露」的静默失效，一条是
硬编码默认值；每条都补了真浏览器 e2e（130 → 全绿）**。

| # | 修的是什么 | 实测证据 | commit |
|---|---|---|---|
| 68 | **中文全角标点打不开命令行 / 不触发引用补全** —— `：`(U+FF1A)、`／`(U+FF0F)、`［［`/`【【` 全部无反应 | 全角冒号在编辑器里必须落到**同一个** `.CodeMirror-dialog`（ASCII `:` 走的就是它，`:w`/`:q!` 都在那边执行）；阅读视图里则开应用层 `#cmdline`；全角括号要弹 show-hint 且候选是真实页面名 | `952e129` |
| 69 | **任务快捷添加写死 `p2`** —— 输入框里写 `p1 标题` 仍建成 p2（`p1` 被当成标题一部分） | 实测落库行从 `p2 2026-01-01 p1 写文档` 改成了 `p1 2026-01-01 写文档`；`3 修脚本`→p3、`P4 打杂`→p4、不带优先级仍 p2 | `952e129` |
| 70 | **`yy` 只进内存寄存器，`Ctrl+V` 粘不出来** | 先写哨兵值到系统剪贴板，`yy` 之后 `navigator.clipboard.readText()` 读到的是那一行；`yiw` 只读到那个词（≤8 字符） | `952e129` |
| 71 | **中文输入法在 normal 模式敲字母「完全没响应」**（未记录，修 #68 时顺手发现） | 组字结果 `j` 被当成 vim 的 j（光标下移一行）且**不写进正文**；组字结果是真中文时不动光标、不插入 | `952e129` |

### 这一轮的方法论收获

- **「两种入口」必须落到同一套界面**。编辑器里的 ASCII `:` 到不了应用层 ——
  CodeMirror 的 vim 在隐藏 textarea 上先吃掉它，打开自己的 ex 对话框。第一版
  把全角冒号接到应用层 `#cmdline`，结果是「阅读视图能用、编辑器里换了一套
  不一样的 ex 界面」。正确做法是喂给 vim（`handleKey(cm, ':', 'normal')`）。
- **capture / bubble 的位置决定成败**。应用层 keydown 是冒泡阶段，而键盘事件
  在隐藏 textarea 上被 vim 先接走、冒泡到 document 时又被「输入框只处理 Esc」
  那条 return 掉 —— 全角冒号的分支必须写在 **capture** 阶段。
- **Playwright 打不出全角字符，`new KeyboardEvent` 的 `keyCode` 恒为 0**。
  前者要用 `evaluate` 派发带 `key` 的合成事件；后者进不了 CodeMirror 的
  keymap（它按 keyCode 查表），ASCII oracle 必须用真实 `keyboard.press`。
- **测试期望值不能硬编码部署前缀**。`/kb/` 是 nginx 那层的，容器直连是 `/`。
  写死任何一边，另一半环境的那批用例就假红 —— 统一走 `base_path()`（从
  `KB_E2E_URL` 推导）。同理默认 URL 从 `:8081/kb` 改成 `:8080`：8081 现在
  挂了 basic auth，直接打是 `ERR_INVALID_AUTH_CREDENTIALS`。
- **`yy` 写剪贴板必须在用户手势的同步栈里**。`navigator.clipboard.writeText`
  在 keydown 里属于 transient activation，先 `await` 别的东西就变
  `NotAllowedError`；失败要退回 `execCommand('copy')`（`<textarea>` + select）。
- **包装 `pushText` 必须把返回值返回出去**。这是 AGENT.md 陷阱 28 的同一个坑
  （monkey-patch 吞返回值 → 调用方拿到 `undefined`，静默坏）。
- **IME 组字回滚不能用 `setValue`**。`setValue` 会清空撤销历史，用户之前
  的 dd/u 全丢 —— 只 `replaceRange('', from, to)` 删掉组字那一段。

## ✅ Checkpoint 2026-11-22（前端 Vim 真实性）

用户诉求：「连续自主推进，定位 bug → 记 todo → 每修一次提交一次，不用等我确认」。
一句话结论：**Vim 该有的动作基本补齐了，且每一项都在真浏览器里按过键**。

| # | 修的是什么 | commit |
|---|---|---|
| 1 | **`/` 变「未知命令」** —— `openCmdline` 只写 DOM 不写 `cmd.prefix`，永远停在 `:`，`/树莓` 被当命令执行 | `884ef2c` |
| 2 | **编辑器里 `/` 抛 TypeError** —— 模板引了 `dialog.css` 却没引 `dialog.js`，`cm.openDialog` undefined → vim 退化成 `prompt()`，`pushInput` 读 `null.length` 打断整个 keydown | `f10b2d5` |
| 3 | **`/all` 文案骗人**（写着「j/k 移动 · Enter 打开」但两样都没实现）→ 列表光标 + `Enter` + 计数前缀 `5j`/`3G` | `de99229` |
| 4 | **缺跳转历史** —— 点链接进下一页后回不去 → `Ctrl-o` / `Ctrl-i` + `space g o`/`gi` | `11f1156` |
| 5 | **阅读位置不记忆**（离开 1200 → 回来 0） | `0ecaefe` |
| 6 | **缺阅读动作** —— `Ctrl-d/u/f/b`、`H/M/L`、`zz/zt/zb`、页内搜索 `n/N` 全无；`/` 在正文页改走页内搜索 | `0152971` |
| 7 | **`q` 只删 tab 不关页面**（关了个寂寞）→ `:bd` 语义：关掉并切走 | `eca6d44` |
| 8 | **前端零 e2e 测试** → `tests/test_frontend.py` 25 例真浏览器（无 chromium 时自动 skip） | `e70413f` |
| 9 | **which-key 键位重复** —— `space b o` 被跳转键覆盖成「上一跳转」；另 13 个命令没进键位表 | `2328753` |
| 10 | **断网只剩 20 字** —— SW 只缓存静态壳，API 数据零缓存 → GET 本地镜像 + 离线空状态 | `4048ae4` |
| 11 | **缺 `gf`** —— 笔记最常做的「顺着链接往下读」只能鼠标点 | `9e2b991` |
| 12 | **缺 marks** —— `m{a-z}` 打标记 / `` `{a-z} `` 跳回（跨会话保留，`:marks` 可列） | `854fda6` |
| 13 | **命令行无历史** —— `:` 里 `↑` 只有浏览器原生「上一格」→ 真历史 + `Ctrl-p/n` | `6c7e7cc` |

### 只有真按一次键才暴露的坑（都写进 commit message 了）

- **`n` 三次都停在第 1 处**：靠「命中在不在视口里」判下一个，命中本来就在视口内时永远判成第 1 个 → 改显式命中游标 `state.hitIdx`
- **`` `z `` 报的还是上一条 toast**：`m`/`` ` `` 前缀块写在 `q` 键附近（很靠后），`z` 前缀分支先把第二个键吃掉 → 前缀块必须放在所有单键动作之前
- **Escape 关不掉命令行**：document capture/bubble 各挂探针才发现按键在两者之间被 `stopPropagation`，`cmd.open` 恒 `true`，之后按 `:` 冒号被打进输入框显示成 `":xxx"`
- **同一个 lhs 两条定义会静默互相覆盖**：我犯两次（`space b o` 被跳转覆盖、`mr` 撞 `m` 叶子）。已在 `composeLeaderSpec` 里做去重合并 + e2e 断言「无重复 lhs」
- **`/all`→KB 继承滚动位置 900**：`innerHTML` 换掉不重置 `scrollTop`，没有记忆时必须**显式归零**
- **e2e 首跑抓到 3 个问题**：`5j` 预期写错（光标初始在首行之前，`5j` 到索引 4）；两个离线用例被自己的 `tearDown` 判红（断网必然产生 resource 加载失败，加白名单）

### 本轮追加（2026-11-23 上午，nvim 差分对照 + iw/aw）

| # | 修的是什么 | commit |
|---|---|---|
| 40 | **搭起 nvim 差分对照装置** —— 用 `nvim --headless -u NONE` + `nvim_feedkeys` 喂同一段文本、同一组按键，逐点比「光标（行,字符列）」与「缓冲区全文」。排掉三个假差异坑：页面尾部换行、nvim 的 col 是**字节**偏移（`charidx` 换算）、Playwright `press()` 只认单键 | — |
| 41 | **`iw`/`aw` 在空白上的选区错** —— `expandWordUnderCursor` 第一件事就是 `while (!test(...)) idx++`，光标在空格上时跳过空白去删下一个词。实测 `'foo(bar, baz)'` ch=8：nvim 删掉空格得 `'foo(bar,baz)'`，我们删掉 `'baz'` 得 `'foo(bar, )'`。新增 `wsAsWord` 参数（`*`/`#` 走同一函数但语义不同，不能一起改） | `dda05b2` |
| 42 | **`aw` 后面没词时光标不对** —— vim 文本不动但光标挪到「空白结束处夹到行长-1」（`'foo  '` ch=3→4、`'   '` ch=1→2）；只 `return null` 会让光标原地不动 | `dda05b2` |
| 43 | **yank operator 无条件 `return oldAnchor`** —— vim 的字符级 `y{motion}` 把光标留在区域起点 `min(anchor, head)`，行级才原地不动。`yb`/`y0` 这种向后运动锚点恰是区域**尾**，全错位。偏差一路传到 `P`（`'foofoo  '` 上 yiwP 把词劈开） | `dda05b2` |
| 44 | **`P` 的光标多走一位** —— paste 的 `P` 分支用 `idx + text.length`（最后字符的下一格），`p` 用 `idx + length - 1`。nvim 实测 `ylP`→(0,2)、`y2lP`→(0,3)、`y$P`→(0,5)，全是 `idx+n-1` | `dda05b2` |
| 45 | **iw/aw 6 例落测** —— 期望值取自真 nvim 实测；含 `yiw` 的 8 列逐列对照表 `[0,0,0,3,3,5,5,5]`。踩了个坑：拿 ch=8 比 `'foo  bar'`（下标 0..7）是越界，nvim 会夹到 7 再算 | `9799d82` |

**96 点矩阵（8 组文本 × 每列 × diw/daw/ciwZ/yiwP）：修前 43/96 一致 → 修后 96/96，0 文本差异。**

### 本轮追加（2026-11-23 凌晨，行操作 ex 命令族）

| # | 修的是什么 | commit |
|---|---|---|
| 35 | **`:g` / `:v` 是死的** —— `vim.js` 的 `defaultExCommandMap` 里根本没有 `delete`/`normal`/`join`/`put`。`global` 的实现是「逐行拼 `:{行号}{cmd}` 再分发」，所以 `:g/a/d` 去执行 `:2d` → 报 `Not an editor command`，一条都不删。`:g/pat/normal dd` 同样死 | `7852e69` |
| 36 | **`:del` 语义冲突** —— 补上 `:d`/`:delete` 后 `'delete'.indexOf('del')===0`，`:del`（原「删当前页」）被前缀匹配抢到行删除，用户以为删页、实际只删一行。取舍：`:d`/`:delete`/`:del` 全删行（对齐 vim），删页改 `:Delete`（对齐 netrw），走确认框 | `7852e69` |
| 37 | **`:normal` 不用 range** —— `exRange(p)` 算了却没挪光标，`:g/beta/normal dd` 每次 dd 都作用在光标行（第一行），实测删掉 `# 测试用`、beta 行不动 | `7852e69` |
| 38 | **`defineEx` 只知道 `params.args`** —— 行号范围（`line`/`lineEnd`）、`argString` 全丢，带 range 的命令永远只能作用在光标行。改成透传整个 `params`，并加第三个 `prefix` 参数（`defineEx('delete', fn, 'd')` → `:d`/`:delete` 都命中） | `7852e69` |
| 39 | **行操作 8 例落测** —— 全部断言真实缓冲区内容 + 页面 API 回读；`:pu` 断言第 6/7/8 行逐条、原来第 5 行被顶到第 5 行 | `3d4eae0` |

### 本轮追加（2026-11-23 凌晨，触屏数字盘）

| # | 修的是什么 | commit |
|---|---|---|
| 31 | **数字盘被目录面板压住** —— `#numgrid` 的 `z-index: 70` 也低于 KBPanel overlay 的 90。这是上一轮 `#fxdock`(55) 的**同族第二处**：上轮只修了 55，没顺手查同族的其它浮层。现在层级关系固定成一条：触屏入口(96/97) > overlay(90) > scrim(89) > dock(40) | `79e0355` |
| 32 | **数字盘遮罩点不动关不掉** —— 唯一出口是右上角 32px 的 ×，触屏没有物理 Esc。加守卫 `if (!e.target.closest('.ng-sheet')) closeNumGrid()`（用 `closest` 而非 `e.target === nx`，否则点格子也被当成点遮罩） | `79e0355` |
| 33 | **数字盘打开时不锁背景滚动** —— 在遮罩上滑动会滚到下面的正文（`view.scrollTop` 0→650），而 sheet 内部不滚，同一块屏幕两种行为。加 `body.ng-open { overflow: hidden }` | `79e0355` |
| 34 | **触屏横滑手势落测** —— 阅读态左滑切下一个缓冲 / 右滑切上一个；编辑器里横滑**不能**切（正在打字丢内容更糟）。只能用 CDP `Input.dispatchTouchEvent` 派发真实序列，`keyboard`/`mouse` 都触发不了 | `4cff598` |

### 本轮追加（2026-11-23 凌晨，触屏可达性）

| # | 修的是什么 | commit |
|---|---|---|
| 29 | **触屏动作坞被目录面板盖住 8/12 个按钮** —— `#fxdock` 的 `z-index: 55` 低于 KBPanel 的 overlay(90)/scrim(89)。390×844 打开 `/page/KB`，目录面板 240×506 正好压在动作坞展开区上，`elementFromPoint` 有 8 个按钮命中的是 `.toc-item`，Playwright 的 `locator.tap()` 直接 Timeout 6000ms。**触屏上动作坞是唯一命令入口**（无键盘、无 which-key），被盖住等于入口没了。改 `z-index: 55 → 96` | `63d56b6` |
| 30 | **目录关掉后每次切页又自己回来** —— `api.on('page')` 无条件 `refresh()`，`refresh()` 也不检查「用户是不是关过」。加会话记忆 `localStorage['kb-toc-off']`：`refresh()` 开头 `if (isOff()) return`，点 × / `:toc` 关闭 / `space u T` 关闭都 `setOff(true)`，`:toc` 打开 `setOff(false)` | `63d56b6` |

**这一轮的方法论**：前几轮都在桌面 1280 上探测，一切"正常"。触屏 390×844 一开就暴露 z-index 层叠与「点了没反应」——因为桌面根本没打开动作坞（`isTouchUi()` 为 false 时 `renderFxPanel` 直接清空）。**多端要求不能靠在单端推理，必须真的换视口点一遍。**

### 本轮追加（2026-11-23 凌晨，折叠）

| # | 修的是什么 | commit |
|---|---|---|
| 27 | **`za` 折叠整个不存在，而且是两层缺失** —— `static/vendor/codemirror/` 里躺着 `foldcode.js`/`brace-fold.js`/`markdown-fold.js` 三个 addon，`templates/index.html` **一个都没引**（`typeof cm.foldCode === 'undefined'`），`sw.js` 的 SHELL 也没有；同时 `vim.js` 的 defaultKeymap 里 `z` 前缀只有 `zz/zt/zb` 五条滚动，`za` 系列全无。补 3 个 addon + `defineAction`/`mapCommand` 注册 `za`/`zo`/`zc`/`zR`/`zM` + ex 层 `:fold`/`:unfold`；sw VERSION v18→v19 | `7af840e` |
| 28 | **`zM` 计数谎报** —— `cm.foldCode` 在「这一行没有可折的 block」时**静默返回、不抛异常**，第一版拿「循环跑了几行」当处数，71 行的文档报出「已折叠 71 处」而实际只折了 10 处。改成数 `__isFold` 标记数量的变化（before/after delta） | `7af840e` |

### 本轮追加（2026-11-23 凌晨，注释与缓冲内跳转）

| # | 修的是什么 | commit |
|---|---|---|
| 25 | **`gcc`/`gc` 完全没实现** —— `vim.js` 里只有 `{keys:'gc', type:'operator', operator:'comment'}` 这条**键位声明**，而 `operators['comment']` 根本没写，所以按 gcc 既无反应也不报错。Markdown 的注释是 `<!-- -->` 而非 `//`，不能套 CM 的 comment addon。自己实现：整行包围/去掉、多行先判方向（全有则全去）、`gcc` normal / `gc` visual、visual 后主动 exit。**第一次仍漏了 count** —— action 回调签名是 `(cm, actionArgs, vim)`，`2gcc` 的行数在 `actionArgs.repeat`，写成 `() =>` 就只注释一行 | `619d500` |
| 26 | **编辑器里 Ctrl-o/Ctrl-i 被应用级 capture 处理器抢走** —— app.js 的 document capture 里无条件 `preventDefault + jumpTo(±1)`，那是**阅读态**的路由跳转历史；而 CodeMirror 的 vim 有 `jumpListWalk`，`/`、`n`、`G`、`%`、marks 的动作全带 `toJumplist`，是**缓冲内**语义。抢走之后编辑器里按 Ctrl-o 光标纹丝不动（应用 jumplist 里根本没有编辑器内的跳转）。改法：`if (state.editing) return;` 放行 | `619d500` |

### 本轮追加（2026-11-23 凌晨，ex 命令层）

| # | 修的是什么 | commit |
|---|---|---|
| 20 | **`gf` 在编辑器里完全不存在** —— CodeMirror 的 vendored vim 全文没有 gf。KB 里「光标下的文件」就是 `[[页面名]]`，语义明确：跳到那个页。用 `Vim.defineAction` + `mapCommand` 注册（`mapCommand` 必须传第 5 个 `extra` 参数，否则 `for (var key in extra)` 抛错）。跳转不要自己 `closeEditor()`（脏缓冲会拒绝，那是 `:q` 的语义），交给 `navigate()` 让它走 `guardDirtyNav` | `1c6935b` |
| 21 | **`:t` / `:m` 复制行、移动行**。`:m` 在 CodeMirror 里是「range 移动光标」，`:m+1` 被当参数丢掉 → 未知命令；`:t` 完全没有。**坐标系统连错三轮**：`:t0` 落第二行 / `:m+1` 原地不动 / `:m+1` 跳两行。根因是「锚点行号 → 插入索引」的 ±1 换算方向随原行位置而变。归纳成 `insertIndexFor()` 直接返回插入索引 + `insertLineAt()` 只负责插，删行统一删到「下一行行首」（`ch: null` **不是**行尾） | `1c6935b` |
| 22 | **数字形态 `:t0`/`:m0` 被吞成命令名** —— vim 的 ex 命令名正则 `/^(\w+|…)/` 里 `\w` 含数字，`m0` 变成叫 "m0" 的命令。0-9 各显式注册一遍 | `1c6935b` |
| 23 | **`:e!` 会静默清空缓冲区** —— `!` 被当成文件名，去打开一个叫「!」的页面（404 → `load('')` → 2172 字变 0 字，无任何提示）。与 `be9da72` 的 `:q!` 是同一个根因的两半：`!` 作为**命令的一部分**（`:q!` 拆成命令名 "q!"）处理过了，作为**命令后的第一个字符**（`:e!` 拆成 "e" + "!"）漏掉了 | `d587d65` |
| 24 | **一批「已实现但没注册名字」的 ex 别名** —— `:bn/:bp/:ls/:only/:noh/:up/:update/:earlier/:later` 全报未知命令，而 `runCommand()` 里早就都实现了。补别名，另加两个语义兜底：`:up`/`:update` 有改动才写、`:earlier`/`:later` 退化到 undo/redo | `d587d65` |

### 本轮追加（2026-11-23 凌晨，编辑器退路）

| # | 修的是什么 | commit |
|---|---|---|
| 18 | **`:q!` 是死路** —— `defineEx('q', () => closeEditor())` 把 CodeMirror 传进来的 `!` 参数整个丢掉（解析层给的是 `params.args = ['!']`，是回调签名 `()` 没接），于是 `:q` 与 `:q!` 完全同义，改坏了东西**退不出去**，只能去点 tabline 的 ×（那条还会静默丢内容）或被迫 `:w` 存坏内容。补 `bang()` 判定 + `:wa`/`:wall`/`:bd`/`:bdelete`/`:qa`/`:qall`，并让 `closeAllBuffers()` 之后回首页 | `be9da72` |
| 19 | e2e 补 4 条退路用例，**顺手抓到自己的测试污染生产库**：第一版直接在 `/edit/KB` 上打字再 `:wa`，把 KB 页首行写成了 `QQ#public`，而断言只看 toast 所以「测试通过了」。改成每条用例一个临时页 `__e2e-excmd-scratch`（建→跑→删），`:wa` 那条额外回读 API 确认真的落盘。已用 id=962 的干净快照还原 KB 页并清掉 4 条脏版本 | `eba4e23` |

### 本轮追加（2026-11-22 晚，路由真实性）

| # | 修的是什么 | commit |
|---|---|---|
| 14 | **`/kb/theme` `/kb/health` `/kb/searches` `/kb/trash` 静默坏掉** —— 路由词表存在三份（`main.py` / 模板正则 / `app.js` BUILTIN），模板那份只认识 9 个内置词，于是把整个路径当成部署前缀 → manifest 与 sw.js 请求到 `/kb/theme/…`，被后端回落成 HTML，浏览器报 `unsupported MIME type`（PWA 装不上、离线壳注册失败）。**页面自身看起来完全正常**，所以藏了很久 | `449e106` |
| 15 | **深链接打开是空的** —— 插件只写 `routes` 不写 `api.view()`，影子视图 load 完又 dispatch 自己 → 空首页；`/kb/theme` 每次打开都翻转配色；`/kb/trash` 压根不在 `parseRoute` 里 | `2e3ba35` |
| 16 | **空行按 `ciw` 抛 TypeError 并打断整个按键流** —— `expandWordUnderCursor` 返回 null，`vim.js` 末尾无条件读 `tmp.start`；之后所有按键都进不了编辑器（看着像卡死） | `3cd2bd2` |
| 17 | 前端 e2e 补 7 个用例锁上述三处（`TestRoutes` 5 + `TestVimTextObjects` 2），**64 全绿** | `2c45a25` |

### 已知还差的（未开工，记在 `.pi/todo.json`）

- `:wa`/`split` 分屏、`%` 括号跳转、搜索历史 `/` 复用上一条
- `ai-native` 的 776 行 AI 功能要迁到新架构（必须重写，不能硬合）
- 新前端何时并入 `main`（根治「生产挂 worktree 目录」的脆弱性）

## ✅ Checkpoint 2026-11-21

前端可访问性（P6 8/8）与对外开放稳定性（P7 4/4）都已上线，全部 commit 已推送 GitHub。当前进度：

| 批次 | 状态 | 备注 |
|------|------|------|
| P0 索引与后端 | ✅ 4/4 | 标签解析、搜索分词/重排、get_page 结构化、侧栏横滑 |
| P1 MCP 工具演进 | ✅ 2/2 | 行级读写（append_to_page / delete_lines）；全库共 17 个工具 |
| P2 前端 | ✅ 9/9 | 含前端全量重写、移动端适配、日记保存修复 |
| P3 可选/待决策 | ⏸ 2/4 完成 | 剩 2 项属决策/权限，**主动暂缓**（见下） |
| P4 优化 backlog | ✅ 12/12 | 备份、草稿、体检、图谱力导向、搜索片段、TOC、键盘流… |
| P5 生产环境修复 | ✅ 7/7 | 时区、URL 片段、CJK 标签、改名、孤儿 ref、MCP HTTP、测试 |
| P6 前端可访问性 | ✅ 8/8 | 见「本轮改动」 |
| P5' AI-native（分支） | ⏸ 5/12 | 演示前 5 项已做；合并后 4 项依赖真 key；展望 3 项需决策 |
| P7 对外开放与稳定性 | ✅ 4/4 | 公开只读子集、MCP 搜索 SQL、500 页上限、HTTP 会话重启即坏（[[2026-11-21]] 全部上线） |

**三方一致**：本地 `main` = fajita = origin/main（`git log -1` 当前 `dc2d98b`）；生产 4 个容器全部 healthy（`kb` / `kb-mcp` / `kb-mcp-public` / `kb-ui-test`）。
**生产已真实生效的最新进度**（[[2026-11-21]] 实测）：页面数不再卡 500（`/api/pages/count` → 579）、MCP 搜索 SQL 修复（`frowid` → `f.rowid`）、公开只读子集端点 `kb-mcp-public`（8 个 `public_*` 工具 / `127.0.0.1:8100` / 11 个 `#public` 页）上线。
**数据规模**（[[2026-11-21]] 生产库实测）：579 页面 / 3182 链接 / 124 标签 / 3610 refs / 290 孤立页 / 1286 断链边（157 个目标）。

## 当前未完成（16 条）

> 按可动手程度排序。上面 checkpoint 表是已完成批次，这里是剩下的。

### 小改动，随时可做（前端）

- [ ] **体检报告也是单列 22880px**：展开后一次直下 290 个孤立页 + 157 个断链目标（[[2026-11-21]] 实测），与已修的 `/all` 是同一个密度问题。改法同款：按类分组折叠、断链按引用次数排序
- [ ] 主按钮 `--accent-hover` 在 dark 下是更亮的蓝，hover 时页面对比度略降（4.5 边缘）
- [ ] 边框 `--border` 对比度 1.17–1.34:1（**故意保留**，装饰性分隔线，非 WCAG 要求——提亮会让界面变 GitHub 那种重分割感）

### 需要你拍板（数据）

- [ ] **图谱虚假超级节点**：生产库 [[2026-11-21]] 实测 1286/3182 条链接（40%）指向不存在的页面，落在 157 个目标上。TOP4 —— `成果与进度` 225 / `状态` 225 / `任务栏` 179 / `工作日报` 171 —— 是 Logseq 日记模板的结构位，共 800 条边（62%），从未创建过这些页。三个方案待选：① 补建 4 个占位页（一次消掉 800 条边） ② 解析层把模板结构位排除出 refs（根治，需维护词表） ③ 仅图谱降权（纯前端零风险，断链报告仍会列）

### 明确暂缓（主动决定，不是遗漏）

- [ ] 飞书：直接调接口或自动同步（`drive.write`、`im.message.send` 权限仍未申请）；「search 取数 → AI 处理 → update 回写」流程与权限配置属此范畴

### AI-native 合并主线后（依赖真 key）

- [ ] embedding 语义检索：SQLite 存向量 + 与 FTS 混合排序
- [ ] edit 流式插入编辑器：SSE 逐字插入
- [ ] 回答引用防编造：服务端校验回答里的 `[[wikilink]]` 是否来自本轮检索集合
- [ ] 成本/上下文预算显示：每轮显示注入页数与字数

### AI-native 展望（二期 / 需决策）

- [ ] 聊天内执行操作：「把这段结论记到 XX 页」→ AI 提议 append → 人确认 → 执行
- [ ] 审链模式：全库扫可疑/冗余链接
- [ ] 隐私数据流向说明：docs 补一节——哪些内容发给第三方 LLM、如何禁用

### 路线图（README 里躺着，按需启动）

- [ ] 插件系统（slash commands 注册制）— 中，写日记时可能最有用
- [ ] block reference `((uuid))` — 高，需重构解析层
- [ ] 多用户 JWT — 高，单人用不需要
- [ ] 实时协同 WebSocket — 高，单人用不需要

## 本轮改动（P6，2026-11-17）

审计方式：Playwright 真实浏览器，2 主题 × 5 路由，逐元素算 WCAG 对比度、枚举 focusable、扫 emoji。

| 项 | 改前 | 改后 | commit |
|---|---|---|---|
| 侧边栏导航键盘可达 | Tab 全部跳过 | Tab 可达，Enter 触发，Ctrl/中键新标签 | `620b81f` |
| `--muted` 对比度 | 2.54–4.24:1（63 处引用） | 5.02–5.85:1 | `bc4546b` |
| `--on-accent` | dark 白字 2.52:1 | 深字 7.14:1 | `bc4546b` |
| `--on-priority` | light p2/p3/p4 白字 2.2–3.2:1 | 全 ≥4.5（p2/p3/p4 压暗保色相） | `bc4546b` |
| embed/dashboard 里 `<a>` | UA 默认蓝 1.82:1 | 全局 accent 着色 | `bc4546b` |
| CodeMirror 行号 | light `#999` 2.66:1 | `var(--muted)` ≥4.6:1 | `67be335` |
| 焦点样式 | `outline:none` 无替代 | 统一 2px accent 环 + 输入框内环 | `bc4546b` |
| 500 页列表 | 18201px 单列 | 4523px，auto-fill 网格（4/2/1 列） | `bc4546b` |
| 119 个标签 | 一堵墙 | 折叠 18 + 筛选框 | `bc4546b` |
| 无名称控件 | 0 处 aria-label | 30 处 | `bc4546b` `3e50171` |
| toast 播报 | 无 | `role=status` + 错误切 assertive | `bc4546b` |
| emoji 图标 | ~50 处 | 0（Lucide 50 符号内联，10KB 零请求） | `bc4546b` |
| SVG stroke 缺失 | dark 下图标完全隐形 | `fill:none stroke:currentColor` | `edbe9ec` |

顺手修的真 bug（非 review 项）：`<body>` 重复标签、`:hover` 重复规则、`rec-badge` 模板串引号错误、历史按钮图标只替换了一处。

## 本轮改动（P7，2026-11-21）

| 项 | 改前 | 改后 |
|---|---|---|
| MCP `search` 的 SQL | `JOIN pages p ON p.id = frowid`（`no such column`，被 LIKE 兜底吞掉） | `f.rowid`，FTS 正常走索引 |
| 「所有页面」页数 | `/api/pages` 卡 `limit=500`，计数冻结在 500，501 页后看不到 | 新增 `GET /api/pages/count`，标题显示真实 579，超出的走 offset「加载更多」 |
| 对外开放 | 全量 17 工具读写端点，不能给别人 | 新增 `kb-mcp-public`：8 个 `public_*` 只读工具，SQL 层强制 `#public` 白名单 |
| 只读性 | — | 连接层 `mode=ro` + `PRAGMA query_only=ON`（`data/` 不能挂 `:ro`，WAL 要写 `-shm`） |
| 元数据泄露 | `links` 会列出私有页名（实测 4 个） | `links` / `backlinks` / `tags` 全部过公开子查询 |
| 存在性探测 | 能区分「不存在」与「存在但私有」 | 统一 `Page 'x' not found` |
| MCP 会话 | 有状态，服务端重启后客户端持旧 `mcp-session-id` 永久 404，须连带重启机器人 | 两个端点 `stateless_http=True`，只重启容器即可 |
| 部署体积 | 改工具实现要重建 449MB 镜像 | `mcp_server.py` / `mcp_public.py` 挂载 `:ro`，改代码不重建 |

## BUG（原始诉求，尚未逐条立项）

> 以下是记在原 KB 页里的原话，保留原样。真正落地的条目已拆进 P0–P6；下面这些还没动。

部分页面的标签解析依旧有问题，无人使用的标签似乎没有自动删除
是不是可以加分屏呢？不添加按钮而是绑定快捷键，类似文件管理器分屏是F3
> 对于高级用户来说，用命令比翻按钮快得多，那我是不是可以参考一下这个设计，我去添加一个模式切换之类的，可以用按钮，也可以绝对极简，除了编辑器就是输入命令，只用命令实现（切换页面，搜索，进入编辑，新建，深浅色切换，增删改任务）
但是对于普通用户或者是其他设备，例如手机，那么快速的点击的方案还是要留着的，那么这两种存在形态应该怎么共存呢，是什么手动切换还是单独开发两个版本呢

要在任务那个页面加一个跳转到编辑的按钮
这个页面应该更简洁一些，不是这AI怎么回回给我写一大堆废话，这个真是有毛病
任务的甘特图功能能不能直接拖拽改变任务的持续时间
不太理解的是：为什么日记页面保存就延迟比较高，而编辑页面里面的保存，似乎就没什么延迟

正文页面需要一个快速跳转到编辑的办法，看文章看到一半想改了结果得跳回文章开头，再拉下来，太破坏体验了

## P0 索引与后端（影响所有 AI 协作）

- [x] **标签解析太贪婪**：正文行内任何井号开头的文字都会被登记为真标签（实测：中枢页表格里的全角括号内容被登记成带右括号的标签）。建议：匹配限定”词字符/汉字/连字符”字符集；跳过代码块与行内代码；剥离尾部标点。（[[2026-10-24]] 修复：TAG_RE 词边界 + `[\w-]` 字符集 + 代码块跳过，尾连字符剥离）
- [x] **搜索排序与分词**：① 多词查询“树莓-通识课 树莓-招新简介”返回 0 结果，疑似整串当一个词 → FTS5 按空格分词，做 OR/AND 组合；② 自己原来的判断：“搜索引擎神秘，至少把名字直接相关的页面放前面” → name 精确/前缀匹配加权重排。（① jieba OR 组合 [[2026-09-29]] 完成；② rerank_by_name 名称重排 [[2026-10-24]] 完成，REST API 与 MCP 共用）
- [x] **get_page 返回元信息与正文混排**：服务端生成的标题行和 Backlinks 头与正文在同一字段返回，AI 工具把返回值原样喂回 update_page 会污染正文。建议：返回体拆出独立 content 字段，或 update 时剥离已知头部。（[[2026-10-24]] 修复：get_page 返回 JSON，content 独立字段，可安全回喂）
- [x] 什么叫，你的tags侧边栏里面有个水平滑块？实际宽度有六七倍？（[[2026-10-24]] 修复：`#tags-section` 加 `overflow-x: hidden`，标签名 `overflow-wrap: anywhere` 折行；根因同上条贪婪解析）
## P1 MCP 工具演进

- [x] delete / rename / 版本快照 / restore 已实现且实测通过（2026-10-10）：rename 自动重写入站链接；删页自动存快照；删后仍可查版本并恢复
- [x] 新增“追加写入”与“逐行删除”工具（自己原来的想法）：todo 这类行级修改场景能显著减少 AI 全文覆盖式更新的纰漏（[[2026-10-24]] 实现 append_to_page + delete_lines，MCP 共 15 个工具）

## P2 前端（源自 KB 修改计划，[[2026-09-29]] 黑客松已完成大部分，需逐个复查状态）

- [x] 反向链接的预览功能是不是应该可以显示反向引用的原文的那一行（[[2026-10-24]] 实现：`parse.py` 新增 `find_link_lines`，API backlinks 带 `snippet` 字段，MCP `get_backlinks` 同样返回引用行；前端反链卡片显示原文行，两行截断）
- [x] 任务视图能在多个页面之间来回切换（[[2026-10-08]] 记；[[2026-10-24]] 实现：设置支持逗号分隔多任务页，任务视图顶部下拉即时切换，当前选择存 localStorage 独立键、校验回退）
- [x] 页面美观度：用 ui-ux skill 继续改进（[[2026-10-24]] 全量重写前端：CSS 设计系统全量重来 + 视图模板重构。时间分组首页/相对日期/侧边栏 active 竖条/图谱聚焦高亮（签名视图）/甘特全主题化/滚动条与 focus ring/空状态引导。回滚锚点 git tag `pre-frontend-rewrite`）
- [x] 手机端编辑页：侧边栏按钮单独成行，上方标题占位过多（[[2026-10-24]] 修复：编辑视图下移动端顶栏折叠为单行 ☰（MutationObserver 驱动 compact class，CSS 全部在 media query 内），editor header/footer 收紧，底部提示文字隐藏）
- [x] 侧边栏展开/收起按钮：放左上角标题旁，收起后留小标签可展开
- [x] CodeMirror：\[\[ 快捷命令弹窗点击后会吞掉前两个 \[\[
- [x] 实时预览加同步滚动
- [x] 编辑器与预览并排，分界线可左右拖拽
- [x] 全部页面浏览页（现在只有首页和日记）
- [x] 日记保存：复用编辑页 CodeMirror 导致不能凭空创建新页面 —— [[2026-10-07]] 本地已修好，**尚未部署到 fajita**（[[2026-10-25]] 在本仓库直接重修：根因是 saveJournal 走 PUT，而后端对不存在页面返回 404，每天首篇日记必保存失败——改为 PUT 404 时回落 POST 创建；顺修日期切换只改变量不加载内容导致的跨日覆盖隐患，切日期现在重载当天内容且未保存内容走草稿兜底。原设备上的修复不再需要找回）
- [x] todo 生成 SVG 甘特图：python 脚本渲染到前端，侧边栏加入口；一并解决“甘特图看板”需求

## P3 可选 / 待决策

- [x] mermaid 集成：语法支持 + web 渲染；todo cron job 改为定时把人工记录转成图表页（[[2026-10-24]] 实现：```mermaid 块渲染（懒加载 9.4.3 UMD + 主题 token 联动 + 错误降级 + 编辑预览防抖 + 斜杠命令）；cron 转图表页经评估**砍掉**——任务甘特已有实时视图，生成静态页会过时，改为任务视图一键"复制为 mermaid gantt 代码块"，数据流单向不产生衍生页）
- [x] 看板自定义：可指定显示某页面/图片内容（[[2026-10-24]] 实现第 1 期：首页即看板——约定页面（默认 `KB-看板`）存在则首页渲染，新增 `![[嵌入引用]]` 语法（深度限 2 层断循环、缺失页点击创建），看板是普通页面可 MCP 编辑；**图片上传搁置**（外链可用），`![[页面#节]]` 精确摘录留待第 2 期）
- [x] AI 参与度重审：免费/本地算力扛住之前，重新考虑“为什么走 AI 这一段”；自建 [[JournalCode]] + 自托管任务管理可能是更轻的解（[[2026-10-25]] 破题于 `ai-native` 分支：MCP 保留为「AI 从外部操作」不变，应用内新增三功能——RAG 问答面板/编辑器划词操作/保存后 AI 建链建议，OpenAI 兼容 + 免 key 优雅降级 + LLM 输出服务端硬校验，设计文档 `docs/ai-native.md`，兼作「智能软件开发」课程作业；主线合并待定）
- [ ] 飞书：直接调接口或自动同步 —— 见上面「当前未完成 → 明确暂缓」（避免两处状态漂移）

## P4 优化 backlog（[[2026-10-24]] 头脑风暴，按推荐顺序；部署完成后按序开工）

### 数据安全
- [x] 自动备份：fajita cron 每日 `sqlite3 .backup`，保留 30 份（工程习惯里的手动备份升级为自动）（[[2026-10-25]] 实现：应用内置后台任务替代宿主 cron——`backup.py` 用 `VACUUM INTO` 原子快照，启动 60s 首备 + 每 24h，保留份数/目录/开关走 env，容器化部署零依赖）
- [x] 编辑器草稿自动保存：CodeMirror 内容 localStorage 每 30s 一份，重开编辑器检测并提示恢复（防误关/崩溃丢字）（[[2026-10-25]] 实现：与打开时内容不同才存，编辑器顶部恢复条一键恢复/丢弃，保存/删除自动清理，日记支持跨日期草稿）

### 知识库健康
- [x] 孤立页 + 断链报告：一个视图出两个清单——零入链页面（整理孤岛）、指向不存在页面的链接（补建/清理）；图谱 API 数据已够，纯前端（[[2026-10-25]] 实现：独立视图方案否决（侧边栏臃肿），改为「所有页面」内折叠区同回收站模式；摘要后台预览、断链按目标聚合一键创建、自链不计入链）
- [x] 库统计：页数/链数/标签分布/更新频率，适合做成看板可嵌入的块（[[2026-10-25]] 实现：并入体检区顶部——统计行 + 标签 TOP8 chips；独立块方案从简）

### AI/MCP 路径减负
- [x] `search` 返回内容摘要：结果加 ~200 字 snippet，省掉一半"搜到再 get_page"的往返（[[2026-10-25]] 实现：`_snippet()` 命中词上下文 ±90 字）
- [x] `append_to_journal` 工具：append_to_page + 自动算当天日期，AI 记日记一步到位（[[2026-10-25]] 实现：默认今天/可指定日期，页面不存在自动创建）
- [x] `recently_changed(since)` 工具：增量拉取"上次看过之后改了什么"（[[2026-10-25]] 实现：接受日期或日期时间，MCP 共 17 个工具）

### 体验细节
- [x] 页面内目录（TOC）：长文 h1-h3 浮动侧栏 + 点击跳转，配合阅读列（[[2026-10-25]] 实现：h≥3 时页面头部“📖 目录”折叠条，缩进层级+跳转，短文自动隐藏；浮动侧栏方案因视口适配复杂改折叠条）
- [x] 键盘流：列表 j/k 导航 + Enter 打开；Ctrl+B/I 加粗斜体（[[2026-10-25]] 实现：全局 keydown 过滤输入焦点/输入法/移动端；坑：knavClear 重置索引导致 TypeError，已记 AGENT）
- [x] 图谱力导向布局：替代固定圆环，纯前端即可，签名视图升级（[[2026-10-25]] 实现：手写力模型斥力+弹簧+向心+alpha 冷却，rAF 驱动收敛即停；拖动 pin/松手 reheat，收敛后自动 fit）

### 检索增强
- [x] 搜索结果内容命中片段（Google 式摘要，现在只高亮页名）（[[2026-10-25]] 实现：`make_snippet` 下沉 search.py 三路共享，REST 与 MCP 同源）
- [x] 常用搜索/过滤器保存（[[2026-10-25]] 实现：搜索页“📌 固定”→ 首页 chips，≤8 个，点击再搜/✕ 删除）

## P5 生产环境修复与工程化（[[2026-11-13]] fajita 实测发现，全部已入库）

- [x] **容器时区**：无 TZ env 时 glibc 回落 UTC，SQLite `datetime('now','localtime')` 写入 UTC 时刻，而浏览器按本地渲染 → 全站时间戳差 8 小时（[[2026-11-13]] 新增 `timeutil.py`：import 即 `tzset()` 固定时区；compose 加 `TZ=Asia/Shanghai`；实测 06:53 → 14:53）
- [x] **URL 片段误判为标签**：`https://x.com/#grid12` 的 `#grid12` 被登记成真标签（`parse.py` 加 `URL_RE` 先行剥离）
- [x] **CJK 标签规则前后端漂移**：`第12周#Monday` 被后端拒但前端仍补全（后端 lookbehind 改 ASCII-only；前端 `TAG_TRIGGER_RE` 正文用 `\p{L}\p{N}`）
- [x] **改名误改标签**：rename 只重写 `kind='link'`（标签来自内容不是页面名）
- [x] **孤儿 ref 残留**：SQLite 默认 FK OFF → `ON DELETE CASCADE` 被忽略 → 删页后「无人使用的标签」仍显示（`mcp_server.py` 加 `PRAGMA foreign_keys=ON`；`list_tags` JOIN pages）
- [x] **MCP stdio 多进程开销**：每客户端一份 python（~80MB PSS ×4）→ 新增 `mcp_http.py` Streamable HTTP，compose 加 `kb-mcp` 容器（`127.0.0.1:8092`）
- [x] **服务器工作区与仓库分叉风险** + **仓库无测试**：全部整理入库（`bed4469`），新增 `tests/` 32 例

## P6 前端可访问性与信息密度（[[2026-11-13]] ui-ux-pro-max review 实测，[[2026-11-17]] 全部完成）

- [x] 侧边栏导航键盘不可达 → 加 `href` + 前缀改写（`620b81f`）
- [x] `--muted` 对比度 2.54–4.24:1 → 5.02–5.85:1；新增 `--on-accent` / `--on-priority`；embed/dashboard 内 `<a>` 掉 UA 蓝 1.82:1 → 全局着色（`bc4546b`）
- [x] 焦点样式被移除 → 统一 `:focus-visible` 2px 环 + 输入框内环（`bc4546b`）
- [x] 列表不随规模扩展 → `#all-pages-list` 网格（500 页 18201px → 4523px）；标签侧栏折叠 18 + 筛选（`bc4546b`）
- [x] 图标按钮无无障碍名称 → 30 处 `aria-label`（`bc4546b` `3e50171`）
- [x] toast 不播报 → `role=status` + 错误 `assertive`（`bc4546b`）
- [x] 未尊重减弱动效 → 已在重写时加入，实测 27 → 0
- [x] 图标用 emoji（~50 处）→ Lucide 50 符号内联 sprite（10KB 零请求）；顺带修 `<body>` 重复、`:hover` 重复、rec-badge 引号错误；SVG 缺 `fill:none stroke:currentColor` 导致深色主题图标隐形 → 根 `<svg>` 与 50 个 `<symbol>` 补齐（`edbe9ec`）

## P7 对外开放与稳定性（[[2026-11-20]]～[[2026-11-21]] 实测发现，已全部上线）

- [x] **MCP 搜索 SQL 拼错列名**：`JOIN pages p ON p.id = frowid` 实际是 `no such column: frowid`，整条 FTS 查询抛 `OperationalError` 被 LIKE 兜底吞掉 → MCP 的 `search` 一直走慢路径（[[2026-11-20]] 修为 `f.rowid`；本地临时库复现验证）
- [x] **所有页面页数卡在 500**：`/api/pages` 的 `limit` 上限 500，前端拿 `pages.length` 当页数 → 超过 500 后计数永久冻结，第 501 页之后的内容完全看不到（[[2026-11-20]] 后端新增 `GET /api/pages/count`，前端改真实总数 + offset「加载更多」；实测 568 → 579）
- [x] **公开只读子集端点 `mcp_public.py`**（[[2026-11-20]]）：8 个 `public_*` 工具，SQL 层强制 `#public` 标签白名单，三层隔离——① 无任何写工具 ② 每条 SQL 追加公开子查询 ③ 私有页与不存在页统一返回 `Page 'x' not found` 堵存在性探测；`links`/`backlinks`/`tags` 元数据同样过滤（曾泄露 4 个私有页名）；连接层 `mode=ro` + `PRAGMA query_only=ON` 强制只读（`data/` 不能挂 `:ro`，WAL 库要写 `-shm`）；新增 `kb-mcp-public` 容器（`127.0.0.1:8100`），与 `kb-mcp` 共用 build context 镜像层全缓存
- [x] **MCP HTTP 会话重启即坏**：FastMCP 默认有状态，服务端重启后客户端持旧 `mcp-session-id` 吃 `404 Session not found`，而 AstrBot 只在 `anyio.ClosedResourceError` 时重连 → 工具永久坏、必须连带重启机器人（[[2026-11-20]] 两个端点均改 `stateless_http=True`；实测只重启容器、不重启机器人，公开工具 7 次 0 失败）

## P5' AI-native backlog（`ai-native` 分支）

### 演示前（高价值低成本）
- [x] **全库批量链接扫描**：`POST/GET /api/ai/links/scan` 后台任务+轮询；体检区新增「AI 建链扫描」区（[[2026-10-25]]）
- [x] **日记/周报生成**：输入框「本周总结」预设 chip + `journal_days` 参数 + 回复「存为页面」（[[2026-10-25]]）
- [x] **聊天引用可见化**：`build_chat_context` 返回 `{text, pages}`，SSE 首帧发 `context_pages`，前端 cite-chips（[[2026-10-25]]）
- [x] **对话持久化**：`kb-ai-chat` 存最近 40 条（[[2026-10-25]]）
- [x] **多轮追问检索**：服务端拼最近 2 条用户消息作检索 query（[[2026-10-25]]）

### 合并主线后 / 展望

见上面「当前未完成」里的同名小节（避免两处打勾状态漂移）。

## 工程习惯（源自 KB 页风险提示，保持）

- 更新前先手动备份 kb 数据库文件（每日自动备份目录之外再加一层）
- fajita 上避免高权限命令
- 监控 SQLite 查询性能，必要时给 name 字段加索引


# 更新日志

> 时间线，只追加不回改。格式：日期 — 做了什么。

- [[2026-09-29]] — AI 黑客松途中完成 P2 大部分前端项（美团模型额度用完）
- [[2026-10-07]] — 深夜修好日记保存不能新建页的 bug（本地）
- [[2026-10-10]] — MCP 全链路实测通过（delete/rename/版本/restore 确认可用）；新建本清单
- [[2026-10-10]] — KB 页瘦身为纯「描述+架构」，三节迁移至本页
- [[2026-10-24]] — 标签解析收紧（P0 首条）：`TAG_RE` 词边界 + `[\w-]` + 剥代码块；tags 侧栏防横滑 CSS
- [[2026-10-24]] — MCP 三连：`get_page` 改 JSON、搜索名称重排下沉 `search.py`、`append_to_page`/`delete_lines`（共 15 工具）
- [[2026-10-24]] — 反链预览显示引用原文行（`find_link_lines` + `snippet` 字段）
- [[2026-10-24]] — 任务视图多页面切换（逗号分隔任务源页 + 下拉即时切换）
- [[2026-10-24]] — 手机端编辑页适配（顶栏折叠单行、header/footer 收紧）；修移动端预览切换 bug
- [[2026-10-24]] — 美化第 0+1 步：删 `applyCustomCSS` 内联 hack，Tokyo Night 全量入 CSS 变量，语义变量 `--tag/--overdue/--p1..p4`
- [[2026-10-24]] — 前端全量重写（骨架 + 8 视图）：时间分组首页、侧栏 active 竖条、图谱聚焦高亮、甘特全主题化、focus-visible、prefers-reduced-motion
- [[2026-10-24]] — 前端细节三连：搜索框自适应铺开、logo 靠左、侧栏宽度动画
- [[2026-10-24]] — mermaid 集成：懒加载 v9 UMD（v10+ 纯 ESM 内嵌浏览器加载不了）+ 主题联动 + 错误降级 + `/图表` 斜杠命令；cron 转图表页**砍掉**（静态生成物会过时）
- [[2026-10-24]] — 首页看板 + `![[嵌入引用]]`（深度限 2 断循环、缺失页点击创建）；图片上传搁置
- [[2026-10-25]] — P4 第一批：内置自动备份（`VACUUM INTO`）、草稿自动保存、MCP 三个新工具（共 17）
- [[2026-10-25]] — P4 第二批：j/k/Enter 键盘流 + 体检折叠区（孤立页/断链聚合）
- [[2026-10-25]] — P4 批A：搜索片段、TOC 折叠条、库统计、保存搜索；批B：图谱力导向（手写零依赖）。P4 12/12
- [[2026-10-25]] — 修日记保存（PUT 404 回落 POST + 切日期重载 + 草稿兜底）
- [[2026-11-13]] — **生产 6 项修复入库**（`bed4469`）：① 容器无 TZ 差 8 小时 → `timeutil.py` ② URL 片段误判标签 → `URL_RE` ③ CJK 标签前后端漂移 → ASCII-only lookbehind ④ 改名误改标签 → 只重写 `kind='link'` ⑤ 孤儿 ref → `PRAGMA foreign_keys=ON` ⑥ MCP stdio 多进程 → `mcp_http.py`。同时入库 32 例单测、运维脚本、`MCP_REMOTE.md`
- [[2026-11-13]] — 进度对齐（生产已部署、Git 已同步）；新增 P6 清单
- [[2026-11-17]] — **P6 全轮完成**（8 个 commit）：导航键盘可达、对比度（`--muted`/`--on-accent`/`--on-priority`）、焦点环、列表网格（18201px → 4523px）、标签折叠筛选、30 处 aria-label、toast 播报、Lucide 50 符号替换 emoji。实测 0 处对比度不达标 / 0 emoji / 0 缺失 symbol / 0 JS 错误
- [[2026-11-17]] — SVG sprite 缺 `fill:none stroke:currentColor` 导致深色主题图标隐形（浅色下显示为黑实心形状），根 `<svg>` 与全部 50 个 `<symbol>` 补齐 + CSS 兜底
- [[2026-11-17]] — 文档同步：README 补双传输 MCP / Docker 双服务 / 时区章节 / 解析层规则；AGENT 新增陷阱 #32–#37
- [[2026-11-17]] — todo 整理为 checkpoint 格式（完成批次压成表格，只留待办与更新日志）
- [[2026-11-17]] — KB 文档校准：`fajita服务器` 服务清单里 KB MCP 从「stdio / 13 工具」更正为「Streamable HTTP / `127.0.0.1:8092` / 17 工具」，拆出 KB 双容器说明（`kb` + `kb-mcp` 共用 `./data`），补 KB 代码更新流程与非 root uid、IP 白名单/可信代理；`kb-MCP` 页重写为双传输（HTTP 优先 / stdio over SSH），工具清单 13 → 17 并补行级修改与版本分组
- [[2026-11-17]] — 本页与仓库 `todo.md` 合并对齐：补回此前丢失的 BUG/体验待办段与 P0–P5' 逐条细节，同时带上 checkpoint 表与 P6 改动对照表；修正过时状态（8 commit 已推送、三方一致 `6a0535a`、容器待重建）
- [[2026-11-17]] — 待办重排：把散落在 P0/P3/P5' 下的未完成项抽成「当前未完成（16 条）」总表，按可动手程度分六档（小改动/待拍板/暂缓/AI-native 两档/路线图）；P3 与 P5' 里重复的改成指针，避免两处打勾状态漂移
- [[2026-11-21]] — **P7 对外开放与稳定性 4/4 上线**（`ee99ea4`）：① MCP 搜索 SQL 列名拼错（`frowid` → `f.rowid`，此前一直静默走 LIKE 兜底）② `/api/pages/count` + 前端「加载更多」，页面数不再卡 500（568 → 579）③ `mcp_public.py` 公开只读子集（8 个 `public_*` 工具 / SQL 层 `#public` 白名单 / `mode=ro` 强制只读 / `kb-mcp-public` 容器 `127.0.0.1:8100`）④ 两个 MCP 端点改 `stateless_http=True`，服务端重启不再让客户端永久 404。本轮改动此前只存在于生产服务器工作区，已全部收编入仓库，三方（本地/fajita/GitHub）同 commit
- [[2026-11-22]] — **新前端接管生产 8080**（`570edda`）+ 停掉冗余测试容器（`eca1291`）；随后修 leader 双 handler 争用与 `[[` 补全四处缺陷（`7702967`）、配色重构为 Catppuccin 四口味且结构层/参数层拆开（`a83446f`）
- [[2026-11-23]] — `dda05b2` **iw/aw 文本对象对齐真 nvim**：搭了 nvim 差分装置
  （`nvim --headless` + `nvim_feedkeys` 逐点比光标与全文），一次抓出三处：`expandWordUnderCursor`
  只会向前找词（光标在空格上时删掉下一个词而非空格）、yank 后光标无条件原地不动（`yb`/`y0`
  这种向后运动锚点是区域尾，全错位并一路传到 `P`）、`P` 的光标比 `p` 多走一位。96 点矩阵
  43/96 → 96/96。全量 114 tests OK。
- [[2026-11-23]] — `d0fc7fb` + `62e9b78` **动作坞 / which-key 装不下时顶到屏幕外**：用户报
  「移动端编辑时找不到选项」，实测**是真的超出去了、而且没有任何滚动出口**。两处同族：
  动作坞（`#fxdock`，`bottom:44px` 向上生长、`overflow:visible`、无 `max-height`）12 条动作
  约 523px 高 → 320×568 顶边 -55px（1 条点不到）、844×390 横屏 -233px（5 条）、740×360 -263px
  （6 条）；which-key 同形状 28 条约 758px → 900×420 顶边 -372px（13 条点不到）、800×340 -452px
  （16 条）、1024×500 -292px（10 条）。触屏上动作坞是**唯一**命令入口（无键盘/无 which-key/无
  `:ex`），少一条就是少一个功能。修法：限高把顶边钉在 ≥16px，**必须用 `dvh` 覆盖 `vh`** ——
  软键盘弹起时 `100vh` 不变、可视区却矮一半，用 vh 算出来的高度照样顶出去（这正是「编辑时」
  点 ＋ 的常态场景）+ `overflow-y:auto` + 顶部渐隐 `mask-image`（只在真滚出内容时挂 `.fade-top`）
  + `.fx-btn{flex:0 0 auto}` 防止被压扁；JS 补「打开回第一条」与 which-key 选中行自动滚进视野。
  实测（CDP 真实 touch 序列）5 种尺寸 12/12 可达：320×568 滚 71px、844×390 滚 249px、
  740×360 滚 279px；软键盘模拟 390×380 限高降到 264px 仍 ≥16px。顺带 bump `sw.js` VERSION
  v20（模板 CSS 变了）。**教训：修完一个「锚定形状」的浮层要 grep 所有同形状的浮层** ——
  这已经是 `#fxdock` 55 → `#numgrid` 70 → `#whichkey` 的第三次同族复发。单测 114 → **120 全绿**。
- [[2026-11-23]] — `7852e69` **`:g`/`:v` 全局命令是死的**：`vim.js` 的 `defaultExCommandMap`
  里没有 `delete`/`normal`/`join`/`put`，而 `global` 是「逐行拼 `:{行号}{cmd}` 再分发」——
  `:g/a/d` 去执行 `:2d` → `Not an editor command`，一个字符都不删。连带修了三处：
  `defineEx` 只知道 `params.args`（range 全丢）、`:normal` 算了 range 却没挪光标、
  `:del` 被前缀匹配抢到行删除（改 `:Delete` 删页面）。全量 108 tests OK。
- [[2026-11-23]] — `79e0355` **数字盘三处**：`#numgrid` 的 `z-index:70` 同样低于 KBPanel overlay
  的 90（上一轮只修了 `#fxdock` 的 55，**没顺手查同族浮层**）；遮罩区点不动关不掉（触屏无 Esc，
  唯一出口是 32px 的 ×）；打开时不锁背景滚动（遮罩上滑动会滚正文，0→650）。层级现在固定成
  一条：触屏入口(96/97) > overlay(90) > scrim(89) > dock(40)。另落测触屏横滑切缓冲（阅读态切换、
  编辑器内不切，只能 CDP 派发真实 touch 序列）。单测 100 全绿。
- [[2026-11-23]] — `63d56b6` **触屏两处**：① 动作坞 `z-index:55` 低于 KBPanel overlay 的 90，
  390×844 上目录面板正好压住展开区，12 个按钮里 8 个 `elementFromPoint` 命中 `.toc-item`，
  真实 tap 超时 —— 触屏上动作坞是唯一命令入口，被盖住等于入口没了。② TOC 关掉后每次切页
  又弹回来（`api.on('page')` 无条件 refresh），加 `localStorage['kb-toc-off']` 会话记忆。
  教训：**多端要求不能靠在单端推理**，桌面 1280 上这两件事完全看不出来。
- [[2026-11-23]] — `7af840e` **`za` 折叠两层缺失**：三个 fold addon 一个都没引（`cm.foldCode`
  是 undefined）+ vim 层 `z` 前缀只有滚动定位、没有 `za` 系列。补 addon 与 5 组键位 + `:fold`。
  顺带抓到**计数谎报**：`cm.foldCode` 无块可折时静默返回、不抛异常，第一版拿行数当处数，
  71 行文档报「已折叠 71 处」而实际 10 处 —— 改数 `__isFold` 标记的 delta。单测 88 全绿。
- [[2026-11-23]] — `7077284` **e2e 临时页的 tearDown 漏删版本快照**，`/api/trash` 的条件正是
  「有快照、没有对应页面」，于是每跑一轮 e2e 回收站就多 10 条 `__e2e-excmd-scratch`。
- [[2026-11-23]] — `619d500` **`gcc`/`gc` 从未实现**（vim.js 只有键位声明、没有 operator），
  按 Markdown 语义自实现 `<!-- -->` 包围/取消；**第一次仍漏了 count**（`2gcc` 只注释一行 ——
  action 回调签名 `(cm, actionArgs, vim)` 被写成 `() =>`，这是第三次踩「参数到了、签名没接」）。
  同 commit 修掉**编辑器里 Ctrl-o/Ctrl-i 被应用级 capture 抢走**（阅读态是路由跳转历史、
  编辑器里应是 CodeMirror 的 `jumpListWalk`，两套语义必须分开）。单测 84 全绿。
- [[2026-11-23]] — **ex 命令层又两处**：`d587d65` **`:e!` 静默清空缓冲区**（`!` 当文件名 →
  打开叫「!」的页 → 404 → `load('')` → 2172 字变 0 字）；`1c6935b` 补 `gf`（编辑器里跟
  `[[wikilink]]`，CodeMirror 全文没有这个动作）与 `:t`/`:m` 复制/移动行（坐标换算连错三轮，
  `:t0` 落第二行、`:m+1` 原地不动、`:m+1` 跳两行）。另补 `:bn/:ls/:only/:up` 等 10 个
  「runCommand 里早有、ex 层没注册」的别名。单测 80 全绿。
- [[2026-11-23]] — **`:q!` 是死路**（`be9da72`）：`defineEx('q', () => closeEditor())` 把解析层
  给的 `['!']` 参数丢掉了，`:q` 与 `:q!` 完全同义，**改坏了东西退不出去**。补 `bang()` 判定 +
  `:wa`/`:bd`/`:qa` 一族；`closeAllBuffers()` 之后回首页。顺带发现自己的 e2e 测试**在污染生产库**
  （第一版直接在 `/edit/KB` 打字再 `:wa`，把 KB 页首行写成 `QQ#public`，而断言只看 toast 所以
  「测试通过」了）—— 改成每条用例一个临时页、跑完删掉，`:wa` 额外回读 API 验证。单测 68 全绿。
- [[2026-11-22]] — **路由与 vim text object 三处静默失效**（`449e106` `2e3ba35` `3cd2bd2`）：
  ① 路由词表三份不同步，模板把 `/kb/theme` 整段当部署前缀 → manifest/sw.js 变成 HTML
  （浏览器报 unsupported MIME type，PWA 装不上）—— 页面本身完全正常，光看页面永远发现不了；
  ② 前缀修好后深链接**第一次真的进入 dispatch**，于是暴露插件 routes 缺 `api.view()`
  （影子视图递归自己）、`/kb/theme` 每次打开翻转配色、`/kb/trash` 不在路由表；
  ③ 空行按 `ciw`，`vim.js` 读 `null.start` 抛 TypeError 打断整个 keydown（看着像编辑器卡死）。
  三项各配 e2e 用例，单测 57 → **64 全绿**。
- [[2026-11-22]] — **前端 Vim 真实性一整轮**（13 个 commit，逐项浏览器实测）：`/` 两处根因（`cmd.prefix` 不赋值 + `dialog.js` 没加载）· 列表光标与计数前缀 · `Ctrl-o`/`Ctrl-i` 跳转历史 · 阅读位置记忆 · `Ctrl-d/u/f/b`+`H/M/L`+`zz`+页内搜索 `n/N` · `q` 的 `:bd` 语义 · which-key 键位去重 · 断网 API 本地镜像 · `gf` · `m{a-z}` marks · 命令行历史。同时补 `tests/test_frontend.py`（25 例真浏览器），单测 32 → **57 全绿**
