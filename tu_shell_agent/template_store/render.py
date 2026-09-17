"""占位符渲染。只做字符串替换，不引入模板引擎（规格 §8，YAGNI）。"""

from __future__ import annotations

import re
from dataclasses import dataclass

_PLACEHOLDER = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)(?::([^}]*))?\}\}")


@dataclass(frozen=True, slots=True)
class PlaceholderSpec:
    name: str
    default: str | None = None
    description: str | None = None


def to_lf(text: str) -> str:
    """CRLF/CR → LF。Git Bash 遇到 \\r 会报错，入口处统一归一化。"""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def render_template(
    body: str,
    declared: list[PlaceholderSpec],
    values: dict[str, str],
) -> str:
    """未在元数据里声明的占位符 → 抛错，避免生成一个缺参数的脚本。"""
    by_name = {spec.name: spec for spec in declared}
    undeclared: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        name, inline_default = match.group(1), match.group(2)
        spec = by_name.get(name)
        if spec is None:
            undeclared.add(name)
            return ""
        if name in values:
            return values[name]
        if spec.default is not None:
            return spec.default
        return inline_default or ""

    rendered = _PLACEHOLDER.sub(replace, to_lf(body))

    if undeclared:
        raise ValueError(f"模板含未声明的占位符：{', '.join(sorted(undeclared))}")
    return rendered


def declared_names(body: str) -> list[str]:
    """取出模板里声明的占位符名（供 UI 生成表单），按首次出现顺序去重。"""
    seen: dict[str, None] = {}
    for match in _PLACEHOLDER.finditer(to_lf(body)):
        seen.setdefault(match.group(1), None)
    return list(seen)
