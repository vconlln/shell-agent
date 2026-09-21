import os

# 无头跑界面测试：必须在 QApplication 创建之前设置
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def shellcheck_path() -> str:
    candidate = os.environ.get("TU_SHELLCHECK") or str(REPO_ROOT / "tools" / "shellcheck")
    if Path(candidate).is_file():
        return candidate
    found = shutil.which("shellcheck")
    if found:
        return found
    pytest.skip("找不到 shellcheck（tools/shellcheck 或 PATH）")


@pytest.fixture(scope="session")
def bash_path() -> str:
    candidate = os.environ.get("TU_BASH") or (
        "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else "/usr/bin/bash"
    )
    if Path(candidate).is_file():
        return candidate
    found = shutil.which("bash")
    if found:
        return found
    pytest.skip("找不到 bash")


@pytest.fixture
def restore_app(qtbot):
    """还原全局样式表/调色板/字体，别把外观泄漏给同一会话里的其它测试。

    （本来只在 test_appearance.py 里；亚克力用例同样会装主题，所以提到这里共用。）
    """
    from PySide6.QtWidgets import QApplication

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


class Grab:
    """`widget.grab()` 的**逻辑像素**取色包装（高 DPI 下必须用）。

    为什么需要：`grab()` 出来的图片是**物理像素**图 —— `devicePixelRatio` 1.5（Windows 150%
    缩放）时图宽是控件逻辑宽度的 1.5 倍。直接拿逻辑坐标去 `pixel()` 会取到别的地方
    （实测：150% 下"脚本只读底"那一格取到的是行号区的颜色，三条用例因此假红）。
    所有按坐标取色的用例都走这里，界面在什么缩放下都测得准。
    """

    def __init__(self, widget) -> None:
        self.image = widget.grab().toImage()
        self.ratio = float(widget.devicePixelRatioF() or 1.0)

    def color(self, x: int, y: int):
        px = min(max(int(round(x * self.ratio)), 0), self.image.width() - 1)
        py = min(max(int(round(y * self.ratio)), 0), self.image.height() - 1)
        return self.image.pixelColor(px, py)

    def name(self, x: int, y: int) -> str:
        return self.color(x, y).name()

    def rgb(self, x: int, y: int) -> tuple[int, int, int]:
        return tuple(self.color(x, y).getRgb()[:3])

    def dominant(self, x: int, y: int, width: int, height: int) -> str:
        """区域内出现次数最多的颜色名（"底色"类断言用这个，别用单个像素）。

        单像素取样太脆：字体度量一变，采样点就可能落到字形上 —— 实测「主按钮浅底深字」
        与「引用条底色」两条用例，在别的用例先跑过（改了全局字体/缩放）之后取到了文字颜色
        （#b7b8ba / #9e9ea2），而底色本身是对的。底色是"面积最大"的那个颜色，用它来判。
        """
        counts: dict[str, int] = {}
        for dy in range(height):
            for dx in range(width):
                name = self.name(x + dx, y + dy)
                counts[name] = counts.get(name, 0) + 1
        return max(counts, key=lambda key: counts[key])
