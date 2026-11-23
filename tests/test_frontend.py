"""前端 e2e 冒烟测试 —— 跑真实浏览器，测的是「键位真的能按出来」。

后端有 test_parse.py / test_refs.py（纯函数、毫秒级），但前端一直是
「改完手动开浏览器看一眼」。这一轮连续修了 7 个交互 bug，其中好几个
（`/` 的 cmd.prefix、`n` 的命中游标、`q` 的关页语义）都是**只有真按
一次键才暴露**的 —— 纯函数测不出来。所以补这个。

跑法：

  # 起一个实例（默认打 http://127.0.0.1:8081/kb）
  KB_E2E_URL=http://127.0.0.1:8081/kb python3 -m unittest tests.test_frontend -v

  # 没有 chromium / 没装 playwright 时自动 skip，不会让 CI 变红
  python3 -m unittest discover -s tests -v

设计约束：
  * 不引 pytest / 不引 fixtures —— 就是 unittest + playwright sync API
  * 断言尽量落在「用户能观察到的结果」（URL、toast 文案、scrollTop、
    光标行名），不测内部变量名，免得改实现就红一片
  * 每个用例自己开新 page（本地存储、缓冲、镜像互不污染）
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

URL = os.environ.get('KB_E2E_URL', 'http://127.0.0.1:8081/kb')
CHROMIUM = os.environ.get('KB_E2E_CHROMIUM', '/usr/sbin/chromium')

try:
    from playwright.sync_api import sync_playwright
except ImportError:                                   # pragma: no cover
    sync_playwright = None

HAVE_BROWSER = bool(sync_playwright) and (
    os.path.exists(CHROMIUM) or bool(os.environ.get('KB_E2E_ALLOW_DOWNLOAD'))
)


def setUpModule():
    if not HAVE_BROWSER:
        raise unittest.SkipTest(
            'e2e: 需要 playwright + chromium（设 KB_E2E_CHROMIUM 指向二进制）'
        )


class Base(unittest.TestCase):
    """每个用例一个干净的 page（同 context 会被 localStorage 串味）。"""

    @classmethod
    def setUpClass(cls):
        cls._pw = sync_playwright().start()
        cls._browser = cls._pw.chromium.launch(
            executable_path=CHROMIUM,
            args=['--no-sandbox', '--disable-dev-shm-usage'],
        )

    @classmethod
    def tearDownClass(cls):
        cls._browser.close()
        cls._pw.stop()

    def setUp(self):
        self.page = self._browser.new_page(viewport={'width': 1280, 'height': 900})
        self.errors = []
        self.page.on('pageerror', lambda e: self.errors.append(str(e)))
        self.page.on('console', self._on_console)

    # 断网用例里浏览器必然打出「资源加载失败」，这是我们主动 set_offline
    # 造成的，不是代码 bug。只认真正的 JS 异常与其它 error。
    IGNORE_CONSOLE = (
        'ERR_INTERNET_DISCONNECTED', 'ERR_FAILED', 'ERR_CONNECTION',
        'Failed to load resource',
    )

    def _on_console(self, msg):
        if msg.type != 'error':
            return
        text = msg.text
        if any(sig in text for sig in self.IGNORE_CONSOLE):
            return
        self.errors.append('console: ' + text)

    def tearDown(self):
        try:
            self.page.close()
        finally:
            pass
        self.assertEqual(self.errors, [], '有 JS 错误：%s' % self.errors[:3])

    # ── 小工具 ───────────────────────────────────────────────
    def goto(self, path, wait=2200):
        """打开 BASE 下的一个路由并等到渲染稳定。

        不能用 wait_until='networkidle' 之后就断言 —— 页面是
        「先骨架后 API」两段渲染，networkidle 时第二段可能还没跑。
        """
        self.page.goto(URL + path, wait_until='networkidle')
        self.page.wait_for_timeout(wait)

    def blur(self):
        self.page.evaluate('() => document.activeElement && document.activeElement.blur()')

    def key(self, *keys):
        for k in keys:
            self.page.keyboard.press(k)

    def url_path(self):
        return self.page.url.split('/kb', 1)[-1]

    def toast(self):
        return self.page.evaluate(
            '() => (document.getElementById("toast") || {}).textContent || ""'
        )

    def scroll_top(self):
        return self.page.evaluate(
            '() => document.getElementById("view").scrollTop'
        )

    def cursor_row(self):
        return self.page.evaluate('''() => {
          const rows = [...document.querySelectorAll('#view [data-page]')]
            .filter(e => e.dataset.page && e.offsetParent && !e.matches('a.wikilink'));
          const i = rows.findIndex(r => r.classList.contains('kb-cursor'));
          return i < 0 ? null : { i, name: rows[i].dataset.page };
        }''')

    def wk_rows(self):
        return self.page.evaluate('''() => [...document.querySelectorAll('.wk-row')]
          .map(r => ({ k: r.dataset.k,
                       label: (r.querySelector('.wk-label') || {}).textContent || '' }))''')

    def which_key(self, *keys):
        self.blur()
        self.key(' ')
        for k in keys:
            self.page.wait_for_timeout(320)
            self.key(k)
        self.page.wait_for_timeout(320)
        return self.wk_rows()


# ══════════════════════════════════════════════════════════
# 1. leader / which-key
# ══════════════════════════════════════════════════════════

class TestLeader(Base):
    def test_space_opens_which_key_in_reading_view(self):
        self.goto('/page/KB')
        self.blur()
        self.key(' ')
        self.page.wait_for_timeout(500)
        self.assertTrue(self.wk_rows(), 'space 没打开 which-key')

    def test_space_opens_which_key_in_EDITOR(self):
        """回归陷阱：编辑器里 space 曾「开了立刻关」。

        根因是 <space> 在两处被处理（CodeMirror extraKeys 先开、
        文档冒泡再把它当成「leader 后的下一个键」关掉）。
        只测首页不会发现 —— 必须测编辑器 normal 模式。
        """
        self.goto('/edit/KB', wait=2600)
        self.page.locator('.CodeMirror').first.click()
        self.page.wait_for_timeout(300)
        self.key('Escape')
        self.page.wait_for_timeout(300)
        self.key(' ')
        self.page.wait_for_timeout(500)
        self.assertTrue(self.wk_rows(), '编辑器 normal 模式里 space 没打开 which-key')

    def test_which_key_has_no_duplicate_lhs(self):
        self.goto('/page/KB')
        dup = self.page.evaluate('''() => {
          const m = {};
          LEADER_SPEC.forEach(s => m[s.lhs] = (m[s.lhs] || 0) + 1);
          return Object.keys(m).filter(k => m[k] > 1);
        }''')
        self.assertEqual(dup, [], '同一 lhs 有多条定义：%s（会静默互相覆盖）' % dup)

    def test_every_which_key_leaf_has_a_run(self):
        self.goto('/page/KB')
        dead = self.page.evaluate('''() => LEADER_SPEC
          .filter(s => !(s.children) && !s.group && !s.run)
          .map(s => s.lhs)''')
        self.assertEqual(dead, [], '有 desc 没 run 的假条目：%s' % dead)

    def test_space_b_o_is_close_others_not_jump_back(self):
        """曾经 bo 被跳转键覆盖，缓冲组里「关闭其他缓冲」按不出来。"""
        self.goto('/page/KB')
        rows = self.which_key('b')
        labels = {r['k']: r['label'] for r in rows}
        self.assertIn('o', labels)
        self.assertIn('关闭其他', labels['o'], 'space b o 不是「关闭其他缓冲」')
        self.key('o')
        self.page.wait_for_timeout(900)
        self.assertIn('关闭其他缓冲', self.toast())


# ══════════════════════════════════════════════════════════
# 2. 列表光标（vim 的行光标）
# ══════════════════════════════════════════════════════════

class TestListCursor(Base):
    def test_j_k_move_cursor_and_enter_opens(self):
        self.goto('/all', wait=2600)
        self.blur()
        self.key('j')
        self.page.wait_for_timeout(300)
        self.assertIsNotNone(self.cursor_row(), 'j 没选中任何行')
        self.key('j')
        self.page.wait_for_timeout(300)
        self.assertEqual(self.cursor_row()['i'], 1)
        self.key('k')
        self.page.wait_for_timeout(300)
        self.assertEqual(self.cursor_row()['i'], 0)

        self.key('Enter')
        self.page.wait_for_timeout(1600)
        self.assertIn('/page/', self.url_path(), 'Enter 没打开选中行')

    def test_count_prefix_5j(self):
        """计数语义：没有光标时 5j 落到「第 5 行」（索引 4）——
        和按 1 次 j 落到第 1 行（索引 0）是一致的：
        光标初始在「第一行之前」，按 N 次就是往下走 N 行。"""
        self.goto('/all', wait=2600)
        self.blur()
        self.page.keyboard.type('5j')
        self.page.wait_for_timeout(500)
        self.assertEqual(self.cursor_row()['i'], 4, '5j 应该到第 5 行')

        # 已有光标时 3j 就是「往下 3 行」
        self.key('g')
        self.page.wait_for_timeout(300)
        self.page.keyboard.type('3j')
        self.page.wait_for_timeout(500)
        self.assertEqual(self.cursor_row()['i'], 3, '从首行 3j 应到索引 3')

    def test_G_and_g(self):
        self.goto('/all', wait=2600)
        self.blur()
        self.key('G')
        self.page.wait_for_timeout(500)
        total = self.page.evaluate('() => document.querySelectorAll("#view [data-page]").length')
        self.assertEqual(self.cursor_row()['i'], total - 1)
        self.key('g')
        self.page.wait_for_timeout(400)
        self.assertEqual(self.cursor_row()['i'], 0)

    def test_reading_page_scrolls_instead_of_list_cursor(self):
        """正文页底部有反链（也带 data-page），不能被当成列表 ——
        按数量判会把 j/k 变成「在反链之间跳」，正文读不下去。"""
        self.goto('/page/KB')
        self.blur()
        self.key('j')
        self.page.wait_for_timeout(400)
        self.assertGreater(self.scroll_top(), 0, '正文页 j 没滚动')
        self.assertFalse(self.page.evaluate('() => isListView()'))
        self.assertIsNone(self.cursor_row())


# ══════════════════════════════════════════════════════════
# 3. 跳转历史 Ctrl-o / Ctrl-i
# ══════════════════════════════════════════════════════════

class TestJumplist(Base):
    def _click_first_wikilink(self):
        self.page.evaluate(
            '() => { const a = [...document.querySelectorAll("a.wikilink[data-page]")]'
            '.find(x => x.dataset.page && x.dataset.page !== "KB"); a && a.click(); }'
        )
        self.page.wait_for_timeout(1800)

    def test_ctrl_o_goes_back_after_clicking_a_link(self):
        self.goto('/page/KB')
        before = self.url_path()
        self._click_first_wikilink()
        after = self.url_path()
        self.assertNotEqual(before, after, '链接没跳转，测不了 Ctrl-o')

        self.blur()
        self.key('Control+o')
        self.page.wait_for_timeout(1800)
        self.assertEqual(self.url_path(), before, 'Ctrl-o 没回到上一页')

    def test_ctrl_i_goes_forward_again(self):
        self.goto('/page/KB')
        before = self.url_path()
        self._click_first_wikilink()
        after = self.url_path()
        self.blur()
        self.key('Control+o')
        self.page.wait_for_timeout(1600)
        self.key('Control+i')
        self.page.wait_for_timeout(1600)
        self.assertEqual(self.url_path(), after)

    def test_jumplist_records_first_page(self):
        """首屏走的是 dispatch（不经 navigate）；不记它的话清单一开场是空的，
        Ctrl-o 无处可退 —— 这是第一版「按了完全没反应」的原因。"""
        self.goto('/page/KB')
        n = self.page.evaluate('() => state.jumplist.length')
        self.assertGreaterEqual(n, 1, '首屏没进 jumplist')


# ══════════════════════════════════════════════════════════
# 4. 阅读位置记忆
# ══════════════════════════════════════════════════════════

class TestScrollMemo(Base):
    def test_scroll_position_restored_after_coming_back(self):
        self.goto('/page/KB')
        self.page.evaluate('() => document.getElementById("view").scrollTop = 1200')
        self.page.wait_for_timeout(400)
        self.page.evaluate(
            '() => { const a = [...document.querySelectorAll("a.wikilink[data-page]")]'
            '.find(x => x.dataset.page && x.dataset.page !== "KB"); a && a.click(); }'
        )
        self.page.wait_for_timeout(1800)
        self.blur()
        self.key('Control+o')
        self.page.wait_for_timeout(2600)
        self.assertGreater(self.scroll_top(), 800, 'Ctrl-o 回来后位置丢了')

    def test_new_page_starts_at_top(self):
        """没有记忆时必须显式归零 —— innerHTML 换掉不会重置 scrollTop，
        上一页读到 1200 会让新页从 1200 开始。"""
        self.goto('/all', wait=2600)
        self.page.evaluate('() => document.getElementById("view").scrollTop = 900')
        self.page.wait_for_timeout(300)
        self.blur()
        self.key('j')
        self.page.wait_for_timeout(200)
        self.key('Enter')
        self.page.wait_for_timeout(2600)
        self.assertLess(self.scroll_top(), 60, '新页面继承了上一页的滚动位置')


# ══════════════════════════════════════════════════════════
# 5. 页内搜索 / 阅读动作
# ══════════════════════════════════════════════════════════

class TestPageSearch(Base):
    def test_slash_in_page_view_does_page_search_not_global(self):
        self.goto('/page/KB')
        self.blur()
        self.key('/')
        self.page.wait_for_timeout(500)
        self.page.keyboard.type('知识')
        self.page.wait_for_timeout(300)
        self.key('Enter')
        self.page.wait_for_timeout(1200)
        self.assertIn('页内搜索', self.toast(), '正文页 / 没走页内搜索')
        n = self.page.evaluate(
            '() => CSS.highlights.has("kb-hit") ? CSS.highlights.get("kb-hit").size : 0'
        )
        self.assertGreater(n, 0, '没有高亮命中')

    def test_n_advances_to_next_hit(self):
        """回归：第一版靠「命中在不在视口里」判下一个，命中本来就在
        视口内时永远判成第 1 个 —— n 三次都停在第 1 处。"""
        self.goto('/page/KB')
        self.blur()
        self.key('/')
        self.page.wait_for_timeout(500)
        self.page.keyboard.type('知识')
        self.page.wait_for_timeout(300)
        self.key('Enter')
        self.page.wait_for_timeout(1200)
        seen = []
        for _ in range(3):
            self.key('n')
            self.page.wait_for_timeout(600)
            seen.append(self.toast())
        self.assertEqual(len(set(seen)), len(seen), 'n 没有逐个前进：%s' % seen)
        self.assertIn('第 2/', seen[0])

    def test_no_hit_reports_cleanly(self):
        self.goto('/page/KB')
        self.blur()
        self.key('/')
        self.page.wait_for_timeout(500)
        self.page.keyboard.type('zzzznotfound')
        self.page.wait_for_timeout(300)
        self.key('Enter')
        self.page.wait_for_timeout(1000)
        self.assertIn('找不到', self.toast())

    def test_slash_in_list_view_still_global_search(self):
        self.goto('/all', wait=2600)
        self.blur()
        self.key('/')
        self.page.wait_for_timeout(400)
        self.page.keyboard.type('树莓')
        self.page.wait_for_timeout(300)
        self.key('Enter')
        self.page.wait_for_timeout(1600)
        self.assertIn('/search/', self.url_path())

    def test_ctrl_d_half_page(self):
        self.goto('/page/KB')
        self.blur()
        h = self.page.evaluate('() => document.getElementById("view").clientHeight')
        self.key('Control+d')
        self.page.wait_for_timeout(400)
        self.assertAlmostEqual(
            self.scroll_top(), h * 0.5, delta=h * 0.2, msg='Ctrl-d 不是半屏'
        )


# ══════════════════════════════════════════════════════════
# 6. 缓冲（:bd 语义）
# ══════════════════════════════════════════════════════════

class TestBuffer(Base):
    def test_q_closes_the_page_and_switches_away(self):
        """回归：q 只把名字从数组里删掉，正文还在屏幕上 = 关了个寂寞。"""
        self.goto('/page/KB')
        self.page.evaluate('() => navigate("page", "todo")')
        self.page.wait_for_timeout(2000)
        self.assertIn('/page/todo', self.url_path())
        self.blur()
        self.key('q')
        self.page.wait_for_timeout(1900)
        self.assertNotIn('/page/todo', self.url_path(), 'q 之后还留在被关掉的页面')
        self.assertIn('已关闭', self.toast())

    def test_bd_behaves_like_q(self):
        self.goto('/page/KB')
        self.page.evaluate('() => navigate("page", "todo")')
        self.page.wait_for_timeout(2000)
        self.blur()
        self.key(':')
        self.page.wait_for_timeout(300)
        self.page.keyboard.type('bd')
        self.key('Enter')
        self.page.wait_for_timeout(1900)
        self.assertNotIn('/page/todo', self.url_path())


# ══════════════════════════════════════════════════════════
# 7. 编辑器里的 / : ?（dialog.js）
# ══════════════════════════════════════════════════════════

class TestEditorDialog(Base):
    def test_open_dialog_exists(self):
        """模板曾只引 dialog.css 没引 dialog.js → cm.openDialog undefined，
        vim 退化成原生 prompt，pushInput 读 null.length 抛 TypeError。"""
        self.goto('/edit/KB', wait=2600)
        kind = self.page.evaluate('() => typeof window.cm.openDialog')
        self.assertEqual(kind, 'function', 'dialog.js 没加载')

    def test_slash_in_editor_opens_codemirror_dialog(self):
        self.goto('/edit/KB', wait=2600)
        self.page.locator('.CodeMirror').first.click()
        self.page.wait_for_timeout(300)
        self.key('Escape', '/')
        self.page.wait_for_timeout(400)
        visible = self.page.evaluate('''() => {
          const d = document.querySelector('.CodeMirror-dialog');
          return !!d && d.getBoundingClientRect().height > 0;
        }''')
        self.assertTrue(visible, '编辑器里 / 没弹出 CM dialog')
        self.key('Escape')


# ══════════════════════════════════════════════════════════
# 8. 离线（API 数据本地镜像）
# ══════════════════════════════════════════════════════════

class TestOffline(Base):
    def test_cached_page_readable_offline(self):
        self.goto('/page/KB', wait=3000)
        self.page.context.set_offline(True)
        try:
            self.goto('/page/KB', wait=3400)
            text = self.page.evaluate(
                '() => document.getElementById("view").innerText'
            )
            self.assertGreater(len(text), 500, '离线后正文没了')
            self.assertNotIn('读取失败', text)
        finally:
            self.page.context.set_offline(False)

    def test_uncached_page_reads_as_offline_not_as_failure(self):
        self.goto('/page/KB', wait=3000)
        self.page.context.set_offline(True)
        try:
            self.goto('/page/zzz-not-cached-xyz', wait=3400)
            text = self.page.evaluate(
                '() => document.getElementById("view").innerText'
            )
            self.assertIn('离线', text)
        finally:
            self.page.context.set_offline(False)


if __name__ == '__main__':
    unittest.main(verbosity=2)


# ══════════════════════════════════════════════════════════
# 9. 路由表 —— 部署前缀 / 深链接 / vim text object
#
# 这一节的三个 bug 都是「只有在某个具体 URL 或某个具体字符位置上
# 才会出现」的那一类，纯函数测不到：
#
#   ① 访问 /kb/theme 时模板把整段路径当部署前缀，manifest 与 sw.js
#      请求到 /kb/theme/… 被后端回落成 HTML（unsupported MIME type）
#   ② /kb/trash 没有 parseRoute 分支，深链接掉成首页，回收站看不到
#   ③ 编辑器里在空行按 ciw：expandWordUnderCursor 返回 null，
#      vim.js 直接读 null.start 抛 TypeError 把整个 keydown 打断
# ══════════════════════════════════════════════════════════

class TestRoutes(Base):
    def test_base_is_kb_on_command_only_routes(self):
        """theme / health / searches / trash / gantt 都必须算出 base=/kb。

        断言的是 __KB_BASE__ 这个「后端注入的前缀」而不是页面内容 ——
        base 错了会牵连 manifest/sw.js/API 三条路径，但页面本身看起来
        完全正常（静默失效）。原先的模板正则只认识 9 个内置词，
        上面五个全部算错。
        """
        for path in ('/theme', '/health', '/searches', '/trash', '/gantt'):
            self.page.goto(URL + path, wait_until='domcontentloaded')
            self.page.wait_for_timeout(1800)
            base = self.page.evaluate('() => window.__KB_BASE__')
            self.assertEqual(base, '/kb', '%s 的部署前缀算错：%r' % (path, base))

    def test_deep_link_health_opens_panel(self):
        """/kb/health 直接打开体检面板，不是一张空白首页。"""
        self.goto('/health', 3000)
        visible = self.page.evaluate(
            '() => {'
            '  const el = document.getElementById("kb-health");'
            '  if (!el) return false;'
            '  const cs = getComputedStyle(el);'
            '  const r = el.getBoundingClientRect();'
            '  return cs.display !== "none" && r.width > 0;'
            '}'
        )
        self.assertTrue(visible, '/kb/health 没打开体检面板')

    def test_deep_link_searches_opens_panel(self):
        """/kb/searches 直接打开常用搜索面板。"""
        self.goto('/searches', 3000)
        visible = self.page.evaluate(
            '() => {'
            '  const el = document.getElementById("kb-saved-search");'
            '  if (!el) return false;'
            '  const cs = getComputedStyle(el);'
            '  const r = el.getBoundingClientRect();'
            '  return cs.display !== "none" && r.width > 0;'
            '}'
        )
        self.assertTrue(visible, '/kb/searches 没打开常用搜索面板')

    def test_deep_link_trash_opens_overlay(self):
        """/kb/trash 要真的弹出回收站（之前没有 parseRoute 分支，掉成首页）。"""
        self.goto('/trash', 3000)
        opened = self.page.evaluate(
            '() => !!document.querySelector("#ov-trash.overlay.open")'
        )
        self.assertTrue(opened, '/kb/trash 没有打开回收站')

    def test_theme_route_does_not_flip_scheme(self):
        """/kb/theme 只是旧链接兜底，不能每次打开都翻转配色。

        原先 dispatch 里写的是 `case 'theme': toggleTheme()`，而 base 修好
        之后这个分支真的会被走到 —— 同一个 URL 刷两次颜色不一样。
        """
        self.page.goto(URL + '/', wait_until='domcontentloaded')
        self.page.wait_for_timeout(1800)
        first = self.page.evaluate(
            '() => document.documentElement.getAttribute("data-scheme")'
        )
        for _ in range(2):
            self.page.goto(URL + '/theme', wait_until='domcontentloaded')
            self.page.wait_for_timeout(1600)
        after = self.page.evaluate(
            '() => document.documentElement.getAttribute("data-scheme")'
        )
        self.assertEqual(first, after, '/kb/theme 把配色翻掉了')


class TestVimTextObjects(Base):
    """编辑器里在「找不到文本对象」的位置按 text object 不该抛异常。"""

    def _editor(self, wait=2600):
        self.page.goto(URL + '/edit/KB', wait_until='networkidle')
        self.page.wait_for_timeout(wait)
        self.page.click('.CodeMirror')
        self.page.wait_for_timeout(300)

    def test_ciw_on_empty_line_is_noop(self):
        """空行上按 ciw：vim 里什么也不做；这里必须不抛异常且按键流还活着。

        原先 vim.js 在没有文本对象可选的字符上直接 return null，
        但末尾两行无条件读 tmp.start → TypeError，整个 keydown 被打断，
        之后所有按键都进不了编辑器。
        """
        self._editor()
        self.page.evaluate(
            '() => {'
            '  const c = document.querySelector(".CodeMirror").CodeMirror;'
            '  let empty = -1;'
            '  for (let i = 0; i < c.lineCount(); i++) {'
            '    if (c.getLine(i).trim() === "") { empty = i; break; }'
            '  }'
            '  c.focus(); c.setCursor({line: empty < 0 ? 0 : empty, ch: 0});'
            '}'
        )
        self.page.wait_for_timeout(200)
        for k in ('c', 'i', 'w'):
            self.page.keyboard.type(k)
            self.page.wait_for_timeout(120)
        self.page.wait_for_timeout(300)
        line_before = self.page.evaluate(
            '() => document.querySelector(".CodeMirror").CodeMirror.getCursor().line'
        )
        self.page.keyboard.type('j')
        self.page.wait_for_timeout(300)
        line_after = self.page.evaluate(
            '() => document.querySelector(".CodeMirror").CodeMirror.getCursor().line'
        )
        self.assertNotEqual(line_before, line_after, '空行 ciw 之后按键流断了')
        self.assertEqual(self.errors, [], 'ciw 在空行抛了异常：%s' % self.errors[:2])

    def test_ciw_on_word_still_works(self):
        """修完空行 bug，正常位置上的 ciw 必须仍然生效（别过度修）。"""
        self._editor()
        self.page.evaluate(
            '() => {'
            '  const c = document.querySelector(".CodeMirror").CodeMirror;'
            '  let line = 0;'
            '  for (let i = 0; i < c.lineCount(); i++) {'
            '    if (c.getLine(i).trim().length > 3) { line = i; break; }'
            '  }'
            '  c.focus(); c.setCursor({line: line, ch: 1});'
            '}'
        )
        self.page.wait_for_timeout(200)
        before = self.page.evaluate(
            '() => document.querySelector(".CodeMirror").CodeMirror.getValue()'
        )
        for k in ('c', 'i', 'w'):
            self.page.keyboard.type(k)
            self.page.wait_for_timeout(120)
        self.page.keyboard.type('ZZ')
        self.page.wait_for_timeout(150)
        self.page.keyboard.press('Escape')
        self.page.wait_for_timeout(400)
        after = self.page.evaluate(
            '() => document.querySelector(".CodeMirror").CodeMirror.getValue()'
        )
        self.assertNotEqual(before, after, '正常位置上 ciw 失效了')


# ══════════════════════════════════════════════════════════
# 10. 编辑器 ex 命令 —— vim 的逃生出口
#
# `:q!` 是最常用的「改坏了想退出」的动作。原先 :q 与 :q! 行为完全一样
# （都只弹一句「有未保存改动」），用户根本退不出去。
#
# 注意：这一节的用例会**真的写库**（:wa 就是保存），所以每条都用
# 自己的临时页（__e2e-excmd-*），跑完立刻 DELETE 掉，
# 绝不碰任何真实笔记 —— 早前版本的用例直接改了 KB 页，写脏了生产数据。
# ══════════════════════════════════════════════════════════

class TestEditorExCommands(Base):
    """编辑器里 :q / :q! / :wa / :bd 的语义必须与 vim 一致。"""

    PAGE = '__e2e-excmd-scratch'

    def setUp(self):
        super().setUp()
        # 必须先落到应用里，页面才有 __KB_BASE__ 与同源 origin
        # （about:blank 上 fetch('/api/...') 会拼成 undefined/api/...）。
        self.page.goto(URL + '/', wait_until='domcontentloaded')
        self.page.wait_for_timeout(1200)
        self.api('POST', '/pages', {'name': self.PAGE, 'content': 'line one\n'})

    def tearDown(self):
        # 页面和**版本快照**都要删 —— 只删页面的话，page_versions 里会留下
        # 一条「没有对应页面的孤儿页名」，它会出现在回收站里（每跑一次 e2e
        # 就多 10 条）。这是本条用例自己抓到的残留（跑完 3 轮后回收站里
        # 出现 __e2e-excmd-scratch 10 条版本）。
        try:
            self.api('DELETE', '/pages/' + self.PAGE)
            self.api('DELETE', '/pages/' + self.PAGE + '/versions')
        except Exception:
            pass
        super().tearDown()

    def api(self, method, path, body=None):
        """直接用页面自己的 fetch 打 API，省得再引 requests。"""
        js = """async ([method, path, body]) => {
          const opts = {method, headers: {'Content-Type': 'application/json'}};
          if (body) opts.body = JSON.stringify(body);
          const r = await fetch(window.__KB_BASE__ + '/api' + path, opts);
          return {status: r.status, text: (await r.text()).slice(0, 120)};
        }"""
        return self.page.evaluate(js, [method, path, body])

    def _dirty_editor(self, wait=2600):
        """打开临时页并制造一处未保存改动。"""
        self.page.goto(URL + '/edit/' + self.PAGE, wait_until='networkidle')
        self.page.wait_for_timeout(wait)
        self.page.evaluate(
            '() => { const c = document.querySelector(".CodeMirror").CodeMirror;'
            ' c.focus(); c.setCursor({line: 0, ch: 0}); }'
        )
        self.page.wait_for_timeout(200)
        self.page.keyboard.type('i')
        self.page.wait_for_timeout(180)
        self.page.keyboard.type('QQ')
        self.page.wait_for_timeout(180)
        self.page.keyboard.press('Escape')
        self.page.wait_for_timeout(400)

    def _ex(self, cmd):
        self.page.keyboard.press(':')
        self.page.wait_for_timeout(300)
        self.page.keyboard.type(cmd)
        self.page.keyboard.press('Enter')
        self.page.wait_for_timeout(900)

    def _state(self):
        return self.page.evaluate(
            '() => ({ editing: !!window.state.editing,'
            '  toast: (document.getElementById("toast").innerText || "").trim(),'
            '  msg: ((document.querySelector(".cm-vim-message") || {}).innerText || "").trim() })'
        )

    def test_q_refuses_with_dirty_buffer(self):
        """:q 有改动时拒绝退出并提示 :q! —— 这是 vim 的 E37。"""
        self._dirty_editor()
        self._ex('q')
        st = self._state()
        self.assertTrue(st['editing'], ':q 在脏缓冲上不该退出')
        self.assertIn('未保存', st['toast'])

    def test_q_bang_discards_and_exits(self):
        """:q! 必须真的丢弃并退出（原先与 :q 无差别，是个死路）。"""
        self._dirty_editor()
        self._ex('q!')
        st = self._state()
        self.assertFalse(st['editing'], ':q! 没有退出编辑器')

    def test_wa_saves_without_exiting(self):
        """:wa（单缓冲时 == :w）保存但不退出。原先报 Not an editor command。"""
        self._dirty_editor()
        self._ex('wa')
        st = self._state()
        self.assertTrue(st['editing'], ':wa 不该退出编辑器')
        self.assertNotIn('Not an editor command', st['msg'])
        # 真落盘了没有
        saved = self.page.evaluate(
            """async (name) => {
              const r = await fetch(window.__KB_BASE__ + '/api/pages/' + name);
              const j = await r.json();
              return j.content || '';
            }""", self.PAGE
        )
        self.assertTrue(saved.startswith('QQ'), ':wa 没有真的保存：%r' % saved[:20])

    def test_bd_is_known_command(self):
        """:bd 必须被识别（原先报 Not an editor command）。"""
        self._dirty_editor()
        self._ex('bd')
        st = self._state()
        self.assertNotIn('Not an editor command', st['msg'])

# ══════════════════════════════════════════════════════════
# 11. 编辑器 gf 与行操作（:t / :m）—— nvim 的日常动作
#
# 两组都不落盘（gf 是导航，:t/:m 只改内存缓冲），
# 但为了不碰真实笔记，仍然用临时页 + 跑完删。
#
# 注意：这里所有 JS 都是**不含换行的单行字符串**。早先把带真实换行的
# Python 三引号字符串直接塞进 page.evaluate，JS 侧拿到的是一个字面里
# 带 U+000A 的字符串 —— 报 "Invalid or unexpected token"，7 条全挂。
# 换行一律用 String.fromCharCode(10) 或传参拼，别写 "\n"。
# ══════════════════════════════════════════════════════════

class TestEditorGfAndLineOps(Base):
    PAGE = '__e2e-lines-scratch'

    # 建临时页：内容在 Python 侧用 chr(10) 拼好，JS 只负责发请求
    CREATE_JS = (
        'async ([name, content]) => {'
        '  const r = await fetch(window.__KB_BASE__ + "/api/pages", {'
        '    method: "POST", headers: {"Content-Type": "application/json"},'
        '    body: JSON.stringify({name: name, content: content}), });'
        '  return r.status; }'
    )

    PURGE_JS = (
        'async (name) => {'
        '  const b = window.__KB_BASE__ + "/api/pages/" + name;'
        '  await fetch(b, {method: "DELETE"});'
        '  await fetch(b + "/versions", {method: "DELETE"});'
        '  return true; }'
    )

    OPEN_JS = (
        '(line) => {'
        '  const c = document.querySelector(".CodeMirror").CodeMirror;'
        '  c.focus(); c.setCursor({line: line, ch: 0}); }'
    )

    CM_JS = (
        '() => {'
        '  const c = document.querySelector(".CodeMirror").CodeMirror;'
        '  return {len: c.getValue().length, n: c.lineCount(),'
        '          cur: c.getCursor().line,'
        '          lines: c.getValue().split(String.fromCharCode(10))}; }'
    )

    CURSOR_ON_LINK_JS = (
        '() => {'
        '  const c = document.querySelector(".CodeMirror").CodeMirror;'
        '  const t = c.getLine(2);'
        '  c.setCursor({line: 2, ch: t.indexOf("[[")}); }'
    )

    def setUp(self):
        super().setUp()
        self.page.goto(URL + '/', wait_until='domcontentloaded')
        self.page.wait_for_timeout(1200)
        body = 'L0 first\nL1 second\nL2 third [[KB]]\nL3 fourth\n'
        st = self.page.evaluate(self.CREATE_JS, [self.PAGE, body])
        if st not in (200, 201):
            raise RuntimeError('建临时页失败: %s' % st)

    def tearDown(self):
        try:
            self.page.evaluate(self.PURGE_JS, self.PAGE)
        except Exception:
            pass
        super().tearDown()

    def _open(self, line=1):
        self.page.goto(URL + '/edit/' + self.PAGE, wait_until='domcontentloaded')
        self.page.wait_for_timeout(2600)
        self.page.evaluate(self.OPEN_JS, line)
        self.page.wait_for_timeout(250)

    def _cm(self):
        return self.page.evaluate(self.CM_JS)

    def _ex(self, cmd):
        self.page.keyboard.press(':')
        self.page.wait_for_timeout(280)
        self.page.keyboard.type(cmd)
        self.page.keyboard.press('Enter')
        self.page.wait_for_timeout(800)

    def _msg(self):
        return self.page.evaluate(
            '() => ((document.querySelector(".cm-vim-message") || {}).innerText || "").trim()'
        )

    def _gf(self):
        """g 与 f 必须分两次按：press('gf') 在 CodeMirror 的 vim 里算一个键。"""
        self.page.keyboard.type('g')
        self.page.wait_for_timeout(180)
        self.page.keyboard.type('f')

    # ── gf ──────────────────────────────────────────────
    def test_gf_opens_wikilink_under_cursor(self):
        """nvim 的 gf = 打开光标下的文件；这里「文件」就是 [[页面名]]。"""
        self._open(2)
        self.page.evaluate(self.CURSOR_ON_LINK_JS)
        self.page.wait_for_timeout(200)
        self._gf()
        self.page.wait_for_timeout(1200)
        st = self.page.evaluate('() => ({path: location.pathname, editing: !!window.state.editing})')
        self.assertTrue(st['path'].endswith('/page/KB'), 'gf 没跳到链接目标，停在 %s' % st['path'])
        self.assertFalse(st['editing'], 'gf 之后仍停在编辑态')

    def test_gf_without_link_reports(self):
        """光标不在链接上时要给提示，不能静默。"""
        self._open(0)
        self.page.evaluate('() => { const c = document.querySelector(".CodeMirror").CodeMirror; c.setCursor({line:0,ch:0}); }')
        self.page.wait_for_timeout(200)
        self._gf()
        self.page.wait_for_timeout(700)
        toast = self.page.evaluate('() => (document.getElementById("toast").innerText || "").trim()')
        self.assertIn('链接', toast)

    # ── :t（复制行）─────────────────────────────────────
    def test_t_dot_duplicates_current_line(self):
        """:t. 复制当前行到下一行 —— 行数 +1、原行内容重复一次。"""
        self._open(1)
        pre = self._cm()
        self._ex('t.')
        post = self._cm()
        self.assertEqual(post['n'], pre['n'] + 1, ':t. 行数没变')
        self.assertEqual(post['lines'][1], pre['lines'][1])
        self.assertEqual(post['lines'][2], pre['lines'][1], ':t. 没把 L1 复制到下一行')

    def test_t_zero_copies_to_top(self):
        """:t0 复制到文首。此前被 vim 的 ex 命令名正则吞成 "t0" 报未识别。"""
        self._open(2)
        pre = self._cm()
        self._ex('t0')
        post = self._cm()
        self.assertEqual(post['n'], pre['n'] + 1)
        self.assertEqual(post['lines'][0], pre['lines'][2], ':t0 没复制到第一行')
        self.assertEqual(post['cur'], 0, ':t0 之后光标不在第一行')
        self.assertNotIn('Not an editor command', self._msg())

    def test_t_dollar_copies_to_end(self):
        """:t$ 复制到文末，且**不能多出一个空行**（越界插入的老坑）。"""
        self._open(2)
        pre = self._cm()
        self._ex('t$')
        post = self._cm()
        self.assertEqual(post['n'], pre['n'] + 1)
        self.assertEqual(post['lines'][-1], pre['lines'][2], ':t$ 末行内容不对')
        self.assertEqual(post['lines'][-2], pre['lines'][-1], ':t$ 末尾多了空行')

    # ── :m（移动行）─────────────────────────────────────
    def test_m_plus_one_swaps_with_next_line(self):
        """:m+1 与下一行互换 —— 行数不变、内容互换。"""
        self._open(1)
        pre = self._cm()
        self._ex('m+1')
        post = self._cm()
        self.assertEqual(post['n'], pre['n'], ':m+1 行数变了')
        self.assertEqual(post['lines'][1], pre['lines'][2], ':m+1 没把 L1 下移')
        self.assertEqual(post['lines'][2], pre['lines'][1], ':m+1 没把 L2 上移')

    def test_m_zero_moves_to_top(self):
        """:m0 移到文首，行数不变。"""
        self._open(2)
        pre = self._cm()
        self._ex('m0')
        post = self._cm()
        self.assertEqual(post['n'], pre['n'])
        self.assertEqual(post['lines'][0], pre['lines'][2], ':m0 没移到第一行')
        self.assertEqual(post['cur'], 0)
        self.assertNotIn('Not an editor command', self._msg())

# ══════════════════════════════════════════════════════════
# 12. `:e!` 与缓冲/撤销一族 ex 别名
#
# `:e!` 曾经把 `!` 当成文件名，去打开一个叫「!」的页面 —— 404 之后
# openEditor 拿到空内容，**缓冲区被清空**，2172 字的正文变 0 字。
# 这是在探测「哪些 ex 命令没实现」时发现的：探测脚本以为是「未知命令」，
# 实际是**静默清空编辑器**，比未实现危险得多。
#
# 一组是 :e! 的两条路径（干净重读 / 脏缓冲丢弃改动重读），
# 一组是「runCommand 里早有实现、只是没在 ex 层注册名字」的别名。
# 都不落盘。
# ══════════════════════════════════════════════════════════

class TestEditorEditAndAliases(Base):
    PAGE = '__e2e-exalias-scratch'
    BODY = 'A first\nB second\nC third\n'

    CREATE_JS = (
        'async ([name, content]) => {'
        '  const r = await fetch(window.__KB_BASE__ + "/api/pages", {'
        '    method: "POST", headers: {"Content-Type": "application/json"},'
        '    body: JSON.stringify({name: name, content: content}), });'
        '  return r.status; }'
    )
    PURGE_JS = (
        'async (name) => {'
        '  const b = window.__KB_BASE__ + "/api/pages/" + name;'
        '  await fetch(b, {method: "DELETE"});'
        '  await fetch(b + "/versions", {method: "DELETE"});'
        '  return true; }'
    )
    CM_JS = (
        '() => {'
        '  const c = document.querySelector(".CodeMirror").CodeMirror;'
        '  return {len: c.getValue().length, n: c.lineCount(),'
        '          head: c.getValue().slice(0, 8),'
        '          msg: ((document.querySelector(".cm-vim-message") || {}).innerText || "").trim(),'
        '          toast: (document.getElementById("toast").innerText || "").trim()}; }'
    )

    def setUp(self):
        super().setUp()
        self.page.goto(URL + '/', wait_until='domcontentloaded')
        self.page.wait_for_timeout(1200)
        st = self.page.evaluate(self.CREATE_JS, [self.PAGE, self.BODY])
        if st not in (200, 201):
            raise RuntimeError('建临时页失败: %s' % st)

    def tearDown(self):
        try:
            self.page.evaluate(self.PURGE_JS, self.PAGE)
        except Exception:
            pass
        super().tearDown()

    def _open(self):
        self.page.goto(URL + '/edit/' + self.PAGE, wait_until='domcontentloaded')
        self.page.wait_for_timeout(2600)
        self.page.evaluate('() => { const c = document.querySelector(".CodeMirror").CodeMirror; c.focus(); c.setCursor({line:0,ch:0}); }')
        self.page.wait_for_timeout(200)

    def _ex(self, cmd):
        self.page.keyboard.press(':')
        self.page.wait_for_timeout(260)
        self.page.keyboard.type(cmd)
        self.page.keyboard.press('Enter')
        self.page.wait_for_timeout(900)

    def _st(self):
        return self.page.evaluate(self.CM_JS)

    # ── :e! ─────────────────────────────────────────
    def test_e_bang_reloads_without_clearing_buffer(self):
        """:e! 必须重读当前文件，**不能把缓冲清成 0 字**（曾经的静默清空）。"""
        self._open()
        pre = self._st()
        self.assertTrue(pre['len'] > 0, '打开就先失败了')
        self._ex('e!')
        post = self._st()
        self.assertEqual(post['len'], pre['len'], ':e! 之后长度变了：%d → %d' % (pre['len'], post['len']))
        self.assertEqual(post['n'], pre['n'], ':e! 之后行数变了')
        self.assertEqual(post['head'], self.BODY[:8], ':e! 之后内容不是原文件')
        self.assertNotIn('Not an editor command', post['msg'])

    def test_e_bang_discards_dirty_and_rereads(self):
        """:e! 在脏缓冲上 = 丢弃改动 + 重读（nvim 语义）。"""
        self._open()
        base = self._st()
        self.page.keyboard.type('i')
        self.page.wait_for_timeout(150)
        self.page.keyboard.type('ZZZ')
        self.page.wait_for_timeout(150)
        self.page.keyboard.press('Escape')
        self.page.wait_for_timeout(400)
        dirty = self._st()
        self.assertEqual(dirty['len'], base['len'] + 3, '没打进去')
        self._ex('e!')
        post = self._st()
        self.assertEqual(post['len'], base['len'], ':e! 没丢掉那 3 个字符')
        self.assertEqual(post['head'], self.BODY[:8], ':e! 没重读原文件')

    # ── 别名 ────────────────────────────────────────
    def test_buffer_aliases_recognized(self):
        """:bn/:bp/:ls/:only 都已在 runCommand 里实现，缺的只是 ex 层的名字。"""
        self._open()
        for cmd, must_in_toast in [('ls', '缓冲'), ('only', '缓冲'), ('bn', ''), ('bp', '')]:
            self._ex(cmd)
            st = self._st()
            self.assertNotIn('Not an editor command', st['msg'], ':%s 仍报未知命令' % cmd)
            if must_in_toast:
                self.assertIn(must_in_toast, st['toast'], ':%s 没有真正执行' % cmd)

    def test_update_semantics(self):
        """:up / :update = 有改动才写，没改动时明确说「没有改动」。"""
        self._open()
        self._ex('up')
        st = self._st()
        self.assertNotIn('Not an editor command', st['msg'])
        self.assertIn('没有改动', st['toast'])

    def test_undo_redo_aliases(self):
        """:undo / :earlier 与 :redo / :later —— 网页里最近的等价物。"""
        self._open()
        self.page.keyboard.type('i')
        self.page.wait_for_timeout(150)
        self.page.keyboard.type('Q')
        self.page.wait_for_timeout(150)
        self.page.keyboard.press('Escape')
        self.page.wait_for_timeout(400)
        after_edit = self._st()
        self._ex('undo')
        undone = self._st()
        self.assertLess(undone['len'], after_edit['len'], ':undo 没生效')
        self.assertNotIn('Not an editor command', undone['msg'])
        self._ex('redo')
        redone = self._st()
        self.assertEqual(redone['len'], after_edit['len'], ':redo 没生效')

# ══════════════════════════════════════════════════════════
# 13. gcc/gc 注释、编辑器内 Ctrl-o/Ctrl-i
#
# 两处都是「动作到了但被别的东西吃掉」：
#   · gcc —— CodeMirror 的 vim 没有 gc 系列（`gc` 只是个没人实现的 operator 名）
#   · Ctrl-o/Ctrl-i —— 应用级 capture 处理器无条件 preventDefault，抢走了
#     CodeMirror 自己的 jumpListWalk（阅读态这两个键是路由跳转，
#     编辑器里应当是**缓冲内**跳转，两套语义必须分开）
#
# gcc 的第二轮 bug 也是「参数没接」这一类：action 回调签名是
# (cm, actionArgs, vim)，`2gcc` 的行数在 actionArgs.repeat 里，
# 第一版写 `() =>` 把它丢掉了 —— 与 :q! 丢 `!`、:e! 丢 `!` 同源。
# ══════════════════════════════════════════════════════════

class TestEditorCommentAndJumplist(Base):
    PAGE = '__e2e-gcc-scratch'
    BODY = 'line one\nline two\nline three\nline four\n'

    CREATE_JS = (
        'async ([name, content]) => {'
        '  const r = await fetch(window.__KB_BASE__ + "/api/pages", {'
        '    method: "POST", headers: {"Content-Type": "application/json"},'
        '    body: JSON.stringify({name: name, content: content}), });'
        '  return r.status; }'
    )
    PURGE_JS = (
        'async (name) => {'
        '  const b = window.__KB_BASE__ + "/api/pages/" + name;'
        '  await fetch(b, {method: "DELETE"});'
        '  await fetch(b + "/versions", {method: "DELETE"});'
        '  return true; }'
    )
    LINES_JS = (
        '() => {'
        '  const c = document.querySelector(".CodeMirror").CodeMirror;'
        '  return c.getValue().split(String.fromCharCode(10)); }'
    )
    CUR_JS = (
        '() => {'
        '  const c = document.querySelector(".CodeMirror").CodeMirror;'
        '  return {line: c.getCursor().line,'
        '          visual: !!c.state.vim.visualMode,'
        '          toast: (document.getElementById("toast").innerText || "").trim()}; }'
    )
    VISUAL_JS = (
        '() => !!document.querySelector(".CodeMirror").CodeMirror.state.vim.visualMode'
    )

    def setUp(self):
        super().setUp()
        self.page.goto(URL + '/', wait_until='domcontentloaded')
        self.page.wait_for_timeout(1200)
        st = self.page.evaluate(self.CREATE_JS, [self.PAGE, self.BODY])
        if st not in (200, 201):
            raise RuntimeError('建临时页失败: %s' % st)

    def tearDown(self):
        try:
            self.page.evaluate(self.PURGE_JS, self.PAGE)
        except Exception:
            pass
        super().tearDown()

    def _open(self, line=0):
        self.page.goto(URL + '/edit/' + self.PAGE, wait_until='domcontentloaded')
        self.page.wait_for_timeout(2400)
        self.page.evaluate(
            '(l) => { const c = document.querySelector(".CodeMirror").CodeMirror;'
            ' c.focus(); c.setCursor({line: l, ch: 0}); }', line
        )
        self.page.wait_for_timeout(250)

    def _type_seq(self, seq, gap=200):
        """逐字按 —— press('gcc') 会被 CodeMirror 当成一个键。"""
        for chx in seq:
            self.page.keyboard.type(chx)
            self.page.wait_for_timeout(gap)
        self.page.wait_for_timeout(400)

    def _lines(self):
        return self.page.evaluate(self.LINES_JS)

    def _cur(self):
        return self.page.evaluate(self.CUR_JS)

    # ── gcc ─────────────────────────────────────────
    def test_gcc_comments_and_uncomments_line(self):
        """gcc 注释当前行（Markdown 用 <!-- -->），再按一次取消。"""
        self._open(0)
        pre = self._lines()
        self._type_seq('gcc')
        post = self._lines()
        self.assertTrue(post[0].startswith('<!--'), 'gcc 没注释：%r' % post[0])
        self.assertTrue(post[0].rstrip().endswith('-->'), 'gcc 没闭合注释：%r' % post[0])
        self.assertEqual(post[1], pre[1], 'gcc 改到了别的行')
        self._type_seq('gcc')
        back = self._lines()
        self.assertEqual(back[0], pre[0], '再按 gcc 没取消注释')

    def test_gcc_count_comments_multiple_lines(self):
        """`2gcc` 注释两行 —— count 在 actionArgs.repeat 里，回调签名必须接。"""
        self._open(0)
        self.page.keyboard.type('2')
        self.page.wait_for_timeout(300)
        self._type_seq('gcc')
        post = self._lines()
        self.assertTrue(post[0].startswith('<!--'), '2gcc 第 1 行没注释')
        self.assertTrue(post[1].startswith('<!--'), '2gcc 第 2 行没注释（count 被丢掉了）')
        self.assertFalse(post[2].startswith('<!--'), '2gcc 多注释了第 3 行')

    def test_visual_gc_comments_selection(self):
        """visual 里 V j gc 注释选区并退出 visual。"""
        self._open(0)
        self._type_seq('V', gap=250)
        self._type_seq('j', gap=250)
        self._type_seq('gc')
        post = self._lines()
        self.assertTrue(post[0].startswith('<!--'), 'V gc 第 1 行没注释')
        self.assertTrue(post[1].startswith('<!--'), 'V gc 第 2 行没注释')
        self.assertFalse(post[2].startswith('<!--'), 'V gc 多注释了第 3 行')
        self.assertFalse(self.page.evaluate(self.VISUAL_JS), 'gc 之后还在 visual 模式')

    # ── Ctrl-o / Ctrl-i ─────────────────────────────
    def test_editor_ctrl_o_ctrl_i_walks_jumplist(self):
        """编辑器里 Ctrl-o / Ctrl-i 必须归 CodeMirror 的 jumpListWalk ——
        应用级 capture 处理器曾经无条件抢走它们，于是按了光标不动。"""
        self._open(0)
        a = self._cur()['line']
        self.page.keyboard.type('G')
        self.page.wait_for_timeout(600)
        g = self._cur()['line']
        self.assertNotEqual(g, a, 'G 没跳到最后一行')
        self.page.keyboard.press('Control+o')
        self.page.wait_for_timeout(700)
        back = self._cur()['line']
        self.assertEqual(back, a, 'Ctrl-o 没回到原位（被应用级 jumplist 抢走了）')
        self.page.keyboard.press('Control+i')
        self.page.wait_for_timeout(700)
        fwd = self._cur()['line']
        self.assertEqual(fwd, g, 'Ctrl-i 没回到 G 的位置')

# ══════════════════════════════════════════════════════════
# 14. 折叠 za/zo/zc/zR/zM + :fold
#
# CodeMirror 的 vim 只有 zz/zt/zb（滚动定位），整个 za 系列都没有；
# 而 foldcode.js + markdown-fold.js 这两个 addon **一个都没引** ——
# 所以即便手动调 cm.foldCode 也不存在（`typeof cm.foldCode === 'undefined'`）。
#
# 另一处 bug 值得记：`cm.foldCode` 在「这一行没有可折的 block」时
# **静默返回、不抛异常**，所以「循环跑了多少行」不等于「折上了多少处」。
# 第一版 zM 在 71 行的文档上报「已折叠 71 处」，实际只折了 10 处。
# 现在按 `__isFold` 标记数变化来判定。
# ══════════════════════════════════════════════════════════

class TestEditorFolding(Base):
    PAGE = '__e2e-fold-scratch'
    BODY = '# H1 top\nbody one\nbody two\n## H2 sub\nsub body\nbody three\n## H2 sub2\nmore\n'
    HEAD_LINE = 0

    CREATE_JS = (
        'async ([name, content]) => {'
        '  const r = await fetch(window.__KB_BASE__ + "/api/pages", {'
        '    method: "POST", headers: {"Content-Type": "application/json"},'
        '    body: JSON.stringify({name: name, content: content}), });'
        '  return r.status; }'
    )
    PURGE_JS = (
        'async (name) => {'
        '  const b = window.__KB_BASE__ + "/api/pages/" + name;'
        '  await fetch(b, {method: "DELETE"});'
        '  await fetch(b + "/versions", {method: "DELETE"});'
        '  return true; }'
    )
    FOLD_JS = (
        '() => {'
        '  const c = document.querySelector(".CodeMirror").CodeMirror;'
        '  return {folds: (c.getAllMarks() || []).filter(m => m.__isFold).length,'
        '          lineCount: c.lineCount(),'
        '          hasFoldCode: typeof c.foldCode,'
        '          toast: (document.getElementById("toast").innerText || "").trim(),'
        '          msg: ((document.querySelector(".cm-vim-message") || {}).innerText || "").trim()}; }'
    )

    def setUp(self):
        super().setUp()
        self.page.goto(URL + '/', wait_until='domcontentloaded')
        self.page.wait_for_timeout(1200)
        st = self.page.evaluate(self.CREATE_JS, [self.PAGE, self.BODY])
        if st not in (200, 201):
            raise RuntimeError('建临时页失败: %s' % st)

    def tearDown(self):
        try:
            self.page.evaluate(self.PURGE_JS, self.PAGE)
        except Exception:
            pass
        super().tearDown()

    def _open(self, line=HEAD_LINE):
        self.page.goto(URL + '/edit/' + self.PAGE, wait_until='domcontentloaded')
        self.page.wait_for_timeout(2400)
        self.page.evaluate(
            '(l) => { const c = document.querySelector(".CodeMirror").CodeMirror;'
            ' c.focus(); c.setCursor({line: l, ch: 0}); }', line
        )
        self.page.wait_for_timeout(250)

    def _seq(self, keys, gap=230):
        for chx in keys:
            self.page.keyboard.type(chx)
            self.page.wait_for_timeout(gap)
        self.page.wait_for_timeout(500)

    def _fold_state(self):
        return self.page.evaluate(self.FOLD_JS)

    def test_fold_addon_is_loaded(self):
        """foldcode.js 必须在模板里加载，否则 cm.foldCode 是 undefined。"""
        self._open()
        st = self._fold_state()
        self.assertEqual(st['hasFoldCode'], 'function', 'cm.foldCode 不存在：foldcode.js 没加载')

    def test_za_toggles_fold(self):
        """za 折叠当前标题块，再按一次展开。"""
        self._open(0)
        self.assertEqual(self._fold_state()['folds'], 0)
        self._seq('za')
        self.assertEqual(self._fold_state()['folds'], 1, 'za 没折上')
        self._seq('za')
        self.assertEqual(self._fold_state()['folds'], 0, '再按 za 没展开')

    def test_zc_zo_and_zM_zR(self):
        """zc 关闭 / zo 打开 / zM 全折 / zR 全展开，计数必须是真的。"""
        self._open(0)
        self._seq('zc')
        self.assertEqual(self._fold_state()['folds'], 1)
        self._seq('zo')
        self.assertEqual(self._fold_state()['folds'], 0)

        self._seq('zM', gap=250)
        st = self._fold_state()
        # 本页有 3 个标题（# H1 / ## H2 / ## H2 sub2），每个至少折出 1 处
        self.assertGreaterEqual(st['folds'], 2, 'zM 没有折叠出多处：%r' % st)
        # 计数不能谎报（曾报「已折叠 71 处」而实际只有 10）
        self.assertNotIn('已折叠 71', st['toast'], 'zM 计数用行数冒充处数')

        self._seq('zR', gap=250)
        self.assertEqual(self._fold_state()['folds'], 0, 'zR 没全部展开')

    def test_ex_fold_and_unfold(self):
        """:fold / :unfold 与 za 同一套实现（ex 层也要认得）。"""
        self._open(0)
        self.page.keyboard.press(':')
        self.page.wait_for_timeout(280)
        self.page.keyboard.type('fold')
        self.page.keyboard.press('Enter')
        self.page.wait_for_timeout(800)
        st = self._fold_state()
        self.assertEqual(st['folds'], 1, ':fold 没生效')
        self.assertNotIn('Not an editor command', st['msg'])
        self.page.keyboard.press(':')
        self.page.wait_for_timeout(280)
        self.page.keyboard.type('unfold')
        self.page.keyboard.press('Enter')
        self.page.wait_for_timeout(800)
        self.assertEqual(self._fold_state()['folds'], 0, ':unfold 没生效')

# ══════════════════════════════════════════════════════════
# 15. 触屏动作坞的可达性 + 目录面板的会话记忆
#
# 两个都是「只有真的用手指点一次才会暴露」的问题：
#
# ① #fxdock 的 z-index 是 55，而 KBPanel 的 overlay 是 90、scrim 是 89。
#    390×844 上打开 /page/KB，目录面板（240×506）正好压在动作坞展开区，
#    12 个按钮里 8 个的 elementFromPoint 命中的是 .toc-item —— 真实 tap
#    直接超时。触屏上动作坞是**唯一**命令入口，被盖住等于入口没了。
#
# ② TOC 关掉后每次 `api.on('page')` 又把它弹出来，没有会话记忆。
#    用户在不想要目录的页面上得反复关同一个东西。
#
# 注意：`locator.tap()` 需要 hasTouch context。基类的 page 是
# `new_page()` 建的无 touch 页面，直接 tap 会报
# "The page does not support tap" —— 与 bug 本身无关的错误，
# 所以这里统一用自建的 touch context。
# ══════════════════════════════════════════════════════════

MOBILE_UA = ('Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 '
             '(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36')


class TestTouchDockReachable(Base):
    """触屏：动作坞必须点得动，且不被任何面板盖住。"""

    def _touch_page(self):
        ctx = self._browser.new_context(**{
            'viewport': {'width': 390, 'height': 844},
            'has_touch': True, 'is_mobile': True,
            'user_agent': MOBILE_UA,
        })
        pg = ctx.new_page()
        pg.goto(URL + '/page/KB', wait_until='domcontentloaded')
        pg.wait_for_timeout(3200)
        return ctx, pg

    def test_dock_zindex_above_overlay_panels(self):
        """动作坞的 z-index 必须高于 KBPanel 的 overlay(90)。"""
        ctx, pg = self._touch_page()
        try:
            z = pg.evaluate(
                '() => ({dock: getComputedStyle(document.getElementById("fxdock")).zIndex,'
                '        toc: (document.getElementById("kb-toc")'
                '              ? getComputedStyle(document.getElementById("kb-toc")).zIndex : null)})'
            )
            self.assertEqual(z['dock'], '96', '动作坞 z-index 不是 96: %r' % z)
            if z['toc'] is not None:
                self.assertGreater(int(z['dock']), int(z['toc']),
                                   '动作坞被目录面板盖住: %r' % z)
        finally:
            ctx.close()

    def test_dock_buttons_all_clickable(self):
        """12 个按钮的 elementFromPoint 必须命中按钮本身（TOC 开着的前提下）。"""
        ctx, pg = self._touch_page()
        try:
            pg.locator('#fx-toggle').first.tap()
            pg.wait_for_timeout(800)
            r = pg.evaluate(
                '() => {'
                '  const p = document.getElementById("fx-panel");'
                '  const out = [];'
                '  for (const t of Array.from(p.querySelectorAll(".fx-btn"))) {'
                '    const rc = t.getBoundingClientRect();'
                '    const el = document.elementFromPoint(rc.left + rc.width / 2, rc.top + rc.height / 2);'
                '    out.push({fx: t.dataset.fx, ok: !!(el && el.closest(".fx-btn"))});'
                '  }'
                '  return {blocked: out.filter(o => !o.ok), total: out.length}; }'
            )
            self.assertEqual(r['blocked'], [], '这些按钮被别的元素盖住了: %r' % (r['blocked'],))
            self.assertEqual(r['total'], 12)
        finally:
            ctx.close()

    def test_real_tap_on_dock_opens_picker(self):
        """真实 tap 点「跳转页面」必须真的打开 #ov-pick。"""
        ctx, pg = self._touch_page()
        try:
            pg.locator('#fx-toggle').first.tap()
            pg.wait_for_timeout(800)
            pg.locator('#fx-panel .fx-btn').first.tap(timeout=6000)
            pg.wait_for_timeout(1300)
            vis = pg.evaluate(
                '() => { const o = document.querySelector("#ov-pick");'
                ' return !!o && getComputedStyle(o).display !== "none"; }'
            )
            self.assertTrue(vis, 'tap 了「跳转页面」但 #ov-pick 没出现')
        finally:
            ctx.close()


class TestTocRemembersClose(Base):
    """关掉目录后不该在每次切页时又弹回来。"""

    def setUp(self):
        super().setUp()
        self.page.goto(URL + '/page/KB', wait_until='domcontentloaded')
        self.page.wait_for_timeout(3000)
        self.page.evaluate('() => { try { localStorage.removeItem("kb-toc-off"); } catch (e) {} }')

    def _toc_display(self):
        return self.page.evaluate(
            '() => { const e = document.getElementById("kb-toc");'
            ' return e ? getComputedStyle(e).display : "MISSING"; }'
        )

    def _close_toc(self):
        self.page.evaluate('() => { const x = document.querySelector("#kb-toc .kbp-x"); if (x) x.click(); }')
        self.page.wait_for_timeout(600)

    def test_closing_toc_persists_across_navigation(self):
        """点 × 关掉后切三页都不能自己回来，且 localStorage 记下了。"""
        self._close_toc()
        self.assertEqual(self.page.evaluate('() => localStorage.getItem("kb-toc-off")'), '1',
                         '关闭后没记住')
        for tgt in ['kb-issue', 'KB', 'kb-docker']:
            self.page.evaluate('(n) => navigate("page", n)', tgt)
            self.page.wait_for_timeout(2000)
            self.assertEqual(self._toc_display(), 'none', '切到 %s 后目录又弹回来了' % tgt)

    def test_toc_command_clears_memory(self):
        """:toc 重新打开时清掉记忆，之后切页照常出现。"""
        self._close_toc()
        self.assertEqual(self._toc_display(), 'none')
        self.page.evaluate('() => runCommand("toc", [])')
        self.page.wait_for_timeout(1200)
        self.assertNotEqual(self._toc_display(), 'none', ':toc 没能重新打开')
        self.assertIsNone(self.page.evaluate('() => localStorage.getItem("kb-toc-off")'),
                          ':toc 没清掉关闭记忆')
        self.page.evaluate('() => navigate("page", "kb-issue")')
        self.page.wait_for_timeout(2000)
        self.assertNotEqual(self._toc_display(), 'none', '重新打开后切页应继续显示')

    def test_toc_still_clickable_on_desktop(self):
        """z-index 调整不能反过来盖住目录面板自己的条目。"""
        self.page.set_viewport_size({'width': 1280, 'height': 900})
        self.page.goto(URL + '/page/KB', wait_until='domcontentloaded')
        self.page.wait_for_timeout(3000)
        r = self.page.evaluate(
            '() => {'
            '  const el = document.getElementById("kb-toc");'
            '  if (!el) return {missing: true};'
            '  const items = el.querySelectorAll(".toc-item");'
            '  if (!items.length) return {items: 0};'
            '  const rc = items[0].getBoundingClientRect();'
            '  const hit = document.elementFromPoint(rc.left + rc.width / 2, rc.top + rc.height / 2);'
            '  return {items: items.length, ok: !!(hit && hit.closest(".toc-item"))}; }'
        )
        self.assertFalse(r.get('missing'), '桌面上目录面板不见了')
        self.assertTrue(r['ok'], '目录项被别的东西盖住了: %r' % (r,))

# ══════════════════════════════════════════════════════════
# 16. 数字盘（#numgrid）—— 触屏的主跳转入口
#
# 动作坞里「数字跳转」打开的那个九宫格，是触屏上替代 `gg` / 数字前缀的
# 唯一办法（没有键盘）。三处问题：
#
# ① z-index 70 < TOC 90 —— 目录开着时它被压住
# ② 遮罩区点不动关不掉（触屏没有 Esc，唯一出口是右上角的小 ×）
# ③ 打开时背景没锁，在遮罩上滑动会滚到下面的正文
#
# 顺带锁住「阅读态横滑切缓冲」这条已有的触屏手势 —— 它只有用真实
# touch 事件序列才测得出来（keyboard 按不出来）。
# ══════════════════════════════════════════════════════════

MOBILE_UA2 = ('Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36')


class TestTouchNumGrid(Base):
    """触屏数字盘：层级、关闭方式、背景锁。"""

    def _touch_page(self, path='/page/KB'):
        ctx = self._browser.new_context(**{
            'viewport': {'width': 390, 'height': 844},
            'has_touch': True, 'is_mobile': True,
            'user_agent': MOBILE_UA2,
        })
        pg = ctx.new_page()
        pg.goto(URL + path, wait_until='domcontentloaded')
        pg.wait_for_timeout(3200)
        return ctx, pg

    OPEN_JS = (
        '() => {'
        '  const t = Array.from(document.querySelectorAll("#fx-panel .fx-btn"))'
        '        .find(x => x.dataset.fx === "1-9");'
        '  if (t) t.dispatchEvent(new MouseEvent("click", {bubbles: true}));'
        '  return !!t; }'
    )

    def _open_grid(self, pg):
        pg.locator('#fx-toggle').first.tap()
        pg.wait_for_timeout(600)
        ok = pg.evaluate(self.OPEN_JS)
        pg.wait_for_timeout(900)
        return ok

    def test_numgrid_zindex_above_toc(self):
        """数字盘层级必须高于目录面板，否则被压住就点不动。"""
        ctx, pg = self._touch_page()
        try:
            self._open_grid(pg)
            z = pg.evaluate(
                '() => ({grid: getComputedStyle(document.getElementById("numgrid")).zIndex,'
                '        toc: (document.getElementById("kb-toc")'
                '              ? getComputedStyle(document.getElementById("kb-toc")).zIndex : null)})'
            )
            self.assertEqual(z['grid'], '97', '数字盘 z-index 不是 97: %r' % z)
            if z['toc'] is not None:
                self.assertGreater(int(z['grid']), int(z['toc']))
        finally:
            ctx.close()

    def test_all_nine_cells_clickable_and_run(self):
        """9 个格子都可点，且 tap 第 5 格真的跳过去。"""
        ctx, pg = self._touch_page()
        try:
            self._open_grid(pg)
            r = pg.evaluate(
                '() => {'
                '  const cells = Array.from(document.querySelectorAll("#numgrid .ng-key"));'
                '  const blocked = cells.filter(c => {'
                '    const rc = c.getBoundingClientRect();'
                '    const el = document.elementFromPoint(rc.left + rc.width / 2, rc.top + rc.height / 2);'
                '    return !(el && el.closest(".ng-key") === c);'
                '  }).map(c => c.dataset.n);'
                '  return {total: cells.length, blocked: blocked,'
                '          names: cells.map(c => c.dataset.n)}; }'
            )
            self.assertEqual(r['total'], 9, '数字盘不是 9 格: %r' % r)
            self.assertEqual(r['blocked'], [], '这些格子被盖住了: %r' % (r['blocked'],))
            before = pg.evaluate('() => location.pathname')
            pg.locator('#numgrid .ng-key').nth(4).tap(timeout=6000)
            pg.wait_for_timeout(1200)
            self.assertNotEqual(pg.evaluate('() => location.pathname'), before,
                                'tap 第 5 格没跳转')
            self.assertEqual(pg.evaluate('() => document.getElementById("numgrid").className'), '',
                             '跳转后数字盘没关闭')
        finally:
            ctx.close()

    def test_backdrop_tap_closes(self):
        """点遮罩空白处必须关闭 —— 触屏没有 Esc 键。"""
        ctx, pg = self._touch_page()
        try:
            self._open_grid(pg)
            geo = pg.evaluate(
                '() => { const sh = document.querySelector("#numgrid .ng-sheet");'
                ' return {top: Math.round(sh.getBoundingClientRect().top)}; }'
            )
            self.assertGreater(geo['top'], 60, 'sheet 位置异常: %r' % geo)
            pg.touchscreen.tap(195, max(20, geo['top'] - 100))
            pg.wait_for_timeout(700)
            self.assertEqual(pg.evaluate('() => document.getElementById("numgrid").className'), '',
                             '点遮罩空白处没关闭数字盘')
        finally:
            ctx.close()

    def test_background_scroll_locked_while_open(self):
        """数字盘打开时背景滚动被锁（body.ng-open）。"""
        ctx, pg = self._touch_page()
        try:
            self._open_grid(pg)
            locked = pg.evaluate(
                '() => ({cls: document.body.className,'
                '        overflow: getComputedStyle(document.body).overflow})'
            )
            self.assertIn('ng-open', locked['cls'], '打开数字盘时没加 ng-open')
            self.assertEqual(locked['overflow'], 'hidden')
            pg.evaluate('() => closeNumGrid()')
            pg.wait_for_timeout(300)
            self.assertNotIn('ng-open',
                             pg.evaluate('() => document.body.className'), '关闭后没解锁')
        finally:
            ctx.close()


class TestTouchSwipeBuffers(Base):
    """阅读态左右横滑 = 切缓冲；编辑器里不能误切。"""

    def _touch_page(self, path):
        ctx = self._browser.new_context(**{
            'viewport': {'width': 390, 'height': 844},
            'has_touch': True, 'is_mobile': True,
            'user_agent': MOBILE_UA2,
        })
        pg = ctx.new_page()
        cdp = ctx.new_cdp_session(pg)
        pg.goto(URL + path, wait_until='domcontentloaded')
        pg.wait_for_timeout(3000)
        pg.evaluate('() => { const x = document.querySelector("#kb-toc .kbp-x"); if (x) x.click(); }')
        pg.wait_for_timeout(400)
        return ctx, pg, cdp

    @staticmethod
    def _swipe(cdp, x0, y0, x1, y1, steps=10):
        cdp.send('Input.dispatchTouchEvent',
                 {'type': 'touchStart', 'touchPoints': [{'x': x0, 'y': y0}]})
        for i in range(1, steps + 1):
            cdp.send('Input.dispatchTouchEvent',
                     {'type': 'touchMove',
                      'touchPoints': [{'x': int(x0 + (x1 - x0) * i / steps),
                                       'y': int(y0 + (y1 - y0) * i / steps)}]})
        cdp.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})

    def test_swipe_switches_buffer_in_reader(self):
        """左滑 = 下一个缓冲，右滑 = 上一个（用真实 touch 序列）。"""
        ctx, pg, cdp = self._touch_page('/page/KB')
        try:
            pg.evaluate('() => addBuffer("kb-issue")')
            pg.wait_for_timeout(400)
            self.assertEqual(pg.evaluate('() => location.pathname'), '/kb/page/KB')
            self._swipe(cdp, 300, 400, 120, 400)
            pg.wait_for_timeout(1200)
            self.assertEqual(pg.evaluate('() => location.pathname'), '/kb/page/kb-issue',
                             '左滑没切到下一个缓冲')
            self._swipe(cdp, 120, 400, 320, 400)
            pg.wait_for_timeout(1200)
            self.assertEqual(pg.evaluate('() => location.pathname'), '/kb/page/KB',
                             '右滑没切回上一个缓冲')
        finally:
            ctx.close()

    def test_swipe_does_not_switch_buffer_in_editor(self):
        """编辑器里横滑不能切缓冲 —— 正在打字时丢内容比手势失效糟糕得多。"""
        ctx, pg, cdp = self._touch_page('/edit/KB')
        try:
            pg.locator('.CodeMirror').first.tap()
            pg.wait_for_timeout(400)
            pg.keyboard.type('QQ')
            pg.wait_for_timeout(500)
            pre_len = pg.evaluate('() => window.cm.getValue().length')
            self._swipe(cdp, 300, 400, 120, 400)
            pg.wait_for_timeout(1200)
            st = pg.evaluate(
                '() => ({path: location.pathname, editing: !!window.state.editing,'
                '        len: window.cm ? window.cm.getValue().length : -1})'
            )
            self.assertEqual(st['path'], '/kb/edit/KB', '编辑器里横滑被误判成切缓冲')
            self.assertTrue(st['editing'], '横滑把编辑态弄丢了')
            self.assertEqual(st['len'], pre_len, '横滑之后缓冲内容变了')
        finally:
            ctx.close()

# ══════════════════════════════════════════════════════════
# 17. 行操作 ex 命令族（:d / :g / :normal / :j / :y / :pu）
#
# CodeMirror 的 vim 把「行操作」这一族整个漏了：defaultExCommandMap 里
# 没有 delete / normal / join / put。后果最严重的是 `:g/xxx/d` ——
# global 的实现是「对每个匹配行拼一条 `:{行号}{cmd}` 再交给分发器」，
# 于是 `:g/a/d` 会去执行 `:2d`，`d` 没注册 → **一个字符都不删**，
# 只在状态区弹一句 `Not an editor command ":2d"`。
#
# 这一节全部落在真实缓冲区内容 / 页面 API 回读上，不看 toast。
# ══════════════════════════════════════════════════════════

EXLINE_SCRATCH = ('# 测试用' + chr(10) + 'alpha 行' + chr(10) + 'beta 行' + chr(10)
                  + 'alpha 再一行' + chr(10) + 'gamma 行' + chr(10)
                  + 'delta aa 行' + chr(10))


class TestEditorExLineOps(Base):
    """行操作 ex 命令族：删除 / 全局 / normal / 合并 / 复制粘贴。"""

    PAGE = '__e2e-exlineops-scratch'

    CREATE_JS = (
        'async ([name, content]) => {'
        '  const b = window.__KB_BASE__ + "/api/pages/" + name;'
        '  const r = await fetch(window.__KB_BASE__ + "/api/pages", {'
        '    method: "POST", headers: {"Content-Type": "application/json"},'
        '    body: JSON.stringify({name: name, content: content}), });'
        '  let listed = 200;'
        '  if (r.status !== 200) {'
        '    listed = (await fetch(window.__KB_BASE__ + "/api/pages/" + name)).status;'
        '  }'
        '  return {created: r.status, exists: listed}; }'
    )

    PURGE_JS = (
        'async (name) => {'
        '  const b = window.__KB_BASE__ + "/api/pages/" + name;'
        '  await fetch(b, {method: "DELETE"});'
        '  await fetch(b + "/versions", {method: "DELETE"});'
        '  return true; }'
    )

    EXISTS_JS = (
        'async (name) => {'
        '  const r = await fetch(window.__KB_BASE__ + "/api/pages/" + name);'
        '  return r.status; }'
    )

    RESET_JS = (
        '([text, cur]) => {'
        '  const w = document.querySelector(".CodeMirror");'
        '  if (!w) return false;'
        '  w.CodeMirror.setValue(text);'
        '  w.CodeMirror.setCursor({line: cur, ch: 0});'
        '  w.CodeMirror.focus();'
        '  return true; }'
    )

    CURSOR_JS = (
        '(line) => {'
        '  const w = document.querySelector(".CodeMirror");'
        '  w.CodeMirror.focus(); w.CodeMirror.setCursor({line: line, ch: 0}); }'
    )

    READ_JS = (
        '() => {'
        '  const w = document.querySelector(".CodeMirror");'
        '  if (!w) return {text: "", lines: -1, msg: "no editor"};'
        '  const m = w.querySelector(".cm-vim-message");'
        '  return {text: w.CodeMirror.getValue(), n: w.CodeMirror.lineCount(),'
        '          msg: m ? m.innerText.trim().slice(0, 80) : null}; }'
    )

    CONFIRM_JS = (
        '() => {'
        '  const o = document.getElementById("ov-pick");'
        '  if (!o) return false;'
        '  const st = getComputedStyle(o);'
        '  return st.display !== "none" && st.visibility !== "hidden"; }'
    )

    def setUp(self):
        super().setUp()
        self.page.goto(URL + '/', wait_until='domcontentloaded')
        self.page.wait_for_timeout(1200)
        self.page.evaluate(self.PURGE_JS, self.PAGE)
        st = self.page.evaluate(self.CREATE_JS, [self.PAGE, EXLINE_SCRATCH])
        self.assertEqual(st['created'], 200, '临时页创建失败: %r' % (st,))
        self.page.goto(URL + '/edit/' + self.PAGE, wait_until='domcontentloaded')
        self.page.wait_for_timeout(3000)

    def tearDown(self):
        try:
            self.page.goto(URL + '/', wait_until='domcontentloaded')
            self.page.wait_for_timeout(500)
            self.page.evaluate(self.PURGE_JS, self.PAGE)
            left = self.page.evaluate(self.EXISTS_JS, self.PAGE)
            self.assertEqual(left, 404, '临时页没清干净（HTTP %r）' % left)
        finally:
            super().tearDown()

    # ── 工具 ──────────────────────────────────────────────
    def _ex(self, cmd, cur=1, wait=1000, reset=True):
        if reset:
            self.page.evaluate(self.RESET_JS, [EXLINE_SCRATCH, cur])
            self.page.wait_for_timeout(300)
        self.page.keyboard.type(':')
        self.page.wait_for_timeout(120)
        self.page.keyboard.type(cmd)
        self.page.wait_for_timeout(120)
        self.page.keyboard.press('Enter')
        self.page.wait_for_timeout(wait)
        return self.page.evaluate(self.READ_JS)

    def _lines(self, r):
        return r['text'].split(chr(10))

    # ── ① 行删除：:d / :delete / :del 三者等价，杀的只是行 ────
    def test_d_deletes_a_line_not_the_page(self):
        """`:d`/`:delete`/`:del` 都删行 —— 页面必须还在。

        `:del` 早先是「删除当前页」的自定义命令；补上 `:d`/`:delete` 之后
        被前缀匹配抢到行删除上（`"delete".indexOf("del") === 0`）。
        按「对齐真实 vim」的取舍：三个名字都删行，删页面改成 `:Delete`。
        """
        for cmd in ('d', 'delete', 'del'):
            r = self._ex(cmd, cur=1)
            self.assertIsNone(r['msg'], ':%s 报错了: %r' % (cmd, r['msg']))
            self.assertEqual(r['n'], 6, ':%s 没删掉 1 行（剩 %d 行）' % (cmd, r['n']))
            self.assertNotIn('alpha 行', r['text'], ':%s 删错了行' % cmd)
            self.assertIn('# 测试用', r['text'], ':%s 把标题行也删了' % cmd)
        st = self.page.evaluate(self.EXISTS_JS, self.PAGE)
        self.assertEqual(st, 200, '临时页被删掉了（:del 又去删页面了）')

    # ── ② 范围删除 ───────────────────────────────────────
    def test_range_delete(self):
        """`:2,4d` 删 3 行；`:2,3delete` 删 2 行。"""
        r = self._ex('2,4d', cur=0)
        self.assertIsNone(r['msg'], '范围删除报错: %r' % r['msg'])
        self.assertEqual(r['n'], 4, '2,4d 应剩 4 行，实际 %d' % r['n'])
        self.assertEqual(r['text'], '# 测试用' + chr(10) + 'gamma 行' + chr(10)
                         + 'delta aa 行' + chr(10),
                         '2,4d 删错行: %r' % r['text'])

        r = self._ex('2,3delete', cur=0)
        self.assertEqual(r['n'], 5, '2,3delete 应剩 5 行，实际 %d' % r['n'])
        self.assertNotIn('alpha 行', r['text'])

    # ── ③ :g/<pat>/d —— 真实 vim 里最常用，原先是死的 ──────
    def test_global_delete_actually_deletes(self):
        """`:g/alpha/d` 必须删掉两行 alpha（曾经一个字符都不删）。"""
        r = self._ex('g/alpha/d', cur=0, wait=1200)
        self.assertIsNone(r['msg'], 'global 仍然是死的: %r' % r['msg'])
        self.assertEqual(r['n'], 5, ':g/alpha/d 应剩 5 行，实际 %d' % r['n'])
        self.assertEqual(r['text'], '# 测试用' + chr(10) + 'beta 行' + chr(10)
                         + 'gamma 行' + chr(10) + 'delta aa 行' + chr(10),
                         ':g/alpha/d 结果不对: %r' % r['text'])

    # ── ④ :v（反向全局）──────────────────────────────────
    def test_inverse_global_deletes_non_matching(self):
        """`:v/alpha/d` 只留匹配 alpha 的行。"""
        r = self._ex('v/alpha/d', cur=0, wait=1200)
        self.assertIsNone(r['msg'], ':v 报错: %r' % r['msg'])
        self.assertEqual(r['n'], 2, ':v/alpha/d 应剩 2 行，实际 %d' % r['n'])
        self.assertEqual(r['text'], 'alpha 行' + chr(10) + 'alpha 再一行',
                         ':v/alpha/d 结果不对: %r' % r['text'])

    # ── ⑤ :g/<pat>/normal dd —— range 必须作用到匹配行 ────
    def test_global_normal_dd_hits_matched_line(self):
        """`:g/beta/normal dd` 删的必须是 beta 行，不是光标那行。

        第一版 :normal 没用 params.line，每次 dd 都作用在当前光标（第一行），
        实测删掉的是 `# 测试用`，beta 行纹丝不动。
        """
        r = self._ex('g/beta/normal dd', cur=0, wait=1400)
        self.assertIsNone(r['msg'], '报错: %r' % r['msg'])
        self.assertNotIn('beta', r['text'], 'beta 行没被删掉: %r' % r['text'])
        self.assertIn('# 测试用', r['text'], ':normal dd 删的是光标行而不是匹配行')
        self.assertEqual(r['n'], 6, '应只删 1 行，实际剩 %d' % r['n'])

    # ── ⑥ :j 合并行 ──────────────────────────────────────
    def test_join_lines(self):
        """`:j` 合并当前行与下一行；带范围一次并多行。"""
        r = self._ex('j', cur=1)
        self.assertIsNone(r['msg'], ':j 报错: %r' % r['msg'])
        self.assertEqual(r['n'], 6, ':j 应少 1 行，实际 %d' % r['n'])
        self.assertIn('alpha 行 beta 行', r['text'], '合并结果不对: %r' % r['text'])

        r = self._ex('2,4j', cur=0)
        self.assertEqual(r['n'], 5, '2,4j 应少 2 行，实际 %d' % r['n'])
        self.assertIn('alpha 行 beta 行 alpha 再一行', r['text'],
                      '2,4j 结果不对: %r' % r['text'])

    # ── ⑦ :y + :pu ───────────────────────────────────────
    def test_yank_and_put(self):
        """`:2,4y` 复制 3 行，`:pu` 贴到指定行**下方**（vim 语义）。"""
        r = self._ex('2,4y', cur=0)
        self.assertIsNone(r['msg'], ':y 报错: %r' % r['msg'])
        self.assertEqual(r['n'], 7, ':y 不该改行数')

        # 光标挪到第 5 行（0 基 4），`:pu` 就插在第 6 行（0 基 5）之前
        self.page.evaluate(self.CURSOR_JS, 4)
        self.page.wait_for_timeout(250)
        r = self._ex('pu', wait=900, reset=False)
        self.assertIsNone(r['msg'], ':pu 报错: %r' % r['msg'])
        self.assertEqual(r['n'], 10, ':pu 应插 3 行（7→10），实际 %d' % r['n'])
        body = self._lines(r)
        self.assertEqual(body[5], 'alpha 行', '粘贴位置不对，第 6 行是 %r' % body[5])
        self.assertEqual(body[6], 'beta 行', '粘贴只贴了一行？第 7 行是 %r' % body[6])
        self.assertEqual(body[7], 'alpha 再一行', '第 8 行是 %r' % body[7])
        self.assertEqual(body[4], 'gamma 行', '原来第 5 行应被顶到第 5 行，实际 %r'
                         % body[4])

    # ── ⑧ :Delete 是删页面，必须走确认框（不静默删）──────────
    def test_capital_Delete_asks_for_confirmation(self):
        """`:Delete` 删的是**页面**，所以必须弹确认框，不能静默删。"""
        r = self._ex('Delete', cur=1, wait=1400)
        self.assertEqual(r['n'], 7, ':Delete 变成了删行（剩 %d 行）' % r['n'])
        self.assertTrue(self.page.evaluate(self.CONFIRM_JS),
                        ':Delete 没有弹确认框（会静默删页面）')
        st = self.page.evaluate(self.EXISTS_JS, self.PAGE)
        self.assertEqual(st, 200, '临时页在确认前就被删了')
        self.page.keyboard.press('Escape')
        self.page.wait_for_timeout(400)

# ══════════════════════════════════════════════════════════
# 18. iw / aw 文本对象 —— 期望值取自真 nvim 实测
#
# 用 `nvim --headless -u NONE` + `nvim_feedkeys` 对同一段文本、同一光标位置
# 跑同一组按键，把结果抄成本节的断言。网页侧对不上就是 bug。
#
# 本轮修的三处（都在 vendored vim.js）：
#
#  ① expandWordUnderCursor 只会「向前找词」——光标落在空白上时，
#     `diw` 越过空白去删下一个词。vim 里选中的是**那段空白本身**。
#  ② yank operator 无条件 `return oldAnchor`（光标原地不动）。vim 的
#     字符级 y{motion} 把光标留在**区域起点**，`yb`/`y0` 这种向后运动时
#     锚点恰是区域**尾**，只留原地就会错位；偏差一路传到后面的 `P` 粘贴。
#  ③ paste 的 `P` 分支用 `idx + text.length`（最后字符的**下一格**），
#     比 `p` 偏一位。
# ══════════════════════════════════════════════════════════

IWAW_SCRATCH = 'placeholder'


class TestEditorWordTextObjects(Base):
    """iw / aw 在空白、标点、CJK、行首缩进、行尾空白上的选区行为。"""

    PAGE = '__e2e-iwaw-scratch'

    PURGE_JS = (
        'async (name) => {'
        '  const b = window.__KB_BASE__ + "/api/pages/" + name;'
        '  await fetch(b, {method: "DELETE"});'
        '  await fetch(b + "/versions", {method: "DELETE"});'
        '  return true; }'
    )

    CREATE_JS = (
        'async ([name, content]) => {'
        '  const b = window.__KB_BASE__ + "/api/pages/" + name;'
        '  await fetch(b, {method: "DELETE"});'
        '  await fetch(b + "/versions", {method: "DELETE"});'
        '  const r = await fetch(window.__KB_BASE__ + "/api/pages", {'
        '    method: "POST", headers: {"Content-Type": "application/json"},'
        '    body: JSON.stringify({name: name, content: content}), });'
        '  return r.status; }'
    )

    EXISTS_JS = (
        'async (name) => {'
        '  const r = await fetch(window.__KB_BASE__ + "/api/pages/" + name);'
        '  return r.status; }'
    )

    SETUP_JS = (
        '([text, ch]) => {'
        '  const w = document.querySelector(".CodeMirror");'
        '  if (!w) return false;'
        '  w.CodeMirror.setValue(text);'
        '  w.CodeMirror.setCursor({line: 0, ch: ch});'
        '  w.CodeMirror.focus();'
        '  return true; }'
    )

    READ_JS = (
        '() => {'
        '  const w = document.querySelector(".CodeMirror");'
        '  const c = w.CodeMirror;'
        '  return {text: c.getValue(), ch: c.getCursor().ch,'
        '          line: c.getCursor().line}; }'
    )

    def setUp(self):
        super().setUp()
        self.page.goto(URL + '/', wait_until='domcontentloaded')
        self.page.wait_for_timeout(1200)
        st = self.page.evaluate(self.CREATE_JS, [self.PAGE, 'placeholder'])
        self.assertEqual(st, 200, '临时页创建失败: %r' % st)
        self.page.goto(URL + '/edit/' + self.PAGE, wait_until='domcontentloaded')
        self.page.wait_for_timeout(3000)

    def tearDown(self):
        try:
            self.page.goto(URL + '/', wait_until='domcontentloaded')
            self.page.wait_for_timeout(500)
            self.page.evaluate(self.PURGE_JS, self.PAGE)
            left = self.page.evaluate(self.EXISTS_JS, self.PAGE)
            self.assertEqual(left, 404, '临时页没清干净（HTTP %r）' % left)
        finally:
            super().tearDown()

    def _run(self, text, ch, keys):
        """在临时页里载入 text、把光标放到 ch、喂 keys，返回最终状态。"""
        self.page.keyboard.press('Escape')
        self.page.wait_for_timeout(150)
        ok = self.page.evaluate(self.SETUP_JS, [text, ch])
        self.assertIsNot(ok, False, '拿不到 CodeMirror 实例')
        self.page.wait_for_timeout(200)
        for k in keys:
            self.page.keyboard.press(k)
            self.page.wait_for_timeout(90)
        self.page.wait_for_timeout(350)
        return self.page.evaluate(self.READ_JS)

    # ── ① 空白当词：diw / daw 在空格上 ────────────────────────
    def test_diw_on_whitespace_deletes_the_space_not_next_word(self):
        """`'foo(bar, baz)'` 光标在 ch=8（空格）上按 diw。

        nvim: (0,8) 'foo(bar,baz)'   ← 删掉那个空格
        修前: (0,9) 'foo(bar, )'     ← 越过空格删掉了 'baz'
        """
        r = self._run('foo(bar, baz)', 8, ['d', 'i', 'w'])
        self.assertEqual(r['text'], 'foo(bar,baz)',
                         'diw 删错了内容: %r' % r['text'])
        self.assertEqual(r['ch'], 8)

        # 两个空格中间：iw = 两个空格
        r = self._run('a  b', 1, ['d', 'i', 'w'])
        self.assertEqual(r['text'], 'ab', 'iw 在双空格上的选区不对: %r' % r['text'])

    def test_diw_on_indent_and_trailing_whitespace(self):
        """行首缩进 / 行尾空白上的 diw。

        nvim: '   abc' ch=1 → '' 之外先吃缩进 → 'abc'
              'foo  '  ch=3 → 'foo'
        """
        r = self._run('   abc', 1, ['d', 'i', 'w'])
        self.assertEqual(r['text'], 'abc', '缩进上的 diw 删错了: %r' % r['text'])

        r = self._run('foo  ', 3, ['d', 'i', 'w'])
        self.assertEqual(r['text'], 'foo', '行尾空白上的 diw 删错了: %r' % r['text'])

    # ── ② aw 只向后吃词 ─────────────────────────────────────
    def test_daw_on_whitespace_takes_space_plus_next_word(self):
        """`'foo(bar, baz)'` ch=8 的 daw = 空格 + 'baz'。

        nvim: (0,8) 'foo(bar,)'；修前 `exitVisualMode` 后跟的选区方向不对。
        """
        r = self._run('foo(bar, baz)', 8, ['d', 'a', 'w'])
        self.assertEqual(r['text'], 'foo(bar,)',
                         'daw 在空格上的选区不对: %r' % r['text'])
        self.assertEqual(r['ch'], 8)

    def test_daw_without_trailing_word_does_nothing_but_moves_cursor(self):
        """后面没有词时 vim 不删东西，但光标会挪到空白结束处。

        nvim 实测：'foo  ' ch=3 daw → 文本不变、光标 (0,4)
                  '   '   ch=1 daw → 文本不变、光标 (0,2)（夹到行长-1）
        """
        for text, ch, want_ch in (('foo  ', 3, 4), ('   ', 1, 2)):
            r = self._run(text, ch, ['d', 'a', 'w'])
            self.assertEqual(r['text'], text,
                             'aw 后面没词时不该改动文本: %r' % r['text'])
            self.assertEqual(r['ch'], want_ch,
                             'aw 后光标应在 %d，实际 %d' % (want_ch, r['ch']))

    # ── ③ yiw / yaw 之后光标在区域起点 ────────────────────────
    def test_yank_moves_cursor_to_region_start(self):
        """字符级 y{motion} 之后光标 = min(锚点, 区域尾)。

        nvim 实测（'foo  bar' 逐列跑 yiw，共 8 列）：
          ch=0,1,2 → 0   （"foo" 起点）
          ch=3,4   → 3   （iw 是空白 [3,5)，起点 3）
          ch=5,6,7 → 5   （"bar" 起点）

        注意别拿 ch=8 去比 —— 'foo  bar' 只有 8 个字符、下标 0..7，
        nvim 对越界列会先夹到 7 再算，跟网页侧「行尾=ch 8」不是同一件事
        （这条第一版就踩了：期望 5、实际 8，以为是 yank bug）。
        """
        expect = {0: 0, 1: 0, 2: 0, 3: 3, 4: 3, 5: 5, 6: 5, 7: 5}
        for ch, want in expect.items():
            r = self._run('foo  bar', ch, ['y', 'i', 'w'])
            self.assertEqual(r['ch'], want,
                             'yiw 在 ch=%d 后光标应在 %d，实际 %d'
                             % (ch, want, r['ch']))

    # ── ④ P 与 p 的光标偏移一致 ──────────────────────────────
    def test_put_P_cursor_lands_on_last_inserted_char(self):
        """`y{l}P` 的光标应与 `p` 一样落在插入文本的最后一字符。

        nvim 实测：'abcdef' ch=2
          ylP  → (0,2) 'abccdef'      （P 插入 1 个字符，光标 = 2）
          y2lP → (0,3) 'abcdcdef'
          y$P  → (0,5) 'abcdefcdef'
        修前 P 走 `idx + text.length`，每次多一位。
        """
        cases = [('abcdef', 2, ['y', 'l', 'P'], 2, 'abccdef'),
                 ('abcdef', 2, ['y', '2', 'l', 'P'], 3, 'abcdcdef'),
                 ('abcdef', 2, ['y', '$', 'P'], 5, 'abcdefcdef')]
        for text, ch, keys, want_ch, want_text in cases:
            r = self._run(text, ch, keys)
            self.assertEqual(r['text'], want_text,
                             '%s 粘贴结果不对: %r' % (''.join(keys), r['text']))
            self.assertEqual(r['ch'], want_ch,
                             '%s 后光标应在 %d，实际 %d'
                             % (''.join(keys), want_ch, r['ch']))

        # p 那一支本来就是对的，别改坏了
        r = self._run('abcdef', 2, ['y', 'l', 'p'])
        self.assertEqual((r['text'], r['ch']), ('abccdef', 3))


