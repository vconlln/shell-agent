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

from conftest import Grab
from PySide6.QtWidgets import QApplication

from tu_shell_agent.ui.main_window import MainWindow
from tu_shell_agent.ui.settings import AppSettings
from tu_shell_agent.ui.theme import (
    TOKENS,
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


def _render_alpha(window, point) -> int:
    """把窗口渲染到**透明**画布上，取该点的 alpha —— 直接量"这扇窗盖住了多少桌面"。

    不能再用"品红/绿双画布"那招：`paintEvent` 会先把这次要重绘的区域**擦成透明**
    （半透明窗口不擦就会留下上一次的像素 = 重影），画布自己的颜色会被一起擦掉，
    于是两种底色的渲染结果永远相同、量不出任何东西。
    """
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QColor, QImage, QPainter

    image = QImage(window.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(0, 0, 0, 0))
    painter = QPainter(image)
    window.render(painter, QPoint(0, 0))
    painter.end()
    return image.pixelColor(point).alpha()


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
    assert modes == {"off", "translucent", "acrylic"}, (
        "背景效果只留三种：不透明 / 半透明 / 亚克力模糊（模糊来源自动选择）"
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


def test_backdrop_modes_paint_a_self_drawn_layer(restore_app, qtbot, tmp_path):
    """两种"透明"效果都由**界面自绘底色**，窗口本身不透明。

    为什么不问系统要（用户 Windows 11 实测两个都没生效）：
      1. Windows 上 `WA_TranslucentBackground` 必须搭配无边框窗口才生效，而我们保留原生标题栏；
      2. Windows 11 的 DWM 亚克力要求窗口没有重定向位图（`WS_EX_NOREDIRECTIONBITMAP`），
         Qt 的普通窗口有，所以 `DwmSetWindowAttribute` 返回成功、背景也显示不出来。
    于是两端统一成自绘：半透明 = 壁纸不模糊 + 淡色调，亚克力 = 壁纸模糊 + 浓色调。
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QImage

    wallpaper = tmp_path / "wall.png"
    image = QImage(160, 100, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(200, 180, 150))
    image.save(str(wallpaper))

    for mode in ("translucent", "acrylic"):
        window = _window(tmp_path, backdrop=mode, acrylic_wallpaper=str(wallpaper))
        qtbot.addWidget(window)
        window.resize(1200, 800)
        window.show()
        window.apply_appearance()
        assert window._effective_backdrop == mode
        assert window._acrylic_image is not None, f"{mode} 模式没有铺底色层"
        assert window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground) is False, (
            "窗口必须保持不透明：半透明属性在 Windows 上要无边框窗口才生效（用户实测不生效）"
        )
        window.close()

    plain = _window(tmp_path, backdrop="off")
    qtbot.addWidget(plain)
    plain.show()
    plain.apply_appearance()
    assert plain._acrylic_image is None


def test_no_wallpaper_falls_back_to_a_solid_background(restore_app, qtbot, tmp_path, monkeypatch):
    """没有壁纸就画不出"透明"，退回纯色底并如实说明（不能留一块假的透明）。"""
    from tu_shell_agent.ui import acrylic as acrylic_module

    monkeypatch.setattr(acrylic_module, "find_wallpaper", lambda *a, **k: None)
    window = _window(tmp_path, backdrop="translucent")
    qtbot.addWidget(window)
    window.resize(1200, 800)
    window.show()
    window.apply_appearance()

    assert window._effective_backdrop == "off"
    assert window._acrylic_image is None
    assert "未找到壁纸图片" in window.status_label.text()


def test_translucent_is_sharp_and_acrylic_is_blurred(restore_app, qtbot, tmp_path):
    """两个模式必须**看得出区别**：一个不模糊、一个模糊。

    用户点过这条："系统提供和界面自绘没有区别了" —— 合并选项之后，区别必须落在"糊不糊"上。
    """
    from PySide6.QtGui import QColor, QImage, QPainter

    wallpaper = tmp_path / "edge.png"
    image = QImage(400, 200, QImage.Format.Format_ARGB32_Premultiplied)
    painter = QPainter(image)
    painter.fillRect(0, 0, 200, 200, QColor(0, 0, 0))
    painter.fillRect(200, 0, 200, 200, QColor(255, 255, 255))
    painter.end()
    image.save(str(wallpaper))

    def transition_width(mode: str) -> int:
        window = _window(tmp_path, backdrop=mode, acrylic_wallpaper=str(wallpaper))
        qtbot.addWidget(window)
        window.resize(400, 200)
        window.show()
        window.apply_appearance()
        layer = window._acrylic_image
        row = [layer.pixelColor(x, layer.height() // 2).red() for x in range(layer.width())]
        window.close()
        return sum(1 for value in row if 30 < value < 225)

    sharp = transition_width("translucent")
    blurred = transition_width("acrylic")
    assert blurred > sharp + 4, f"亚克力没有比半透明更糊（过渡像素 {blurred} vs {sharp}）"


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
    window.open_console(window.settings_page)      # 设置页搬进控制台弹窗了
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


def test_dialogs_follow_the_current_backdrop_mode(restore_app, qtbot, tmp_path):
    """对话框是**独立顶层窗口**，必须自己跟随当前的背景模式。

    （以前断言的是 `WA_TranslucentBackground`；现在两种透明效果都由界面自绘、窗口统一
    不透明，所以判据改成"模式跟着走，且不会被设成半透明窗口"。）
    """
    from PySide6.QtCore import Qt

    from tu_shell_agent.ui import backdrop as backdrop_module
    from tu_shell_agent.ui.widgets.confirm_dialog import ConfirmDialog

    window = _window(tmp_path, backdrop="acrylic")
    qtbot.addWidget(window)
    window.show()
    window.apply_appearance()
    assert backdrop_module.current_mode() == "acrylic"

    dialog = ConfirmDialog(1, "/tmp/script.sh", "echo hi")
    qtbot.addWidget(dialog)
    assert dialog.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground) is False

    window.settings.backdrop = "off"
    window.apply_appearance()
    assert backdrop_module.current_mode() == "off"


def test_rendered_surfaces_let_the_backdrop_through(restore_app, qtbot, tmp_path):
    """半透明模式下，界面表面必须**看得出壁纸的影响**（而不是一片纯色）。

    窗口现在始终不透明，不能再量 alpha；判据换成"与纯色模式相比颜色确实不同"。
    """
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QColor, QImage, QPainter

    wallpaper = tmp_path / "wall.png"
    image = QImage(200, 120, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(210, 170, 120))          # 明显的暖色；纯色底是冷灰
    image.save(str(wallpaper))

    def render(mode: str) -> QImage:
        window = _window(tmp_path, backdrop=mode, acrylic_wallpaper=str(wallpaper))
        qtbot.addWidget(window)
        window.resize(1200, 800)
        window.show()
        window.apply_appearance()
        shot = QImage(window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        shot.fill(QColor(0, 0, 0, 0))
        painter = QPainter(shot)
        window.render(painter, QPoint(0, 0))
        painter.end()
        window.close()
        return shot

    plain = render("off")
    layered = render("translucent")
    # 取样点必须落在**真正的空白**上：输入卡片那一带的右下角现在是发送圆点（主色不透明），
    # 打在它上面量到的是按钮自己的蓝，看不出壁纸有没有透出来（改这一版时踩到过）。
    for label, point in (
        ("右列对话面板空白处", QPoint(1150, 150)),
        ("对话记录区", QPoint(900, 300)),
        ("脚本视图", QPoint(600, 400)),
    ):
        a, b = plain.pixelColor(point), layered.pixelColor(point)
        delta = abs(a.red() - b.red()) + abs(a.green() - b.green()) + abs(a.blue() - b.blue())
        assert delta >= 12, f"{label} 与纯色模式几乎一样（差 {delta}），壁纸没有透出来"


def test_opaque_mode_keeps_every_surface_solid(restore_app, qtbot, tmp_path):
    """关掉背景效果时必须回到完全不透明 —— 默认外观不能被"透明改造"顺带改掉。"""
    from PySide6.QtCore import QPoint

    window = _window(tmp_path, backdrop="off")
    qtbot.addWidget(window)
    window.show()
    window.apply_appearance()

    for name, widget in {
        "脚本视图": window.center_pane.script_view,
        "输出视图": window.right_pane.output_view,
    }.items():
        center = widget.mapTo(window, QPoint(widget.width() // 2, widget.height() // 2))
        assert _render_alpha(window, center) == 255, f"{name} 在 off 模式下被透出来了"


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

    # 控制台弹窗的页签条（工具区搬进弹窗之后，页签都在这里）
    window.open_console(window.history_page)
    bar = window.tool_tabs.tabBar()
    grab = Grab(window.console_dialog)      # 弹窗是顶层窗口，只它的帧里能取到色

    def pixel(offset):
        point = bar.mapTo(window.console_dialog, bar.rect().topLeft() + offset)
        color = grab.color(point.x(), point.y())
        return (color.red(), color.green(), color.blue(), color.alpha())

    # 条内纯底色取样：页签上方的内边距，不会落在文字或选中药丸上
    strip = max(
        (pixel(QPoint(x, y)) for y in (2, 3) for x in (bar.width() // 3, bar.width() // 2)),
        key=lambda c: sum(c[:3]),
    )
    corner = pixel(QPoint(1, 1))

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
        window.open_console(window.settings_page)      # 设置页搬进控制台弹窗了
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
        "acrylic": "亚克力模糊",
    }, f"背景效果的模式名称被改动了：{labels}"

    # 提示与状态文字里不许出现口语 / 括号旁白；并且要说明系统模糊的可用平台
    banned = (
        "问系统要", "多半", "不假装", "挑一个", "不需要系统支持", "（不模糊）",
        "生成脚本用",          # 用户点名：标签不该是"用"结尾的短语
        "测试替身", "还没回复完", "可以继续问", "点它",
        "就好", "一下", "咱们",
    )
    texts = []
    for mode in ("acrylic",):
        page.backdrop_combo.setCurrentIndex(page.backdrop_combo.findData(mode))
        texts.append(page.backdrop_hint.text())
    window.settings.backdrop = "blur"
    window.apply_appearance()
    texts.extend([window.status_label.text(), window._appearance_hint()])
    for text in texts:
        for phrase in banned:
            assert phrase not in text, f"界面文案出现口语：{text!r} 含 {phrase!r}"

    page.backdrop_combo.setCurrentIndex(page.backdrop_combo.findData("acrylic"))
    hint = page.backdrop_hint.text()
    assert "界面自绘" in hint, "提示要说明底色是界面自绘的（不再依赖系统）"
    assert "不依赖系统" in hint


def test_panel_corners_show_the_pane_surface_not_a_deeper_wedge(restore_app, qtbot, tmp_path):
    """面板圆角切开的位置露出的是**它所在栏的底色**，既不是控件自己的底色，也不是更深的杂色。

    用户圈出「校验与输出」栏里"几个深黑的色角"，根因：折叠区块的外壳与内容容器是纯布局容器，
    但 Qt 给它们标了 `WA_StyledBackground` —— `不透明` 模式下没有对应 QSS 规则时，Qt 用调色板的
    Window 色填充，于是在面板圆角外露出一圈既不属于面板、也不属于它背后那层的楔形。

    现在的布局：右栏是右列的「校验与输出」页签（**未选中时页签页不渲染**，取色会拿到
    过期几何 —— 第一版改装后就是这么假红的），所以取色前先把它切到前台。
    取样区里允许出现的只有：面板底色、栏底色、以及两者的描边混色。
    """
    window = _window(tmp_path, backdrop="off", ui_scale=0.8)
    qtbot.addWidget(window)
    window.resize(1400, 950)
    window.show()
    window.apply_appearance()
    window.right_tabs.setCurrentWidget(window.right_pane)
    qtbot.wait(30)

    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QColor, QImage, QPainter

    image = QImage(window.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(255, 0, 255))
    painter = QPainter(image)
    window.render(painter, QPoint(0, 0))
    painter.end()

    pane_bg = (16, 17, 20)       # #101114：栏底色（off 模式下 bg_surface == bg）
    fills = {
        "报告视图": (11, 12, 14),    # #0b0c0e 只读底
        "输出视图": (11, 12, 14),
        "校验报告": (23, 24, 28),    # #17181c 列表底
    }
    for name, widget in (
        ("报告视图", window.right_pane.notes_view),
        ("输出视图", window.right_pane.output_view),
        ("校验报告", window.right_pane.findings_tree),
    ):
        fill = fills[name]
        origin = widget.mapTo(window, widget.rect().topLeft())
        colors = {
            tuple(image.pixelColor(origin.x() + dx, origin.y() + dy).getRgb()[:3])
            for dx in range(0, 11)
            for dy in range(0, 9)
        }
        assert fill in colors, f"{name} 的面板底色没吃到主题"
        assert pane_bg in colors, (
            f"{name} 的圆角外没看到栏底色 {pane_bg} —— 圆角没生效，或者被别的色盖住了"
        )
        # 极角那个像素：圆角生效时它落在圆外，应当是栏底色（或它与面板底色的过渡）；
        # 它等于面板底色就说明这个角是直角。
        extreme = tuple(image.pixelColor(origin.x(), origin.y()).getRgb()[:3])
        assert extreme != fill, f"{name} 的角是直角（角上的像素就是面板底色）"
        assert all(
            min(fill[i], pane_bg[i]) - 2 <= extreme[i] <= max(fill[i], pane_bg[i]) + 2
            for i in range(3)
        ), f"{name} 的角上出现了既不属于面板也不属于栏底色的 {extreme}"
        # 反锯齿只会产生"两种底色之间"的过渡色，不会比两者都深；
        # 出现更深的第三种色就是"深黑角"那一类问题（有东西在角上刷了别的底色）。
        floor = min(sum(fill), sum(pane_bg))
        strays = {color for color in colors if sum(color) < floor - 4}
        assert not strays, f"{name} 的圆角处出现了比面板与栏底色都深的杂色 {sorted(strays)}"


def test_no_rounded_panel_shows_foreign_colors_at_its_corners(restore_app, qtbot, tmp_path):
    """通用判据：每个圆角面板的角上只允许出现**它自己的底色、它背后的底色、以及两者与描边的混色**。

    这是"深黑色角"那一类问题的克星（用户报过一次）。成因有两条，都不是"圆角没生效"：
    1. 纯容器被 Qt 标了 `WA_StyledBackground`，没有 QSS 规则时它会用调色板的 Window 色填充；
    2. `QHeaderView::section` 自带底色。
    两者都是**子控件/容器，不会被父控件的圆角裁剪** —— 于是角上露出一块既不属于面板、
    也不属于它背后那层的颜色，看上去就是一块深黑的小方角。
    """
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QColor, QImage, QPainter

    window = _window(tmp_path, backdrop="off", ui_scale=0.8)
    qtbot.addWidget(window)
    window.resize(1400, 950)
    window.show()
    window.apply_appearance()

    def capture(frame) -> "QImage":
        """把一帧画到品红画布上：圆角切开的位置会露出背后的颜色（品红=没画到）。"""
        image = QImage(frame.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(255, 0, 255))
        painter = QPainter(image)
        frame.render(painter, QPoint(0, 0))
        painter.end()
        return image

    def rgb(image, x: int, y: int) -> tuple[int, int, int]:
        return tuple(image.pixelColor(x, y).getRgb()[:3])

    def between(color, first, second) -> bool:
        return all(
            min(first[index], second[index]) - 2 <= color[index] <= max(first[index], second[index]) + 2
            for index in range(3)
        )

    # 面板现在分散在三处：主窗口的常驻栏、右列的两个页签、控制台弹窗的页签。
    # **没被选中的页签根本不渲染**，硬从一帧里按坐标取色会取到过期几何下的别的控件
    # （这类假红踩过两次），所以每个面板都在"它真的显示着"的那一帧里取色。
    window.right_tabs.setCurrentWidget(window.chat_panel)
    window.open_console(window.history_page)
    qtbot.wait(30)
    chat_frame = capture(window)
    history_frame = capture(window.console_dialog)

    window.open_console(window.templates_pane)
    qtbot.wait(30)
    templates_frame = capture(window.console_dialog)

    window.right_tabs.setCurrentWidget(window.right_pane)
    qtbot.wait(30)
    pane_frame = capture(window)

    # 允许色 = 面板底色 / 背后的底色 / 两者之间的过渡 / **底色与白色描边的过渡**。
    # 描边是白色带 alpha（`border` 与 `border_heavy` 两个令牌），角上的反锯齿像素就是
    # "底色 + 若干白"。这里按令牌算出来，不写死实测值 —— 写死的那个值在把输入类圆角的
    # 半径从 14px 改成 10px 之后就失配了（角上多出的一像素是 `border_heavy` 与底色的混合）。
    _alphas = [
        TOKENS["border"][3] / 255,
        TOKENS["border_heavy"][3] / 255,
        # 输入类控件 hover 时用的是 border_strong。**必须一起允许**：这条用例跑在别的用例
        # 之后时，控件可能还带着 hover 状态 —— Qt 在没有收到 mouse leave 事件时不会清
        # `WA_UnderMouse`（pytest 里没有真实鼠标移动），于是角上的描边是那一档更亮的白。
        TOKENS["border_strong"][3] / 255,
    ]

    def _over_white(base, alpha):
        return tuple(round(base[index] * (1 - alpha) + 255 * alpha) for index in range(3))
    panels = (
        ("脚本视图", window.center_pane.script_view, window, pane_frame),
        ("时间线", window.center_pane.timeline, window, pane_frame),
        ("方案预览", window.left_pane.plan_preview, window, pane_frame),
        ("额外说明", window.left_pane.extra_edit, window, pane_frame),
        ("校验报告", window.right_pane.findings_tree, window, pane_frame),
        ("输出视图", window.right_pane.output_view, window, pane_frame),
        ("报告视图", window.right_pane.notes_view, window, pane_frame),
        ("对话记录", window.chat_panel.transcript, window, chat_frame),
        ("历史列表", window.history_page.list_widget, window.console_dialog, history_frame),
        ("模板列表", window.templates_pane.list_widget, window.console_dialog, templates_frame),
    )
    problems: list[str] = []
    for name, widget, frame, image in panels:
        rect = widget.rect()
        if rect.width() < 20 or rect.height() < 16:
            continue
        origin = widget.mapTo(frame, rect.topLeft())
        if origin.x() < 8 or origin.y() < 8:
            continue
        fill = rgb(image, origin.x() + 6, origin.y() + 6)
        outside = rgb(image, origin.x() - 6, origin.y() + 6)
        for dx in range(0, 9):
            for dy in range(0, 7):
                color = rgb(image, origin.x() + dx, origin.y() + dy)
                allowed = (
                    color in (fill, outside)
                    or between(color, fill, outside)
                    or any(between(color, fill, _over_white(fill, alpha)) for alpha in _alphas)
                    or any(
                        between(color, outside, _over_white(outside, alpha))
                        for alpha in _alphas
                    )
                )
                if not allowed:
                    problems.append(f"{name} 的角上出现了 {color}（自底色 {fill}／背后 {outside}）")
                    break
            else:
                continue
            break
    assert not problems, "；".join(problems)


def test_switching_background_modes_switches_the_layer(restore_app, qtbot, tmp_path):
    """切换背景效果必须真的换底色层（而不是只改了个下拉）。"""
    from PySide6.QtGui import QColor, QImage

    wallpaper = tmp_path / "wall.png"
    image = QImage(120, 80, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(180, 160, 140))
    image.save(str(wallpaper))

    window = _window(tmp_path, backdrop="off", acrylic_wallpaper=str(wallpaper))
    qtbot.addWidget(window)
    window.show()
    window.apply_appearance()
    assert window._acrylic_image is None

    for mode, has_layer in (("translucent", True), ("acrylic", True), ("off", False)):
        window.settings.backdrop = mode
        window.apply_appearance()
        assert (window._acrylic_image is not None) is has_layer, f"{mode} 模式的底色层不对"
        assert window.isVisible(), f"{mode} 模式切换后窗口不可见了"


def test_input_corners_stay_rounded_at_natural_height_and_every_scale(restore_app, qtbot, tmp_path):
    """输入控件的圆角在**自然高度**下、每个缩放档位都要真的画出来。

    用户报的"会话这里不是圆角的"（会话下拉）：输入类的圆角原来写的是 `radius_lg`(14px)，
    而 Qt 在**半径 >= 控件高度的一半**时整个退回直角。单行输入控件没有 min-height，
    高度由字体度量决定 —— 正好卡在边界上：

    | ui_scale | 输入控件自然高度 | 半径 | 结果 |
    | --- | --- | --- | --- |
    | 0.8 | 26px | 11.2px | 圆角（勉强） |
    | 1.0 | 29px | 14px | 圆角（差 0.5px） |
    | 1.4 | 35px | 19.6px | **直角** |
    | 1.6 | 39px | 22.4px | **直角** |

    Windows 的字体度量比这里更矮，1.0 档就已经是直角 —— 所以必须在 Linux 上就能拦住它。
    这条**故意不给控件设最小高度**：老用例 `test_form_controls_are_rounded_...` 设了
    `setMinimumHeight(30)`，正好把半径压到合法区间，于是这个 bug 从它眼皮底下过去了。
    """
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QComboBox, QLineEdit, QSpinBox, QVBoxLayout, QWidget

    from tu_shell_agent.ui.theme import apply_theme, sized

    for scale in (0.8, 1.0, 1.4, 1.6):
        apply_theme(restore_app, scale=scale)
        host = QWidget()
        host.setObjectName("paneCard")
        qtbot.addWidget(host)
        host.resize(320, 260)
        layout = QVBoxLayout(host)
        controls = {
            "QComboBox": QComboBox(),
            "QSpinBox": QSpinBox(),
            "QLineEdit": QLineEdit(),
        }
        for widget in controls.values():
            layout.addWidget(widget)          # 不设最小高度：量的是自然高度
        host.show()
        qtbot.waitExposed(host)
        qtbot.wait(20)

        radius = float(sized("radius", scale).rstrip("px"))
        grab = Grab(host)
        for name, widget in controls.items():
            height = widget.height()
            assert radius < height / 2, (
                f"scale={scale} 时 {name} 高 {height}px、半径 {radius}px —— "
                f"半径不小于半高，Qt 会退回直角"
            )
            corner = widget.mapTo(host, QPoint(1, 1))
            center = widget.mapTo(host, QPoint(widget.width() // 2, widget.height() // 2))
            assert grab.name(corner.x(), corner.y()) != grab.name(center.x(), center.y()), (
                f"scale={scale} 时 {name} 是矩形底（圆角被半径/高度比吃掉了）"
            )
        host.close()


def test_every_input_in_the_window_stays_rounded_at_the_default_scale(restore_app, qtbot, tmp_path):
    """"界面里所有单行输入控件"这条通用断言：半径必须小于半高（含平台字体差异的余量）。

    这条不看像素、只看几何，所以它守的是**同一个 bug 在不同平台上**的表现：
    用 Windows 的字体度量（控件更矮）也必须是圆角。
    """
    from PySide6.QtWidgets import QComboBox, QLineEdit, QSpinBox

    from tu_shell_agent.ui.theme import sized

    window = _window(tmp_path, backdrop="off")
    qtbot.addWidget(window)
    window.resize(1400, 950)
    window.show()
    window.apply_appearance()
    qtbot.wait(30)

    import re

    from PySide6.QtWidgets import QAbstractSpinBox

    # 半径要**从真正生效的样式表里读**，不能读令牌名：写死 `sized("radius", ...)` 的话，
    # 有人把规则改回 `radius_lg` 这条用例照样通过（变异验证抓到了这一点）。
    sheet = QApplication.instance().styleSheet()
    match = re.search(r"QLineEdit[^{]*\{[^}]*border-radius:\s*([\d.]+)px", sheet)
    assert match is not None, "样式表里找不到输入控件的圆角规则"
    radius = float(match.group(1))
    checked = 0
    for kind in (QLineEdit, QSpinBox, QComboBox):
        for widget in window.findChildren(kind):
            if not widget.isVisible() or widget.height() < 8:
                continue
            # QComboBox/QSpinBox 内部各有一个 QLineEdit（它们自己的文本行，19~22px）：
            # 真正决定形状的是外层控件，内部那个不画出自己的圆角，不该按它判。
            if isinstance(widget.parent(), (QComboBox, QAbstractSpinBox)):
                continue
            checked += 1
            assert radius <= widget.height() * 0.45, (
                f"{widget.objectName() or kind.__name__} 高 {widget.height()}px、"
                f"半径 {radius}px —— 余量不足，Qt 在 半径>=半高 时会画直角"
            )
    assert checked >= 4, f"没有检查到输入控件（用例失效了）：{checked}"


def test_inputs_are_a_lighter_field_not_a_black_hole(restore_app, qtbot, tmp_path):
    """输入框比卡片**亮**一档，并且描边看得见 —— 用户实测反馈："会话这里不是圆角的黑底"。

    历史：输入框原本比面板更深（#121317），理由是"形状靠更深的底色表达"。但那个色接近纯黑，
    在半透明壁纸上像一块黑洞、看不出是圆角框。改成反过来：底色比卡片亮一档 + 描边加亮到 16%，
    形状靠"更亮 + 有边"表达。这条按像素钉住方向，免得以后又"顺手调深"。
    """
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtWidgets import QComboBox, QLineEdit, QVBoxLayout, QWidget

    from tu_shell_agent.ui.theme import apply_theme, qcolor

    apply_theme(restore_app)
    host = QWidget()
    host.setObjectName("paneCard")          # 和真实场景一样，父容器是卡片
    qtbot.addWidget(host)
    host.resize(320, 200)
    layout = QVBoxLayout(host)
    line = QLineEdit()
    combo = QComboBox()
    combo.addItem("未发现可恢复的会话")
    for widget in (line, combo):
        # 去掉焦点：获得焦点的输入框描边是蓝色焦点环（#5b71b4），量不到"常态描边"
        widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout.addWidget(widget)
    host.show()
    qtbot.waitExposed(host)
    qtbot.wait(20)

    def luminance(color) -> float:
        return 0.2126 * color.red() + 0.7152 * color.green() + 0.0722 * color.blue()

    card = qcolor("bg_elevated")
    input_fill = qcolor("bg_input")
    select_fill = qcolor("bg_select")
    assert luminance(input_fill) > luminance(card), (
        f"输入框底色 {TOKENS['bg_input']} 不比卡片 {TOKENS['bg_elevated']} 亮 —— 又回到黑底了"
    )
    assert luminance(select_fill) > luminance(input_fill), (
        "选择框（下拉）应当比文本框更亮一档（用户要求：与旁边的按钮同类）"
    )

    grab = Grab(host)
    for name, widget, expect in (("文本框", line, input_fill), ("下拉", combo, select_fill)):
        point = widget.mapTo(host, QPoint(widget.width() - 6, widget.height() // 2))
        got = grab.color(point.x(), point.y())
        assert abs(luminance(got) - luminance(expect)) <= 6, (
            f"{name} 的实际填充是 {got.name()}，与令牌 {expect.name()} 差太多"
        )
        # 描边必须**明显**看得出来：贴边那几个像素里最亮的那个要比填充亮一截。
        # 为什么取一小段的最大值而不是单个像素：150% 缩放（devicePixelRatio 1.5）下 1 逻辑像素
        # 的描边落在哪一列取决于取整，单点取样会取到圆角外的底色（实测亮度差 -4，假红）；
        # 扫 0~3 列取最亮值就与缩放无关了。
        # 阈值 25 是量出来的：白色 16% 描边叠在 #1b1c21 上亮度差 35，8%（旧值）只有 16 ——
        # 取中间值，撤掉"加亮描边"这一改动就会转红。
        # 描边只有 1 个**逻辑**像素（150% 缩放下 1.5 个物理像素），而"逻辑坐标 × ratio"
        # 的取整差一像素就会整段落在填充里（实测亮度差 0 的假红）。所以这里直接在**物理像素**
        # 上扫一小段取最亮值；往外扫到卡片底色也不会误判 —— 卡片比输入框暗，只有描边更亮。
        edge = widget.mapTo(host, QPoint(0, widget.height() // 2))
        first_physical = round(edge.x() * grab.ratio)
        row_physical = round(edge.y() * grab.ratio)
        band = [
            luminance(grab.image.pixelColor(px, row_physical))
            for px in range(first_physical - 2, first_physical + 7)
        ]
        contrast = max(band) - luminance(got)
        assert contrast >= 25, (
            f"{name} 的描边看不出来（边缘与填充亮度差只有 {contrast:.0f}）—— 圆角框没有边界感"
        )


def test_editable_combo_has_no_dark_box_inside(restore_app, qtbot):
    """下拉内部不许出现文本框的底色 —— 浅底下套一个深框是最难看的观感问题。

    `QComboBox` 可编辑时内部是一个 `QLineEdit`，它会被 `QLineEdit` 那条规则命中，
    理论上会在浅色下拉里画出一块**深色方框**。**实测（当前 Qt 6.11 + Fusion）不会**：
    去掉 `QComboBox QLineEdit { background: transparent; }` 那条规则之后，内部还是
    下拉自己的底色（#232428），取不到文本框底色 —— 所以那条规则是**防御性**的。
    这条用例因此守的是**结果**（下拉内部不许出现文本框底色），而不是某一条规则；
    换 Qt 版本或换样式之后如果真出现深框，它会立刻转红。
    """
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QComboBox, QVBoxLayout, QWidget

    from tu_shell_agent.ui.theme import apply_theme, qcolor

    apply_theme(restore_app)
    host = QWidget()
    host.setObjectName("paneCard")
    qtbot.addWidget(host)
    host.resize(320, 120)
    layout = QVBoxLayout(host)
    combo = QComboBox()
    combo.setEditable(True)
    combo.addItems(["deepseek/deepseek-v4-pro"])
    layout.addWidget(combo)
    host.show()
    qtbot.waitExposed(host)
    qtbot.wait(20)

    grab = Grab(host)
    input_fill = qcolor("bg_input").name()
    for x in range(2, combo.width() - 20):
        point = combo.mapTo(host, QPoint(x, combo.height() // 2))
        got = grab.name(point.x(), point.y())
        assert got != input_fill, (
            f"可编辑下拉内部 ({x}, 中间) 是文本框底色 {input_fill} —— 浅底里套了一层深框"
        )


def test_layout_containers_do_not_paint_their_own_background(restore_app, qtbot, tmp_path):
    """纯布局容器不许自己上色 —— 半透明/亚克力模式下会在面板里糊出一条**深色带**。

    用户报的"会话底下的黑色底色"就是它：`#chatSessionRow`（装"会话 + 下拉 + 两个按钮"的
    纯容器）落进通用 `QWidget {{ background-color: bg }}` 规则里，于是整行被盖了一层深色；
    同一类还有 `#proposalBar`、`#controlsRow`（底部控件行）与右列的页签容器 `#rightTabs`。

    实测（均匀壁纸 + 半透明模式）：会话行内的空白处亮度 **46**，面板其他空白处 **68** ——
    差 22，肉眼就是"会话底下一条黑带"。这条按像素钉住：容器内的空白必须与面板的空白同色。
    """
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QWidget

    from tu_shell_agent.ui.main_window import MainWindow
    from tu_shell_agent.ui.settings import AppSettings

    # 均匀壁纸：壁纸本身有明暗时，"这一块比别处暗"可能只是壁纸的明暗，量不准
    wallpaper = tmp_path / "uniform.png"
    image = QImage(320, 200, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(120, 140, 160))
    assert image.save(str(wallpaper))

    apply_theme(restore_app, backdrop="translucent")
    window = MainWindow(
        wire_controller=False,
        settings=AppSettings(
            run_root=str(tmp_path / "runs"),
            templates_dir=str(tmp_path / "tpl"),
            backdrop="translucent",
            acrylic_wallpaper=str(wallpaper),
        ),
    )
    qtbot.addWidget(window)
    window.resize(1400, 900)
    window.show()
    window.apply_appearance()
    window.right_tabs.setCurrentWidget(window.chat_panel)
    qtbot.wait(50)
    assert window._effective_backdrop == "translucent", "壁纸没被采纳，量不出透明层"

    chat = window.chat_panel
    grab = Grab(window)
    panel_origin = chat.mapTo(window, chat.rect().topLeft())

    def luminance(point) -> float:
        color = grab.color(point.x(), point.y())
        return 0.2126 * color.red() + 0.7152 * color.green() + 0.0722 * color.blue()

    session_row = chat.findChild(QWidget, "chatSessionRow")
    assert session_row is not None
    controls_row = chat.findChild(QWidget, "controlsRow")
    assert controls_row is not None

    # 参照点：面板内**不属于任何容器**的空白 —— 取同一列、会话行下方那一带
    reference_y = session_row.geometry().bottom() + 20
    reference = luminance(chat.mapTo(window, QPoint(5, reference_y)))

    # 纯布局容器：里面的空白必须与面板其他空白**一样亮**（容器自己不上色）
    pure_spots: dict[str, QPoint] = {
        "会话行": session_row.mapTo(window, QPoint(5, 2)),
        # 右列页签容器：页签条上方那条边缘
        "右列页签容器": window.right_tabs.mapTo(window, QPoint(window.right_tabs.width() // 2, 1)),
    }
    for name, point in pure_spots.items():
        got = luminance(point)
        assert abs(got - reference) <= 6, (
            f"{name} 的空白处亮度 {got:.0f}，面板其他空白处 {reference:.0f} —— "
            f"容器自己上了色，在透明模式下就是一条深色带（用户报的会话底下的黑色底色）"
        )

    # **输入卡片是例外，而且必须是例外**：它是一张有名字的卡片（`#chatComposer`，
    # 底色 bg_input），不是"放布局的纯容器" —— 参照图里输入区就是一块独立的圆角框。
    # 所以这里反过来断言"它确实比面板底色深"：哪天它被误改成透明，这条会红。
    controls_blank = controls_row.mapTo(
        window,
        QPoint(
            (chat.mode_button.geometry().right() + chat.model_combo.geometry().left()) // 2,
            2,
        ),
    )
    card = luminance(controls_blank)
    assert card < reference - 10, (
        f"输入卡片里的空白亮度 {card:.0f} 与面板底色 {reference:.0f} 几乎一样 —— "
        "输入卡片没有自己的底色（它该是一块独立卡片，不是透明的布局容器）"
    )
