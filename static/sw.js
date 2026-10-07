/* KB service worker — offline app shell.
 *
 * Served from the app root (see main.py) so its scope covers the whole app.
 * The deployment prefix is derived from this script's own URL, so the same
 * file works for both '/' and '/kb/' deployments.
 *
 * Strategy:
 *   /api/*      → never intercepted: data must always be live
 *   navigations → network first, cached shell as the offline fallback
 *   /static/*   → cache first (vendored assets are immutable)
 *
 * Bump VERSION whenever the shell contents change, otherwise clients keep the
 * old HTML until the worker updates.
 */
const BASE = self.location.pathname.replace(/\/sw\.js$/, '');
const VERSION = 'kb-shell-v1';

const SHELL = [
  BASE + '/',
  BASE + '/manifest.webmanifest',
  BASE + '/static/icons/icon-192.png',
  BASE + '/static/vendor/codemirror/codemirror.min.css',
  BASE + '/static/vendor/codemirror/dracula.min.css',
  BASE + '/static/vendor/codemirror/codemirror.min.js',
  BASE + '/static/vendor/codemirror/markdown.min.js',
  BASE + '/static/vendor/codemirror/closebrackets.min.js',
  BASE + '/static/vendor/codemirror/matchbrackets.min.js',
  BASE + '/static/vendor/codemirror/active-line.min.js',
  BASE + '/static/vendor/markdown-it.min.js',
  BASE + '/static/vendor/frappe-gantt/frappe-gantt.css',
  BASE + '/static/vendor/frappe-gantt/frappe-gantt.min.js',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(VERSION)
      // addAll() rejects the whole install if any URL 404s; add individually
      // so one missing asset cannot break offline support entirely.
      .then((cache) => Promise.all(
        SHELL.map((url) => cache.add(url).catch(() => { /* non-fatal */ }))
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

  // Never cache the API — a stale note is worse than a failed request.
  if (url.pathname.startsWith(BASE + '/api/')) return;

  // SPA navigations: try the network (so edits and new content show up),
  // fall back to the cached shell when offline.
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

  // Vendored assets never change without a VERSION bump.
  if (url.pathname.startsWith(BASE + '/static/')) {
    event.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((res) => {
        if (res && res.ok) {
          const copy = res.clone();
          caches.open(VERSION).then((c) => c.put(req, copy)).catch(() => {});
        }
        return res;
      }))
    );
  }
});
