"""自绘亚克力（`ui/acrylic.py`）：找壁纸、模糊、铺到窗口最底层。"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor, QImage, QPainter

from tu_shell_agent.ui import acrylic
from tu_shell_agent.ui.main_window import MainWindow
from tu_shell_agent.ui.settings import AppSettings


def _two_tone(path: Path, width: int = 400, height: int = 200) -> str:
    """造一张左黑右白的图：模糊之后接缝必须变成一片过渡色。"""
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    painter = QPainter(image)
    painter.fillRect(0, 0, width // 2, height, QColor(0, 0, 0))
    painter.fillRect(width // 2, 0, width // 2, height, QColor(255, 255, 255))
    painter.end()
    image.save(str(path))
    return str(path)


def _transitions(image: QImage, row: int) -> list[int]:
    """一行里"既不是黑也不是白"的像素（硬边一个都没有，模糊后有一片）。"""
    return [
        value
        for value in (image.pixelColor(x, row).red() for x in range(image.width()))
        if 20 < value < 235
    ]


# ── 找壁纸 ────────────────────────────────────────────────────────────


def test_find_wallpaper_accepts_a_file_or_a_directory(tmp_path):
    """设置里填的可以是图片，也可以是目录（目录取最新那张 —— 用户就是这么存壁纸的）。"""
    folder = tmp_path / "wallpaper"
    folder.mkdir()
    older, newer = _two_tone(folder / "a.png"), _two_tone(folder / "b.png")
    import os

    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))

    assert acrylic.find_wallpaper(newer, home=tmp_path) == newer
    assert acrylic.find_wallpaper(str(folder), home=tmp_path) == newer


def test_find_wallpaper_reads_the_desktop_shell_session(tmp_path):
    """自动探测：DankMaterialShell 把当前壁纸记在会话状态里。"""
    image = _two_tone(tmp_path / "pic.png")
    state = tmp_path / ".local/state/DankMaterialShell/session.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"wallpaperPathDark": image}), encoding="utf-8")

    assert acrylic.find_wallpaper("", home=tmp_path) == image


def test_find_wallpaper_returns_none_when_there_is_nothing(tmp_path):
    """什么都找不到要返回 None —— 界面据此如实提示"只有半透明"，而不是假装糊上了。"""
    assert acrylic.find_wallpaper("", home=tmp_path) is None
    assert acrylic.find_wallpaper(str(tmp_path / "不存在.png"), home=tmp_path) is None


# ── 模糊 ──────────────────────────────────────────────────────────────


def test_blur_actually_spreads_a_hard_edge(restore_app, qtbot, tmp_path):
    """半径越大，接缝处的过渡越宽；半径为 0 时保持硬边。"""
    source = _two_tone(tmp_path / "edge.png")

    sharp = acrylic.backdrop_image(source, 400, 200, 0)
    wide = acrylic.backdrop_image(source, 400, 200, 40)

    assert sharp is not None and wide is not None
    row = wide.height() // 2
    assert len(_transitions(wide, row)) > len(_transitions(sharp, row)) + 4, (
        "模糊没有把硬边摊开 —— 那它就只是把原图贴上去"
    )
    # 两端仍要保持原色（模糊不该把整幅图洗成灰）
    assert wide.pixelColor(2, row).red() < 60
    assert wide.pixelColor(wide.width() - 3, row).red() > 200


def test_backdrop_image_needs_a_real_wallpaper(restore_app, qtbot, tmp_path):
    """没有壁纸 / 尺寸无效时返回 None，调用方才有机会退化为半透明而不是画一张黑图。"""
    assert acrylic.backdrop_image(None, 400, 200, 40) is None
    assert acrylic.backdrop_image(str(tmp_path / "缺.png"), 400, 200, 40) is None
    assert acrylic.backdrop_image(_two_tone(tmp_path / "a.png"), 0, 0, 40) is None


# ── 铺到窗口上 ────────────────────────────────────────────────────────


def _window(tmp_path, **fields) -> MainWindow:
    settings = AppSettings(run_root=str(tmp_path / "runs"), templates_dir=str(tmp_path / "tpl"), **fields)
    return MainWindow(wire_controller=False, settings=settings)


def _render(window: MainWindow, background: tuple[int, int, int]) -> QImage:
    image = QImage(window.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(*background))
    painter = QPainter(image)
    window.render(painter, QPoint(0, 0))
    painter.end()
    return image


def test_acrylic_covers_the_desktop_with_a_blurred_wallpaper(restore_app, qtbot, tmp_path):
    """亚克力的判据：窗口底色不再"透出桌面"，而是自己那份模糊壁纸。

    测法：同一扇窗分别渲染到**品红**和**纯绿**画布上。若桌面能透出来，两张图的像素必然
    不同；亚克力模式下壁纸层是不透明的，两张图必须一模一样（而颜色又不是画布色）。
    """
    wallpaper = _two_tone(tmp_path / "wall.png", 600, 400)
    window = _window(tmp_path, backdrop="acrylic", acrylic_wallpaper=wallpaper)
    qtbot.addWidget(window)
    window.resize(1200, 800)
    window.show()
    window.apply_appearance()

    assert window._acrylic_image is not None, "没铺上模糊壁纸"
    over_magenta = _render(window, (255, 0, 255))
    over_green = _render(window, (0, 255, 0))

    point = QPoint(1100, 700)          # 工具区里的空白处
    a, b = over_magenta.pixelColor(point), over_green.pixelColor(point)
    assert (a.red(), a.green(), a.blue()) == (b.red(), b.green(), b.blue()), (
        f"桌面透出来了（品红 {a.getRgb()[:3]} vs 绿 {b.getRgb()[:3]}）—— 亚克力层没铺在底下"
    )
    assert (a.red(), a.green(), a.blue()) != (255, 0, 255)


def test_acrylic_without_a_wallpaper_degrades_to_plain_translucency(restore_app, qtbot, tmp_path, monkeypatch):
    """找不到壁纸时不能假装糊上了：退化为半透明，并在状态栏与设置页如实说明。"""
    monkeypatch.setattr(acrylic, "find_wallpaper", lambda *a, **k: None)
    window = _window(tmp_path, backdrop="acrylic")
    qtbot.addWidget(window)
    window.resize(1200, 800)
    window.show()
    window.apply_appearance()

    assert window._acrylic_image is None
    assert "没找到壁纸" in window.status_label.text()
    assert "找不到" in window._appearance_hint() or "没找到" in window._appearance_hint()

    over_magenta = _render(window, (255, 0, 255))
    point = QPoint(1100, 700)
    assert over_magenta.pixelColor(point).red() > 40, "退化后应当还能透出桌面"


def test_leaving_acrylic_mode_drops_the_layer(restore_app, qtbot, tmp_path):
    """切回别的模式必须把模糊层撤掉，否则换了背景效果却还是那张壁纸。"""
    wallpaper = _two_tone(tmp_path / "wall.png")
    window = _window(tmp_path, backdrop="acrylic", acrylic_wallpaper=wallpaper)
    qtbot.addWidget(window)
    window.show()
    window.apply_appearance()
    assert window._acrylic_image is not None

    window.settings.backdrop = "translucent"
    window.apply_appearance()
    assert window._acrylic_image is None


def test_acrylic_settings_round_trip(restore_app, qtbot, tmp_path):
    """两个新字段要能存能读，设置页也要能铺回来（否则用户以为自己设了）。"""
    wallpaper = _two_tone(tmp_path / "wall.png")
    path = tmp_path / "settings.json"
    settings = AppSettings(backdrop="acrylic", acrylic_wallpaper=wallpaper, acrylic_blur=72)
    settings.save(path)

    loaded = AppSettings.load(path)
    assert loaded.backdrop == "acrylic"
    assert loaded.acrylic_wallpaper == wallpaper
    assert loaded.acrylic_blur == 72

    window = _window(tmp_path)
    qtbot.addWidget(window)
    page = window.settings_page
    page.set_settings(loaded)
    assert page.backdrop_combo.currentData() == "acrylic"
    assert page.wallpaper_edit.text() == wallpaper
    assert page.acrylic_blur_spin.value() == 72
    page.collect()
    assert loaded.acrylic_blur == 72


def test_changing_the_wallpaper_refreshes_the_layer(restore_app, qtbot, tmp_path, monkeypatch):
    """壁纸换了要跟着换（本机桌面每 900 秒轮换一次，模糊层不能停在旧图上）。"""
    first = _two_tone(tmp_path / "a.png")
    second = _two_tone(tmp_path / "b.png")
    window = _window(tmp_path, backdrop="acrylic", acrylic_wallpaper=first)
    qtbot.addWidget(window)
    window.show()
    window.apply_appearance()
    assert window._acrylic_wallpaper == first
    assert window._acrylic_timer.isActive(), "亚克力模式下应当开着低频自检"

    monkeypatch.setattr(acrylic, "find_wallpaper", lambda *a, **k: second)
    window._poll_acrylic_wallpaper()
    assert window._acrylic_wallpaper == second

    window.settings.backdrop = "off"
    window.apply_appearance()
    assert not window._acrylic_timer.isActive(), "离开亚克力模式要停掉自检"
