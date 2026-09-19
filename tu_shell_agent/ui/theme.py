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

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

# ── 令牌 ────────────────────────────────────────────────────────────────────
#
# 配色取向（2026-09-19 用户要求"显得高级、尽量圆角"）：底色从 Codex 的 #181818 往**冷调
# 更深**走（#101114），面板只比底色亮一档（#17181c）——"高级感"主要来自**低对比的分层**，
# 而不是把面板刷得比背景亮很多；文字不用纯白（#ecedf0），因为纯白在高对比深底上偏"廉价"；
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
    "bg_elevated": "#17181c",     # 面板/输入：只比底色亮一档
    "bg_under": "#0b0c0e",        # 更深的一层（只读底、凹陷）
    # 输入框比面板**更深**：和面板同色时，圆角处的像素与填充同色 —— 形状根本看不出来
    # （用户报的"圆角边框 + 长方形底色"里有一部分就是这个：输入框和卡片都是 #17181c）。
    "bg_input": "#121317",
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
    """挑一个真实存在的等宽字体族（找不到就交给 Qt 的 monospace 别名）。

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


def unstack_viewports(root) -> int:
    """让滚动区的**视口**不再重复画一遍底色，返回处理过的数量。

    Qt 的行为：给 QAbstractScrollArea 设了 QSS 的 background-color 之后，视口会自己再画一层
    同样的颜色。半透明模式下这就成了叠加 —— 实测 `bg_under` 的 51% 被叠成 74%
    （1-(1-0.51)²），于是脚本/输出/报告这些大块头看起来"还是纯黑底"。
    这里把视口底色交还给外面的那一层，只画一次，透明度才如实生效。
    """
    from PySide6.QtWidgets import QAbstractScrollArea

    handled = 0
    for area in [root, *root.findChildren(QAbstractScrollArea)]:
        if not isinstance(area, QAbstractScrollArea):
            continue
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
        QAbstractScrollArea,
        QFrame,
        QScrollArea,
        QSplitter,
        QStackedWidget,
        QTabWidget,
        QWidget,
    )

    container_types = (QSplitter, QStackedWidget, QScrollArea, QTabWidget, QAbstractScrollArea)
    keep = {"paneCard", "confirmScriptView"}
    handled = 0
    for widget in [root, *root.findChildren(QWidget)]:
        if widget.objectName() in keep:
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
    from PySide6.QtWidgets import QAbstractScrollArea

    handled = 0
    for area in [root, *root.findChildren(QAbstractScrollArea)]:
        if not isinstance(area, QAbstractScrollArea):
            continue
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

    **亚克力模糊**（Windows 11 的 Acrylic / macOS 的毛玻璃）需要窗口管理器支持：真正能拿到的是
    "窗口半透明 + 由窗口管理器去模糊背后的内容"。所以这里做两件事：
      1. 把底色变成带 alpha 的颜色（半透明）—— 这一步与平台无关，任何合成器都能生效；
      2. 平台模糊由 ui/backdrop.py 去尝试（Windows 走 DWM，其它平台多半拿不到）。
    拿不到模糊时**只保留半透明**，界面会如实告诉用户（不假装模糊成功了）。

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
    base["bg_input"] = (18, 19, 23, 130)
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
#leftPane, #centerPane, #rightPane, #toolTabs, #centerTabs,
#settingsPage, #selfCheckPage, #historyPage, #templatesPage, #chatPanel, #wallpaperPage,
CollapsibleSection, #sectionBody {
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
    border: 1px solid {_color('border', colors)};
    border-radius: {sized('radius_lg', scale)};
    padding: 4px 8px;
    selection-background-color: {_color('accent', colors)};
    selection-color: {_color('fg_on_accent', colors)};
}}
/* 注意：这里**故意不写** min-height。
   QSS 的 `min-height` 会覆盖 widget 的 `setMinimumHeight()`（加到 QPlainTextEdit 上会把
   方案预览/脚本视图定制的 90/140px 下限冲掉，实测从 90 掉到 34）；而且用它来防"控件被压扁"
   是无效的 —— 容器比最小尺寸还小时 Qt 照样会挤压，真正管用的是把表单放进滚动区
   （见 widgets/scroll.py）加上窗口/栏目的最小尺寸。 */
QLineEdit:hover, QSpinBox:hover, QComboBox:hover {{ border-color: {_color('border_heavy', colors)}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QTextBrowser:focus,
QSpinBox:focus, QComboBox:focus {{ border-color: {_color('border_focus', colors)}; }}
QLineEdit:read-only, QPlainTextEdit:read-only, QTextEdit:read-only, QTextBrowser:read-only {{
    background-color: {_color('bg_under', colors)};
}}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{ color: {_color('fg_disabled', colors)}; }}

/* 下拉与微调按钮去掉原生立体感 */
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{
    background-color: {_color('bg_elevated', colors)};
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
QHeaderView::section {{
    background-color: {_color('bg', colors)};
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
QMenu {{ background-color: {_color('bg_elevated', colors)}; border: 1px solid {_color('border', colors)}; border-radius: {sized('radius', scale)}; }}
QMenu::item {{ padding: 5px 18px; }}
QMenu::item:selected {{ background-color: {_color('bg_selected', colors)}; }}
QToolTip {{
    background-color: {_color('bg_under', colors)};
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
    - `ui_font` / `mono_font` 为空表示"用系统默认 / 自动挑一个等宽字体"；
    - `backdrop` 见 `backdrop_colors()`；窗口级的半透明与平台模糊由 `ui/backdrop.py` 处理。
    """
    app.setStyle("Fusion")          # 原生样式会带来各自的立体感，Fusion 才吃调色板

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
