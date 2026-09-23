"""后端注册表与命令行适配器。

适配器这部分刻意驱动**真实的** `CliAgentAdapter` + 一个可执行脚本（`tests/fixtures/fake_agent.sh`）：

- 本机没有 codeagent，而 claude 需要凭据，所以正式测试不碰真 CLI；
- 但适配器最容易写错的地方全在进程边界上（命令行怎么拼、提示词怎么给、JSON 行怎么读、
  超时与取消怎么杀进程树），换成替身对象就全绕过去了 —— 那就等于没测。

流式事件的形状照 claude 2.1.112 的**实测输出**模拟：`system/init` → `assistant`（thinking 块，
没有 text）→ 一行非 JSON 噪音 → 一个未知事件类型 → `assistant`（text 块，与 thinking 帧
**同一个 message id**）→ 第二条消息 → `result`。``--disallowedTools`` 让 init 事件的 tools
列表里不再出现执行类工具，这一点也在假 CLI 里照做。
"""

from __future__ import annotations

import os
import re
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from tu_shell_agent.agent_backends import (
    BackendError,
    UnknownBackendError,
    available_backends,
    backend_descriptor,
    backend_ids,
    build_adapter,
    probe_backend,
    resolve_command,
)
from tu_shell_agent.agent_backends import cli_agent as cli_module
from tu_shell_agent.agent_backends.backends import claude as claude_backend
from tu_shell_agent.agent_backends.backends import codeagent as codeagent_backend
from tu_shell_agent.agent_backends.backends import custom as custom_backend
from tu_shell_agent.agent_backends.cli_agent import CliAgentAdapter, explain_cli_error
from tu_shell_agent.orchestrator.cli_contract import END, SCRIPT_BEGIN, with_output_instructions

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "fake_agent.sh"

MESSAGE = "## 模板骨架\n```bash\necho ok\n```\n\n## 方案文档\n打印 ok\n"

@pytest.fixture(autouse=True)
def _no_leaked_switches(monkeypatch):
    """每个用例从一个干净的开关集开始：假 CLI 的行为全靠环境变量，串味会让用例互相影响。"""
    for key in list(os.environ):
        if key.startswith("FAKE_AGENT_"):
            monkeypatch.delenv(key, raising=False)
    yield


@pytest.fixture
def recorder(tmp_path, monkeypatch) -> SimpleNamespace:
    """让假 CLI 把收到的参数与提示词记到临时文件里。"""
    args = tmp_path / "args.txt"
    prompt = tmp_path / "stdin.txt"
    monkeypatch.setenv("FAKE_AGENT_ARGS_FILE", str(args))
    monkeypatch.setenv("FAKE_AGENT_STDIN_FILE", str(prompt))
    return SimpleNamespace(args=args, prompt=prompt, tmp=tmp_path)


def invocations(path: Path) -> list[list[str]]:
    """把记录文件读成"每次调用一个列表"（假 CLI 每次调用用一行 <<END>> 收尾）。

    注意记录文件是**一行一个参数**，而多行参数（系统提示）会跨多行：所以 `len(call)` 不是
    参数个数，`argc=...` 那一行才是。带换行的值只取到首行，要全文就读 stdin 记录文件。
    """
    calls: list[list[str]] = [[]]
    for line in path.read_text(encoding="utf-8").splitlines():
        if line == "<<END>>":
            calls.append([])
        else:
            calls[-1].append(line)
    return [call for call in calls if call]


def argc_of(call: list[str]) -> int:
    """这次调用收到了几个参数（假 CLI 写的 `argc=` 行）。"""
    for item in call:
        if item.startswith("argc="):
            return int(item.split("=", 1)[1])
    raise AssertionError(f"记录里没有 argc：{call!r}")


def flag_value(argv: list[str], flag: str) -> str:
    """取 `--flag value` 里的 value；没有就返回空串。"""
    for index, item in enumerate(argv):
        if item == flag and index + 1 < len(argv):
            return argv[index + 1]
    return ""


def wait_for_pids(path: Path, count: int, timeout_s: float = 5.0) -> list[int]:
    """等假 CLI 把 pid 写进文件（有界等待，不靠 sleep 猜时序）。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if path.exists():
            pids = [int(line) for line in path.read_text().split() if line.strip()]
            if len(pids) >= count:
                return pids
        time.sleep(0.02)
    raise AssertionError(f"{timeout_s}s 内没有读到 {count} 个 pid")


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:                       # 存在但不属于我们
        return True
    return True


def new_adapter(run_dir: Path, command: str = str(FIXTURE)) -> CliAgentAdapter:
    return CliAgentAdapter(command=command, spec=claude_backend.CLI_SPEC)


# ── 注册表 ────────────────────────────────────────────────────────────


def test_registry_lists_every_backend_in_a_stable_order():
    # 顺序是契约：默认后端在最前，内置 agent（直连模型 API）紧随其后，自定义最后
    assert backend_ids() == ("opencode", "builtin", "claude", "codeagent", "custom")
    assert [item.display_name for item in available_backends()][0] == "opencode"


def test_unknown_backend_id_raises_instead_of_falling_back_to_opencode():
    """未知 id 必须报错：静默回退会把"名字写错了"变成"用另一个 agent 跑了一次"。"""
    with pytest.raises(UnknownBackendError) as error:
        backend_descriptor("claud")
    message = str(error.value)
    assert "claud" in message
    assert "opencode" in message and "claude" in message      # 可选值要列出来
    with pytest.raises(UnknownBackendError):
        build_adapter("claud", "claud")


def test_descriptors_carry_probe_command_and_serve_flag():
    assert backend_descriptor("opencode").needs_serve is True
    assert backend_descriptor("opencode").version_args == ("--version",)
    for backend_id, command in (("claude", "claude"), ("codeagent", "codeagent")):
        descriptor = backend_descriptor(backend_id)
        assert (descriptor.default_command, descriptor.needs_serve) == (command, False)
        assert descriptor.version_args == ("--version",)
        assert descriptor.cli is not None
    assert backend_descriptor("custom").requires_command is True
    assert backend_descriptor("custom").default_command == ""


def test_codeagent_and_custom_reuse_the_claude_flag_constants():
    """两个同源后端不许各抄一份旗标：抄一份就等于权限收敛点有了第二个副本。"""
    assert codeagent_backend.DESCRIPTOR.cli is claude_backend.DESCRIPTOR.cli
    assert custom_backend.DESCRIPTOR.cli is claude_backend.DESCRIPTOR.cli
    assert codeagent_backend.DENIED_TOOLS is claude_backend.DENIED_TOOLS


def test_build_adapter_returns_the_right_adapter_class():
    from tu_shell_agent.opencode_adapter import OpencodeAdapter

    assert isinstance(build_adapter("opencode", "opencode"), OpencodeAdapter)
    for backend_id in ("claude", "codeagent"):
        adapter = build_adapter(backend_id, backend_descriptor(backend_id).default_command)
        assert isinstance(adapter, CliAgentAdapter)
        assert adapter._command == backend_descriptor(backend_id).default_command
    assert isinstance(build_adapter("custom", "/usr/local/bin/my-agent"), CliAgentAdapter)


def test_custom_backend_without_a_command_is_a_clear_error():
    with pytest.raises(BackendError) as error:
        build_adapter("custom", "")
    assert "命令" in str(error.value)


def test_resolve_command_prefers_typed_then_fallback_then_default():
    assert resolve_command("claude", "/opt/claude", fallback="/opt/opencode") == "/opt/claude"
    assert resolve_command("opencode", "", fallback="/opt/opencode") == "/opt/opencode"
    assert resolve_command("codeagent", "") == "codeagent"


# ── 版本探测 ──────────────────────────────────────────────────────────


def test_probe_reads_the_version_from_the_backend_command(recorder):
    result = probe_backend("claude", str(FIXTURE))
    assert result.ok is True
    assert result.tool is not None
    assert result.tool.version == "2.1.112"
    assert result.tool.path == str(FIXTURE)
    assert "2.1.112" in result.detail()


def test_probe_reports_a_missing_command_with_an_install_hint():
    result = probe_backend("claude", "/nonexistent/claude-for-tests")
    assert result.ok is False
    assert "找不到命令" in result.message
    assert "安装" in result.message                 # 可操作建议，而不是只报"失败"


def test_probe_reports_a_nonzero_exit_with_the_last_output_line(tmp_path):
    broken = tmp_path / "broken-agent"
    broken.write_text("#!/usr/bin/env bash\necho 'boom: 端口被占用' >&2\nexit 3\n", encoding="utf-8")
    broken.chmod(0o755)

    result = probe_backend("claude", str(broken))

    assert result.ok is False
    assert "退出码 3" in result.message
    assert "boom" in result.message


def test_probe_of_the_custom_backend_without_a_command_explains_what_to_do():
    result = probe_backend("custom", "")
    assert result.ok is False
    assert "命令" in result.message


# ── generate / chat 的命令行与会话 ─────────────────────────────────────


def test_generate_parses_the_contract_out_of_the_stream(recorder, tmp_path):
    adapter = new_adapter(tmp_path)
    session_id = adapter.start(str(tmp_path), "tu-shell-writer", None)

    generated = adapter.generate(session_id, MESSAGE, {}, 30_000)

    assert SCRIPT_BEGIN not in generated.script           # 标记本身不进脚本
    assert "# @@TU:BODY@@" in generated.script
    assert "echo fake-ok" in generated.script
    assert generated.notes == "按方案实现；保留全部锚点。"
    assert generated.assumptions == ("假设一：目标目录存在", "假设二：不需要提权")
    assert END not in generated.script


def test_new_session_uses_session_id_and_the_prompt_goes_through_stdin(recorder, tmp_path):
    adapter = new_adapter(tmp_path)
    session_id = adapter.start(str(tmp_path), "tu-shell-writer", None)
    assert re.fullmatch(r"[0-9a-f-]{36}", session_id), f"会话 id 必须是 UUID：{session_id}"
    assert uuid.UUID(session_id)                          # 命令行那边要求合法 UUID

    adapter.generate(session_id, MESSAGE, {}, 30_000)

    argv = invocations(recorder.args)[0]
    # 记录的是 "$@"，**不含**程序名（argv[0]）—— 程序名由"进程真的跑起来了"证明。
    assert argv[0] == "-p"
    assert flag_value(argv, "--session-id") == session_id
    assert "--resume" not in argv
    assert flag_value(argv, "--output-format") == "stream-json"
    assert "--verbose" in argv
    # 提示词走 stdin：几百 KB 的方案文档塞进 argv 会撞命令行长度上限
    prompt = recorder.prompt.read_text(encoding="utf-8")
    assert prompt.startswith(MESSAGE.rstrip())
    assert SCRIPT_BEGIN in prompt                          # 输出格式要求随每次生成一起发
    assert with_output_instructions(MESSAGE).strip() == prompt.strip()


def test_every_flag_value_is_exactly_one_argv_element(recorder, tmp_path):
    """系统提示是多行文本，必须是**一个** argv 元素，不能按空白被拆成好几个参数。

    这条用 `argc` 钉住：多行内容不会让参数个数变多（被拆开才会）。逐项数一遍：
    -p / --output-format stream-json / --verbose / --append-system-prompt <整段> /
    --session-id <uuid> / --permission-mode default / --allowedTools <清单> /
    --disallowedTools <清单> = 14 个。
    """
    adapter = new_adapter(tmp_path)
    session_id = adapter.start(str(tmp_path), "tu-shell-writer", None)

    adapter.generate(session_id, MESSAGE, {}, 30_000)

    assert argc_of(invocations(recorder.args)[0]) == 14
    # 系统提示确实是多行的（不是多行的话这条用例证明不了什么）
    assert len(cli_module.CLI_SYSTEM_RULES.splitlines()) > 3
    # 而记录文件里它也确实跨了多行 —— 这正是不能按行数当参数个数的原因
    assert len(invocations(recorder.args)[0]) > 14


def test_second_turn_resumes_the_same_session(recorder, tmp_path):
    """进程每轮退出，所以第二轮起必须 --resume：不用它等于每轮开一段新会话（模型失忆）。"""
    adapter = new_adapter(tmp_path)
    session_id = adapter.start(str(tmp_path), "tu-shell-writer", None)

    adapter.generate(session_id, MESSAGE, {}, 30_000)
    adapter.generate(session_id, MESSAGE, {}, 30_000)

    first, second = invocations(recorder.args)
    assert flag_value(first, "--session-id") == session_id
    assert flag_value(second, "--resume") == session_id
    assert "--session-id" not in second


def test_resume_uses_the_existing_session_id(recorder, tmp_path):
    adapter = new_adapter(tmp_path)
    adapter.resume(str(tmp_path), None)

    adapter.generate("ses_既有会话", MESSAGE, {}, 30_000)

    argv = invocations(recorder.args)[0]
    assert flag_value(argv, "--resume") == "ses_既有会话"
    assert "--session-id" not in argv


def test_execution_tools_are_denied_on_every_call(recorder, tmp_path):
    """权限收敛：引擎是唯一的执行者，agent 只写不跑。

    这条断言的是**命令行里真的带上了**禁用清单，而不只是常量表里有 Bash —— 常量表对模型
    没有任何约束力，只有传下去的旗标有。
    """
    adapter = new_adapter(tmp_path)
    session_id = adapter.start(str(tmp_path), "tu-shell-writer", None)
    adapter.generate(session_id, MESSAGE, {}, 30_000)
    adapter.chat(session_id, "解释一下", 30_000)

    calls = invocations(recorder.args)
    assert len(calls) == 2
    for argv in calls:
        denied = flag_value(argv, "--disallowedTools")
        assert denied, "没有传 --disallowedTools"
        for tool in ("Bash", "Edit", "Write", "WebFetch"):
            assert tool in denied.split(",")
        assert flag_value(argv, "--permission-mode") == "default"
        assert "Bash" not in flag_value(argv, "--allowedTools").split(",")


def test_the_fake_cli_confirms_denied_tools_left_the_tool_list(recorder, tmp_path):
    """假 CLI 照真实 CLI 的行为把被禁工具从 init 事件里剔掉，这里核对清单确实生效。"""
    adapter = new_adapter(tmp_path)
    session_id = adapter.start(str(tmp_path), "tu-shell-writer", None)
    events = run_and_capture(adapter, session_id, tmp_path)

    init = _first_event(events, "system")
    assert init is not None
    assert "Bash" not in init["tools"]
    assert "Read" in init["tools"]          # 只读工具留着：读运行目录对生成有帮助
    assert init["session_id"] == session_id


def test_streaming_deltas_skip_thinking_and_do_not_repeat_a_message(recorder, tmp_path):
    """实测形状里同一条消息会发多次（thinking 帧 + text 帧）：正文只许发一次。"""
    adapter = new_adapter(tmp_path)
    session_id = adapter.start(str(tmp_path), "tu-shell-writer", None)
    deltas: list[str] = []

    adapter.generate(session_id, MESSAGE, {}, 30_000, on_delta=deltas.append)

    assert deltas, "流式增量一次都没回调"
    assert "先想一下" not in "".join(deltas), "thinking 块被当成正文发出去了"
    joined = "".join(deltas)
    assert joined.count("echo fake-ok") == 1, f"正文被重复追加：{joined!r}"
    assert deltas[-1] == "补充说明。"                       # 第二条消息是新的增量
    assert SCRIPT_BEGIN in joined


def test_chat_returns_plain_text_and_passes_the_preamble_as_system_prompt(recorder, tmp_path):
    adapter = new_adapter(tmp_path)
    session_id = adapter.start(str(tmp_path), "tu-shell-writer", None)

    reply = adapter.chat(
        session_id, "为什么第一轮失败了？", 30_000, system_preamble="（上下文）方案文档如下"
    )

    assert "echo fake-ok" in reply                     # 自由对话不做契约解析，原样返回
    argv = invocations(recorder.args)[0]
    # 单行前情才能这样断（多行值在记录里会跨行，见 invocations 的说明）
    assert flag_value(argv, "--append-system-prompt") == "（上下文）方案文档如下"
    # 对话不带输出格式要求：那句要求只属于生成脚本那条路
    assert SCRIPT_BEGIN not in recorder.prompt.read_text(encoding="utf-8")


def test_chat_uses_the_per_call_model(recorder, tmp_path):
    """逐条指定模型：对话里换模型不用重建会话（与 opencode 那条路的语义一致）。"""
    adapter = new_adapter(tmp_path)
    session_id = adapter.start(str(tmp_path), "tu-shell-writer", "sonnet")

    adapter.chat(session_id, "你好", 30_000)                 # 不指定模型：用 start() 记下的
    adapter.chat(session_id, "你好", 30_000, model="opus")    # 逐条指定模型

    without_model, with_model = invocations(recorder.args)
    assert flag_value(with_model, "--model") == "opus"
    # 没逐条指定时用会话/设置里那个模型兜底（与 opencode 那条路一致：start(model) 定的模型
    # 是默认值，逐条指定只是覆盖本次）。
    assert flag_value(without_model, "--model") == "sonnet"
    assert argc_of(with_model) == argc_of(without_model)


def test_no_model_flag_at_all_when_nothing_is_configured(recorder, tmp_path):
    """一个模型都没配时不带 --model：带上空值会把"用默认模型"变成"用名叫空的模型"。"""
    adapter = new_adapter(tmp_path)
    session_id = adapter.start(str(tmp_path), "tu-shell-writer", None)

    adapter.chat(session_id, "你好", 30_000)

    assert "--model" not in invocations(recorder.args)[0]


def test_generate_without_markers_reports_a_readable_error(recorder, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_AGENT_BAD_TEXT", "1")
    adapter = new_adapter(tmp_path)
    session_id = adapter.start(str(tmp_path), "tu-shell-writer", None)

    with pytest.raises(RuntimeError) as error:
        adapter.generate(session_id, MESSAGE, {}, 30_000)

    message = str(error.value)
    assert "缺少标记" in message
    assert "处理：" in message                    # 可操作的建议，不是只有原文


def test_generate_without_any_assistant_text_is_reported_honestly(recorder, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_AGENT_SILENT", "1")
    adapter = new_adapter(tmp_path)
    session_id = adapter.start(str(tmp_path), "tu-shell-writer", None)

    with pytest.raises(RuntimeError) as error:
        adapter.generate(session_id, MESSAGE, {}, 30_000)

    assert "没有输出任何助手文本" in str(error.value)


def test_result_only_stream_falls_back_to_the_result_text(recorder, tmp_path, monkeypatch):
    """有的版本只发 result 事件：那时用 result 里的全文兜底，而不是判成"没产出"。"""
    monkeypatch.setenv("FAKE_AGENT_RESULT_ONLY", "1")
    adapter = new_adapter(tmp_path)
    session_id = adapter.start(str(tmp_path), "tu-shell-writer", None)

    generated = adapter.generate(session_id, MESSAGE, {}, 30_000)

    assert "# @@TU:BODY@@" in generated.script


# ── 失败路径：超时、退出码、is_error、取消 ─────────────────────────────


def test_timeout_kills_the_process_tree_and_explains_what_to_do(
    recorder, tmp_path, monkeypatch
):
    monkeypatch.setenv("FAKE_AGENT_SLEEP", "30")
    pids_file = tmp_path / "pids.txt"
    monkeypatch.setenv("FAKE_AGENT_PID_FILE", str(pids_file))
    adapter = new_adapter(tmp_path)
    session_id = adapter.start(str(tmp_path), "tu-shell-writer", None)

    with pytest.raises(RuntimeError) as error:
        adapter.generate(session_id, MESSAGE, {}, 800)

    assert "没有返回" in str(error.value)
    assert "生成超时" in str(error.value)
    pid = wait_for_pids(pids_file, 1)[0]
    assert not alive(pid), "超时之后子进程还活着"


def test_nonzero_exit_reports_the_code_and_the_stderr_tail(recorder, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_AGENT_EXIT", "7")
    adapter = new_adapter(tmp_path)
    session_id = adapter.start(str(tmp_path), "tu-shell-writer", None)

    with pytest.raises(RuntimeError) as error:
        adapter.generate(session_id, MESSAGE, {}, 30_000)

    message = str(error.value)
    assert "退出码 7" in message
    assert "上游拒绝" in message                       # stderr 尾部，真正的理由


def test_error_result_event_is_treated_as_failure_even_with_exit_code_zero(
    recorder, tmp_path, monkeypatch
):
    """退出码 0 但 result 事件说本轮失败：只看退出码会把它报成"没产出脚本"，理由丢一半。"""
    monkeypatch.setenv("FAKE_AGENT_ERROR_RESULT", "1")
    adapter = new_adapter(tmp_path)
    session_id = adapter.start(str(tmp_path), "tu-shell-writer", None)

    with pytest.raises(RuntimeError) as error:
        adapter.generate(session_id, MESSAGE, {}, 30_000)

    message = str(error.value)
    assert "报告本轮失败" in message
    assert "no credentials" in message
    assert "处理：" in message                        # 认得出"没登录"并给出下一步


def test_abort_kills_the_whole_process_tree(recorder, tmp_path, monkeypatch):
    """取消必须杀掉整棵树：agent 常常自己再起帮手，留着它们会继续动运行目录里的东西。"""
    monkeypatch.setenv("FAKE_AGENT_SLEEP", "30")
    monkeypatch.setenv("FAKE_AGENT_CHILD_SLEEP", "30")
    pids_file = tmp_path / "pids.txt"
    monkeypatch.setenv("FAKE_AGENT_PID_FILE", str(pids_file))
    adapter = new_adapter(tmp_path)
    session_id = adapter.start(str(tmp_path), "tu-shell-writer", None)
    failures: list[str] = []

    def run() -> None:
        try:
            adapter.generate(session_id, MESSAGE, {}, 60_000)
        except RuntimeError as error:
            failures.append(str(error))

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    parent_pid, child_pid = wait_for_pids(pids_file, 2)
    adapter.abort(session_id)
    worker.join(timeout=10)

    assert not worker.is_alive()
    assert failures == ["已取消"]
    assert not alive(parent_pid), "被 abort 之后子进程还活着"
    assert not alive(child_pid), "子进程的帮手还活着：杀的不是整棵树"


def test_dispose_is_quiet_and_stops_a_running_call(recorder, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_AGENT_SLEEP", "30")
    pids_file = tmp_path / "pids.txt"
    monkeypatch.setenv("FAKE_AGENT_PID_FILE", str(pids_file))
    adapter = new_adapter(tmp_path)
    session_id = adapter.start(str(tmp_path), "tu-shell-writer", None)
    failures: list[str] = []

    def run() -> None:
        try:
            adapter.generate(session_id, MESSAGE, {}, 60_000)
        except RuntimeError as error:
            failures.append(str(error))

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    pid = wait_for_pids(pids_file, 1)[0]
    adapter.dispose()                       # 不能抛异常：它在 finally 与关窗路径上
    worker.join(timeout=10)
    adapter.dispose()                       # 重复调用同样要安全

    assert failures == ["已取消"]
    assert not alive(pid)


def test_missing_command_is_reported_before_touching_the_process(recorder, tmp_path):
    adapter = new_adapter(tmp_path, command="/nonexistent/agent-for-tests")
    session_id = adapter.start(str(tmp_path), "tu-shell-writer", None)

    with pytest.raises(RuntimeError) as error:
        adapter.generate(session_id, MESSAGE, {}, 5_000)

    assert "找不到命令" in str(error.value)
    assert not recorder.args.exists(), "不该起子进程"


def test_cancel_token_before_the_call_does_not_spawn_a_process(recorder, tmp_path):
    """已经置位的取消令牌要在起进程**之前**生效 —— 否则取消一次会白起一个 agent 进程。"""
    adapter = new_adapter(tmp_path)
    session_id = adapter.start(str(tmp_path), "tu-shell-writer", None)
    token = threading.Event()
    token.set()

    with pytest.raises(RuntimeError) as error:
        adapter.generate(session_id, MESSAGE, {}, 30_000, cancel=token)

    assert "已取消" in str(error.value)
    assert not recorder.args.exists()


def test_session_id_mismatch_is_noted_but_does_not_fail_the_turn(
    recorder, tmp_path, monkeypatch
):
    """命令行 agent 另开了会话时要说一句，但不能因此判失败（这一轮的产出仍然可用）。"""
    notes: list[str] = []
    monkeypatch.setenv("FAKE_AGENT_SESSION_ID", "ses_别的会话")
    adapter = CliAgentAdapter(command=str(FIXTURE), spec=claude_backend.CLI_SPEC, note=notes.append)
    session_id = adapter.start(str(tmp_path), "tu-shell-writer", None)

    generated = adapter.generate(session_id, MESSAGE, {}, 30_000)

    assert "# @@TU:BODY@@" in generated.script
    assert any("会话 id" in note for note in notes), notes


def test_unknown_events_and_noise_do_not_break_the_turn(recorder, tmp_path):
    """未知事件类型与非 JSON 噪音必须被忽略（假 CLI 每次都会发这两样）。"""
    adapter = new_adapter(tmp_path)
    session_id = adapter.start(str(tmp_path), "tu-shell-writer", None)

    generated = adapter.generate(session_id, MESSAGE, {}, 30_000)   # 不抛异常即通过

    assert generated.script.strip().endswith("echo fake-ok")


def test_explain_cli_error_only_maps_known_problems():
    assert "登录" in explain_cli_error("Invalid API key · Please run /login")
    assert "新会话" in explain_cli_error("No conversation found with session ID: x")
    assert explain_cli_error("完全看不懂的一段输出") == ""


def test_delta_extractor_handles_cumulative_frames_and_partial_chunks():
    """解析器自身的边界：同 id 的累计帧只发尾巴；增量帧直接发；result 不当正文重复发。"""
    extractor = cli_module._DeltaExtractor()
    assert extractor.feed('{"type":"assistant","message":{"id":"m1","content":['
                          '{"type":"text","text":"你好"}]}}') == "你好"
    # 同一条消息的累计帧（多了两个字）：只发"世界"
    assert extractor.feed('{"type":"assistant","message":{"id":"m1","content":['
                          '{"type":"text","text":"你好世界"}]}}') == "世界"
    # 增量帧（--include-partial-messages 打开时会出现）
    assert extractor.feed('{"type":"stream_event","event":{"type":"content_block_delta",'
                          '"delta":{"type":"text_delta","text":"！"}}}') == "！"
    # result 事件只留作兜底，不作为增量再发一遍
    assert extractor.feed('{"type":"result","result":"你好世界！","is_error":false}') is None
    assert extractor.result_text == "你好世界！"
    assert extractor.is_error is False
    # 噪音
    assert extractor.feed("这不是 JSON") is None
    assert extractor.feed('{"type":"未来","x":1}') is None


# ── 小工具 ────────────────────────────────────────────────────────────


def run_and_capture(adapter: CliAgentAdapter, session_id: str, run_dir: Path) -> list[dict]:
    """直接起一次进程并把 JSON 事件行读回来（用来核对 init 事件的内容）。"""
    import json
    import subprocess

    argv = adapter.build_argv(session_id=session_id, new_session=True)
    completed = subprocess.run(
        argv, input=MESSAGE, capture_output=True, text=True, cwd=str(run_dir), timeout=30
    )
    events = []
    for line in completed.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _first_event(events: list[dict], kind: str) -> dict | None:
    return next((event for event in events if event.get("type") == kind), None)
