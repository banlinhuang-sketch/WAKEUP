"""匹配规则相关的轻量工具函数。"""

from __future__ import annotations

from .models import MatchRule


def match_any(text: str, rules: list[MatchRule]) -> bool:
    """只要任意一条规则命中，就返回 True。"""
    return any(rule.matches(text) for rule in rules)


def parse_rules_text(text: str) -> list[MatchRule]:
    """把多行文本解析成规则对象列表，便于 GUI 文本框和配置对象互转。"""
    rules: list[MatchRule] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.lower().startswith("regex:"):
            rules.append(MatchRule(type="regex", pattern=line[6:].strip()))
        else:
            rules.append(MatchRule(type="keyword", pattern=line))
    return rules


def rules_to_text(rules: list[MatchRule]) -> str:
    """把规则对象回写成多行文本，便于展示和持久化。"""
    lines: list[str] = []
    for rule in rules:
        if rule.type == "regex":
            lines.append(f"regex:{rule.pattern}")
        else:
            lines.append(rule.pattern)
    return "\n".join(lines)
