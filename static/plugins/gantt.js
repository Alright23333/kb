/* ══════════════════════════════════════════════════════════════════════
   插件：gantt —— 任务甘特图
   ─────────────────────────────────────────────────────────────────────
   这是 lazygit 模式的直接对应物：外部工具（frappe-gantt）负责渲染，
   宿主（KB）负责喂数据并给它一块显示区域。

   和 lazygit 的区别：nvim 里那块区域只能是终端，这里可以是真 DOM，
   所以外加了「任务表本身可点跳转、日期区间可调、导出 mermaid 代码」。

   数据来源：任务页的 parseTasks（每行格式见 app.js 的 parseTaskLine）
     p1 2026-11-20 做某事 due:2026-11-25
   → start = 行首日期，end = due，没有 due 的当期任务按 1 天画
   ══════════════════════════════════════════════════════════════════════ */
KB.define('gantt', (api, opts) => {
  const MODE = (opts && opts.mode) || 'Month';
  let viewEl = null;
  let gantt = null;

  /* 只在 lazily 加载 frappe-gantt 的 css/js，不挂在首屏 */
  function ensureLib() {
    if (window.Gantt) return Promise.resolve();
    return new Promise((resolve, reject) => {
      api.css('vendor/frappe-gantt/frappe-gantt.css');
      const s = document.createElement('script');
      s.src = api.BASE + '/static/vendor/frappe-gantt/frappe-gantt.min.js';
      s.onload = resolve;
      s.onerror = () => reject(new Error('frappe-gantt 加载失败'));
      document.head.appendChild(s);
    });
  }

  /* 任务行 → frappe-gantt 的 task 结构 */
  function toGanttTasks(tasks, pageName) {
    const out = [];
    const today = new Date();
    tasks.forEach((t, i) => {
      const start = t.start || '2026-01-01';
      let end = t.due;
      if (!end) {
        // 无截止 → 起点 + 1 天，视觉上是一条短条
        const d = new Date(start + 'T00:00:00');
        d.setDate(d.getDate() + 1);
        end = d.toISOString().slice(0, 10);
      }
      if (end < start) end = start;
      const pct = t.done ? 100 : (t.due && t.due < today.toISOString().slice(0, 10) ? 0 : 30);
      out.push({
        id: 't' + i,
        name: t.title.slice(0, 60),
        start, end,
        progress: pct,
        pri: t.pri,
        done: t.done,
        sourcePage: pageName,
        custom_class: 'gantt-' + t.pri + (t.done ? ' gantt-done' : ''),
      });
    });
    return out;
  }

  async function render(arg, token) {
    const view = api.ui.view();
    if (!view) return;
    view.className = '';
    view.innerHTML = `<div class="list-wrap">
      <div class="list-head"><h1>甘特图</h1>
        <span class="count" id="gantt-src"></span>
        <span class="spacer"></span>
        <select id="gantt-mode" class="btn btn-sm">
          <option value="Day">日</option><option value="Week">周</option>
          <option value="Month" selected>月</option>
        </select>
        <button class="btn btn-sm" id="gantt-mermaid">复制 mermaid</button>
      </div>
      <div id="gantt-host" style="padding:1em .8em 3em"></div>
    </div>`;

    const host = document.getElementById('gantt-host');
    host.innerHTML = '<div class="empty">加载中…</div>';

    try {
      const page = await api.data.jfetch(api.API + '/pages/' + encodeURIComponent(api.data.tasks.current()));
      if (!document.getElementById('gantt-host')) return;   // 已经离开这个视图
      const tasks = api.data.tasks.parse(page.content).filter(t => !t.done);
      document.getElementById('gantt-src').textContent = page.name + ' · ' + tasks.length + ' 条未完成';

      if (!tasks.length) {
        host.innerHTML = '<div class="empty">这个任务页里没有可画的任务<br><span class="sl-hint">格式：p1 2026-11-20 做某事 due:2026-11-25</span></div>';
        return;
      }

      await ensureLib();
      host.innerHTML = '';
      const svgWrap = document.createElement('div');
      svgWrap.className = 'gantt-wrap';
      host.appendChild(svgWrap);

      const list = toGanttTasks(tasks, page.name);
      gantt = new window.Gantt(svgWrap, list, {
        view_mode: MODE,
        date_format: 'YYYY-MM-DD',
        language: 'zh',
        bar_height: 24,
        padding: 16,
        on_click: (task) => {
          const name = list.find(x => String(x.id) === String(task.id));
          if (name) api.ui.toast(name.name);
        },
      });

      document.getElementById('gantt-mode').addEventListener('change', (e) => {
        if (gantt && gantt.change_view_mode) gantt.change_view_mode(e.target.value);
      });
      document.getElementById('gantt-mermaid').addEventListener('click', () => {
        copyMermaid(list, page.name);
      });
    } catch (e) {
      host.innerHTML = '<div class="empty">读取失败：' + api.ui.escapeHtml(e.message) + '</div>';
    }
  }

  /* 把当前甘特导出成 mermaid gantt 代码块，可直接粘贴到页面里 */
  function copyMermaid(list, pageName) {
    const lines = ['```mermaid', 'gantt', '  dateFormat  YYYY-MM-DD', '  title ' + pageName.replace(/"/g, '') , '  axisFormat  %m-%d'];
    list.forEach((t, i) => {
      const tag = 'task' + i;
      lines.push(`  ${t.name.replace(/[:\n]/g, ' ')} :${tag}, ${t.start}, ${t.end}`);
    });
    lines.push('```');
    const text = lines.join('\n');
    const done = () => api.ui.toast('mermaid 代码已复制到剪贴板');
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(() => fallback());
    } else fallback();
    function fallback() {
      const ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand('copy'); done(); } catch (e) { api.ui.toast('复制失败', 'error'); }
      ta.remove();
    }
  }

  api.css('plugins/gantt.css');
  api.view('gantt', render, { desc: '任务甘特图' });
  api.command('gantt', '任务甘特图', () => api.ui.navigate('gantt'));
});
