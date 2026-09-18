"""worker 必须：把引擎事件变成信号、支持取消、并且确认请求是"发信号 + 等回答"的握手。"""

import os
import threading

from tu_shell_agent.types import DetectionReport, ExecuteResult, GeneratedScript, RunConfig
from tu_shell_agent.ui.engine_worker import EngineWorker


class _FakeOpencode:
    def __init__(self, gate: threading.Event | None = None) -> None:
        self.gate = gate
        self.cancelled = False

    def start(self, run_dir, agent_name, model):
        return "ses_1"

    def generate(self, session_id, message, schema, timeout_ms, on_delta=None, cancel=None):
        if self.gate is not None:
            self.gate.wait(5)  # 让测试有机会取消
        return GeneratedScript(script="#!/usr/bin/env bash\n# @@TU:BODY@@\necho ok\n", notes="n", assumptions=())

    def abort(self, session_id):
        self.cancelled = True

    def dispose(self):
        pass


class _FakeToolchain:
    def __init__(self) -> None:
        # 记录被执行过的脚本路径：取消类断言必须能区分"引擎拒绝执行"和"执行到一半被取消"
        # （后者 outcome 同样是 cancelled，只断言 outcome 会放过"取消后照跑"的回归）。
        self.executions: list[str] = []

    def detect(self):
        return DetectionReport(None, None, None, ())

    def shellcheck(self, script_path):
        return [], 0, '{"comments": []}'

    def execute(self, script_path, cwd, timeout_ms, cancel=None, on_stdout=None, on_stderr=None):
        self.executions.append(script_path)
        return ExecuteResult(0, None, False, False, 1, "ok\n", "")


class _Store:
    """run_dir 指向 pytest 的 tmp_path：测试不碰共享的 /tmp/run。"""

    def __init__(self, run_dir) -> None:
        self.run_dir = str(run_dir)

    def write_script(self, round_no, script):
        # 真的把脚本落盘（引擎的契约是「write_script 之后文件就在 script_path 上」，
        # 后续 rounds 的 shellcheck/execute 都按这个路径找文件）。
        path = os.path.join(self.run_dir, f"script-{round_no}.sh")
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(script)
        return path

    def write_attempt(self, round_no, files):
        pass

    def write_meta(self, patch):
        pass

    def write_inputs(self, files):
        pass


def _worker(*, opencode=None, toolchain=None) -> EngineWorker:
    """worker 没有 trusted 开关：跳过确认是**模板**的属性（见 _make_input 的 trusted 参数）。

    替身必须**同一个实例**装进 worker 与 LoopPorts：worker.run() 用构造参数里的
    opencode/toolchain 组装端口（ports 里那份只贡献 store），所以各建一份的话，
    断言读到的是"从没被调用过的那一份"，恒为空 —— 那是假绿。
    """
    return EngineWorker(
        opencode=opencode if opencode is not None else _FakeOpencode(),
        toolchain=toolchain if toolchain is not None else _FakeToolchain(),
        config=RunConfig(run_root="/tmp/root"),
    )


def _make_input(worker: EngineWorker, opencode, toolchain, *, trusted: bool, tmp_path):
    """用**同一批 fake** 同时装 worker 与 LoopInput，worker 只替换"确认"这一个端口。

    worker 与编排层必须看到同一份 fake，断言才有意义；所以这里自己组装 LoopPorts，
    而不是去读 worker 的私有属性。`emit=None` 只是占位 —— run() 一定用自身的 _emit 覆盖它，
    这样 LoopInput 里的 ports.emit 与它无关（也顺便证明 worker 没有把 emit 透传出去）。
    """
    from tu_shell_agent.orchestrator.loop import LoopInput, LoopPorts, TemplateSpec

    store = _Store(tmp_path)
    ports = LoopPorts(
        opencode=opencode,
        toolchain=toolchain,
        confirm=worker,
        store=store,
        emit=None,
    )
    input_ = LoopInput(
        plan="打印 ok",
        template=TemplateSpec(
            id="single",
            body="#!/usr/bin/env bash\n# @@TU:BODY@@\necho ok\n",
            anchors=("@@TU:BODY@@",),
            trusted=trusted,
        ),
        values={},
        run_dir=store.run_dir,
        config=RunConfig(run_root="/tmp/root"),
        ports=ports,
    )
    worker.submit(input_)
    return store


def test_worker_emits_run_events_in_order(qtbot, tmp_path):
    opencode = _FakeOpencode()
    toolchain = _FakeToolchain()
    worker = _worker(opencode=opencode, toolchain=toolchain)
    _make_input(worker, opencode, toolchain, trusted=True, tmp_path=tmp_path)
    seen: list[str] = []
    worker.event.connect(lambda event: seen.append(event.type))

    with qtbot.waitSignal(worker.finished_result, timeout=10_000) as blocker:
        worker.start()
    worker.wait(5_000)

    assert blocker.args[0].outcome == "succeeded"
    assert "script" in seen and "shellcheck" in seen and "execute" in seen


def test_worker_cancel_sets_token_and_finishes_cancelled(qtbot, tmp_path):
    """取消令牌被置位后，引擎必须收在 cancelled，且**一次都不执行**脚本。

    这一条盯的是「生成期间取消 + generate 正常返回」：若编排层不在执行前复查令牌，
    脚本会照跑（executions 非空）并报 succeeded —— 用户取消了一个会改磁盘的脚本，
    却看到"成功"。所以这里既断言 outcome，也断言 executions。
    """
    gate = threading.Event()
    opencode = _FakeOpencode(gate)
    toolchain = _FakeToolchain()
    worker = _worker(opencode=opencode, toolchain=toolchain)
    _make_input(worker, opencode, toolchain, trusted=True, tmp_path=tmp_path)
    with qtbot.waitSignal(worker.finished_result, timeout=10_000) as blocker:
        worker.start()
        qtbot.wait(100)
        worker.cancel()
        gate.set()
    worker.wait(5_000)
    assert worker.cancel_token().is_set()
    assert blocker.args[0].outcome == "cancelled"
    assert toolchain.executions == []


def test_worker_confirm_handshake_blocks_until_answered(qtbot, tmp_path):
    """确认握手：worker 发 confirm_requested 并阻塞，主线程 answer_confirm 后继续执行。

    只断言"问过确认"是不够的 —— 把 `_confirm_gate.wait()` 换成 `pass`（不再阻塞）或把
    返回值写死成 True（确认形同虚设）这两种坏法，在"只断言 answer == [True]"下都照样全绿。
    所以这里同时断言：答复之前一次都没执行、答复之后确实执行了。
    """
    answer: list[bool] = []
    opencode = _FakeOpencode()
    toolchain = _FakeToolchain()
    worker = _worker(opencode=opencode, toolchain=toolchain)
    store = _make_input(worker, opencode, toolchain, trusted=False, tmp_path=tmp_path)

    def on_request(payload: dict) -> None:
        # 收到确认请求的那一刻，脚本必须还没有被执行过（握手真的在阻塞）
        assert toolchain.executions == []
        answer.append(True)
        worker.answer_confirm(True)

    worker.confirm_requested.connect(on_request)
    with qtbot.waitSignal(worker.finished_result, timeout=10_000) as blocker:
        worker.start()
    worker.wait(5_000)
    assert answer == [True]
    assert blocker.args[0].outcome == "succeeded"
    assert toolchain.executions == [f"{store.run_dir}/script-1.sh"]


def test_worker_cancel_while_waiting_for_confirm_does_not_execute(qtbot, tmp_path):
    """取消与确认交错时按"拒绝"处理：先取消、之后就算有人点了"执行"也不许跑。

    `cancel()` 会把确认关口一并打开（否则 worker 线程会一直等下去），放行之后只看
    `_confirm_answer` 的话，"取消 → 用户点执行"就会放行一个已被取消的脚本。
    """
    opencode = _FakeOpencode()
    toolchain = _FakeToolchain()
    worker = _worker(opencode=opencode, toolchain=toolchain)
    _make_input(worker, opencode, toolchain, trusted=False, tmp_path=tmp_path)

    def on_request(_payload: dict) -> None:
        worker.cancel()            # 用户在确认框还开着的时候点了"取消"
        worker.answer_confirm(True)  # 随后（或并发地）又点了"执行"

    worker.confirm_requested.connect(on_request)
    with qtbot.waitSignal(worker.finished_result, timeout=10_000) as blocker:
        worker.start()
    worker.wait(5_000)

    assert blocker.args[0].outcome == "cancelled"
    assert toolchain.executions == []
