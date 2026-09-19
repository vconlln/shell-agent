"""外观设置：界面缩放、字体、背景效果（半透明 / 亚克力模糊）。

三件事的"能验证"程度不同，测试也照实分开：
- **缩放与字体**：纯本地行为，可以精确断言（字号、QSS 里的尺寸、字体族）。
- **半透明**：可断言底色带 alpha、窗口属性已设；但"透出来好不好看"只能人看。
- **模糊**：只有平台能给。开发机（Linux/Wayland）拿不到，所以这里断言的是**诚实**：
  拿不到时必须报告 False 并如实提示，而不是假装成功。
"""

from __future__ import annotations

import json

import pytest
from PySide6.QtWidgets import QApplication

from tu_shell_agent.ui.main_window import MainWindow
from tu_shell_agent.ui.settings import AppSettings
from tu_shell_agent.ui.theme import (
    apply_theme,
    backdrop_colors,
    build_stylesheet,
    scaled_font_size,
    sized,
)


@pytest.fixture
def restore_app(qtbot):
    """还原全局样式表/调色板/字体，别把外观泄漏给同一会话里的其它测试。"""
    app = QApplication.instance()
    previous = (app.styleSheet(), app.palette(), app.font().pointSizeF())
    try:
        yield app
    finally:
        sheet, palette, point_size = previous
        app.setStyleSheet(sheet)
        app.setPalette(palette)
        font = app.font()
        font.setPointSizeF(point_size)
        app.setFont(font)


def _window(tmp_path, **fields) -> MainWindow:
    settings = AppSettings(run_root=str(tmp_path / "runs"), templates_dir=str(tmp_path / "tpl"), **fields)
    return MainWindow(wire_controller=False, settings=settings)


# ── 界面缩放 ──────────────────────────────────────────────────────────────


def test_scale_changes_font_size_and_all_sizes(restore_app, qtbot, tmp_path):
    """缩放要同时放大**字号与尺寸**（只放大字号会让界面变挤）。"""
    window = _window(tmp_path, ui_scale=1.3)
    qtbot.addWidget(window)
    window.show()
    window.apply_appearance()

    assert restore_app.font().pointSizeF() == pytest.approx(scaled_font_size(1.3))
    sheet = build_stylesheet(scale=1.3)
    assert sized("radius", 1.3) in sheet
    assert sized("radius_lg", 1.3) in sheet
    assert sized("row_height", 1.3) in sheet
    # 颜色不随缩放变（缩放乘颜色没有意义）
    assert "#101114" in sheet


def test_default_scale_keeps_the_original_sizes(restore_app, qtbot, tmp_path):
    """默认缩放 = 1.0，尺寸与字号与主题基准一致（不能让默认值偷偷改版式）。"""
    window = _window(tmp_path)
    qtbot.addWidget(window)
    window.show()
    window.apply_appearance()

    assert restore_app.font().pointSizeF() == pytest.approx(9.75)
    assert sized("radius") == "10px"
    assert restore_app.palette().window().color().name() == "#101114"


def test_scaled_controls_really_grow(restore_app, qtbot, tmp_path):
    """缩放要落到真实控件上（像素级）：按钮与输入框应当变高。"""
    normal = _window(tmp_path, ui_scale=1.0)
    qtbot.addWidget(normal)
    normal.show()
    normal.apply_appearance()
    qtbot.wait(30)
    base_height = normal.start_button.height()

    big = _window(tmp_path, ui_scale=1.5)
    qtbot.addWidget(big)
    big.show()
    big.apply_appearance()
    qtbot.wait(30)

    assert big.start_button.height() > base_height, (
        f"缩放 1.5 之后按钮没变高（{base_height} → {big.start_button.height()}）"
    )


# ── 字体 ──────────────────────────────────────────────────────────────────


def test_chosen_mono_font_lands_in_the_stylesheet(restore_app, qtbot, tmp_path):
    """选了等宽字体就要真的用上（脚本/报告/输出这些区域）。"""
    target = "DejaVu Sans Mono"
    window = _window(tmp_path, mono_font=target)
    qtbot.addWidget(window)
    window.show()
    window.apply_appearance()

    sheet = restore_app.styleSheet()
    assert f'font-family: "{target}"' in sheet
    assert "QPlainTextEdit#scriptView" in sheet and "QPlainTextEdit#outputView" in sheet


def test_chosen_ui_font_lands_in_the_stylesheet(restore_app, qtbot, tmp_path):
    window = _window(tmp_path, ui_font="Noto Sans")
    qtbot.addWidget(window)
    window.show()
    window.apply_appearance()

    assert 'QWidget { font-family: "Noto Sans"; }' in restore_app.styleSheet()


def test_settings_page_offers_fonts_and_scale(qtbot):
    """设置页要给出缩放控件与两个字体下拉（含"自动/系统默认"选项）。"""
    from tu_shell_agent.ui.pages.settings_page import SettingsPage

    page = SettingsPage()
    qtbot.addWidget(page)
    # 字体列表是**延迟填充**的（2100+ 字族 × 两个下拉，建一次很贵），所以要先显示出来
    page.show()
    qtbot.waitExposed(page)

    assert page.ui_scale_spin.minimum() <= 1.0 <= page.ui_scale_spin.maximum()
    assert page.ui_font_combo.itemData(0) == ""          # 第一项 = 系统默认
    assert page.mono_font_combo.itemData(0) == ""        # 第一项 = 自动
    assert page.ui_font_combo.count() > 1
    assert page.backdrop_combo.count() == 3


# ── 背景效果 ──────────────────────────────────────────────────────────────


def test_default_backdrop_is_fully_opaque(restore_app, qtbot, tmp_path):
    """默认必须完全不透明：半透明会影响可读性，不该是默认。"""
    window = _window(tmp_path)
    qtbot.addWidget(window)
    window.show()
    window.apply_appearance()

    assert restore_app.palette().window().color().alpha() == 255
    colors = backdrop_colors("off")
    assert colors["bg"] == "#101114"


def test_translucent_backdrop_makes_surfaces_transparent(restore_app, qtbot, tmp_path):
    """半透明：底色带 alpha，并且窗口属性真的设上了。"""
    from PySide6.QtCore import Qt

    window = _window(tmp_path, backdrop="translucent")
    qtbot.addWidget(window)
    window.show()
    window.apply_appearance()

    assert restore_app.palette().window().color().alpha() < 255
    assert restore_app.palette().base().color().alpha() < 255
    assert window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground) is True
    # 文字与强调色不能被一起做透明 —— 那会直接毁掉对比度
    assert restore_app.palette().windowText().color().alpha() == 255


def test_blur_reports_honestly_when_the_platform_cannot_do_it(restore_app, qtbot, tmp_path):
    """拿不到模糊时必须如实报 False 并给提示，不能假装成功。

    开发机是 Linux/Wayland（Niri），没有给普通应用的模糊接口；Windows 11 上才可能为 True。
    """
    from tu_shell_agent.ui import backdrop as backdrop_module

    window = _window(tmp_path, backdrop="blur")
    qtbot.addWidget(window)
    window.show()
    # 直接问平台能不能模糊 —— 断言要跟**平台的真实能力**比，而不是跟窗口自己报的标志比。
    # （第一版就是拿标志当基准，于是"把标志写死成 True"这个变异体照样通过。）
    platform_can = backdrop_module.try_enable_blur(window)
    window.apply_appearance()

    assert window.blur_available == platform_can, "模糊可用性不能谎报"
    if platform_can:
        assert window._appearance_hint() == ""            # 真拿到了就别报丧
    else:
        assert "不支持" in window._appearance_hint()
        assert "不支持" in window.status_label.text()
    # 无论模糊成不成，半透明都要生效（模糊是叠加在半透明之上的）
    assert window.testAttribute(
        __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.WidgetAttribute.WA_TranslucentBackground
    ) is True


def test_appearance_settings_round_trip(tmp_path):
    """四个外观字段要能存进设置文件并读回来。"""
    path = tmp_path / "settings.json"
    settings = AppSettings.load(path)
    settings.ui_scale = 1.25
    settings.ui_font = "Noto Sans"
    settings.mono_font = "JetBrains Mono"
    settings.backdrop = "translucent"
    settings.save()

    again = AppSettings.load(path)
    assert (again.ui_scale, again.ui_font, again.mono_font, again.backdrop) == (
        1.25, "Noto Sans", "JetBrains Mono", "translucent",
    )
    assert json.loads(path.read_text(encoding="utf-8"))["ui_scale"] == 1.25


def test_appearance_applies_immediately_without_saving(restore_app, qtbot, tmp_path):
    """改外观控件要**立刻**生效，不必先点"保存"。

    用户反馈"我改了缩放之后不管用"——原因就是原来只在"保存"回调里应用外观。
    预览只改内存；落盘仍由"保存"负责（这一点下面的文件断言锁住）。
    """
    path = tmp_path / "settings.json"
    settings = AppSettings.load(path)
    settings.run_root = str(tmp_path / "runs")
    window = MainWindow(wire_controller=False, settings=settings)
    qtbot.addWidget(window)
    window.show()
    window.apply_appearance()
    qtbot.wait(30)

    before = restore_app.font().pointSizeF()
    window.settings_page.ui_scale_spin.setValue(1.3)      # 只改控件，不点保存
    qtbot.wait(50)

    assert restore_app.font().pointSizeF() > before, "改缩放后没有立刻生效"
    assert not path.exists(), "预览不该写文件（落盘仍由「保存」负责）"


def test_saved_font_survives_opening_the_settings_page(restore_app, qtbot, tmp_path):
    """已保存的字体在**打开设置页之后**必须还在（用户报的"有时生效有时不生效"）。

    真实场景是跨重启的：上次选了字体 → 存盘 → 下次启动，`set_settings` 先把值放进控件
    （但字体列表还没填充，下拉里只有"自动"一项）→ 用户点开设置页 → 延迟填充发生。
    丢设置的原因（a）是本用例锁住的：填充前用 `combo.currentData()` 当"当前值"，拿到的永远是
    空串（那时下拉里只有"自动"一项）→ 填完把选择重置回"自动"。
    另有一处防御性处理（填充时 blockSignals）**在本流程里等价、测不出来**：因为"自动"项已经占了
    index 0，addItem 不会引发 currentIndexChanged。留着它是防将来列表为空时才填充的情形，
    不声称它修了什么。
    用例必须**先有已保存的字体、再看设置页** —— 第一版是在显示之后才选字体，变异体根本触发不到。
    """
    target = "DejaVu Sans Mono"
    path = tmp_path / "settings.json"
    settings = AppSettings.load(path)
    settings.run_root = str(tmp_path / "runs")
    settings.mono_font = target              # 模拟"上次已经选过并保存"
    settings.save()

    window = MainWindow(wire_controller=False, settings=settings)
    qtbot.addWidget(window)
    window.show()
    window.apply_appearance()

    # 点开设置页（延迟填充在这里发生）
    window.tool_tabs.setCurrentWidget(window.settings_page)
    qtbot.waitExposed(window.settings_page)
    qtbot.wait(30)

    combo = window.settings_page.mono_font_combo
    assert combo.count() > 1, "显示之后字体列表应当已填充"
    assert combo.currentData() == target, "打开设置页把已保存的字体重置了"
    assert settings.mono_font == target, "设置里的字体被即时预览冲掉了"
