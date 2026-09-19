"""执行前的确认对话框：把脚本全文摊开，并把危险模式标出来（规格 §9、§12）。

安全模型是"引擎独占执行、用户逐次确认"（规格 §9）：非 trusted 模板每次执行前都要人点一下，
所以这个对话框是最后一道人工闸门。它只做两件事 —— 把要跑的脚本**完整**展示（不截断、
不折叠），以及把明显危险的模式标出来。它不代替用户判断，只保证用户是在知情的前提下点。

`dangerous_matches()` 是纯函数，单测不必弹窗；模式表故意写得保守（宁可多报不可漏报），
因为漏报的代价是"用户以为没事"。
"""

from __future__ import annotations

import re

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

# (正则, 人话说明)。顺序即展示顺序；只做模式匹配，不做语义分析。
DANGEROUS_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\brm\s+(-\S+\s+)*-\S*[rR]\S*f\b|\brm\s+-\S*f\S*[rR]\b", "rm 递归强制删除"),
    (r"\bsudo\b|\bsu\s+-", "以 root 权限执行"),
    (r"\bmkfs(\.\w+)?\b", "格式化文件系统"),
    (r"\bdd\b[^\n]*\bof=/dev/", "dd 直接写块设备"),
    (r">\s*/dev/(sd|hd|nvme|vd)", "重定向覆盖块设备"),
    (r"\bchmod\s+(-\S+\s+)*777\s+/", "对根目录设 777"),
    (r"\bchown\s+(-\S+\s+)*-R\s+\S+\s+/", "递归修改根目录属主"),
    (r"\b(curl|wget)\b[^\n|]*\|\s*(sudo\s+)?(ba|z)?sh\b", "从网络直接管道执行"),
    (r":\(\)\s*\{.*\}\s*;?\s*:", "fork 炸弹"),
)


def dangerous_matches(script: str) -> list[str]:
    """返回脚本里命中的危险模式说明（去重、保持模式表顺序）。"""
    hits: list[str] = []
    for pattern, description in DANGEROUS_PATTERNS:
        if description in hits:
            continue
        if re.search(pattern, script):
            hits.append(description)
    return hits


class ConfirmDialog(QDialog):
    """展示待执行脚本并要一个"执行 / 跳过"的回答。

    调用方（RunController）只在没有 trusted 模板时用它；测试用替身绕过真实弹窗，
    所以这里的正确性由 `dangerous_matches` 的单测 + 控件装配的构造测试共同保证。
    """

    def __init__(
        self,
        round_no: int,
        script_path: str,
        script: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("confirmDialog")
        self.setWindowTitle(f"第 {round_no} 轮：确认执行")
        self.resize(820, 620)

        self.script_view = QPlainTextEdit()
        self.script_view.setObjectName("confirmScriptView")
        self.script_view.setReadOnly(True)
        self.script_view.setPlainText(script)

        hits = dangerous_matches(script)
        self.warning = QLabel()
        self.warning.setObjectName("confirmWarning")
        self.warning.setWordWrap(True)
        if hits:
            self.warning.setText("⚠ 检测到危险模式：" + "、".join(hits))
        else:
            self.warning.setText("未检测到已知危险模式（这不等于脚本安全，仍需自行判断）")

        buttons = QDialogButtonBox()
        self.execute_button = buttons.addButton("执行", QDialogButtonBox.ButtonRole.AcceptRole)
        self.execute_button.setObjectName("confirmExecuteButton")
        self.skip_button = buttons.addButton("跳过", QDialogButtonBox.ButtonRole.RejectRole)
        self.skip_button.setObjectName("confirmSkipButton")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"脚本路径：{script_path}"))
        layout.addWidget(self.warning)
        layout.addWidget(self.script_view, 1)
        layout.addWidget(buttons)

        # 子控件都建好之后再套用背景模式：对话框是**独立顶层窗口**，不这么做的话，
        # 开了半透明之后弹出来的确认框仍是一整块纯不透明的深色
        # （用户反馈的"对话框之类的还是纯黑底"）。
        from .. import backdrop as backdrop_module

        backdrop_module.apply_to(self)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """半透明窗口：先把重绘区域擦掉再画正常内容，否则会留下上一次的像素（重影）。"""
        from .. import backdrop as backdrop_module

        backdrop_module.erase_damage(self, event)
        super().paintEvent(event)

    @staticmethod
    def ask(round_no: int, script_path: str, script: str, parent: QWidget | None = None) -> bool:
        """模态弹窗并返回是否获批。唯一会阻塞的地方 —— 只在主线程、且只在执行前调用。"""
        dialog = ConfirmDialog(round_no, script_path, script, parent)
        return dialog.exec() == QDialog.DialogCode.Accepted
