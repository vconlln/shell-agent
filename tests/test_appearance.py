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
    modes = {page.backdrop_combo.itemData(i) for i in range(page.backdrop_combo.count())}
    assert modes == {"off", "translucent", "blur", "acrylic"}, (
        "背景效果要四种：不透明 / 半透明 / 问系统要模糊 / 自绘壁纸模糊"
    )


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
    # 透明度要**看得出来**：第一版给 92%（只有 8% 的壁纸透出来），用户反馈"没有效果"。
    # 这里锁住"至少透出两成"，免得以后又被调回看不见的值。
    assert restore_app.palette().window().color().alpha() <= 210
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
        assert "不可用" in window._appearance_hint()
        assert "不可用" in window.status_label.text()
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


def test_entry_point_applies_appearance_before_showing_the_window():
    """入口必须先应用外观再 show()。

    `WA_TranslucentBackground` 这类窗口属性只有在窗口显示之前设上才稳：先 show 再设，
    属性虽然读出来是 True，窗口表面却已经是不透明的 —— 用户反馈的"半透明没有效果"
    有一半就是这个顺序造成的。这条用源码断言把它钉住（比"跑起来看看"更可靠）。
    """
    import inspect

    from tu_shell_agent.ui import app as app_module

    source = inspect.getsource(app_module.main)
    assert source.index("apply_appearance") < source.index(".show()"), (
        "app.py 里 apply_appearance() 必须在 show() 之前调用"
    )


def test_structural_surfaces_do_not_stack_opacity():
    """开了背景效果后，结构性表面（卡片/面板）不能再各自叠一层底色。

    用户反馈"主工作区的黑底与浅黑底没有透明效果"：窗口 78% + 卡片 76% + 面板 78% 三层叠起来
    合成不透明度 ≈ 99%，肉眼就是不透明。现在只让最外层承担透明度，内部改透明/极淡一层白，
    叠加后 ≈ 79%（桌面能透进主工作区）。
    """
    off = backdrop_colors("off")
    on = backdrop_colors("translucent")

    # 默认模式一点都不能变（像素测试全都建立在"不透明"上）
    assert off["bg_card"] == off["bg_elevated"]
    assert off["bg_surface"] == off["bg"]

    assert on["bg_surface"] == "transparent", "面板不该再自己上一层底色"
    assert isinstance(on["bg_card"], tuple) and on["bg_card"][3] <= 40, "卡片只该留极淡的一层"

    def alpha(value) -> float:
        if value == "transparent":
            return 0.0
        if isinstance(value, tuple):
            return value[3] / 255
        return 1.0

    stacked = 1.0
    for key in ("bg", "bg_card", "bg_surface"):
        stacked *= 1 - alpha(on[key])
    assert 1 - stacked <= 0.85, f"主工作区叠加后仍接近不透明（{1 - stacked:.0%}）"


def test_content_surfaces_are_clearly_translucent(restore_app, qtbot, tmp_path):
    """内容区（脚本/报告/输出/列表）也要"看得出透"。

    第一版只把页面与卡片做成半透明，内容区还留着 72~78% 的 alpha —— 它们占的面积最大，
    所以用户看到的仍是"纯黑底和浅黑底"。
    """
    on = backdrop_colors("translucent")

    def alpha(value) -> float:
        return value[3] / 255 if isinstance(value, tuple) else 1.0

    assert alpha(on["bg_under"]) <= 0.55, "只读内容区（脚本/输出）仍太不透明"
    assert alpha(on["bg_elevated"]) <= 0.62, "列表/树的底色仍太不透明"
    # 输入类**故意更不透明**：那里是读字/写字的地方，51% 时对比度只有 3.1:1（实测），
    # 用户报的"看不见字"就是这一类。所以这里断言的是下限而不是上限。
    assert alpha(on["bg_input"]) >= 0.70, "输入控件太透，里面的字会糊在壁纸上"
    # 文字不跟着透明（对比度靠它）
    from tu_shell_agent.ui.theme import TOKENS as BASE_TOKENS

    assert not isinstance(BASE_TOKENS["fg"], tuple)


def test_dialogs_follow_the_translucent_backdrop(restore_app, qtbot, tmp_path):
    """对话框是**独立顶层窗口**，必须自己带 WA_TranslucentBackground。

    只在主窗口上设这个属性时，弹出来的确认框仍是一整块纯不透明的深色 ——
    用户反馈的"对话框之类的还是纯黑底"就是这个。
    """
    from PySide6.QtCore import Qt

    from tu_shell_agent.ui import backdrop as backdrop_module
    from tu_shell_agent.ui.widgets.confirm_dialog import ConfirmDialog

    window = _window(tmp_path, backdrop="translucent")
    qtbot.addWidget(window)
    window.show()
    window.apply_appearance()

    dialog = ConfirmDialog(1, "/tmp/script.sh", "echo hi\n")
    qtbot.addWidget(dialog)
    assert dialog.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground) is True

    # 关掉背景效果之后，新开的对话框要回到不透明
    window.settings.backdrop = "off"
    window.apply_appearance()
    opaque = ConfirmDialog(1, "/tmp/script.sh", "echo hi\n")
    qtbot.addWidget(opaque)
    assert opaque.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground) is False


def test_rendered_surfaces_let_the_backdrop_through(restore_app, qtbot, tmp_path):
    """**渲染结果**层面的证据：把整个窗口画到品红画布上，看壁纸还能透出多少。

    只断言令牌里的 alpha 不够 —— 真正决定观感的是**合成之后**的结果，而这正是我踩过的坑：
    窗口底自己就是一层 78% 的深色时，内容区再透明，合成后也只剩 22% 能透出来
    （用户反馈的"还是没有完全透明"）。所以这里把整窗渲染到品红上，
    再按控件→窗口的坐标映射取该控件中心的那一个像素，用"品红残留比例"反推等效不透明度。
    """
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QColor, QImage, QPainter

    window = _window(tmp_path, backdrop="translucent")
    qtbot.addWidget(window)
    window.show()
    window.apply_appearance()

    targets = {
        "脚本视图": window.center_pane.script_view,
        "输出视图": window.right_pane.output_view,
        "报告视图": window.right_pane.notes_view,
        "方案预览": window.left_pane.plan_preview,
        "对话记录": window.chat_panel.transcript,
        "运行时间线": window.tool_tabs,
    }

    image = QImage(window.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(255, 0, 255))
    painter = QPainter(image)
    window.render(painter, QPoint(0, 0))
    painter.end()

    for name, widget in targets.items():
        center = widget.mapTo(window, QPoint(widget.width() // 2, widget.height() // 2))
        pixel = image.pixelColor(center)
        bleed = pixel.red() / 255  # 品红残留比例 = 壁纸能透出来的程度
        print(f"[透出量] {name}: rgb={pixel.red(), pixel.green(), pixel.blue()} 透出≈{bleed:.0%}")
        assert bleed >= 0.18, (
            f"{name} 几乎不透：等效不透明≈{1 - bleed:.0%}"
            f"（测得 {pixel.red(), pixel.green(), pixel.blue()}）"
        )
        assert bleed <= 0.75, f"{name} 透得过头，底色没了、文字会看不清"


def test_opaque_mode_keeps_every_surface_solid(restore_app, qtbot, tmp_path):
    """关掉背景效果时必须回到完全不透明 —— 默认外观不能被"透明改造"顺带改掉。"""
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QColor, QImage, QPainter

    window = _window(tmp_path, backdrop="off")
    qtbot.addWidget(window)
    window.show()
    window.apply_appearance()

    image = QImage(window.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(255, 0, 255))
    painter = QPainter(image)
    window.render(painter, QPoint(0, 0))
    painter.end()

    for name, widget in {
        "脚本视图": window.center_pane.script_view,
        "输出视图": window.right_pane.output_view,
    }.items():
        center = widget.mapTo(window, QPoint(widget.width() // 2, widget.height() // 2))
        pixel = image.pixelColor(center)
        assert pixel.red() < 70, f"{name} 在 off 模式下被透出来了：{pixel.red()}"


def test_tab_strip_background_is_rounded(restore_app, qtbot, tmp_path):
    """页签条整条的底色也要是圆角。

    它原来由调色板自绘：整条比周围多叠一层（实测条内 69%、条外 46%），而且是**直角矩形** ——
    用户截图圈出来的就是这一块。现在改成显式上色 + 圆角，角落里露出的应当是父底色。
    """
    from PySide6.QtCore import QPoint

    window = _window(tmp_path, backdrop="translucent")
    qtbot.addWidget(window)
    window.resize(1400, 900)
    window.show()
    window.apply_appearance()

    bar = window.tool_tabs.tabBar()
    image = window.grab().toImage()

    def pixel(offset):
        point = bar.mapTo(window, bar.rect().topLeft() + offset)
        color = image.pixelColor(point)
        return (color.red(), color.green(), color.blue(), color.alpha())

    # 条内纯底色取样：页签上方的内边距，不会落在文字或选中药丸上
    strip = max(
        (pixel(QPoint(x, y)) for y in (2, 3) for x in (bar.width() // 3, bar.width() // 2)),
        key=lambda c: sum(c[:3]),
    )
    corner = pixel(QPoint(1, 1))

    assert strip[3] < 255, "页签条应当是半透明的显式底色，而不是调色板刷的不透明块"
    assert strip != corner, "页签条角落与条内同色 → 还是直角矩形"


def test_combo_popup_stays_readable_in_transparent_modes(restore_app, qtbot, tmp_path):
    """下拉弹层不能跟着变透明：那里是读字的地方。

    用户反馈："选择字体的背景也跟着透明了，看不见字"。实测原因：把页面做成透明之后，
    **页面里的**下拉，其弹出列表会跟着变透明（调色板继承），文字直接浮在壁纸上；
    而且真正要上色的是**弹层容器**那一层，只给列表上色没用。
    判据：把弹层渲染到与用户壁纸同等亮度的底上，行底色必须足够深、与文字色的对比度达标。
    """

    def contrast(fg, bg):
        def channel(value):
            value /= 255
            return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

        def luminance(color):
            r, g, b = (channel(v) for v in color[:3])
            return 0.2126 * r + 0.7152 * g + 0.0722 * b

        high, low = sorted((luminance(fg), luminance(bg)), reverse=True)
        return (high + 0.05) / (low + 0.05)

    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QColor, QImage, QPainter

    wallpaper_brightness = (189, 186, 182)      # 用户那张壁纸的平均亮度（实测）
    text = (236, 237, 240)                      # 主题前景色

    for mode in ("acrylic", "translucent"):
        window = _window(tmp_path, backdrop=mode)
        qtbot.addWidget(window)
        window.resize(1400, 900)
        window.show()
        window.tool_tabs.setCurrentWidget(window.settings_page)
        window.apply_appearance()

        combo = window.settings_page.blocking_combo
        combo.showPopup()
        popup = combo.view().window()
        image = QImage(popup.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(*wallpaper_brightness))
        painter = QPainter(image)
        popup.render(painter, QPoint(0, 0))
        painter.end()

        row = image.pixelColor(popup.width() // 2, 4)
        background = (row.red(), row.green(), row.blue())
        assert contrast(text, background) >= 4.5, (
            f"{mode} 模式下下拉弹层的底色是 {background}，与文字几乎没有对比"
            f"（对比度 {contrast(text, background):.1f}:1）"
        )
        combo.hidePopup()


def test_appearance_text_is_formal(restore_app, qtbot, tmp_path):
    """界面文案一律用书面语：不用口语、不加括号旁白。

    用户明确要求过两次（"问系统要""不需要系统支持"这类）。这条用例把已经定下来的
    四种模式名称与提示语钉住，顺带挡住那几句被点名的口语。
    """
    window = _window(tmp_path, backdrop="off")
    qtbot.addWidget(window)
    page = window.settings_page

    labels = {
        page.backdrop_combo.itemData(index): page.backdrop_combo.itemText(index)
        for index in range(page.backdrop_combo.count())
    }
    assert labels == {
        "off": "不透明（默认）",
        "translucent": "半透明",
        "blur": "亚克力模糊（系统提供）",
        "acrylic": "亚克力模糊（界面自绘）",
    }, f"背景效果的模式名称被改动了：{labels}"

    # 提示与状态文字里不许出现口语 / 括号旁白；并且要说明系统模糊的可用平台
    banned = ("问系统要", "多半", "不假装", "挑一个", "不需要系统支持", "（不模糊）")
    texts = []
    for mode in ("blur", "acrylic"):
        page.backdrop_combo.setCurrentIndex(page.backdrop_combo.findData(mode))
        texts.append(page.backdrop_hint.text())
    window.settings.backdrop = "blur"
    window.apply_appearance()
    texts.extend([window.status_label.text(), window._appearance_hint()])
    for text in texts:
        for phrase in banned:
            assert phrase not in text, f"界面文案出现口语：{text!r} 含 {phrase!r}"

    page.backdrop_combo.setCurrentIndex(page.backdrop_combo.findData("blur"))
    assert "Windows" in page.backdrop_hint.text(), "系统模糊的提示要写明哪些平台可用"
