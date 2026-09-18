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
