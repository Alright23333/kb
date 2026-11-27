"""Markdown parsing for Logseq-style syntax.

Extracts the structural signals from raw markdown:
- `[[wiki links]]` -> links (with optional `|alias`)
- `#tags`          -> tags (excluding headings, [[..]] internals, code, URLs)
- `key:: value`    -> properties (line-leading, optional list bullet)

All three signals skip code: fenced blocks, inline backticks, and
4-space-indented blocks (tab-indented lines stay visible — those are
Logseq outline bullets, not code).

All functions are pure (no I/O) so both the API layer and the importer
can share them.
"""
import re

# [[target]] or [[target|alias]]
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")

# A tag: '#' at a word boundary, followed by word chars (incl. CJK via \w)
# or hyphens only. Excludes headings ("# ", "## " via lookbehind), org
# markers ("#+BEGIN_..."), URL anchors ("a#frag") and mid-word hashes
# ("foo#bar"). Punctuation (half/full-width brackets, commas...) terminates
# the tag instead of being captured into it.
#
# The lookbehind is ASCII-only on purpose: Python's \w matches CJK, so a
# CJK-aware lookbehind would reject "第12周#Monday" while the front-end
# (JS \w is ASCII-only) still autocompletes it — the two rules would drift
# apart. The tag *body* stays \w so CJK tag names ("#采购") still work.
TAG_RE = re.compile(r"(?<![0-9A-Za-z_#])#(?!\+)([\w-]+)")

# Fenced code blocks (``` / ~~~) and inline code are never parsed.
CODE_RE = re.compile(r"(?s)(```.*?```|~~~.*?~~~|`[^`\n]*`)")

# Indented code blocks. CODE_RE only knew fenced blocks and inline backticks,
# so `#tag` / `[[link]]` / `key:: value` written inside a 4-space-indented
# block were still indexed while the renderer (markdown-it) drew them as code
# — the sidebar and backlink panel disagreed with the page body.
#
# The rule is 4+ **spaces** only, on purpose: real pages here are Logseq
# outlines indented with tabs, and a tab-indented line is a bullet whose tags
# and links are genuine.
#
# A line only *opens* a block when the line above it is blank, and the text
# after the indent is not a list bullet / quote marker (those are list-item
# continuations, not code).
INDENT_CODE_LINE_RE = re.compile(r"^ {4,}(?![ \t]*(?:[-*+]\s|\d+[.)]\s|>))")

# URLs are stripped before tag extraction: a '#' inside a URL is a fragment
# ("https://x.com/#grid12", "…#cookie"), never a tag. The character class
# stops at whitespace and at the punctuation that commonly closes a URL in
# prose or a markdown link.
URL_RE = re.compile(r"(?i)\b(?:https?|ftp)://[^\s)\]\"'<>]+")

# Property line: optional list bullet, then `key:: value`.
PROPERTY_RE = re.compile(r"^\s*-?\s*([^\s:：]+)::\s*(.*)$")


def _mask_indented_code(content: str) -> str:
    """Blank indented code blocks, keeping line numbers stable.

    Blanking (rather than deleting) means callers that echo a line number
    still point at the right place in the original text.
    """
    lines = content.split("\n")
    if not any(INDENT_CODE_LINE_RE.match(line) for line in lines):
        return content
    out: list[str] = []
    prev_blank = True
    for line in lines:
        if not line.strip():
            out.append(line)
            prev_blank = True
            continue
        if prev_blank and INDENT_CODE_LINE_RE.match(line):
            out.append("")
            prev_blank = True
            continue
        out.append(line)
        prev_blank = False
    return "\n".join(out)


def _strip_code(content: str) -> str:
    """Return content with every kind of code removed.

    Indented blocks are masked first (that needs the original line
    structure), then fenced blocks and inline backticks are collapsed.
    """
    return CODE_RE.sub(" ", _mask_indented_code(content))


def extract_links(content: str) -> set[str]:
    """Return the set of link targets from `[[...]]`, resolving aliases.

    Code is stripped first: a `[[target]]` inside a code block (a TOML table
    header, a doc snippet) is not a link to a page.
    """
    targets: set[str] = set()
    for m in WIKILINK_RE.finditer(_strip_code(content)):
        inner = m.group(1).strip()
        # [[target|alias]] -> target is before the first '|'
        target = inner.split("|", 1)[0].strip()
        if target:
            targets.add(target)
    return targets


def extract_tags(content: str) -> set[str]:
    """Return the set of tags from `#tag`, ignoring [[..]] internals, headings,
    and anything inside code blocks or inline code."""
    # Strip code first (fenced, inline *and indented*), then wiki-link
    # brackets so a tag inside [[#x]] is not double-counted, then URLs so
    # "#frag" is not read as a tag.
    stripped = _strip_code(content)
    stripped = WIKILINK_RE.sub(" ", stripped)
    stripped = URL_RE.sub(" ", stripped)
    tags = {m.group(1).rstrip("-") for m in TAG_RE.finditer(stripped)}
    return {t for t in tags if t}


def extract_properties(content: str) -> dict[str, str | None]:
    """Return properties from line-leading `key:: value` pairs.

    Code is stripped first: sample output and TOML snippets inside a block
    must not turn into page properties.
    """
    props: dict[str, str | None] = {}
    for line in _strip_code(content).splitlines():
        m = PROPERTY_RE.match(line)
        if m:
            key = m.group(1)
            value = m.group(2).strip()
            props[key] = value or None
    return props


def find_link_lines(content: str, target: str, max_lines: int = 3) -> list[str]:
    """Return up to `max_lines` lines that reference `target` via [[wikilink]].

    Case-insensitive; alias form ([[target|alias]]) counts. Used to show
    the quoting line in backlink previews.
    """
    needle = target.casefold()
    out: list[str] = []
    for line in content.splitlines():
        for m in WIKILINK_RE.finditer(line):
            link_target = m.group(1).split("|", 1)[0].strip()
            if link_target.casefold() == needle:
                out.append(line.strip()[:200])
                break
        if len(out) >= max_lines:
            break
    return out


def parse(content: str) -> dict:
    """Parse content and return all structural signals at once."""
    return {
        "links": extract_links(content),
        "tags": extract_tags(content),
        "properties": extract_properties(content),
    }
