"""自绘亚克力模糊：**不需要窗口管理器支持**的毛玻璃。

真正的亚克力（Acrylic / 毛玻璃）要模糊的是"窗口背后"的像素，那只有合成器拿得到
（Windows 的 DWM、KWin、Hyprland 的 blur 插件）。应用自己读不到别的窗口的像素：
Wayland 出于安全压根不允许，X11 抓屏拿到的也是"整块屏幕"而不是"窗口背后"。

但观感可以自己造 —— 壁纸图片是能读的，而窗口背后通常就是壁纸：

1. 找到当前壁纸（DankMaterialShell / KDE / hyprpaper 的配置，或 ~/Pictures 里最新的一张）；
2. 在窗口自己的背景层里画一份**高斯模糊过的**壁纸；
3. QSS 那层半透明深色底再压上去 —— 看起来就是毛玻璃。

**代价说清楚**（不假装它是真亚克力）：
- 背后如果是别的窗口，它不会出现在这一层里 —— 这是静态壁纸，不是实时抓屏；
- Wayland 拿不到窗口的屏幕坐标，所以模糊层按窗口大小重新裁切，
  不保证与桌面壁纸逐像素对齐（模糊之后这点错位基本看不出来）；
- 找不到壁纸图片时退化为普通半透明，并在界面上如实说明。
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

# 模糊强度（QGraphicsBlurEffect 的半径，单位像素）。40 左右已经明显"毛"了，
# 再大就开始糊成一片色块，所以设置页给的是 0~120。
DEFAULT_BLUR = 40

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".bmp")

# 模糊结果按**尺寸分桶**缓存：拖动窗口时每个像素都重算一次会把界面拖卡，
# 分桶之后 64 像素以内共用一份结果，再按实际尺寸拉伸（模糊层拉伸看不出来）。
_BUCKET = 64
_MAX_CACHE = 6


def find_wallpaper(configured: str = "", *, home: Path | None = None) -> str | None:
    """找出当前壁纸图片的路径；都找不到就返回 None。

    顺序：设置里指定的 → DankMaterialShell → KDE → hyprpaper → ~/Pictures 里最新的。
    指定的可以是图片，也可以是目录（取其中最新的一张）。
    """
    base = Path(home) if home is not None else Path.home()

    picked = _from_path(configured)
    if picked is not None:
        return picked

    for finder in (_dms_wallpaper, _kde_wallpaper, _hyprpaper_wallpaper, _pictures_wallpaper):
        try:
            found = finder(base)
        except (OSError, ValueError, json.JSONDecodeError):
            continue                      # 配置坏了不该让界面起不来，继续找下一处
        if found:
            return found
    return None


def _from_path(value: str) -> str | None:
    """用户指定值 → 现存图片路径（可以是文件，也可以是目录）。"""
    if not value:
        return None
    path = Path(value).expanduser()
    if path.is_file():
        return str(path)
    if path.is_dir():
        return _newest_image(path)
    return None


def _newest_image(directory: Path) -> str | None:
    if not directory.is_dir():
        return None
    images = [
        entry
        for entry in directory.iterdir()
        if entry.is_file() and entry.suffix.lower() in IMAGE_SUFFIXES
    ]
    if not images:
        return None
    return str(max(images, key=lambda entry: entry.stat().st_mtime))


def _dms_wallpaper(home: Path) -> str | None:
    """DankMaterialShell（用户本机正在用的桌面壳）把当前壁纸记在会话状态里。"""
    session = home / ".local/state/DankMaterialShell/session.json"
    if not session.is_file():
        return None
    data = json.loads(session.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return None
    for key in ("wallpaperPathDark", "wallpaperPath", "wallpaperPathLight"):
        found = _from_path(str(data.get(key, "") or ""))
        if found is not None:
            return found
    return None


def _kde_wallpaper(home: Path) -> str | None:
    """KDE 的壁纸写在 appletsrc 里，形如 `Image=file:///path/to.png`。"""
    config = home / ".config/plasma-org.kde.plasma.desktop-appletsrc"
    if not config.is_file():
        return None
    match = re.search(r"^Image=file://(.+)$", config.read_text(encoding="utf-8"), re.MULTILINE)
    return _from_path(unquote(match.group(1)).strip()) if match else None


def _hyprpaper_wallpaper(home: Path) -> str | None:
    config = home / ".config/hypr/hyprpaper.conf"
    if not config.is_file():
        return None
    for line in config.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        for keyword in ("preload", "wallpaper"):
            if line.startswith(keyword):
                value = line.split("=", 1)[-1].strip().split(",")[-1].strip()
                found = _from_path(value)
                if found is not None:
                    return found
    return None


def _pictures_wallpaper(home: Path) -> str | None:
    for folder in (home / "Pictures/wallpaper", home / "Pictures/Wallpapers", home / "Pictures"):
        found = _newest_image(folder)
        if found is not None:
            return found
    return None


def backdrop_image(wallpaper: str | None, width: int, height: int, blur: int) -> QImage | None:
    """按窗口尺寸生成"模糊壁纸"图层；没有壁纸或尺寸无效时返回 None（调用方退化为半透明）。"""
    if not wallpaper or width <= 1 or height <= 1:
        return None
    path = Path(wallpaper)
    if not path.is_file():
        return None
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return None
    # 尺寸分桶 + 按 (路径, mtime, 尺寸桶, 模糊强度) 缓存：换壁纸/改强度/改尺寸才会重算。
    bucket_w = max(_BUCKET, (width + _BUCKET - 1) // _BUCKET * _BUCKET)
    bucket_h = max(_BUCKET, (height + _BUCKET - 1) // _BUCKET * _BUCKET)
    blurs = max(0, min(200, int(blur)))
    try:
        return _cached_backdrop(str(path), stamp, bucket_w, bucket_h, blurs)
    except (OSError, ValueError):
        return None


@lru_cache(maxsize=_MAX_CACHE)
def _cached_backdrop(path: str, stamp: float, width: int, height: int, blur: int) -> QImage | None:
    image = QImage(path)
    if image.isNull():
        return None
    return _blur(_cover(image, width, height), blur)


def _cover(image: QImage, width: int, height: int) -> QImage:
    """等比铺满并居中裁切（保持壁纸比例，不拉伸变形）。"""
    scaled = image.scaled(
        width,
        height,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    left = max(0, (scaled.width() - width) // 2)
    top = max(0, (scaled.height() - height) // 2)
    return scaled.copy(left, top, min(width, scaled.width()), min(height, scaled.height()))


def _blur(image: QImage, radius: int) -> QImage:
    """模糊：先按半径大幅降采样，再逐级放大回来（mipmap 式的做法）。

    两点取舍（都试过）：
    - 不用 `QGraphicsBlurEffect`：它在离屏场景里**不生效**（实测接缝处仍是纯黑纯白，
      半径 40/80 都没变化），而且要走一遍图形栈；
    - 也不在 Python 里做卷积：1400×900 的图做可分离卷积是百万级像素的循环，太慢。
    降采样用的是 SmoothTransformation（区域平均），逐级放大每步再做一次三角滤波，
    叠起来就是一条很接近高斯的结果 —— 而且全程在 Qt 的 C++ 里跑，实测 0.08s。
    """
    if radius <= 0:
        return image
    factor = max(2, min(image.width() // 8, image.height() // 8, round(radius / 3)))
    small = image.scaled(
        max(1, image.width() // factor),
        max(1, image.height() // factor),
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    # 逐级放大：一步到位会有轻微的方块感，分几步就平滑了
    while small.width() * 2 < image.width():
        small = small.scaled(
            min(image.width(), small.width() * 2),
            min(image.height(), small.height() * 2),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    return small.scaled(
        image.width(),
        image.height(),
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
