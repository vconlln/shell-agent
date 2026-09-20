"""窗口背景效果：半透明，以及"若平台支持"的亚克力模糊。

**说实话的部分**：真正被模糊的是**窗口背后的内容**，这件事只有窗口管理器/合成器能做，
Qt 本身没有跨平台的"给我一块毛玻璃"接口。所以这里分两步：

1. **半透明**（任何平台都能做）：窗口属性 `WA_TranslucentBackground` + 底色带 alpha。
   在合成器下，背后的壁纸/窗口会透出来 —— 透出来但不模糊。
2. **模糊**（能拿到才做）：
   - Windows 11：`DwmSetWindowAttribute(DWMWA_SYSTEMBACKDROP_TYPE)` 设成 Acrylic(3)/Mica(2)；
     这是系统原生的亚克力，效果最好。
   - KDE/X11：`_KDE_NET_WM_BLUR_BEHIND_REGION` 属性（KWin 会据此给该区域加模糊）。
   - Wayland（含 Niri/sway/GNOME）：**没有给普通应用的模糊接口**，返回 False。
   - macOS：NSVisualEffectView，本项目的 PySide6 环境不直接支持，返回 False。

拿不到模糊时**不假装成功**：调用方据此在界面上如实写明"当前桌面不支持模糊，已退化为半透明"。
"""

from __future__ import annotations

import os
import sys
from typing import Any


# 当前背景模式（由 apply_appearance 写入）。对话框是**独立顶层窗口**，创建时也要跟着变透明 ——
# 只在主窗口上设 WA_TranslucentBackground 的话，弹出来的确认框仍是纯不透明的一整块。
_current_mode = "off"


def set_mode(mode: str) -> None:
    global _current_mode
    _current_mode = mode or "off"


def current_mode() -> str:
    return _current_mode


def apply_to(window: Any) -> None:
    """把"当前背景模式"应用到某个顶层窗口（主窗口与所有对话框都走这里）。"""
    from . import theme as theme_module

    # 窗口**始终不透明**：两种"透明"效果都是界面自绘的底色层（见文件顶部说明）。
    # 容器去叠加只对非纯色模式做 —— 纯色模式的观感与历史版本逐像素一致。
    if _current_mode in ("translucent", "acrylic"):
        theme_module.unstack_viewports(window)
        theme_module.thin_containers(window)
    else:
        theme_module.restack_viewports(window)


def erase_damage(widget: Any, event: Any, color: Any = None) -> None:
    """把这次要重绘的区域**重填成底色**，再让正常绘制往上叠。

    为什么必须重填：底色层是"混合"画上去的，滚动或重排后只重绘一块区域时，上一次留下的
    像素会透过来 —— 屏幕上就是**重影**（用户报的"半透明又成这种重影的了"）。
    重填用 `CompositionMode_Source`：它是"替换"，不受 alpha 影响。

    填的颜色必须是**不透明**的实色（窗口现在始终不透明）：填带 alpha 的色会挖出窟窿。
    """
    from PySide6.QtGui import QColor, QPainter

    fill = color if color is not None else QColor(0, 0, 0, 0)
    painter = QPainter(widget)
    painter.save()
    try:
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(event.rect(), fill)
    finally:
        # **必须恢复**：合成模式是画笔状态，会跟着这次绘制一路传下去 ——
        # 留在 Source 上，后面所有绘制都变成"替换"，半透明就整体失效
        # （实测：留在 Source 上时窗口渲染不再与背景混合，透出 0%）。
        painter.restore()
        painter.end()


# 说明：这里**曾经**有 enable/disable_translucent 与 try_enable_blur（Windows 走 DWM、
# KDE 走 KWin）。两条路在真实机器上都靠不住：
#   1. Windows 上 `WA_TranslucentBackground` 必须搭配**无边框窗口**才生效
#      （Qt 的已知限制），而我们保留原生标题栏 —— 于是客户区永远不透明；
#   2. Windows 11 的 DWM 亚克力要求窗口**没有重定向位图**（WS_EX_NOREDIRECTIONBITMAP），
#      Qt 的普通窗口有，所以 DwmSetWindowAttribute 即使返回成功，背景也显示不出来。
# 结论：两个"透明"效果都改成**界面自绘底色**（见 ui/acrylic.py）：窗口保持不透明，
# 半透明 = 壁纸不模糊 + 淡色调，亚克力 = 壁纸模糊 + 浓色调。两端行为一致，也不依赖合成器。


def erase_damage(widget: Any, event: Any, color: Any = None) -> None:
    """把这次要重绘的区域**重填成底色**，再让正常绘制往上叠。

    为什么必须重填：底色层是"混合"画上去的，滚动或重排后只重绘一块区域时，上一次留下的
    像素会透过来 —— 屏幕上就是**重影**（用户报的"半透明又成这种重影的了"）。
    重填用 `CompositionMode_Source`：它是"替换"，不受 alpha 影响。

    填的颜色必须是**不透明**的实色（窗口现在始终不透明）：填带 alpha 的色会挖出窟窿。
    """
    from PySide6.QtGui import QColor, QPainter

    fill = color if color is not None else QColor(0, 0, 0, 0)
    painter = QPainter(widget)
    painter.save()
    try:
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(event.rect(), fill)
    finally:
        # **必须恢复**：合成模式是画笔状态，会跟着这次绘制一路传下去 ——
        # 留在 Source 上，后面所有绘制都变成"替换"，半透明就整体失效
        # （实测：留在 Source 上时窗口渲染不再与背景混合，透出 0%）。
        painter.restore()
        painter.end()


def _set_translucent(window: Any, want: bool) -> None:
    """设置"窗口背景可半透明"，**并在窗口已经显示时重建它**。

    踩过的坑（用户报的"半透明都没有透明效果了"）：`WA_TranslucentBackground` 必须在窗口
    被创建之前设好 —— 窗口已经在屏幕上时再改这个属性不会生效，Qt 的窗口后端早已定型。
    所以从「不透明」切到「半透明」时属性是设上了、窗口却还是不透。
    办法是隐藏再显示一次，强制按新属性重建窗口；下次显示前设属性的常规路径不受影响
    （启动时 apply_appearance 本来就在 show() 之前跑）。
    """
    from PySide6.QtCore import Qt

    attribute = Qt.WidgetAttribute.WA_TranslucentBackground
    if window.testAttribute(attribute) == want:
        window.setAutoFillBackground(False) if want else None
        return
    window.setAttribute(attribute, want)
    if want:
        # 不动窗口边框（用户还要拖窗口），只关掉"自动填充背景"，
        # 免得它用不透明底色把 alpha 盖掉。
        window.setAutoFillBackground(False)
    if window.isVisible():
        window.hide()
        window.show()


def enable_translucent(window: Any) -> None:
    """让窗口背景可以半透明（底色自身的 alpha 由主题负责）。"""
    _set_translucent(window, True)


def disable_translucent(window: Any) -> None:
    _set_translucent(window, False)


def try_enable_blur(window: Any) -> bool:
    """尝试给窗口加平台级模糊；成功返回 True，不支持返回 False（调用方据此如实提示）。"""
    if sys.platform == "win32":
        return _enable_windows_acrylic(window)
    if sys.platform.startswith("linux") and os.environ.get("XDG_SESSION_TYPE", "") != "wayland":
        return _enable_kde_blur(window)
    return False


def _enable_windows_acrylic(window: Any) -> bool:
    """Windows 11 22H2+ 的系统亚克力（Acrylic）。老系统上返回 False。"""
    try:
        import ctypes

        from ctypes import wintypes

        hwnd = int(window.winId())
        dwm = ctypes.windll.dwmapi
        # DWMWA_SYSTEMBACKDROP_TYPE = 38；DWMSBT_TRANSIENTWINDOW = 3（亚克力）
        value = ctypes.c_int(3)
        result = dwm.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), ctypes.c_uint(38), ctypes.byref(value), ctypes.sizeof(value)
        )
        if result != 0:
            return False
        # 顺便把标题栏也切成深色，免得浅色标题栏与深色内容打架
        mode = ctypes.c_int(1)          # DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        dwm.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), ctypes.c_uint(20), ctypes.byref(mode), ctypes.sizeof(mode)
        )
        return True
    except Exception:  # noqa: BLE001 - 平台细节失败一律当"不支持"
        return False


def _enable_kde_blur(window: Any) -> bool:
    """X11 + KWin：写 `_KDE_NET_WM_BLUR_BEHIND_REGION` 属性。

    需要用 xcb 拿窗口 id 并设置属性；环境里没有 python-xcb 时返回 False（不去 shell 调 xprop，
    那会在用户机器上留下莫名其妙的子进程）。
    """
    try:
        from PySide6.QtGui import QGuiApplication

        if QGuiApplication.platformName() != "xcb":
            return False
        return False        # 未安装 xcb 绑定：如实返回"不支持"，而不是假装成功
    except Exception:  # noqa: BLE001
        return False
