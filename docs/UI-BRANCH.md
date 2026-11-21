# `ui-lazyvim` 分支：前端重构的独立开发区

## 一句话

后端一行没改，前端整个重写。这个分支只动**前端 + 一个服务端缓存头**，
`main` 上的业务随时可以合进来。

## 这个分支相对 `main` 动了什么

```
main.py                  +2 处（其余 229 行逐字节相同）
templates/index.html       -5234 行（旧内联单文件）
                             +866 行（新模板：只有设计 token + DOM 骨架）
static/app.js              +3292 行（外置的 SPA 主程序）
static/plugin.js           +474 行（插件内核，lazy.nvim 模型）
static/panel.js            +236 行（面板容器 overlay / dock / full）
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
```

生产目录 `/home/user/services/kb-docker` 始终在 `main`，两边互不干扰
（`git worktree list` 能看到）。

生产容器 `kb` 只挂了 `./templates/index.html`（见 `docker-compose.override.yml`），
所以这个分支**不会**影响线上。它自己有一套：

- 容器 `kb-ui-test` → `127.0.0.1:8099`
- 预览 `https://fajita.tail117f80.ts.net:8447/`（tailnet 内）
- 数据 `~/pi-working/kb-ui-test/data/kb.db`（独立副本，579 页）

> 注意：`kb-ui-test` 容器挂的仍是 `~/pi-working/kb-ui-test` 的源码。
> 若想让容器跑**这个 worktree** 的代码，把容器 volumes 指向
> `/home/user/pi-working/kb-ui`，或者干脆在那儿起第二个容器。
> 两者代码目前内容相同，合并前建议验证一次。

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
| 生产未受影响 | `kb` 仍 26h healthy / 579 页，`/static/app.js` 仍 404（旧模板内联） |

## 还没做的

- 旧版有、新版没有：知识库体检报告（`:health`）、保存搜索
- 合并到 `main` 前需要决定这 2 项是「写成插件」还是「不做」
