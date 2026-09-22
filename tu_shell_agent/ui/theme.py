"""Codex 风格深色主题（令牌取自本机 Codex 桌面版的真实样式表，不是凭印象调的）。

令牌来源：`/opt/codex-desktop/resources/app.asar` 里的 `webview/assets/app-*.css`，
`electron-dark` 作用域下的设计变量。取到的关键值（下文 `TOKENS` 一一对应）：

| 用途 | Codex 原值 |
| --- | --- |
| 主背景 / 面板 / 编辑器 | `#181818` / `#212121` / `#212121` |
| 文字 主 / 次 / 三级 | `#ffffff` / `70% 白` / `50% 白` |
| 边框 浅 / 常规 / 重 | `4% 白` / `8% 白` / `16% 白` |
| 焦点描边 | `#339cffb3` |
| 强调色 | 蓝 `#339cff`、绿 `#40c977`、橙 `#fb6a22`、红 `#ff6764`、黄 `#ffc300` |
| 圆角 | 6px / 8px / 胶囊 |
| 列表行高 | 30px |

为什么把颜色集中在这里而不是各控件自带：这个界面要在深色底上**说清"这次到底成功没有"**，
颜色是有语义的（阻断=红、通过=绿、未跑=灰）。散落到十几个控件里，改主题就会出现
"背景换了、某处红还是旧的浅色红"这种半吊子状态 —— 事实上有三处硬编码颜色就是这么来的
（右栏 stderr 红、diff 的浅底行、diff 行号灰），本模块一并收口。

字体分工与 Codex 一致：界面文字用系统 sans，**脚本、报告、输出、diff 用等宽** —— 这些
内容是拿来核对的，等宽才看得清列对齐与空格。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

# ── 令牌 ────────────────────────────────────────────────────────────────────
#
# 配色取向（2026-09-19 用户要求"显得高级、尽量圆角"）：底色从 Codex 的 #181818 往**冷调
# 更深**走（#101114），面板只比底色亮一档（#17181c）——"高级感"主要来自**低对比的分层**，
# 而不是把面板刷得比背景亮很多；文字不用纯白（#ecedf0），因为纯白在高对比深底上偏"廉价"；
# 输入类控件（文本框/下拉/数字框）比卡片**亮一档**，并且描边是 16% 白（2026-09-20 按用户
# 实测反馈调的：原来比卡片更深、接近纯黑，在半透明壁纸上像一块黑洞、看不出是圆角框）；
# 强调色从 #339cff 换成偏靛蓝的 #7c9cff，语义色全部降饱和（错误是玫瑰红而非正红），
# 这样红色只用在"真的出问题了"的地方，而不是到处抢眼。
# 圆角全面放大（控件 10px、面板 14px、页签胶囊），分割条给 6px 的可抓宽度但只在悬停时显色。
# 颜色值分两种写法，别混：不透明的写 "#rrggbb"；**半透明的写 (r,g,b,a) 元组**。
#
# 为什么不写 8 位 hex：Qt 把 8 位 hex 解析成 `#AARRGGBB`（不是 CSS 的 `#RRGGBBAA`），
# 于是 Codex 的 `#ffffff0a`（4% 白）在 Qt 里变成"不透明的黄"—— 实测渲染出 #ffff0a。
# 这个坑只有真的截图取色才看得见：QSS 不报错，palette 也不报错，界面就是花的。
# 所有颜色都必须经 css() / qcolor() 出口，禁止在别处直接写颜色字面量。
TOKENS: dict[str, str | tuple[int, int, int, int]] = {
    # 背景
    "bg": "#101114",              # 冷调更深的一层（比 Codex 的 #181818 再压一档）
    "bg_elevated": "#17181c",     # 面板/卡片：只比底色亮一档（输入类见 bg_input，比它再亮一档）
    "bg_under": "#0b0c0e",        # 更深的一层（只读底、凹陷）
    # 输入框底色。历史：曾经比面板**更深**（#121317），理由是"和面板同色时圆角处的像素与填充
    # 同色，形状看不出来"。但用户实测（截图 + 逐像素量）反馈：那个色**接近纯黑**，在浅色面板/
    # 半透明壁纸上像一块黑洞，看不出是"圆角框" —— 于是**反过来做**：输入框比卡片亮一档
    # （#1b1c21 vs 卡片 #17181c）+ 描边加亮到 16%，形状靠"更亮 + 有边"表达，而不是靠更深。
    "bg_input": "#1b1c21",
    # 「选择框」（QComboBox）用**按钮那种浅底**：它是"点开选一个"，与旁边按钮同类，
    # 底色取按钮在卡片上的合成色（白 5% 叠在 #17181c 上 ≈ #232428），在 off 模式下与按钮一致。
    "bg_select": "#232428",
    # 浮层（下拉列表 / 菜单 / 提示气泡）：**不跟着变透明**。它们是"临时盖在一切之上的
    # 一层"，读的就是里面的字；跟着透会把文字糊在壁纸上（实测字体下拉列表底色全透明，
    # 文字与背景对比度掉到 1.7:1，等于看不见）。
    "bg_menu": "#17181c",
    "bg_hover": (255, 255, 255, 10),     # 4% 白（--color-background...hover）
    "bg_selected": (255, 255, 255, 20),  # 8% 白
    "bg_button": (255, 255, 255, 13),    # 5% 白
    "bg_button_hover": (255, 255, 255, 20),
    "bg_button_active": (255, 255, 255, 31),
    "bg_accent": (51, 156, 255, 31),
    # 文字
    "fg": "#ecedf0",                       # 不用纯白：深底上纯白偏刺眼
    "fg_secondary": (236, 237, 240, 178),  # 70%
    "fg_tertiary": (236, 237, 240, 120),   # 47%
    "fg_disabled": (236, 237, 240, 70),    # 27%
    "fg_on_accent": "#0b0c0e",
    # 边框
    "border_light": (255, 255, 255, 10),
    "border": (255, 255, 255, 20),
    "border_heavy": (255, 255, 255, 41),
    "border_strong": (255, 255, 255, 61),    # 输入框 hover：比常态再亮一档
    "border_focus": (124, 156, 255, 170),
    # 语义色
    "accent": "#7c9cff",          # 偏靛蓝，比"互联网蓝"更收敛
    "ok": "#4ec98a",
    "warn": "#e8a86a",
    "error": "#f2707a",           # 玫瑰红：语义色降饱和，红色留给真问题
    "error_dim": (242, 112, 122, 128),
    "muted": "#a8adb8",
    "info": "#7c9cff",
    # 控件尺寸（照 Codex 的行高与圆角）
    "radius": "10px",
    "radius_lg": "14px",
    # 页签用的"胶囊"半径。**不能写 999px**：Qt 画页签时，只要圆角半径 >= 页签高度的一半
    # 就整个退回画直角（实测 28px 高的页签：14px 生效，16px 变直角）—— 999px 那种写法
    # 在这里不是"更大的圆角"，而是"没有圆角"。11px 在页签高 24~34px 时都安全。
    "radius_pill": "11px",
    "row_height": "30px",
    "handle": "6px",   # 分割条的**可抓宽度**（不是画出 6px 粗线：见 QSS 里的 hover 规则）
    "font_size": "13px",
    "font_size_small": "12px",
    "font_size_section": "11px",
}

# 等宽字体：脚本/报告/输出/diff 用。逐个探测常见等宽字体，取第一个可用的。
_MONO_CANDIDATES = (
    "JetBrains Mono", "Cascadia Mono", "SF Mono", "Menlo", "Consolas",
    "DejaVu Sans Mono", "Liberation Mono", "Noto Sans Mono CJK SC", "monospace",
)


# 尺寸类令牌：会随"界面缩放"一起放大/缩小（颜色不缩放）。
_SIZE_TOKENS = frozenset(
    {
        "radius", "radius_lg", "radius_pill", "row_height",
        "font_size", "font_size_small", "font_size_section", "handle",
    }
)


def sized(name: str, scale: float = 1.0) -> str:
    """取尺寸令牌（可缩放）。缩放只作用于尺寸，颜色不动 —— 把颜色也乘起来没有意义。"""
    value = TOKENS[name]
    assert isinstance(value, str) and value.endswith("px"), name
    return f"{round(float(value[:-2]) * scale, 1):g}px"


def scaled_font_size(scale: float = 1.0) -> float:
    """界面基准字号（pt）。13px ≈ 9.75pt，与 Codex 的 0.875rem 对齐。"""
    return round(9.75 * scale, 2)


def css(name: str) -> str:
    """取 QSS 用的颜色字符串：不透明给 #rrggbb，半透明给 rgba(r,g,b,a)。"""
    value = TOKENS[name]
    if isinstance(value, tuple):
        r, g, b, a = value
        return f"rgba({r}, {g}, {b}, {a})"
    return value


def qcolor(name: str) -> QColor:
    """取 QPainter/QPalette 用的 QColor（同样绕开 8 位 hex 的解析陷阱）。"""
    value = TOKENS[name]
    if isinstance(value, tuple):
        return QColor(*value)
    return QColor(value)


def mono_family() -> str:
    """选择一个真实存在的等宽字体族（找不到时交给 Qt 的 monospace 别名）。

    没有 QGuiApplication 时直接返回通用族：`QFontDatabase.families()` 在那种情况下
    不是返回空列表而是**让进程 abort**（实测：QFontDatabase: Must construct a
    QGuiApplication before accessing QFontDatabase + 核心转储）。样式表本身是纯字符串，
    不该因为"谁在什么时候来取"而把调用方炸掉。
    """
    if QApplication.instance() is None:
        return "monospace"
    available = set(QFontDatabase.families())
    for name in _MONO_CANDIDATES:
        if name in available:
            return name
    return "monospace"


# 浮层底色守卫：下拉列表 / 菜单的底色由 apply_theme 写在这里，由事件过滤器在**显示前**钉死。
# 为什么需要：把页面做成透明之后，**页面里的**下拉，其弹出列表会跟着变透明（调色板继承），
# 文字直接浮在壁纸上；而独立的下拉却是正常的 —— 用户反馈的
# "选择字体的背景也跟着透明了，看不见字"就是这个。所以不依赖继承，显式钉一遍。
_popup_style = ""
_popup_container_style = ""
_popup_keeper = None


def _is_popup_view(widget) -> bool:
    """这个控件是不是下拉弹层里的列表？

    **不能只看 parent**：列表的父是弹层容器（QFrame），不是 QComboBox；它自己也不是窗口
    （窗口是那个容器）。判据是"它所在的窗口是个弹出窗口"。
    """
    from PySide6.QtWidgets import QAbstractItemView

    if not isinstance(widget, QAbstractItemView):
        return False
    window = widget.window()
    if window is None or window is widget:
        return False
    # 必须按**窗口类型字段**精确判断：`Qt.WindowType.Popup` 的位里含 `Window`，
    # 直接做位与会把所有顶层窗口都判成弹层 —— 那样主界面里的历史/模板列表会被一起
    # 染成浮层色（实测：主背景从 #101114 变成 #17181c，六个用例转红）。
    kind = window.windowFlags() & Qt.WindowType.WindowType_Mask
    return kind == Qt.WindowType.Popup


def _pin_popup(widget) -> bool:
    """把浮层的底色钉死（列表 + **弹层容器**）。返回是否做了改动。

    为什么需要：把页面做成透明之后，**页面里的**下拉，其弹出列表会跟着变透明（调色板继承），
    文字直接浮在壁纸上 —— 用户反馈的"选择字体的背景也跟着透明了，看不见字"。
    而"弹层容器"才是真正要上色的那一层：实测只给列表上色时列表底色仍不被绘制，整块弹层是透的。
    """
    from PySide6.QtWidgets import QMenu

    if isinstance(widget, QMenu):
        if widget.styleSheet() != _popup_style:
            widget.setStyleSheet(_popup_style)
            return True
        return False
    if not _is_popup_view(widget):
        return False
    changed = False
    if widget.styleSheet() != _popup_style:
        widget.setStyleSheet(_popup_style)
        changed = True
    container = widget.window()
    if container is not None and container.styleSheet() != _popup_container_style:
        container.setStyleSheet(_popup_container_style)
        changed = True
    return changed


class _PopupKeeper(QObject):
    """浮层守卫：显示前把底色与文字色钉在浮层令牌上（新弹出的浮层也照顾得到）。"""

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt 命名
        if _popup_style and event.type() in (QEvent.Type.Polish, QEvent.Type.Show):
            _pin_popup(obj)          # 内部会先比较再设，避免样式表触发递归 polish
        return False


def _install_popup_keeper(app) -> None:
    """装一次就够；应用对象活多久它就活多久（模块级引用防止被 GC）。"""
    global _popup_keeper
    if _popup_keeper is None:
        _popup_keeper = _PopupKeeper(app)
        app.installEventFilter(_popup_keeper)


def fit_form_labels(root) -> int:
    """把表单标签列撑到"文字真正需要的宽度"，返回处理过的标签数量。

    为什么需要：Qt 的 `QLabel.sizeHint()` **不含 QSS 的 padding**，而 QFormLayout 就是按
    sizeHint 分配标签列宽度的 —— 于是每个标签都比文字窄一点点（实测每种缩放下都差 4px），
    中文标签"运行根目录"直接被截尾。换字体/改缩放之后不会自动重算，所以这个函数在每次
    应用外观之后都要跑一遍。
    """
    from PySide6.QtGui import QFontMetrics
    from PySide6.QtWidgets import QFormLayout, QLabel

    handled = 0
    for form in [root, *root.findChildren(QFormLayout)]:
        if not isinstance(form, QFormLayout):
            continue
        width = 0
        for row in range(form.rowCount()):
            item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
            widget = item.widget() if item is not None else None
            if isinstance(widget, QLabel):
                metrics = QFontMetrics(widget.font())
                width = max(width, metrics.horizontalAdvance(widget.text()) + _LABEL_PADDING)
        if not width:
            continue
        for row in range(form.rowCount()):
            item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
            widget = item.widget() if item is not None else None
            if isinstance(widget, QLabel) and widget.setText:
                widget.setMinimumWidth(width)
                handled += 1
    return handled


# 标签列额外留白：QSS 给 QLabel 的 padding 不计入 sizeHint，这里补回来（4px 实测差 + 余量）。
_LABEL_PADDING = 10


def unstack_viewports(root) -> int:
    """让滚动区的**视口**不再重复画一遍底色，返回处理过的数量。

    Qt 的行为：给 QAbstractScrollArea 设了 QSS 的 background-color 之后，视口会自己再画一层
    同样的颜色。半透明模式下这就成了叠加 —— 实测 `bg_under` 的 51% 被叠成 74%
    （1-(1-0.51)²），于是脚本/输出/报告这些大块头看起来"还是纯黑底"。
    这里把视口底色交还给外面的那一层，只画一次，透明度才如实生效。
    """
    from PySide6.QtWidgets import QPlainTextEdit, QTextBrowser, QTextEdit, QWidget

    handled = 0
    # **只处理文本视图**。原来是把所有 QAbstractScrollArea 一网打尽 —— 结果把下拉列表
    # （QListView 也是滚动区）的视口底色也清成了透明：字体下拉一打开就是"字浮在壁纸上"，
    # 用户反馈的"选择字体的背景也跟着透明了，看不见字"就是这个。
    # 浮层（下拉/菜单/提示）本来就不该参与透明化，它们要的是可读性。
    kinds = (QPlainTextEdit, QTextEdit, QTextBrowser)
    text_views = [root] if isinstance(root, kinds) else []
    # findChildren 不接受类型元组（PySide6 会报 subscripted generics），所以取全部再筛
    text_views += [
        area
        for area in root.findChildren(QWidget)
        if isinstance(area, kinds) and area.window() is root
    ]
    for area in text_views:
        viewport = area.viewport()
        if viewport is None:
            continue
        # 记住原值再改：还原时若一律设成 True，视口会把整块矩形填满，
        # **连圆角都被填成直角**（实测 off 模式下列表角落与中心同色）。
        if viewport.property("dsh_prev_autofill") is None:
            viewport.setProperty("dsh_prev_autofill", viewport.autoFillBackground())
        viewport.setAutoFillBackground(False)
        viewport.setStyleSheet("background: transparent;")
        handled += 1
    return handled


def thin_containers(root) -> int:
    """容器控件不再自绘背景，返回处理过的数量。

    与 unstack_viewports 同一个病根：Qt 会把父控件的 QSS 底色灌进子控件的调色板 Window 角色，
    于是每个 `autoFillBackground` 的容器都再刷一遍 —— 层数一多就等于不透明。
    这里把"只起布局作用"的容器统一关掉自动填充；`off` 模式下外观不变（它们刷的就是同一个底色）。
    """
    from PySide6.QtWidgets import (
        QAbstractItemView,
        QFrame,
        QScrollArea,
        QSplitter,
        QStackedWidget,
        QTabWidget,
        QWidget,
    )

    # 注意**不要把 QAbstractItemView（列表/树/下拉列表）算进来**：它们是内容表面，
    # 底色来自调色板 —— 清掉之后下拉列表会整块透明，字浮在壁纸上看不见
    # （用户反馈的"选择字体的背景也跟着透明了，看不见字"）。滚动区里只有"纯文本视图"
    # 才是我们要去叠加的对象，那个由 unstack_viewports 单独处理。
    container_types = (QSplitter, QStackedWidget, QScrollArea, QTabWidget)
    keep = {"paneCard", "confirmScriptView"}
    handled = 0
    for widget in [root, *root.findChildren(QWidget)]:
        if widget.objectName() in keep:
            continue
        if isinstance(widget, QAbstractItemView):
            continue
        # 浮层（下拉列表 / 菜单 / 提示）是独立顶层窗口：它们要的是可读性，不参与透明化
        if widget is not root and widget.window() is not root:
            continue
        is_bare = type(widget) in (QWidget, QFrame)
        if isinstance(widget, container_types) or is_bare:
            widget.setAutoFillBackground(False)
            # QSS 会把父控件的底色灌进子控件的调色板；对"只负责布局"的容器连调色板也清掉，
            # 这样不论走哪条绘制路径都不会再叠一层。
            palette = widget.palette()
            palette.setColor(QPalette.ColorRole.Window, QColor(0, 0, 0, 0))
            palette.setColor(QPalette.ColorRole.Base, QColor(0, 0, 0, 0))
            widget.setPalette(palette)
            handled += 1
    return handled


def restack_viewports(root) -> int:
    """撤销 unstack_viewports（回到不透明模式时用），保证 `off` 与以前逐像素一致。"""
    from PySide6.QtWidgets import QPlainTextEdit, QTextBrowser, QTextEdit, QWidget

    handled = 0
    # **只处理文本视图**。原来是把所有 QAbstractScrollArea 一网打尽 —— 结果把下拉列表
    # （QListView 也是滚动区）的视口底色也清成了透明：字体下拉一打开就是"字浮在壁纸上"，
    # 用户反馈的"选择字体的背景也跟着透明了，看不见字"就是这个。
    # 浮层（下拉/菜单/提示）本来就不该参与透明化，它们要的是可读性。
    kinds = (QPlainTextEdit, QTextEdit, QTextBrowser)
    text_views = [root] if isinstance(root, kinds) else []
    # findChildren 不接受类型元组（PySide6 会报 subscripted generics），所以取全部再筛
    text_views += [
        area
        for area in root.findChildren(QWidget)
        if isinstance(area, kinds) and area.window() is root
    ]
    for area in text_views:
        viewport = area.viewport()
        if viewport is None:
            continue
        previous = viewport.property("dsh_prev_autofill")
        if previous is None:
            # 没被 unstack_viewports 动过的视口**一个字都不要改**：给没改过的视口设空样式表
            # 会重置它的样式状态，`off` 模式下列表的圆角会被视口填成直角（实测）。
            continue
        viewport.setStyleSheet("")
        viewport.setAutoFillBackground(bool(previous))
        viewport.setProperty("dsh_prev_autofill", None)
        handled += 1
    return handled


def build_palette(*, backdrop: str = "off") -> QPalette:
    """把令牌灌进 QPalette：控件自绘的部分（行号槽、文本选中、滚动条）也跟着变。"""
    colors = backdrop_colors(backdrop)

    def color(name: str) -> QColor:
        """按当前背景效果取色（半透明时带 alpha）。"""
        value = colors[name]
        if isinstance(value, tuple) and len(value) == 4:
            return QColor(*value)
        return QColor(str(value))

    palette = QPalette()
    bg = color("bg")
    elevated = color("bg_elevated")
    fg = color("fg")

    palette.setColor(QPalette.ColorRole.Window, bg)
    palette.setColor(QPalette.ColorRole.WindowText, fg)
    palette.setColor(QPalette.ColorRole.Base, elevated)
    palette.setColor(QPalette.ColorRole.AlternateBase, color("bg_under"))
    palette.setColor(QPalette.ColorRole.Text, fg)
    palette.setColor(QPalette.ColorRole.Button, elevated)
    palette.setColor(QPalette.ColorRole.ButtonText, fg)
    palette.setColor(QPalette.ColorRole.ToolTipBase, color("bg_under"))
    palette.setColor(QPalette.ColorRole.ToolTipText, fg)
    palette.setColor(QPalette.ColorRole.PlaceholderText, color("fg_tertiary"))
    palette.setColor(QPalette.ColorRole.Highlight, color("accent"))
    palette.setColor(QPalette.ColorRole.HighlightedText, color("fg_on_accent"))
    palette.setColor(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, color("fg_disabled")
    )
    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.ButtonText,
        color("fg_disabled"),
    )
    if backdrop != "off":
        # **一处着色**：半透明时 QSS 已经给这些表面上了色，调色板再上一遍就是两层叠加 ——
        # 实测脚本视图 51% 的 alpha 被叠成 74%（1-(1-0.51)²），窗口底更是叠到 95%，
        # 于是用户看到的仍是"纯黑底和浅黑底"。这里让调色板只保留前景色、不再填充背景，
        # 透明度就由 QSS 那唯一一层如实生效。
        for role in (
            QPalette.ColorRole.Window,
            QPalette.ColorRole.Base,
            QPalette.ColorRole.AlternateBase,
            QPalette.ColorRole.Button,
            QPalette.ColorRole.ToolTipBase,
        ):
            palette.setColor(role, QColor(0, 0, 0, 0))
    return palette


def backdrop_colors(backdrop: str) -> dict[str, str | tuple[int, int, int, int]]:
    """按"背景效果"给出底色令牌。

    **两种效果都是界面自绘的**（不依赖窗口管理器/合成器）：半透明 = 壁纸不模糊 + 淡色调，
    亚克力 = 壁纸模糊 + 浓色调；模糊在 ui/acrylic.py 里用 Qt 自己算，窗口本身始终不透明。
    为什么不再问系统要模糊（Windows 的 DWM、KDE 的 KWin）：两条路在真机上都靠不住，
    原因与实测写在 ui/backdrop.py 的模块说明里。

    `off` 保持完全不透明：像素测试与"看不清就调不透明度"这类麻烦都不引入。
    """
    base = dict(TOKENS)
    # 结构性表面（栏目的卡片 / 面板本身）：默认与以前一样；开了背景效果就**不再自己上色**。
    # 原因是叠加：窗口底 78% + 卡片 76% + 面板/只读底 → 合成不透明度 ≈ 98.8%，等于不透明 ——
    # 用户反馈的"主工作区的黑底与浅黑底没有透明效果"就是叠出来的。
    # 所以只让**最外层**承担透明度，内部改用"极淡的一层白 + 边框"来区分区域。
    base["bg_surface"] = base["bg"]
    base["bg_card"] = base["bg_elevated"]
    if backdrop == "off":
        return base
    # 透明度要**看得出来**。踩过两个坑：
    #   1) 第一版给 92%，只有 8% 的壁纸透出来 —— 用户反馈"没有效果"；
    #   2) 后来给 78%，但**窗口底自己就是一层 78% 的深色盖在最上面**，内部表面再透，
    #      合成后最多也只能透出 22% —— 用户反馈"还是没有完全透明，还是纯黑底/浅黑底"。
    # 实测（把界面画到品红画布上反推等效不透明度）：窗口底 43%、内容区 47~51% 时，
    # 空白处能透出约一半、内容区仍能透出约三成 —— 既看得出透，文字对比也还在。
    base["bg"] = (16, 17, 20, 110)
    base["bg_surface"] = "transparent"          # 面板/页面：不再叠加一层底色
    base["bg_card"] = (255, 255, 255, 12)       # 卡片：只留极淡的一层白，靠边框区分
    # 内容区（脚本/报告/输出/列表）面积最大，"还是纯黑/浅黑"的印象主要来自它们 ——
    # 所以这几个降到"半透明能明显看出来"的程度；文字仍全不透明，可读性靠它保。
    base["bg_elevated"] = (23, 24, 28, 130)
    base["bg_under"] = (11, 12, 14, 120)
    # 输入类（输入框 / 下拉 / 数字框）比内容区**更不透明**：这些是读字与写字的地方，
    # 实测 51% 时下拉里的字体名与底色对比度只有 3.1:1（低于可读线 4.5:1），
    # 用户反馈的"看不见字"就是这一类。透明让给大块的只读内容区。
    base["bg_input"] = (27, 28, 33, 205)
    # 选择框同样保持输入级的 alpha（205）：它是读模型名/会话名的地方，
    # 跟着按钮那样透（按钮是 5% 白）会把字糊在壁纸上（实测对比度掉到 3.1:1 的那一类问题）。
    base["bg_select"] = (35, 36, 40, 205)
    # 浮层保持接近不透明（244/255 ≈ 96%）：透明只给"看内容"的表面，不给"读字"的浮层。
    base["bg_menu"] = (23, 24, 28, 244)
    return base


def _color(name: str, colors: dict) -> str:
    """QSS 用色：从（可能被背景效果改写过的）令牌表里取。"""
    value = colors[name]
    if isinstance(value, tuple) and len(value) == 4:
        r, g, b, a = value
        return f"rgba({r}, {g}, {b}, {a})"
    return str(value)


def build_stylesheet(
    *, scale: float = 1.0, ui_font: str = "", mono_font: str = "", backdrop: str = "off"
) -> str:
    """全局 QSS。目标不是"好看"，是**去掉原生控件感**并让语义色一致。

    三条规则贯穿始终：圆角只用 6/8px；分隔一律 1px 低对比边框（不用 QFrame 的凹陷/凸起、
    不用渐变）；交互反馈只用"叠加一层白"（hover 4% / 选中 8%），不换色相 —— 这样深色底上
    不会有跳出来的原生灰按钮。
    """
    colors = backdrop_colors(backdrop)
    mono = mono_font or mono_family()
    # **只让窗口那一层上色**：分割器、堆叠页、滚动区、标签页这些容器若各自再刷一遍窗口底色，
    # 43% 叠四五层就等于 97% 不透明 —— 实测主工作区 95~97%，正是用户说的"还是纯黑底和浅黑底"。
    # 容器一律不上色，底色只在最外层画一次；真正该有底色的表面（卡片/输入框/列表/按钮）
    # 都有自己的 ID 或类规则，不受影响。
    # **只在半透明模式下生效**：`off` 模式必须与以往逐像素一致（页签页的底色一撤，
    # 列表视口会把圆角填成直角 —— 实测 off 模式下列表角落与中心同色）。
    containers_transparent_rule = (
        """QSplitter, QStackedWidget, QScrollArea, QTabWidget::pane,
#leftPane, #centerPane, #rightPane, #toolTabs, #centerTabs, #rightTabs,
#settingsPage, #selfCheckPage, #historyPage, #templatesPage, #chatPanel, #wallpaperPage {
    background: transparent;
}"""
        if backdrop != "off"
        else ""
    )
    # 界面字体：没指定就用系统默认（不塞 font-family，交给 Qt/系统）
    ui_font_rule = (
        f'QWidget {{ font-family: "{ui_font}"; }}' if ui_font else ""
    )
    return f"""
/* ── 基底 ─────────────────────────────────────────────────────────── */
{ui_font_rule}
QWidget {{
    background-color: {_color('bg', colors)};
    color: {_color('fg', colors)};
    font-size: {sized('font_size', scale)};
}}
QMainWindow, QDialog {{ background-color: {_color('bg', colors)}; }}

/* 折叠区块的外壳与其内容容器**任何模式下都不上色**。
   它们被 Qt 标了 WA_StyledBackground，off 模式（没有 QSS 规则）时会用调色板的
   Window 色填充 —— 于是面板圆角外露出一圈**比卡片更深的窗口底色**，
   看起来就是"每个角上一块深黑的小方角"（用户报的就是这个）。 */
CollapsibleSection, #sectionBody, #findingsBody, #outputBody, #notesBody {{
    background: transparent;
}}
/* 纯布局容器**任何模式下都不上色**（第二组是"右列改成页签"那次漏掉的）：
   `#rightTabs` 不在名单里 → 它吃到通用 `QWidget {{ background-color: bg }}`，
   半透明/亚克力模式下给**整个右列**盖了一层深色（用户报的"会话底下的黑色底色"就是它：
   实测右列空白处亮度 46~51，而左栏同样的空白处是 78 —— 壁纸被压掉了三成），
   纯色模式下则在卡片里糊出一块比卡片更深的矩形。
   `#chatSessionRow` / `#proposalBar` / `#controlsRow` 同理（都是放布局的纯容器）。 */
#rightTabs, #chatSessionRow, #proposalBar, #controlsRow {{
    background: transparent;
}}
QLabel {{ background: transparent; }}

/* 分区小标题：Codex 的那种"小号大写、字距略宽、次级色" */
QLabel#paneHeader, QLabel[role="section"] {{
    color: {_color('fg_tertiary', colors)};
    font-size: {sized('font_size_section', scale)};
    font-weight: 600;
    letter-spacing: 0.08em;
    padding: 2px 0;
}}
QLabel[role="hint"], QLabel#statusLabel {{ color: {_color('fg_secondary', colors)}; }}
/* 栏内区块标题：收起时变淡（自动折叠没有箭头，靠这个提示状态） */
QLabel#sectionHeader[collapsed="true"] {{ color: {_color('fg_disabled', colors)}; }}
QLabel[role="muted"] {{ color: {_color('fg_tertiary', colors)}; font-size: {sized('font_size_small', scale)}; }}

/* ── 面板 ─────────────────────────────────────────────────────────── */
/* 面板不再画 1px 边框，改用更大圆角的浅色卡片：圆角要看得出来，边框就得退到很淡；
   分隔感由分割条 hover 与留白提供。 */
QWidget#leftPane, QWidget#centerPane, QWidget#rightPane, QWidget#templatesPane,
QWidget#historyPage, QWidget#settingsPage, QWidget#selfCheckPage, QWidget#chatPanel {{
    background-color: {_color('bg_surface', colors)};
}}
QWidget#chatPanel {{ border: 1px solid {_color('border_light', colors)}; border-radius: {sized('radius_lg', scale)}; }}
/* 「引用」条（选中代码带进对话时的那一行）：必须显式上色 —— 不写规则的 QWidget 会被上面
   那条通用 QWidget 规则填成窗口底色（#101114），在 bg_surface 的面板里像一道凹陷的黑条
   （折叠区块的"深黑色角"就是同一类原因）。 */
QWidget#chatQuoteBar {{
    background-color: {_color('bg_card', colors)};
    border: 1px solid {_color('border_light', colors)};
    border-radius: {sized('radius', scale)};
}}
QPushButton#chatQuoteClearButton {{ padding: 2px 8px; min-height: 18px; }}
/* 三栏/底栏的"卡片"：大圆角 + 极淡边框。栏与栏的分隔靠它，而不是靠那条透明的分割条。 */
QWidget#paneCard {{
    background-color: {_color('bg_card', colors)};
    border: 1px solid {_color('border_light', colors)};
    border-radius: {sized('radius_lg', scale)};
}}

/* ── 按钮：无渐变、无阴影、1px 边框 ────────────────────────────── */
QPushButton {{
    background-color: {_color('bg_button', colors)};
    color: {_color('fg', colors)};
    border: 1px solid {_color('border', colors)};
    border-radius: {sized('radius', scale)};
    padding: 5px 12px;
    min-height: 22px;
}}
QPushButton:hover {{ background-color: {_color('bg_button_hover', colors)}; border-color: {_color('border_heavy', colors)}; }}
QPushButton:pressed {{ background-color: {_color('bg_button_active', colors)}; }}
QPushButton:disabled {{
    color: {_color('fg_disabled', colors)};
    border-color: {_color('border_light', colors)};
    background-color: transparent;
}}
QPushButton:focus {{ border-color: {_color('border_focus', colors)}; }}
/* 主按钮（开始）：Codex 用白底黑字表示"主操作"。
   **必须重复写 border-radius**：Qt 里 `border` 简写会把同一条规则之外的圆角重置掉，
   只写 `border: 1px solid ...` 的按钮会被画成**直角矩形**（实测：主按钮整块是方的，
   而同一条基类规则下的次按钮是圆的 —— 因为基类规则自己带了 border-radius）。 */
QPushButton#primaryButton {{
    background-color: {_color('fg', colors)};
    color: {_color('fg_on_accent', colors)};
    border: 1px solid {_color('fg', colors)};
    border-radius: {sized('radius', scale)};
    font-weight: 600;
}}
QPushButton#primaryButton:hover {{ background-color: #e6e6e6; border-color: #e6e6e6; }}
QPushButton#primaryButton:disabled {{
    background-color: {_color('bg_button', colors)};
    color: {_color('fg_disabled', colors)};
    border-color: {_color('border_light', colors)};
}}

/* ── 输入类：深底 + 1px 边框 + 蓝色焦点 ───────────────────────── */
QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser, QSpinBox, QComboBox {{
    background-color: {_color('bg_input', colors)};
    color: {_color('fg', colors)};
    border: 1px solid {_color('border_heavy', colors)};
    /* 圆角用 `radius`（10px）而**不是** `radius_lg`（14px）：Qt 画圆角时，
       半径 >= 控件高度的一半就整个退回**直角**（页签那条注释里记过同一件事）。
       单行输入控件没有 min-height，高度由字体度量决定 —— 正好卡在这个边界上：
       实测 ui_scale=1.0 时高 29px、半径 14px（勉强圆角），1.4/1.6 时高 35/39px、
       半径 19.6/22.4px → **直角**；Windows 的字体度量还会再矮一点，1.0 就直角了
       （用户报的"会话这里不是圆角的"就是这个）。改成 10px 之后各档都有余量。 */
    border-radius: {sized('radius', scale)};
    padding: 4px 8px;
    selection-background-color: {_color('accent', colors)};
    selection-color: {_color('fg_on_accent', colors)};
}}
/* 注意：这里**故意不写** min-height。
   QSS 的 `min-height` 会覆盖 widget 的 `setMinimumHeight()`（加到 QPlainTextEdit 上会把
   方案预览/脚本视图定制的 90/140px 下限冲掉，实测从 90 掉到 34）；而且用它来防"控件被压扁"
   是无效的 —— 容器比最小尺寸还小时 Qt 照样会挤压，真正管用的是把表单放进滚动区
   （见 widgets/scroll.py）加上窗口/栏目的最小尺寸。 */
QLineEdit:hover, QSpinBox:hover, QComboBox:hover {{ border-color: {_color('border_strong', colors)}; }}
/* 「选择框」用按钮那种浅底（用户要求：会话/模型这类选择框不要黑底，与旁边的按钮同类）。
   必须放在上面那条组规则**之后**：两者都是单类型选择器，同优先级时后写的生效。 */
QComboBox {{ background-color: {_color('bg_select', colors)}; }}
/* 可编辑的 QComboBox 内部是一个 QLineEdit，会被上面 `QLineEdit` 那条规则命中 ——
   理论上会在浅色下拉里画出一块深色方框。**实测（Qt 6.11 + Fusion）当前不会**：
   去掉这条之后内部仍是下拉自己的底色，取不到文本框底色。留着它是**防御性**的
   （换 Qt 版本或换样式时可能出现深框，那时用例 test_editable_combo_has_no_dark_box_inside
   会转红）。 */
QComboBox QLineEdit {{ background: transparent; border: none; padding: 0; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QTextBrowser:focus,
QSpinBox:focus, QComboBox:focus {{ border-color: {_color('border_focus', colors)}; }}
QLineEdit:read-only, QPlainTextEdit:read-only, QTextEdit:read-only, QTextBrowser:read-only {{
    background-color: {_color('bg_under', colors)};
}}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{ color: {_color('fg_disabled', colors)}; }}

/* 下拉与微调按钮去掉原生立体感 */
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{
    background-color: {_color('bg_menu', colors)};
    border: 1px solid {_color('border', colors)};
    selection-background-color: {_color('bg_selected', colors)};
    selection-color: {_color('fg', colors)};
    outline: none;
}}
QSpinBox::up-button, QSpinBox::down-button {{
    background-color: transparent; border: none; width: 16px;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{ background-color: {_color('bg_hover', colors)}; }}

/* ── 列表 / 树 / 表：行高 30、无网格、hover 4% ─────────────────── */
QListWidget, QTreeWidget, QTableWidget {{
    background-color: {_color('bg_elevated', colors)};
    border: 1px solid {_color('border_light', colors)};
    border-radius: {sized('radius_lg', scale)};
    outline: none;
    alternate-background-color: {_color('bg_under', colors)};
}}
QListWidget::item, QTreeWidget::item {{ min-height: {sized('row_height', scale)}; padding: 2px 6px; }}
QListWidget::item:hover, QTreeWidget::item:hover {{ background-color: {_color('bg_hover', colors)}; }}
QListWidget::item:selected, QTreeWidget::item:selected {{
    background-color: {_color('bg_selected', colors)};
    color: {_color('fg', colors)};
}}
/* 表头：`QHeaderView` 自己的视口会用调色板 Base 铺满（= 窗口底色），
   而它是列表/树的**子控件、不会被父控件的圆角裁剪** —— 于是在面板的左上/右上角
   露出一块比卡片更深的方块（用户截图上圈的"深黑小角"里就有它）。
   所以表头**整体透明**，让下面那块圆角面板自己显示。 */
QHeaderView, QHeaderView QWidget {{
    background: transparent;
}}
QHeaderView::section {{
    background: transparent;      /* 底色交给下面的面板：表头是子控件，不会被圆角裁剪 */
    color: {_color('fg_tertiary', colors)};
    border: none;
    border-bottom: 1px solid {_color('border_light', colors)};
    padding: 4px 6px;
}}

/* ── 页签：Codex 的胶囊式，去掉原生边框与底部横线 ─────────────── */
{containers_transparent_rule}
QTabWidget::pane {{ border: 1px solid {_color('border_light', colors)}; border-radius: {sized('radius_lg', scale)}; top: -1px; }}
/* 页签条的底色**必须显式给**（否则由调色板自己刷，画出来是方角的一大块 ——
   用户截图圈的就是它：页签条整条比周围多叠一层，69% vs 46%，而且是直角矩形）。
   显式上色 + 圆角，才和下面的面板圆角对得上。 */
QTabBar {{
    qproperty-drawBase: 0;
    background-color: {_color('bg_elevated', colors)};
    border-radius: {sized('radius_lg', scale)};
}}
QTabBar::tab {{
    background: transparent;
    color: {_color('fg_tertiary', colors)};
    border: 1px solid transparent;
    border-radius: {sized('radius_pill', scale)};   /* 胶囊式页签（半径必须 < 页签半高，见 TOKENS 注释） */
    padding: 5px 14px;
    margin-right: 6px;
    min-height: 24px;
}}
QTabBar::tab:hover {{ color: {_color('fg', colors)}; background-color: {_color('bg_hover', colors)}; }}
QTabBar::tab:selected {{
    color: {_color('fg', colors)};
    background-color: {_color('bg_button', colors)};
    border-color: {_color('border', colors)};
}}

/* ── 分割器：1px 细线，不拖出宽槽 ──────────────────────────────── */
/* 分割条：**可抓宽度必须是毫米级手感**（6px），但平时不显色 —— 之前写死 1px 之后
   用户根本抓不住（"不能调节竖向的长度"就是这么来的）。1px 的分隔感改由各面板自己的
   1px 边框提供，所以视觉上依然是细线。 */
QSplitter::handle {{ background-color: transparent; }}
QSplitter::handle:horizontal {{ width: {sized('handle', scale)}; }}
QSplitter::handle:vertical {{ height: {sized('handle', scale)}; }}
QSplitter::handle:hover {{ background-color: {_color('border_heavy', colors)}; }}
QSplitter::handle:pressed {{ background-color: {_color('accent', colors)}; }}

/* ── 菜单 / 提示 / 滚动条 ─────────────────────────────────────── */
QMenu {{ background-color: {_color('bg_menu', colors)}; border: 1px solid {_color('border', colors)}; border-radius: {sized('radius', scale)}; }}
QMenu::item {{ padding: 5px 18px; }}
QMenu::item:selected {{ background-color: {_color('bg_selected', colors)}; }}
QToolTip {{
    background-color: {_color('bg_menu', colors)};
    color: {_color('fg_secondary', colors)};
    border: 1px solid {_color('border', colors)};
    border-radius: {sized('radius', scale)};
    padding: 4px 6px;
}}
/* 滚动条轨道用**所在控件**的底色，不用 transparent：
   透明时轨道区域显示的是背后那一层（卡片色），与控件自己的底色不一致。
   注：这条是为了配色一致，**不声称**修掉了用户报的"圆角没覆盖完全" —— 那个现象我复现过一次，
   但在当前代码里再也复现不出来（两种写法取色完全相同），所以没有对应的测试。 */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
/* 轨道底色按**控件名**点名，不用 `:read-only`：
   实测 `QPlainTextEdit:read-only QScrollBar` 会把伪状态判在**滚动条自己**身上（它永远不是只读），
   于是只读视图的滚动条被涂成"可编辑输入框"的 #121317 —— 117 个像素混在深色底里，
   看起来就是圆角旁边一小块长方形深色（用户报的"尖尖的黑色"）。 */
QPlainTextEdit#outputView QScrollBar, QPlainTextEdit#scriptView QScrollBar,
QPlainTextEdit#notesView QScrollBar, QPlainTextEdit#chatTranscript QScrollBar,
QTextBrowser#compareView QScrollBar, QPlainTextEdit#planPreview QScrollBar
{{ background: {_color('bg_under', colors)}; }}
QPlainTextEdit#extraInstructionEdit QScrollBar, QPlainTextEdit#chatInput QScrollBar
{{ background: {_color('bg_input', colors)}; }}
QListWidget QScrollBar, QTreeWidget QScrollBar, QTableWidget QScrollBar,
QScrollArea QScrollBar {{ background: {_color('bg_elevated', colors)}; }}
QScrollBar::handle {{ background-color: {_color('border_heavy', colors)}; border-radius: 4px; min-height: 28px; }}
QScrollBar::handle:hover {{ background-color: {_color('fg_disabled', colors)}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ── 复选框 ───────────────────────────────────────────────────── */
QCheckBox {{ spacing: 6px; }}
QCheckBox::indicator {{
    width: 15px; height: 15px;
    border: 1px solid {_color('border_heavy', colors)};
    border-radius: 5px;
    background-color: {_color('bg_input', colors)};
}}
QCheckBox::indicator:checked {{ background-color: {_color('accent', colors)}; border-color: {_color('accent', colors)}; }}

/* ── 等宽区：脚本 / 报告 / 输出 / diff / 预览 ─────────────────── */
QPlainTextEdit#scriptView, QPlainTextEdit#outputView, QPlainTextEdit#notesView,
QTreeWidget#findingsTree, QTextBrowser#compareView, QPlainTextEdit#planPreview,
QPlainTextEdit#templateBody, QPlainTextEdit#templatePreview, QPlainTextEdit#confirmScriptView {{
    font-family: "{mono}";
    font-size: {sized('font_size_small', scale)};
}}
QPlainTextEdit#notesView {{ font-family: inherit; }}
/* 对话记录与输入框也用等宽：里面的脚本片段要能对齐。
   记录区**显式**给"更深的只读底"：它挂在工具区页签里之后，`QPlainTextEdit:read-only`
   这条通用规则不再稳定命中（实测渲染成了输入框的底色），所以在这里写死 ——
   "只读区看起来和输入框一样"会让人以为可以直接在记录里打字。 */
QPlainTextEdit#chatTranscript {{
    background-color: {_color('bg_under', colors)};
    font-family: "{mono}";
    font-size: {sized('font_size_small', scale)};
}}
QPlainTextEdit#chatInput {{ font-family: "{mono}"; font-size: {sized('font_size_small', scale)}; }}

/* ── 底栏状态：单行、次级色、上方一条细线 ─────────────────────── */
QLabel#statusLabel {{
    color: {_color('fg_secondary', colors)};
    padding-left: 10px;
    border-left: 1px solid {_color('border_light', colors)};
}}
QStatusBar {{ background-color: {_color('bg', colors)}; border-top: 1px solid {_color('border_light', colors)}; }}
"""


def apply_theme(
    app: QApplication,
    *,
    scale: float = 1.0,
    ui_font: str = "",
    mono_font: str = "",
    backdrop: str = "off",
) -> None:
    """给整个应用装主题（幂等，可重复调用；改设置后直接再调一次即可热更新）。

    - `scale` 同时放大字号与所有尺寸（圆角、行高、内边距）——只放大字号会让界面变挤；
    - `ui_font` / `mono_font` 为空表示"使用系统默认 / 自动选择等宽字体"；
    - `backdrop` 见 `backdrop_colors()`；窗口级的底色层与重绘擦除由 `ui/backdrop.py` 处理
      （两个效果都是界面自绘，不依赖系统）。
    """
    app.setStyle("Fusion")          # 原生样式会带来各自的立体感，Fusion 才吃调色板

    # 浮层底色：任何模式下都显式钉住（off 模式下它就是原来的面板色，外观不变）
    global _popup_style, _popup_container_style
    _popup_container_style = (
        "background-color: {menu}; border: 1px solid {border}; border-radius: {radius};"
    ).format(
        menu=_color("bg_menu", backdrop_colors(backdrop)),
        border=_color("border", backdrop_colors(backdrop)),
        radius=sized("radius_lg", scale),
    )
    _popup_style = (
        "QAbstractItemView, QMenu {{ background-color: {menu}; color: {fg};"
        " border: 1px solid {border}; }}"
    ).format(
        menu=_color("bg_menu", backdrop_colors(backdrop)),
        fg=_color("fg", backdrop_colors(backdrop)),
        border=_color("border", backdrop_colors(backdrop)),
    )
    _install_popup_keeper(app)
    # 已经存在的浮层也先钉一遍（事件只在之后才发生，启动时就建好的下拉要在这里兜住）
    for widget in app.allWidgets():
        _pin_popup(widget)

    font = QFont()
    if ui_font:
        font.setFamily(ui_font)
    font.setPointSizeF(scaled_font_size(scale))
    palette = build_palette(backdrop=backdrop)
    sheet = build_stylesheet(
        scale=scale, ui_font=ui_font, mono_font=mono_font, backdrop=backdrop
    )

    # **幂等短路**：内容完全一样时不要再设一遍 —— `setStyleSheet` 会重新 polish 进程里
    # 所有活着的控件树，代价随窗口数增长（实测把整套测试从 47s 拖到 332s）。
    # 设置真的变了（或第一次装）才会走到下面三行。
    current = app.font()
    if (
        app.styleSheet() == sheet
        and app.palette() == palette
        and current.family() == font.family()
        and current.pointSizeF() == font.pointSizeF()
    ):
        return

    app.setPalette(palette)
    app.setStyleSheet(sheet)
    app.setFont(font)


# 语义色出口：界面代码不要自己写颜色，从这里取，主题换的时候才不会漏。
# 用 QColor 的地方（QPainter/QTextCharFormat）拿 qcolor(...)，拼 HTML 的地方拿 css(...)。
STDERR_COLOR = qcolor("error")
BLOCKING_COLOR = qcolor("error")
NON_BLOCKING_COLOR = qcolor("muted")
DIFF_ADDED_BG = "#12241c"      # 深色底上的"新增行"：低饱和深底，别用浅绿整块糊上去
DIFF_ADDED_FG = "#6fd39b"
DIFF_REMOVED_BG = "#2a1417"
DIFF_REMOVED_FG = "#f2909a"
DIFF_GUTTER_FG = css("fg_tertiary")
