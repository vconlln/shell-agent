"""设置持久化与环境自检页的测试契约（计划任务 4）。

前三条用例与计划给的代码逐字一致；其后几条补齐计划没有覆盖、但本任务交付物必须被验证的部分
（默认保存路径、`save()` 的"无参写回 load 路径"语义、设置页的读写往返、自检页重跑探测的接线点）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtCore import QStandardPaths
from PySide6.QtWidgets import QApplication

from tu_shell_agent.types import DetectedTool, DetectionReport
from tu_shell_agent.ui.pages.selfcheck import SelfCheckPage
from tu_shell_agent.ui.pages.settings_page import SettingsPage
from tu_shell_agent.ui.settings import AppSettings, default_settings_path


def test_settings_round_trip(tmp_path):
    path = tmp_path / "settings.json"
    settings = AppSettings.load(path)
    assert settings.blocking_level == "info"      # 默认与引擎一致
    assert settings.max_rounds == 3
    settings.run_root = str(tmp_path / "runs")
    settings.opencode_path = "/opt/opencode"
    settings.save()

    again = AppSettings.load(path)
    assert again.run_root == str(tmp_path / "runs")
    assert again.opencode_path == "/opt/opencode"
    assert json.loads(path.read_text(encoding="utf-8"))["blocking_level"] == "info"


def test_settings_ignores_unknown_keys_and_missing_file(tmp_path):
    """未知键忽略（向前兼容）；文件不存在时给默认值**并且**记住路径。

    第二条不是废话：界面首次运行就是这条路径（没有设置文件），此时若没记住路径，
    用户点一次"保存"就会撞上 ValueError。
    """
    path = tmp_path / "settings.json"
    path.write_text('{"max_rounds": 5, "未来的键": 1}', encoding="utf-8")
    settings = AppSettings.load(path)
    assert settings.max_rounds == 5
    assert not hasattr(settings, "未来的键")

    missing = tmp_path / "还没写过.json"
    fresh = AppSettings.load(missing)
    assert fresh.max_rounds == 3            # 引擎默认值
    assert fresh.loaded_from == missing     # 记住路径，save() 才写得回去
    fresh.save()
    assert missing.is_file()


def test_selfcheck_page_renders_report_and_problems(qtbot):
    page = SelfCheckPage()
    qtbot.addWidget(page)
    page.render(
        DetectionReport(
            opencode=DetectedTool(path="/usr/bin/opencode", version="1.18.31"),
            bash=DetectedTool(path="/usr/bin/bash", version="5.3.15"),
            shellcheck=None,
            problems=("未找到 shellcheck：winget install --id koalaman.shellcheck",),
        )
    )
    text = page.summary_text()
    assert "1.18.31" in text
    assert "5.3.15" in text
    assert "未找到 shellcheck" in text
    assert page.has_problems() is True


def test_settings_save_without_load_path_raises():
    """`save()` 无参时只能写回 load() 记住的路径；没有路径就要立刻报错，不能瞎猜位置。"""
    settings = AppSettings()
    try:
        settings.save()
    except ValueError as exc:
        assert "路径" in str(exc)
    else:  # pragma: no cover - 走到这里说明 save() 静默丢了数据
        raise AssertionError("没有 load() 过的 AppSettings.save() 必须抛 ValueError")


def test_settings_file_never_contains_internal_path_memory(tmp_path):
    """`_loaded_from` 是普通实例属性而不是字段，否则它会被写进 JSON 污染设置文件。"""
    path = tmp_path / "settings.json"
    settings = AppSettings.load(path)
    settings.save()
    assert "_loaded_from" not in json.loads(path.read_text(encoding="utf-8"))


def test_default_settings_path_follows_application_name(qtbot):
    """默认位置按 QApplication.applicationName() 分目录，必须与 app.py 里的应用名一致。"""
    app = QApplication.instance()
    assert app is not None
    original = app.applicationName()
    try:
        app.setApplicationName("tu-shell-agent")
        path = default_settings_path()
        assert path.name == "settings.json"
        assert path.parent.name == "tu-shell-agent"
        base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        assert path == Path(base) / "settings.json"
    finally:
        app.setApplicationName(original)


def test_settings_page_round_trips_widgets_through_file(qtbot, tmp_path):
    path = tmp_path / "settings.json"
    page = SettingsPage()
    qtbot.addWidget(page)
    page.set_settings(AppSettings.load(path))

    page.run_root_edit.setText(str(tmp_path / "runs"))
    page.opencode_path_edit.setText("/opt/opencode")
    page.bash_path_edit.setText("/usr/bin/bash")
    page.blocking_combo.setCurrentText("warning")
    page.max_rounds_spin.setValue(5)
    page.generate_timeout_spin.setValue(600_000)

    page.save_button.click()                       # 走用户真实路径：点"保存"

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["run_root"] == str(tmp_path / "runs")
    assert on_disk["opencode_path"] == "/opt/opencode"
    assert on_disk["bash_path"] == "/usr/bin/bash"
    assert on_disk["blocking_level"] == "warning"
    assert on_disk["max_rounds"] == 5
    assert on_disk["generate_timeout_ms"] == 600_000
    assert path.name in page.status_label.text()   # 用户必须能看见"存到哪了"

    # 重新打开设置页：控件要回填磁盘上的值，否则用户会以为设置丢了
    reopened = SettingsPage()
    qtbot.addWidget(reopened)
    reopened.set_settings(AppSettings.load(path))
    assert reopened.run_root_edit.text() == str(tmp_path / "runs")
    assert reopened.blocking_combo.currentText() == "warning"
    assert reopened.max_rounds_spin.value() == 5
    assert reopened.collect().execute_timeout_ms == 120_000   # 未改动的字段保持原值


def test_settings_ignores_wrongly_typed_values(tmp_path):
    """手改设置文件时类型写错（"3" / 7）不能把界面或引擎带崩：当没写，用默认值。"""
    path = tmp_path / "settings.json"
    path.write_text('{"max_rounds": "五", "run_root": 7}', encoding="utf-8")
    settings = AppSettings.load(path)
    assert settings.max_rounds == 3
    assert settings.run_root == ""


def test_settings_rejects_non_object_json(tmp_path):
    """顶层不是对象的文件要被当成坏文件报出来，而不是抛 AttributeError 之类的怪异常。"""
    path = tmp_path / "settings.json"
    path.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError):
        AppSettings.load(path)


def test_settings_page_falls_back_on_unknown_blocking_level(qtbot, tmp_path):
    """文件被手改坏了（级别不是四个合法值之一）时不能把下拉框设成空，要退回默认 info。"""
    path = tmp_path / "settings.json"
    path.write_text('{"blocking_level": "灾难"}', encoding="utf-8")
    page = SettingsPage()
    qtbot.addWidget(page)
    page.set_settings(AppSettings.load(path))
    assert page.blocking_combo.currentText() == "info"


def test_settings_page_survives_corrupt_file(qtbot, tmp_path, monkeypatch):
    """设置文件损坏时界面必须还能起来，并且如实说明"用的是默认值，保存会覆盖"。"""
    path = tmp_path / "settings.json"
    path.write_text("{ 这不是 json", encoding="utf-8")
    monkeypatch.setattr(
        "tu_shell_agent.ui.pages.settings_page.default_settings_path", lambda: path
    )
    page = SettingsPage()                      # 构造不能抛异常，否则整个窗口都起不来
    qtbot.addWidget(page)
    # 内容坏掉与读不动（权限/占用）在提示里分开：前者"保存会覆盖它"，后者"保存也会失败"
    assert "无法解析" in page.status_label.text()
    assert page.max_rounds_spin.value() == 3


def test_selfcheck_page_reports_and_recheck_signal(qtbot):
    """页面本身不跑探测（那是控制器的活），只把"用户要求重跑"这件事发出去。"""
    page = SelfCheckPage()
    qtbot.addWidget(page)
    assert page.report() is None
    assert page.has_problems() is False            # 还没探测过，不能谎报"有问题"

    report = DetectionReport(
        opencode=None,
        bash=DetectedTool(path="/usr/bin/bash", version="5.3.15"),
        shellcheck=None,
        problems=("未找到 opencode", "未找到 shellcheck"),
    )
    page.render(report)
    assert page.report() is report

    with qtbot.waitSignal(page.recheck_requested, timeout=1000):
        page.recheck_button.click()


def test_default_settings_path_does_not_depend_on_ambient_application_name():
    """设置路径只由 APP_NAME 决定，不受"当前谁在跑"影响。

    之前用 QStandardPaths.AppDataLocation 时它会按 applicationName 分目录：无 QApplication
    时退成 ~/.local/share/settings.json，pytest 下又变成 <临时目录>/pytest-qt-qapp/ ——
    保存与读取指向两个不同文件，症状是"设置保存了、重启却没生效"。
    """
    from PySide6.QtCore import QCoreApplication

    from tu_shell_agent.ui.settings import APP_NAME, default_settings_path

    path = default_settings_path()
    assert path.is_absolute()
    # 这一条才是重点：路径必须落在以应用名命名的目录里（否则保存与读取会指向两个文件）
    assert path.parent.name == APP_NAME

    # 换个应用名再问一次：路径必须一模一样（真应用里 app.py 设的就是 APP_NAME，
    # 但这条不变量不该依赖"谁先谁后调用了什么"）。
    previous = QCoreApplication.applicationName()
    try:
        QCoreApplication.setApplicationName("某个别的名字")
        assert default_settings_path() == path
    finally:
        QCoreApplication.setApplicationName(previous)


def test_selfcheck_page_shows_warnings_separately_from_problems(qtbot):
    """提示与问题必须分开显示，而且提示不算"自检没过"。

    最容易误判的情形：opencode 没保存凭据（auth.json 是空的），但用户用环境变量给了 key
    —— 那时生成正常，只是自检多一条提示。反过来，无凭据且没给 key 时，这一条提示就是
    "自检全绿却一生成就失败"的唯一线索。
    """
    page = SelfCheckPage()
    qtbot.addWidget(page)
    page.render(
        DetectionReport(
            opencode=DetectedTool(path="/usr/bin/opencode", version="1.18.31"),
            bash=DetectedTool(path="/usr/bin/bash", version="5.3.15"),
            shellcheck=DetectedTool(path="/usr/bin/shellcheck", version="0.11.0"),
            problems=(),
            warnings=("opencode 里没有已保存的凭据 —— 先跑一次 `opencode auth login`。",),
        )
    )

    text = page.summary_text()
    assert "提示（不阻断运行）" in text
    assert "opencode auth login" in text
    assert "问题：" not in text          # 没有故障就不该出现"问题"这一节
    assert page.has_problems() is False  # 提示不算故障
    assert page.has_warnings() is True    # 但要说有提示
