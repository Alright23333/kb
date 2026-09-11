"""Markdown parsing for Logseq-style syntax.

Extracts the structural signals from raw markdown:
- `[[wiki links]]` -> links (with optional `|alias`)
- `#tags`          -> tags (excluding headings, [[..]] internals, URLs)
- `key:: value`    -> properties (line-leading, optional list bullet)

All functions are pure (no I/O) so both the API layer and the importer
can share them.
"""
import re

# [[target]] or [[target|alias]]
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")

# A tag: '#' immediately followed by non-space/non-bracket chars.
# Excludes headings ("# ", "## " via lookbehind) and org markers ("#+BEGIN_...").
TAG_RE = re.compile(r"(?<!#)#(?!\+)([^\s#\[\]()]+)")

# Property line: optional list bullet, then `key:: value`.
PROPERTY_RE = re.compile(r"^\s*-?\s*([^\s:：]+)::\s*(.*)$")


def extract_links(content: str) -> set[str]:
    """Return the set of link targets from `[[...]]`, resolving aliases."""
    targets: set[str] = set()
    for m in WIKILINK_RE.finditer(content):
        inner = m.group(1).strip()
        # [[target|alias]] -> target is before the first '|'
        target = inner.split("|", 1)[0].strip()
        if target:
            targets.add(target)
    return targets


def extract_tags(content: str) -> set[str]:
    """Return the set of tags from `#tag`, ignoring [[..]] internals and headings."""
    # Strip wiki-link brackets so a tag inside [[#x]] is not double-counted.
    stripped = WIKILINK_RE.sub(" ", content)
    return {m.group(1) for m in TAG_RE.finditer(stripped)}


def extract_properties(content: str) -> dict[str, str | None]:
    """Return properties from line-leading `key:: value` pairs."""
    props: dict[str, str | None] = {}
    for line in content.splitlines():
        m = PROPERTY_RE.match(line)
        if m:
            key = m.group(1)
            value = m.group(2).strip()
            props[key] = value or None
    return props


def parse(content: str) -> dict:
    """Parse content and return all structural signals at once."""
    return {
        "links": extract_links(content),
        "tags": extract_tags(content),
        "properties": extract_properties(content),
    }
