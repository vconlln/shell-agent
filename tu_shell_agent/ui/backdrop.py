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

    # acrylic（自绘壁纸模糊）同样要窗口半透明：模糊层是画在窗口最底层的，
    # 深色底再压上去才是"毛玻璃"，而它的透明度同样依赖 WA_TranslucentBackground。
    if _current_mode in ("translucent", "blur", "acrylic"):
        enable_translucent(window)
        theme_module.unstack_viewports(window)
        theme_module.thin_containers(window)
    else:
        disable_translucent(window)
        theme_module.restack_viewports(window)


def enable_translucent(window: Any) -> None:
    """让窗口背景可以半透明（底色自身的 alpha 由主题负责）。"""
    from PySide6.QtCore import Qt

    window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    # 无边框会让半透明的圆角真正生效；这里不动窗口边框（用户还要拖窗口），
    # 只把"自动填充背景"关掉，避免不透明底色把 alpha 覆盖掉。
    window.setAutoFillBackground(False)


def disable_translucent(window: Any) -> None:
    from PySide6.QtCore import Qt

    window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)


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
