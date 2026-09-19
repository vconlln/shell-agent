"""按**目录**检索历史会话 + 对话记录的落盘格式。

opencode 的会话本身活在它自己的库里；"哪次对话对应哪个目录"这件事由我们记录 ——
引擎那条路（生成脚本）早就把 `sessionId` 写进了运行目录的 `meta.json`，所以「继续修复」
重启后还能接着上次的会话跑。但**模型对话**那条路以前什么都不写：会话 id 只在内存里，
连对话记录都没落盘，重启就找不回来了。

这里补两件事：

1. 对话记录落成 `chat.jsonl`（一行一条，追加写）—— 人可读、机器可解析，且不怕正文里
   出现任何 markdown 结构；
2. `scan_sessions(run_root)` 直接**扫文件夹**：每个子目录读 `meta.json`，有 `sessionId`
   的就算一段可恢复的会话。于是历史会话是可检索的，不依赖任何内存状态。

`kind` 取 `meta.json` 里的 `kind`（对话目录会写 `"chat"`），没有就按"有没有轮次"推断。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from ..filetext import write_text_lf

CHAT_LOG_NAME = "chat.jsonl"
META_NAME = "meta.json"

# 角色 → 展示名。与界面里的说话人保持一致，回填记录时不会串味。
ROLE_LABELS = {"user": "你", "model": "模型", "error": "！", "note": "·"}


@dataclass(frozen=True, slots=True)
class ChatEntry:
    """一条对话记录。`when` 是可读时间（写盘时记下来，界面回填时按原样显示）。"""

    role: str
    text: str
    when: str = ""


@dataclass(frozen=True, slots=True)
class SessionRef:
    """一段可从磁盘恢复的会话。"""

    session_id: str
    run_dir: str
    kind: str = "run"            # run / chat
    rounds: int = 0
    outcome: str = ""
    model: str = ""
    messages: int = 0
    updated_at: float = 0.0

    def display_time(self) -> str:
        """目录名带时间戳（`YYYYMMDD-HHMMSS-xxxx`），直接用；拿不到就退回修改时间。"""
        name = Path(self.run_dir).name
        stamp = name.split("-")
        if len(stamp) >= 2 and len(stamp[0]) == 8 and len(stamp[1]) == 6 and stamp[0].isdigit():
            return f"{stamp[0][4:6]}-{stamp[0][6:8]} {stamp[1][:2]}:{stamp[1][2:4]}"
        if self.updated_at:
            return time.strftime("%m-%d %H:%M", time.localtime(self.updated_at))
        return "时间未知"

    def label(self) -> str:
        parts = [self.display_time(), "对话" if self.kind == "chat" else "运行"]
        if self.messages:
            parts.append(f"{self.messages} 条")
        if self.rounds:
            parts.append(f"{self.rounds} 轮")
        if self.outcome:
            parts.append(self.outcome)
        if self.model:
            parts.append(self.model)
        return " · ".join(parts)


def chat_log_path(run_dir: str) -> Path:
    return Path(run_dir) / CHAT_LOG_NAME


def append_chat(run_dir: str, role: str, text: str, *, when: str = "") -> None:
    """追加一条对话记录。**追加写**：中途崩溃也只丢最后一条，前面的还在。"""
    path = chat_log_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"role": role, "text": text, "when": when or time.strftime("%Y-%m-%d %H:%M")}
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_chat(run_dir: str) -> list[ChatEntry]:
    """读回对话记录。坏行（写了一半）直接跳过 —— 读历史不该因为一行残缺就整段失败。"""
    path = chat_log_path(run_dir)
    if not path.is_file():
        return []
    entries: list[ChatEntry] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except ValueError:
            continue
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or "")
        if not text:
            continue
        entries.append(ChatEntry(str(raw.get("role") or "note"), text, str(raw.get("when") or "")))
    return entries


def scan_sessions(run_root: str) -> list[SessionRef]:
    """扫运行根目录下的每个子目录，返回**按最近使用排序**的可恢复会话。

    只认有 `sessionId` 的目录：没有 id 就没法让 opencode 接着那一段，
    列出来只会让人点了却没反应。缺 `meta.json`、坏 `meta.json`、被手改坏的目录都跳过。
    """
    root = Path(run_root).expanduser()
    if not root.is_dir():
        return []
    found: list[SessionRef] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        reference = _session_in(entry)
        if reference is not None:
            found.append(reference)
    found.sort(key=lambda item: item.updated_at, reverse=True)
    return found


def session_in(run_dir: str) -> SessionRef | None:
    """读一个目录里的会话信息；没有/读不出来返回 None。"""
    return _session_in(Path(run_dir))


def _session_in(run_dir: Path) -> SessionRef | None:
    meta_path = run_dir / META_NAME
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict):
        return None
    session_id = str(meta.get("sessionId") or "").strip()
    if not session_id:
        return None

    rounds = meta.get("rounds")
    rounds_count = len(rounds) if isinstance(rounds, list) else 0
    entries = read_chat(str(run_dir))
    stamps = [meta_path.stat().st_mtime if meta_path.is_file() else 0.0]
    if chat_log_path(str(run_dir)).is_file():
        stamps.append(chat_log_path(str(run_dir)).stat().st_mtime)
    kind = str(meta.get("kind") or ("run" if rounds_count or meta.get("outcome") else "chat"))
    config = meta.get("config") if isinstance(meta.get("config"), dict) else {}
    return SessionRef(
        session_id=session_id,
        run_dir=str(run_dir),
        kind=kind,
        rounds=rounds_count,
        outcome=str(meta.get("outcome") or ""),
        model=str(meta.get("model") or config.get("model") or ""),
        messages=len(entries),
        updated_at=max(stamps),
    )


def write_session_model(run_dir: str, model: str) -> None:
    """只更新目录里记的模型（会话 id 与 kind 不动）—— 对话里换模型后要让文件夹记住。"""
    path = Path(run_dir) / META_NAME
    current: dict = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                current = loaded
        except (OSError, ValueError):
            current = {}
    current["model"] = model
    write_text_lf(path, json.dumps(current, ensure_ascii=False, indent=2) + "\n")


def write_session_meta(run_dir: str, session_id: str, *, kind: str, model: str = "") -> None:
    """把会话 id 落进目录的 `meta.json`（合并写，不覆盖引擎写过的其它字段）。"""
    path = Path(run_dir) / META_NAME
    current: dict = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                current = loaded
        except (OSError, ValueError):
            current = {}
    current["sessionId"] = session_id
    current["kind"] = kind
    if model:
        current["model"] = model
    write_text_lf(path, json.dumps(current, ensure_ascii=False, indent=2) + "\n")
