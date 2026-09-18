import json
from datetime import datetime, timezone

from tu_shell_agent.run_store.layout import AGENT_RELATIVE_PATH, attempt_dir, make_run_id, run_dir_for
from tu_shell_agent.run_store.store import RunStore


def test_make_run_id_is_sortable_and_path_safe():
    run_id = make_run_id(datetime(2026, 9, 17, 12, 0, 0, tzinfo=timezone.utc), salt="ab12")
    assert run_id == "20260917-120000-ab12"
    assert "/" not in run_id


def test_layout_matches_spec():
    run_dir = run_dir_for("/tmp/root", "r1")
    assert run_dir.endswith("r1")
    assert attempt_dir(run_dir, 2).endswith("attempts/2")
    assert AGENT_RELATIVE_PATH == ".opencode/agents/tu-shell-writer.md"


def test_write_script_forces_lf(tmp_path):
    store = RunStore(str(tmp_path / "r1"))
    store.init()
    path = store.write_script(1, "echo hi\r\necho bye\r\n")
    assert path.endswith("script.sh")
    assert (tmp_path / "r1" / "script.sh").read_text(encoding="utf-8") == "echo hi\necho bye\n"


def test_write_attempt_keeps_each_round(tmp_path):
    store = RunStore(str(tmp_path / "r1"))
    store.init()
    store.write_script(1, "echo one\n")
    store.write_attempt(1, {"stdout.txt": "one\n", "shellcheck.json": '{"comments": []}'})
    store.write_script(2, "echo two\n")
    store.write_attempt(2, {"stdout.txt": "two\n"})

    round_one = tmp_path / "r1" / "attempts" / "1"
    assert (round_one / "stdout.txt").read_text(encoding="utf-8") == "one\n"
    assert (round_one / "script.sh").read_text(encoding="utf-8") == "echo one\n"
    assert (tmp_path / "r1" / "attempts" / "2" / "stdout.txt").read_text(encoding="utf-8") == "two\n"


def test_write_meta_merges_patches(tmp_path):
    store = RunStore(str(tmp_path / "r1"))
    store.init()
    store.write_meta({"runId": "r1"})
    store.write_meta({"outcome": "succeeded", "rounds": 2})
    meta = json.loads((tmp_path / "r1" / "meta.json").read_text(encoding="utf-8"))
    assert meta == {"runId": "r1", "outcome": "succeeded", "rounds": 2}


def test_init_creates_agent_directory(tmp_path):
    store = RunStore(str(tmp_path / "r1"))
    store.init()
    assert (tmp_path / "r1" / ".opencode" / "agents").is_dir()


def test_write_meta_creates_run_dir_when_called_first(tmp_path):
    run_dir = tmp_path / "fresh"
    store = RunStore(str(run_dir))
    store.write_meta({"outcome": "aborted_dependency", "rounds": 0})
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["outcome"] == "aborted_dependency"


def test_scripts_and_evidence_are_written_with_lf_bytes(tmp_path):
    """落盘的脚本必须**按字节**是 LF。

    这条只能在字节层面断言：`Path.write_text()` 在 Windows 上会把 \\n 翻译成 \\r\\n，
    而 `read_text()` 又会把 CRLF 悄悄读回 LF —— 用 read_text 写的断言在 Windows 上照样绿，
    所以这个坑只能靠 read_bytes 发现。脚本一旦落成 CRLF，shellcheck 会给每一行报
    SC1017（error），默认阻断级别下每轮都不执行，Windows 上永远跑不到 succeeded。
    """
    run_dir = tmp_path / "run"
    store = RunStore(str(run_dir))
    store.init()

    store.write_script(1, "#!/usr/bin/env bash\necho ok\n")
    store.write_inputs({"plan.md": "方案第一行\n方案第二行\n"})
    store.write_attempt(1, {"notes.md": "取舍说明\n假设\n"})
    store.write_meta({"outcome": "succeeded", "rounds": 1})

    for relative in ("script.sh", "attempts/1/script.sh", "plan.md", "attempts/1/notes.md", "meta.json"):
        raw = (run_dir / relative).read_bytes()
        assert b"\r\n" not in raw, relative
        assert raw.endswith(b"\n"), relative


def test_run_artifacts_never_go_through_text_mode_writes(tmp_path, monkeypatch):
    """运行产物一律不许走 `Path.write_text`（文本模式）。

    为什么不用"模拟 Windows 换行"来测：本机 Linux 的 `os.linesep` 就是 "\n"，
    而 `Path.write_text` 用的文本包装器并不服从运行时改动的 `os.linesep`
    （实测 monkeypatch 之后它依旧写 LF），所以"翻译成 CRLF"这件事在 Linux 上复现不出来。
    能可靠锁住的是一条更强的性质：**这条路径上根本没有文本模式写**。
    真到了 Windows 上，只要没有文本模式写，就不可能出现 CRLF 脚本。
    """
    from pathlib import Path as _Path

    writes: list[str] = []
    real_write_text = _Path.write_text

    def spy(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        writes.append(str(self))
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(_Path, "write_text", spy)

    store = RunStore(str(tmp_path / "run"))
    store.init()
    store.write_script(1, "#!/usr/bin/env bash\necho ok\n")
    store.write_inputs({"plan.md": "方案\n"})
    store.write_attempt(1, {"notes.md": "取舍\n"})
    store.write_meta({"outcome": "succeeded", "rounds": 1})

    assert writes == [], f"这些文件走了文本模式写（Windows 上会变成 CRLF）：{writes}"
