"""左栏：方案选择与预览 + 方案文件夹文件树。

**运行参数不在这一栏**（用户 2026-09-20）：运行根目录、阻断级别、轮次上限、生成/执行
超时与设置页的「运行」一节完全重复，留着等于同一件事有两处能改。现在只在设置页改，
这一栏空出来的位置放文件树（选文件夹 → 看里面的文件 → 点一个当方案）。

只收集输入并做廉价校验，**不发起运行**（运行由任务 9 的 RunController 负责），
因此这里不碰网络、子进程，也不做建目录之类的磁盘写操作：写盘的失败信息
在引擎侧才有上下文（例如「运行根是文件」「无权限」），在界面里抢先试一遍
只会让同一件事有两处说法，且会在用户敲路径的过程中反复触发。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from ..markdown import MarkdownBrowser
from ..widgets.plan_tree import PlanTree
from ..widgets.scroll import form_container, scrollable
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QVBoxLayout, QWidget,
)

from ...types import Severity

# 与 types.SEVERITY_RANK 同源的四个级别；顺序即下拉框顺序（由重到轻）
BLOCKING_LEVELS: tuple[Severity, ...] = ("error", "warning", "info", "style")

# 未读到方案正文时预览区的开场白：区别于「方案文件空」
_PREVIEW_IDLE = "（尚未选择方案文档）"


def _section(text: str) -> QLabel:
    """分组小标题：QSS 按 role 属性统一成"小号大写次级色"，语义写在属性上而不是各写样式。"""
    label = QLabel(text)
    label.setProperty("role", "section")
    return label


class LeftPane(QWidget):
    """方案 + 本次运行参数的输入区。"""

    plan_changed = Signal(str)
    # 文件树的根目录换了（由主窗口记进设置，下次打开还在原处）
    plan_tree_root_changed = Signal(str)          # 选择方案后发出（绝对路径）

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("leftPane")   # main_window 与骨架测试依赖的名字，不要改
        self._plan_path: str = ""        # 空串 = 未选方案
        self._plan_text: str = ""        # 方案正文；读失败时为空（错误说明只在预览里显示）
        self._plan_error: str = ""       # 非空 = 上次读取失败，validate() 要如实报出来

        self.plan_edit = QLineEdit()
        self.plan_edit.setObjectName("planEdit")
        self.plan_edit.setReadOnly(True)          # 路径只能由文件对话框或 set_plan 写入
        self.plan_edit.setPlaceholderText("选择一个方案文档（.md / .txt）")
        browse_button = QPushButton("选择方案…")
        browse_button.clicked.connect(self._browse_plan)

        plan_row = QHBoxLayout()
        plan_row.addWidget(self.plan_edit, 1)
        plan_row.addWidget(browse_button)

        # 方案预览渲染 Markdown（用户："选择方案后展示的这个框没有渲染"）：
        # 方案文档本身就是 Markdown，之前当纯文本显示 —— 标题、列表、代码块全成了平铺的字。
        # `MarkdownBrowser` 是 QTextBrowser：一样能选中复制、能滚、能被 `install_ask_action`
        # 接管右键菜单（"就选中的内容提问"那条路不受影响）。
        self.plan_preview = MarkdownBrowser()
        self.plan_preview.setObjectName("planPreview")
        # 最小高度：没有它的时候，窗口一缩小 QVBoxLayout 会把这个预览压到 12px（实测），
        # 方案正文等于看不见 —— 用户报的"挤压到看不见"就是这个。
        self.plan_preview.setMinimumHeight(90)
        self.plan_preview.set_plain(_PREVIEW_IDLE)

        # 表单进滚动区：左栏内容需要 576px，靠"压缩控件"适应高度会把输入框压到 13px
        # （见 widgets/scroll.py 的说明）。现在按需要出滚动条，控件保持正常高度。
        content, layout = form_container()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scrollable(content))
        layout.addWidget(_section("方案文档"))
        layout.addLayout(plan_row)
        layout.addWidget(self.plan_preview, 1)
        layout.addWidget(_section("补充要求（本次运行临时追加）"))
        self.extra_edit = QPlainTextEdit()
        self.extra_edit.setObjectName("extraInstructionEdit")
        self.extra_edit.setPlaceholderText(
            "要额外叮嘱的话，例如：别动 logs/ 目录；先备份再删除。\n"
            "会作为独立一段进提示词，与方案冲突时以这里为准；留空则一个字都不加。"
        )
        self.extra_edit.setFixedHeight(64)
        layout.addWidget(self.extra_edit)

        # 运行参数（运行根目录 / 阻断级别 / 轮次上限 / 生成与执行超时）**只在设置页**改：
        # 这里原来是同样的一份表单，两处能改同一件事，用户要求把这块换掉
        # （"其实设置已经有了，所以这里没有必要出现"）。
        layout.addWidget(_section("文件夹（点选方案文档）"))
        self.plan_tree = PlanTree()
        self.plan_tree.plan_chosen.connect(self.set_plan)
        self.plan_tree.root_changed.connect(self.plan_tree_root_changed)
        layout.addWidget(self.plan_tree)

    # ---- 方案 -----------------------------------------------------------------

    def set_plan(self, path: str) -> None:
        """选定方案：读文件填预览；读不了就在预览里如实写错误，路径照样记下。"""
        self._plan_path = str(path)
        self.plan_edit.setText(self._plan_path)
        self.plan_tree.set_plan_path(self._plan_path)
        if self._plan_path:
            self.plan_tree.reveal(self._plan_path)
        if not self._plan_path:
            self._plan_error = ""
            self._plan_text = ""
            self.plan_preview.set_plain(_PREVIEW_IDLE)
            self.plan_changed.emit("")
            return
        self._reload_preview()
        self.plan_changed.emit(self._plan_path)

    def _browse_plan(self) -> None:
        """文件对话框选方案。用静态方法而不是实例方法，避免为了一个弹窗持有 QFileDialog。"""
        chosen, _ = QFileDialog.getOpenFileName(
            self, "选择方案文档", self._plan_path or str(Path.home()), "方案文档 (*.md *.txt);;全部文件 (*)",
        )
        if chosen:                       # 取消时返回空串，不能把已选方案清掉
            self.set_plan(chosen)

    def _reload_preview(self) -> None:
        """把方案正文读进预览，或把失败原因写进预览。"""
        try:
            text = Path(self._plan_path).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # 编码问题与"读不到"是两回事：Windows 记事本另存为 ANSI(GBK)/Unicode(UTF-16)
            # 都会落到这里，笼统说"读不到"会让用户去查权限，而真正要做的是另存为 UTF-8。
            self._plan_error = (
                f"方案文档不是 UTF-8 编码（{Path(self._plan_path).name}）："
                "请用编辑器另存为 UTF-8 后重试"
            )
            self._plan_text = ""
            self.plan_preview.set_plain(self._plan_error)
            return
        except OSError as error:
            # 方案是给人看的，读不了就是读不了，先说清原因再谈运行
            self._plan_error = f"读不到方案文档：{error}"
            self._plan_text = ""
            self.plan_preview.set_plain(self._plan_error)
            return
        self._plan_error = ""
        self._plan_text = text
        self.plan_preview.set_markdown(text)      # 方案是 Markdown，按 Markdown 显示

    def plan_path(self) -> str:
        """已选方案的路径；空串表示未选。"""
        return self._plan_path

    def plan_text(self) -> str:
        """方案正文；未选或读失败时为空串（预览区里显示的提示语不算正文）。"""
        return self._plan_text

    def extra_instruction(self) -> str:
        """用户临时追加的要求（可为空）。空串时提示词里不会多出一个空段落。"""
        return self.extra_edit.toPlainText()

    # ---- 文件树 ---------------------------------------------------------------

    def set_plan_tree_root(self, path: str) -> bool:
        """换文件树的根目录（主窗口从设置里读出来交给它）。"""
        return self.plan_tree.set_root(path)

    def plan_tree_root(self) -> str:
        return self.plan_tree.root()

    def validate(self) -> list[str]:
        """返回全部问题；文案带上「方案」二字，用户才能一眼定位到是哪一格。

        运行根目录**不在这里查**：它归设置页管，运行前由引擎在建目录时给出结论。

        只查存在性、可读性与「是不是文件」这类廉价事实：真正的可写性要等引擎建运行目录时
        才有结论，界面里先试一遍会在用户敲路径的过程中反复误报。
        """
        problems: list[str] = []
        path = self._plan_path
        if not path:
            problems.append("未选择方案文档")
        elif self._plan_error:
            # 原样带上读到失败时的原因（编码/权限/文件没了），不要在这里改写成笼统的
            # "读不到" —— 那会把"请另存为 UTF-8"这种可操作的话丢掉。
            problems.append(self._plan_error)
        elif not Path(path).is_file():
            problems.append(f"方案文档读不到：{path}")
        elif not self._plan_text.strip():
            # 空方案（0 字节/纯空白）跑起来是"照着一个空方案生成脚本"：引擎对方案不做检查，
            # 它会把空正文写进 run_dir/plan.md，最后还可能落成 succeeded —— 一次没有方案的
            # 运行被记成成功。运行前就拦在这里。
            problems.append(f"方案文档是空的：{path}")

        return problems
