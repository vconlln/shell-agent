import json

import pytest

from tu_shell_agent.template_store.builtins import BUILTIN_TEMPLATES
from tu_shell_agent.template_store.store import TemplateInput, TemplateStore


def test_first_open_writes_three_builtin_templates(tmp_path):
    store = TemplateStore(str(tmp_path))
    ids = sorted(meta.id for meta in store.list())
    assert ids == ["args-batch", "logged-errors", "single"]
    assert len(BUILTIN_TEMPLATES) == 3


def test_every_builtin_template_has_anchor(tmp_path):
    store = TemplateStore(str(tmp_path))
    for meta in store.list():
        assert "# @@TU:" in store.read(meta.id)


def test_save_then_read_round_trips_and_updates_index(tmp_path):
    store = TemplateStore(str(tmp_path))
    store.save(
        TemplateInput(
            id="mine",
            name="我的模板",
            description="",
            trusted=False,
            placeholders=[],
            body="echo hi # @@TU:BODY@@\n",
        )
    )
    assert "echo hi" in store.read("mine")
    index = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert "mine" in [item["id"] for item in index["templates"]]


def test_remove_deletes_body_file(tmp_path):
    store = TemplateStore(str(tmp_path))
    store.save(
        TemplateInput(
            id="gone", name="g", description="", trusted=False,
            placeholders=[], body="# @@TU:BODY@@\n",
        )
    )
    store.remove("gone")
    with pytest.raises(FileNotFoundError):
        store.read("gone")


def test_illegal_id_is_rejected(tmp_path):
    store = TemplateStore(str(tmp_path))
    with pytest.raises(ValueError, match="非法模板 id"):
        store.save(
            TemplateInput(
                id="../escape", name="x", description="", trusted=False,
                placeholders=[], body="",
            )
        )


def test_hand_dropped_template_file_gets_registered(tmp_path):
    (tmp_path / "hand.tpl.sh").write_text("echo hand # @@TU:BODY@@\n", encoding="utf-8")
    store = TemplateStore(str(tmp_path))
    assert "hand" in [meta.id for meta in store.list()]


def test_set_trusted_persists(tmp_path):
    store = TemplateStore(str(tmp_path))
    store.set_trusted("single", True)
    assert TemplateStore(str(tmp_path)).get("single").trusted is True
