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
    "bg_input": "#17181c",
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
    "radius_pill": "999px",
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


def build_palette() -> QPalette:
    """把令牌灌进 QPalette：控件自绘的部分（行号槽、文本选中、滚动条）也跟着变。"""
    palette = QPalette()
    bg = qcolor("bg")
    elevated = qcolor("bg_elevated")
    fg = qcolor("fg")

    palette.setColor(QPalette.ColorRole.Window, bg)
    palette.setColor(QPalette.ColorRole.WindowText, fg)
    palette.setColor(QPalette.ColorRole.Base, elevated)
    palette.setColor(QPalette.ColorRole.AlternateBase, qcolor("bg_under"))
    palette.setColor(QPalette.ColorRole.Text, fg)
    palette.setColor(QPalette.ColorRole.Button, elevated)
    palette.setColor(QPalette.ColorRole.ButtonText, fg)
    palette.setColor(QPalette.ColorRole.ToolTipBase, qcolor("bg_under"))
    palette.setColor(QPalette.ColorRole.ToolTipText, fg)
    palette.setColor(QPalette.ColorRole.PlaceholderText, qcolor("fg_tertiary"))
    palette.setColor(QPalette.ColorRole.Highlight, qcolor("accent"))
    palette.setColor(QPalette.ColorRole.HighlightedText, qcolor("fg_on_accent"))
    palette.setColor(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, qcolor("fg_disabled")
    )
    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.ButtonText,
        qcolor("fg_disabled"),
    )
    return palette


def build_stylesheet() -> str:
    """全局 QSS。目标不是"好看"，是**去掉原生控件感**并让语义色一致。

    三条规则贯穿始终：圆角只用 6/8px；分隔一律 1px 低对比边框（不用 QFrame 的凹陷/凸起、
    不用渐变）；交互反馈只用"叠加一层白"（hover 4% / 选中 8%），不换色相 —— 这样深色底上
    不会有跳出来的原生灰按钮。
    """
    mono = mono_family()
    return f"""
/* ── 基底 ─────────────────────────────────────────────────────────── */
QWidget {{
    background-color: {css('bg')};
    color: {css('fg')};
    font-size: {css('font_size')};
}}
QMainWindow, QDialog {{ background-color: {css('bg')}; }}
QLabel {{ background: transparent; }}

/* 分区小标题：Codex 的那种"小号大写、字距略宽、次级色" */
QLabel#paneHeader, QLabel[role="section"] {{
    color: {css('fg_tertiary')};
    font-size: {css('font_size_section')};
    font-weight: 600;
    letter-spacing: 0.08em;
    padding: 2px 0;
}}
QLabel[role="hint"], QLabel#statusLabel {{ color: {css('fg_secondary')}; }}
QLabel[role="muted"] {{ color: {css('fg_tertiary')}; font-size: {css('font_size_small')}; }}

/* ── 面板 ─────────────────────────────────────────────────────────── */
/* 面板不再画 1px 边框，改用更大圆角的浅色卡片：圆角要看得出来，边框就得退到很淡；
   分隔感由分割条 hover 与留白提供。 */
QWidget#leftPane, QWidget#centerPane, QWidget#rightPane, QWidget#templatesPane,
QWidget#historyPage, QWidget#settingsPage, QWidget#selfCheckPage, QWidget#chatPanel {{
    background-color: {css('bg')};
}}
QWidget#chatPanel {{ border: 1px solid {css('border_light')}; border-radius: {css('radius_lg')}; }}
/* 三栏/底栏的"卡片"：大圆角 + 极淡边框。栏与栏的分隔靠它，而不是靠那条透明的分割条。 */
QWidget#paneCard {{
    background-color: {css('bg_elevated')};
    border: 1px solid {css('border_light')};
    border-radius: {css('radius_lg')};
}}

/* ── 按钮：无渐变、无阴影、1px 边框 ────────────────────────────── */
QPushButton {{
    background-color: {css('bg_button')};
    color: {css('fg')};
    border: 1px solid {css('border')};
    border-radius: {css('radius')};
    padding: 5px 12px;
    min-height: 22px;
}}
QPushButton:hover {{ background-color: {css('bg_button_hover')}; border-color: {css('border_heavy')}; }}
QPushButton:pressed {{ background-color: {css('bg_button_active')}; }}
QPushButton:disabled {{
    color: {css('fg_disabled')};
    border-color: {css('border_light')};
    background-color: transparent;
}}
QPushButton:focus {{ border-color: {css('border_focus')}; }}
/* 主按钮（开始）：Codex 用白底黑字表示"主操作" */
QPushButton#primaryButton {{
    background-color: {css('fg')};
    color: {css('fg_on_accent')};
    border: 1px solid {css('fg')};
    font-weight: 600;
}}
QPushButton#primaryButton:hover {{ background-color: #e6e6e6; border-color: #e6e6e6; }}
QPushButton#primaryButton:disabled {{
    background-color: {css('bg_button')};
    color: {css('fg_disabled')};
    border-color: {css('border_light')};
}}

/* ── 输入类：深底 + 1px 边框 + 蓝色焦点 ───────────────────────── */
QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser, QSpinBox, QComboBox {{
    background-color: {css('bg_input')};
    color: {css('fg')};
    border: 1px solid {css('border')};
    border-radius: {css('radius_lg')};
    padding: 4px 8px;
    selection-background-color: {css('accent')};
    selection-color: {css('fg_on_accent')};
}}
QLineEdit:hover, QSpinBox:hover, QComboBox:hover {{ border-color: {css('border_heavy')}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QTextBrowser:focus,
QSpinBox:focus, QComboBox:focus {{ border-color: {css('border_focus')}; }}
QLineEdit:read-only, QPlainTextEdit:read-only, QTextEdit:read-only, QTextBrowser:read-only {{
    background-color: {css('bg_under')};
}}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{ color: {css('fg_disabled')}; }}

/* 下拉与微调按钮去掉原生立体感 */
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{
    background-color: {css('bg_elevated')};
    border: 1px solid {css('border')};
    selection-background-color: {css('bg_selected')};
    selection-color: {css('fg')};
    outline: none;
}}
QSpinBox::up-button, QSpinBox::down-button {{
    background-color: transparent; border: none; width: 16px;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{ background-color: {css('bg_hover')}; }}

/* ── 列表 / 树 / 表：行高 30、无网格、hover 4% ─────────────────── */
QListWidget, QTreeWidget, QTableWidget {{
    background-color: {css('bg_elevated')};
    border: 1px solid {css('border_light')};
    border-radius: {css('radius_lg')};
    outline: none;
    alternate-background-color: {css('bg_under')};
}}
QListWidget::item, QTreeWidget::item {{ min-height: {css('row_height')}; padding: 2px 6px; }}
QListWidget::item:hover, QTreeWidget::item:hover {{ background-color: {css('bg_hover')}; }}
QListWidget::item:selected, QTreeWidget::item:selected {{
    background-color: {css('bg_selected')};
    color: {css('fg')};
}}
QHeaderView::section {{
    background-color: {css('bg')};
    color: {css('fg_tertiary')};
    border: none;
    border-bottom: 1px solid {css('border_light')};
    padding: 4px 6px;
}}

/* ── 页签：Codex 的胶囊式，去掉原生边框与底部横线 ─────────────── */
QTabWidget::pane {{ border: 1px solid {css('border_light')}; border-radius: {css('radius_lg')}; top: -1px; }}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{
    background: transparent;
    color: {css('fg_tertiary')};
    border: 1px solid transparent;
    border-radius: {css('radius_pill')};   /* 胶囊式页签：圆角最大的地方，最能出"高级感" */
    padding: 5px 14px;
    margin-right: 6px;
    min-height: 22px;
}}
QTabBar::tab:hover {{ color: {css('fg')}; background-color: {css('bg_hover')}; }}
QTabBar::tab:selected {{
    color: {css('fg')};
    background-color: {css('bg_button')};
    border-color: {css('border')};
}}

/* ── 分割器：1px 细线，不拖出宽槽 ──────────────────────────────── */
/* 分割条：**可抓宽度必须是毫米级手感**（6px），但平时不显色 —— 之前写死 1px 之后
   用户根本抓不住（"不能调节竖向的长度"就是这么来的）。1px 的分隔感改由各面板自己的
   1px 边框提供，所以视觉上依然是细线。 */
QSplitter::handle {{ background-color: transparent; }}
QSplitter::handle:horizontal {{ width: {css('handle')}; }}
QSplitter::handle:vertical {{ height: {css('handle')}; }}
QSplitter::handle:hover {{ background-color: {css('border_heavy')}; }}
QSplitter::handle:pressed {{ background-color: {css('accent')}; }}

/* ── 菜单 / 提示 / 滚动条 ─────────────────────────────────────── */
QMenu {{ background-color: {css('bg_elevated')}; border: 1px solid {css('border')}; border-radius: {css('radius')}; }}
QMenu::item {{ padding: 5px 18px; }}
QMenu::item:selected {{ background-color: {css('bg_selected')}; }}
QToolTip {{
    background-color: {css('bg_under')};
    color: {css('fg_secondary')};
    border: 1px solid {css('border')};
    padding: 4px 6px;
}}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle {{ background-color: {css('border_heavy')}; border-radius: 4px; min-height: 28px; }}
QScrollBar::handle:hover {{ background-color: {css('fg_disabled')}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ── 复选框 ───────────────────────────────────────────────────── */
QCheckBox {{ spacing: 6px; }}
QCheckBox::indicator {{
    width: 15px; height: 15px;
    border: 1px solid {css('border_heavy')};
    border-radius: 5px;
    background-color: {css('bg_input')};
}}
QCheckBox::indicator:checked {{ background-color: {css('accent')}; border-color: {css('accent')}; }}

/* ── 等宽区：脚本 / 报告 / 输出 / diff / 预览 ─────────────────── */
QPlainTextEdit#scriptView, QPlainTextEdit#outputView, QPlainTextEdit#notesView,
QTreeWidget#findingsTree, QTextBrowser#compareView, QPlainTextEdit#planPreview,
QPlainTextEdit#templateBody, QPlainTextEdit#templatePreview, QPlainTextEdit#confirmScriptView {{
    font-family: "{mono}";
    font-size: {css('font_size_small')};
}}
QPlainTextEdit#notesView {{ font-family: inherit; }}
/* 对话记录与输入框也用等宽：里面的脚本片段要能对齐。
   记录区**显式**给"更深的只读底"：它挂在工具区页签里之后，`QPlainTextEdit:read-only`
   这条通用规则不再稳定命中（实测渲染成了输入框的底色），所以在这里写死 ——
   "只读区看起来和输入框一样"会让人以为可以直接在记录里打字。 */
QPlainTextEdit#chatTranscript {{
    background-color: {css('bg_under')};
    font-family: "{mono}";
    font-size: {css('font_size_small')};
}}
QPlainTextEdit#chatInput {{ font-family: "{mono}"; font-size: {css('font_size_small')}; }}

/* ── 底栏状态：单行、次级色、上方一条细线 ─────────────────────── */
QLabel#statusLabel {{
    color: {css('fg_secondary')};
    padding-left: 10px;
    border-left: 1px solid {css('border_light')};
}}
QStatusBar {{ background-color: {css('bg')}; border-top: 1px solid {css('border_light')}; }}
"""


def apply_theme(app: QApplication) -> None:
    """给整个应用装上 Codex 风格深色主题（幂等，可重复调用）。"""
    app.setStyle("Fusion")          # 原生样式会带来各自的立体感，Fusion 才吃调色板
    app.setPalette(build_palette())
    app.setStyleSheet(build_stylesheet())
    font = QFont()
    font.setPointSizeF(9.75)        # ≈13px，与 Codex 的 0.875rem 一致
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
