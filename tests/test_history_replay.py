"""历史回放的测试契约：列两种 meta.json 形状都要能读，选中一条就能回填三区。"""

import json
from pathlib import Path

from tu_shell_agent.ui.pages.history import HistoryPage


def _make_run(root: Path, run_id: str, meta: dict, script: str = "echo hi\n") -> Path:
    run_dir = root / run_id
    (run_dir / "attempts" / "1").mkdir(parents=True)
    (run_dir / "script.sh").write_text(script, encoding="utf-8")
    (run_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    (run_dir / "attempts" / "1" / "stdout.txt").write_text("hi\n", encoding="utf-8")
    return run_dir


def test_history_lists_runs_and_tolerates_both_meta_shapes(qtbot, tmp_path):
    root = tmp_path / "runs"
    _make_run(root, "20260918-100000-aaaa", {"outcome": "succeeded", "rounds": 2})
    _make_run(root, "20260918-110000-bbbb", {
        "runId": "20260918-110000-bbbb", "outcome": "needs_human", "rounds": 3,
        "sessionId": "ses_x", "config": {"blocking_level": "info"}, "detection": {},
    })
    page = HistoryPage(run_root=str(root))
    qtbot.addWidget(page)
    page.reload()

    assert page.list_widget.count() == 2
    texts = [page.list_widget.item(i).text() for i in range(2)]
    assert any("succeeded" in t for t in texts)
    assert any("needs_human" in t for t in texts)


def test_replay_loads_script_and_output_into_panes(qtbot, tmp_path):
    root = tmp_path / "runs"
    _make_run(root, "20260918-100000-aaaa", {"outcome": "succeeded", "rounds": 1}, script="echo replay\n")
    page = HistoryPage(run_root=str(root))
    qtbot.addWidget(page)
    page.reload()

    page.list_widget.setCurrentRow(0)
    snapshot = page.current_snapshot()

    assert "echo replay" in snapshot["script"]
    assert "hi" in snapshot["stdout"]
    assert snapshot["meta"]["outcome"] == "succeeded"


def test_history_survives_broken_and_missing_meta(qtbot, tmp_path):
    """半截写入的 meta.json、缺 meta.json 的目录、以及只有 config 的 verify 中间形态：

    三种都要"读得下去"——坏记录跳过、缺键显示未知，绝不 KeyError 让整页空掉。
    """
    root = tmp_path / "runs"
    _make_run(root, "20260918-100000-aaaa", {"outcome": "succeeded", "rounds": 1})
    broken = root / "20260918-120000-cccc"
    broken.mkdir(parents=True)
    (broken / "meta.json").write_text('{"outcome": "succe', encoding="utf-8")  # 半截
    (root / "20260918-130000-dddd").mkdir(parents=True)  # 连 meta.json 都没有
    partial = root / "20260918-140000-eeee"
    partial.mkdir(parents=True)
    (partial / "meta.json").write_text('{"config": {"max_rounds": 3}}', encoding="utf-8")

    page = HistoryPage(run_root=str(root))
    qtbot.addWidget(page)
    page.reload()

    assert page.list_widget.count() == 2  # 完整的一条 + 只有 config 的一条
    texts = [page.list_widget.item(i).text() for i in range(2)]
    assert any("轮次未知" in t for t in texts)  # 缺 outcome/rounds → 显示未知
    assert all("20260918-1" in t for t in texts)


def test_reload_is_safe_without_run_root(qtbot):
    """没配置 run_root（或目录还不存在）时 reload() 必须安静地列空表，而不是抛异常。"""
    page = HistoryPage()
    qtbot.addWidget(page)
    page.reload()
    assert page.list_widget.count() == 0
    assert page.current_snapshot() == {}


def test_history_refresh_button_rescans_the_run_root(qtbot, tmp_path):
    """刷新按钮是回放页唯一的"重新读盘"入口：界面开着的时候别的进程也会往运行根写。"""
    root = tmp_path / "runs"
    root.mkdir(parents=True)
    page = HistoryPage(run_root=str(root))
    qtbot.addWidget(page)
    page.reload()
    assert page.list_widget.count() == 0

    _make_run(root, "20260918-100000-aaaa", {"outcome": "succeeded", "rounds": 1})
    page.refresh_button.click()

    assert page.list_widget.count() == 1
