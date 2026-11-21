/* ══════════════════════════════════════════════════════════════════════
   插件：autosave-hint —— 最小示范
   ─────────────────────────────────────────────────────────────────────
   不注册任何 UI，只挂钩子：编辑器打开后计时，超过 N 分钟没保存就
   toast 提醒一次。用来演示「插件可以完全不碰 DOM，只订阅事件」。
   ══════════════════════════════════════════════════════════════════════ */
KB.define('autosave-hint', (api, opts) => {
  const LIMIT_MS = (opts && opts.minutes ? opts.minutes : 20) * 60e3;
  let timer = null;

  function start() {
    stop();
    timer = setTimeout(() => {
      if (api.state && api.state.editing) {
        api.ui.toast('已编辑 20 分钟，记得 :w 保存', 'warn');
      }
    }, LIMIT_MS);
  }
  function stop() { if (timer) { clearTimeout(timer); timer = null; } }

  api.on('editor', start);
  api.on('navigate', stop);
  api.command('autosave', '重置保存提醒计时', () => { start(); api.ui.toast('提醒计时已重置'); });
});
