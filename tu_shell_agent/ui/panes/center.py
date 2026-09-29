"""中栏：本轮脚本（带行号）+ 与上一轮的 diff + 轮次时间线。

纯展示控件：只吃字符串与轮次号，不读文件、不起子进程。RunEvent 到界面的映射
（script → show_round、shellcheck → add_timeline_entry 等）留给 run_controller。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout, QListWidget, QListWidgetItem, QPushButton, QSplitter, QStackedWidget,
    QTabWidget, QTextBrowser, QTextEdit, QVBoxLayout, QWidget,
)

from ...filetext import write_text_lf
from ..markdown import MarkdownBrowser
from ..widgets.diff_view import diff_counts, render_diff_html
from ..widgets.script_view import ScriptView
from ..widgets.selection_menu import install_ask_action, selected_text
from ..theme import DIFF_GUTTER_FG

_CURRENT_TAB = 0
_COMPARE_TAB = 1
_FILE_TAB = 2


class CenterPane(QWidget):
    """脚本正文、与上一轮的差异、以及每一轮发生了什么。"""

    # 「就选中的代码提问」：中栏任何一处选中的内容都可以带进对话问（用户要求"询问代码"）
    ask_about_selection = Signal(str, str)
    # 自动整理的说明（粘贴时换了换行符、格式化了哪些行）：转给状态栏，用户得看得见
    notice = Signal(str)
    # 「文件」页保存成功（携带绝对路径）：主窗口据此刷新方案预览等依赖这份文件的地方
    file_saved = Signal(str)

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
        # 对比页里的片段（含 +/− 前缀）同样可以选中提问：读 diff 时最常见的动作就是
        # "这一行为什么要改" —— 让选中内容直接进对话，比让人手抄一行过去实际。
        install_ask_action(
            self.compare_view,
            lambda text: self.ask_about_selection.emit(text, self._compare_source()),
            label="就选中的差异提问",
            extra_actions=(("回退到上一轮", self.revert_to_previous),),
        )
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

        # ── 「文件」页：左栏文件树里点一个文件，就在这里编辑并保存 ──────────
        # 用户要求（2026-09-20）："这个方案文档我还不能编辑……可以在底下文件夹选择文件，
        # 并在中间这栏脚本这里进行编辑"。复用 ScriptView：行号、shell 高亮、Tab=4 空格、
        # Ctrl+Z/Ctrl+Shift+Z 撤销重做、粘贴自动整理，全都是现成的同一套行为。
        self.file_view = ScriptView()
        self.file_view.setObjectName("fileView")
        # Markdown 文件要有**渲染视图**（用户："虽然说你中间可以编辑文件了，但是 markdown
        # 文件没有渲染"）：编辑页与预览页叠在一起，用右上角那个按钮切换，默认给渲染好的样子。
        self.file_preview = MarkdownBrowser()
        self.file_preview.setObjectName("filePreview")
        self.file_stack = QStackedWidget()
        self.file_stack.setObjectName("fileStack")
        self.file_stack.addWidget(self.file_view)
        self.file_stack.addWidget(self.file_preview)
        self.tabs.addTab(self.file_stack, "文件")

        # 「格式化」放在**页签右上角**：不额外占一行高度（中栏本来就矮），
        # 但它是"手改脚本"这条路上最常用的动作，不该藏进菜单。
        self.format_button = QPushButton("格式化")
        self.format_button.setObjectName("formatScriptButton")
        self.format_button.setToolTip(
            "按结构整理脚本缩进、去掉行尾空白、换行符统一成 LF（Ctrl+Shift+F）。\n"
            "只动行首与行尾空白，不改任何语句；heredoc 正文与跨行字符串原样保留。"
        )
        self.format_button.clicked.connect(lambda _checked=False: self._format_current())
        # 「保存」只对「文件」页有意义：脚本页的内容归运行流程管，不在这里写盘。
        self.save_button = QPushButton("保存")
        self.save_button.setObjectName("saveFileButton")
        self.save_button.setToolTip("把这一页的内容写回文件（Ctrl+S）；换行符统一成 LF。")
        self.save_button.clicked.connect(lambda _checked=False: self.save_file())
        self.save_button.setVisible(False)
        # 「预览」(Markdown 专用)：编辑 ↔ 渲染之间切换
        self.preview_button = QPushButton("预览")
        self.preview_button.setObjectName("previewMarkdownButton")
        self.preview_button.setToolTip(
            "在「编辑」与「渲染后的样子」之间切换（只对 .md / .markdown 文件显示）。\n"
            "预览用的是当前编辑器里的内容（改了没保存也能看）。"
        )
        self.preview_button.clicked.connect(lambda _checked=False: self.toggle_file_preview())
        self.preview_button.setVisible(False)
        corner = QWidget()
        corner_layout = QHBoxLayout(corner)
        corner_layout.setContentsMargins(0, 0, 0, 0)
        corner_layout.setSpacing(4)
        corner_layout.addWidget(self.preview_button)
        corner_layout.addWidget(self.save_button)
        corner_layout.addWidget(self.format_button)
        self.tabs.setCornerWidget(corner, Qt.Corner.TopRightCorner)
        self.tabs.currentChanged.connect(lambda _index: self._sync_corner_buttons())

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

        # 「文件」页的状态：当前路径、是否只读（读的时候就打不开）、页签标签
        self._file_path = ""
        self._file_readonly = False
        self._file_label = "文件"
        self._sync_corner_buttons()

        self._rounds: list[tuple[int, str]] = []  # 已展示过的 (轮次, 脚本)，按到达顺序
        self._current_round: int | None = None
        self._compare_with_previous = False
        self._compare_html = ""
        self._proposal_html = ""
        self._refresh_compare()

        # 脚本页的选中提问直接转出去（来源说明由 ScriptView 自己算，它管着行号）
        self.script_view.ask_about_selection.connect(self.ask_about_selection)
        # 自动整理（粘贴换行符 / 格式化）的结果说明转给状态栏
        self.script_view.notice.connect(self.notice)
        # 文件页同样：选中提问、自动整理说明、以及"改了没保存"的标记
        self.file_view.ask_about_selection.connect(self.ask_about_selection)
        self.file_view.notice.connect(self.notice)
        self.file_view.document().modificationChanged.connect(self._on_file_modified)
        # Ctrl+S 只在这个页里生效（WidgetShortcut）：全局抢 Ctrl+S 会影响别的控件
        self.save_shortcut = QShortcut(QKeySequence.StandardKey.Save, self.file_view)
        self.save_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        self.save_shortcut.activated.connect(self.save_file)

    # ── 选中提问 ──────────────────────────────────────────────────
    def selected_code(self) -> str:
        """当前「本轮」页里选中的代码；没有选中返回空串（测试与外部都走这个口）。"""
        return selected_text(self.script_view)

    def _compare_source(self) -> str:
        """对比页的来源说明：提议的 diff 与"上一轮 diff"要分得清，问的时候含义不同。"""
        title = self.tabs.tabText(_COMPARE_TAB)
        return f"{title} · 选中片段"

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

    # ── 文件页：编辑左栏文件树里选中的文件 ──────────────────────────
    def open_path(self, path: str) -> bool:
        """在中栏「文件」页打开一个文件供编辑；读不了就如实说明并**标为只读**。

        为什么标只读而不是留一个空编辑器：读不了（二进制、非 UTF-8、没权限）时让用户
        以为可以改、改完保存却发现写坏了文件，比直接说"这个打不开"糟得多。
        """
        target = Path(str(path or ""))
        if not str(path or "").strip():
            return False
        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            self._show_uneditable(
                target, "这个文件不是 UTF-8 文本（可能是二进制，或用了 GBK/UTF-16 编码），"
                "不能在这里编辑。"
            )
            return False
        except OSError as error:
            self._show_uneditable(target, f"读不了这个文件：{error}")
            return False
        # set_text 会归一化换行（CRLF→LF）并保持可撤销；读完先清"已修改"标记
        self.file_view.set_text(text)
        self.file_view.document().setModified(False)
        self._file_path = str(target)
        self._file_readonly = False
        self._set_file_tab_label(target.name)
        self.tabs.setCurrentIndex(_FILE_TAB)
        # Markdown 直接给渲染后的样子（可一键切回编辑）；其它文件就是编辑器
        if self.file_is_markdown():
            self.file_stack.setCurrentIndex(1)
            self.file_preview.set_markdown(text)
            self.preview_button.setText("编辑")
            self.preview_button.setVisible(True)
        else:
            self.file_stack.setCurrentIndex(0)
            self.preview_button.setVisible(False)
        self._sync_corner_buttons()
        hint = "改完按 Ctrl+S 保存" if not self.file_is_markdown() else "点右上角「编辑」可改，Ctrl+S 保存"
        self.notice.emit(f"已在中栏打开 {target.name}（{hint}）")
        return True

    def save_file(self) -> bool:
        """把「文件」页的内容写回磁盘（LF 结尾）；成功与否都发一句说明。"""
        if not self._file_path:
            self.notice.emit("还没有打开任何文件：先在左栏的文件夹里点一个文件。")
            return False
        if self._file_readonly:
            self.notice.emit("这个文件读的时候就没打开成功，不能保存（先修好编码或权限）。")
            return False
        target = Path(self._file_path)
        try:
            write_text_lf(target, self.file_view.toPlainText())
        except OSError as error:
            self.notice.emit(f"保存失败（{target}）：{error}")
            return False
        self.file_view.document().setModified(False)
        self._set_file_tab_label(target.name)
        if self.is_previewing_markdown():
            self.file_preview.set_markdown(self.file_view.toPlainText())
        self._sync_corner_buttons()
        self.notice.emit(f"已保存 {target.name}（{target}）")
        self.file_saved.emit(str(target))
        return True

    def file_path(self) -> str:
        """「文件」页当前编辑的文件（没打开则为空串）。"""
        return self._file_path

    def file_is_dirty(self) -> bool:
        """有没有未保存的改动（标签上的圆点与它同源）。"""
        return bool(self.file_view.document().isModified())

    def file_is_markdown(self) -> bool:
        """当前打开的是不是 Markdown（决定要不要给"预览"按钮）。"""
        return Path(self._file_path).suffix.lower() in (".md", ".markdown")

    def toggle_file_preview(self) -> bool:
        """在"编辑"与"渲染"之间切换；返回切换后是否处于**渲染**状态。

        渲染用的是**编辑器里的当前内容**（改了没保存也能看），而不是磁盘上的旧版本 ——
        用户要的就是"边改边看效果"。
        """
        if not self.file_is_markdown():
            return False
        to_preview = self.file_stack.currentIndex() == 0
        if to_preview:
            self.file_preview.set_markdown(self.file_view.toPlainText())
        self.file_stack.setCurrentIndex(1 if to_preview else 0)
        self.preview_button.setText("编辑" if to_preview else "预览")
        self.save_button.setVisible(not to_preview)     # 预览页不给保存按钮（那里不能改）
        return to_preview

    def is_previewing_markdown(self) -> bool:
        """文件页当前是不是停在渲染视图上。"""
        return self.file_stack.currentIndex() == 1

    def _show_uneditable(self, target: Path, reason: str) -> None:
        """把"为什么不能编辑"写在文件页里：用户点开就该看到原因，而不是空白。"""
        self.file_view.set_text(f"（{target.name} 打不开）\n\n{reason}\n\n路径：{target}\n")
        self.file_view.setReadOnly(True)
        self.file_view.document().setModified(False)
        self._file_path = str(target)
        self._file_readonly = True
        self.file_stack.setCurrentIndex(0)
        self.preview_button.setVisible(False)     # 打不开的文件谈不上预览
        self._set_file_tab_label(target.name)
        self.tabs.setCurrentIndex(_FILE_TAB)
        self._sync_corner_buttons()
        self.notice.emit(f"{target.name} 打不开：{reason}")

    def _set_file_tab_label(self, name: str) -> None:
        """页签上写文件名，有未保存改动时加一个圆点（用户一眼看得出保存没保存）。"""
        dirty = " •" if self.file_is_dirty() else ""
        self._file_label = f"{name}{dirty}" if name else "文件"
        self.tabs.setTabText(_FILE_TAB, self._file_label)

    def _sync_corner_buttons(self) -> None:
        """页签右上角的按钮跟着当前页走：保存只在文件页、格式化作用于"你正在看的那一篇"。"""
        on_file = self.tabs.currentIndex() == _FILE_TAB
        previewing = on_file and self.is_previewing_markdown()
        self.save_button.setVisible(on_file and not previewing)
        self.preview_button.setVisible(on_file and self.file_is_markdown() and not self._file_readonly)
        if on_file:
            self.save_button.setEnabled(not self._file_readonly)
            self.save_button.setText("保存 •" if self.file_is_dirty() else "保存")

    def _format_current(self) -> None:
        """格式化当前这一页（脚本页改脚本、文件页改文件）——按钮的含义跟着眼睛走。"""
        view = self.file_view if self.tabs.currentIndex() == _FILE_TAB else self.script_view
        view.format_now()

    def _on_file_modified(self, _modified: bool) -> None:
        """内容一改就更新页签标记与保存按钮（不弹窗、不拦截，只是如实显示状态）。"""
        if self._file_path and not self._file_readonly:
            self._set_file_tab_label(Path(self._file_path).name)
        self._sync_corner_buttons()

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

    def revert_to_previous(self) -> bool:
        """把中栏的脚本**退回上一轮那一版**（差异页右键菜单里的那一项）。

        为什么要有它：模型这一轮改坏了，用户想"就要上一轮那版" —— 以前只能自己抄回去。
        退回的是**文本**（进编辑框、Ctrl+Z 也能撤销这次回退），要执行仍然走「改后重跑」
        （shellcheck → 人工确认 → 执行），一道闸门都没少。
        """
        previous = self._previous_script()
        if previous is None:
            self.notice.emit("没有上一轮可回退（这是首轮，或还没有对比对象）。")
            return False
        earlier = [no for no, _script in self._rounds if no < (self._current_round or 0)]
        source_round = max(earlier) if earlier else None
        self.script_view.set_text(previous)          # 可撤销：Ctrl+Z 能退回回退之前
        if self._current_round is not None:
            # 当前轮的内容换成了上一轮那一版，对比页随之变成"与上一轮一致"
            for index, (existing, _script) in enumerate(self._rounds):
                if existing == self._current_round:
                    self._rounds[index] = (self._current_round, previous)
                    break
        self._refresh_compare()
        where = f"第 {source_round} 轮" if source_round is not None else "上一轮"
        self.notice.emit(f"已把中栏脚本回退到{where}那一版（要执行请点「改后重跑」；Ctrl+Z 可撤销）")
        return True

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
