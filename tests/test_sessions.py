"""按目录检索历史会话 + 对话记录落盘（`run_store/sessions.py`）。"""

from __future__ import annotations

import json
from pathlib import Path

from tu_shell_agent.run_store.sessions import (
    SessionRef,
    append_chat,
    read_chat,
    scan_sessions,
    session_in,
    write_session_meta,
)


def _run_dir(root: Path, name: str, **meta) -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return directory


# ── 记录落盘 ──────────────────────────────────────────────────────────


def test_chat_log_round_trips(tmp_path):
    """对话记录要能原样读回来；正文里出现任何 markdown 结构都不能破坏解析。"""
    run_dir = str(tmp_path / "r")
    append_chat(run_dir, "user", "你好")
    append_chat(run_dir, "model", "## 标题\n```bash\necho hi\n```\n多行正文", when="2026-09-19 21:30")

    entries = read_chat(run_dir)
    assert [entry.role for entry in entries] == ["user", "model"]
    assert entries[0].text == "你好"
    assert entries[1].text.startswith("## 标题")
    assert entries[1].when == "2026-09-19 21:30"


def test_broken_lines_are_skipped_not_fatal(tmp_path):
    """中途崩溃会留下半行 —— 读历史不该因为一行残缺就整段失败。"""
    run_dir = str(tmp_path / "r")
    append_chat(run_dir, "user", "第一句")
    with (Path(run_dir) / "chat.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"role": "model", "text": "半行')          # 残缺
        handle.write("\n\n")                                      # 空行
    assert [entry.text for entry in read_chat(run_dir)] == ["第一句"]


def test_write_session_meta_merges(tmp_path):
    """写会话 id 不能把引擎写过的字段冲掉（config / detection 都还要用）。"""
    run_dir = _run_dir(tmp_path, "20260919-121209-1048", config={"model": "deepseek/x"}, outcome="succeeded")
    write_session_meta(str(run_dir), "ses_1", kind="run", model="deepseek/x")

    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["sessionId"] == "ses_1"
    assert meta["config"] == {"model": "deepseek/x"}
    assert meta["outcome"] == "succeeded"


# ── 目录扫描 ──────────────────────────────────────────────────────────


def test_scan_finds_sessions_by_folder(tmp_path):
    """历史会话从**文件夹**里扫出来：对话目录与运行目录都要能被发现。"""
    chat_dir = _run_dir(tmp_path, "20260919-131546-c95f", kind="chat", sessionId="ses_chat")
    append_chat(str(chat_dir), "user", "你好")
    append_chat(str(chat_dir), "model", "你好，有什么可以帮你的？")
    run_dir = _run_dir(
        tmp_path,
        "20260919-121209-1048",
        sessionId="ses_run",
        rounds=[{"round": 1}, {"round": 2}],
        outcome="succeeded",
        config={"model": "deepseek/deepseek-v4-pro"},
    )
    _run_dir(tmp_path, "20260918-000000-0000", kind="chat")        # 没有 sessionId → 不可恢复

    # 列表按"最近使用"排序（用文件时间戳判定，不看目录名里的创建时间）：
    # 刚聊过的对话要排在最前面，否则每次都得自己往下找。
    import os

    os.utime(run_dir / "meta.json", (1_000, 1_000))
    for name in ("meta.json", "chat.jsonl"):
        os.utime(chat_dir / name, (2_000, 2_000))

    found = scan_sessions(str(tmp_path))
    assert [item.session_id for item in found] == ["ses_chat", "ses_run"], "应只列可恢复的会话"
    chat_ref, run_ref = found
    assert (chat_ref.kind, chat_ref.messages) == ("chat", 2)
    assert (run_ref.kind, run_ref.rounds, run_ref.outcome) == ("run", 2, "succeeded")
    assert run_ref.model == "deepseek/deepseek-v4-pro"
    assert "对话" in chat_ref.label() and "2 条" in chat_ref.label()
    assert "运行" in run_ref.label() and "2 轮" in run_ref.label()


def test_scan_tolerates_missing_and_broken_meta(tmp_path):
    """缺 meta.json / meta.json 是坏的 / 压根不是目录 —— 都要跳过而不是炸掉。"""
    (tmp_path / "没有 meta").mkdir()
    broken = tmp_path / "坏的"
    broken.mkdir()
    (broken / "meta.json").write_text("{不是 JSON", encoding="utf-8")
    (tmp_path / "一个文件.txt").write_text("x", encoding="utf-8")

    assert scan_sessions(str(tmp_path)) == []
    assert scan_sessions(str(tmp_path / "不存在")) == []


def test_session_in_reads_one_directory(tmp_path):
    directory = _run_dir(tmp_path, "20260919-131546-c95f", kind="chat", sessionId="ses_x")
    reference = session_in(str(directory))
    assert isinstance(reference, SessionRef) and reference.session_id == "ses_x"
    assert session_in(str(tmp_path / "没有")) is None


def test_label_falls_back_to_mtime_without_a_timestamp_name(tmp_path):
    directory = _run_dir(tmp_path, "随手改的名字", sessionId="ses_y")
    reference = session_in(str(directory))
    assert reference is not None
    assert reference.display_time() != "时间未知"
