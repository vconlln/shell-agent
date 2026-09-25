"""右栏：shellcheck 报告（按 SC 编号分组）+ 执行输出 + 模型取舍说明。

这一栏是「人核对方案约束是否被落实」的唯一落点（规格 §11）：`succeeded` 只说明
shellcheck 没有阻断项且退出码为 0，模型完全可能靠**放宽**方案里的约束来跑通
（实测三次运行三次都这么过），而它把这些取舍写在脚本正文之外的 notes / assumptions 里。
所以三件事必须同屏可见，报告也要说清「注入的阻断级别是哪一级、哪几条会阻断」——
否则一次"通过"很容易被读成"方案被正确实现了"。

本控件只做展示：不读盘、不起进程、不判定成败，内容全部由 RunController 喂进来。
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QLabel, QPlainTextEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ...types import SEVERITY_RANK, ExecuteResult, Severity, ShellcheckFinding, blocks_run
from ..markdown import MarkdownBrowser
from ..widgets.collapsible import CollapsibleSection, plan_collapse
from ..theme import BLOCKING_COLOR, NON_BLOCKING_COLOR, STDERR_COLOR

# 级别由重到轻：与 types.SEVERITY_RANK、左栏阻断级别下拉框同源
_LEVELS_BY_WEIGHT: tuple[Severity, ...] = ("error", "warning", "info", "style")


def blocking_rule(level: Severity) -> str:
    """把"当前级别意味着什么"如实算出来。

    不能写成一句常量：级别是可配的，常量在非默认级别下会与同一屏的树自相矛盾
    （例如级别设成 error 时它仍宣称 info 会阻断，而旁边正把某条 info 标成"仅展示"）。
    规则与引擎同源：严重度 ≥ 当前级别的发现会阻断并回灌修复（types.blocks_run）。
    """
    order = [item for item in _LEVELS_BY_WEIGHT if SEVERITY_RANK[item] >= SEVERITY_RANK[level]]
    blocking_text = "/".join(order) or "（无）"
    rest = [item for item in _LEVELS_BY_WEIGHT if item not in order]
    tail = f"{'/'.join(rest)} 只展示" if rest else "其余级别不存在"
    return f"严重度 ≥ {level} 的（{blocking_text}）会阻断并回灌修复，{tail}"

# stderr 是"出问题了"的那条流，跟 stdout 混在一起时人得逐行找，用颜色分开。
# 颜色从主题取（theme.STDERR_COLOR 等）：写死会让深色主题下出现浅色底才配的红。
_STDERR_COLOR = STDERR_COLOR
_BLOCKING_COLOR = BLOCKING_COLOR
_NON_BLOCKING_COLOR = NON_BLOCKING_COLOR

def _section(text: str) -> QLabel:
    """分组小标题（样式由 QSS 按 role=section 统一）。"""
    label = QLabel(text)
    label.setProperty("role", "section")
    return label


_NOTES_TITLE = "模型取舍说明与假设（与中栏脚本正文并排核对）"
_NOTES_WARNING = (
    "succeeded 只表示「shellcheck 无阻断项 + 退出码 0」，不代表方案里的约束被实现："
    "模型为跑通而放宽的约束只写在这里，必须逐条对照方案原文。"
)

_UNSET_NOTES = "（模型未给出取舍说明；请直接对照方案原文核对脚本正文）"
_UNSET_ASSUMPTIONS = "（模型未声明任何假设）"


class RightPane(QWidget):
    """shellcheck 报告分组 + 执行输出 + 模型取舍说明。"""

    sections_auto_collapsed = Signal(tuple)   # 因空间不足被自动收起的 key 集合（测试/提示用）

    finding_activated = Signal(int)     # 发现所在行号，中栏据此跳转

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("rightPane")   # main_window 与骨架测试依赖的名字，不要改
        self._findings: tuple[ShellcheckFinding, ...] = ()
        # "校验过、这轮没发现"与"还没校验过"必须分开说：前者是结论，后者只是没数据
        self._has_report: bool = False
        # 与 RunConfig 默认一致（实测 SC2086 是 info 级）；接线方在运行参数变化时覆盖
        self._blocking_level: Severity = "info"

        self.findings_header = _section("校验报告（按 SC 编号分组；双击条目跳到中栏对应行）")
        self.findings_header.setWordWrap(True)

        self.findings_summary = QLabel()
        self.findings_summary.setObjectName("findingsSummary")
        self.findings_summary.setWordWrap(True)

        self.findings_tree = QTreeWidget()
        self.findings_tree.setObjectName("findingsTree")
        self.findings_tree.setColumnCount(2)
        self.findings_tree.setHeaderLabels(["SC 编号 / 说明", "数量 / 位置"])
        # 激活（双击、回车）才跳转：单击就跳会让人在展开分组时被反复拽走
        self.findings_tree.itemActivated.connect(self._on_item_activated)

        self.execute_summary = QLabel()
        self.execute_summary.setObjectName("executeSummary")
        self.execute_summary.setWordWrap(True)

        self.output_view = QPlainTextEdit()
        self.output_view.setObjectName("outputView")
        self.output_view.setReadOnly(True)
        # 日志按原样换行：自动折行会让人分不清是脚本换的行还是界面折的行
        self.output_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

        self.notes_header = QLabel(f"{_NOTES_TITLE}\n{_NOTES_WARNING}")
        self.notes_header.setObjectName("notesHeader")
        self.notes_header.setWordWrap(True)

        # 报告视图也渲染 Markdown：模型写的取舍说明与假设本来就常带列表与行内代码，
        # 当纯文本显示时 `**加粗**`、`- 列表` 会原样露出来（用户："几乎所有的对话框都没有
        # markdown 渲染"）。
        self.notes_view = MarkdownBrowser()
        self.notes_view.setObjectName("notesView")

        # 三块各自可折叠（默认展开）：窗口矮的时候让用户自己决定先看哪块，而不是让布局
        # 把三块一起压扁。内部控件仍挂在对象树上（折叠只是 setVisible(False)），
        # 所以 findChild 与既有代码、测试都不受影响。
        findings_body = self._wrap(
            [self.findings_header, self.findings_summary, self.findings_tree], stretch=True
        )
        output_body = self._wrap(
            [_section("stdout / stderr（stderr 标红）"), self.execute_summary, self.output_view],
            stretch=True,
        )
        notes_body = self._wrap([self.notes_header, self.notes_view], stretch=True)
        # 三块各自的最小高度：折叠能解决"没空间"，但**展开着**的时候不能让布局把某一块压成一条缝
        # （实测过：窗口高 560px 时报告树与输出区各剩 35px，等于看不见）。
        self.findings_tree.setMinimumHeight(52)
        self.output_view.setMinimumHeight(52)
        self.notes_view.setMinimumHeight(40)
        self.sections = {
            "findings": CollapsibleSection("校验报告", findings_body),
            "output": CollapsibleSection("执行输出", output_body),
            "notes": CollapsibleSection("模型取舍说明与假设", notes_body),
        }

        layout = QVBoxLayout(self)
        for key, stretch in (("findings", 3), ("output", 3), ("notes", 2)):
            layout.addWidget(self.sections[key], stretch)

        self._refresh_findings()
        self.execute_summary.setText("执行结果：尚未执行")
        self.notes_view.set_plain(f"{_UNSET_NOTES}\n\n假设（脚本成立的前提）：\n{_UNSET_ASSUMPTIONS}")

    @staticmethod
    def _wrap(widgets: Sequence[QWidget], *, stretch: bool) -> QWidget:
        """把若干控件装进一个容器（折叠区块的内容体），返回容器。"""
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        for index, widget in enumerate(widgets):
            layout.addWidget(widget, 1 if (stretch and index == len(widgets) - 1) else 0)
        return body

    # ---- 折叠：按可用高度自动判定 ---------------------------------------
    # 优先级从高到低：运行中盯着的是"有没有问题、跑成什么样"，取舍说明是事后核对用的。
    _SECTION_ORDER: tuple[tuple[str, int], ...] = (
        ("findings", 150),   # 校验报告：永不自动收起
        ("output", 150),     # 执行输出
        ("notes", 110),      # 模型取舍说明与假设
    )

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self.apply_auto_collapse()

    def apply_auto_collapse(self) -> set[str]:
        """按当前高度收起/展开三块，返回被收起的 key 集合。

        为什么要自动（用户裁定，2026-09-19）：折叠不该要用户手动点 —— 空间够就全展开，
        不够才收，收回来的高度给更需要看的那块。之前那套"默认展开 + 点击切换 + 状态写进
        设置"已删除：用户明确说这里不需要手动折叠。
        """
        should_collapse = plan_collapse(
            max(self.height(), 0), list(self._SECTION_ORDER), keep_expanded="findings"
        )
        for key, section in self.sections.items():
            section.set_collapsed(key in should_collapse)
        if should_collapse != getattr(self, "_auto_collapsed", None):
            self._auto_collapsed = set(should_collapse)
            self.sections_auto_collapsed.emit(tuple(sorted(should_collapse)))
        return should_collapse

    # ---- 阻断级别 -------------------------------------------------------------

    @property
    def blocking_level(self) -> Severity:
        """本次运行注入的阻断级别。判定由引擎做，这里只用它标注哪几条会阻断。"""
        return self._blocking_level

    @blocking_level.setter
    def blocking_level(self, level: Severity) -> None:
        """换级别后必须重画报告：同一份发现，在不同级别下"会不会阻断"是不同结论。"""
        if level == self._blocking_level:
            return
        self._blocking_level = level
        self._refresh_findings()

    # ---- 校验报告 -------------------------------------------------------------

    def reset(self) -> None:
        """回到"还没跑过校验"的初始态；历史回放换一次运行前必须先调它。

        为什么要单独一个方法：`render_findings(())` 表达的是"校验过了、零发现"，
        而回放一个**没有留下报告**的运行（第 1 轮契约失败就退出了）根本不是这个意思 ——
        那会把"没有数据"显示成"检查过、没问题"。reset 之后 `_has_report` 回到 False，
        摘要行如实写"报告：尚未校验"。
        """
        self._findings = ()
        self._has_report = False
        self._refresh_findings()
        # 执行结论与 notes 也要清：只清报告的话，新一次运行（或回放另一条运行）开始后，
        # 屏幕上会留着上一次的退出码、stdout 与模型自述 —— 那是最容易被读成
        # "这次也成功了"的一种假象。
        self.execute_summary.setText("执行结果：尚未执行")
        self.output_view.clear()
        self.render_notes("", ())

    def render_findings(self, findings: Sequence[ShellcheckFinding]) -> None:
        """重画报告：同一 SC 编号聚成一组，组内按行列排序。

        顶层组的先后顺序 = 引擎给出报告的顺序（同编号首现即定型），不按级别重排：
        重排会让同一脚本的两次报告行序不一致，人对不上号；级别由组标题、组内第二列
        和摘要行的分级计数体现。
        """
        self._findings = tuple(findings)
        self._has_report = True
        self._refresh_findings()

    def _refresh_findings(self) -> None:
        """按当前发现与阻断级别重画整块报告（摘要行 + 树），两条入口共用这一条重画路径。"""
        self.findings_tree.clear()
        if not self._findings:
            # 还没跑过校验 → 说还没校验；跑过而没发现 → 说 0 处。
            # 两者都不能只留一片空白：空白会被读成"检查过了、没问题"。
            head = "报告：0 处（本轮没有发现）" if self._has_report else "报告：尚未校验"
            self.findings_summary.setText(
                f"{head}｜阻断级别 {self._blocking_level}（{blocking_rule(self._blocking_level)}）"
            )
            return

        groups: dict[str, list[ShellcheckFinding]] = {}
        for finding in self._findings:
            groups.setdefault(finding.code, []).append(finding)

        for code, members in groups.items():
            blocking = any(blocks_run(item.level, self._blocking_level) for item in members)
            group = QTreeWidgetItem([
                f"{code}（{self._levels_text(members)}）· {'阻断' if blocking else '仅展示'}",
                f"{len(members)} 处",
            ])
            group.setForeground(0, QBrush(QColor(_BLOCKING_COLOR if blocking else _NON_BLOCKING_COLOR)))
            group.setToolTip(0, (
                f"{code}：{len(members)} 处，{self._levels_text(members)} 级；"
                + (f"达到阻断级别 {self._blocking_level}，会触发回灌修复"
                   if blocking else f"低于阻断级别 {self._blocking_level}，只展示不触发修复")
            ))
            for finding in sorted(members, key=lambda item: (item.line, item.column)):
                child = QTreeWidgetItem([
                    f"第 {finding.line} 行:  {finding.message}",
                    f"列 {finding.column} · {finding.level}",
                ])
                # 行号随条目存下来：跳转只需要行号，不必让槽回头看发现列表的次序
                child.setData(0, Qt.ItemDataRole.UserRole, finding.line)
                child.setToolTip(0, (
                    f"{finding.code}（{finding.level}）第 {finding.line} 行第 {finding.column} 列："
                    f"{finding.message}"
                ))
                group.addChild(child)
            self.findings_tree.addTopLevelItem(group)

        self.findings_tree.expandAll()
        counts = {level: 0 for level in _LEVELS_BY_WEIGHT}
        for finding in self._findings:
            if finding.level in counts:
                counts[finding.level] += 1
        by_level = " · ".join(f"{level} {counts[level]}" for level in _LEVELS_BY_WEIGHT)
        self.findings_summary.setText(
            f"报告：{len(self._findings)} 处｜阻断级别 {self._blocking_level}（{blocking_rule(self._blocking_level)}）"
            f"｜按级别：{by_level}"
        )

    @staticmethod
    def _levels_text(members: Sequence[ShellcheckFinding]) -> str:
        """组标题里的级别。同一编号理论上只对应一个级别，仍按实际出现过的级别如实列出。"""
        levels = sorted({item.level for item in members}, key=lambda level: -SEVERITY_RANK[level])
        return "/".join(levels)

    def _on_item_activated(self, item: QTreeWidgetItem, _column: int) -> None:
        """条目被激活 → 把行号广播出去。分组行不带行号，静默忽略。"""
        line = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(line, int):
            self.finding_activated.emit(line)

    # ---- 执行输出 -------------------------------------------------------------

    def render_execute(self, result: ExecuteResult) -> None:
        """重画执行输出与摘要行。覆盖上一轮：右栏只显示最近一轮，历史留在运行目录里。"""
        self._set_output(result.stdout, result.stderr)
        self.execute_summary.setText(self._execute_summary_text(result))

    def _execute_summary_text(self, result: ExecuteResult) -> str:
        """摘要行必须能区分四态：正常退出 / 非零退出 / 超时 / 已取消（后两者含"超时""已取消"字样）。"""
        states: list[str] = []
        if result.cancelled:
            states.append("已取消")
        if result.timed_out:
            states.append("超时")
        if not states:
            if result.exit_code is None:
                states.append("结束状态未知")
            else:
                states.append("正常退出" if result.exit_code == 0 else "非零退出")

        exit_code = "—" if result.exit_code is None else str(result.exit_code)
        parts = [f"执行结果：{'、'.join(states)}", f"退出码 {exit_code}"]
        if result.signal is not None:
            parts.append(f"信号 {result.signal}")
        parts.append(f"耗时 {result.duration_ms} ms")
        if result.exit_code == 0 and states[0] not in ("超时", "已取消"):
            # 退出码 0 最容易被当成功劳簿：这里就地把它说清楚，别让人跨栏去找那句话
            parts.append("退出码 0 只说明脚本正常结束，不代表方案里的约束被满足")
        if "\ufffd" in result.stdout or "\ufffd" in result.stderr:
            # 引擎按 errors="replace" 解码（execute.py），非法字节会变成替换字符；
            # ExecuteResult 没有对应字段，替换字符是界面上唯一能看见的信号
            parts.append("输出含替换字符（非法字节已按 UTF-8 有损解码，未必是脚本的真实输出）")
        return " · ".join(parts)

    def _set_output(self, stdout: str, stderr: str) -> None:
        """两条流写进同一个只读视图：stderr 染色，文本本身一字不改、也不截断。

        不加"stdout:"之类的前缀或分隔行：输出区必须原样等于脚本真正打印的内容，
        否则人复制出去复盘时拿到的就不是脚本的输出了。分色只靠格式，不靠插入字符。
        """
        self.output_view.clear()
        cursor = self.output_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        # stdout 不以换行结尾时（printf / echo -n）先补一个换行：否则 stderr 的首行会接到
        # stdout 最后一行的行尾，拼出一条**两条流都没打印过**的行，复制出去复盘就失真了。
        # 补的是流之间的边界，不是给输出加内容。
        if stdout and stderr and not stdout.endswith("\n"):
            stdout = stdout + "\n"
        for text, color in ((stdout, None), (stderr, _STDERR_COLOR)):
            if not text:
                continue
            char_format = QTextCharFormat()
            if color is not None:
                char_format.setForeground(QColor(color))
            cursor.insertText(text, char_format)
        self.output_view.moveCursor(QTextCursor.MoveOperation.Start)

    # ---- 模型取舍说明 ---------------------------------------------------------

    def render_notes(self, notes: str, assumptions: Sequence[str]) -> None:
        """摊开模型自述的取舍与假设（规格 §11）。

        缺内容时写明确的一句话而不是留白：空白会被读成"没有取舍"，而更常见的情况是
        "模型没交代"——前者可以放过，后者必须追问，两者的处理方式完全不同。
        """
        # 拼成 Markdown：小标题 + 模型原文（它自己就是 Markdown）+ 假设列表
        parts = [
            "### 取舍说明",
            "模型自述：为跑通而放宽或改动了方案里的哪些约束。",
            "",
            notes.strip() or _UNSET_NOTES,
            "",
            "### 假设",
            "脚本成立的前提：",
            "",
        ]
        if assumptions:
            parts.extend(f"- {item}" for item in assumptions)
        else:
            parts.append(f"- {_UNSET_ASSUMPTIONS}")
        self.notes_view.set_markdown("\n".join(parts))
