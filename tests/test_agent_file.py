from pathlib import Path

from tu_shell_agent.opencode_adapter.agent_file import (
    AGENT_NAME,
    render_agent_file,
    write_agent_file,
)

DENY_KEYS = (
    "bash", "edit", "glob", "grep", "list", "lsp", "skill",
    "task", "todowrite", "question", "webfetch", "websearch", "doom_loop",
)


def test_every_dangerous_permission_is_denied_per_key():
    text = render_agent_file(run_dir="C:/Users/me/runs/r1")
    for key in DENY_KEYS:
        assert f"\n  {key}: deny\n" in text, key
    assert "\n  external_directory: deny\n" in text


def test_read_is_scoped_to_the_run_directory():
    text = render_agent_file(run_dir="C:/Users/me/runs/r1")
    assert '\n    "*": deny\n' in text
    assert '\n    "C:/Users/me/runs/r1/**": allow\n' in text


def test_model_line_absent_by_default_present_when_given():
    assert "\nmodel: " not in render_agent_file(run_dir="/tmp/r1")
    assert "\nmodel: anthropic/claude-sonnet-4\n" in render_agent_file(
        run_dir="/tmp/r1", model="anthropic/claude-sonnet-4"
    )


def test_backslashes_are_normalized_to_forward_slashes():
    text = render_agent_file(run_dir="C:\\Users\\me\\runs\\r1")
    assert "C:/Users/me/runs/r1/**" in text
    assert "\\" not in text.split("---")[1]


def test_frontmatter_is_delimited_by_exactly_two_markers():
    text = render_agent_file(run_dir="/tmp/r1")
    assert text.startswith("---\n")
    assert text.count("\n---\n") == 1


def test_write_agent_file_lands_in_the_run_directory(tmp_path):
    path = write_agent_file(str(tmp_path))
    assert path == str(tmp_path / ".opencode" / "agents" / f"{AGENT_NAME}.md")
    assert Path(path).is_file()
