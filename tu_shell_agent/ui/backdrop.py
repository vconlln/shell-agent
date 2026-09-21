"""窗口背景效果：半透明 / 亚克力模糊。

**核心事实（曾经踩过两次，所以写在最前面）**：真正被模糊的是**窗口背后的内容**，
只有窗口管理器/合成器能做，Qt 没有跨平台的"给我一块毛玻璃"接口。而两个平台的系统级方案
在真机上都靠不住：

- Windows 上 `WA_TranslucentBackground` 必须搭配**无边框窗口**才生效（Qt 的已知限制），
  而我们保留原生标题栏 → 客户区永远不透明；
- Windows 11 的 DWM 亚克力要求窗口**没有重定向位图**（`WS_EX_NOREDIRECTIONBITMAP`），
  Qt 的普通窗口有，于是 `DwmSetWindowAttribute` 即使返回成功，背景也显示不出来；
- KDE/X11 的 `_KDE_NET_WM_BLUR_BEHIND_REGION` 需要 xcb 绑定，Wayland（含 Niri）根本没有
  给普通应用的模糊接口。

结论：两种效果都改成**界面自绘底色层**（见 `ui/acrylic.py`），窗口**始终不透明**：

| 模式 | 底色层 |
| --- | --- |
| `off` | 无（主题纯色，与历史版本逐像素一致） |
| `translucent` | 壁纸**不模糊** + 淡色调 `TRANSLUCENT_TINT` |
| `acrylic` | 壁纸**高斯模糊** + 浓色调 `TINT` |

于是两端行为一致、不依赖合成器，也不需要"问系统要模糊"这条不可测的代码路径。

这个模块因此只剩四件事：记住当前模式、把它应用到每个顶层窗口（含对话框）、
重绘前擦掉上一次的残留（防重影）、以及给对话框留一份同样的入口。
"""

from __future__ import annotations

from typing import Any

# 当前背景模式（由 apply_appearance 写入）。对话框是**独立顶层窗口**，创建时也要跟着变透明 ——
# 只在主窗口上处理的话，弹出来的确认框仍是另一套底色。
_current_mode = "off"


def set_mode(mode: str) -> None:
    global _current_mode
    _current_mode = mode or "off"


def current_mode() -> str:
    return _current_mode


def apply_to(window: Any) -> None:
    """把"当前背景模式"应用到某个顶层窗口（主窗口与所有对话框都走这里）。

    窗口**始终不透明**：两种"透明"效果都是界面自绘的底色层（见文件顶部说明）。
    容器去叠加只对非纯色模式做 —— 纯色模式的观感与历史版本逐像素一致。
    """
    from . import theme as theme_module

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
