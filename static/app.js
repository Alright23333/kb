/* ══════════════════════════════════════════════════════════════════════
   KB 前端 —— nvim / lazyvim 风格的编辑器式知识库
   
   设计原则：
   1. 没有侧边栏、没有工具栏。导航 = `:` 命令行 + `<space>` 快捷键树。
   2. 顶部 tabline 是 buffer 列表；底部 statusline 是状态。
   3. 内容区永远占满，读的时候只有文字。
   4. 所有跳转都走一个 navigate()，URL 永远可分享、可刷新。
   ─────────────────────────────────────────────────────────────────────
   这个文件不依赖模板里的任何 DOM id 之外的约定，
   模板只管骨架（tabline / view / editor / statusline / overlays）。
   ══════════════════════════════════════════════════════════════════════ */

/* ── 基础 ───────────────────────────────────────────────── */

/* 部署前缀的推导。两条来源：
 *  1) 模板先算一遍写进 window.__KB_BASE__（它不知道插件路由词）
 *  2) 这里按「路径里第一个路由词之前的部分」重算
 * 第 2 条是必须的：访问 /gantt 时模板只看内置路由词，会误把整个
 * '/gantt' 当成部署前缀，base 一错，路由就全部退回首页。 */
/* 部署前缀：**优先用后端注入的值**（templates/index.html 里 <!--KB_BASE-->
 * 被 main.py 替换成一段 script）。路由词表只存在后端一处，避免第三次出现
 * 「前端自己的词表不认识某个路由 → 把路由当成了前缀」的事故。
 * 下面这段推导只在「模板没注入」时兜底（例如直接开静态文件调试）。 */
const BASE = (function () {
  if (typeof window.__KB_BASE__ === 'string') return window.__KB_BASE__;
  const BUILTIN = ['page', 'all', 'graph', 'todo', 'journal', 'tag', 'search', 'edit', 'new',
    'theme', 'trash', 'health', 'searches', 'gantt'];
  const declared = (window.KB && typeof window.KB.declaredRoutes === 'function')
    ? window.KB.declaredRoutes() : [];
  const known = BUILTIN.concat(declared);

  const raw = location.pathname.replace(/\/+$/, '');
  const segs = raw.split('/').filter(Boolean);
  const idx = segs.findIndex(s => known.indexOf(decodeURIComponent(s)) >= 0);
  if (idx > 0) return '/' + segs.slice(0, idx).join('/');  // /kb/page/KB → /kb
  if (idx === 0) return '';                                // /gantt → ''
  // 没有路由词：要么是根，要么是纯部署前缀（/kb）
  if (window.__KB_BASE__ !== undefined) return window.__KB_BASE__;
  return raw === '' ? '' : raw;
})();
const API = BASE + '/api';

const $ = (id) => document.getElementById(id);

function escapeHtml(s) {
  if (s === null || s === undefined) return '';
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/* ── 缓存 / 请求 ────────────────────────────────────────── */

const _cache = new Map();
function invalidateCache(prefix) {
  if (!prefix) { _cache.clear(); return; }
  for (const k of _cache.keys()) if (k.startsWith(prefix)) _cache.delete(k);
}

/* ══════════════════════════════════════════════════════════
   只读请求的本地镜像 —— 离线也能读已经看过的内容
   ──────────────────────────────────────────────────────────
   原来断网后整站只剩「读取失败：Failed to fetch」20 个字：
   service worker 只缓存了静态壳（HTML/CSS/JS），API 数据没缓存。

   做法：所有 GET 成功时把响应写进 localStorage（kb-api-mirror），
   失败且「不像服务端明确拒绝」（无 status 或 5xx）时回落到镜像。
   404/403 这种明确答案不回落 —— 否则删掉的页面会从缓存里诈尸。

   只镜像 GET：写操作（POST/PUT/DELETE）永远走网络，绝不假装成功。
   ══════════════════════════════════════════════════════════ */

const MIRROR_KEY = 'kb-api-mirror';
const MIRROR_MAX = 240;

let mirror = null;
let mirrorDirty = false;

function mirrorLoad() {
  if (mirror) return mirror;
  try {
    const raw = JSON.parse(localStorage.getItem(MIRROR_KEY) || '{}');
    mirror = (raw && typeof raw === 'object' && !Array.isArray(raw)) ? raw : {};
  } catch (e) { mirror = {}; }
  return mirror;
}

/* 镜像存的是 { ts, val } 包装，不往响应对象上挂 _ts ——
 * 那会污染渲染（数组上加属性、页面对象多一个字段）和后续回写。 */
function mirrorPut(url, val) {
  const m = mirrorLoad();
  if (!m[url]) {
    const keys = Object.keys(m);
    if (keys.length >= MIRROR_MAX) {
      let oldest = keys[0], oldTs = Infinity;
      for (const k of keys) {
        const ts = (m[k] && m[k].ts) || 0;
        if (ts < oldTs) { oldTs = ts; oldest = k; }
      }
      delete m[oldest];
    }
  }
  m[url] = { ts: Date.now(), val };
  mirrorDirty = true;
  mirrorScheduleSave();
}

let mirrorSaveTimer = null;
function mirrorScheduleSave() {
  if (mirrorSaveTimer) return;
  // 批量写：连续请求（首页一次并发 3~4 个）不该各写一次 localStorage
  mirrorSaveTimer = setTimeout(() => {
    mirrorSaveTimer = null;
    if (!mirrorDirty || !mirror) return;
    try {
      localStorage.setItem(MIRROR_KEY, JSON.stringify(mirror));
      mirrorDirty = false;
    } catch (e) {
      // 配额满了：砍一半再试一次
      try {
        const m = mirror;
        const keys = Object.keys(m).sort((a, b) => ((m[a] && m[a].ts) || 0) - ((m[b] && m[b].ts) || 0));
        for (const k of keys.slice(0, Math.ceil(keys.length / 2))) delete m[k];
        localStorage.setItem(MIRROR_KEY, JSON.stringify(m));
        mirrorDirty = false;
      } catch (e2) {}
    }
  }, 250);
}

function mirrorGet(url) {
  const m = mirrorLoad();
  const hit = m[url];
  return hit ? hit.val : undefined;
}

/* 从镜像取数时给用户一个明确信号，别让人以为数据是新的 */
function noteOfflineData() {
  state.offlineData = true;
  const hint = STATUS.net && STATUS.net.querySelector('.sl-hint');
  const dot = STATUS.net && STATUS.net.querySelector('.sl-dot');
  if (hint) hint.textContent = 'offline·缓存';
  if (dot) dot.classList.add('off');
  // 也让用户知道「看到的是缓存」，别以为是最新的
  if (typeof toast === 'function') toast('离线：显示的是本地缓存', 'error');
}

async function jfetch(url, opts) {
  const method = ((opts && opts.method) || 'GET').toUpperCase();
  let resp;
  try {
    resp = await fetch(url, opts);
  } catch (e) {
    // 网络层失败（断网/超时）—— GET 可以回落镜像
    if (method === 'GET') {
      const hit = mirrorGet(url);
      if (hit !== undefined) { noteOfflineData(); return hit; }
    }
    throw e;
  }
  if (!resp.ok) {
    let detail = resp.status + ' ' + resp.statusText;
    try { const j = await resp.json(); if (j && j.detail) detail = j.detail; } catch (e) {}
    const err = new Error(detail);
    err.status = resp.status;
    // 5xx 是服务端自身问题（容器挂了），可以回落；4xx 是明确答案，不能
    if (method === 'GET' && resp.status >= 500) {
      const hit = mirrorGet(url);
      if (hit !== undefined) { noteOfflineData(); return hit; }
    }
    throw err;
  }
  const val = await resp.json();
  if (method === 'GET') mirrorPut(url, val);
  return val;
}

async function cached(key, url) {
  if (_cache.has(key)) return _cache.get(key);
  const v = await jfetch(url);
  _cache.set(key, v);
  return v;
}

function getPage(name) { return cached('page:' + name.toLowerCase(), `${API}/pages/${encodeURIComponent(name)}`); }
function getTags() { return cached('tags', `${API}/tags`); }
function pageCount() { return cached('count', `${API}/pages/count`).then(r => r.count); }

let allNames = null;
async function ensureNames() {
  if (allNames) return allNames;
  const names = await cached('names', `${API}/pages/names`);
  allNames = names;
  return names;
}

/* ── Toast ──────────────────────────────────────────────── */

let toastTimer = null;
function toast(msg, kind) {
  const el = $('toast');
  el.textContent = msg;
  el.className = 'show' + (kind ? ' ' + kind : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = ''; }, 2200);
}

/* ── 全局状态 ───────────────────────────────────────────── */

const state = {
  view: 'home',          // home | page | all | tag | todo | journal | graph | search | trash
  current: null,         // 当前页面名（editor 里为正在编辑的名字）
  buffers: [],           // tabline 里的名字
  editing: false,
  dirty: false,
  vimMode: 'normal',
  leaderOpen: false,
};

const STATUS = {
  mode: $('sl-mode'), name: $('sl-name'), pos: $('sl-pos'),
  words: $('sl-words'), pages: $('sl-pages'), theme: $('sl-theme'),
  net: $('sl-net'), clock: $('sl-clock'),
};

function setMode(m) {
  state.vimMode = m;
  const map = { normal: 'NORMAL', insert: 'INSERT', visual: 'VISUAL', replace: 'REPLACE', cmd: 'CMD' };
  const cls = { normal: '', insert: 'insert', visual: 'visual', replace: 'replace', cmd: 'cmd' };
  STATUS.mode.textContent = map[m] || m.toUpperCase();
  STATUS.mode.className = 'sl-mode ' + (cls[m] || '');
}

function setStatusName(name, dirty) {
  STATUS.name.innerHTML = name
    ? escapeHtml(name) + (dirty ? ' <span class="dirty">●</span>' : '')
    : '<span class="sl-hint">—</span>';
}

function updateClock() {
  const d = new Date();
  STATUS.clock.textContent =
    String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
}

async function updatePageCount() {
  try {
    const n = await pageCount();
    STATUS.pages.textContent = n + ' 页';
  } catch (e) { STATUS.pages.textContent = '—'; }
}

function updateNet() {
  const dot = STATUS.net.querySelector('.sl-dot');
  const hint = STATUS.net.querySelector('.sl-hint');
  if (navigator.onLine) {
    dot.classList.remove('off');
    // 之前显示过「离线缓存」，恢复网络后要清标记，否则一直挂着
    hint.textContent = state.offlineData ? 'sync·待刷新' : 'sync';
    if (state.offlineData) state.offlineData = false;
  } else {
    dot.classList.add('off');
    hint.textContent = 'offline';
  }
}

/* ── 主题 ───────────────────────────────────────────────── */

/* ══════════════════════════════════════════════════════════
   配色 —— 照 nvim 的 :colorscheme 模型
   ──────────────────────────────────────────────────────────
   一个主题 = static/themes/<name>.css 的一个文件，内含
   :root[data-scheme="<name>"] { 一堆 CSS 变量 }。
   切换只改 html 上的 data-scheme 属性，不重建任何组件
   （这正是 nvim 切配色不重开窗口的道理）。
   注册表在 static/themes/index.js 里（相当于 colors/ 目录的 ls）。
   ══════════════════════════════════════════════════════════ */

const THEMES_KEY = 'kb-colorscheme';

function themesReg() {
  return window.KBThemes || null;
}

function applyTheme(scheme) {
  const reg = themesReg();
  if (!reg) {
    // 主题注册表没加载（极端情况）→ 退回属性直写
    document.documentElement.setAttribute('data-scheme', scheme);
    return;
  }
  const entry = reg.themeEntry(scheme);
  if (!entry) {
    toast('没有这个配色：' + scheme + '（:colorscheme 查看全部）', 'error');
    return;
  }
  reg.applyTheme(entry.name, BASE);
  localStorage.setItem(THEMES_KEY, entry.name);
  if (STATUS.theme) STATUS.theme.textContent = entry.dark ? 'mocha' : 'latte';
  if (window.mermaid && window.__mermaidInit) initMermaidTheme();
  if (window.KB && window.KB.trigger) window.KB.trigger('theme', { scheme: entry.name });
}

function currentTheme() {
  const reg = themesReg();
  if (reg) return reg.currentScheme();
  return document.documentElement.getAttribute('data-scheme') || 'catppuccin-mocha';
}

/* :colorscheme [名字] —— 不带参数时列出全部（同 nvim 行为） */
function colorscheme(arg) {
  const reg = themesReg();
  if (!reg) return toast('主题注册表未加载', 'error');
  const name = (arg || '').trim();
  const cur = currentTheme();

  if (!name) {
    // :colorscheme 不带参数 → 列出全部，当前项打 *
    toast('配色：' + reg.themeNames().map(n => n === cur ? '*' + n : n).join('  ') + '（:colorscheme <名字>）');
    return;
  }

  // 解析：精确名 → 同组名（:colorscheme catppuccin = 这一族）→ 子串缩写（latte → catppuccin-latte）
  const hit = reg.themeEntry(name);
  if (hit && hit.group) {
    // 用户报的是「组名」（catppuccin）→ 不动配色，只报这一族有哪几个
    const members = reg.groupMembers(hit.name);
    toast(hit.group + '：' + members.map(n => n === cur ? '*' + n : n).join('  '));
    return;
  }
  const partial = hit || reg.themeNames().map(n => reg.themeEntry(n)).find(t => t.name.indexOf(name) >= 0);
  if (!partial) return toast('没有这个配色：' + name, 'error');
  if (!partial.group) { applyTheme(partial.name); toast('配色 → ' + partial.label); return; }

  // 组内缩写（latte/frappe/mocha/macchiato）本就命中 partial 了，直接切
  applyTheme(partial.name);
  toast('配色 → ' + partial.label);
}

function toggleTheme() {
  const reg = themesReg();
  if (!reg) return;
  const cur = currentTheme();
  // 有 group 的只在同组内轮转（catppuccin 四口味），否则全表轮转。
  // nvim 里 :colorscheme 也是按 flavour 走，不会把无关配色卷进来。
  let list = reg.groupMembers(cur);
  if (list.length <= 1) list = reg.themeNames();
  const next = list[(list.indexOf(cur) + 1) % list.length];
  applyTheme(next);
}

/* ── markdown-it ────────────────────────────────────────── */

const md = window.markdownit({
  html: false, linkify: true, typographer: true, breaks: true,
});

const defaultLinkOpen = md.renderer.rules.link_open ||
  function (tokens, idx, options, env, self) { return self.renderToken(tokens, idx, options); };

// [[target]] / [[target|alias]]
md.inline.ruler.before('link', 'wikilink', function (s, silent) {
  const start = s.pos;
  if (s.src.slice(start, start + 2) !== '[[') return false;
  const end = s.src.indexOf(']]', start + 2);
  if (end === -1) return false;
  if (!silent) {
    const inner = s.src.slice(start + 2, end);
    const parts = inner.split('|');
    const name = parts[0].trim();
    const display = parts.length > 1 ? parts.slice(1).join('|').trim() : name;
    const open = s.push('link_open', 'a', 1);
    open.attrSet('href', '#');
    open.attrSet('data-page', name);
    open.attrSet('class', 'wikilink');
    const txt = s.push('text', '', 0);
    txt.content = display;
    s.push('link_close', 'a', -1);
  }
  s.pos = end + 2;
  return true;
});

// ![[target]] 嵌入
md.inline.ruler.before('wikilink', 'kbembed', function (s, silent) {
  const start = s.pos;
  if (s.src.slice(start, start + 3) !== '![[') return false;
  const end = s.src.indexOf(']]', start + 3);
  if (end === -1) return false;
  if (!silent) {
    const t = s.push('kb_embed', '', 0);
    t.meta = { name: s.src.slice(start + 3, end).split('|')[0].trim() };
  }
  s.pos = end + 2;
  return true;
});
md.renderer.rules.kb_embed = (tokens, idx) =>
  `<div class="kb-embed" data-embed="${escapeHtml(tokens[idx].meta.name)}"></div>\n`;

// #tag —— 与后端 TAG_RE 对齐：'#' 前不是字母/数字/下划线/#
md.inline.ruler.before('wikilink', 'kbhash', function (s, silent) {
  const start = s.pos;
  if (s.src[start] !== '#') return false;
  const prev = start > 0 ? s.src[start - 1] : '';
  if (prev && /[0-9A-Za-z_#]/.test(prev)) return false;
  const m = /^#([^\s#\[\](){}<>|`'"~!?,.;:。，、；：！？（）【】《》]+)/.exec(s.src.slice(start));
  if (!m) return false;
  if (/^#+\s/.test(s.src.slice(start))) return false; // 标题
  if (!silent) {
    const t = s.push('kb_tag', '', 0);
    t.content = m[1];
  }
  s.pos = start + m[0].length;
  return true;
});
md.renderer.rules.kb_tag = (tokens, idx) =>
  `<span class="tagline" data-tag="${escapeHtml(tokens[idx].content)}">#${escapeHtml(tokens[idx].content)}</span>`;

md.renderer.rules.link_open = function (tokens, idx, options, env, self) {
  const href = tokens[idx].attrGet('href') || '';
  if (href.startsWith('wiki:')) {
    tokens[idx].attrSet('href', '#');
    tokens[idx].attrSet('data-page', href.slice(5));
  }
  return defaultLinkOpen(tokens, idx, options, env, self);
};

const defaultFence = md.renderer.rules.fence ||
  function (tokens, idx, options, env, self) { return self.renderToken(tokens, idx, options); };
md.renderer.rules.fence = function (tokens, idx, options, env, self) {
  const info = (tokens[idx].info || '').trim().split(/\s+/)[0];
  if (info === 'mermaid') return `<div class="mermaid">${escapeHtml(tokens[idx].content)}</div>\n`;
  return defaultFence(tokens, idx, options, env, self);
};

function renderMd(content) { return md.render(content || ''); }

/* ── mermaid（懒加载，本地 vendored） ───────────────────── */

let mermaidPromise = null;
function ensureMermaid() {
  if (window.mermaid) return Promise.resolve();
  if (mermaidPromise) return mermaidPromise;
  mermaidPromise = new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = BASE + '/static/vendor/mermaid/mermaid.min.js';
    s.onload = resolve;
    s.onerror = () => { mermaidPromise = null; reject(new Error('mermaid 加载失败')); };
    document.head.appendChild(s);
  });
  return mermaidPromise;
}

function initMermaidTheme() {
  if (!window.mermaid) return;
  const cs = getComputedStyle(document.documentElement);
  const v = (n) => cs.getPropertyValue(n).trim();
  mermaid.initialize({
    startOnLoad: false, securityLevel: 'strict', theme: 'base',
    themeVariables: {
      background: 'transparent',
      mainBkg: v('--bg-deep'), primaryColor: v('--bg-deep'),
      primaryBorderColor: v('--accent'), primaryTextColor: v('--fg'),
      textColor: v('--fg'), lineColor: v('--overlay0'),
      secondaryColor: v('--bg-deep'), secondaryBorderColor: v('--accent2'),
      tertiaryColor: v('--bg'), tertiaryBorderColor: v('--line'),
      fontSize: '13px',
    },
  });
  window.__mermaidInit = true;
}

async function renderMermaid(root) {
  const blocks = (root || document).querySelectorAll('.mermaid');
  if (!blocks.length) return;
  try { await ensureMermaid(); } catch (e) { return; }
  initMermaidTheme();
  for (const b of blocks) {
    const src = b.dataset.src !== undefined ? b.dataset.src : b.textContent;
    b.dataset.src = src;
    b.classList.remove('mermaid-error');
    b.removeAttribute('data-processed');
    b.textContent = src;
  }
  try { mermaid.init(undefined, blocks); } catch (e) { /* 坏块保留源码 */ }
}

/* ── 嵌入 / 未解析链接 ─────────────────────────────────── */

async function fillEmbeds(root, depth) {
  depth = depth || 0;
  const els = (root || document).querySelectorAll('.kb-embed:not([data-filled])');
  for (const el of els) {
    el.dataset.filled = '1';
    const name = el.dataset.embed;
    if (!name) { el.textContent = '(空嵌入)'; continue; }
    if (depth >= 2) { el.classList.add('kb-embed-max'); el.textContent = `↪ 嵌入层级上限（${name}）`; continue; }
    try {
      const pg = await getPage(name);
      el.innerHTML = renderMd(pg.content);
      await fillEmbeds(el, depth + 1);
    } catch (e) {
      el.innerHTML = `<span class="kb-embed-create" data-create="${escapeHtml(name)}">${escapeHtml(name)} 不存在 — 点击创建</span>`;
    }
  }
}

async function markUnresolved(root) {
  const names = await ensureNames();
  const lower = new Set(names.map(n => n.toLowerCase()));
  (root || document).querySelectorAll('a.wikilink[data-page]').forEach(a => {
    if (!lower.has(a.dataset.page.toLowerCase())) a.classList.add('unresolved');
  });
}

/* ── 时间格式 ───────────────────────────────────────────── */

function fmtDate(s) { return s ? String(s).slice(0, 16) : ''; }

function relDate(s) {
  if (!s) return '';
  const d = new Date(String(s).replace(' ', 'T'));
  if (isNaN(d)) return String(s).slice(0, 10);
  const diff = Date.now() - d.getTime();
  if (diff < 0) return String(s).slice(0, 10);
  const min = Math.floor(diff / 60000);
  if (min < 1) return '刚刚';
  if (min < 60) return min + ' 分';
  const hr = Math.floor(min / 60);
  if (hr < 24) return hr + ' 小时';
  const day = Math.floor(hr / 24);
  if (day === 1) return '昨天';
  if (day < 30) return day + ' 天';
  return String(s).slice(0, 10);
}

function todayStr() {
  const t = new Date();
  return new Date(t - t.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}

function timeGroup(s) {
  const d = new Date(String(s).replace(' ', 'T'));
  if (isNaN(d)) return '更早';
  const now = new Date();
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const t = d.getTime();
  if (t >= start) return '今天';
  if (t >= start - 864e5) return '昨天';
  if (t >= start - 7 * 864e5) return '本周';
  if (t >= start - 30 * 864e5) return '本月';
  return '更早';
}

/* ══════════════════════════════════════════════════════════
   路由
   ══════════════════════════════════════════════════════════ */

function routeUrl(target, arg) {
  switch (target) {
    case 'all': return BASE + '/all';
    case 'graph': return BASE + '/graph';
    case 'todo': return BASE + '/todo';
    case 'journal': return BASE + '/journal' + (arg ? '/' + encodeURIComponent(arg) : '');
    case 'theme': return BASE + '/theme';
    case 'tag': return BASE + '/tag/' + encodeURIComponent(arg || '');
    case 'search': return BASE + '/search/' + encodeURIComponent(arg || '');
    case 'page': return BASE + '/page/' + encodeURIComponent(arg || '');
    case 'edit': return BASE + '/edit/' + encodeURIComponent(arg || '');
    case 'new': return BASE + '/new';
    case 'trash': return BASE + '/trash';
    default:
      // 插件注册的视图 = 动态路由（REST 后端不需要知道它，SPA 兜底即可）
      if (window.KB && window.KB.routeWords && window.KB.routeWords().indexOf(target) >= 0) {
        return BASE + '/' + target + (arg ? '/' + encodeURIComponent(arg) : '');
      }
      return BASE + '/';
  }
}

function parseRoute(path) {
  let p = path;
  if (BASE && p.startsWith(BASE)) p = p.slice(BASE.length);
  p = p.replace(/^\/+/, '');
  if (!p) return { target: 'home', arg: null };
  const parts = p.split('/');
  const rest = parts.slice(1).map(decodeURIComponent).join('/');
  switch (parts[0]) {
    case 'all': return { target: 'all' };
    case 'graph': return { target: 'graph' };
    case 'todo': return { target: 'todo' };
    case 'theme': return { target: 'theme' };
    case 'journal': return { target: 'journal', arg: rest || null };
    case 'tag': return { target: 'tag', arg: rest };
    case 'search': return { target: 'search', arg: rest };
    case 'page': return { target: 'page', arg: rest };
    case 'edit': return { target: 'edit', arg: rest };
    case 'new': return { target: 'new' };
    /* 回收站是 overlay 视图，但深链接 /kb/trash 必须能落到 openTrash()。
     * 原先这里没有 case，/kb/trash 掉进 default → routeWords() 里也没有
     * 'trash'（它不是插件路由）→ 判定成 home，收件箱永远看不到。 */
    case 'trash': return { target: 'trash' };
    default:
      if (window.KB && window.KB.routeWords && window.KB.routeWords().indexOf(parts[0]) >= 0) {
        return { target: parts[0], arg: rest || null };
      }
      return { target: 'home' };
  }
}

let navToken = 0;

/* ══════════════════════════════════════════════════════════
   跳转历史 —— vim 的 jumplist（Ctrl-o / Ctrl-i）
   ──────────────────────────────────────────────────────────
   之前点链接进下一页后回不去：浏览器的 back 只能一页一页退，而且
   编辑器/列表光标状态全丢。vim 的做法是维护一条 jumplist，
   Ctrl-o 往回、Ctrl-i 往前，各自独立于文件修改。

   判据和 vim 一致：**只记「跳转」，不记「浏览」**。
   - 从 A 点链接到 B      → 记一条
   - 手动 /all 再点 B     → 不记（这是新浏览，不是跳转）
   这里简化为「只要换了 target:arg 就记，但同一页面连续不重复记」。
   ══════════════════════════════════════════════════════════ */

const JUMP_MAX = 100;
state.jumplist = state.jumplist || [];   // [{target, arg}]
state.jumpIdx = typeof state.jumpIdx === 'number' ? state.jumpIdx : -1;

function jumpKey(target, arg) {
  return target + '\u0001' + (arg == null ? '' : arg);
}

function recordJump(target, arg) {
  if (target === 'home' && !arg) return;         // 回首页不入栈
  const key = jumpKey(target, arg);
  // 回退/前进过程中触发的 dispatch 不算新跳转
  if (state.jumpReplaying) return;
  const list = state.jumplist;
  if (state.jumpIdx >= 0 && list[state.jumpIdx] && jumpKey(list[state.jumpIdx].target, list[state.jumpIdx].arg) === key) {
    return;                                       // 同一位置，不重复
  }
  // 从中间跳转时截断「未来」
  if (state.jumpIdx < list.length - 1) list.splice(state.jumpIdx + 1);
  list.push({ target, arg });
  if (list.length > JUMP_MAX) list.shift();
  state.jumpIdx = list.length - 1;
}

function jumpTo(delta) {
  const list = state.jumplist;
  if (!list.length) return toast('没有跳转记录');
  const want = state.jumpIdx + delta;
  if (want < 0 || want >= list.length) return toast(delta < 0 ? '已经是最早的位置' : '已经是最新的位置');
  state.jumpIdx = want;
  const { target, arg } = list[want];
  state.jumpReplaying = true;
  try { dispatch(target, arg); } finally { state.jumpReplaying = false; }
  // URL 跟着走，但不新增历史条目（否则浏览器 back 会与 jumplist 打架）
  const url = routeUrl(target, arg);
  if (url && url !== location.pathname) history.replaceState({ target, arg }, '', url);
}

/* ══════════════════════════════════════════════════════════
   标记 —— vim 的 m{a-z} / `{a-z}
   ──────────────────────────────────────────────────────────
   jumplist 记的是「你路过哪」，mark 记的是「你特意标了哪」。
   读一篇长文时看到关键段落按 ma，去别处转一圈后 ``a 直接回来 ——
   jumplist 做不到这一点（它只按顺序退）。

   记的是「路由 + 滚动位置」，所以能精确回到同一段。
   跨会话保留（localStorage）—— vim 的 mark 在退出后是不留的，
   但这是网页，留着更实用：昨天标的地方今天还想回去。
   ══════════════════════════════════════════════════════════ */

const MARKS_KEY = 'kb-marks';

let marks = null;

function marksLoad() {
  if (marks) return marks;
  try {
    const raw = JSON.parse(localStorage.getItem(MARKS_KEY) || '{}');
    marks = (raw && typeof raw === 'object' && !Array.isArray(raw)) ? raw : {};
  } catch (e) { marks = {}; }
  return marks;
}

function marksSave() {
  try { localStorage.setItem(MARKS_KEY, JSON.stringify(marks || {})); } catch (e) {}
}

/* 在当前位置打一个标记（只在阅读态；编辑器里归 vim 自己）*/
function setMark(ch) {
  if (!/^[a-z]$/.test(ch || '')) return toast('标记只能是 a-z');
  if (state.editing) return toast('编辑器里标记归 vim（ma / `a）');
  const key = ch;
  const target = state.viewKind === 'page' ? 'page' : null;
  const arg = target ? state.current : null;
  if (!arg) return toast('这个视图没法打标记（只能标具体页面）');
  const m = marksLoad();
  m[key] = { target, arg, scroll: $('view').scrollTop, ts: Date.now() };
  marksSave();
  toast(`标记 ${key} → ${arg} @ ${Math.round(m[key].scroll)}px`);
}

/* 跳到标记（第二个键是 a-z）*/
function jumpMark(ch) {
  if (!/^[a-z]$/.test(ch || '')) return toast('标记只能是 a-z');
  if (state.editing) return toast('编辑器里标记归 vim（ma / `a）');
  const m = marksLoad();
  const mark = m[ch];
  if (!mark) return toast(`标记 ${ch} 未设置 —— 先按 m${ch} 打一个`);

  const want = mark.scroll || 0;
  state.jumpReplaying = true;
  try {
    if (state.viewKind === 'page' && (state.current || '').toLowerCase() === String(mark.arg).toLowerCase()) {
      // 已经就在这一页 → 只调滚动位置
      const v = $('view');
      v.scrollTop = Math.max(0, Math.min(v.scrollHeight - v.clientHeight, want));
    } else {
      dispatch(mark.target, mark.arg);
      // 内容异步长进来，等渲染稳住再落位置
      const key = routeKey(mark.target, mark.arg);
      const memo = scrollMemoLoad();
      memo[key] = want;            // 借用阅读位置记忆的重试机制
      scrollMemoSave();
      scrollRouteKey = key;
      restoreScrollPos(key);
    }
  } finally {
    state.jumpReplaying = false;
  }
  const url = routeUrl(mark.target, mark.arg);
  if (url && url !== location.pathname) history.replaceState({ target: mark.target, arg: mark.arg }, '', url);
  toast(`跳到标记 ${ch} → ${mark.arg}`);
}

function listMarks() {
  const m = marksLoad();
  const keys = Object.keys(m).sort();
  if (!keys.length) return toast('还没有标记（ma 打一个）');
  showView(`<div class="list-wrap">
    <div class="list-head"><h2>标记</h2><span class="t">${keys.length} 个</span></div>
    ${keys.map(k => `<div class="row" data-mark="${escapeHtml(k)}">
      <span class="row-name"><b>${escapeHtml(k)}</b> · ${escapeHtml(m[k].arg)}</span>
      <span class="t">${Math.round(m[k].scroll)}px</span>
    </div>`).join('')}
  </div>`, 'list');
  /* 点击 / Enter 跳回去。data-page 那套是给「页面行」的，
   * 标记行没有 page，所以单独绑（不进 listRows，避免 j/k 混进来）。 */
  const v = $('view');
  if (!v) return;
  v.querySelectorAll('[data-mark]').forEach(row => {
    row.addEventListener('click', () => jumpMark(row.dataset.mark));
  });
}

function navigate(target, arg, opts) {
  opts = opts || {};
  const url = routeUrl(target, arg);
  const go = () => {
    if (!opts.replace && url !== location.pathname) history.pushState({ target, arg }, '', url);
    else if (url !== location.pathname) history.replaceState({ target, arg }, '', url);
    recordJump(target, arg);     // 记进 jumplist（Ctrl-o 能回得来）
    dispatch(target, arg);
  };
  // 有未保存改动 → 先问，避免误丢（pcall 保持同步语义：无改动时行为完全不变）
  if (state.editing && editorDirty) {
    return guardDirtyNav(target, arg).then(ok => { if (ok) go(); });
  }
  go();
}

/* 编辑器里保护未保存改动。
 * 心智模型借自 vim 的 E37（no write since last change）：
 * 有改动就不静默换页，先让用户选 保存 / 丢弃 / 取消。 */
let pendingNav = null;
let navGuardBusy = false;

async function guardDirtyNav(target, arg) {
  if (!state.editing || !editorDirty) return true;
  const name = state.current || '(新页面)';
  const choice = await promptChoice(
    `「${name}」有未保存的改动`,
    '保存后继续 / 丢弃改动继续 / 留在这里',
    [
      { key: 'w', label: '保存并继续  :w' },
      { key: 'd', label: '丢弃改动继续  :q!' },
      { key: 'c', label: '留在这里      Esc' },
    ]);
  if (choice === 'c' || choice === null) return false;
  if (choice === 'd') { editorDirty = false; draftClear(); return true; }
  try { await saveEditor(); return true; }
  catch (e) { toast('保存失败：' + e.message, 'error'); return false; }
}

function routeKey(target, arg) {
  return target + ':' + (arg == null ? '' : arg);
}

function dispatch(target, arg) {
  const token = ++navToken;

  /* 阅读位置记忆：先把「旧这一页读到哪」存起来，再切到新路由。
   * 顺序不能反 —— 一旦 scrollRouteKey 换成新的，旧页的位置就找不回来了。 */
  saveScrollPos();
  scrollRouteKey = routeKey(target, arg);

  // 离开编辑器（除了仍在编辑态的子路由）
  if (target !== 'edit' && target !== 'new') state.editing = false;
  // 上下文变了 → 动作坞要跟着换（touch 模式下才有可见效果）
  if (typeof renderFxPanel === 'function') renderFxPanel();
  switch (target) {
    case 'home': showHome(token); break;
    case 'all': showAll(token); break;
    case 'tag': showTag(arg, token); break;
    case 'search': showSearch(arg, token); break;
    case 'todo': showTodo(token); break;
    case 'journal': showJournal(arg, token); break;
    case 'graph': showGraph(token); break;
    /* /kb/theme 现在能算对部署前缀了（后端注入 base），于是它真的会
     * 进到这个分支。**不能在这里 toggleTheme()** —— 那样每次刷新或从
     * 收藏夹打开都会翻转一次配色，同一个 URL 两次打开颜色不一样。
     * 切配色是命令（:theme / :colorscheme / space u c），URL 只是旧链接
     * 兜底：落到首页即可。 */
    case 'theme': showHome(token); break;
    /* /kb/trash 同理：回收站是个 overlay，深链接进来先铺首页再叠面板，
     * Esc / :q 有东西可退。原先这个 target 根本没有 case，
     * 一路掉到 default 当页面名去查 —— 查不到就是空页。 */
    case 'trash': showHome(token); openTrash(); break;
    case 'new': openEditor('', '', token); break;
    case 'edit': openEditor(arg, null, token); break;
    case 'page': showPage(arg, token); break;
    default: {
      // 插件注册的视图（lazy.nvim 里没有对应概念，是网页的加成）
      const fn = (window.KB && window.KB.viewFor) ? window.KB.viewFor(target) : null;
      if (fn) { fn(arg, token); break; }
      showPage(arg, token); break;
    }
  }
  // 通知插件层：一次导航完成（相当于 BufEnter）
  if (window.KB && window.KB.trigger) window.KB.trigger('navigate', { target, arg, token });
}

window.addEventListener('popstate', () => {
  try {
    const saved = JSON.parse(localStorage.getItem('kb-buffers') || '[]');
    if (Array.isArray(saved)) state.buffers = saved.filter(x => typeof x === 'string').slice(0, 20);
  } catch (e) {}

  const r = parseRoute(location.pathname);
  dispatch(r.target, r.arg);
});

/* ── buffer 管理 ────────────────────────────────────────── */

function addBuffer(name) {
  if (typeof name !== 'string' || !name) return;   // 非字符串（数组参数等）直接挡掉
  if (!state.buffers.some(b => b.toLowerCase() === name.toLowerCase())) {
    state.buffers.push(name);
  }
  renderTabline();
}

function closeBuffer(name) {
  if (typeof name !== 'string' || !name) return;
  state.buffers = state.buffers.filter(b => b.toLowerCase() !== name.toLowerCase());
  renderTabline();
}

function persistBuffers() {
  try { localStorage.setItem('kb-buffers', JSON.stringify(state.buffers.slice(0, 20))); } catch (e) {}
}

function renderTabline() {
  persistBuffers();
  const el = $('tabline');
  if (!el) return;
  const cur = state.current ? state.current.toLowerCase() : null;

  // touch 模式且未展开：收成一行摘要，把屏幕留给内容
  if (isTouchUi && isTouchUi() && !tablineExpanded) {
    const total = state.buffers.length;
    const label = state.current ? state.current : (total ? total + ' 个缓冲' : '无缓冲');
    el.innerHTML = `<div class="tab-summary" data-tabexpand role="button" aria-label="展开缓冲列表">
      <span class="tab-sum-icon">${state.editing ? '✎' : '▤'}</span>
      <span class="tab-name">${escapeHtml(label)}</span>
      ${total > 1 ? `<span class="tab-sum-count">+${total - 1}</span>` : ''}
      <span class="tab-sum-caret">▾</span>
    </div>`;
    return;
  }

  let html = '';
  for (const b of state.buffers) {
    const active = cur && b.toLowerCase() === cur;
    html += `<div class="tab${active ? ' active' : ''}" data-buffer="${escapeHtml(b)}" role="tab" aria-selected="${active ? 'true' : 'false'}">
      <span class="tab-icon">${active && state.editing ? '✎' : '·'}</span>
      <span class="tab-name">${escapeHtml(b)}</span>
      <button class="tab-close" data-close="${escapeHtml(b)}" title="关闭">×</button>
    </div>`;
  }
  html += `<button class="tab-new" title="新建页面 (space n / :new)" data-newtab>+</button>`;
  if (isTouchUi && isTouchUi()) {
    html += `<button class="tab-fold" data-tabfold title="收起缓冲列表">▴</button>`;
  }
  el.innerHTML = html;
}

/* ── 缓冲辅助操作 ──────────────────────────────────────── */

function closeOtherBuffers() {
  if (!state.current) return toast('没有当前页面');
  state.buffers = state.buffers.filter(b => b.toLowerCase() === state.current.toLowerCase());
  renderTabline();
  toast('已关闭其他缓冲');
}

function closeAllBuffers() {
  state.buffers = [];
  renderTabline();
  toast('已关闭全部缓冲');
}

/* ══════════════════════════════════════════════════════════
   关掉当前缓冲 —— vim 的 :bd / LazyVim 的 q
   ──────────────────────────────────────────────────────────
   原来 q 只是把名字从 tabline 数组里删掉，正文还留在屏幕上 ——
   tab 少了一个，但你在看的还是那一页，等于「关了个寂寞」。
   vim 的 :bd 语义是：删掉这个 buffer，然后切到另一个 buffer；
   没有别的 buffer 就落到空/首页。这里照做。
   ══════════════════════════════════════════════════════════ */

function closeCurrentBuffer() {
  // state.current 理应是字符串，但 navigate 被传数组/对象时会被污染 ——
  // 这里统一收敛成字符串，否则 b.toLowerCase() 直接抛 TypeError，
  // q 键表现为「按了完全没反应」。
  const cur = Array.isArray(state.current) ? String(state.current[0] || '') : String(state.current || '');
  if (!cur) { toast('没有当前页面'); return; }
  const rest = state.buffers.filter(b => b.toLowerCase() !== cur.toLowerCase());
  state.buffers = rest;
  renderTabline();

  // 编辑器里先退出编辑态（有改动要走保存确认，交给 closeEditor）
  if (state.editing) {
    const saved = cur;
    closeEditor();
    if (state.editing) return;   // closeEditor 弹了确认框，等用户决定
  }

  // 切到「最近访问过的另一个缓冲」，没有就回首页
  let next = null;
  try {
    const recent = JSON.parse(localStorage.getItem('kb_recent') || '[]');
    if (Array.isArray(recent)) {
      next = recent.map(r => r && r.name).filter(n => typeof n === 'string'
        && n.toLowerCase() !== cur.toLowerCase() && rest.some(b => b.toLowerCase() === n.toLowerCase()))[0] || null;
    }
  } catch (e) {}
  if (!next && rest.length) next = rest[rest.length - 1];

  if (next) { navigate('page', next); toast(`已关闭 ${cur} → ${next}`); }
  else { navigate('home'); toast(`已关闭 ${cur}`); }
}

/* ── 最近访问 ──────────────────────────────────────────── */

function trackRecent(name) {
  try {
    let recent = JSON.parse(localStorage.getItem('kb_recent') || '[]');
    recent = recent.filter(r => r.name !== name);
    recent.unshift({ name, ts: Date.now() });
    localStorage.setItem('kb_recent', JSON.stringify(recent.slice(0, 30)));
  } catch (e) {}
}

function getRecent() {
  try { return JSON.parse(localStorage.getItem('kb_recent') || '[]'); } catch (e) { return []; }
}

function pickRecent() {
  const recent = getRecent();
  if (!recent.length) return toast('还没有访问记录');
  pickMode = 'page';
  pickItems = recent.map(r => ({ label: r.name, value: r.name }));
  $('pick-title').textContent = '最近打开';
  $('pick-prompt').textContent = 'f';
  $('pick-input').value = '';
  pickActive = 0;
  pickOnPick = null;
  renderPick('');
  openOverlay('ov-pick');
  setTimeout(() => $('pick-input').focus(), 30);
}

function pickBuffer() {
  if (!state.buffers.length) return toast('没有打开的缓冲');
  pickMode = 'page';
  pickItems = state.buffers.map(n => ({ label: n, value: n }));
  $('pick-title').textContent = '切换缓冲';
  $('pick-prompt').textContent = 'b';
  $('pick-input').value = '';
  pickActive = 0;
  pickOnPick = null;
  renderPick('');
  openOverlay('ov-pick');
  setTimeout(() => $('pick-input').focus(), 30);
}

/* ── 编辑器显示开关 ────────────────────────────────────── */

function toggleLineNumbers() {
  if (!cm) return toast('编辑器还没打开过');
  cm.setOption('lineNumbers', !cm.getOption('lineNumbers'));
  toast('行号 ' + (cm.getOption('lineNumbers') ? '开' : '关'));
}

function toggleLineWrapping() {
  if (!cm) return toast('编辑器还没打开过');
  cm.setOption('lineWrapping', !cm.getOption('lineWrapping'));
  toast('软换行 ' + (cm.getOption('lineWrapping') ? '开' : '关'));
}

function discardAndClose() {
  if (!state.editing) return toast('不在编辑状态');
  editorDirty = false;
  draftClear();
  closeEditor();
}

/* ══════════════════════════════════════════════════════════
   视图骨架
   ══════════════════════════════════════════════════════════ */

function enterView(mode) {
  // mode: 'content' | 'editor' | 'graph'
  // state.editing 只由 openEditor/closeEditor 负责——
  // 之前这里无条件置 false，会让 openEditor 刚设的 true 立刻被抹掉，
  // 结果是编辑器开着但 :w 报「不在编辑状态」。
  const view = $('view'), ed = $('editor');
  ed.classList.toggle('open', mode === 'editor');
  view.className = '';       // 视图类（graph-active 等）由调用方重新设置
  view.removeAttribute('style');
  view.style.display = mode === 'editor' ? 'none' : '';
  STATUS.pos.textContent = 'Ln 1, Col 1';
  STATUS.words.textContent = '';
}

/* ══════════════════════════════════════════════════════════
   阅读位置记忆 —— vim 的 "''" 标记（回到上次离开的地方）
   ──────────────────────────────────────────────────────────
   之前离开页面再回来总是回到顶部：一篇长页面读到一半点个链接，
   Ctrl-o 退回来又得从头往下滚。nvim 里同一个 buffer 的位置是记着的。

   做法：每次 dispatch 前把当前 #view 的 scrollTop 存进
   localStorage（按 route key 分开），视图渲染后按需还原。
   用「重试几次」而不是单次设置：showPage 是先 showView 骨架、
   再异步查 API 填内容，内容没到位时 scrollTop 设不上（会被 clamp）。
   ══════════════════════════════════════════════════════════ */

const SCROLL_KEY = 'kb-scroll-memo';
const SCROLL_MAX = 80;

let scrollMemo = null;
let scrollRouteKey = null;

function scrollMemoLoad() {
  if (scrollMemo) return scrollMemo;
  try {
    const raw = JSON.parse(localStorage.getItem(SCROLL_KEY) || '{}');
    scrollMemo = (raw && typeof raw === 'object' && !Array.isArray(raw)) ? raw : {};
  } catch (e) { scrollMemo = {}; }
  return scrollMemo;
}

function scrollMemoSave() {
  if (!scrollMemo) return;
  try {
    // 只留最近 SCROLL_MAX 条，避免 localStorage 无限长
    const keys = Object.keys(scrollMemo);
    if (keys.length > SCROLL_MAX) {
      const surplus = keys.slice(0, keys.length - SCROLL_MAX);
      for (const k of surplus) delete scrollMemo[k];
    }
    localStorage.setItem(SCROLL_KEY, JSON.stringify(scrollMemo));
  } catch (e) {}
}

/* 记下当前视图的滚动位置（dispatch 前调用） */
function saveScrollPos() {
  if (!scrollRouteKey) return;
  const v = $('view');
  if (!v) return;
  const m = scrollMemoLoad();
  // 接近顶部就不记，免得「一打开就归零」的无效条目堆满
  if (v.scrollTop < 40) delete m[scrollRouteKey];
  else m[scrollRouteKey] = v.scrollTop;
  scrollMemoSave();
}

/* 还原滚动位置。
 *
 * 两个坑：
 * ① 内容异步长进来（showPage 先骨架后 API、showAll 先骨架后列表），
 *    内容没到位时 scrollHeight 不够，设 scrollTop 会被浏览器 clamp。
 *    所以按「时间」重试（最多 ~2s），而不是按帧数 —— 网络慢时
 *    6 帧（100ms）根本等不到。
 * ② 没有记忆时必须显式归零：innerHTML 换掉后浏览器不会重置 scrollTop，
 *    上一页读到 1200 就会让新页从 1200 开始 —— 这是「点进新页面落在文章中段」。 */
function restoreScrollPos(key, deadline) {
  const want = scrollMemoLoad()[key];
  const v = $('view');
  if (!v) return;
  if (!want) { v.scrollTop = 0; return; }                 // 没有记忆 → 顶部
  if (deadline === undefined) deadline = Date.now() + 2000;
  const max = v.scrollHeight - v.clientHeight;
  if (max >= want - 8) { v.scrollTop = want; return; }    // 够高，设上
  if (Date.now() > deadline) { v.scrollTop = 0; return; } // 等不到内容，宁可顶部
  setTimeout(() => restoreScrollPos(key, deadline), 100);
}

function showView(html, kind) {
  state.listIdx = -1;   // 换视图：列表光标归零
  state.viewKind = kind || null;
  enterView('content');
  $('view').innerHTML = html;
  // 换视图后还原「这一页上次读到哪」（内容异步长的，restore 内部会重试）
  if (scrollRouteKey) restoreScrollPos(scrollRouteKey);
}

/* ══════════════════════════════════════════════════════════
   首页 dashboard
   ══════════════════════════════════════════════════════════ */

function getSetting(key, dflt) {
  const v = localStorage.getItem(key);
  return v === null ? dflt : v;
}

async function showHome(token) {
  state.current = null;
  state.editing = false;
  setStatusName(null);
  const dashPage = getSetting('kb-dashboard-page', '');
  try {
    const [count, recent, tags, todoInfo] = await Promise.all([
      pageCount().catch(() => 0),
      cached('pages:recent', `${API}/pages?limit=8`),
      getTags().catch(() => []),
      getTaskStats().catch(() => null),
    ]);
    if (token !== navToken) return;

    let dashHtml = '';
    if (dashPage) {
      try {
        const pg = await getPage(dashPage);
        if (token !== navToken) return;
        dashHtml = `<div class="card" style="margin-bottom:1.1em">
          <h3>${escapeHtml(dashPage)}<span class="spacer"></span>
            <span class="sl-hint" style="font-size:11px">space e 编辑</span></h3>
          <div class="doc">${renderMd(pg.content)}</div></div>`;
      } catch (e) { /* 看板页不存在就只显示统计 */ }
    }

    const recentHtml = recent.map(p =>
      `<li data-page="${escapeHtml(p.name)}"><b>${escapeHtml(p.name)}</b><span class="t">${relDate(p.updated_at)}</span></li>`
    ).join('') || '<li class="sl-hint">还没有页面</li>';

    const tagsHtml = tags.slice(0, 12).map(t =>
      `<span class="chip" data-tag="${escapeHtml(t.name)}">#${escapeHtml(t.name)}<span class="n"> ${t.count}</span></span>`
    ).join('');

    const todoHtml = todoInfo ? `
      <div class="card"><h3>任务 <span class="spacer"></span>
        <span class="sl-hint" style="font-size:11px">space t</span></h3>
        <div class="big" style="color:var(--warn)">${todoInfo.overdue}<span class="unit">逾期</span></div>
        <div style="font-size:12.5px;color:var(--muted);margin-top:.4em">
          今天 ${todoInfo.today} · 本周 ${todoInfo.week} · 共 ${todoInfo.open} 未完成
        </div></div>` : '';

    showView(`
      <div class="dash">
        <div class="dash-title">KB</div>
        <div class="dash-sub">${count} 个页面 · <span class="kbd-inline">space h</span> 看快捷键 · <span class="kbd-inline">:</span> 命令行</div>
        ${dashHtml}
        <div class="dash-grid">
          <div class="card"><h3>最近更新</h3><ul class="mini">${recentHtml}</ul></div>
          ${todoHtml || '<div class="card"><h3>任务</h3><div class="sl-hint" style="font-size:12.5px">未设置任务页</div></div>'}
          <div class="card"><h3>标签</h3><div class="tagcloud">${tagsHtml || '<span class="sl-hint">无</span>'}</div></div>
          <div class="card"><h3>入口</h3><ul class="mini">
            <li data-page=""><b>所有页面</b><span class="t">space a</span></li>
            <li data-page=""><b>今日日记</b><span class="t">space j</span></li>
            <li data-page=""><b>知识图谱</b><span class="t">space g</span></li>
          </ul></div>
        </div>
      </div>`);

    const view = $('view');
    view.querySelectorAll('.mini li[data-page]').forEach(li => {
      li.addEventListener('click', () => {
        const n = li.dataset.page;
        if (n) navigate('page', n);
      });
    });
    // 首页里的 wikilink / tag / mermaid
    markUnresolved(view);
    fillEmbeds(view);
    renderMermaid(view);
  } catch (e) {
    if (token !== navToken) return;
    showView(`<div class="empty">首页加载失败：${escapeHtml(e.message)}</div>`);
  }
}

/* ══════════════════════════════════════════════════════════
   页面阅读
   ══════════════════════════════════════════════════════════ */

async function showPage(name, token) {
  if (!name) { showHome(token); return; }
  state.current = name;
  state.editing = false;
  addBuffer(name);
  trackRecent(name);
  setStatusName(name, false);

  try {
    const pg = await getPage(name);
    if (token !== navToken) return;

    const tagsHtml = (pg.tags || []).map(t =>
      `<span class="chip" data-tag="${escapeHtml(t)}">#${escapeHtml(t)}</span>`).join('');
    const bl = (pg.backlinks || []);
    const blHtml = bl.length ? `
      <div class="backlinks">
        <h3>← 反向链接 ${bl.length}</h3>
        ${bl.map(b => `<div class="bl-item" data-page="${escapeHtml(b.name)}">
          <div class="bl-name">${escapeHtml(b.name)}</div>
          ${b.snippet ? `<div class="bl-snip">${escapeHtml(b.snippet.trim())}</div>` : ''}
        </div>`).join('')}
      </div>` : '';

    showView(`
      <div class="page-head">
        <div class="crumbs">page/<b>${escapeHtml(pg.name)}</b></div>
      <div class="doc" id="page-body">${renderMd(pg.content)}</div>
      </div>
      <div class="page-meta" style="max-width:62em;margin:.8em auto 0">
        <span>更新 ${fmtDate(pg.updated_at)}</span>
        <span>·</span>
        <span>${(pg.content || '').length} 字</span>
        ${tagsHtml ? '<span>·</span>' + tagsHtml : ''}
        <span class="spacer"></span>
        <button class="btn btn-sm" data-act="edit">编辑 (space e)</button>
      </div>
      ${blHtml}`, 'page');
    const view = $('view');
    markUnresolved(view);
    fillEmbeds(view);
    renderMermaid(view);
    STATUS.words.textContent = (pg.content || '').length + ' 字';
    const editBtn = view.querySelector('[data-act="edit"]');
    if (editBtn) editBtn.addEventListener('click', () => openEditor(name, null, ++navToken));
    if (window.KB && window.KB.trigger) window.KB.trigger('page', { name, page: pg });
  } catch (e) {
    if (token !== navToken) return;
    if (e.status === 404) {
      // 页面不存在 → 直接进编辑器，名字预填
      openEditor(name, '', ++navToken);
      return;
    }
    /* 断网 + 这一页没缓存过 → 明确说清原因和出路，别甩一句
     * 「Failed to fetch」（用户看不出是网断了还是服务挂了）。 */
    const offline = !navigator.onLine || /failed to fetch|networkerror|load failed/i.test(e.message || '');
    if (offline) {
      showView(`<div class="empty">
        <b>离线，这一页还没有本地缓存</b><br>
        <span style="opacity:.7">联网后打开过一次的页面才能离线阅读。</span><br><br>
        <button class="btn btn-sm" data-act="retry">重试</button>
      </div>`, 'page');
      const rb = document.querySelector('[data-act="retry"]');
      if (rb) rb.addEventListener('click', () => showPage(name, ++navToken));
      return;
    }
    showView(`<div class="empty">读取失败：${escapeHtml(e.message)}</div>`);
  }
}

/* ══════════════════════════════════════════════════════════
   所有页面 / 标签 / 搜索
   ══════════════════════════════════════════════════════════ */

let allPagesCache = null;

/* /pages 单次最多 500 条，KB 已经超过这个数。
 * 无条件相信一次请求 = 永远看不到最新的那些页面。 */
async function fetchAllPages() {
  if (_cache.has('pages:all')) return _cache.get('pages:all');
  const out = [];
  let offset = 0;
  for (let guard = 0; guard < 20; guard++) {
    const chunk = await jfetch(`${API}/pages?limit=500&offset=${offset}`);
    out.push(...chunk);
    if (chunk.length < 500) break;
    offset += 500;
  }
  _cache.set('pages:all', out);
  return out;
}

async function showAll(token) {
  state.current = null;
  state.editing = false;
  setStatusName(null);
  showView(`
    <div class="list-wrap">
      <div class="list-head"><h1>所有页面</h1><span class="count" id="all-count">…</span>
        <span class="spacer"></span>
        <span class="sl-hint" style="font-size:11.5px">j/k 移动 · Enter 打开</span>
      </div>
      <div class="filterbar"><span>/</span><input id="all-filter" placeholder="实时筛选…" autocomplete="off"></div>
      <div id="all-body"><div class="empty">加载中…</div></div>
    </div>`);
  try {
    const pages = await fetchAllPages();
    if (token !== navToken) return;
    allPagesCache = pages;
    const total = await pageCount().catch(() => pages.length);
    if (token !== navToken) return;
    $('all-count').textContent = (total > pages.length ? `${pages.length} / ${total}` : total) + ' 页';
    renderAllList(pages);
    const input = $('all-filter');
    input.addEventListener('input', () => renderAllList(allPagesCache, input.value.trim()));
    setTimeout(() => input.focus(), 30);
  } catch (e) {
    if (token !== navToken) return;
    $('all-body').innerHTML = `<div class="empty">加载失败：${escapeHtml(e.message)}</div>`;
  }
}

function renderAllList(pages, filter) {
  const q = (filter || '').toLowerCase();
  const list = q ? pages.filter(p => p.name.toLowerCase().includes(q)) : pages;
  if (!list.length) {
    $('all-body').innerHTML = `<div class="empty">没有匹配</div>`;
    return;
  }
  let html = '', last = '';
  for (const p of list) {
    const g = timeGroup(p.updated_at);
    if (g !== last) { html += `<div class="row-group">${g}</div>`; last = g; }
    html += `<div class="row" data-page="${escapeHtml(p.name)}">
      <span class="row-name">${escapeHtml(p.name)}</span>
      <span class="row-date">${relDate(p.updated_at)}</span></div>`;
  }
  $('all-body').innerHTML = html;
}

async function showTag(tag, token) {
  state.current = null;
  state.editing = false;
  setStatusName(null);
  showView(`
    <div class="list-wrap">
      <div class="list-head"><h1>#${escapeHtml(tag)}</h1><span class="count" id="tag-count">…</span></div>
      <div id="tag-body"><div class="empty">加载中…</div></div>
    </div>`);
  try {
    const rows = await jfetch(`${API}/tags/${encodeURIComponent(tag)}/pages`);
    if (token !== navToken) return;
    $('tag-count').textContent = rows.length + ' 页';
    $('tag-body').innerHTML = rows.map(p =>
      `<div class="row" data-page="${escapeHtml(p.name)}">
        <span class="row-name">${escapeHtml(p.name)}</span>
        <span class="row-date">${relDate(p.updated_at)}</span></div>`).join('')
      || '<div class="empty">没有页面使用这个标签</div>';
  } catch (e) {
    if (token !== navToken) return;
    $('tag-body').innerHTML = `<div class="empty">加载失败：${escapeHtml(e.message)}</div>`;
  }
}

async function showSearch(q, token) {
  state.current = null;
  state.editing = false;
  setStatusName(null);
  showView(`
    <div class="list-wrap">
      <div class="list-head"><h1>搜索</h1><span class="count">${escapeHtml(q)}</span></div>
      <div id="search-body"><div class="empty">搜索中…</div></div>
    </div>`);
  try {
    const rows = await jfetch(`${API}/pages?search=${encodeURIComponent(q)}&limit=60`);
    if (token !== navToken) return;
    if (!rows.length) {
      $('search-body').innerHTML = `<div class="empty">没有匹配「${escapeHtml(q)}」</div>`;
      return;
    }
    $('search-body').innerHTML = rows.map(r =>
      `<div class="row" data-page="${escapeHtml(r.name)}">
        <span class="row-name">${escapeHtml(r.name)}</span>
        <span class="row-snip">${escapeHtml((r.snippet || '').replace(/\s+/g, ' ').trim())}</span>
        <span class="row-date">${relDate(r.updated_at)}</span></div>`).join('');
  } catch (e) {
    if (token !== navToken) return;
    $('search-body').innerHTML = `<div class="empty">搜索失败：${escapeHtml(e.message)}</div>`;
  }
}

/* ══════════════════════════════════════════════════════════
   任务视图
   ══════════════════════════════════════════════════════════ */

const DEFAULT_TASK_PAGES = 'todo, Tasks';
function taskPages() {
  return (getSetting('kb-task-page', DEFAULT_TASK_PAGES) || DEFAULT_TASK_PAGES)
    .split(',').map(s => s.trim()).filter(Boolean);
}
/* localStorage 里存的第一项决定当前来源；没存过就用第一个存在的页面。
 * 旧版默认写死 'Tasks'，但这台机器的实际任务页叫 'todo'。 */
async function resolveTaskPage() {
  const saved = getSetting('kb-task-page-current', '');
  if (saved) return saved;
  const cands = taskPages();
  try {
    const names = await ensureNames();
    for (const c of cands) if (names.some(n => n.toLowerCase() === c.toLowerCase())) {
      localStorage.setItem('kb-task-page-current', c);
      return c;
    }
  } catch (e) {}
  return cands[0];
}
let _taskPageResolved = '';
function taskPage() {
  return _taskPageResolved || getSetting('kb-task-page-current', '') || taskPages()[0] || 'todo';
}

function parseTaskLine(line) {
  const m = line.match(/^\s*(p[1-4]|\d)\s+(\d{4}-\d{2}-\d{2})\s+(.*?)(?:\s+due:(\d{4}-\d{2}-\d{2}))?(?:\s+(rec:\S+))?(?:\s+done:(\d{4}-\d{2}-\d{2}))?\s*$/i);
  if (!m) return null;
  let [, pri, start, mid, due, rec, done] = m;
  pri = pri.toLowerCase();
  if (/^[1-4]$/.test(pri)) pri = 'p' + pri;
  const links = [];
  const re = /\[\[([^\]]+)\]\]/g;
  let lm;
  while ((lm = re.exec(mid)) !== null) links.push(lm[1].split('|')[0].trim());
  const title = mid.replace(re, '').trim().replace(/\s+/g, ' ');
  if (!title) return null;
  return { pri, start, title, links, due: due || null, rec: rec || null, done: done || null, raw: line };
}

function parseTasks(content) {
  const out = [];
  content.split('\n').forEach((l, i) => {
    const t = parseTaskLine(l);
    if (t) { t.lineIndex = i; out.push(t); }
  });
  return out;
}

async function getTaskStats() {
  _taskPageResolved = await resolveTaskPage();
  const page = await getPage(_taskPageResolved);
  const tasks = parseTasks(page.content).filter(t => !t.done);
  const today = todayStr();
  const weekEnd = new Date(Date.now() + 7 * 864e5 - new Date().getTimezoneOffset() * 60000)
    .toISOString().slice(0, 10);
  return {
    open: tasks.length,
    overdue: tasks.filter(t => t.due && t.due < today).length,
    today: tasks.filter(t => t.due === today).length,
    week: tasks.filter(t => t.due && t.due >= today && t.due <= weekEnd).length,
  };
}

async function showTodo(token) {
  state.current = null;
  state.editing = false;
  setStatusName(null);
  _taskPageResolved = await resolveTaskPage();
  const pages = taskPages();
  showView(`
    <div class="list-wrap">
      <div class="list-head">
        <h1>任务</h1>
        <select id="todo-src" class="btn btn-sm" style="font-family:var(--mono)"></select>
        <span class="count" id="todo-count">…</span>
        <span class="spacer"></span>
        <button class="btn btn-sm" data-act="addtask">+ 新任务</button>
      </div>
      <div id="todo-body"><div class="empty">加载中…</div></div>
    </div>`);
  const sel = $('todo-src');
  sel.innerHTML = pages.map(p => `<option${p === taskPage() ? ' selected' : ''}>${escapeHtml(p)}</option>`).join('');
  sel.addEventListener('change', () => {
    localStorage.setItem('kb-task-page-current', sel.value);
    showTodo(++navToken);
  });
  const addBtn = document.querySelector('[data-act="addtask"]');
  if (addBtn) addBtn.addEventListener('click', () => quickAddTask());

  try {
    const page = await getPage(taskPage());
    if (token !== navToken) return;
    const all = parseTasks(page.content);
    renderTasks2(all);
  } catch (e) {
    if (token !== navToken) return;
    $('todo-body').innerHTML = `<div class="empty">任务页「${escapeHtml(taskPage())}」不可读<br>
      <span class="hintline">:set taskpage &lt;页面名&gt; 可改来源</span></div>`;
  }
}

function renderTasks(tasks) {
  const today = todayStr();
  const todoSrc = taskPage();
  const open = tasks.filter(t => !t.done);
  const done = tasks.filter(t => t.done);

  const group = (title, list) => {
    if (!list.length) return '';
    return `<div class="row-group">${title} · ${list.length}</div>` + list.map(t => taskRow(t, today)).join('');
  };

  const overdue = open.filter(t => t.due && t.due < today);
  const dueToday = open.filter(t => t.due === today);
  const soon = open.filter(t => t.due && t.due > today);
  const noDue = open.filter(t => !t.due);

  $('todo-count').textContent = open.length + ' 未完成';
  $('todo-body').innerHTML =
    group('逾期', overdue) + group('今天', dueToday) + group('以后', soon) +
    group('无截止日', noDue) + (done.length ? `<div class="row-group">已完成 · ${done.length}</div>` + done.slice(0, 20).map(t => taskRow(t, today)).join('') : '')
    || '<div class="empty">没有任务</div>';

  $('todo-body').querySelectorAll('.task .box').forEach(box => {
    box.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleTask(parseInt(box.dataset.idx, 10));
    });
  });
  $('todo-body').querySelectorAll('.task').forEach(row => {
    row.addEventListener('click', (e) => {
      if (e.target.closest('.box')) return;
      const a = e.target.closest('a[data-page]');
      if (a) { navigate('page', a.dataset.page); }
    });
  });
}

function taskRow(t, today) {
  const cls = ['task'];
  if (t.done) cls.push('done');
  let dueCls = '';
  if (t.due) {
    if (t.due < today) dueCls = ' over';
    else if (t.due === today) dueCls = ' today';
  }
  const title = t.title.replace(/\[\[([^\]]+)\]\]/g, '[[$1]]');
  const linksHtml = t.links.map(l =>
    `<a class="wikilink" href="#" data-page="${escapeHtml(l)}">${escapeHtml(l)}</a>`).join(' ');
  return `<div class="${cls.join(' ')}">
    <span class="box" data-idx="${t._idx}" title="完成 / 取消">${t.done ? '✓' : '○'}</span>
    <span class="pri ${t.pri}">${t.pri.toUpperCase()}</span>
    <span class="task-title">${escapeHtml(t.title)} ${linksHtml}</span>
    ${t.rec ? `<span class="rec">${escapeHtml(t.rec)}</span>` : ''}
    ${t.due ? `<span class="due${dueCls}">${escapeHtml(t.due)}</span>` : ''}
  </div>`;
}

let _taskList = [];

function renderTasks2(tasks) {
  tasks.forEach((t, i) => { t._idx = i; });
  _taskList = tasks;
  renderTasks(tasks);
}

async function toggleTask(idx) {
  const t = _taskList[idx];
  if (!t) return;
  const src = taskPage();
  try {
    const page = await jfetch(`${API}/pages/${encodeURIComponent(src)}`);
    const lines = page.content.split('\n');
    const li = lines.findIndex(l => l.trim() === t.raw.trim());
    if (li === -1) { toast('任务行已变化，请刷新', 'error'); return; }
    let next;
    if (t.done) {
      next = t.raw.replace(/\s+done:\S+\s*$/i, '').trimEnd();
    } else if (t.rec) {
      const nd = recalcRecurring(t, todayStr());
      if (!nd) { toast('无法计算周期', 'error'); return; }
      const base = t.raw.replace(/\s+due:\S+/g, '').replace(/\s+rec:\S+/g, '').replace(/\s+done:\S+\s*$/i, '').trimEnd();
      next = base + ' due:' + nd + ' ' + t.rec;
    } else {
      next = t.raw.replace(/\s+done:\S+\s*$/i, '').trimEnd() + ' done:' + todayStr();
    }
    lines[li] = next;
    await jfetch(`${API}/pages/${encodeURIComponent(src)}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: lines.join('\n') }),
    });
    invalidateCache('page:');
    toast(t.done ? '已取消完成' : (t.rec ? '已滚动到下一周期' : '完成'));
    showTodo(++navToken);
  } catch (e) {
    toast('更新失败：' + e.message, 'error');
  }
}

function recalcRecurring(t, compDate) {
  if (!t.rec) return null;
  const due = new Date(t.due || t.start);
  const comp = new Date(compDate);
  const iso = (d) => {
    const x = new Date(d - d.getTimezoneOffset() * 60000);
    return x.toISOString().slice(0, 10);
  };
  if (t.rec === 'rec:+1m' || t.rec === 'rec:+1y') {
    let d = new Date(due);
    while (d <= comp && d.getFullYear() < 2200) {
      if (t.rec === 'rec:+1m') d.setMonth(d.getMonth() + 1);
      else d.setFullYear(d.getFullYear() + 1);
    }
    return iso(d);
  }
  const m = t.rec.match(/^rec:(\d+)(w|d|b)$/);
  if (m) {
    const n = parseInt(m[1], 10), unit = m[2];
    let d = new Date(comp), added = 0;
    if (unit === 'w') { d.setDate(d.getDate() + n * 7); return iso(d); }
    if (unit === 'd') { d.setDate(d.getDate() + n); return iso(d); }
    while (added < n) { d.setDate(d.getDate() + 1); if (d.getDay() !== 0 && d.getDay() !== 6) added++; }
    return iso(d);
  }
  return null;
}

async function quickAddTask() {
  const text = await promptText('新任务', '标题（可用 [[页面]] 链接）');
  if (!text) return;
  const src = taskPage();
  const line = `p2 ${todayStr()} ${text}`;
  try {
    const page = await jfetch(`${API}/pages/${encodeURIComponent(src)}`);
    const content = (page.content.replace(/\s*$/, '') + '\n' + line).replace(/^\n/, '');
    await jfetch(`${API}/pages/${encodeURIComponent(src)}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    });
    invalidateCache('page:');
    toast('已添加');
    showTodo(++navToken);
  } catch (e) { toast('添加失败：' + e.message, 'error'); }
}

/* ══════════════════════════════════════════════════════════
   日记
   ══════════════════════════════════════════════════════════ */

async function showJournal(date, token) {
  date = date || todayStr();
  let content = '';
  try { content = (await getPage(date)).content; } catch (e) { content = ''; }
  if (token !== navToken) return;
  openEditor(date, content, token, true);
}

/* ══════════════════════════════════════════════════════════
   编辑器（CodeMirror + vim）
   ══════════════════════════════════════════════════════════ */

let cm = null;
let editorDirty = false;

function initCM() {
  if (cm) return cm;
  cm = CodeMirror.fromTextArea($('cm'), {
    mode: 'markdown',
    theme: 'catppuccin',
    lineNumbers: true,
    lineWrapping: true,
    matchBrackets: true,
    autoCloseBrackets: true,
    styleActiveLine: true,
    indentUnit: 2,
    tabSize: 2,
    scrollPastEnd: 0.35,
    keyMap: 'vim',
    extraKeys: {
      /* 注意：这里**不能**放 'Space'。leader 统一在 document 的 capture
       * 阶段判定（见下面「Leader 唯一出口」）。 */
      'Tab': (c) => {
        const v = c.state.vim;
        if (v && v.insertMode) { c.replaceSelection('  '); return; }
        return CodeMirror.Pass;
      },
    },
  });

  // 插件注册的 CodeMirror 扩展（补全等）
  if (window.KB && typeof window.KB.cmExt === 'function') {
    for (const fn of window.KB.cmExt()) { try { fn(cm); } catch (e) { console.warn('[cm ext]', e); } }
  }
  if (window.KB && window.KB.trigger) window.KB.trigger('editor', { cm });

  // 按当前输入模式决定 vim 键位是否启用（touch 默认直接可输入）
  if (isTouchUi()) {
    cm.setOption('keyMap', 'default');
    setTimeout(() => { try { cm.focus(); } catch (e) {} }, 40);
  }

  cm.on('vim-mode-change', (e) => {
    if (!e) return;
    setMode(e.mode === 'replace' ? 'replace' : e.mode);
    if (e.mode === 'insert') closeWhichKey();
    // 输入法状态：非 insert 模式下中文输入法会把普通模式按键吃掉，
    // 用 statusline 提示（vim 里没有这问题，浏览器有）
    try {
      const ta = cm.getInputField();
      if (ta) ta.style.imeMode = e.mode === 'insert' ? 'auto' : 'active';
    } catch (err) {}
  });
  setMode('normal');

  cm.on('change', () => {
    if (!editorDirty) { editorDirty = true; setStatusName(state.current, true); }
    const v = cm.getValue();
    STATUS.words.textContent = v.length + ' 字';
    draftSave();
    if ($('editor').classList.contains('preview-on')) updatePreview();
  });

  cm.on('cursorActivity', () => {
    const c = cm.getCursor();
    STATUS.pos.textContent = 'Ln ' + (c.line + 1) + ', Col ' + (c.ch + 1);
  });

  // nvim-ish Ex 命令（编辑态下的 :xxx）
  const defineEx = (name, fn) => {
    try {
      CodeMirror.Vim.defineEx(name, name, function (c, params) { fn((params && params.args) || []); });
    } catch (e) { /* 已注册 */ }
  };
  defineEx('w', () => saveEditor());
  defineEx('write', () => saveEditor());
  defineEx('q', () => closeEditor());
  defineEx('quit', () => closeEditor());
  defineEx('wq', () => { saveEditor().then(() => closeEditor()); });
  defineEx('x', () => { if (editorDirty) saveEditor().then(() => closeEditor()); else closeEditor(); });
  defineEx('e', (a) => runCommand('e', a));
  defineEx('edit', (a) => runCommand('e', a));
  defineEx('new', () => runCommand('new', []));
  defineEx('help', () => runCommand('help', []));
  defineEx('graph', () => runCommand('graph', []));
  defineEx('todo', () => runCommand('todo', []));
  defineEx('journal', (a) => runCommand('journal', a));
  defineEx('all', () => runCommand('all', []));
  defineEx('home', () => runCommand('home', []));
  defineEx('theme', () => runCommand('theme', []));
  defineEx('versions', () => runCommand('versions', []));
  defineEx('settings', () => runCommand('settings', []));
  defineEx('find', (a) => runCommand('find', a));
  defineEx('rename', (a) => runCommand('rename', a));
  defineEx('del', () => runCommand('del', []));
  defineEx('preview', () => togglePreview());
  defineEx('set', (a) => runCommand('set', a));

  return cm;
}

function draftKey(name) { return 'kb-draft:page:' + (name || '').toLowerCase(); }
let draftTimer = null;
function draftSave() {
  clearTimeout(draftTimer);
  draftTimer = setTimeout(() => {
    try {
      localStorage.setItem(draftKey(state.current), JSON.stringify({
        name: state.current, content: cm.getValue(), ts: Date.now(),
      }));
    } catch (e) {}
  }, 1200);
}
function draftClear() {
  try { localStorage.removeItem(draftKey(state.current)); } catch (e) {}
}
function draftCheck(name, content) {
  try {
    const d = JSON.parse(localStorage.getItem(draftKey(name)) || 'null');
    if (!d || typeof d.content !== 'string') return null;
    if (d.content === content) { localStorage.removeItem(draftKey(name)); return null; }
    return d;
  } catch (e) { return null; }
}

function openEditor(name, content, token, isJournal) {
  state.editing = true;
  state.current = name || '';
  editorDirty = false;
  enterView('editor');
  const c = initCM();
  $('ed-name').textContent = name ? name : '(新页面)';
  $('ed-hint').textContent = isJournal ? '日记' : (name ? '' : '输入页面名后 :w 保存');

  const load = (text) => {
    c.setValue(text || '');
    c.clearHistory();
    editorDirty = false;
    setStatusName(name, false);
    STATUS.words.textContent = (text || '').length + ' 字';
    setTimeout(() => { c.refresh(); c.focus(); }, 20);
    if (isTouchUi()) { c.setOption('keyMap', 'default'); setMode('insert'); renderFxPanel(); }
    const d = draftCheck(name, text || '');
    if (d) {
      toast(`有草稿（${relDate(new Date(d.ts).toISOString().slice(0, 19).replace('T', ' '))}），:w 覆盖 / 刷新丢掉`, 'warn');
    }
  };

  if (content !== null && content !== undefined) {
    load(content);
    if (name) addBuffer(name);
    bumpTabline();
    return;
  }
  // 需要从服务器取
  getPage(name).then(pg => {
    if (token !== navToken) return;
    load(pg.content);
    const d = draftCheck(name, pg.content);
    if (d) showDraftPrompt(name, pg.content, d);
  }).catch(() => {
    if (token !== navToken) return;
    load('');
  });
  if (name) addBuffer(name);
  bumpTabline();
}

function bumpTabline() {
  setTimeout(renderTabline, 0);
}

function showDraftPrompt(name, serverContent, draft) {
  setHint(`草稿更新于 ${relDate(new Date(draft.ts).toISOString().slice(0, 19).replace('T', ' '))} · :draft 恢复 · :fresh 丢弃`);
}

function setHint(text) {
  const h = $('ed-hint');
  if (h) h.textContent = text || '';
}

function updatePreview() {
  const p = $('preview');
  if (!p) return;
  p.innerHTML = `<div class="doc">${renderMd(cm.getValue())}</div>`;
  markUnresolved(p);
  fillEmbeds(p);
  renderMermaid(p);
}

function togglePreview() {
  const ed = $('editor');
  ed.classList.toggle('preview-on');
  if (ed.classList.contains('preview-on')) updatePreview();
  else setTimeout(() => cm && cm.refresh(), 30);
  return true;
}

async function saveEditor() {
  const name = state.current;
  const content = cm.getValue();
  if (!name) {
    // 没有名字：现在提示输入
    const n = await promptText('页面名', '保存为新页面（即 [[链接目标]]）');
    if (!n) return;
    state.current = n;
    $('ed-name').textContent = n;
    addBuffer(n);
    return saveEditor();
  }
  try {
    try {
      await jfetch(`${API}/pages/${encodeURIComponent(name)}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content }),
      });
    } catch (e) {
      if (e.status === 404) {
        await jfetch(`${API}/pages`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name, content }),
        });
      } else throw e;
    }
    invalidateCache();
    editorDirty = false;
    setStatusName(name, false);
    draftClear();
    toast('已保存 ' + name);
    updatePageCount();
  } catch (e) {
    toast('保存失败：' + e.message, 'error');
    throw e;
  }
}

function closeEditor() {
  if (editorDirty) {
    toast('有未保存改动，:w 保存 / :q! 丢弃', 'warn');
    return;
  }
  state.editing = false;
  _taskPageResolved = _taskPageResolved;
  const name = state.current;
  if (name) navigate('page', name);
  else navigate('home');
}

/* ── 页面名 / 重命名 / 删除 ─────────────────────────────── */

async function renameCurrent(newName) {
  const old = state.current;
  if (!old || !newName) return;
  try {
    await jfetch(`${API}/pages/${encodeURIComponent(old)}/rename`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: newName }),
    });
    invalidateCache();
    state.buffers = state.buffers.map(b => b === old ? newName : b);
    state.current = newName;
    $('ed-name').textContent = newName;
    renderTabline();
    toast(`已重命名 → ${newName}`);
  } catch (e) { toast('重命名失败：' + e.message, 'error'); }
}

async function deleteCurrent() {
  const name = state.current;
  if (!name) return;
  const ok = await confirmText(`删除「${name}」？`, '可在回收站恢复');
  if (!ok) return;
  try {
    await jfetch(`${API}/pages/${encodeURIComponent(name)}`, { method: 'DELETE' });
    invalidateCache();
    closeBuffer(name);
    state.current = null;
    state.editing = false;
    toast('已删除，可在 :trash 恢复');
    navigate('all');
  } catch (e) { toast('删除失败：' + e.message, 'error'); }
}

/* ══════════════════════════════════════════════════════════
   图谱（canvas，避免 579 节点 SVG 卡顿）
   ══════════════════════════════════════════════════════════ */

let gfx = null;

async function showGraph(token) {
  state.current = null;
  state.editing = false;
  setStatusName(null);
  enterView('content');
  $('view').className = 'graph-active';
  $('view').innerHTML = `<div id="graphwrap"><canvas id="graph-svg"></canvas>
    <div class="graph-hud" id="graph-hud">加载中…</div></div>`;

  try {
    const data = await cached('graph', `${API}/graph`);
    if (token !== navToken) return;
    startGraph(data);
  } catch (e) {
    if (token !== navToken) return;
    $('graph-hud').textContent = '图谱加载失败：' + e.message;
  }
}

function startGraph(data) {
  const canvas = $('graph-svg');
  const ctx = canvas.getContext('2d');
  const nodes = data.nodes.map((n, i) => ({ id: i, name: n.name, x: 0, y: 0, vx: 0, vy: 0, deg: 0 }));
  const idx = new Map(nodes.map(n => [n.name.toLowerCase(), n]));
  const edges = [];
  for (const e of data.edges) {
    const a = idx.get(String(e.source).toLowerCase());
    const b = idx.get(String(e.target).toLowerCase());
    if (!a || !b || a === b) continue;
    edges.push([a, b]);
    a.deg++; b.deg++;
  }

  // 只画有连接的节点：孤立页面对图谱没有信息量
  const live = nodes.filter(n => n.deg > 0);

  // 初始化：圆形排布，避免全部叠在原点
  const R = Math.min(canvas.clientWidth, canvas.clientHeight) * 0.35 || 300;
  live.forEach((n, i) => {
    const a = (i / live.length) * Math.PI * 2;
    n.x = Math.cos(a) * R * (0.5 + Math.random() * 0.5);
    n.y = Math.sin(a) * R * (0.5 + Math.random() * 0.5);
  });

  // 力导向（Fruchterman-Reingold 简化版），限定迭代次数保证首屏快
  const ITER = 240;
  let k = Math.sqrt((1.0 * 900 * 620) / Math.max(1, live.length));
  for (let it = 0; it < ITER; it++) {
    const alpha = 1 - it / ITER;
    // 斥力
    for (let i = 0; i < live.length; i++) {
      const a = live[i];
      for (let j = i + 1; j < live.length; j++) {
        const b = live[j];
        let dx = a.x - b.x, dy = a.y - b.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 0.01) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = 0.01; }
        const f = (k * k * alpha) / d2;
        a.vx += dx * f; a.vy += dy * f;
        b.vx -= dx * f; b.vy -= dy * f;
      }
    }
    // 引力
    for (const [a, b] of edges) {
      let dx = a.x - b.x, dy = a.y - b.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
      const f = (d * d) / (k * alpha + 0.01) * 0.6;
      a.vx -= dx / d * f; a.vy -= dy / d * f;
      b.vx += dx / d * f; b.vy += dy / d * f;
    }
    // 向心 + 阻尼
    for (const n of live) {
      n.vx -= n.x * 0.012 * alpha;
      n.vy -= n.y * 0.012 * alpha;
      n.vx *= 0.72; n.vy *= 0.72;
      n.x += Math.max(-24, Math.min(24, n.vx));
      n.y += Math.max(-24, Math.min(24, n.vy));
    }
  }

  const view = { x: 0, y: 0, z: 1 };
  let hover = null;
  const neigh = new Map();
  for (const [a, b] of edges) {
    if (!neigh.has(a.id)) neigh.set(a.id, new Set());
    if (!neigh.has(b.id)) neigh.set(b.id, new Set());
    neigh.get(a.id).add(b.id); neigh.get(b.id).add(a.id);
  }

  function resize() {
    const r = canvas.parentElement.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = r.width * dpr;
    canvas.height = r.height * dpr;
    canvas.style.width = r.width + 'px';
    canvas.style.height = r.height + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function colors() {
    const cs = getComputedStyle(document.documentElement);
    return {
      fg: cs.getPropertyValue('--fg').trim(),
      accent: cs.getPropertyValue('--accent').trim(),
      accent2: cs.getPropertyValue('--accent2').trim(),
      line: cs.getPropertyValue('--line').trim(),
      faint: cs.getPropertyValue('--faint').trim(),
      bg: cs.getPropertyValue('--bg').trim(),
      ok: cs.getPropertyValue('--ok').trim(),
    };
  }

  function draw() {
    const c = colors();
    const w = canvas.clientWidth, h = canvas.clientHeight;
    ctx.fillStyle = c.bg;
    ctx.fillRect(0, 0, w, h);
    ctx.save();
    ctx.translate(w / 2 + view.x, h / 2 + view.y);
    ctx.scale(view.z, view.z);

    // 边
    ctx.strokeStyle = c.line;
    ctx.lineWidth = 0.6 / view.z;
    for (const [a, b] of edges) {
      const involved = hover && (a.id === hover.id || b.id === hover.id);
      ctx.globalAlpha = hover ? (involved ? 0.9 : 0.12) : 0.42;
      ctx.beginPath();
      ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;

    // 节点
    for (const n of live) {
      const r = 2.4 + Math.min(6, Math.sqrt(n.deg) * 1.15);
      const isHover = hover && hover.id === n.id;
      const isNeigh = hover && neigh.get(hover.id) && neigh.get(hover.id).has(n.id);
      ctx.beginPath();
      ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
      ctx.fillStyle = isHover ? c.accent : (isNeigh ? c.accent2 : c.faint);
      ctx.globalAlpha = hover ? (isHover || isNeigh ? 1 : 0.25) : 0.85;
      ctx.fill();
      if (n.deg >= 8 || isHover || isNeigh) {
        ctx.globalAlpha = isHover ? 1 : 0.8;
        ctx.font = `${11 / view.z}px ui-monospace, monospace`;
        ctx.fillStyle = isHover ? c.fg : c.faint;
        ctx.fillText(n.name, n.x + r + 3 / view.z, n.y + 3 / view.z);
      }
    }
    ctx.globalAlpha = 1;
    ctx.restore();
  }

  function hitTest(mx, my) {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    const x = (mx - w / 2 - view.x) / view.z;
    const y = (my - h / 2 - view.y) / view.z;
    let best = null, bestD = 14 * 14 / (view.z * view.z);
    for (const n of live) {
      const d = (n.x - x) * (n.x - x) + (n.y - y) * (n.y - y);
      if (d < bestD) { bestD = d; best = n; }
    }
    return best;
  }

  let dragging = false, lastX = 0, lastY = 0;

  canvas.addEventListener('mousemove', (e) => {
    const r = canvas.getBoundingClientRect();
    const mx = e.clientX - r.left, my = e.clientY - r.top;
    if (dragging) {
      view.x += e.clientX - lastX;
      view.y += e.clientY - lastY;
      lastX = e.clientX; lastY = e.clientY;
      canvas.style.cursor = 'grabbing';
      draw();
      return;
    }
    const n = hitTest(mx, my);
    if (n !== hover) { hover = n; draw(); }
    canvas.style.cursor = n ? 'pointer' : 'grab';
    if (n) $('graph-hud').textContent = n.name + ' · ' + n.deg + ' 条链接';
  });

  canvas.addEventListener('mousedown', (e) => {
    const r = canvas.getBoundingClientRect();
    const n = hitTest(e.clientX - r.left, e.clientY - r.top);
    if (n) return;
    dragging = true; lastX = e.clientX; lastY = e.clientY;
  });
  window.addEventListener('mouseup', () => { dragging = false; });
  canvas.addEventListener('click', (e) => {
    const r = canvas.getBoundingClientRect();
    const n = hitTest(e.clientX - r.left, e.clientY - r.top);
    if (n) navigate('page', n.name);
  });
  canvas.addEventListener('wheel', (e) => {
    e.preventDefault();
    const f = e.deltaY < 0 ? 1.12 : 0.9;
    view.z = Math.max(0.22, Math.min(4, view.z * f));
    draw();
  }, { passive: false });

  let touchDist = 0;
  canvas.addEventListener('touchstart', (e) => {
    if (e.touches.length === 2) {
      touchDist = Math.hypot(e.touches[0].clientX - e.touches[1].clientX,
                             e.touches[0].clientY - e.touches[1].clientY);
    }
  }, { passive: true });
  canvas.addEventListener('touchmove', (e) => {
    if (e.touches.length === 2 && touchDist) {
      const d = Math.hypot(e.touches[0].clientX - e.touches[1].clientX,
                           e.touches[0].clientY - e.touches[1].clientY);
      view.z = Math.max(0.22, Math.min(4, view.z * (d / touchDist)));
      touchDist = d; draw();
    }
    e.preventDefault();
  }, { passive: false });

  window.addEventListener('resize', () => { resize(); draw(); });

  resize();
  draw();
  $('graph-hud').textContent = `${live.length} 节点 · ${edges.length} 链接 · 滚轮缩放 · 点击打开`;
  gfx = { draw };
}

/* ══════════════════════════════════════════════════════════
   命令行
   ══════════════════════════════════════════════════════════ */

const cmd = {
  open: false, prefix: ':',
};

/* ══════════════════════════════════════════════════════════
   命令行历史 —— vim 的 cmdline history（↑ / ↓，等价 Ctrl-p / Ctrl-n）
   ──────────────────────────────────────────────────────────
   之前 `:` 里按 ↑ 只是浏览器原生的「上一格」，等于没有。
   vim 里 ↑ 是「上一条命令」，反复按能一路翻回去。

   ⚠ 一个老坑：命令行是「前缀状态机」。如果历史里存了裸 `/`
   或 `search`（不带参数），选它回车会变成「Page not found」——
   vim 在这里就是回显该行，而 vim 里裸 `/` 回车是「重复上一个搜索」。
   vim 搜索历史如果只有一个 `/`，按上箭头会跳过它。
   这里不跳，但把空搜索挡掉给出解释。
   ══════════════════════════════════════════════════════════ */

const CMDHIST_KEY = 'kb-cmdline-history';
const CMDHIST_MAX = 200;

let cmdHist = null;
let cmdHistPos = -1;
let cmdHistDraft = '';

function cmdHistLoad() {
  if (cmdHist) return cmdHist;
  try {
    const raw = JSON.parse(localStorage.getItem(CMDHIST_KEY) || '[]');
    cmdHist = Array.isArray(raw) ? raw.filter(x => typeof x === 'string').slice(-CMDHIST_MAX) : [];
  } catch (e) { cmdHist = []; }
  return cmdHist;
}

function cmdHistPush(line) {
  line = String(line || '').trim();
  if (!line) return;
  const h = cmdHistLoad();
  // 连续重复的不再存（vim 同行为）
  if (h[h.length - 1] !== line) h.push(line);
  if (h.length > CMDHIST_MAX) h.splice(0, h.length - CMDHIST_MAX);
  cmdHist = h;
  try { localStorage.setItem(CMDHIST_KEY, JSON.stringify(h)); } catch (e) {}
}

function cmdHistReset(draft) {
  cmdHistLoad();
  cmdHistPos = cmdHist.length;      // 指向「历史末尾之后」= 当前草稿
  cmdHistDraft = draft || '';
}

/* 方向键：dir=+1 往回翻（更早），dir=-1 往前翻（更近）
 *
 * 语义照 readline / vim：位置在「末尾之后」时保存当前已输入的内容，
 * 走到末尾之外就恢复它 —— 否则按 ↓ 翻回来时只拿到空串，原始半条命令没了。 */
function cmdHistMove(dir) {
  const h = cmdHistLoad();
  if (!h.length) return null;
  if (cmdHistPos < 0) cmdHistReset($('cmdline-input').value);

  let next = cmdHistPos + dir;
  if (next < 0) next = 0;
  if (next > h.length) next = h.length;

  if (next === cmdHistPos) return null;              // 到头了，不动
  cmdHistPos = next;
  return cmdHistPos >= h.length ? cmdHistDraft : h[cmdHistPos];
}

function openCmdline(prefix) {
  const el = $('cmdline');
  el.classList.add('open');
  cmd.open = true;
  /* 必须存下来：submitCmdline / updateCmdHint / 输入事件都靠 cmd.prefix
   * 区分「搜索」和「命令」。原来这里只写进 DOM 不写进状态，于是按 /
   * 打开后 cmd.prefix 仍是 ':'，输入关键词被当成未知命令执行。 */
  cmd.prefix = prefix || ':';
  $('cmdline-prompt').textContent = prefix;
  const input = $('cmdline-input');
  input.value = '';
  $('cmdline-hint').innerHTML = prefix === '/'
    ? '输入关键词搜索 · <span class="k">Enter</span> 执行'
    : '命令 · <span class="k">Tab</span> 补全 · <span class="k">Esc</span> 关闭';
  setMode('cmd');
  cmdHistReset('');
  setTimeout(() => input.focus(), 10);
  if (prefix === '/') {
    setHintCmd('搜索标题与正文');
  }
  updateCmdHint();
}

function setHintCmd(t) { $('cmdline-hint').innerHTML = t; }

function closeCmdline() {
  $('cmdline').classList.remove('open');
  cmd.open = false;
  // 焦点必须离开命令行输入框。否则后续按键（比如 Esc 后再按 `:`）
  // 会被这个仍然聚焦的 input 吃掉，内容是「上一条 + 新输入的字符」。
  /* 焦点必须离开命令行输入框 —— 无条件 blur，别指望「判断 activeElement」
   * 那套（实测 Escape 之后焦点仍在 input 上，于是下次按 `:` 的冒号
   * 被输入框吃掉，命令行里显示 ':xxx'）。 */
  const input = $('cmdline-input');
  if (input) {
    input.blur();
    // 编辑器里接着交还给 CodeMirror（normal 模式），阅读态交还给 body
    if (state.editing && cm) setTimeout(() => cm.focus(), 0);
  }
  setMode(cm && cm.state.vim ? (cm.state.vim.insertMode ? 'insert' : 'normal') : 'normal');
}

const BASE_COMMANDS = [
  ['w', '保存当前页面'],
  ['write', '保存当前页面'],
  ['q', '退出编辑器'],
  ['quit', '退出编辑器'],
  ['wq', '保存并退出'],
  ['x', '保存（如有改动）并退出'],
  ['e <页面>', '打开页面编辑'],
  ['edit <页面>', '打开页面编辑'],
  ['new', '新建页面'],
  ['find <词>', '模糊跳转页面'],
  ['rename <新名>', '重命名当前页'],
  ['del', '删除当前页（进回收站）'],
  ['versions', '版本历史'],
  ['preview', '切换预览'],
  ['all', '所有页面'],
  ['home', '回到首页'],
  ['graph', '知识图谱'],
  ['todo', '任务'],
  ['journal [日期]', '日记'],
  ['tag <标签>', '按标签浏览'],
  ['tags', '标签云'],
  ['search <词>', '全文搜索'],
  ['theme', '切换主题（循环）'],
  ['colorscheme [名字]', '切配色（不带参数列出全部，同 nvim）'],
  ['mobile!', '移动端预览开/关（调试）'],
  ['vimui', '强制 vim 输入模式'],
  ['touchui', '强制 touch 输入模式'],
  ['autoinput', '按输入设备自动选'],
  ['settings', '设置'],
  ['set taskpage <页>', '改任务来源页'],
  ['set dashboard <页>', '改首页看板页'],
  ['export [zip|json]', '导出'],
  ['import', '导入'],
  ['trash', '回收站'],
  ['help', '快捷键帮助'],
  ['keys', '快捷键帮助'],
  ['draft', '恢复草稿'],
  ['fresh', '丢弃草稿'],
  ['reload', '重新拉取当前页'],
  // 缓冲（vim 命名：bnext / bprev / bdelete）
  ['marks', '标记列表（m{a-z} 打 / `{a-z} 跳）'],
  ['bnext', '下一个缓冲（同 ]b）'],
  ['bprev', '上一个缓冲（同 [b）'],
  ['bfirst', '第一个缓冲'],
  ['blast', '最后一个缓冲'],
  ['bd bdelete', '关闭当前缓冲（含退出该页）'],
  ['buffers', '列出打开的缓冲'],
  // 搜索/替换（vim 的 :noh / :%s 心智）
  ['noh', '清掉搜索高亮'],
  // 视图
  ['only', '只留当前缓冲'],
];

/* 命令表 = 内置 + 插件（lazy.nvim 的 cmd）。插件注册后重建一次。 */
let COMMANDS = BASE_COMMANDS.slice();
function composeCommands() {
  const out = BASE_COMMANDS.slice();
  const seen = new Set(out.map(c => c[0]));
  if (window.KB && typeof window.KB.cmds === 'function') {
    for (const c of window.KB.cmds()) {
      /* 一个插件的 cmd 触发（影子）和它自己注册的命令同名是正常的，
       * 这里去重，:补全表里不该出现两行一样的。 */
      if (!c || !c.name || seen.has(c.name)) continue;
      seen.add(c.name);
      out.push([c.name, c.desc || c.name]);
    }
  }
  return out;
}
function _rebuildCommandTable() {
  COMMANDS = composeCommands();
}

function updateCmdHint() {
  const raw = $('cmdline-input').value;
  if (cmd.prefix === '/') return;
  const word = raw.split(/\s+/)[0];
  if (!word) {
    $('cmdline-hint').innerHTML = '命令 · <span class="k">Tab</span> 补全 · <span class="k">Esc</span> 关闭';
    return;
  }
  const matches = COMMANDS.filter(([n]) => n.split(' ')[0].startsWith(word) || n.startsWith(raw)).slice(0, 6);
  $('cmdline-hint').innerHTML = matches.length
    ? matches.map(([n, d]) => `<span class="k">${escapeHtml(n)}</span> ${escapeHtml(d)}`).join(' &nbsp;·&nbsp; ')
    : '未知命令 · <span class="k">:help</span> 查看全部';
}

function submitCmdline() {
  const raw = $('cmdline-input').value.trim();
  const prefix = cmd.prefix;
  closeCmdline();
  if (!raw) return;
  cmdHistPush(raw);            // 进历史（vim 的行为：执行过就记）
  if (prefix === '/') {
    /* `/` 的语义按上下文分（和 vim 的直觉一致）：
     * - 正在读一篇正文 → 页内搜索（记 pattern，跳第一个命中，之后 n/N）
     * - 在列表页（/all、首页） → 全库搜索（真正换页面）
     * 全库搜索永远可以用 `space s` 或 `:search xxx`，所以正文页里
     * 让出 `/` 给页内搜索不会丢功能。 */
    if (!isListView() && state.current && state.viewKind === 'page') {
      state.lastPattern = raw;
      const n = buildPageHits(raw);
      if (!n) { toast(`本页找不到「${raw}」`); return; }
      pageHitJump(1);
      toast(`页内搜索「${raw}」· ${n} 处 · n / N 跳转`);
      return;
    }
    navigate('search', raw, { replace: true });
    return;
  }
  const parts = raw.split(/\s+/);
  runCommand(parts[0], parts.slice(1));
}

function runCommand(name, args) {
  args = args || [];
  const arg = args.join(' ');
  switch (name) {
    case 'w': case 'write': return state.editing ? saveEditor() : toast('不在编辑状态');
    /* :q —— 有编辑器就关编辑器（vim :q 关窗口）；
     * 阅读态没有「窗口」概念，等同 :bd：关掉当前这一页并切走。
     * 之前这里直接 toast「不在编辑状态」，等于 q 键半残。 */
    case 'q': case 'quit': return state.editing ? closeEditor() : closeCurrentBuffer();
    case 'q!': case 'qall':
      state.editing = false; editorDirty = false; return navigate('page', state.current || '');
    case 'wq': return state.editing ? saveEditor().then(closeEditor) : toast('不在编辑状态');
    case 'x':
      if (!state.editing) return toast('不在编辑状态');
      return editorDirty ? saveEditor().then(closeEditor) : closeEditor();
    case 'e': case 'edit':
      if (!arg) return state.current ? openEditor(state.current, null, ++navToken) : toast('需要页面名');
      return openEditor(arg, null, ++navToken);
    case 'new': return openEditor('', '', ++navToken);
    case 'find': return openPicker('跳转页面', 'f', arg, 'page');
    case 'rename': return arg ? renameCurrent(arg) : pickRename();
    case 'del': return deleteCurrent();
    case 'versions': return openVersions();
    case 'preview': return togglePreview();
    case 'all': return navigate('all');
    case 'home': return navigate('home');
    case 'graph': return navigate('graph');
    case 'todo': return navigate('todo');
    case 'journal': return navigate('journal', arg || undefined);
    case 'tag': return arg ? navigate('tag', arg) : showTagCloud();
    case 'tags': return showTagCloud();
    case 'search': return arg ? navigate('search', arg) : openCmdline('/');
    case 'theme': return toggleTheme();
    case 'colorscheme': case 'colo': return colorscheme(arg);
    case 'mobile': case 'mobile!':
      forceTouch = !forceTouch;
      applyInputMode();
      toast('移动端预览 ' + (forceTouch ? '开' : '关'));
      return;
    case 'vimui': return setInputMode('vim');
    case 'touchui': return setInputMode('touch');
    case 'autoinput': return setInputMode('auto');
    case 'settings': return openSettings();
    case 'set': return applySetCommand(args);
    case 'export': return exportData(arg || 'zip');
    case 'import': return $('import-file').click();
    case 'trash': return openTrash();
    case 'help': case 'keys': return openHelp();
    case 'bnext': return cycleBuffer(1);
    case 'bprev': return cycleBuffer(-1);
    case 'bfirst': return state.buffers.length ? navigate('page', state.buffers[0]) : toast('没有打开的缓冲');
    case 'blast': return state.buffers.length ? navigate('page', state.buffers[state.buffers.length - 1]) : toast('没有打开的缓冲');
    case 'marks': return listMarks();
    case 'bd': case 'bdelete': return closeCurrentBuffer();
    case 'buffers':
      toast('缓冲 ' + state.buffers.length + ' 个：' + (state.buffers.join(' · ') || '(空)'));
      return;
    case 'only': return closeOtherBuffers();
    case 'noh': {
      if (cm) { cm.setOption('highlightSelectionMatches', null); }
      toast('已清除搜索高亮');
      return;
    }
    case 'draft': return restoreDraft();
    case 'fresh': return dropDraft();
    case 'reload': {
      invalidateCache();
      if (state.editing) openEditor(state.current, null, ++navToken);
      else if (state.current) navigate('page', state.current);
      else navigate('home');
      return;
    }
    default: {
      // 插件命令（lazy.nvim 的 cmd）
      const pc = (window.KB && window.KB.isLoaded) ? window.KB.cmds().find(c => c.name === name) : null;
      if (pc && pc.run) return pc.run(args, arg);
      return toast('未知命令 :' + name + ' — :help 查看', 'error');
    }
  }
}

function applySetCommand(args) {
  const key = args[0], val = args.slice(1).join(' ');
  if (key === 'taskpage') {
    if (!val) return toast('用法 :set taskpage <页面>');
    localStorage.setItem('kb-task-page', val);
    return toast('任务来源页 = ' + val);
  }
  if (key === 'dashboard') {
    localStorage.setItem('kb-dashboard-page', val);
    return toast('首页看板页 = ' + (val || '(空)'));
  }
  return toast('未知设置项 · :set taskpage|dashboard');
}

function restoreDraft() {
  if (!state.editing) return toast('不在编辑状态');
  try {
    const d = JSON.parse(localStorage.getItem(draftKey(state.current)) || 'null');
    if (!d) return toast('没有草稿');
    cm.setValue(d.content);
    editorDirty = true;
    setStatusName(state.current, true);
    toast('已恢复草稿');
  } catch (e) { toast('草稿损坏', 'error'); }
}

function dropDraft() {
  draftClear();
  toast('已丢弃草稿');
  if (state.editing) openEditor(state.current, null, ++navToken);
}

/* ══════════════════════════════════════════════════════════
   which-key（<space> 快捷树）
   ══════════════════════════════════════════════════════════ */

/* ── which-key：声明式 spec ──────────────────────────────
 *
 * 结构照 which-key.nvim / LazyVim 的约定，不自创嵌套对象：
 *   - lhs 是完整键序列字符串（'f' → space f，'ff' → space f f），
 *     前缀由字符串本身表达，树只是它的投影
 *   - group 给前缀一个分组名（显示时前缀一个 +，可继续下钻）
 *   - desc + run 是叶子；同一份数据同时驱动快捷键与帮助页
 *   - 排序按 which-key 默认：字母序在前的叶子先，分组排后面
 *   - 键位命名沿用 LazyVim：f=查找 s=搜索 b=缓冲 g=跳转 t=任务
 *     u=界面开关 q=退出 h=帮助 n=新建 e=编辑 w=保存 v=版本 r=重命名
 *     x=删除 y=回收站 o=导出 d=首页 j=日记
 */

const BASE_LEADER_SPEC = [
  /* 分组（前缀） */
  { lhs: 'f', group: '查找' },
  { lhs: 's', group: '搜索' },
  { lhs: 'b', group: '缓冲' },
  { lhs: 'u', group: '界面' },
  { lhs: 'g', group: '跳转' },
  { lhs: 'q', group: '退出' },

  /* f — 查找 */
  { lhs: 'ff', desc: '跳转页面', run: () => openPicker('跳转页面', 'f', '', 'page') },
  /* gf —— 跟到视口里最靠上的链接（vim 的 gf）。gF 是「跟到最后一个」。 */
  { lhs: 'gf', desc: '跟到本页的链接 (gf)', run: () => followLinkUnderCursor() },
  { lhs: 'fr', desc: '最近打开的页面', run: () => pickRecent() },
  { lhs: 'fb', desc: '切换缓冲', run: () => pickBuffer() },
  { lhs: 'ft', desc: '标签云', run: () => showTagCloud() },

  /* s — 搜索 */
  { lhs: 'sg', desc: '全文搜索', run: () => openCmdline('/') },

  /* b — 缓冲 */
  { lhs: 'bd', desc: '关闭当前缓冲（含退出该页）', run: () => closeCurrentBuffer() },
  { lhs: 'bo', desc: '关闭其他缓冲', run: () => closeOtherBuffers() },
  { lhs: 'ba', desc: '关闭全部缓冲', run: () => closeAllBuffers() },

  /* u — 界面开关 */
  { lhs: 'uc', desc: '切配色 (:colorscheme)', run: () => toggleTheme() },
  { lhs: 'un', desc: '切换行号', run: () => toggleLineNumbers() },
  { lhs: 'uw', desc: '切换软换行', run: () => toggleLineWrapping() },

  /* g — 跳转视图 */
  /* g o / g i —— 与 Ctrl-o / Ctrl-i 同一动作（vim 里 jumplist 常挂 g 前缀）。
   * 上一版这里误写成 `bo`，撞上了缓冲组的 `bo`（关闭其他缓冲）——
   * 同一个 lhs 两条定义，后写的覆盖前写的，于是 space b o 变成了跳转。 */
  { lhs: 'go', desc: '← 上一跳转（Ctrl-o）', run: () => jumpTo(-1) },
  { lhs: 'gi', desc: '→ 下一跳转（Ctrl-i）', run: () => jumpTo(1) },
  { lhs: 'gg', desc: '知识图谱', run: () => navigate('graph') },
  { lhs: 'gd', desc: '首页看板', run: () => navigate('home') },
  { lhs: 'ga', desc: '所有页面', run: () => navigate('all') },
  { lhs: 'gt', desc: '任务', run: () => navigate('todo') },

  /* q — 退出 */
  { lhs: 'qq', desc: '退出编辑器', run: () => state.editing ? closeEditor() : toast('不在编辑状态') },
  { lhs: 'qd', desc: '丢弃改动并退出', run: () => discardAndClose() },

  /* 其余全局视图（之前只有 :命令，没有 leader 键 —— which-key 里看不见） */
  { lhs: 'a', desc: '所有页面 (:all)', run: () => navigate('all') },
  { lhs: 'd', desc: '首页看板 (:home)', run: () => navigate('home') },
  { lhs: 't', group: '任务/标签' },
  { lhs: 'tt', desc: '任务列表 (:todo)', run: () => navigate('todo') },
  { lhs: 'tl', desc: '标签云 (:tags)', run: () => showTagCloud() },
  { lhs: 'c', group: '配色' },
  { lhs: 'cc', desc: '切换配色 (:colorscheme)', run: () => toggleTheme() },
  { lhs: 'K', desc: '按键说明 (:keys)', run: () => openHelp() },
  /* 标记列表挂 g 组（space g m）。不能挂裸 `M` —— H/M/L 已经占了
   * 裸 M（视口到屏幕中部），which-key 里的 M 会与它语义打架；
   * 也不能挂 `mr` —— `m` 是「移动端」叶子，同一前缀不能既叶子又分组。 */
  { lhs: 'gm', desc: '标记列表 (:marks)', run: () => listMarks() },
  { lhs: 'm', desc: '移动端 / 触摸 UI (:touchui)', run: () => runCommand('touchui') },
  { lhs: 'D', desc: '草稿 (:draft)', run: () => runCommand('draft') },

  /* 顶层叶子 */
  { lhs: 'h', desc: '快捷键帮助', run: () => openHelp() },
  { lhs: 'n', desc: '新建页面', run: () => openEditor('', '', ++navToken) },
  { lhs: 'e', desc: '编辑当前页', run: () => state.current ? openEditor(state.current, null, ++navToken) : toast('没有当前页面') },
  { lhs: 'w', desc: '保存', run: () => state.editing ? saveEditor() : toast('不在编辑状态') },
  { lhs: 'p', desc: '切换预览', run: () => state.editing ? togglePreview() : toast('不在编辑状态') },
  { lhs: 'v', desc: '版本历史', run: () => openVersions() },
  { lhs: 'r', desc: '重命名当前页', run: () => pickRename() },
  { lhs: 'x', desc: '删除当前页', run: () => deleteCurrent() },
  { lhs: 'y', desc: '回收站', run: () => openTrash() },
  { lhs: 'o', desc: '导出备份', run: () => exportData('zip') },
  { lhs: 'j', desc: '今日日记', run: () => navigate('journal') },
  { lhs: 'J', desc: '指定日期日记', run: () => promptText('日记日期', 'YYYY-MM-DD', todayStr()).then(d => { if (d) navigate('journal', d); }) },
  { lhs: 'T', desc: '设置', run: () => openSettings() },
  { lhs: 'R', desc: '重新加载（清缓存）', run: () => { invalidateCache(); navigate(state.current ? 'page' : 'home', state.current || undefined); } },
];

/* lhs → 树。一个节点要么有 children（分组），要么有 run（叶子）。
 * 插件注册键位后会重建一次（见 rebuildLeaderTree），所以这里是 let + 函数。 */
function buildLeaderTree(spec) {
  const root = { children: new Map() };
  for (const item of spec) {
    let node = root;
    for (const ch of item.lhs) {
      if (!node.children.has(ch)) node.children.set(ch, { key: ch, children: new Map() });
      node = node.children.get(ch);
    }
    if (item.group) node.group = item.group;
    if (item.desc) node.desc = item.desc;
    if (item.run) node.run = item.run;
    if (item.plugin) node.plugin = item.plugin;
  }
  return root;
}
/* 插件注册的键位并进来，所以 LEADER_SPEC 是「内置 + 插件」的合成视图。
 * 这个合成正是 lazy.nvim 的 keys 机制：插件在自己的 spec 里写 keys，
 * 用户感知不到它来自哪个插件，which-key 里看到的是一张统一的表。 */
function composeLeaderSpec() {
  const out = BASE_LEADER_SPEC.slice();
  if (window.KB && typeof window.KB.keys === 'function') {
    for (const k of window.KB.keys()) {
      if (!k || !k.lhs) continue;
      out.push({ lhs: k.lhs, desc: k.desc, group: k.group, run: k.run, plugin: true });
    }
  }
  /* 按 lhs 去重 —— 同一个键只能有一条定义，否则 which-key 里出现两行、
   * 帮助页重复、后写的还会静默覆盖前写的（这正是 space b o 一度
   * 变成「上一跳转」的原因）。
   *
   * 重复从哪来：插件可以「两处都声明」——spec 里写 keys（这样懒加载
   * 前 which-key 就能看到键名），插件文件里再 api.keys() 一次
   * （这样 disable 时能干净收回）。两处本来就该都有，只是合成时要去重。
   *
   * 合并规则：后写的 desc 覆盖前者，但 run 一旦有就不许被空值覆盖
   * （spec 里通常只有 desc，没有 run）。 */
  const byLhs = new Map();
  for (const item of out) {
    const prev = byLhs.get(item.lhs);
    if (!prev) { byLhs.set(item.lhs, Object.assign({}, item)); continue; }
    const merged = Object.assign({}, prev);
    if (item.desc) merged.desc = item.desc;
    if (item.group) merged.group = item.group;
    if (item.run) merged.run = item.run;
    if (item.plugin) merged.plugin = true;
    byLhs.set(item.lhs, merged);
  }
  return [...byLhs.values()];
}

let LEADER_SPEC = composeLeaderSpec();
let LEADER_TREE = buildLeaderTree(LEADER_SPEC);

let wkPath = [];      // 已按下的键序列
let wkSel = 0;        // 当前高亮项
let wkPrefix = '';
let wkRows = [];

function wkNode() {
  let node = LEADER_TREE;
  for (const ch of wkPath) node = node.children.get(ch) || { children: new Map() };
  return node;
}

/* which-key 默认排序：叶子按字母序，分组排后面 */
function wkEntries(node) {
  const kids = [...node.children.values()];
  const leaves = kids.filter(n => n.run);
  const groups = kids.filter(n => !n.run && n.children.size);
  const cmp = (a, b) => a.key.localeCompare(b.key);
  return [...leaves.sort(cmp), ...groups.sort(cmp)];
}

function openWhichKey() {
  wkPath = [];
  wkSel = 0;
  state.leaderOpen = true;
  $('whichkey').classList.add('open');
  renderWhichKey();
}

function closeWhichKey() {
  $('whichkey').classList.remove('open');
  state.leaderOpen = false;
  wkPath = [];
  wkPrefix = '';
}

function renderWhichKey() {
  const node = wkNode();
  const entries = wkEntries(node);
  wkRows = entries;
  wkPrefix = wkPath.join('');

  const head = `<div class="wk-head"><span class="wk-prefix">space${wkPrefix ? ' ' + wkPrefix : ''}</span>
    <span>${wkPath.length ? '继续输入 · Esc 取消' : '选择命令 · Esc 取消'}</span></div>`;

  $('whichkey').innerHTML = head + `<div class="wk-grid">` + entries.map((n, i) => {
    const isGroup = !n.run;
    const label = isGroup ? n.group : n.desc;
    return `<div class="wk-row${i === wkSel ? ' sel' : ''}${isGroup ? ' group' : ''}" data-k="${escapeHtml(n.key)}">
      <span class="wk-key">${escapeHtml(n.key)}</span>
      <span class="wk-label">${isGroup ? escapeHtml(label) + ' +' : escapeHtml(label)}</span></div>`;
  }).join('') + `</div>`;

  $('whichkey').querySelectorAll('.wk-row').forEach(row => {
    row.addEventListener('click', () => wkPress(row.dataset.k));
  });
}

/* 按下一个键：分组则下钻，叶子则执行。都通过这里，鼠标点击走同一条路径。 */
function wkPress(k) {
  const node = wkNode();
  const next = node.children.get(k);
  if (!next) { closeWhichKey(); return false; }
  if (next.run) {
    closeWhichKey();
    try { next.run(); } catch (e) { toast(String(e.message || e), 'error'); }
    return true;
  }
  wkPath.push(k);
  wkSel = 0;
  renderWhichKey();
  return true;
}

/* 帮助页直接从 spec 生成，避免两边手抄不同步 */
/* 插件段落也是从注册表现算的：装了新插件，帮助页自动多一行，
 * 与 which-key 用的是同一份 spec，不会漂移。 */
function pluginHelpSection() {
  if (!window.KB || !window.KB.list) return '';
  const rows = window.KB.list()
    .filter(p => p.enabled)
    .map(p => {
      const bits = [];
      if (p.triggers && (p.triggers.keys || []).length) bits.push('space ' + p.triggers.keys.join(' / space '));
      if (p.triggers && (p.triggers.cmd || []).length) bits.push(':' + p.triggers.cmd.join(' / :'));
      if (p.routes && p.routes.length) bits.push('/' + p.routes.join(' / /'));
      return [(bits.join('  ') || '启动即载'), p.desc || p.name];
    });
  if (!rows.length) return '';
  return `<div class="help-sec"><h4>插件（:Plugins 管理）</h4>` +
    rows.map(([k, d]) => `<div class="help-row"><span class="help-key">${escapeHtml(k)}</span><span class="help-desc">${escapeHtml(d)}</span></div>`).join('') +
    `</div>`;
}

function leaderHelpRows() {
  const rows = [];
  const walk = (node, path) => {
    for (const n of wkEntries(node)) {
      const seq = path + n.key;
      if (n.run) rows.push(['space ' + seq, n.desc]);
      else { rows.push(['space ' + seq, n.group + ' +']); walk(n, seq); }
    }
  };
  walk(LEADER_TREE, '');
  return rows;
}

/* ══════════════════════════════════════════════════════════
   选择器（fuzzy picker）
   ══════════════════════════════════════════════════════════ */

let pickItems = [], pickMode = 'page', pickActive = 0, pickOnPick = null;

function fuzzyScore(q, text) {
  if (!q) return 1;
  const ql = q.toLowerCase(), t = text.toLowerCase();
  const idx = t.indexOf(ql);
  if (idx >= 0) return 1000 - idx + (t.length - ql.length > 0 ? 0 : 0);
  let qi = 0, score = 0;
  for (let ti = 0; ti < t.length && qi < ql.length; ti++) {
    if (t[ti] === ql[qi]) { qi++; score++; }
  }
  return qi === ql.length ? score : -1;
}

async function openPicker(title, prompt, initial, mode, onPick) {
  pickMode = mode || 'page';
  pickOnPick = onPick || null;
  $('pick-title').textContent = title;
  $('pick-prompt').textContent = prompt;
  const input = $('pick-input');
  input.value = initial || '';
  $('pick-list').innerHTML = '<div class="empty">加载中…</div>';
  openOverlay('ov-pick');
  setTimeout(() => { input.focus(); input.select(); }, 30);

  if (pickMode === 'page') {
    try {
      const names = await ensureNames();
      pickItems = names.map(n => ({ label: n, value: n }));
      renderPick(input.value);
    } catch (e) { $('pick-list').innerHTML = `<div class="empty">${escapeHtml(e.message)}</div>`; }
  }
}

function renderPick(q) {
  const scored = pickItems
    .map(it => ({ it, s: fuzzyScore(q, it.label) }))
    .filter(x => x.s > 0)
    .sort((a, b) => b.s - a.s)
    .slice(0, 100);
  if (!scored.length) { $('pick-list').innerHTML = '<div class="empty">没有匹配</div>'; return; }
  pickActive = Math.min(pickActive, scored.length - 1);
  if (pickActive < 0) pickActive = 0;
  $('pick-list').innerHTML = scored.map((x, i) =>
    `<div class="row${i === pickActive ? ' kb-sel' : ''}" data-pick="${escapeHtml(x.it.value)}" data-i="${i}">
      <span class="row-name">${escapeHtml(x.it.label)}</span></div>`).join('');
  $('pick-list').querySelectorAll('.row').forEach(r => {
    r.addEventListener('click', () => doPick(r.dataset.pick));
  });
}

function movePick(dir) {
  const rows = $('pick-list').querySelectorAll('.row');
  if (!rows.length) return;
  pickActive = Math.max(0, Math.min(rows.length - 1, pickActive + dir));
  rows.forEach((r, i) => r.classList.toggle('kb-sel', i === pickActive));
  rows[pickActive].scrollIntoView({ block: 'nearest' });
}

function doPick(value) {
  closeOverlays();
  if (pickOnPick) { const f = pickOnPick; pickOnPick = null; return f(value); }
  if (pickMode === 'page') return navigate(value === '' ? 'new' : 'page', value || undefined);
  if (pickMode === 'date') return navigate('journal', value);
}

/* ══════════════════════════════════════════════════════════
   覆盖层
   ══════════════════════════════════════════════════════════ */

function openOverlay(id) {
  closeOverlays(true);
  $(id).classList.add('open');
}

function closeOverlays(keepOthers) {
  if (!keepOthers) {
    document.querySelectorAll('.overlay.open').forEach(o => o.classList.remove('open'));
  }
}

function closeAllTransient() {
  closeWhichKey();
  closeOverlays();
  hideLp();
  if (cmd.open) closeCmdline();
}

function openHelp() {
  const sec = (t, rows) => `<div class="help-sec"><h4>${t}</h4>` +
    rows.map(([k, d]) => `<div class="help-row"><span class="help-key">${escapeHtml(k)}</span><span class="help-desc">${escapeHtml(d)}</span></div>`).join('') + `</div>`;
  $('help-body').innerHTML =
    sec('全局（任何视图）', [
      ['space', '打开快捷键提示（which-key）'],
      [':', '命令行（:help 查看全部命令）'],
      ['/', '全文搜索'],
      ['Esc', '关闭浮层'],
      ['Ctrl+S', '保存（编辑中）'],
    ]) +
    sec('space 快捷键树', leaderHelpRows()) +
    sec('编辑器（vim）', [
      ['i / a / o / O', '进入插入模式'],
      ['Esc', '回到普通模式'],
      ['dd / yy / cc', '删行 / 复制行 / 改行'],
      ['/word', '编辑器内搜索'],
      [':w  :q  :wq  :x', '保存 / 退出 / 保存退出'],
      [':e <页>  :find <词>', '打开别的页面'],
      ['u / Ctrl+R', '撤销 / 重做'],
      ['gg / G / 0 / $', '跳文件头尾 / 行首尾'],
      [']b / [b', '下一个 / 上一个缓冲（vim 惯例）'],
    ]) +
    sec('浏览（非编辑器）', [
      ['j / k  ↓ / ↑', '上下滚动'],
      ['dd / uu', '翻一屏 / 回一屏（连按两次，懒人版 Ctrl-D）'],
      ['gg / G', '顶/底（列表里 G 到末行）'],
      ['Ctrl-d / Ctrl-u', '半屏下 / 上（3<C-d> = 3 行）'],
      ['Ctrl-f / Ctrl-b', '整屏下 / 上'],
      ['H / M / L', '视口到窗口首 / 中 / 尾'],
      ['n / N', '页内搜索下一个 / 上一个命中'],
      ['Ctrl-o / Ctrl-i', '跳转历史 上一跳 / 下一跳'],
      ['3j / 5G', '计数前缀：数字 + 动作'],
      ['m{a-z} / `{a-z}', '打标记 / 跳回标记（跨会话保留）'],
      ['space g f', '跟到本页的链接（gf）'],
      [']b / [b', '下一个 / 上一个缓冲'],
      ['q', '关闭当前缓冲'],
      ['R', '清缓存并回首页'],
    ]) +
    sec('命令行常用', [
      [':find <词>', '模糊跳转'],
      [':bnext  :bprev  :buffers', '缓冲循环 / 列出（同 ]b [b）'],
      [':todo  :graph  :all  :home', '切视图'],
      [':new  :e <页>  :rename <新名>', '写'],
      [':versions  :trash  :settings', '历史 / 回收站 / 设置'],
      [':set taskpage <页>', '换任务来源页'],
      [':export zip|json', '导出备份'],
    ]) +
    sec('移动端 / 触屏', [
      ['右下 ＋', '动作坞：按上下文列可用动作（同一张能力表）'],
      ['点状态栏 NORMAL', '同动作坞入口'],
      ['左右滑', '下一个 / 上一个缓冲（同 ]b [b）'],
      ['tabline 摘要条', '点一下展开全部缓冲'],
      [':mobile! 或 :touchui', '在电脑上强制移动端布局预览'],
      [':vimui 或 :autoinput', '强制 vim 键位 / 按设备自动'],
    ]) +
    (pluginHelpSection()) +
    sec('鼠标', [
      ['点击 wikilink', '跳转；悬停 300ms 显示预览'],
      ['点击 #tag', '按标签浏览'],
      ['tabline', '切换已打开页面，× 关闭'],
    ]);
  openOverlay('ov-help');
}

let versionTarget = null, versionsCache = [], selectedVersion = null;

async function openVersions(target) {
  const name = target || state.current;
  if (!name) return toast('没有当前页面');
  versionTarget = name;
  selectedVersion = null;
  $('ov-versions-title').textContent = `版本历史 · ${name}`;
  $('vlist').innerHTML = '<div class="empty">加载中…</div>';
  $('vpreview').style.display = 'none';
  $('vrestore').disabled = true;
  $('vnote').textContent = '';
  openOverlay('ov-versions');
  try {
    versionsCache = await jfetch(`${API}/pages/${encodeURIComponent(name)}/versions`);
    if (!versionsCache.length) {
      $('vlist').innerHTML = '<div class="empty">还没有历史版本</div>';
      return;
    }
    $('vlist').innerHTML = versionsCache.map(v =>
      `<div class="vrow" data-vid="${v.id}"><span class="vdate">${escapeHtml(v.created_at)}</span>
        <span class="vsize">${v.size} 字节</span></div>`).join('');
    $('vlist').querySelectorAll('.vrow').forEach(r => {
      r.addEventListener('click', () => selectVersion(parseInt(r.dataset.vid, 10)));
    });
    $('vnote').textContent = `共 ${versionsCache.length} 个快照（最多显示最近 100 个）`;
  } catch (e) {
    $('vlist').innerHTML = `<div class="empty">${escapeHtml(e.message)}</div>`;
  }
}

async function selectVersion(vid) {
  selectedVersion = vid;
  $('vlist').querySelectorAll('.vrow').forEach(r => r.classList.toggle('sel', +r.dataset.vid === vid));
  $('vrestore').disabled = false;
  $('vpreview').style.display = 'block';
  $('vpreview').innerHTML = '<div class="sl-hint">加载中…</div>';
  try {
    const v = await jfetch(`${API}/pages/${encodeURIComponent(versionTarget)}/versions/${vid}`);
    $('vpreview').innerHTML = `<div class="doc">${renderMd(v.content)}</div>`;
    renderMermaid($('vpreview'));
  } catch (e) {
    $('vpreview').innerHTML = `<div class="sl-hint">${escapeHtml(e.message)}</div>`;
  }
}

async function restoreVersion() {
  if (!selectedVersion || !versionTarget) return;
  const ok = await confirmText(`恢复到版本 #${selectedVersion}？`, '当前内容会先存为新快照');
  if (!ok) return;
  try {
    await jfetch(`${API}/pages/${encodeURIComponent(versionTarget)}/versions/${selectedVersion}/restore`, { method: 'POST' });
    invalidateCache();
    toast('已恢复');
    closeOverlays();
    if (state.editing) openEditor(state.current, null, ++navToken);
    else navigate('page', versionTarget);
  } catch (e) { toast('恢复失败：' + e.message, 'error'); }
}

async function purgeVersions() {
  if (!versionTarget) return;
  const ok = await confirmText(`清理「${versionTarget}」的全部历史？`, '不可撤销');
  if (!ok) return;
  try {
    const r = await jfetch(`${API}/pages/${encodeURIComponent(versionTarget)}/versions`, { method: 'DELETE' });
    toast(`已清理 ${r.purged} 个快照`);
    openVersions();
  } catch (e) { toast('清理失败：' + e.message, 'error'); }
}

function openSettings() {
  $('set-taskpage').value = getSetting('kb-task-page', DEFAULT_TASK_PAGES);
  $('set-dashboard').value = getSetting('kb-dashboard-page', '');
  $('set-css').value = '';
  openOverlay('ov-settings');
  fetch(`${API}/custom.css`, { cache: 'no-store' })
    .then(r => r.ok ? r.text() : '')
    .then(t => { $('set-css').value = t; })
    .catch(() => {});
}

async function saveSettings() {
  localStorage.setItem('kb-task-page', $('set-taskpage').value.trim() || DEFAULT_TASK_PAGES);
  localStorage.setItem('kb-dashboard-page', $('set-dashboard').value.trim());
  try {
    await fetch(`${API}/custom.css`, {
      method: 'PUT', headers: { 'Content-Type': 'text/plain' },
      body: $('set-css').value,
    });
  } catch (e) { toast('自定义 CSS 保存失败', 'error'); }
  applyCustomCSS($('set-css').value);
  invalidateCache();
  toast('设置已保存');
  closeOverlays();
}

function applyCustomCSS(css) {
  let el = $('custom-css');
  if (!el) { el = document.createElement('style'); el.id = 'custom-css'; document.head.appendChild(el); }
  el.textContent = css || '';
}

async function loadCustomCSS() {
  try {
    const r = await fetch(`${API}/custom.css`, { cache: 'no-store' });
    if (r.ok) applyCustomCSS(await r.text());
  } catch (e) {}
}

async function openTrash() {
  openOverlay('ov-trash');
  $('trash-list').innerHTML = '<div class="empty">加载中…</div>';
  try {
    const rows = await jfetch(`${API}/trash`);
    if (!rows.length) { $('trash-list').innerHTML = '<div class="empty">回收站是空的</div>'; return; }
    $('trash-list').innerHTML = rows.map(r =>
      `<div class="row" data-restore="${escapeHtml(r.page_name)}" data-vid="${r.latest_version_id}">
        <span class="row-name">${escapeHtml(r.page_name)}</span>
        <span class="row-date">${r.version_count} 版本 · ${escapeHtml(fmtDate(r.last_modified))}</span>
        <span class="row-badge sl-hint">恢复</span></div>`).join('');
    $('trash-list').querySelectorAll('.row').forEach(row => {
      row.addEventListener('click', () => restoreFromTrash(row.dataset.restore, parseInt(row.dataset.vid, 10)));
    });
  } catch (e) {
    $('trash-list').innerHTML = `<div class="empty">${escapeHtml(e.message)}</div>`;
  }
}

async function restoreFromTrash(name, vid) {
  try {
    await jfetch(`${API}/pages/${encodeURIComponent(name)}/versions/${vid}/restore`, { method: 'POST' });
    invalidateCache();
    toast(`已恢复「${name}」`);
    closeOverlays();
    navigate('page', name);
  } catch (e) { toast('恢复失败：' + e.message, 'error'); }
}

async function showTagCloud() {
  state.current = null;
  state.editing = false;
  setStatusName(null);
  try {
    const tags = await getTags();
    showView(`<div class="list-wrap"><div class="list-head"><h1>标签</h1>
      <span class="count">${tags.length} 个</span></div>
      <div class="tagcloud">${tags.map(t =>
        `<span class="chip" data-tag="${escapeHtml(t.name)}">#${escapeHtml(t.name)}<span class="n"> ${t.count}</span></span>`
      ).join('')}</div></div>`);
  } catch (e) {
    showView(`<div class="empty">${escapeHtml(e.message)}</div>`);
  }
}

function pickRename() {
  if (!state.current) return toast('没有当前页面');
  const old = state.current;
  promptText('重命名', '新名字', old).then(n => { if (n && n !== old) renameCurrent(n); });
}

/* 简单输入对话框：复用 picker 的样式，避免 window.prompt 的丑样式 */
function promptText(title, placeholder, dflt) {
  return new Promise(resolve => {
    $('pick-title').textContent = title;
    $('pick-prompt').textContent = '>';
    const input = $('pick-input');
    input.value = dflt || '';
    $('pick-list').innerHTML = `<div class="sl-hint" style="padding:.4em .2em">${escapeHtml(placeholder || '')}</div>`;
    pickOnPick = null;
    pickMode = 'prompt';
    openOverlay('ov-pick');
    setTimeout(() => input.focus(), 30);
    const handler = (e) => {
      if (e.key === 'Enter') {
        const v = input.value.trim();
        cleanup();
        closeOverlays();
        resolve(v);
      } else if (e.key === 'Escape') {
        cleanup();
        closeOverlays();
        resolve(null);
      }
    };
    const cleanup = () => input.removeEventListener('keydown', handler);
    input.addEventListener('keydown', handler);
  });
}

/* promptChoice：vim 里 :qa 那种「有 N 个缓冲区被修改」的三选一。
 * 返回选中项的 key（'w'/'d'/'c'），Esc 或关窗返回 null。
 * 复用 ov-pick，键盘/鼠标同一路径。 */
function promptChoice(title, desc, options) {
  return new Promise(resolve => {
    $('pick-title').textContent = title;
    $('pick-prompt').textContent = '>';
    const input = $('pick-input');
    input.value = '';
    input.setAttribute('readonly', 'readonly');
    pickMode = 'choice';
    pickOnPick = null;
    pickItems = options.map(o => ({ label: o.label, value: o.key }));
    pickActive = 0;
    renderChoice('');

    function renderChoice(q) {
      $('pick-list').innerHTML =
        (desc ? `<div class="empty" style="padding:.6em 0 .2em"><div>${escapeHtml(desc)}</div></div>` : '') +
        options.map((o, i) => `<div class="row${i === pickActive ? ' kb-sel' : ''}" data-pick="${escapeHtml(o.key)}">
          <span class="row-name">${escapeHtml(o.label)}</span></div>`).join('');
      $('pick-list').querySelectorAll('.row').forEach(r => {
        r.addEventListener('click', () => finish(r.dataset.pick));
      });
    }

    openOverlay('ov-pick');
    setTimeout(() => input.focus(), 30);

    const handler = (e) => {
      if (e.key === 'Escape') { e.preventDefault(); finish(null); return; }
      if (e.key === 'ArrowDown') { e.preventDefault(); pickActive = Math.min(options.length - 1, pickActive + 1); renderChoice(''); return; }
      if (e.key === 'ArrowUp') { e.preventDefault(); pickActive = Math.max(0, pickActive - 1); renderChoice(''); return; }
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        finish(options[pickActive] ? options[pickActive].key : null);
        return;
      }
      // 直接按选项首字母
      const hit = options.find(o => o.key.toLowerCase() === e.key.toLowerCase());
      if (hit) { e.preventDefault(); finish(hit.key); }
    };
    const finish = (key) => {
      cleanup();
      input.removeAttribute('readonly');
      closeOverlays();
      resolve(key);
    };
    const cleanup = () => input.removeEventListener('keydown', handler);
    input.addEventListener('keydown', handler);
  });
}

function confirmText(title, desc) {
  return new Promise(resolve => {
    $('pick-title').textContent = title;
    $('pick-prompt').textContent = '>';
    $('pick-input').value = '';
    $('pick-list').innerHTML = `<div class="empty" style="padding:1.2em 0">
      <div>${escapeHtml(desc || '')}</div>
      <div class="hintline">输入 <b>y</b> + Enter 确认 · Esc 取消</div></div>`;
    pickMode = 'confirm';
    pickOnPick = null;
    openOverlay('ov-pick');
    setTimeout(() => $('pick-input').focus(), 30);
    const handler = (e) => {
      if (e.key === 'Enter') {
        const v = $('pick-input').value.trim().toLowerCase();
        cleanup(); closeOverlays(); resolve(v === 'y' || v === 'yes');
      } else if (e.key === 'Escape') {
        cleanup(); closeOverlays(); resolve(false);
      }
    };
    const cleanup = () => $('pick-input').removeEventListener('keydown', handler);
    $('pick-input').addEventListener('keydown', handler);
  });
}

/* ── 导入 / 导出 ────────────────────────────────────────── */

function exportData(fmt) {
  window.open(`${API}/export/${fmt === 'json' ? 'json' : 'zip'}`, '_blank');
  toast('导出开始');
}

async function importFiles(files) {
  if (!files || !files.length) return;
  const fd = new FormData();
  fd.append('file', files[0]);
  fd.append('conflict', 'skip');
  try {
    const r = await jfetch(`${API}/import`, { method: 'POST', body: fd });
    invalidateCache();
    toast(`导入完毕：新增 ${r.created} / 跳过 ${r.skipped}`);
    updatePageCount();
  } catch (e) { toast('导入失败：' + e.message, 'error'); }
}

/* ══════════════════════════════════════════════════════════
   链接悬浮预览
   ══════════════════════════════════════════════════════════ */

const lp = { el: $('lp'), cache: new Map(), timer: null, hideTimer: null, cur: null };

function hideLp() { lp.el.classList.remove('open'); lp.cur = null; }

async function showLp(anchor) {
  const name = anchor.dataset.page;
  if (!name) return;
  lp.cur = anchor;
  let pg = lp.cache.get(name);
  if (pg === undefined) {
    try { pg = await getPage(name); } catch (e) { pg = null; }
    if (lp.cache.size > 24) lp.cache.clear();
    lp.cache.set(name, pg);
  }
  if (lp.cur !== anchor) return;
  if (!pg) {
    lp.el.innerHTML = `<div class="lp-head">${escapeHtml(name)}</div>
      <div class="lp-body"><span class="kb-embed-create" data-create="${escapeHtml(name)}">不存在 — 点击创建</span></div>`;
  } else {
    const preview = pg.content.length > 700
      ? pg.content.slice(0, 700).replace(/\n[^\n]*$/, '') + '\n…'
      : pg.content;
    lp.el.innerHTML = `<div class="lp-head">${escapeHtml(pg.name)}</div>
      <div class="lp-body">${renderMd(preview)}</div>
      <div class="lp-foot">${(pg.backlinks || []).length} 个反向链接 · 更新 ${fmtDate(pg.updated_at)}</div>`;
  }
  positionLp(anchor);
}
function positionLp(anchor) {
  const r = anchor.getBoundingClientRect();
  lp.el.classList.add('open');
  const w = lp.el.offsetWidth, h = lp.el.offsetHeight;
  let x = Math.max(8, Math.min(r.left, window.innerWidth - w - 8));
  let y = r.bottom + 8;
  if (y + h > window.innerHeight - 8) y = Math.max(8, r.top - h - 8);
  lp.el.style.left = x + 'px';
  lp.el.style.top = y + 'px';
}

lp.el.addEventListener('click', (e) => {
  const create = e.target.closest('[data-create]');
  if (create) { const n = create.dataset.create; hideLp(); openEditor(n, '', ++navToken); }
});

let lpTouchTimer = null;

document.addEventListener('mouseover', (e) => {
  const a = e.target.closest('a.wikilink[data-page]');
  if (!a || a.classList.contains('unresolved')) return;
  clearTimeout(lp.timer); clearTimeout(lp.hideTimer);
  lp.timer = setTimeout(() => showLp(a), 320);
});
document.addEventListener('mouseout', (e) => {
  const a = e.target.closest('a.wikilink[data-page]');
  if (!a) return;
  clearTimeout(lp.timer);
  lp.hideTimer = setTimeout(() => { if (!lp.el.matches(':hover')) hideLp(); }, 220);
});

document.addEventListener('click', (e) => {
  const a = e.target.closest('a.wikilink[data-page]');
  const tag = e.target.closest('[data-tag]');
  const create = e.target.closest('[data-create]');

  if (a) {
    e.preventDefault();
    // 移动端：第一次点显示预览，第二次点进入
    if (isMobile() && !a.classList.contains('unresolved')) {
      if (lp.cur === a) { hideLp(); navigate('page', a.dataset.page); }
      else showLp(a);
      return;
    }
    navigate('page', a.dataset.page);
    return;
  }
  if (tag) { e.preventDefault(); navigate('tag', tag.dataset.tag); return; }
  if (create) { e.preventDefault(); const n = create.dataset.create; openEditor(n, '', ++navToken); return; }

  const buf = e.target.closest('[data-buffer]');
  if (buf && !e.target.closest('[data-close]')) {
    const n = buf.dataset.buffer;
    if (state.editing && state.current === n) return;
    navigate('page', n);
    return;
  }
  const closeBtn = e.target.closest('[data-close]');
  if (closeBtn) { e.preventDefault(); closeBuffer(closeBtn.dataset.close); return; }
  const tabFold = e.target.closest('[data-tabfold]');
  if (tabFold) { e.preventDefault(); collapseTabline(); return; }
  const tabExpand = e.target.closest('[data-tabexpand]');
  if (tabExpand) { e.preventDefault(); toggleTablineExpand(); return; }
  const newTab = e.target.closest('[data-newtab]');
  if (newTab) { e.preventDefault(); openEditor('', '', ++navToken); return; }

  const row = e.target.closest('.row[data-page], .bl-item[data-page], .mini li[data-page]');
  if (row && row.dataset.page) { navigate('page', row.dataset.page); return; }

  const chip = e.target.closest('.tagcloud .chip');
  if (chip) { navigate('tag', chip.dataset.tag); return; }

  // 点击空白关掉预览卡
  if (!e.target.closest('#lp')) hideLp();
}, true);

/* ══════════════════════════════════════════════════════════
   输入模式（vim / touch）
   ──────────────────────────────────────────────────────────
   vim 键位不是手机的好输入法：没有物理键盘，长按、组合键、
   连按两次（dd/gg）都别扭。所以把「能力」和「按键」拆开：

     · 技能层 ACTION_SPEC  —— 一份声明式动作表（lhs 仅作提示），
       桌面用 vim 键位/which-key 触发，移动端用按钮/数字盘触发。
       两条输入路径共用同一批 run()，不存在两套逻辑。
     · 输入层 三种模式（参照 LazyVim 的 vim.keymap / editor-only /
       hybrid 三个思路）：
         vim   —— 桌面：不加任何移动端 UI
         touch —— 移动：隐藏 tabline 与 vim 提示，动作坞 + 数字盘，
                  编辑器默认进插入模式（手机上打开就能打字）
         auto  —— 由输入设备决定（有键盘就 vim，只有手指就 touch）
   ══════════════════════════════════════════════════════════ */

const INPUT_MODE_KEY = 'kb-input-mode';

function detectCoarse() {
  try { return window.matchMedia('(pointer: coarse)').matches; } catch (e) { return false; }
}

function inputMode() {
  return getSetting(INPUT_MODE_KEY, 'auto');
}

/* forceTouch 让桌面浏览器也能预览移动端布局（:mobile!），
 * 也照顾「平板 + 键盘」这种不该被 UA 硬绑的人 */
let forceTouch = false;

function effectiveInputMode() {
  if (forceTouch) return 'touch';
  const m = inputMode();
  if (m === 'auto') return detectCoarse() ? 'touch' : 'vim';
  return m;
}

function isTouchUi() { return effectiveInputMode() === 'touch'; }

function setInputMode(m) {
  localStorage.setItem(INPUT_MODE_KEY, m);
  applyInputMode();
  toast('输入模式 = ' + m + '（' + effectiveInputMode() + '）');
}

/* 冻结编辑器在 vim/insert 之间切换：touch 模式默认进插入，能直接打字 */
function setEditorVim(enabled) {
  if (!cm) return;
  const cur = cm.getOption('keyMap');
  const want = enabled ? 'vim' : 'default';
  if (cur !== want) cm.setOption('keyMap', want);
  // 关掉 vim 时状态栏不该继续显示 NORMAL（手机上会误导）
  if (!enabled) setMode('insert');
  else setMode('normal');
}

function applyInputMode() {
  const touch = isTouchUi();
  document.body.classList.toggle('touch-ui', touch);
  const dock = $('fxdock');
  if (dock) {
    dock.classList.toggle('on', touch);
    if (!touch) { dock.classList.remove('open'); }
  }
  const nx = $('numgrid');
  if (nx && !touch) closeNumGrid();
  $('sl-mode').classList.toggle('tappable', touch);
  // touch 下 tabline 变成「可收起的单行」，避免和动作坞抢空间
  if (touch) collapseTabline();
  renderFxPanel();
  if (cm) setEditorVim(!touch);
  // 只关心 pointer 变化时不重跑整页
  bumpTabline();
}

window.addEventListener('resize', () => applyInputMode());

/* touch 模式：tabline 默认收起成一行「3 个缓冲 · 当前」，点一下展开 */
let tablineExpanded = false;

function collapseTabline() {
  tablineExpanded = false;
  const tl = $('tabline');
  if (tl) tl.classList.remove('expanded');
  renderTabline();
}

function toggleTablineExpand() {
  tablineExpanded = !tablineExpanded;
  const tl = $('tabline');
  if (tl) tl.classList.toggle('expanded', tablineExpanded);
  renderTabline();
}

/* ── 能力表：一份声明，两套触发器 ──────────────────────
 * 桌面走 vim 键位 + which-key，移动端从同一张表渲染动作坞按钮。
 * ctx 表示「在哪些上下文里有意义」，用来过滤，而不是复制一份。
 */

const ACTION_SPEC = [
  /* 查找 / 搜索 */
  { lhs: 'ff', ctx: ['page', 'buffer', 'search'], label: '跳转页面', run: () => openPicker('跳转页面', 'f', '', 'page') },
  { lhs: 'sg', ctx: ['page', 'buffer', 'search'], label: '全文搜索', run: () => openCmdline('/') },
  { lhs: 'fr', ctx: ['page', 'buffer'], label: '最近打开', run: () => pickRecent() },
  { lhs: 'fb', ctx: ['page', 'buffer'], label: '切换缓冲', run: () => pickBuffer() },
  /* 缓冲 */
  { lhs: ']b', ctx: ['page', 'buffer'], label: '下一个缓冲', run: () => cycleBuffer(1) },
  { lhs: '[b', ctx: ['page', 'buffer'], label: '上一个缓冲', run: () => cycleBuffer(-1) },
  { lhs: 'bd', ctx: ['page', 'buffer', 'editor'], label: '关闭缓冲', run: () => state.current ? closeBuffer(state.current) : toast('没有当前页面') },
  /* 写 */
  { lhs: 'n', ctx: ['page', 'buffer'], label: '新建页面', run: () => openEditor('', '', ++navToken) },
  { lhs: 'e', ctx: ['page'], label: '编辑当前页', run: () => state.current ? openEditor(state.current, null, ++navToken) : toast('没有当前页面') },
  { lhs: 'w', ctx: ['editor'], label: '保存', run: () => state.editing ? saveEditor() : toast('不在编辑状态') },
  { lhs: 'p', ctx: ['editor'], label: '预览', run: () => state.editing ? togglePreview() : toast('不在编辑状态') },
  { lhs: 'q', ctx: ['editor'], label: '退出编辑', run: () => state.editing ? closeEditor() : toast('不在编辑状态') },
  /* 页面维护 */
  { lhs: 'v', ctx: ['page', 'editor'], label: '版本历史', run: () => openVersions() },
  { lhs: 'r', ctx: ['page'], label: '重命名', run: () => pickRename() },
  { lhs: 'x', ctx: ['page'], label: '删除当前页', run: () => deleteCurrent() },
  { lhs: 'y', ctx: ['page'], label: '回收站', run: () => openTrash() },
  { lhs: 'o', ctx: ['page'], label: '导出备份', run: () => exportData('zip') },
  /* 触屏专用：数字跳转（桌面等价于 gg + 列表序号，手机上一指直达） */
  { lhs: '1-9', ctx: ['page', 'buffer', 'search'], label: '数字跳转', touchOnly: true, run: () => openNumGrid($('view')) },
  /* 全局（任何上下文都留） */
  { lhs: 'T', ctx: ['always'], label: '切换主题', run: () => toggleTheme() },
  { lhs: 's', ctx: ['always'], label: '设置', run: () => openSettings() },
  { lhs: 'h', ctx: ['always'], label: '帮助', run: () => openHelp() },
];

/* 动作坞只列这个上下文相关的动作（含 always） */
function dockActions(ctx) {
  return ACTION_SPEC.filter(a =>
    (a.ctx.includes('always') && !a.touchOnly) || a.ctx.includes(ctx));
}

function renderFxPanel() {
  const panel = $('fx-panel');
  if (!panel) return;
  if (!isTouchUi()) { panel.innerHTML = ''; return; }
  const items = dockActions(ctxKind());
  panel.innerHTML = items.map(a => `<button class="fx-btn" data-fx="${escapeHtml(a.lhs)}">
      <span>${escapeHtml(a.label)}</span><span class="fx-k">${escapeHtml(a.lhs)}</span></button>`).join('');
  panel.querySelectorAll('.fx-btn').forEach(b => {
    b.addEventListener('click', () => {
      const a = items.find(x => x.lhs === b.dataset.fx);
      if (!a) return;
      dockClose();
      try { a.run(); } catch (e) { toast(String(e.message || e), 'error'); }
    });
  });
}

/* 页面/编辑器/缓冲三种上下文，决定动作坞列哪几条 */
function ctxKind() {
  if (state.editing) return 'editor';
  if (location.pathname.indexOf('/search/') >= 0) return 'search';
  if (state.current) return 'page';
  return 'buffer';
}

function dockToggle() {
  $('fxdock').classList.toggle('open');
}

function dockClose() { $('fxdock').classList.remove('open'); }

document.addEventListener('click', (e) => {
  if (isTouchUi() && e.target.closest('#sl-mode')) { e.preventDefault(); dockToggle(); return; }
  const tg = e.target.closest('#fx-toggle');
  if (tg) { e.preventDefault(); dockToggle(); return; }
  const ing = e.target.closest('#fxdock');
  if (!ing) dockClose();
}, true);

/* ── 移动端数字盘 ────────────────────────────────────
 * 借用 LazyVim 给代码折叠/分屏/缓冲编的号：数字不是随机快捷键，
 * 而是一组「有意义的频次/距离」——按手势点数字比记键名容易。 */

let ngHost = null, ngList = [];

function openNumGrid(host) {
  const grid = $('ng-grid');
  if (!grid) return;
  ngHost = host === undefined ? null : host;
  ngList = [];
  if (!ngList.length) {
    // 页面视图：取当前列表的 9 项
    if (ngHost) {
      ngList = [...ngHost.querySelectorAll('.row[data-page], .mini li[data-page], .task .task-title a[data-page], .bl-item[data-page]')]
        .slice(0, 9)
        .map((el, i) => ({
          n: i + 1,
          label: (el.dataset.page || '').slice(0, 14),
          title: el.dataset.page || '',
          run: () => navigate('page', el.dataset.page),
        }));
    }
    if (!ngList.length && state.buffers.length) {
      ngList = state.buffers.slice(0, 9).map((b, i) => ({ n: i + 1, label: b.slice(0, 14), title: b, run: () => navigate('page', b) }));
    }
    if (!ngList.length) {
      ngList = ['f', 's', 'g', 't', 'd', 'a', 'b', 'n', 'j'].map((k, i) => {
        const it = ACTION_SPEC_BY_LHS_OR_KEY(k);
        return { n: i + 1, label: it ? it.label : k, run: it ? it.run : () => {} };
      });
    }
  }
  grid.innerHTML = ngList.map((x, i) => `<div class="ng-key" data-n="${x.n}" title="${escapeHtml(x.title || x.label)}">
      <span class="ng-num">${x.n}</span><span class="ng-lbl">${escapeHtml(x.label)}</span></div>`).join('');
  grid.querySelectorAll('.ng-key').forEach(k => {
    k.addEventListener('click', () => {
      const item = ngList.find(x => String(x.n) === k.dataset.n);
      closeNumGrid();
      if (item) { try { item.run(); } catch (e) { toast(String(e.message || e), 'error'); } }
    });
  });
  $('ng-foot').innerHTML = '<span><span class="k">1-9</span> 选择</span><span><span class="k">Esc</span> 关闭</span><span>移动端替代 gg / 数字前缀</span>';
  $('numgrid').classList.add('open');
}

function ACTION_SPEC_BY_LHS_OR_KEY(k) {
  return ACTION_SPEC.find(a => a.lhs.replace(/[`<>]/g, '') === k);
}

function closeNumGrid() { $('numgrid').classList.remove('open'); }

window.addEventListener('keydown', (e) => {
  if (!$('numgrid').classList.contains('open')) return;
  if (e.key === 'Escape') { e.preventDefault(); closeNumGrid(); return; }
  if (/^[1-9]$/.test(e.key)) {
    const item = ngList.find(x => String(x.n) === e.key);
    if (item) { e.preventDefault(); closeNumGrid(); item.run(); }
  }
});

/* ══════════════════════════════════════════════════════════
   触屏手势（只在 touch 模式启用）
   ──────────────────────────────────────────────────────────
   手势不是自创快捷键，而是把已有的 vim 动作映射到触屏原语：
     · 单指纵向滑动 → 天然滚动（浏览器负责，不接管）
     · 横向滑动     → ]b / [b（切缓冲），等价 LazyVim 的 ]b [b
     · 长按 wikilink → 预览（桌面是 hover，手机没有 hover）
   之所以只做这三条，是因为其它动作在动作坞里已经一点即达；
   手势多了会和系统返回手势冲突。
   ══════════════════════════════════════════════════════════ */

let swipeStart = null;

function attachTouchGestures() {
  const view = $('view');
  if (!view || view.__kbTouch) return;
  view.__kbTouch = true;

  view.addEventListener('touchstart', (e) => {
    if (!isTouchUi() || e.touches.length !== 1) { swipeStart = null; return; }
    const t = e.touches[0];
    swipeStart = { x: t.clientX, y: t.clientY, ts: Date.now(), moved: false };
  }, { passive: true });

  view.addEventListener('touchmove', (e) => {
    if (!swipeStart || e.touches.length !== 1) return;
    const t = e.touches[0];
    if (Math.abs(t.clientX - swipeStart.x) > 12 || Math.abs(t.clientY - swipeStart.y) > 12) swipeStart.moved = true;
  }, { passive: true });

  view.addEventListener('touchend', (e) => {
    if (!swipeStart || !isTouchUi()) { swipeStart = null; return; }
    const t = e.changedTouches[0];
    const dx = t.clientX - swipeStart.x;
    const dy = t.clientY - swipeStart.y;
    const dt = Date.now() - swipeStart.ts;
    const start = swipeStart;
    swipeStart = null;
    // 只认「快、短、以横向为主」的滑动，避免和滚动/长按打架
    if (dt > 500 || !start.moved) return;
    if (Math.abs(dx) < 60 || Math.abs(dx) < Math.abs(dy) * 1.8) return;
    if (!$('view').closest('#editor')) { /* noop, 保持结构 */ }
    cycleBuffer(dx < 0 ? 1 : -1);
    toast(dx < 0 ? '→ 下一个缓冲' : '← 上一个缓冲');
  }, { passive: true });
}

function isMobile() { return window.innerWidth <= 760; }

/* ══════════════════════════════════════════════════════════
   键盘
   ══════════════════════════════════════════════════════════ */

let lastKey = 0;

/* vim 的计数前缀：按数字键先攒起来，下一个动作乘上去（3j / 5k / 2]b）。
 * 只在非编辑器视图生效 —— 编辑器里数字属于 vim 自己。 */
let pendingCount = 0;
let pendingCountTs = 0;

/* z 前缀（zz / zt / zb / z.）—— 和计数前缀同理，等第二个键 */
let pendingZ = false;
let pendingZTimer = null;

/* 标记前缀（m{a-z} 打、`{a-z} 跳）—— 两个键序列，需要记第一个键 */
let pendingMark = null;      // 'set' | 'jump' | null
let pendingMarkTs = 0;

function takeCount() {
  const n = pendingCount || 1;
  pendingCount = 0;
  return n;
}

function pushCountDigit(ch) {
  const now = Date.now();
  // 超过 1.5 秒算新的计数（与 vim 的 timeoutlen 类似）
  if (now - pendingCountTs > 1500) pendingCount = 0;
  pendingCount = Math.min(999, pendingCount * 10 + Number(ch));
  pendingCountTs = now;
}

/* ══════════════════════════════════════════════════════════
   Leader（<space>）唯一出口 —— capture 阶段
   ──────────────────────────────────────────────────────────
   原来的写法有两份处理同一事件的代码，导致两个方向都坏：

   ① <space> 在编辑器里由 CodeMirror 的 extraKeys 打开 which-key，
      但同一个 keydown 会继续冒泡到 document，被下面 leaderOpen 分支
      当成「leader 之后的第一个按键」→ 立刻 closeWhichKey()。
      表现：编辑器 normal 模式按 space 什么都不发生。
      （非编辑器视图没有 CodeMirror 抢键，所以那边一直是好的 ——
       这就是「主页能开、编辑页不能」的原因，不是页面逻辑混乱，
       是同一状态被两个 handler 争用。）

   ② 反方向：which-key 打开时按键先到 CodeMirror（f 被当成 vim 的
      find motion 吃掉），然后才到 leaderOpen 分支。

   懒人包的做法是 <Space> 被映射掉，编辑器根本看不到它。所以这里在
   capture 阶段一次判定「打开 / 后续按键」，并 stopPropagation，
   让 CodeMirror 与输入框都收不到这些键。
   ══════════════════════════════════════════════════════════ */
document.addEventListener('keydown', (e) => {
  /* 命令行打开时，Escape 必须在这里关掉 —— 不能等冒泡阶段那个 handler。
   *
   * 实测（在 document 的 capture/bubble 各挂一个探针）：
   *   capture key=Escape target=cmdline-input cmdOpen=true
   *   （bubble 探针根本没被调用）
   * 也就是按键在 capture 与 bubble 之间被某个监听者 stopPropagation 了。
   * 结果 Escape 关不掉命令行、cmd.open 一直 true —— 之后再按 `:`，
   * 冒泡 handler 认为「命令行还开着，其它键交给 input」，
   * 于是冒号被当成普通字符打进输入框，显示成 ":xxx"。
   * 在 capture 阶段处理最保险：此时一定还没被别人打断。 */
  if (cmd.open) {
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      closeCmdline();
    }
    return;                                  // 其它键交给命令行（冒泡处处理）
  }

  /* Ctrl-o / Ctrl-i —— vim 的跳转历史，编辑器里也要能用（对标 nvim 默认键位）。
   * 放在 capture 且不等 leaderOpen，因为这个组合不可能是 vim 的普通动作。 */
  if ((e.ctrlKey || e.metaKey) && (e.key === 'o' || e.key === 'i') && !e.shiftKey) {
    e.preventDefault();
    e.stopPropagation();
    jumpTo(e.key === 'o' ? -1 : 1);
    return;
  }

  if (e.ctrlKey || e.metaKey || e.altKey) return;

  // which-key 已打开：所有键都归它，编辑器不许看
  if (state.leaderOpen) {
    e.preventDefault();
    e.stopPropagation();
    if (e.key === 'Escape') { closeWhichKey(); return; }
    if (e.key === 'ArrowDown') { wkSel = Math.min(wkRows.length - 1, wkSel + 1); renderWhichKey(); return; }
    if (e.key === 'ArrowUp') { wkSel = Math.max(0, wkSel - 1); renderWhichKey(); return; }
    if (e.key === 'Enter') {
      const row = wkRows[wkSel];
      if (row) wkPress(row.key);
      return;
    }
    if (e.key.length === 1) {
      if (!wkPress(e.key)) closeWhichKey();
      return;
    }
    return;
  }

  if (e.key !== ' ') return;

  // 输入框里的空格就是空格；编辑器只在 normal 模式把 space 当 leader
  const inField = e.target && e.target.matches && e.target.matches('input, textarea, select');
  const vimNormal = cm && cm.hasFocus() && cm.state.vim &&
                    !cm.state.vim.insertMode && !cm.state.vim.visualMode;
  if (inField && !vimNormal) return;

  e.preventDefault();
  e.stopPropagation();
  openWhichKey();
}, true);

document.addEventListener('keydown', (e) => {
  const inFlyout = document.querySelector('.overlay.open');
  const inInput = e.target.matches('input, textarea, select');
  const cmFocused = cm && cm.hasFocus();

  // ── 命令行 ──
  if (cmd.open) {
    const input = $('cmdline-input');
    /* Escape 后 closeCmdline 已经 blur 输入框；这里再 cm.focus() 是多余的
     * 而且会把焦点抢回编辑器 —— 阅读态没有编辑器，于是焦点悬空。
     * 直接 return 即可。 */
    if (e.key === 'Escape') { e.preventDefault(); closeCmdline(); return; }
    if (e.key === 'Enter') { e.preventDefault(); submitCmdline(); return; }
    /* 历史翻页 —— vim 的 ↑ / ↓（也认 Ctrl-p / Ctrl-n）。
     * 必须在 input 的文本框吃掉它们之前处理，所以这里 preventDefault。 */
    if (e.key === 'ArrowUp' || ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key === 'p')) {
      e.preventDefault();
      const v = cmdHistMove(-1);            // ↑ = 更早的一条
      if (v === null) return;
      input.value = v;
      input.setSelectionRange(v.length, v.length);
      updateCmdHint();
      return;
    }
    if (e.key === 'ArrowDown' || ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key === 'n')) {
      e.preventDefault();
      const v = cmdHistMove(1);             // ↓ = 更近的一条
      if (v === null) return;
      input.value = v;
      input.setSelectionRange(v.length, v.length);
      updateCmdHint();
      return;
    }
    if (e.key === 'Tab') {
      e.preventDefault();
      const raw = input.value, word = raw.split(/\s+/)[0];
      const m = COMMANDS.map(c => c[0].split(' ')[0]).find(n => n.startsWith(word));
      if (m && !raw.includes(' ')) input.value = m;
      updateCmdHint();
      return;
    }
    return; // 其它键交给 input
  }

  // ── which-key ──
  // <space> 与 which-key 打开后的按键都在 document 的 capture 阶段处理完了
  // （见「Leader 唯一出口」），这里只是兜底，正常到不了。
  if (state.leaderOpen) return;

  // ── 浮层 ──
  if (inFlyout) {
    if (e.key === 'Escape') { e.preventDefault(); closeOverlays(); return; }
    if (inFlyout.id === 'ov-pick') {
      if (e.key === 'ArrowDown') { e.preventDefault(); movePick(1); return; }
      if (e.key === 'ArrowUp') { e.preventDefault(); movePick(-1); return; }
      if (e.key === 'Enter' && pickMode === 'page') {
        e.preventDefault();
        const sel = $('pick-list').querySelector('.row.kb-sel');
        doPick(sel ? sel.dataset.pick : $('pick-input').value.trim());
        return;
      }
    }
    return;
  }

  // ── 输入框里只处理 Esc ──
  if (inInput) {
    if (e.key === 'Escape') { e.target.blur(); cm && cm.focus(); }
    return;
  }

  // ── 编辑器（vim 自己处理大部分）──
  if (cmFocused) {
    const v = cm.state.vim;
    const insert = v && (v.insertMode || v.visualMode);

    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
      e.preventDefault(); saveEditor(); return;
    }
    if (e.key === 'Escape') {
      if (insert) return; // vim 自己回到 normal
      closeAllTransient();
      return;
    }
    if (!insert && e.key === ':') {
      e.preventDefault(); closeWhichKey(); openCmdline(':'); return;
    }
    /* 编辑器里的 / 交给 vim 自己（= 页内搜索，与 nvim 一致）。
     * 原来这里抢过来开全库搜索，但 CodeMirror 的 keydown 在 textarea 上、
     * 比 document 冒泡更早，/ 已经被 vim 吃掉，这段根本没机会执行
     * （而且 vim 那条路径因为缺 dialog.js 会抛 TypeError 把整个事件打断）。
     * 全库搜索改用 space s 或 :search —— 与 vim 的 :grep 分工一致。 */
    return;
  }

  // ── 非编辑器视图：自己接管 ──
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') return; // 无编辑器

  if (e.key === 'Escape') { closeAllTransient(); return; }
  if (e.key === ':') { e.preventDefault(); openCmdline(':'); return; }
  if (e.key === '/') { e.preventDefault(); openCmdline('/'); return; }

  /* m{a-z} 打标记 / `{a-z} 跳标记 —— vim 的 mark（两个键序列，前缀状态机）。
   *
   * 必须放在所有单键动作**之前**：`z 里的 z 会被 z 前缀分支先吃掉，
   * 于是「打标记」永远等不到第二个键 —— 这就是第一版 `z 报的还是上一条
   * toast 的原因。前缀活着的时候，第二个键优先归它。 */
  if (pendingMark && Date.now() - pendingMarkTs < 1500) {
    const mode = pendingMark;
    pendingMark = null;
    if (/^[a-z]$/.test(e.key)) {
      e.preventDefault();
      e.stopPropagation();
      if (mode === 'set') setMark(e.key);
      else jumpMark(e.key);
      return;
    }
    // 不是 a-z → 前缀作废，当前键照常往下走
  }
  if (e.key === 'm' && !e.shiftKey) {
    pendingMark = 'set';
    pendingMarkTs = Date.now();
    e.preventDefault();
    return;
  }
  if (e.key === '`' || e.key === "'") {
    pendingMark = 'jump';
    pendingMarkTs = Date.now();
    e.preventDefault();
    return;
  }
  // <space> 已在 capture 阶段处理（Leader 唯一出口）

  /* 列表视图（/all、标签、搜索、首页卡片）：j/k 移动光标，Enter 打开。
   * 正文页：保持原来的滚动语义（一整篇读下去不该被光标打断）。 */
  if (isListView()) {
    if (e.key === 'k' || e.key === 'ArrowUp') {
      e.preventDefault(); listMove(-1, takeCount()); return;
    }
    if (e.key === 'j' || e.key === 'ArrowDown') {
      e.preventDefault(); listMove(1, takeCount()); return;
    }
    if (e.key === 'Enter') {
      e.preventDefault(); listOpen(); return;
    }
    if (e.key === 'G') {
      e.preventDefault();
      const n = pendingCount;                        // 5G = 第 5 行
      pendingCount = 0;
      setListCursor(n ? n - 1 : listRows().length - 1);
      return;
    }
    if (e.key === 'g') { setListCursor(0); return; }
  }

  /* vim 的阅读动作（正文页与列表页通用）—— 之前只有 j/k 单步和 G/gg 到首尾，
   * 一屏一屏翻、居中、定位到屏幕首/中/尾都得靠鼠标拖滚动条。 */

  // Ctrl-d / Ctrl-u：半屏滚动（vim 的 scroll 值默认是窗口一半）
  if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && (e.key === 'd' || e.key === 'u')) {
    e.preventDefault(); e.stopPropagation();
    const n = pendingCount; pendingCount = 0;
    const v = $('view');
    if (n) { v.scrollTop += (e.key === 'd' ? 1 : -1) * n * 30; return; }  // 3<C-d> = 滚 3 行
    v.scrollTop += (e.key === 'd' ? 1 : -1) * v.clientHeight * 0.5;
    return;
  }
  // Ctrl-f / Ctrl-b：整屏（vim 的 page down/up）
  if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && (e.key === 'f' || e.key === 'b')) {
    e.preventDefault(); e.stopPropagation();
    $('view').scrollTop += (e.key === 'f' ? 1 : -1) * $('view').clientHeight * 0.9;
    return;
  }

  // H / M / L —— 光标（或视口）到窗口首 / 中 / 尾
  if (!isListView() && (e.key === 'H' || e.key === 'M' || e.key === 'L')) {
    const v = $('view');
    const h = v.clientHeight;
    e.preventDefault();
    if (e.key === 'H') v.scrollTop += -h * 0.15;
    else if (e.key === 'M') v.scrollTop += h * 0.35;
    else v.scrollTop += h * 0.75;
    return;
  }

  /* z 前缀：zz / zt / zb。
   *
   * vim 的 z 系列是把「光标行」贴到屏幕中 / 顶 / 底。这里没有行光标，
   * 近似为「把视口里当前的内容位置微调」：zt 往回 0.3 屏（内容视觉上贴顶）、
   * zb 往前 0.3 屏（贴底）、zz 居中（不动）。
   * 缺点是不如真正的 zz 精确，但比完全没有强 —— 至少三个键有反应且不误伤。 */
  if (!isListView()) {
    if (pendingZ) {
      pendingZ = false;
      if (e.key === 'z' || e.key === 't' || e.key === 'b') {
        const v = $('view');
        const h = v.clientHeight;
        if (e.key === 't') v.scrollTop -= h * 0.3;
        else if (e.key === 'b') v.scrollTop += h * 0.3;
        e.preventDefault();
        pendingZTimer && clearTimeout(pendingZTimer);
        return;
      }
      // 不是 z/t/b → 当前键照常处理（不 return）
    }
    if (e.key === 'z') {
      pendingZ = true;
      pendingZTimer && clearTimeout(pendingZTimer);
      pendingZTimer = setTimeout(() => { pendingZ = false; }, 1200);
      return;
    }
  }

  /* n / N —— 页内搜索的下一个 / 上一个命中（vim 语义） */
  if (!isListView() && (e.key === 'n' || e.key === 'N')) {
    e.preventDefault();
    pageHitJump(e.key === 'n' ? 1 : -1);
    return;
  }

  if (e.key === 'k' || e.key === 'ArrowUp') { scrollView(-1); return; }
  if (e.key === 'j' || e.key === 'ArrowDown') { scrollView(1); return; }
  if (e.key === 'd') {   // dd 风格：连按两次跳到下一屏
    const now = Date.now();
    if (now - lastKey < 400) { scrollView(1, true); lastKey = 0; return; }
    lastKey = now; return;
  }
  if (e.key === 'u') {
    const now = Date.now();
    if (now - lastKey < 400) { scrollView(-1, true); lastKey = 0; return; }
    lastKey = now; return;
  }
  if (e.key === 'G') { pendingCount = 0; $('view').scrollTop = $('view').scrollHeight; return; }
  if (e.key === 'g') {
    const now = Date.now();
    if (now - lastKey < 400) { $('view').scrollTop = 0; lastKey = 0; return; }
    lastKey = now; return;
  }
  // 缓冲导航：照 LazyVim 的 ]b / [b（]b = 下一个缓冲，[b = 上一个）
  if (e.key === ']') { pendingBuf = { dir: 1, ts: Date.now() }; return; }
  if (e.key === '[') { pendingBuf = { dir: -1, ts: Date.now() }; return; }
  if (e.key === 'b' && pendingBuf && Date.now() - pendingBuf.ts < 500) {
    const dir = pendingBuf.dir; pendingBuf = null;
    cycleBuffer(dir);
    return;
  }
  if (e.key === 'q') { closeCurrentBuffer(); return; }
  if (e.key === 'R') { invalidateCache(); navigate('home'); return; }
  /* 数字键 = 计数前缀（vim 的 3j / 5G）。放在最后，别抢前面的单键命令。
   * 1-9 起头；0 只在已有计数时追加（vim 里 0 单独按是行首）。 */
  if (/^[1-9]$/.test(e.key)) { pushCountDigit(e.key); return; }
  if (e.key === '0' && pendingCount) { pushCountDigit(e.key); return; }
});

/* ]b / [b：循环切换缓冲，与 vim/LazyVim 的 ]b [b 一致 */
let pendingBuf = null;

function cycleBuffer(dir) {
  if (!state.buffers.length) return toast('没有打开的缓冲');
  const cur = state.current ? state.current.toLowerCase() : '';
  let idx = state.buffers.findIndex(n => n.toLowerCase() === cur);
  idx = (idx + dir + state.buffers.length) % state.buffers.length;
  navigate('page', state.buffers[idx]);
}

/* ══════════════════════════════════════════════════════════
   列表光标 —— vim 的「行光标」概念搬到可点列表上
   ──────────────────────────────────────────────────────────
   /all、标签、搜索这些视图里的每一行是一个链接，但之前 j/k 只是滚动，
   提示写着「j/k 移动 · Enter 打开」而 Enter 根本没绑定 —— 文案骗人。
   这里给「可点行」一套统一光标：j/k 上下移动、Enter 打开、gg/G 首尾。
   纯滚动视图（正文页）走原来的 scrollView，两条路径按视图类型分流。
   ══════════════════════════════════════════════════════════ */

/* 排除正文里的 [[wikilink]]：它们也带 data-page，但那是「文章内的链接」，
 * 不是列表行。正文页不该被 j/k 当成列表来导航。 */
const LIST_SEL = '[data-page]:not(a.wikilink), [data-pick]';

function listRows() {
  const v = $('view');
  if (!v) return [];
  // 只认「真有可跳转目标」的行，避免把装饰性 DOM 算进来
  return [...v.querySelectorAll(LIST_SEL)].filter(el => {
    const t = el.dataset.page || el.dataset.pick;
    return !!t && el.offsetParent !== null;
  });
}

function listCursor() {
  const rows = listRows();
  if (!rows.length) return -1;
  return rows.findIndex(r => r.classList.contains('kb-cursor'));
}

function setListCursor(i, opts) {
  const rows = listRows();
  if (!rows.length) return -1;
  const n = Math.max(0, Math.min(rows.length - 1, i));
  rows.forEach((r, k) => r.classList.toggle('kb-cursor', k === n));
  const el = rows[n];
  // 只滚到刚好可见，不整页跳（与 vim 的 <C-e>/<C-y> 不同：这里保持上下文）
  const v = $('view');
  const r1 = el.getBoundingClientRect(), r2 = v.getBoundingClientRect();
  const pad = 12;
  if (!(opts && opts.noScroll)) {
    if (r1.top < r2.top + pad) v.scrollTop -= (r2.top + pad - r1.top);
    else if (r1.bottom > r2.bottom - pad) v.scrollTop += (r1.bottom - (r2.bottom - pad));
  }
  state.listIdx = n;
  return n;
}

function listMove(dir, count) {
  const rows = listRows();
  if (!rows.length) return false;
  const cur = listCursor();
  const from = cur < 0 ? (dir > 0 ? -1 : 0) : cur;
  setListCursor(from + dir * (count || 1));
  return true;
}

function listOpen() {
  const rows = listRows();
  const cur = listCursor();
  if (cur < 0 || !rows[cur]) return false;
  const el = rows[cur];
  const target = el.dataset.page || el.dataset.pick;
  if (!target) return false;
  el.click();
  return true;
}

/* 列表视图里 j/k 的语义与正文页不同。
 *
 * 判据用「视图骨架」而不是「有多少个 data-page」：正文页底部也有反链
 * （.bl-item[data-page]），按数量判会把正文页误当成列表 —— j/k 变成在
 * 反链之间跳，正文反而读不下去。所以只认 .list-wrap（/all、标签、搜索、
 * 回收站都是这个骨架），首页看板单独认 .dash。 */
function isListView() {
  const v = $('view');
  if (!v) return false;
  return !!v.querySelector(':scope > .list-wrap, :scope > .dash');
}

/* ══════════════════════════════════════════════════════════
   页内搜索跳转 —— vim 的 n / N
   ──────────────────────────────────────────────────────────
   之前 `/` 在阅读视图打开的是「全库搜索」（跳到 /kb/search/xxx），
   列表页里 n/N 完全没绑定。这里补「在正文里找词并跳过去」：

   - 用 / 提交一次关键词后记下 pattern（state.lastPattern）
   - n 下一个命中、N 上一个，命中处高亮
   - 高亮用 CSS Custom Highlight API（不改 DOM，不破坏事件绑定）；
     老浏览器没有这个 API 就只跳转不高亮
   ══════════════════════════════════════════════════════════ */

const hlRanges = [];

function clearPageHits() {
  hlRanges.length = 0;
  if (window.CSS && CSS.highlights) CSS.highlights.delete('kb-hit');
}

function buildPageHits(pat) {
  clearPageHits();
  state.hitIdx = -1;
  const v = $('view');
  if (!v || !pat) return 0;
  const needle = pat.toLowerCase();
  const walker = document.createTreeWalker(v, NodeFilter.SHOW_TEXT, {
    acceptNode: (node) => {
      if (!node.nodeValue || !node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
      // 跳过脚本/样式
      const p = node.parentNode;
      if (p && /^(SCRIPT|STYLE)$/.test(p.tagName)) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  let node;
  while ((node = walker.nextNode())) {
    const text = node.nodeValue;
    const low = text.toLowerCase();
    let from = 0;
    while (true) {
      const at = low.indexOf(needle, from);
      if (at < 0) break;
      const r = document.createRange();
      r.setStart(node, at);
      r.setEnd(node, at + needle.length);
      hlRanges.push(r);
      from = at + needle.length;
    }
  }
  if (hlRanges.length && window.CSS && CSS.highlights) {
    CSS.highlights.set('kb-hit', new Highlight(...hlRanges));
  }
  return hlRanges.length;
}

/* 命中游标（vim 的「当前位置」）。第一版靠「视口里有没有」判断下一个，
 * 命中本来就在视口内时永远判成第 1 个 —— n 三次都停在第 1/10 处。 */
function pageHitJump(dir) {
  const pat = state.lastPattern;
  if (!pat) return toast('没有搜索词（先用 / 搜索）');
  if (!hlRanges.length) {
    const n = buildPageHits(pat);
    if (!n) return toast(`本页找不到「${pat}」`);
  }
  const total = hlRanges.length;
  let idx = state.hitIdx;
  if (typeof idx !== 'number' || idx < 0 || idx >= total) idx = dir > 0 ? -1 : total;
  idx += dir;
  let wrapped = false;
  if (idx >= total) { idx = 0; wrapped = true; }
  if (idx < 0) { idx = total - 1; wrapped = true; }
  state.hitIdx = idx;

  const v = $('view');
  const rect = hlRanges[idx].getBoundingClientRect();
  const vTop = v.getBoundingClientRect().top;
  // 把命中放到窗口上 1/3 处；已经在合适位置就别抖（差值很小就不动）
  const want = v.scrollTop + (rect.top - vTop) - v.clientHeight * 0.35;
  const max = Math.max(0, v.scrollHeight - v.clientHeight);
  const target = Math.max(0, Math.min(max, want));
  if (Math.abs(target - v.scrollTop) > 4) v.scrollTop = target;

  toast(`第 ${idx + 1}/${total} 处「${pat}」${wrapped ? '（已回绕）' : ''}`);
}

/* ══════════════════════════════════════════════════════════
   gf —— vim 的「跟到光标下的链接」
   ──────────────────────────────────────────────────────────
   vim 里光标停在文件名上按 gf 就跳过去。网页没有键盘光标，
   这里取一个近似：**跟到视口里最靠上的那个链接**。
   优先 wikilink，其次反向链接；列表视图则跟当前光标行。

   为什么不做「精确光标」：正文是渲染出来的 HTML，没有字符级光标，
   要做得精确就得给每个字加 caret 位置 —— 那不是这个前端该干的事。
   「视口里第一个链接」这个语义在笔记场景里已经够用（读完一段就往下走）。
   ══════════════════════════════════════════════════════════ */

function followLinkUnderCursor() {
  // 列表视图：跟当前光标行（Enter 已经有，但 gf 也该能用）
  if (isListView()) {
    const rows = listRows();
    const cur = listCursor();
    if (cur >= 0 && rows[cur]) { rows[cur].click(); return; }
  }
  const v = $('view');
  if (!v) return toast('没有可打开的视图');
  const links = [...v.querySelectorAll('a.wikilink[data-page], .bl-item[data-page]')]
    .filter(el => (el.dataset.page || '').trim());
  if (!links.length) return toast('本页没有链接可跟');

  const vTop = v.getBoundingClientRect().top;
  // 第一个「还没滚出视口上方」的链接
  const hit = links.find(el => el.getBoundingClientRect().bottom > vTop + 4) || links[0];
  hit.click();
  toast('gf → ' + hit.dataset.page);
}

function scrollView(dir, page) {
  const v = $('view');
  const h = v.clientHeight;
  v.scrollTop += dir * (page ? h * 0.9 : h * 0.25);
}

if ($('ng-close')) $('ng-close').addEventListener('click', () => closeNumGrid());

$('cmdline-input').addEventListener('input', () => {
  /* 手动改了内容 → 历史游标回到「末尾之后」，把改后的内容当成新草稿。
   * 否则「↑ 召回一条 → 编辑 → ↓」会跳回召回前那条的旧快照。 */
  cmdHistReset($('cmdline-input').value);
  updateCmdHint();
  if (cmd.prefix === '/') {
    const q = $('cmdline-input').value.trim();
    setHintCmd(q ? `搜索「${escapeHtml(q)}」· Enter 执行` : '输入关键词搜索');
  }
});

// picker 输入
$('pick-input').addEventListener('input', (e) => {
  pickActive = 0;
  if (pickMode === 'page') renderPick(e.target.value);
});

// 覆盖层点空白关闭
document.querySelectorAll('.overlay').forEach(o => {
  o.addEventListener('click', (e) => { if (e.target === o) closeOverlays(); });
});

/* ══════════════════════════════════════════════════════════
   插件桥（plugin.js / panel.js 用）
   ──────────────────────────────────────────────────────────
   插件不该直接摸全局状态，所以这里把内部实现挂成登记过的一小组句柄。
   命名带 __KB 前缀，一眼能看出「这是给插件层用的，不是公共 API」。
   ══════════════════════════════════════════════════════════ */

Object.assign(window, {
  // 基础
  navigate, dispatch, toast, escapeHtml, renderMd,
  openOverlay, closeOverlays, openPicker, confirmText, promptText,
  setStatusName, showView, getSetting, showHome, openTrash,
  /* 插件视图需要「铺一层首页当底，再叠自己的面板」：
   * 深链接 /kb/health 进来时不能是一张空白页，Esc 也得有东西可退。
   * navToken 直接 ++ 会让进行中的请求失效，所以收敛成一个函数。 */
  pluginBackdrop: () => showHome(++navToken),
  // 数据
  jfetch, getPage, getTags, ensureNames, fetchAllPages, invalidateCache,
  taskPages, taskPage, parseTasks, parseTaskLine,
  // 编辑器操作
  openEditor, closeEditor, saveEditor,
  // 树/表重建（插件注册后调用）
  rebuildLeaderTree: () => {
    LEADER_SPEC = composeLeaderSpec();
    LEADER_TREE = buildLeaderTree(LEADER_SPEC);
    if (state.leaderOpen) renderWhichKey();
  },
  rebuildCommandTable: () => { _rebuildCommandTable(); },
  rebuildRouteTable: () => { /* 路由表是 switch，插件视图走 dispatch 的插件分支 */ },
  runCommand,
});

/* 这三个必须是活引用：Object.assign 会把 getter 的「当时的值」拷走
 * （cm 在 bridge 执行时还是 null），之后编辑器建好也拿不到。 */
Object.defineProperties(window, {
  cm: { get() { return cm; }, configurable: true },
  state: { get() { return state; }, configurable: true },
  _cache: { get() { return _cache; }, configurable: true },
});

/* ══════════════════════════════════════════════════════════
   启动
   ══════════════════════════════════════════════════════════ */

(function boot() {
  const reg = themesReg();
  const savedScheme = localStorage.getItem(THEMES_KEY) || (reg ? reg.defaultTheme() : 'catppuccin-mocha');
  applyTheme(savedScheme);

  updateClock();
  setInterval(updateClock, 15000);
  updateNet();
  window.addEventListener('online', updateNet);
  window.addEventListener('offline', updateNet);
  updatePageCount();
  setInterval(updatePageCount, 120000);

  loadCustomCSS();
  renderTabline();

  try {
    const saved = JSON.parse(localStorage.getItem('kb-buffers') || '[]');
    if (Array.isArray(saved)) state.buffers = saved.filter(x => typeof x === 'string').slice(0, 20);
  } catch (e) {}

  applyInputMode();
  attachTouchGestures();

  /* 插件层：先同步装触发条件（这样 lazy 插件声明的路由词立刻可见，
   * 首屏直接访问 /gantt 不会被当成未知路由退回首页），
   * 再异步加载非 lazy 插件。 */
  if (window.KB && typeof window.KB.armAll === 'function') {
    try { window.KB.armAll(); } catch (e) { console.warn('[plugins]', e); }
    Promise.resolve().then(() => window.KB.setup()).catch((e) => {
      console.warn('[plugins]', e);
    });
  }

  const r = parseRoute(location.pathname);
  // 首屏直接 dispatch（replace 掉可能的空历史项）
  if (location.pathname === BASE + '/' || location.pathname === BASE) {
    history.replaceState({ target: 'home' }, '', location.pathname);
  }
  /* 首屏这一页也要进 jumplist。否则清单里只有「点进去的那一页」一条，
   * Ctrl-o 无处可退 —— 这就是第一版「Ctrl-o 按了没反应」的原因：
   * boot 走的是 dispatch（不记），只有 navigate 才记。 */
  recordJump(r.target, r.arg);
  dispatch(r.target, r.arg);

  window.addEventListener('resize', () => { if (cm) cm.refresh(); });

  // 离线提示
  window.addEventListener('unhandledrejection', (e) => {
    const msg = String((e.reason && e.reason.message) || '');
    if (!navigator.onLine || /failed to fetch|networkerror|load failed/i.test(msg)) {
      e.preventDefault();
    }
  });

  // service worker（离线壳）
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register(BASE + '/sw.js', { scope: BASE + '/' }).catch(() => {});
    });
  }

  // beforeunload 草稿
  window.addEventListener('beforeunload', (e) => {
    if (state.editing && editorDirty) {
      draftSave();
    }
  });
})();
