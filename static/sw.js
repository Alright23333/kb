/* KB service worker —— 离线外壳。
 *
 * 由应用根路径提供（见 main.py），所以 scope 覆盖整个应用。
 * 部署前缀从本脚本自身 URL 推导，'/' 和 '/kb/' 两种部署共用一个文件。
 *
 * 策略：
 *   /api/*                  → 完全不拦截：数据必须永远实时
 *   navigations             → 网络优先，离线时回退到缓存的外壳
 *   /static/vendor/*        → 先缓存（第三方库版本固定，不会变）
 *   /static/ 其它（app.js） → 网络优先：自己改的前端必须立刻生效
 *
 * 外壳内容或静态资源策略变化时必须改 VERSION，否则客户端会一直跑旧的
 * HTML/JS（这个坑踩过一次：app.js 改了但浏览器还在用缓存副本）。
 */
const BASE = self.location.pathname.replace(/\/sw\.js$/, '');
const VERSION = 'kb-shell-v16';

const SHELL = [
  BASE + '/',
  BASE + '/manifest.webmanifest',
  BASE + '/static/icons/icon-192.png',
  BASE + '/static/vendor/codemirror/codemirror.min.css',
  BASE + '/static/vendor/codemirror/dialog.css',
  BASE + '/static/vendor/codemirror/show-hint.css',
  BASE + '/static/vendor/codemirror/show-hint.js',
  BASE + '/static/vendor/codemirror/codemirror.min.js',
  BASE + '/static/vendor/codemirror/markdown.min.js',
  BASE + '/static/vendor/codemirror/closebrackets.js',
  BASE + '/static/vendor/codemirror/matchbrackets.js',
  BASE + '/static/vendor/codemirror/active-line.js',
  BASE + '/static/vendor/codemirror/searchcursor.js',
  BASE + '/static/vendor/codemirror/search.js',
  BASE + '/static/vendor/codemirror/jump-to-line.js',
  BASE + '/static/vendor/codemirror/scrollpastend.js',
  BASE + '/static/vendor/codemirror/vim.js',
  BASE + '/static/vendor/markdown-it.min.js',
  BASE + '/static/vendor/mermaid/mermaid.min.js',
  BASE + '/static/app.js',
  BASE + '/static/themes/index.js',
  BASE + '/static/themes/catppuccin-mocha.css',
  BASE + '/static/themes/catppuccin-latte.css',
  BASE + '/static/panel.js',
  BASE + '/static/plugin.js',
  BASE + '/static/plugins/index.js',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(VERSION)
      // addAll() 只要有一个 URL 404 整个安装就失败；逐个 add，
      // 缺一个资源不该让离线能力整体失效。
      .then((cache) => Promise.all(
        SHELL.map((url) => cache.add(url).catch(() => { /* 非致命 */ }))
      ))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  let url;
  try { url = new URL(req.url); } catch (e) { return; }
  if (url.origin !== self.location.origin) return;

  // API 永不缓存：过期的笔记比请求失败更糟。
  if (url.pathname.startsWith(BASE + '/api/')) return;

  // SPA 导航：先试网络（这样编辑和新内容能立刻出现），离线回退外壳。
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res && res.ok) {
            const copy = res.clone();
            caches.open(VERSION).then((c) => c.put(BASE + '/', copy)).catch(() => {});
          }
          return res;
        })
        .catch(() => caches.match(BASE + '/').then((hit) => hit || Response.error()))
    );
    return;
  }

  // 第三方库：版本固定，缓存优先。
  if (url.pathname.startsWith(BASE + '/static/vendor/')) {
    event.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((res) => {
        if (res && res.ok) {
          const copy = res.clone();
          caches.open(VERSION).then((c) => c.put(req, copy)).catch(() => {});
        }
        return res;
      }))
    );
    return;
  }

  // 自己写的前端（app.js 等）：网络优先，失败才用缓存。
  // 缓存优先会让改动永远上不了线，除非每次改都 bump VERSION。
  if (url.pathname.startsWith(BASE + '/static/')) {
    event.respondWith(
      fetch(req).then((res) => {
        if (res && res.ok) {
          const copy = res.clone();
          caches.open(VERSION).then((c) => c.put(req, copy)).catch(() => {});
        }
        return res;
      }).catch(() => caches.match(req).then((hit) => hit || Response.error()))
    );
  }
});

// 允许页面主动要求新 worker 立刻接管（改完前端后不必手动清缓存）。
self.addEventListener('message', (event) => {
  if (event.data === 'kb:skip-waiting') self.skipWaiting();
});
