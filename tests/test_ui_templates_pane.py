from tu_shell_agent.template_store.store import TemplateStore
from tu_shell_agent.ui.panes.templates import TemplatesPane


def _pane(qtbot, tmp_path) -> TemplatesPane:
    pane = TemplatesPane(store=TemplateStore(str(tmp_path / "templates")))
    qtbot.addWidget(pane)
    pane.reload()
    return pane


def test_reload_lists_builtin_templates(qtbot, tmp_path):
    pane = _pane(qtbot, tmp_path)
    ids = [pane.list_widget.item(i).text() for i in range(pane.list_widget.count())]
    assert set(ids) >= {"single", "args-batch", "logged-errors"}


def test_selecting_template_loads_body_and_placeholders(qtbot, tmp_path):
    pane = _pane(qtbot, tmp_path)
    pane.select("args-batch")
    assert "getopts" in pane.body_edit.toPlainText()
    # 通过公开访问器拿控件，不要靠 QFormLayout.itemAt 猜 label/field 的交替顺序
    assert pane.placeholder_input("script_name") is not None
    assert pane.placeholder_input("work_dir") is not None


def test_placeholder_values_change_rendered_preview(qtbot, tmp_path):
    pane = _pane(qtbot, tmp_path)
    pane.select("args-batch")
    pane.set_placeholder_value("work_dir", "/var/log")
    assert "/var/log" in pane.preview.toPlainText()


def test_trusted_toggle_persists(qtbot, tmp_path):
    pane = _pane(qtbot, tmp_path)
    pane.select("single")
    pane.trusted_check.setChecked(True)
    pane.save_current()
    assert TemplateStore(str(tmp_path / "templates")).get("single").trusted is True


def test_selected_template_spec_is_loop_ready(qtbot, tmp_path):
    pane = _pane(qtbot, tmp_path)
    pane.select("single")
    spec = pane.to_template_spec()
    assert spec.id == "single"
    assert "@@TU:BODY@@" in spec.anchors
    assert spec.trusted is False


def test_empty_placeholder_inputs_do_not_override_template_defaults(tmp_path):
    """空输入框 ≠ 用户填了空值。

    `render_template` 里 values 的优先级高于模板自带的默认值，而输入框建出来就是空的：
    若把空串当成"用户给的值"，选中内置模板后什么都不填就会把默认值顶掉 ——
    `{{work_dir:.}}` 渲染成 `DIR=""`、`{{script_name:task.sh}}` 变成空名字，
    预览里那份会跑失败的骨架正是交给模型生成的基准。
    """
    from tu_shell_agent.template_store.store import TemplateStore
    from tu_shell_agent.ui.panes.templates import TemplatesPane

    pane = TemplatesPane(store=TemplateStore(str(tmp_path / "templates")))
    pane.reload()
    pane.select("args-batch")

    assert pane.placeholder_values() == {}          # 什么都没填 → 不参与渲染
    skeleton = pane.rendered_skeleton()
    assert 'DIR="."' in skeleton                    # 行内默认值活下来了
    assert "task.sh" in skeleton                    # 元数据默认值也活下来了

    pane.set_placeholder_value("work_dir", "/var/log")
    assert pane.placeholder_values() == {"work_dir": "/var/log"}
    assert 'DIR="/var/log"' in pane.rendered_skeleton()


# ── 导入 shell 文件当模板（用户要求"支持我上传 shell 文件来当作模板库"）─────


def _import_from(pane, monkeypatch, paths):
    """走真实的导入入口，只把文件对话框换掉（其余都是真逻辑）。"""
    from PySide6.QtWidgets import QFileDialog

    monkeypatch.setattr(
        QFileDialog, "getOpenFileNames", staticmethod(lambda *a, **k: (list(paths), ""))
    )
    pane.import_from_file()


def test_importing_a_plain_shell_file_makes_a_template(qtbot, tmp_path, monkeypatch):
    """传一个普通 .sh 进来就该成为模板（旧按钮叫「导入 .tpl.sh」，看着就像不收 .sh）。"""
    source = tmp_path / "备份日志.sh"
    source.write_text("#!/usr/bin/env bash\nset -euo pipefail\necho backup\n", encoding="utf-8")
    pane = _pane(qtbot, tmp_path)

    _import_from(pane, monkeypatch, [source])

    ids = [pane.list_widget.item(i).text() for i in range(pane.list_widget.count())]
    # 中文文件名会被清洗成 imported（id 只允许小写字母数字与 -），正文必须原样收下
    imported = [i for i in ids if i not in {"single", "args-batch", "logged-errors"}]
    assert imported == ["imported"], ids
    stored = TemplateStore(str(tmp_path / "templates")).get("imported")
    assert "echo backup" in TemplateStore(str(tmp_path / "templates")).read("imported")
    assert stored.trusted is False, "外来脚本一律先按不可信处理"


def test_importing_a_shell_file_without_anchor_adds_one_and_says_so(qtbot, tmp_path, monkeypatch):
    """没有 `# @@TU:BODY@@` 锚点就在末尾补一行 —— 并**告诉用户补了**。"""
    source = tmp_path / "cleanup.sh"
    source.write_text("#!/usr/bin/env bash\nrm -f /tmp/x\n", encoding="utf-8")
    pane = _pane(qtbot, tmp_path)

    _import_from(pane, monkeypatch, [source])

    body = TemplateStore(str(tmp_path / "templates")).read("cleanup")
    assert body.rstrip().endswith("# @@TU:BODY@@"), body
    assert body.index("rm -f /tmp/x") < body.index("# @@TU:BODY@@"), "锚点必须在末尾"
    assert "# @@TU:BODY@@" in pane.notice_label.text()
    assert "末尾" in pane.notice_label.text()


def test_importing_a_file_that_already_has_an_anchor_is_left_alone(qtbot, tmp_path, monkeypatch):
    """已经有锚点的文件不许再补一个（重复锚点会让契约与提示词都变怪）。"""
    source = tmp_path / "with-anchor.tpl.sh"
    source.write_text(
        "#!/usr/bin/env bash\n# @@TU:BODY@@\nmain() { :; }\n", encoding="utf-8"
    )
    pane = _pane(qtbot, tmp_path)

    _import_from(pane, monkeypatch, [source])

    body = TemplateStore(str(tmp_path / "templates")).read("with-anchor")
    assert body.count("# @@TU:BODY@@") == 1, body
    assert "补一行" not in pane.notice_label.text()


def test_importing_multiple_files_at_once(qtbot, tmp_path, monkeypatch):
    """一次选多个：都进来，逐个报结果（用户要"上传 shell 文件"多半是一批）。"""
    first = tmp_path / "one.sh"
    second = tmp_path / "two.sh"
    first.write_text("#!/usr/bin/env bash\necho one\n", encoding="utf-8")
    second.write_text("#!/usr/bin/env bash\necho two\n", encoding="utf-8")
    pane = _pane(qtbot, tmp_path)

    _import_from(pane, monkeypatch, [first, second])

    store = TemplateStore(str(tmp_path / "templates"))
    assert "echo one" in store.read("one")
    assert "echo two" in store.read("two")
    assert "已导入 2 个模板" in pane.notice_label.text()


def test_importing_an_unreadable_file_is_reported_not_swallowed(qtbot, tmp_path, monkeypatch):
    """GBK 的脚本在 Windows 上很常见：读不了要**如实说**，不能静默什么都不做。"""
    source = tmp_path / "gbk.sh"
    source.write_bytes("#!/usr/bin/env bash\necho 中文\n".encode("gbk"))
    pane = _pane(qtbot, tmp_path)

    _import_from(pane, monkeypatch, [source])

    notice = pane.notice_label.text()
    assert "未导入" in notice and "gbk.sh" in notice, notice
    ids = [pane.list_widget.item(i).text() for i in range(pane.list_widget.count())]
    assert "gbk" not in ids


def test_importing_a_file_keeps_its_placeholders(qtbot, tmp_path, monkeypatch):
    """外壳脚本里的 {{占位符}} 要照样认出来（模板的用法就是先填参数再渲染）。"""
    source = tmp_path / "greet.sh"
    source.write_text(
        '#!/usr/bin/env bash\necho "{{who}}" > "{{out:result.txt}}"\n', encoding="utf-8"
    )
    pane = _pane(qtbot, tmp_path)

    _import_from(pane, monkeypatch, [source])

    body = TemplateStore(str(tmp_path / "templates")).read("greet")
    assert "{{who}}" in body
    stored = TemplateStore(str(tmp_path / "templates")).get("greet")
    names = {spec.name for spec in stored.placeholders}
    assert names == {"who", "out"}

def test_imported_id_drops_the_shell_suffix(qtbot, tmp_path, monkeypatch):
    """`cleanup.sh` 的模板 id 是 `cleanup`，不是 `cleanup-sh`。

    旧逻辑只去 `.tpl.sh`，于是普通 `*.sh` 的那个点会被清洗成横线 —— 用户会看到模板名里
    莫名其妙多出 `-sh`（现在主打导入普通 shell 文件，这条必须去掉）。
    """
    source = tmp_path / "cleanup.sh"
    source.write_text("#!/usr/bin/env bash\necho hi\n", encoding="utf-8")
    pane = _pane(qtbot, tmp_path)

    _import_from(pane, monkeypatch, [source])

    ids = [pane.list_widget.item(i).text() for i in range(pane.list_widget.count())]
    assert "cleanup" in ids, ids
    assert "cleanup-sh" not in ids, ids
