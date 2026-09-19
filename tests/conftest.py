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
