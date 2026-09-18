"""历史运行列表与回放（规格 §12）：扫 run_root 下的 meta.json，选中一条回填三区。

meta.json 在历史上有三种形状，回放必须都能读：
  1. 裸形状 `{outcome, rounds}`；
  2. 超集 `{runId, sessionId, config, detection, outcome, rounds}`；
  3. verify 路径只有 `config` 的中间形态（缺 outcome/rounds）。
缺键一律显示成"未知"，绝不 KeyError —— 历史页是只读回放，一条坏记录最多少显示一列，
不能让整页因为一条半截写入的记录而空掉。
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...types import SEVERITY_RANK, ShellcheckFinding


def parse_shellcheck(raw: str) -> tuple[ShellcheckFinding, ...]:
    """把落盘的 shellcheck json1 报告还原成 findings（回放用）。

    读不动就返回空元组而不是抛：回放时"这份报告缺了"和"报告里没有问题"必须能区分开 ——
    区分不了的时候，历史页只显示脚本与输出，不谎称"零发现"。
    """

    def _int(value: object) -> int:
        return value if isinstance(value, int) else 0

    try:
        data = json.loads(raw) if raw.strip() else None
    except json.JSONDecodeError:
        return ()
    comments = data.get("comments") if isinstance(data, dict) else None
    if not isinstance(comments, list):
        return ()
    findings: list[ShellcheckFinding] = []
    for item in comments:
        if not isinstance(item, dict):
            continue
        level = item.get("level")
        findings.append(
            ShellcheckFinding(
                code=str(item.get("code") or ""),
                line=_int(item.get("line")),
                column=_int(item.get("column")),
                level=level if level in SEVERITY_RANK else "info",
                message=str(item.get("message") or ""),
            )
        )
    return tuple(findings)


class HistoryPage(QWidget):
    """历次运行的列表 + 选中项的快照。

    选中某条时发 `run_selected(dict)`：接线由 main_window（任务 9）负责，
    本页只管"读盘 + 发快照"，不去认识三区控件。
    """

    run_selected = Signal(object)  # {"meta":…, "script":…, "stdout":…, "stderr":…}

    def __init__(self, run_root: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("historyPage")
        self.run_root = run_root

        self.list_widget = QListWidget()
        # 界面骨架测试（tests/test_ui_skeleton.py）按这个 objectName 找控件：改名会让它变红。
        self.list_widget.setObjectName("historyList")
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.setObjectName("historyRefreshButton")
        self.refresh_button.clicked.connect(lambda _checked=False: self.reload())
        header = QHBoxLayout()
        header.addWidget(QLabel("历史运行"))
        header.addStretch(1)
        header.addWidget(self.refresh_button)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)

        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addWidget(self.list_widget, 2)
        layout.addWidget(self.detail, 1)

        self.list_widget.currentItemChanged.connect(self._on_current_changed)

    # ── 扫描与列项 ────────────────────────────────────────────────
    def reload(self) -> None:
        """重扫 run_root。新的在前（run id 以时间戳开头，倒序即时间倒序）。"""
        self.list_widget.clear()
        for meta, run_dir in self._scan():
            item = QListWidgetItem(self._label(meta, run_dir))
            item.setData(Qt.ItemDataRole.UserRole, str(run_dir))
            self.list_widget.addItem(item)

    def _scan(self) -> list[tuple[dict, Path]]:
        if not self.run_root:
            return []
        root = Path(self.run_root)
        if not root.is_dir():
            return []
        found: list[tuple[dict, Path]] = []
        for child in sorted(root.iterdir(), reverse=True):
            meta_path = child / "meta.json"
            if not meta_path.is_file():
                continue
            meta = self._read_json(meta_path)
            if isinstance(meta, dict):
                found.append((meta, child))
        return found

    @staticmethod
    def _label(meta: dict, run_dir: Path) -> str:
        run_id = meta.get("runId") or run_dir.name
        outcome = meta.get("outcome") or "未知"
        rounds = meta.get("rounds")
        rounds_text = f"{rounds} 轮" if isinstance(rounds, int) else "轮次未知"
        return f"{run_id} · {outcome} · {rounds_text}"

    # ── 选中项快照 ────────────────────────────────────────────────
    def _on_current_changed(self, current: QListWidgetItem | None, _previous=None) -> None:
        if current is None:
            self.detail.setPlainText("")
            return
        snapshot = self.current_snapshot()
        self.detail.setPlainText(self._format_detail(snapshot))
        self.run_selected.emit(snapshot)

    def current_snapshot(self) -> dict:
        """当前选中运行的 `{meta, script, notes, findings, stdout, stderr}`；没选中时返回空字典。"""
        item = self.list_widget.currentItem()
        if item is None:
            return {}
        run_dir = Path(str(item.data(Qt.ItemDataRole.UserRole)))
        attempt = self._latest_attempt(run_dir)
        return {
            "meta": self._read_json(run_dir / "meta.json"),
            "script": self._read_text(run_dir / "script.sh"),
            "notes": self._read_text(attempt / "notes.md"),
            "findings": parse_shellcheck(self._read_text(attempt / "shellcheck.json")),
            "stdout": self._read_text(attempt / "stdout.txt"),
            "stderr": self._read_text(attempt / "stderr.txt"),
        }

    @staticmethod
    def _latest_attempt(run_dir: Path) -> Path:
        """回放看**最后一轮**的产物（轮号是目录名，按数值取最大，别按字典序）。"""
        attempts = run_dir / "attempts"
        if not attempts.is_dir():
            return attempts
        numbered: list[tuple[int, Path]] = []
        for child in attempts.iterdir():
            if child.is_dir() and child.name.isdigit():
                numbered.append((int(child.name), child))
        if not numbered:
            return attempts
        return max(numbered)[1]

    @staticmethod
    def _format_detail(snapshot: dict) -> str:
        meta = snapshot.get("meta") or {}
        if not meta:
            return ""
        outcome = meta.get("outcome") or "未知"
        rounds = meta.get("rounds")
        lines = [f"结果：{outcome}    轮次：{rounds if rounds is not None else '未知'}"]
        if meta.get("runId"):
            lines.append(f"运行 ID：{meta['runId']}")
        if meta.get("sessionId"):
            lines.append(f"会话：{meta['sessionId']}")
        return "\n".join(lines)

    # ── 读盘兜底 ──────────────────────────────────────────────────
    @staticmethod
    def _read_text(path: Path) -> str:
        """读不了的产物当空字符串：回放缺文件是常态（第 1 轮契约失败就没执行产物）。"""
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return ""

    @staticmethod
    def _read_json(path: Path):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
