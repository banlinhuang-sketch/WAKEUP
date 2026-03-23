"""匹配规则工具测试。"""

from __future__ import annotations

import unittest

from voice_wakeup_tester.matching import match_any, parse_rules_text
from voice_wakeup_tester.models import MatchRule


class MatchingTests(unittest.TestCase):
    """验证关键字与正则规则的匹配行为。"""

    def test_match_any_supports_keyword_and_regex(self) -> None:
        """关键字和正则两种规则都应能命中。"""
        rules = [
            MatchRule(type="keyword", pattern="WAKEUP_SUCCESS"),
            MatchRule(type="regex", pattern=r"Voice wake up \w+"),
        ]

        self.assertTrue(match_any("hello WAKEUP_SUCCESS world", rules))
        self.assertTrue(match_any("AudioHAL: Voice wake up triggered", rules))
        self.assertFalse(match_any("ordinary log line", rules))

    def test_parse_rules_text(self) -> None:
        """多行文本应能解析为规则对象列表。"""
        rules = parse_rules_text("WAKEUP_SUCCESS\nregex:Voice wake up .*")
        self.assertEqual(len(rules), 2)
        self.assertEqual(rules[0].type, "keyword")
        self.assertEqual(rules[1].type, "regex")


if __name__ == "__main__":
    unittest.main()
