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
