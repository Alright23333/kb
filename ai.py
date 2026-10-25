"""AI integration layer: OpenAI-compatible LLM client + KB context builders.

Three AI-native touchpoints (design doc: docs/ai-native.md):
- 问 (ask):   grounded chat over the KB (RAG-lite: jieba/FTS retrieval + current page)
- 写 (write): inline editor actions on selected text
- 连 (link):  post-save link suggestions that strengthen the link graph

All features degrade gracefully: without KB_AI_API_KEY every endpoint
reports disabled instead of erroring.

Config (env):
- KB_AI_API_KEY   — any OpenAI-compatible key; empty → AI off
- KB_AI_BASE_URL  — default https://open.bigmodel.cn/api/paas/v4 (GLM)
- KB_AI_MODEL     — default glm-4-flash (free tier)
"""
import datetime
import json
import os
import re

import httpx

from database import get_db
from parse import CODE_RE, WIKILINK_RE
from search import build_search_query, _has_cjk, _STOPWORDS

try:
    import jieba
    _HAS_JIEBA = True
except ImportError:
    _HAS_JIEBA = False

AI_API_KEY = os.environ.get("KB_AI_API_KEY", "").strip()
AI_BASE_URL = os.environ.get("KB_AI_BASE_URL", "https://open.bigmodel.cn/api/paas/v4").rstrip("/")
AI_MODEL = os.environ.get("KB_AI_MODEL", "glm-4-flash")


class AIError(Exception):
    """Upstream LLM failure with a user-facing message."""


def ai_enabled() -> bool:
    return bool(AI_API_KEY)


_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=10.0))
    return _client


def _payload(messages: list[dict], stream: bool) -> dict:
    return {
        "model": AI_MODEL,
        "messages": messages,
        "temperature": 0.4,
        "stream": stream,
    }


async def chat(messages: list[dict]) -> str:
    """Non-streaming completion; returns the full assistant text."""
    url = f"{AI_BASE_URL}/chat/completions"
    headers = {"Authorization": f"Bearer {AI_API_KEY}"}
    try:
        resp = await _get_client().post(url, json=_payload(messages, stream=False), headers=headers)
    except httpx.HTTPError as e:
        raise AIError(f"无法连接 AI 服务: {e}") from e
    if resp.status_code != 200:
        raise AIError(f"AI 服务返回 {resp.status_code}: {resp.text[:300]}")
    try:
        return resp.json()["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, ValueError) as e:
        raise AIError(f"AI 响应格式异常: {e}") from e


async def chat_stream(messages: list[dict]):
    """Streaming completion; yields content deltas."""
    url = f"{AI_BASE_URL}/chat/completions"
    headers = {"Authorization": f"Bearer {AI_API_KEY}"}
    try:
        async with _get_client().stream(
            "POST", url, json=_payload(messages, stream=True), headers=headers
        ) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", "replace")
                raise AIError(f"AI 服务返回 {resp.status_code}: {body[:300]}")
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except ValueError:
                    continue
                try:
                    delta = obj["choices"][0]["delta"].get("content")
                except (KeyError, IndexError):
                    continue
                if delta:
                    yield delta
    except httpx.HTTPError as e:
        raise AIError(f"AI 连接中断: {e}") from e


# ── Retrieval (RAG-lite) ──────────────────────────────────────────

def _question_terms(q: str, cap: int = 6) -> list[str]:
    """Segment a natural-language question into retrieval terms."""
    if _HAS_JIEBA and _has_cjk(q):
        segs = [s.strip() for s in jieba.cut(q, cut_all=False) if s.strip()]
    else:
        segs = q.split()
    seen: set[str] = set()
    out: list[str] = []
    for s in segs:
        if len(s) < 2 or s in _STOPWORDS:
            continue
        key = s.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) >= cap:
            break
    return out


async def _search_pages(q: str, limit: int) -> list[tuple[str, str]]:
    """Return [(name, content)] best-effort matches for a natural question."""
    db = await get_db()
    try:
        fts_expr, like_patterns = build_search_query(q)
        if fts_expr:
            try:
                cur = await db.execute(
                    """SELECT p.name, p.content FROM pages_fts f
                       JOIN pages p ON p.id = f.rowid
                       WHERE pages_fts MATCH ? ORDER BY rank LIMIT ?""",
                    (fts_expr, limit),
                )
                rows = await cur.fetchall()
                if rows:
                    return [(r["name"], r["content"] or "") for r in rows]
            except Exception:
                pass

        terms = like_patterns or [t for t in _question_terms(q)]
        if terms:
            conds, params = [], []
            for t in terms:
                like = f"%{t}%"
                conds.append("(name LIKE ? OR content LIKE ?)")
                params.extend([like, like])
            params.append(limit)
            cur = await db.execute(
                f"""SELECT name, content FROM pages
                    WHERE {' OR '.join(conds)}
                    ORDER BY updated_at DESC LIMIT ?""",
                tuple(params),
            )
            rows = await cur.fetchall()
            if rows:
                return [(r["name"], r["content"] or "") for r in rows]

        cur = await db.execute(
            "SELECT name, content FROM pages ORDER BY updated_at DESC LIMIT ?", (limit,)
        )
        rows = await cur.fetchall()
        return [(r["name"], r["content"] or "") for r in rows]
    finally:
        await db.close()


async def build_chat_context(
    question: str, current_page: str | None, journal_days: int | None = None
) -> dict:
    """Assemble the grounded-context block + the pages it used (citation chips).

    Returns {"text": str, "pages": [name, ...]}. Sources, in order:
    current page → recent journal entries (journal_days) → keyword retrieval.
    """
    blocks: list[str] = []
    pages_used: list[str] = []
    cited = set()

    if current_page:
        db = await get_db()
        try:
            cur = await db.execute(
                "SELECT name, content FROM pages WHERE name = ? COLLATE NOCASE", (current_page,)
            )
            row = await cur.fetchone()
        finally:
            await db.close()
        if row:
            blocks.append(f"【当前页面:{row['name']}】\n{(row['content'] or '')[:4000]}")
            pages_used.append(row["name"])
            cited.add(row["name"].casefold())

    if journal_days:
        cutoff = (datetime.date.today() - datetime.timedelta(days=journal_days - 1)).isoformat()
        db = await get_db()
        try:
            cur = await db.execute(
                """SELECT name, content FROM pages
                   WHERE name GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
                     AND name >= ?
                   ORDER BY name DESC LIMIT 10""",
                (cutoff,),
            )
            rows = await cur.fetchall()
        finally:
            await db.close()
        if rows:
            entries = "\n\n".join(f"### {r['name']}\n{(r['content'] or '')[:1500]}" for r in rows)
            blocks.append(f"【最近 {journal_days} 天日记】\n{entries}")
            for r in rows:
                pages_used.append(r["name"])
                cited.add(r["name"].casefold())
        else:
            blocks.append(f"【最近 {journal_days} 天日记】（该时间段没有日记页面）")

    hits = await _search_pages(question, limit=4)
    others = [(n, c) for n, c in hits if n.casefold() not in cited]
    if others:
        pages = "\n\n".join(f"### {n}\n{c[:1200]}" for n, c in others)
        blocks.append(f"【检索到的相关页面】\n{pages}")
        pages_used.extend(n for n, _ in others)

    if not blocks:
        return {"text": "（知识库为空或没有检索到相关内容）", "pages": []}
    return {"text": "\n\n".join(blocks), "pages": pages_used}


CHAT_SYSTEM = """你是用户个人知识库(KB)的内置 AI 助手。规则:
- 优先依据下方提供的知识库上下文回答;上下文没有的信息就直说"知识库里没有",不要编造
- 引用来源页面时写成 [[页面名]] 维基链接,用户可以点击跳转
- 用中文,简洁分点,markdown 格式

知识库上下文:
"""


# ── Editor actions ────────────────────────────────────────────────

EDIT_ACTIONS = {
    "polish": "润色下面这段文字:保持原意、语气和 markdown 格式,让表达更清晰流畅。直接输出润色后的全文,不要任何解释或前后缀。",
    "continue": "续写下面这段文字:顺着文意自然延伸 2-4 句。直接输出续写的新内容,不要重复原文,不要解释。",
    "summarize": "把下面这段文字总结为 3-5 条要点,markdown 无序列表输出,不要解释。",
    "expand": "扩展下面这段内容:补充有价值的细节或例子。直接输出扩展后的全文,不要解释。",
}


def edit_messages(action: str, text: str, instruction: str | None) -> list[dict]:
    if action == "custom":
        sys_prompt = (instruction or "改进下面这段文字") + "\n直接输出结果,不要任何解释。"
    else:
        sys_prompt = EDIT_ACTIONS.get(action)
        if sys_prompt is None:
            raise AIError(f"未知操作: {action}(可选: {'/'.join(EDIT_ACTIONS)} 或 custom)")
    return [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": text[:4000]},
    ]


# ── Link suggestions ──────────────────────────────────────────────

LINKS_SYSTEM = """你是知识库的链接编辑。给定一个页面的内容和库里所有页面名列表,找出内容中提到了、但还没有建立 [[双向链接]] 的概念,且这个概念恰好是库里的某个页面。

只输出一个 JSON 数组(不要 markdown 代码块,不要解释),每项:
{"anchor": "内容中的原文片段(必须逐字出现在内容里,5-25字)", "target": "页面名(必须来自列表)", "reason": "一句话理由"}
最多 5 项,没有合适的就输出 []。不要建议链接到页面自身,不要建议内容里已经有 [[链接]] 指向的页面。"""


def _protected_spans(content: str) -> list[tuple[int, int]]:
    """Char ranges that must not be modified: code + existing wikilinks."""
    spans = []
    for rx in (CODE_RE, WIKILINK_RE):
        for m in rx.finditer(content):
            spans.append(m.span())
    return spans


def _find_free(content: str, anchor: str) -> int | None:
    """First occurrence of anchor not inside a protected span."""
    spans = _protected_spans(content)
    start = 0
    while True:
        i = content.find(anchor, start)
        if i < 0:
            return None
        end = i + len(anchor)
        if not any(s < end and e > i for s, e in spans):
            return i
        start = i + 1


def _extract_json_array(text: str) -> list:
    """Tolerantly pull the first JSON array out of an LLM reply."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[\w]*\n?|```$", "", text).strip()
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        val = json.loads(text[start : end + 1])
        return val if isinstance(val, list) else []
    except ValueError:
        return []


async def suggest_links(name: str, content: str, all_names: list[str]) -> list[dict]:
    """Ask the LLM for link candidates, then hard-validate each one."""
    candidates = [n for n in all_names if n.casefold() != name.casefold()]
    if not candidates or len(content) < 80:
        return []

    existing_targets = {t.casefold() for t in WIKILINK_RE.findall(content)}
    user_msg = (
        f"页面名: {name}\n\n页面内容:\n{content[:4000]}\n\n"
        f"库中其他页面名列表:\n{', '.join(candidates)}"
    )
    raw = await chat(
        [
            {"role": "system", "content": LINKS_SYSTEM},
            {"role": "user", "content": user_msg},
        ]
    )

    out, seen = [], set()
    for item in _extract_json_array(raw):
        if not isinstance(item, dict):
            continue
        anchor = str(item.get("anchor", "")).strip()
        target = str(item.get("target", "")).strip()
        reason = str(item.get("reason", "")).strip()[:120]
        if not anchor or not target:
            continue
        if target.casefold() == name.casefold():
            continue
        if target.casefold() not in {c.casefold() for c in candidates}:
            continue
        if target.casefold() in existing_targets:
            continue
        if _find_free(content, anchor) is None:
            continue
        key = (anchor.casefold(), target.casefold())
        if key in seen:
            continue
        seen.add(key)
        out.append({"anchor": anchor, "target": target, "reason": reason})
        if len(out) >= 5:
            break
    return out


def apply_link(content: str, anchor: str, target: str) -> str | None:
    """Wrap the first free occurrence of anchor as [[target|anchor]]."""
    i = _find_free(content, anchor)
    if i is None:
        return None
    # wikilink syntax is [[page|alias]] — target page FIRST, anchor is the display text
    link = f"[[{target}|{anchor}]]" if anchor.strip() != target.strip() else f"[[{anchor}]]"
    return content[:i] + link + content[i + len(anchor) :]
