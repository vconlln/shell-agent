import pytest

from tu_shell_agent.template_store.render import (
    PlaceholderSpec,
    declared_names,
    render_template,
    to_lf,
)

BODY = "#!/usr/bin/env bash\nset -euo pipefail\n# @@TU:BODY@@\necho {{greeting:hello}} {{name}}\n"


def test_replaces_declared_placeholders_using_defaults():
    out = render_template(
        BODY,
        [PlaceholderSpec(name="name", default="world"), PlaceholderSpec(name="greeting")],
        {},
    )
    assert "echo hello world" in out


def test_caller_values_win_over_defaults():
    out = render_template(
        BODY,
        [PlaceholderSpec(name="name"), PlaceholderSpec(name="greeting")],
        {"name": "张三", "greeting": "你好"},
    )
    assert "echo 你好 张三" in out


def test_undeclared_placeholder_raises_instead_of_silently_emptying():
    with pytest.raises(ValueError, match="未声明的占位符.*name"):
        render_template(BODY, [PlaceholderSpec(name="greeting")], {})


def test_crlf_is_normalized_to_lf():
    assert to_lf("line1\r\nline2\r\n") == "line1\nline2\n"


def test_declared_names_lists_placeholders_in_order():
    assert declared_names("a {{x}} b {{y:1}} c {{x}}") == ["x", "y"]
