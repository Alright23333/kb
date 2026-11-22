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
        try:
            self.api('DELETE', '/pages/' + self.PAGE)
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

