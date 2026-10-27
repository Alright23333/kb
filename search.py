"""Shared FTS search with jieba CJK segmentation support.

FTS5 trigram tokenizer indexes 3-char sequences. For CJK:
- Queries ≥3 chars: trigram works directly (sliding window match)
- Queries <3 chars (e.g. "笔记" = 2 chars): trigram can't match, falls back to LIKE

jieba helps by segmenting longer CJK queries into meaningful words,
which are then OR'd to improve recall. For example:
    "记笔记的方法" → ["记笔记的方法", "记笔记", "方法"]
Each term ≥3 chars becomes a trigram MATCH candidate.

jieba is optional — gracefully degrades if not installed.
"""

import re

try:
    import jieba
    _HAS_JIEBA = True
except ImportError:
    _HAS_JIEBA = False

# Stopwords to exclude from segmentation
_STOPWORDS = set("的是了在和有也不都为而要又为很都这")


def _is_cjk(char: str) -> bool:
    cp = ord(char)
    return (
        0x4E00 <= cp <= 0x9FFF or
        0x3400 <= cp <= 0x4DBF or
        0x3040 <= cp <= 0x309F or
        0x30A0 <= cp <= 0x30FF or
        0xAC00 <= cp <= 0xD7AF
    )


def _has_cjk(text: str) -> bool:
    return any(_is_cjk(c) for c in text)


def _escape_fts(term: str) -> str:
    """Escape term for FTS5 MATCH."""
    return '"' + term.replace('"', '""') + '"'


def build_search_query(query: str) -> tuple[str | None, list[str]]:
    """Build FTS MATCH expression + LIKE fallback patterns from user query.

    Returns (fts_expr, like_patterns):
    - fts_expr: FTS5 MATCH string with OR'd terms (or None if no ≥3 char terms)
    - like_patterns: LIKE patterns for short-term fallback

    Strategy:
    1. Non-CJK: use raw query if ≥3 chars, else LIKE fallback
    2. CJK ≥3 chars: raw query as primary + jieba segments as OR boosters
    3. CJK <3 chars: LIKE fallback only (trigram can't help)
    """
    q = query.strip()
    if not q:
        return None, []

    # Non-CJK path
    if not _has_cjk(q):
        if len(q) >= 3:
            return _escape_fts(q), []
        return None, [q]

    # CJK path
    # Always include the raw query if ≥2 chars (exact phrase priority)
    terms = [q]

    # jieba segmentation for longer queries
    if _HAS_JIEBA and len(q) > 3:
        for seg in jieba.cut(q, cut_all=False):
            seg = seg.strip()
            if not seg or seg in _STOPWORDS:
                continue
            if seg != q and seg not in terms:
                terms.append(seg)

    # Split terms into FTS (≥3 chars) and LIKE fallback (<3 chars)
    fts_terms = []
    like_patterns = []
    seen_fts = set()

    for term in terms:
        if len(term) >= 3:
            key = term.lower()
            if key not in seen_fts:
                seen_fts.add(key)
                fts_terms.append(_escape_fts(term))
        elif len(term) >= 1:
            like_patterns.append(term)

    fts_expr = " OR ".join(fts_terms) if fts_terms else None
    return fts_expr, like_patterns


def _rank_terms(query: str) -> list[str]:
    """Terms used for name-relevance ranking: full query + space-split words
    + jieba segments (CJK), deduped, preserving order."""
    q = query.strip()
    if not q:
        return []
    terms = [q]
    terms.extend(w for w in q.split() if w)
    if _HAS_JIEBA and _has_cjk(q) and len(q) > 3:
        for seg in jieba.cut(q, cut_all=False):
            seg = seg.strip()
            if seg and seg not in _STOPWORDS:
                terms.append(seg)
    seen = set()
    out = []
    for t in terms:
        k = t.casefold()
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def rerank_by_name(rows: list[dict], query: str, name_key: str = "name") -> list[dict]:
    """Re-rank search results so name-related pages come first.

    Tiers: exact name match > name starts with term > name contains term >
    content-only match. A row's tier is its best tier across all rank terms.
    Stable sort: within a tier the original order (FTS rank / recency) is kept.
    """
    terms = _rank_terms(query)
    if not terms or not rows:
        return rows

    def tier(row: dict) -> int:
        name = str(row.get(name_key, "")).casefold()
        best = 3
        for t in terms:
            if name == t:
                return 0
            if best > 1 and name.startswith(t):
                best = 1
            elif best > 2 and t in name:
                best = 2
        return best

    return sorted(rows, key=tier)


def make_snippet(query: str, content: str, width: int = 90) -> str:
    """Content fragment for search results: the first region matching any
    query term (whole query, then space-split words), ±width chars.
    Falls back to the content head. Shared by REST API and MCP search."""
    if not content:
        return ""
    q = query.strip()
    terms = [q] + [t for t in re.split(r"\s+", q) if t]
    hay = content.casefold()
    for t in terms:
        i = hay.find(t.casefold())
        if i >= 0:
            start = max(0, i - width)
            end = min(len(content), i + len(t) + width)
            frag = content[start:end].replace("\n", " ").strip()
            return ("…" if start > 0 else "") + frag + ("…" if end < len(content) else "")
    first = content.strip().replace("\n", " ")
    return first[:120] + ("…" if len(first) > 120 else "")
