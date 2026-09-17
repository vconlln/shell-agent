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
