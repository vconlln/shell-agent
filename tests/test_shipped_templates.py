"""随仓库交付的模板库（`templates/`）：内容完整、能被解析、与内置模板一致。"""

from __future__ import annotations

from pathlib import Path

from tu_shell_agent.template_store.builtins import BUILTIN_TEMPLATES
from tu_shell_agent.template_store.store import TemplateStore
from tu_shell_agent.ui.settings import default_templates_dir, repo_templates_dir

REPO = Path(__file__).resolve().parents[1]


def test_repo_templates_dir_is_the_shipped_one():
    """源码检出里，模板库就是仓库的 `templates/`（模板随代码一起版本化）。"""
    assert repo_templates_dir() == REPO / "templates"
    assert default_templates_dir() == REPO / "templates"


def test_shipped_templates_load_and_are_complete():
    """交付的模板要能全部加载，且索引与实际文件一致（没有孤儿条目、没有漏登记的文件）。"""
    directory = REPO / "templates"
    store = TemplateStore(str(directory))
    metas = store.list()
    ids = {meta.id for meta in metas}

    assert "single" in ids and len(metas) >= 3

    bodies = {path.name[: -len(".tpl.sh")] for path in directory.glob("*.tpl.sh")}
    assert bodies == ids, f"索引与文件不一致：索引 {sorted(ids)}，文件 {sorted(bodies)}"

    for meta in metas:
        body = store.read(meta.id)
        assert body.strip(), f"{meta.id} 的模板正文是空的"
        assert body.startswith("#!/usr/bin/env bash"), f"{meta.id} 缺少 shebang"
        # 有锚点的模板，锚点必须写成契约要求的 `# @@TU:名字@@`
        for line in body.splitlines():
            if "@@TU:" in line:
                assert line.strip().startswith("# @@TU:") and line.strip().endswith("@@"), (
                    f"{meta.id} 的锚点写法不对：{line!r}"
                )


def test_shipped_templates_match_the_builtin_seeds():
    """交付的模板应当就是内置种子（用户第一次打开时看到的那三个）。

    这条不是"必须多出一个文件"的洁癖：模板库一旦与内置种子分叉，用户在两台机器上
    会看到不同的模板，而"到底哪个是官方版本"无从判断。
    """
    store = TemplateStore(str(REPO / "templates"))
    shipped = {meta.id: store.read(meta.id) for meta in store.list()}
    for builtin in BUILTIN_TEMPLATES:
        assert builtin.id in shipped, f"交付模板里缺少内置模板 {builtin.id}"
        assert shipped[builtin.id].strip() == builtin.body.strip(), f"{builtin.id} 的正文与内置种子不一致"
