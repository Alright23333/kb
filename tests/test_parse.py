"""Unit tests for parse.py — the tag/URL boundary rules.

Run with:  python -m unittest discover -s tests -v
(or:       python tests/test_parse.py)

AGENT.md requires running the parse unit tests after touching a regex, so
these live in the repo rather than in a throwaway script.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parse import extract_links, extract_properties, extract_tags, parse


class TestTags(unittest.TestCase):
    def test_plain_tag(self):
        self.assertEqual(extract_tags("#tag"), {"tag"})

    def test_tag_at_line_start(self):
        self.assertEqual(extract_tags("#tag\n"), {"tag"})

    def test_tag_after_space(self):
        self.assertEqual(extract_tags("hello #tag world"), {"tag"})

    def test_cjk_tag_name(self):
        # The tag body accepts CJK — "工作中" is a normal tag here.
        self.assertEqual(extract_tags("#工作中"), {"工作中"})

    def test_cjk_before_hash_still_a_tag(self):
        # "第12周#Monday" — a CJK char before '#' must NOT cancel the tag.
        # Python's \w matches CJK, so the lookbehind has to be ASCII-only.
        self.assertEqual(extract_tags("第12周#Monday"), {"Monday"})

    def test_cjk_text_before_hash_tag(self):
        self.assertEqual(extract_tags("早起#画画 ，终于"), {"画画"})

    def test_ascii_word_before_hash_is_not_a_tag(self):
        # Mid-word hash: "C#" / "a#b@c" are not tags.
        self.assertEqual(extract_tags("C#"), set())
        self.assertEqual(extract_tags("a#b@c"), set())
        self.assertEqual(extract_tags("foo#bar"), set())

    def test_heading_is_not_a_tag(self):
        self.assertEqual(extract_tags("# Heading"), set())
        self.assertEqual(extract_tags("## Heading"), set())
        self.assertEqual(extract_tags("### Third level"), set())

    def test_org_marker_is_not_a_tag(self):
        self.assertEqual(extract_tags("#+BEGIN_ABC"), set())
        self.assertEqual(extract_tags("#+END_ABC"), set())

    def test_double_hash_is_not_a_tag(self):
        self.assertEqual(extract_tags("a##b"), set())

    def test_code_block_is_skipped(self):
        content = "```\n#notatag\n```\n#realtag"
        self.assertEqual(extract_tags(content), {"realtag"})

    def test_inline_code_is_skipped(self):
        self.assertEqual(extract_tags("`#notatag` #realtag"), {"realtag"})

    def test_wikilink_internals_are_skipped(self):
        self.assertEqual(extract_tags("[[#anchor]]"), set())
        self.assertEqual(extract_tags("[[Page]] #realtag"), {"realtag"})

    def test_url_fragment_is_not_a_tag(self):
        # A '#' inside a URL is a fragment, never a tag.
        self.assertEqual(extract_tags("https://x.com/#frag"), set())
        self.assertEqual(extract_tags("[a](https://x.com/#grid12)"), set())
        self.assertEqual(extract_tags("see https://x.com/#cookie now"), set())

    def test_url_with_tag_like_path(self):
        content = "[夸克网盘](https://alistgo.com/zh/guide/drivers/quark.html#cookie)"
        self.assertEqual(extract_tags(content), set())

    def test_trailing_hyphen_stripped(self):
        self.assertEqual(extract_tags("#tag-"), {"tag"})

    def test_punctuation_terminates_tag(self):
        self.assertEqual(extract_tags("（#全角括号）"), {"全角括号"})
        self.assertEqual(extract_tags("#tag."), {"tag"})

    def test_multiple_tags(self):
        self.assertEqual(extract_tags("#a #b #c"), {"a", "b", "c"})

    def test_no_tags(self):
        self.assertEqual(extract_tags("plain text"), set())
        self.assertEqual(extract_tags(""), set())


class TestLinks(unittest.TestCase):
    def test_plain_link(self):
        self.assertEqual(extract_links("[[Foo]]"), {"Foo"})

    def test_alias_uses_target(self):
        self.assertEqual(extract_links("[[Foo|bar]]"), {"Foo"})

    def test_case_not_deduped_in_python(self):
        # The set keeps both spellings; dedup happens at the DB layer.
        self.assertEqual(len(extract_links("[[Foo]] [[foo]]")), 2)


class TestProperties(unittest.TestCase):
    def test_simple_property(self):
        self.assertEqual(extract_properties("key:: value"), {"key": "value"})

    def test_list_bullet_property(self):
        self.assertEqual(extract_properties("- key:: value"), {"key": "value"})

    def test_empty_value(self):
        self.assertEqual(extract_properties("key::"), {"key": None})

    def test_not_a_property(self):
        self.assertEqual(extract_properties("just text"), {})


class TestParse(unittest.TestCase):
    def test_parse_returns_all_signals(self):
        sig = parse("[[Link]] #tag\nkey:: value")
        self.assertEqual(sig["links"], {"Link"})
        self.assertEqual(sig["tags"], {"tag"})
        self.assertEqual(sig["properties"], {"key": "value"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
