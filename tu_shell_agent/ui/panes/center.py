"""中栏：本轮脚本（带行号）+ 与上一轮的 diff + 轮次时间线。

纯展示控件：只吃字符串与轮次号，不读文件、不起子进程。RunEvent 到界面的映射
（script → show_round、shellcheck → add_timeline_entry 等）留给 run_controller。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QListWidget, QListWidgetItem, QSplitter, QTabWidget, QTextBrowser, QTextEdit,
    QVBoxLayout, QWidget,
)

from ..widgets.diff_view import diff_counts, render_diff_html
from ..widgets.script_view import ScriptView
from ..theme import DIFF_GUTTER_FG

_CURRENT_TAB = 0
_COMPARE_TAB = 1


class CenterPane(QWidget):
    """脚本正文、与上一轮的差异、以及每一轮发生了什么。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("centerPane")  # main_window 与界面骨架测试按这个 objectName 找控件

        self.tabs = QTabWidget()
        self.tabs.setObjectName("centerTabs")

        self.script_view = ScriptView()
        self.compare_view = QTextBrowser()
        self.compare_view.setObjectName("compareView")
        # 与脚本视图保持一致：脚本不折行，长行靠横向滚动条看
        self.compare_view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.compare_view.setOpenExternalLinks(False)
        # 各块都要有能用的最小高度，否则窗口一缩小就被压成一条缝（实测 12~35px）
        # 140 是"宽屏舒服"的值，但在高 DPI（Windows 150% 时 1080p 只有 720 逻辑像素高）下
        # 它把整窗地板抬到 666，比屏幕还高 —— 于是布局只能违反最小尺寸，右栏与对话面板被压。
        # 80 仍然能看几行脚本，余下的高度留给"窗口地板低于屏幕"这件更要紧的事。
        self.script_view.setMinimumHeight(80)
        # 与 script_view 同理：两个视图在同一个页签里，页签高度取两者的最大值，
        # 留一个 140 会把整窗地板抬到 666（见 script_view 的注释）。
        self.compare_view.setMinimumHeight(80)

        self.tabs.addTab(self.script_view, "本轮")
        self.tabs.addTab(self.compare_view, "对比上一轮")

        self.timeline = QListWidget()
        self.timeline.setObjectName("timeline")
        self.timeline.setMinimumHeight(80)

        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setObjectName("centerSplitter")
        self.splitter.addWidget(self.tabs)
        self.splitter.addWidget(self.timeline)
        self.splitter.setSizes([560, 200])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.splitter)

        self._rounds: list[tuple[int, str]] = []  # 已展示过的 (轮次, 脚本)，按到达顺序
        self._current_round: int | None = None
        self._compare_with_previous = False
        self._compare_html = ""
        self._proposal_html = ""
        self._refresh_compare()

    # ── 模型提议（对话里给出脚本、等待用户接受/拒绝）──────────────────
    def show_proposal(self, script: str) -> tuple[int, int]:
        """把"模型提议的脚本"与**当前脚本**的差异显示在对比页，并切过去。

        返回 (新增行数, 删除行数) —— 对话那边要拿它显示 +N −M。
        这不改任何状态：接受与否由用户在对话面板里点，脚本只有被接受才进中栏。
        """
        current = self.current_text()
        html = render_diff_html(current, script)
        self._proposal_html = html
        self.compare_view.setHtml(html)
        self.tabs.setTabText(_COMPARE_TAB, "模型提议（未接受）")
        self.tabs.setCurrentIndex(_COMPARE_TAB)
        return diff_counts(current, script)

    def proposal_html(self) -> str:
        """当前提议的 diff HTML（空 = 没有提议）。测试与后续样式共用这份契约。"""
        return self._proposal_html

    def clear_proposal(self) -> None:
        """撤掉提议展示：页签标题还原、回到「本轮」，对比页恢复成"对比上一轮"。"""
        self._proposal_html = ""
        self.tabs.setTabText(_COMPARE_TAB, "对比上一轮")
        self._refresh_compare()
        self.tabs.setCurrentIndex(_CURRENT_TAB)

    # ── 整屏重置 ──────────────────────────────────────────────────
    def reset(self) -> None:
        """回到「还没有脚本」的初始态；历史回放换一次运行前必须先调它。

        不清的话，上一次运行的轮次会被当成新运行的「上一轮」，对比页给出的是
        两次不同运行之间的假差异。对比开关属于控制器的状态，这里不动它，免得
        与控制器那边的复选框不同步。
        """
        self._rounds.clear()
        self._current_round = None
        self.script_view.set_text("")
        self.timeline.clear()
        self._refresh_compare()
        self.tabs.setCurrentIndex(_CURRENT_TAB)

    # ── 脚本与对比 ────────────────────────────────────────────────
    def show_round(self, round_no: int, script: str) -> None:
        """显示某轮脚本；同一轮重复到达就覆盖，避免事件重放堆出重复轮次。"""
        for index, (existing, _script) in enumerate(self._rounds):
            if existing == round_no:
                self._rounds[index] = (round_no, script)
                break
        else:
            self._rounds.append((round_no, script))

        self._current_round = round_no
        self.script_view.set_text(script)
        self._refresh_compare()
        if self._compare_with_previous:
            # 用户既然开着对比，新脚本到了就该让他看到「这一轮改了什么」
            self.tabs.setCurrentIndex(_COMPARE_TAB)

    def jump_to_line(self, line_no: int) -> None:
        """把光标跳到某一行（右栏报告里双击一条发现时用；规格 §12）。

        转发给脚本视图，而不是让控制器去摸 `script_view`：视图怎么实现跳转是中栏的内部事。
        """
        self.script_view.jump_to_line(line_no)
        self.tabs.setCurrentIndex(_CURRENT_TAB)

    def current_text(self) -> str:
        """本轮脚本全文（右栏点击跳转、历史回填与界面测试都用它）。"""
        return self.script_view.toPlainText()

    def set_compare_with_previous(self, enabled: bool) -> None:
        """打开/关闭「对比上一轮」：控制器把它接在复选框上，打开即切到对比页。"""
        self._compare_with_previous = enabled
        if enabled:
            self._refresh_compare()
        self.tabs.setCurrentIndex(_COMPARE_TAB if enabled else _CURRENT_TAB)

    def compare_html(self) -> str:
        """对比页当前展示的 HTML 原文。

        返回渲染时的原文而不是 QTextBrowser.toHtml()：Qt 重新序列化会丢掉
        diff-added / diff-removed 这两个 class（已实测），而它们是样式与测试的契约。
        """
        return self._compare_html

    # ── 轮次时间线 ────────────────────────────────────────────────
    def add_timeline_entry(self, round_no: int, phase: str = "", outcome: str = "") -> None:
        """往时间线追加一条「第几轮 · 什么阶段 · 结果」；细节留给右栏。"""
        text = f"第 {round_no} 轮"
        if phase:
            text += f" · {phase}"
        if outcome:
            text += f" — {outcome}"
        item = QListWidgetItem(text)
        item.setToolTip(text)
        self.timeline.addItem(item)
        self.timeline.scrollToBottom()  # 轮次不断追加，最新一条要留在视野里

    # ── 内部：对比页 ──────────────────────────────────────────────
    def _refresh_compare(self) -> None:
        current = None if self._current_round is None else self._script_of(self._current_round)
        previous = self._previous_script()
        if current is None:
            self._compare_html = _placeholder("还没有脚本可对比。")
        elif previous is None:
            self._compare_html = _placeholder(f"第 {self._current_round} 轮是首轮，没有上一轮可对比。")
        else:
            self._compare_html = render_diff_html(previous, current)
        self.compare_view.setHtml(self._compare_html)

    def _script_of(self, round_no: int) -> str | None:
        for existing, script in self._rounds:
            if existing == round_no:
                return script
        return None

    def _previous_script(self) -> str | None:
        """比当前轮更早的最近一轮脚本；首轮（或历史回放）没有就返回 None。

        按「比当前轮小」而不是「列表倒数第二条」来找，这样回看老轮次时对比的
        也确实是它自己的上一轮。
        """
        if self._current_round is None:
            return None
        earlier = [(no, script) for no, script in self._rounds if no < self._current_round]
        return max(earlier, key=lambda pair: pair[0])[1] if earlier else None


def _placeholder(message: str) -> str:
    # 占位文字用主题的三级色：写死的 #8c959f 是浅色底时代的值，深色底上太亮
    return f'<div class="diff-placeholder" style="color:{DIFF_GUTTER_FG}">{message}</div>'
