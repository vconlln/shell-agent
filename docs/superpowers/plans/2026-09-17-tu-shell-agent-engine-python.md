# tu-shell-agent 核心引擎实现计划（Python 版 · Plan 1 / 2）

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 交付一个无界面的 Python 核心引擎：给定"方案文档 + shell 模板"，调用本机 opencode 生成脚本，用 shellcheck 静态校验、用 bash（Windows 上即 Git Bash）执行，失败时把结构化证据回灌同一 opencode 会话自修，最多 3 轮。

**架构：** `ui → orchestrator → {opencode_adapter, shell_toolchain, template_store, run_store}`。`orchestrator` 与两个 store 是纯 Python（不 import PySide6、不 import `subprocess`），外界一律经 `ports.py` 的 `typing.Protocol` 注入，因此可在 Linux 上无界面纯单测。本计划结束时用 `python -m tu_shell_agent.cli` 就能在命令行跑完整个流程并打印时间线；PySide6 界面是 Plan 2。

**技术栈：** Python 3.14（最低 3.10）、`httpx`（直打 opencode 的 HTTP/SSE，官方无 Python SDK）、`subprocess`、pytest 9；开发与测试机为 Arch Linux（真实 bash + 项目内 `tools/shellcheck`）。

**规格：** `docs/superpowers/specs/2026-09-17-tu-shell-agent-design.md`（技术栈修订见规格 §19）

> **取代关系：** 本计划取代被删除的 TypeScript 版计划（`2026-09-17-tu-shell-agent-engine.md`，见 git 历史）。架构与任务边界一致，只是语言与库换了。

---

## 前置准备（人工，一次性）

项目已在 `/home/vconlln/my-agent`，虚拟环境已建（`.venv`，Python 3.14.7）。装依赖：

```bash
cd /home/vconlln/my-agent
.venv/bin/python -m pip install httpx pytest
```

预期末尾出现 `Successfully installed ... httpx ... pytest ...`。（PySide6 / pytest-qt / PyInstaller 属于 Plan 2 与 M4，本计划不需要。）

确认两个外部依赖就绪（本机走项目内的静态二进制，避免 sudo）：

```bash
cd /home/vconlln/my-agent
./tools/shellcheck --version          # 预期 version: 0.11.0
/usr/bin/bash --version | head -1     # 预期 GNU bash, version 5.x
```

> 本机没有 Windows 的 Git Bash，因此引擎把 bash 解释器路径做成配置项：Linux 默认 `/usr/bin/bash`，Windows 默认探测 Git Bash。这是可移植性的落点，不是临时妥协。

## 文件结构

每个文件一个职责，先锁定边界再拆任务。

```
my-agent/
  pyproject.toml                        项目元数据 + pytest 配置
  .gitignore                            .venv/、__pycache__/、.pytest_cache/、tools/、.toolhome/
  tools/shellcheck                      已就位（0.11.0 静态二进制，git 忽略）
  tu_shell_agent/
    __init__.py                         版本号
    types.py                            全部共享类型（冻结 dataclass）+ 阻断判定
    ports.py                            四个 typing.Protocol 端口
    template_store/
      __init__.py
      render.py                         {{name}} 渲染、未声明即报错、LF 归一化
      builtins.py                       3 套内置模板
      store.py                          模板 CRUD + index.json
    run_store/
      __init__.py
      layout.py                         运行目录布局与路径推导
      store.py                          每轮落盘、meta.json
    shell_toolchain/
      __init__.py
      detect.py                         环境探测（opencode/bash/shellcheck）
      shellcheck.py                     调 shellcheck 并归一化报告与退出码
      execute.py                        spawn bash、流式输出、超时、杀进程树
      facade.py                         组装成 ToolchainPort 的实现
    opencode_adapter/
      __init__.py                       组装成 OpencodePort 的实现（含自动拒绝权限）
      agent_file.py                     生成项目级 agent 定义（逐键 deny）
      server.py                         起停 opencode serve（端口、密码、日志、health 轮询）
      events.py                         SSE 解析 + 事件取值助手
    orchestrator/
      __init__.py
      contract.py                       锚点/空/超长/CRLF 契约校验
      prompt.py                         输出 schema + 每轮 user message 组装
      loop.py                           主状态机（唯一"知道流程"的地方）
    cli.py                              开发驱动：跑完整流程并打印时间线
  tests/
    conftest.py                         公共夹具
    test_types.py  test_render.py  test_template_store.py  test_run_store.py
    test_detect.py  test_shellcheck.py  test_execute.py
    test_contract.py  test_prompt.py  test_agent_file.py
    test_events.py  test_server.py  test_loop.py
    test_e2e_offline.py  test_e2e_live.py
  test_fixtures/
    plan-simple.md                      简单方案（真实冒烟用）
```

**分层规则（实现时不得违反，审查者会检查）：**
- `orchestrator/` 与两个 `*_store/` **不得** import `subprocess`、`httpx`、PySide6；外部世界一律经 `ports.py` 注入。
- 只有 `shell_toolchain/` 与 `opencode_adapter/` 允许 `subprocess` 与网络。
- `types.py` 是类型的唯一来源；任何模块不得私自定义与它重复的结构。
- 所有跨模块数据用 `types.py` 里的**冻结 dataclass**；不用裸 dict 传业务数据（`RunEvent.payload` 是唯一例外，它是给 UI 的事件载荷）。

---

### 任务 1：项目骨架与共享类型

**文件：**
- 创建：`pyproject.toml`、`tu_shell_agent/__init__.py`、`tu_shell_agent/types.py`、`tu_shell_agent/ports.py`、`tests/test_types.py`
- 修改：`.gitignore`

- [ ] **步骤 1：写失败的测试**

```python
# tests/test_types.py
from tu_shell_agent.types import SEVERITY_RANK, blocks_run


def test_error_is_more_severe_than_warning():
    assert SEVERITY_RANK["error"] > SEVERITY_RANK["warning"]


def test_blocking_level_warning_blocks_error_and_warning_but_not_info():
    assert blocks_run("error", "warning") is True
    assert blocks_run("warning", "warning") is True
    assert blocks_run("info", "warning") is False
    assert blocks_run("style", "warning") is False


def test_blocking_level_error_only_blocks_error():
    assert blocks_run("error", "error") is True
    assert blocks_run("warning", "error") is False
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/python -m pytest tests/test_types.py -q`
预期：FAIL，`ModuleNotFoundError: No module named 'tu_shell_agent'`

- [ ] **步骤 3：写最小实现**

```toml
# pyproject.toml
[project]
name = "tu-shell-agent"
version = "0.1.0"
description = "把方案文档变成可执行 shell 脚本，并用 Git Bash 执行、shellcheck 校验"
requires-python = ">=3.10"
dependencies = ["httpx>=0.27"]

[project.optional-dependencies]
dev = ["pytest>=8"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
addopts = "-q"
```

```python
# tu_shell_agent/__init__.py
__version__ = "0.1.0"
```

```python
# tu_shell_agent/types.py
"""全部共享类型。本模块是类型的唯一来源，其它模块不得重复定义这些结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Severity = Literal["error", "warning", "info", "style"]

SEVERITY_RANK: dict[Severity, int] = {"error": 3, "warning": 2, "info": 1, "style": 0}


def blocks_run(level: Severity, blocking_level: Severity) -> bool:
    """这条 shellcheck 发现是否应当阻断本次运行（级别 >= 配置的阻断级别）。"""
    return SEVERITY_RANK[level] >= SEVERITY_RANK[blocking_level]


Stage = Literal["contract", "shellcheck", "execute"]
RunOutcome = Literal["succeeded", "needs_human", "cancelled", "aborted_dependency"]
ContractFailure = Literal["empty", "too_large", "has_crlf", "missing_anchor"]


@dataclass(frozen=True, slots=True)
class ShellcheckFinding:
    code: str
    line: int
    column: int
    level: Severity
    message: str


@dataclass(frozen=True, slots=True)
class ExecuteResult:
    exit_code: int | None
    signal: int | None
    timed_out: bool
    cancelled: bool
    duration_ms: int
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class ContractResult:
    ok: bool
    reason: ContractFailure | None
    missing_anchors: tuple[str, ...]
    size_bytes: int


@dataclass(frozen=True, slots=True)
class GeneratedScript:
    script: str
    notes: str
    assumptions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ContractEvidence:
    reason: ContractFailure
    missing_anchors: tuple[str, ...] = ()
    # message 用于「这一轮根本没产出脚本」（结构化输出失败/超时），此时 reason 记 empty
    message: str | None = None


@dataclass(frozen=True, slots=True)
class ExecuteEvidence:
    exit_code: int | None
    timed_out: bool
    stdout_tail: str
    stderr_tail: str
    duration_ms: int


@dataclass(frozen=True, slots=True)
class FailureEvidence:
    round: int
    stage: Stage
    contract: ContractEvidence | None = None
    shellcheck: tuple[ShellcheckFinding, ...] = ()
    execute: ExecuteEvidence | None = None


@dataclass(frozen=True, slots=True)
class DetectedTool:
    path: str
    version: str


@dataclass(frozen=True, slots=True)
class DetectionReport:
    opencode: DetectedTool | None
    bash: DetectedTool | None
    shellcheck: DetectedTool | None
    problems: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RunConfig:
    run_root: str
    max_rounds: int = 3
    generate_timeout_ms: int = 300_000
    execute_timeout_ms: int = 120_000
    blocking_level: Severity = "warning"
    bash_path: str | None = None
    shellcheck_path: str | None = None
    opencode_path: str | None = None
    model: str | None = None


@dataclass(frozen=True, slots=True)
class RunEvent:
    """给 UI 的事件。type 取值见 loop.py 的 emit 调用点。"""

    type: str
    round: int = 0
    payload: dict[str, Any] = field(default_factory=dict)
```

```python
# tu_shell_agent/ports.py
"""引擎与外界的四个端口。orchestrator 只认这些 Protocol，不认具体实现。"""

from __future__ import annotations

from typing import Any, Callable, Protocol

from .types import (
    DetectionReport,
    ExecuteResult,
    GeneratedScript,
    ShellcheckFinding,
)


class ToolchainPort(Protocol):
    def detect(self) -> DetectionReport: ...

    def shellcheck(self, script_path: str) -> tuple[list[ShellcheckFinding], int, str]:
        """返回 (发现列表, shellcheck 退出码, 原始 json1 文本)。"""
        ...

    def execute(
        self,
        script_path: str,
        cwd: str,
        timeout_ms: int,
        cancel: Any = None,
        on_stdout: Callable[[str], None] | None = None,
        on_stderr: Callable[[str], None] | None = None,
    ) -> ExecuteResult: ...


class OpencodePort(Protocol):
    def start(self, run_dir: str, agent_name: str, model: str | None) -> str:
        """起 server、建会话，返回 session_id。"""
        ...

    def generate(
        self,
        session_id: str,
        message: str,
        schema: dict[str, Any],
        timeout_ms: int,
        on_delta: Callable[[str], None] | None = None,
        cancel: Any = None,
    ) -> GeneratedScript: ...

    def abort(self, session_id: str) -> None: ...

    def dispose(self) -> None: ...


class ConfirmPort(Protocol):
    def confirm(self, round_no: int, script_path: str, script: str, trusted: bool) -> bool:
        """返回 False 表示用户拒绝执行本次脚本。"""
        ...


class RunStorePort(Protocol):
    run_dir: str

    def write_script(self, round_no: int, script: str) -> str: ...

    def write_attempt(self, round_no: int, files: dict[str, str]) -> None: ...

    def write_meta(self, patch: dict[str, Any]) -> None: ...
```

```gitignore
# .gitignore（在既有内容后追加）
.venv/
__pycache__/
*.pyc
.pytest_cache/
```

- [ ] **步骤 4：运行测试验证通过**

运行：`.venv/bin/python -m pytest tests/test_types.py -q`
预期：PASS（3 passed）

- [ ] **步骤 5：Commit**

```bash
git add pyproject.toml .gitignore tu_shell_agent/__init__.py tu_shell_agent/types.py tu_shell_agent/ports.py tests/test_types.py
git commit -m "feat(engine): Python 骨架与共享类型

冻结 dataclass 承载跨模块数据，四个 Protocol 定义引擎与外界的缝；
orchestrator 之后只依赖端口，因此可在 Linux 上无界面单测。"
```

---

### 任务 2：模板渲染

**文件：**
- 创建：`tu_shell_agent/template_store/__init__.py`、`tu_shell_agent/template_store/render.py`、`tests/test_render.py`

- [ ] **步骤 1：写失败的测试**

```python
# tests/test_render.py
import pytest

from tu_shell_agent.template_store.render import (
    PlaceholderSpec,
    declared_names,
    render_template,
    to_lf,
)

BODY = "#!/usr/bin/env bash\nset -euo pipefail\n# @@TU:BODY@@\necho {{greeting:hello}} {{name}}\n"


def test_replaces_declared_placeholders_using_defaults():
    out = render_template(
        BODY,
        [PlaceholderSpec(name="name", default="world"), PlaceholderSpec(name="greeting")],
        {},
    )
    assert "echo hello world" in out


def test_caller_values_win_over_defaults():
    out = render_template(
        BODY,
        [PlaceholderSpec(name="name"), PlaceholderSpec(name="greeting")],
        {"name": "张三", "greeting": "你好"},
    )
    assert "echo 你好 张三" in out


def test_undeclared_placeholder_raises_instead_of_silently_emptying():
    with pytest.raises(ValueError, match="未声明的占位符.*name"):
        render_template(BODY, [PlaceholderSpec(name="greeting")], {})


def test_crlf_is_normalized_to_lf():
    assert to_lf("line1\r\nline2\r\n") == "line1\nline2\n"


def test_declared_names_lists_placeholders_in_order():
    assert declared_names("a {{x}} b {{y:1}} c {{x}}") == ["x", "y"]
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/python -m pytest tests/test_render.py -q`
预期：FAIL，`ModuleNotFoundError: No module named 'tu_shell_agent.template_store'`

- [ ] **步骤 3：写最小实现**

```python
# tu_shell_agent/template_store/__init__.py
```

```python
# tu_shell_agent/template_store/render.py
"""占位符渲染。只做字符串替换，不引入模板引擎（规格 §8，YAGNI）。"""

from __future__ import annotations

import re
from dataclasses import dataclass

_PLACEHOLDER = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)(?::([^}]*))?\}\}")


@dataclass(frozen=True, slots=True)
class PlaceholderSpec:
    name: str
    default: str | None = None
    description: str | None = None


def to_lf(text: str) -> str:
    """CRLF/CR → LF。Git Bash 遇到 \\r 会报错，入口处统一归一化。"""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def render_template(
    body: str,
    declared: list[PlaceholderSpec],
    values: dict[str, str],
) -> str:
    """未在元数据里声明的占位符 → 抛错，避免生成一个缺参数的脚本。"""
    by_name = {spec.name: spec for spec in declared}
    undeclared: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        name, inline_default = match.group(1), match.group(2)
        spec = by_name.get(name)
        if spec is None:
            undeclared.add(name)
            return ""
        if name in values:
            return values[name]
        if spec.default is not None:
            return spec.default
        return inline_default or ""

    rendered = _PLACEHOLDER.sub(replace, to_lf(body))

    if undeclared:
        raise ValueError(f"模板含未声明的占位符：{', '.join(sorted(undeclared))}")
    return rendered


def declared_names(body: str) -> list[str]:
    """取出模板里声明的占位符名（供 UI 生成表单），按首次出现顺序去重。"""
    seen: dict[str, None] = {}
    for match in _PLACEHOLDER.finditer(to_lf(body)):
        seen.setdefault(match.group(1), None)
    return list(seen)
```

- [ ] **步骤 4：运行测试验证通过**

运行：`.venv/bin/python -m pytest tests/test_render.py -q`
预期：PASS（5 passed）

- [ ] **步骤 5：Commit**

```bash
git add tu_shell_agent/template_store/ tests/test_render.py
git commit -m "feat(template): 占位符渲染与 LF 归一化

未声明占位符直接报错；CRLF 在入口归一化，避免 Git Bash 报 \\r 错。"
```

---

### 任务 3：内置模板与模板库

**文件：**
- 创建：`tu_shell_agent/template_store/builtins.py`、`tu_shell_agent/template_store/store.py`、`tests/test_template_store.py`

- [ ] **步骤 1：写失败的测试**

```python
# tests/test_template_store.py
import json

import pytest

from tu_shell_agent.template_store.builtins import BUILTIN_TEMPLATES
from tu_shell_agent.template_store.store import TemplateInput, TemplateStore


def test_first_open_writes_three_builtin_templates(tmp_path):
    store = TemplateStore(str(tmp_path))
    ids = sorted(meta.id for meta in store.list())
    assert ids == ["args-batch", "logged-errors", "single"]
    assert len(BUILTIN_TEMPLATES) == 3


def test_every_builtin_template_has_anchor(tmp_path):
    store = TemplateStore(str(tmp_path))
    for meta in store.list():
        assert "# @@TU:" in store.read(meta.id)


def test_save_then_read_round_trips_and_updates_index(tmp_path):
    store = TemplateStore(str(tmp_path))
    store.save(
        TemplateInput(
            id="mine",
            name="我的模板",
            description="",
            trusted=False,
            placeholders=[],
            body="echo hi # @@TU:BODY@@\n",
        )
    )
    assert "echo hi" in store.read("mine")
    index = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert "mine" in [item["id"] for item in index["templates"]]


def test_remove_deletes_body_file(tmp_path):
    store = TemplateStore(str(tmp_path))
    store.save(
        TemplateInput(
            id="gone", name="g", description="", trusted=False,
            placeholders=[], body="# @@TU:BODY@@\n",
        )
    )
    store.remove("gone")
    with pytest.raises(FileNotFoundError):
        store.read("gone")


def test_illegal_id_is_rejected(tmp_path):
    store = TemplateStore(str(tmp_path))
    with pytest.raises(ValueError, match="非法模板 id"):
        store.save(
            TemplateInput(
                id="../escape", name="x", description="", trusted=False,
                placeholders=[], body="",
            )
        )


def test_hand_dropped_template_file_gets_registered(tmp_path):
    (tmp_path / "hand.tpl.sh").write_text("echo hand # @@TU:BODY@@\n", encoding="utf-8")
    store = TemplateStore(str(tmp_path))
    assert "hand" in [meta.id for meta in store.list()]


def test_set_trusted_persists(tmp_path):
    store = TemplateStore(str(tmp_path))
    store.set_trusted("single", True)
    assert TemplateStore(str(tmp_path)).get("single").trusted is True


def test_save_on_fresh_store_still_seeds_builtins(tmp_path):
    store = TemplateStore(str(tmp_path))
    store.save(
        TemplateInput(
            id="mine", name="我的", description="", trusted=False,
            placeholders=[], body="# @@TU:BODY@@\n",
        )
    )
    ids = {meta.id for meta in store.list()}
    assert {"single", "args-batch", "logged-errors"} <= ids
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/python -m pytest tests/test_template_store.py -q`
预期：FAIL，`ModuleNotFoundError: No module named 'tu_shell_agent.template_store.builtins'`

- [ ] **步骤 3：写最小实现**

```python
# tu_shell_agent/template_store/builtins.py
"""三套内置模板（规格 §8）。锚点形如 # @@TU:NAME@@，生成结果必须保留。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BuiltinTemplate:
    id: str
    name: str
    description: str
    body: str


SINGLE = """#!/usr/bin/env bash
set -euo pipefail

# @@TU:BODY@@

main() {
  :
}

main "$@"
"""

ARGS_BATCH = """#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
用法: {{script_name:task.sh}} [-n] [-d 目录]
USAGE
}

DIR="{{work_dir:.}}"
DRY_RUN=0
while getopts ":nd:h" opt; do
  case "$opt" in
    n) DRY_RUN=1 ;;
    d) DIR="$OPTARG" ;;
    h) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done
shift $((OPTIND - 1))

if [[ ! -d "$DIR" ]]; then
  echo "目录不存在: $DIR" >&2
  exit 1
fi

# @@TU:BODY@@
"""

LOGGED_ERRORS = """#!/usr/bin/env bash
set -euo pipefail

LOG_PREFIX="{{log_prefix:task}}"
WORK_DIR=""
cleanup() {
  [[ -n "$WORK_DIR" && -d "$WORK_DIR" ]] && rm -rf "$WORK_DIR"
}
trap cleanup EXIT
trap 'echo "[$LOG_PREFIX] 第 $LINENO 行失败" >&2' ERR

log() { printf '[%s] %s\\n' "$LOG_PREFIX" "$*"; }
die() { printf '[%s] 错误: %s\\n' "$LOG_PREFIX" "$*" >&2; exit 1; }

WORK_DIR="$(mktemp -d)"
log "临时目录 $WORK_DIR"

# @@TU:BODY@@

log "完成"
"""

BUILTIN_TEMPLATES: tuple[BuiltinTemplate, ...] = (
    BuiltinTemplate(
        id="single",
        name="单命令执行",
        description="最小骨架：shebang + set -euo pipefail + main()",
        body=SINGLE,
    ),
    BuiltinTemplate(
        id="args-batch",
        name="参数解析批处理",
        description="getopts 解析、目录校验、usage",
        body=ARGS_BATCH,
    ),
    BuiltinTemplate(
        id="logged-errors",
        name="带日志与错误处理",
        description="log/die、trap ERR、mktemp 临时目录与退出清理",
        body=LOGGED_ERRORS,
    ),
)
```

```python
# tu_shell_agent/template_store/store.py
"""模板库：index.json + <id>.tpl.sh。首次打开写入内置模板，之后目录归用户所有。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .builtins import BUILTIN_TEMPLATES
from .render import PlaceholderSpec, declared_names

_ID_OK = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass(slots=True)
class TemplateMeta:
    id: str
    name: str
    description: str = ""
    trusted: bool = False
    placeholders: list[PlaceholderSpec] = field(default_factory=list)
    updated_at: str | None = None


@dataclass(slots=True)
class TemplateInput:
    id: str
    name: str
    body: str
    description: str = ""
    trusted: bool = False
    placeholders: list[PlaceholderSpec] = field(default_factory=list)


class TemplateStore:
    def __init__(self, directory: str) -> None:
        self.directory = Path(directory)

    # ── 内部 ────────────────────────────────────────────────────────────
    @property
    def _index_path(self) -> Path:
        return self.directory / "index.json"

    def _body_path(self, template_id: str) -> Path:
        return self.directory / f"{template_id}.tpl.sh"

    def _read_index(self) -> list[TemplateMeta]:
        if not self._index_path.exists():
            return []
        raw = json.loads(self._index_path.read_text(encoding="utf-8"))
        metas: list[TemplateMeta] = []
        for item in raw.get("templates", []):
            metas.append(
                TemplateMeta(
                    id=item["id"],
                    name=item.get("name", item["id"]),
                    description=item.get("description", ""),
                    trusted=bool(item.get("trusted", False)),
                    placeholders=[
                        PlaceholderSpec(
                            name=p["name"],
                            default=p.get("default"),
                            description=p.get("description"),
                        )
                        for p in item.get("placeholders", [])
                    ],
                    updated_at=item.get("updatedAt"),
                )
            )
        return metas

    def _write_index(self, metas: list[TemplateMeta]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "templates": [
                {
                    "id": m.id,
                    "name": m.name,
                    "description": m.description,
                    "trusted": m.trusted,
                    "placeholders": [
                        {"name": p.name, "default": p.default, "description": p.description}
                        for p in m.placeholders
                    ],
                    "updatedAt": m.updated_at,
                }
                for m in metas
            ]
        }
        self._index_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    # ── 对外 ────────────────────────────────────────────────────────────
    def list(self) -> list[TemplateMeta]:
        """首次调用写入内置模板；顺带登记目录里手工放进去的 .tpl.sh。"""
        self.directory.mkdir(parents=True, exist_ok=True)
        metas = self._read_index()

        if not metas:
            for builtin in BUILTIN_TEMPLATES:
                self._body_path(builtin.id).write_text(builtin.body, encoding="utf-8")
                metas.append(
                    TemplateMeta(
                        id=builtin.id,
                        name=builtin.name,
                        description=builtin.description,
                        placeholders=[PlaceholderSpec(name=n) for n in declared_names(builtin.body)],
                        updated_at=datetime.now(timezone.utc).isoformat(),
                    )
                )
            self._write_index(metas)

        known = {m.id for m in metas}
        dirty = False
        for path in sorted(self.directory.glob("*.tpl.sh")):
            template_id = path.name[: -len(".tpl.sh")]
            if template_id in known:
                continue
            metas.append(
                TemplateMeta(
                    id=template_id,
                    name=template_id,
                    placeholders=[
                        PlaceholderSpec(name=n)
                        for n in declared_names(path.read_text(encoding="utf-8"))
                    ],
                )
            )
            known.add(template_id)
            dirty = True
        if dirty:
            self._write_index(metas)

        return metas

    def get(self, template_id: str) -> TemplateMeta:
        for meta in self.list():
            if meta.id == template_id:
                return meta
        raise KeyError(f"模板不存在：{template_id}")

    def read(self, template_id: str) -> str:
        return self._body_path(template_id).read_text(encoding="utf-8")

    def save(self, item: TemplateInput) -> TemplateMeta:
        if not _ID_OK.match(item.id):
            raise ValueError(f"非法模板 id：{item.id}")
        self.directory.mkdir(parents=True, exist_ok=True)
        self._body_path(item.id).write_text(item.body, encoding="utf-8")
        meta = TemplateMeta(
            id=item.id,
            name=item.name,
            description=item.description,
            trusted=item.trusted,
            placeholders=item.placeholders,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        metas = [m for m in self.list() if m.id != item.id]
        metas.append(meta)
        self._write_index(metas)
        return meta

    def remove(self, template_id: str) -> None:
        self._body_path(template_id).unlink(missing_ok=True)
        self._write_index([m for m in self._read_index() if m.id != template_id])

    def set_trusted(self, template_id: str, trusted: bool) -> None:
        # 必须走 list()：index.json 是惰性创建的，_read_index() 在全新 store 上必然为空。
        metas = self.list()
        for meta in metas:
            if meta.id == template_id:
                meta.trusted = trusted
                self._write_index(metas)
                return
        raise KeyError(f"模板不存在：{template_id}")
```

- [ ] **步骤 4：运行测试验证通过**

运行：`.venv/bin/python -m pytest tests/test_template_store.py -q`
预期：PASS（7 passed）

- [ ] **步骤 5：Commit**

```bash
git add tu_shell_agent/template_store/builtins.py tu_shell_agent/template_store/store.py tests/test_template_store.py
git commit -m "feat(template): 三套内置模板与模板库持久化

内置覆盖单命令/参数解析/日志与错误处理；index.json 可重建，
目录里手工放置的 .tpl.sh 会被自动登记。"
```

---

### 任务 4：运行目录与 run_store

**文件：**
- 创建：`tu_shell_agent/run_store/__init__.py`、`tu_shell_agent/run_store/layout.py`、`tu_shell_agent/run_store/store.py`、`tests/test_run_store.py`

- [ ] **步骤 1：写失败的测试**

```python
# tests/test_run_store.py
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


def test_write_meta_creates_run_dir_when_called_first(tmp_path):
    run_dir = tmp_path / "fresh"
    store = RunStore(str(run_dir))
    store.write_meta({"outcome": "aborted_dependency", "rounds": 0})
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["outcome"] == "aborted_dependency"


def test_init_creates_agent_directory(tmp_path):
    store = RunStore(str(tmp_path / "r1"))
    store.init()
    assert (tmp_path / "r1" / ".opencode" / "agents").is_dir()
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/python -m pytest tests/test_run_store.py -q`
预期：FAIL，`ModuleNotFoundError: No module named 'tu_shell_agent.run_store'`

- [ ] **步骤 3：写最小实现**

```python
# tu_shell_agent/run_store/__init__.py
```

```python
# tu_shell_agent/run_store/layout.py
"""运行目录布局（规格 §10）。"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path

AGENT_RELATIVE_PATH = ".opencode/agents/tu-shell-writer.md"


def make_run_id(at: datetime | None = None, salt: str | None = None) -> str:
    """时间戳用 UTC：否则同一条测试在不同时区会得到不同前缀。"""
    moment = at or datetime.now(timezone.utc)
    suffix = salt if salt is not None else secrets.token_hex(2)
    return f"{moment:%Y%m%d-%H%M%S}-{suffix}"


def run_dir_for(run_root: str, run_id: str) -> str:
    return str(Path(run_root) / run_id)


def attempt_dir(run_dir: str, round_no: int) -> str:
    return str(Path(run_dir) / "attempts" / str(round_no))
```

```python
# tu_shell_agent/run_store/store.py
"""每轮产物落盘。script.sh 在运行目录根与 attempts/<n>/ 双写，便于回放。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..template_store.render import to_lf
from .layout import attempt_dir


class RunStore:
    def __init__(self, run_dir: str) -> None:
        self.run_dir = run_dir

    def init(self) -> None:
        Path(self.run_dir, ".opencode", "agents").mkdir(parents=True, exist_ok=True)

    def write_script(self, round_no: int, script: str) -> str:
        text = to_lf(script)
        round_path = Path(attempt_dir(self.run_dir, round_no))
        round_path.mkdir(parents=True, exist_ok=True)
        (round_path / "script.sh").write_text(text, encoding="utf-8")
        root_path = Path(self.run_dir) / "script.sh"
        root_path.write_text(text, encoding="utf-8")
        return str(root_path)

    def write_attempt(self, round_no: int, files: dict[str, str]) -> None:
        target = Path(attempt_dir(self.run_dir, round_no))
        target.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            (target / name).write_text(content, encoding="utf-8")

    def write_meta(self, patch: dict[str, Any]) -> None:
        path = Path(self.run_dir) / "meta.json"
        # 与 write_script / write_attempt 保持一致的自我修复：三者都不该假定调用方先调过 init()。
        path.parent.mkdir(parents=True, exist_ok=True)
        current: dict[str, Any] = {}
        if path.exists():
            current = json.loads(path.read_text(encoding="utf-8"))
        current.update(patch)
        path.write_text(
            json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
```

- [ ] **步骤 4：运行测试验证通过**

运行：`.venv/bin/python -m pytest tests/test_run_store.py -q`
预期：PASS（6 passed）

- [ ] **步骤 5：Commit**

```bash
git add tu_shell_agent/run_store/ tests/test_run_store.py
git commit -m "feat(run-store): 运行目录布局与每轮落盘

script.sh 在运行目录根与 attempts/<n>/ 双写；meta.json 支持增量合并；
写盘统一 LF 归一化。"
```

---

### 任务 5：环境探测

**文件：**
- 创建：`tu_shell_agent/shell_toolchain/__init__.py`、`tu_shell_agent/shell_toolchain/detect.py`、`tests/test_detect.py`

- [ ] **步骤 1：写失败的测试**

```python
# tests/test_detect.py
import shutil

import pytest

from tu_shell_agent.shell_toolchain.detect import (
    DetectDeps,
    candidate_paths,
    detect_all,
    is_at_least,
    parse_version,
    system_deps,
)

LINUX_VERSIONS = {
    "/usr/bin/bash": "GNU bash, version 5.2.037-1 (x86_64-pc-linux-gnu)",
    "/usr/bin/shellcheck": "ShellCheck - shell script analysis tool\nversion: 0.11.0",
    "/usr/bin/opencode": "1.18.31",
}


def test_linux_resolves_all_three_from_path():
    report = detect_all(
        DetectDeps(
            platform="linux",
            exists=lambda path: True,
            which=lambda name: {
                "bash": "/usr/bin/bash",
                "shellcheck": "/usr/bin/shellcheck",
                "opencode": "/usr/bin/opencode",
            }.get(name),
            run_version=lambda path: LINUX_VERSIONS.get(path, ""),
        )
    )
    assert report.bash.path == "/usr/bin/bash"
    assert report.shellcheck.version == "0.11.0"
    assert report.opencode.version == "1.18.31"
    assert report.problems == ()


def test_windows_finds_git_bash_by_candidate_order():
    git_bash = "C:/Program Files/Git/bin/bash.exe"
    report = detect_all(
        DetectDeps(
            platform="win32",
            exists=lambda path: path == git_bash,
            which=lambda name: None,
            run_version=lambda path: "GNU bash, version 5.2.37(1)-release",
        )
    )
    assert report.bash.path == git_bash


def test_missing_tools_produce_actionable_problems():
    report = detect_all(
        DetectDeps(platform="win32", exists=lambda path: False, which=lambda name: None, run_version=lambda path: "")
    )
    joined = "\n".join(report.problems)
    assert report.opencode is None
    assert "opencode" in joined
    assert "Git Bash" in joined
    assert "shellcheck" in joined


def test_overrides_win_over_autodetection():
    report = detect_all(
        DetectDeps(
            platform="linux",
            exists=lambda path: path == "/opt/custom/bash",
            which=lambda name: "/usr/bin/bash" if name == "bash" else None,
            run_version=lambda path: "GNU bash, version 5.2.37(1)-release",
            overrides={"bash": "/opt/custom/bash"},
        )
    )
    assert report.bash.path == "/opt/custom/bash"


def test_candidate_paths_cover_both_program_files_locations():
    paths = candidate_paths("win32", "bash")
    assert "C:/Program Files/Git/bin/bash.exe" in paths
    assert "C:/Program Files (x86)/Git/bin/bash.exe" in paths


def test_old_opencode_version_is_flagged():
    report = detect_all(
        DetectDeps(
            platform="linux",
            exists=lambda path: True,
            which=lambda name: f"/usr/bin/{name}",
            run_version=lambda path: "1.0.140" if "opencode" in path else "ShellCheck\nversion: 0.11.0",
        )
    )
    assert any("版本过低" in problem for problem in report.problems)


def test_parse_version_extracts_semver():
    assert parse_version("shellcheck", "ShellCheck\nversion: 0.11.0") == "0.11.0"
    assert parse_version("bash", "GNU bash, version 5.2.37(1)-release (x86_64)") == "5.2.37"


def test_is_at_least_compares_three_segments():
    assert is_at_least("1.18.31", "1.1.1") is True
    assert is_at_least("1.0.9", "1.1.1") is False
    assert is_at_least("1.1.1", "1.1.1") is True


def test_system_deps_probes_version_in_c_locale():
    """版本探测必须与本地化无关：中文 locale 下 `bash --version` 输出「GNU bash，版本 5.3.15」，
    英文正则会解析出 unknown。这条在中文机器上能真实抓住该缺陷。"""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("本机没有 bash")
    output = system_deps().run_version(bash)
    assert parse_version("bash", output) != "unknown"
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/python -m pytest tests/test_detect.py -q`
预期：FAIL，`ModuleNotFoundError: No module named 'tu_shell_agent.shell_toolchain'`

- [ ] **步骤 3：写最小实现**

```python
# tu_shell_agent/shell_toolchain/__init__.py
```

```python
# tu_shell_agent/shell_toolchain/detect.py
"""环境探测（规格 §9）。平台分支集中在本模块，缺失项汇总为中文可执行指引。"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Callable

from ..types import DetectedTool, DetectionReport

ToolName = str  # "opencode" | "bash" | "shellcheck"

_VERSION_PATTERNS: dict[str, re.Pattern[str]] = {
    "shellcheck": re.compile(r"version:\s*([0-9][^\s]*)"),
    "bash": re.compile(r"version\s+([0-9][^\s(]*)"),
    "opencode": re.compile(r"([0-9]+\.[0-9]+\.[0-9]+)"),
}

_INSTALL_HINTS = {
    "opencode": (
        "未找到 opencode：请安装 Windows 原生 opencode"
        "（choco install opencode / scoop install opencode / npm i -g opencode-ai），"
        "本应用不支持仅存在于 WSL 的安装"
    ),
    "bash": "未找到 Git Bash：请安装 Git for Windows（提供 bash.exe）",
    "shellcheck": "未找到 shellcheck：winget install --id koalaman.shellcheck，或使用官方 release zip",
}

MIN_OPENCODE_VERSION = "1.1.1"


@dataclass(slots=True)
class DetectDeps:
    platform: str
    exists: Callable[[str], bool]
    which: Callable[[str], str | None]
    run_version: Callable[[str], str]
    overrides: dict[str, str] = field(default_factory=dict)


def candidate_paths(platform: str, tool: str) -> list[str]:
    """Windows 上的固定候选位置（Git for Windows 的两种安装路径等）。"""
    if platform != "win32":
        return []
    if tool == "bash":
        return [
            "C:/Program Files/Git/bin/bash.exe",
            "C:/Program Files (x86)/Git/bin/bash.exe",
        ]
    if tool == "opencode":
        appdata = os.environ.get("APPDATA", "")
        if not appdata:
            return []
        return [
            os.path.join(appdata, "npm", "opencode.cmd"),
            os.path.join(appdata, "npm", "opencode"),
        ]
    return []


def parse_version(tool: str, output: str) -> str:
    pattern = _VERSION_PATTERNS.get(tool)
    if pattern is None:
        return "unknown"
    match = pattern.search(output)
    return match.group(1) if match else "unknown"


def is_at_least(version: str, minimum: str) -> bool:
    def parts(value: str) -> list[int]:
        out: list[int] = []
        for chunk in value.split(".")[:3]:
            digits = re.match(r"\d+", chunk)
            out.append(int(digits.group(0)) if digits else 0)
        return out + [0] * (3 - len(out))

    left, right = parts(version), parts(minimum)
    for a, b in zip(left, right):
        if a != b:
            return a > b
    return True


def _resolve(tool: str, deps: DetectDeps) -> DetectedTool | None:
    candidates: list[str] = []
    override = deps.overrides.get(tool)
    if override:
        candidates.append(override)
    candidates.extend(candidate_paths(deps.platform, tool))
    from_path = deps.which(tool)
    if from_path:
        candidates.append(from_path)

    for path in candidates:
        if not deps.exists(path):
            continue
        return DetectedTool(path=path, version=parse_version(tool, deps.run_version(path)))
    return None


def detect_all(deps: DetectDeps) -> DetectionReport:
    opencode = _resolve("opencode", deps)
    bash = _resolve("bash", deps)
    shellcheck = _resolve("shellcheck", deps)

    problems: list[str] = []
    if opencode is None:
        problems.append(_INSTALL_HINTS["opencode"])
    elif not is_at_least(opencode.version, MIN_OPENCODE_VERSION):
        problems.append(
            f"opencode 版本过低（{opencode.version}）：需要 >= {MIN_OPENCODE_VERSION} 才有 permission 配置"
        )
    if bash is None:
        problems.append(_INSTALL_HINTS["bash"])
    if shellcheck is None:
        problems.append(_INSTALL_HINTS["shellcheck"])

    return DetectionReport(
        opencode=opencode,
        bash=bash,
        shellcheck=shellcheck,
        problems=tuple(problems),
    )


def system_deps(overrides: dict[str, str] | None = None) -> DetectDeps:
    """生产环境的依赖实现：走 PATH 与真实进程。"""

    def run_version(path: str) -> str:
        # 版本探测必须与本地化无关：中文 locale 下 `bash --version` 会输出
        # 「GNU bash，版本 5.3.15」，规格里的英文正则就解析不出来（返回 unknown）。
        env = {**os.environ, "LC_ALL": "C", "LANG": "C"}
        try:
            completed = subprocess.run(
                [path, "--version"], capture_output=True, text=True, timeout=20, env=env
            )
            return f"{completed.stdout}\n{completed.stderr}"
        except (OSError, subprocess.SubprocessError):
            return ""

    return DetectDeps(
        platform="win32" if os.name == "nt" else "linux",
        exists=lambda path: os.path.isfile(path),
        which=lambda name: shutil.which(name),
        run_version=run_version,
        overrides=dict(overrides or {}),
    )
```

- [ ] **步骤 4：运行测试验证通过**

运行：`.venv/bin/python -m pytest tests/test_detect.py -q`
预期：PASS（8 passed）

- [ ] **步骤 5：Commit**

```bash
git add tu_shell_agent/shell_toolchain/ tests/test_detect.py
git commit -m "feat(toolchain): 环境探测与缺失项诊断

平台分支集中在 candidate_paths；缺失项汇总为中文可执行指引，不抛异常；
opencode 低于 1.1.1 时明确告警（permission 配置的前提）。"
```

---

### 任务 6：shellcheck 封装

**文件：**
- 创建：`tu_shell_agent/shell_toolchain/shellcheck.py`、`tests/conftest.py`、`tests/test_shellcheck.py`

- [ ] **步骤 1：写失败的测试**

```python
# tests/conftest.py
import os
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def shellcheck_path() -> str:
    candidate = os.environ.get("TU_SHELLCHECK") or str(REPO_ROOT / "tools" / "shellcheck")
    if Path(candidate).is_file():
        return candidate
    found = shutil.which("shellcheck")
    if found:
        return found
    pytest.skip("找不到 shellcheck（tools/shellcheck 或 PATH）")


@pytest.fixture(scope="session")
def bash_path() -> str:
    candidate = os.environ.get("TU_BASH") or (
        "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else "/usr/bin/bash"
    )
    if Path(candidate).is_file():
        return candidate
    found = shutil.which("bash")
    if found:
        return found
    pytest.skip("找不到 bash")
```

```python
# tests/test_shellcheck.py
import pytest

from tu_shell_agent.shell_toolchain.shellcheck import (
    ShellcheckError,
    build_env,
    parse_json1,
    run_shellcheck,
)


def test_parse_json1_maps_comments():
    raw = (
        '{"comments": [{"file": "a.sh", "line": 3, "column": 7, '
        '"level": "warning", "code": 2086, "message": "Double quote"}]}'
    )
    findings = parse_json1(raw)
    assert len(findings) == 1
    assert findings[0].code == "SC2086"
    assert findings[0].line == 3
    assert findings[0].level == "warning"


def test_parse_json1_empty_comments():
    assert parse_json1('{"comments": []}') == []


def test_build_env_drops_shellcheck_opts():
    env = build_env({"PATH": "/usr/bin", "SHELLCHECK_OPTS": "--exclude=SC2086"})
    assert "SHELLCHECK_OPTS" not in env
    assert env["PATH"] == "/usr/bin"


def test_clean_script_exits_zero(tmp_path, shellcheck_path):
    script = tmp_path / "ok.sh"
    script.write_text('#!/usr/bin/env bash\nset -euo pipefail\necho "hi"\n', encoding="utf-8")
    findings, exit_code, _raw = run_shellcheck(shellcheck_path, str(script))
    assert exit_code == 0
    assert findings == []


def test_unquoted_variable_reports_sc2086_and_exit_code_one(tmp_path, shellcheck_path):
    script = tmp_path / "bad.sh"
    script.write_text('#!/usr/bin/env bash\nf="a b"\nls $f\n', encoding="utf-8")
    findings, exit_code, _raw = run_shellcheck(shellcheck_path, str(script))
    assert exit_code == 1  # 1 表示「有问题」，不是失败
    assert any(f.code == "SC2086" for f in findings)


def test_syntax_error_reports_error_level(tmp_path, shellcheck_path):
    script = tmp_path / "syntax.sh"
    script.write_text("#!/usr/bin/env bash\nif [ 1 -eq 1 ]; then\n  echo hi\n", encoding="utf-8")
    findings, _exit_code, _raw = run_shellcheck(shellcheck_path, str(script))
    assert any(f.level == "error" for f in findings)


def test_missing_file_raises_dependency_error(tmp_path, shellcheck_path):
    with pytest.raises(ShellcheckError) as excinfo:
        run_shellcheck(shellcheck_path, str(tmp_path / "nope.sh"))
    assert excinfo.value.exit_code == 2
    assert "shellcheck" in str(excinfo.value)


def test_bad_flag_raises_with_full_command(tmp_path, shellcheck_path):
    script = tmp_path / "ok.sh"
    script.write_text("#!/usr/bin/env bash\necho hi\n", encoding="utf-8")
    with pytest.raises(ShellcheckError) as excinfo:
        run_shellcheck(shellcheck_path, str(script), extra_args=["--bogus-flag"])
    assert excinfo.value.exit_code in (2, 3, 4)
    assert "--bogus-flag" in str(excinfo.value)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/python -m pytest tests/test_shellcheck.py -q`
预期：FAIL，`ModuleNotFoundError: No module named 'tu_shell_agent.shell_toolchain.shellcheck'`

- [ ] **步骤 3：写最小实现**

```python
# tu_shell_agent/shell_toolchain/shellcheck.py
"""shellcheck 封装（规格 §11）：固定参数 + 退出码语义。"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Mapping

from ..types import ShellcheckFinding

LEVELS = ("error", "warning", "info", "style")


class ShellcheckError(RuntimeError):
    """shellcheck 自身出问题（文件读不了 / 参数错 / 未知 formatter），不是脚本有问题。"""

    def __init__(self, message: str, exit_code: int | None, command: str, stderr: str) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.command = command
        self.stderr = stderr


def parse_json1(raw: str) -> list[ShellcheckFinding]:
    payload = json.loads(raw or "{}")
    findings: list[ShellcheckFinding] = []
    for comment in payload.get("comments", []):
        level = comment.get("level", "warning")
        findings.append(
            ShellcheckFinding(
                code=f"SC{comment['code']}",
                line=int(comment["line"]),
                column=int(comment["column"]),
                level=level if level in LEVELS else "warning",  # type: ignore[arg-type]
                message=comment.get("message", ""),
            )
        )
    return findings


def build_env(env: Mapping[str, str]) -> dict[str, str]:
    """必须删除 SHELLCHECK_OPTS：它会被隐式前置，污染结果。"""
    copy = dict(env)
    copy.pop("SHELLCHECK_OPTS", None)
    return copy


def run_shellcheck(
    shellcheck_path: str,
    script_path: str,
    extra_args: list[str] | None = None,
) -> tuple[list[ShellcheckFinding], int, str]:
    """返回 (发现列表, 退出码, 原始 json1 文本)。

    固定参数：--norc（不读用户 .shellcheckrc）、-s bash（Git Bash 即 bash）、-f json1。
    退出码语义：0 干净 / 1 有问题 / 2 文件无法处理 / 3、4 调用错误。
    """
    args = ["--norc", "-s", "bash", "-f", "json1", *(extra_args or []), "--", script_path]
    command = " ".join([shellcheck_path, *args])

    completed = subprocess.run(
        [shellcheck_path, *args],
        capture_output=True,
        text=True,
        env=build_env(os.environ),
    )
    exit_code = completed.returncode

    if exit_code in (0, 1):
        return parse_json1(completed.stdout), exit_code, completed.stdout

    hints = {2: "文件无法处理", 3: "参数语法错误", 4: "未知 formatter/选项"}
    hint = hints.get(exit_code, "shellcheck 异常退出")
    raise ShellcheckError(
        f"shellcheck 调用失败（退出码 {exit_code}，{hint}）：{command}",
        exit_code,
        command,
        completed.stderr,
    )
```

- [ ] **步骤 4：运行测试验证通过**

运行：`.venv/bin/python -m pytest tests/test_shellcheck.py -q`
预期：PASS（8 passed）

- [ ] **步骤 5：Commit**

```bash
git add tu_shell_agent/shell_toolchain/shellcheck.py tests/conftest.py tests/test_shellcheck.py
git commit -m "feat(toolchain): shellcheck 封装与退出码语义

固定 --norc -s bash -f json1；清理 SHELLCHECK_OPTS；退出码 1 视为
「有问题」而非失败，2/3/4 抛 ShellcheckError 并携带完整命令。"
```

---

### 任务 7：脚本执行与进程树终止

**文件：**
- 创建：`tu_shell_agent/shell_toolchain/execute.py`、`tests/test_execute.py`

- [ ] **步骤 1：写失败的测试**

```python
# tests/test_execute.py
import threading
import time

from tu_shell_agent.shell_toolchain.execute import kill_tree, run_script


def test_captures_stdout_stderr_and_exit_code(tmp_path, bash_path):
    script = tmp_path / "ok.sh"
    script.write_text("echo 到标准输出\necho 到标准错误 >&2\nexit 0\n", encoding="utf-8")
    result = run_script(bash_path, str(script), cwd=str(tmp_path), timeout_ms=5_000)
    assert result.stdout.strip() == "到标准输出"
    assert result.stderr.strip() == "到标准错误"
    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.cancelled is False


def test_nonzero_exit_is_returned_not_raised(tmp_path, bash_path):
    script = tmp_path / "fail.sh"
    script.write_text("echo boom >&2\nexit 7\n", encoding="utf-8")
    result = run_script(bash_path, str(script), cwd=str(tmp_path), timeout_ms=5_000)
    assert result.exit_code == 7
    assert "boom" in result.stderr


def test_timeout_kills_process_tree(tmp_path, bash_path):
    script = tmp_path / "slow.sh"
    script.write_text("sleep 30\n", encoding="utf-8")
    started = time.monotonic()
    result = run_script(bash_path, str(script), cwd=str(tmp_path), timeout_ms=400)
    assert result.timed_out is True
    assert time.monotonic() - started < 5


def test_cancel_event_stops_run(tmp_path, bash_path):
    script = tmp_path / "long.sh"
    script.write_text("sleep 30\n", encoding="utf-8")
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    result = run_script(bash_path, str(script), cwd=str(tmp_path), timeout_ms=30_000, cancel=cancel)
    assert result.cancelled is True


def test_streaming_callbacks_receive_output_in_order(tmp_path, bash_path):
    script = tmp_path / "stream.sh"
    script.write_text("echo one\nsleep 0.2\necho two\n", encoding="utf-8")
    chunks: list[str] = []
    run_script(
        bash_path, str(script), cwd=str(tmp_path), timeout_ms=5_000,
        on_stdout=chunks.append,
    )
    joined = "".join(chunks)
    assert "one" in joined and "two" in joined


def test_kill_tree_on_dead_pid_is_silent():
    kill_tree(999_999)  # 不应抛异常
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/python -m pytest tests/test_execute.py -q`
预期：FAIL，`ModuleNotFoundError: No module named 'tu_shell_agent.shell_toolchain.execute'`

- [ ] **步骤 3：写最小实现**

```python
# tu_shell_agent/shell_toolchain/execute.py
"""用 bash 执行生成的脚本：流式回传、超时、可取消、杀进程树（规格 §11）。"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from typing import Any, Callable

from ..types import ExecuteResult


def kill_tree(pid: int) -> None:
    """Windows 用 taskkill 杀整棵树；类 Unix 用进程组。"""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def run_script(
    bash_path: str,
    script_path: str,
    cwd: str,
    timeout_ms: int,
    cancel: Any = None,
    on_stdout: Callable[[str], None] | None = None,
    on_stderr: Callable[[str], None] | None = None,
) -> ExecuteResult:
    started = time.monotonic()
    popen_kwargs: dict[str, Any] = {
        "cwd": cwd,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "env": dict(os.environ),
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    process = subprocess.Popen([bash_path, "--noprofile", "--norc", script_path], **popen_kwargs)

    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    state = {"timed_out": False, "cancelled": False}
    lock = threading.Lock()

    def pump(stream, sink: list[str], callback: Callable[[str], None] | None) -> None:
        if stream is None:
            return
        for chunk in iter(stream.readline, ""):
            with lock:
                sink.append(chunk)
            if callback is not None:
                callback(chunk)

    threads = [
        threading.Thread(target=pump, args=(process.stdout, stdout_parts, on_stdout), daemon=True),
        threading.Thread(target=pump, args=(process.stderr, stderr_parts, on_stderr), daemon=True),
    ]
    for thread in threads:
        thread.start()

    deadline = started + timeout_ms / 1000.0
    while process.poll() is None:
        if process.pid is not None and cancel is not None:
            is_set = getattr(cancel, "is_set", None)
            if callable(is_set) and is_set():
                state["cancelled"] = True
                kill_tree(process.pid)
                break
        if time.monotonic() > deadline:
            state["timed_out"] = True
            if process.pid is not None:
                kill_tree(process.pid)
            break
        time.sleep(0.05)

    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if process.pid is not None:
            kill_tree(process.pid)

    for thread in threads:
        thread.join(timeout=5)

    return ExecuteResult(
        exit_code=process.returncode,
        signal=None,
        timed_out=state["timed_out"],
        cancelled=state["cancelled"],
        duration_ms=int((time.monotonic() - started) * 1000),
        stdout="".join(stdout_parts),
        stderr="".join(stderr_parts),
    )
```

- [ ] **步骤 4：运行测试验证通过**

运行：`.venv/bin/python -m pytest tests/test_execute.py -q`
预期：PASS（6 passed）

- [ ] **步骤 5：Commit**

```bash
git add tu_shell_agent/shell_toolchain/execute.py tests/test_execute.py
git commit -m "feat(toolchain): bash 执行、超时与进程树终止

bash --noprofile --norc 执行脚本；两个读线程做流式回调；超时与取消都
杀整棵进程树（Windows taskkill /T /F，POSIX 进程组）；非零退出码如实返回。"
```

---

### 任务 8：契约校验与提示词

**文件：**
- 创建：`tu_shell_agent/orchestrator/__init__.py`、`tu_shell_agent/orchestrator/contract.py`、`tu_shell_agent/orchestrator/prompt.py`、`tests/test_contract.py`、`tests/test_prompt.py`

- [ ] **步骤 1：写失败的测试**

```python
# tests/test_contract.py
from tu_shell_agent.orchestrator.contract import (
    MAX_SCRIPT_BYTES,
    check_contract,
    extract_anchors,
    normalize_script,
)

ANCHORS = ("@@TU:BODY@@",)


def test_extract_anchors_in_order():
    assert extract_anchors("a\n# @@TU:BODY@@\nb\n# @@TU:EXTRA@@\n") == ("@@TU:BODY@@", "@@TU:EXTRA@@")


def test_contract_passes_for_non_empty_script_with_all_anchors():
    result = check_contract("#!/usr/bin/env bash\n# @@TU:BODY@@\necho hi\n", ANCHORS)
    assert result.ok is True
    assert result.missing_anchors == ()
    assert result.reason is None


def test_empty_script_fails():
    assert check_contract("   \n", ANCHORS).reason == "empty"


def test_missing_anchor_fails_and_lists_it():
    result = check_contract("#!/usr/bin/env bash\necho hi\n", ANCHORS)
    assert result.ok is False
    assert result.reason == "missing_anchor"
    assert result.missing_anchors == ("@@TU:BODY@@",)


def test_crlf_fails():
    assert check_contract("echo hi\r\n# @@TU:BODY@@\r\n", ANCHORS).reason == "has_crlf"


def test_oversized_script_fails():
    big = "# @@TU:BODY@@\n" + "x" * (MAX_SCRIPT_BYTES + 10) + "\n"
    assert check_contract(big, ANCHORS).reason == "too_large"


def test_normalize_strips_markdown_fence_and_forces_lf():
    fenced = "```bash\r\n#!/usr/bin/env bash\r\n# @@TU:BODY@@\r\n```\r\n"
    assert normalize_script(fenced) == "#!/usr/bin/env bash\n# @@TU:BODY@@\n"


def test_normalize_ensures_single_trailing_newline():
    assert normalize_script("echo hi") == "echo hi\n"
    assert normalize_script("echo hi\n\n\n") == "echo hi\n"
```

```python
# tests/test_prompt.py
from tu_shell_agent.orchestrator.prompt import (
    OUTPUT_SCHEMA,
    build_first_message,
    build_repair_message,
    shellcheck_summary,
)
from tu_shell_agent.types import (
    ContractEvidence,
    ExecuteEvidence,
    FailureEvidence,
    ShellcheckFinding,
)


def test_output_schema_requires_three_fields():
    assert OUTPUT_SCHEMA["required"] == ["script", "notes", "assumptions"]


def test_first_message_carries_skeleton_plan_rundir_and_anchors():
    message = build_first_message(
        skeleton="#!/usr/bin/env bash\n# @@TU:BODY@@\n",
        anchors=("@@TU:BODY@@",),
        plan="把所有 .log 文件压缩",
        run_dir="/tmp/runs/r1",
    )
    assert "把所有 .log 文件压缩" in message
    assert "@@TU:BODY@@" in message
    assert "/tmp/runs/r1" in message


def test_repair_message_carries_structured_shellcheck_evidence():
    evidence = FailureEvidence(
        round=1,
        stage="shellcheck",
        shellcheck=(
            ShellcheckFinding(
                code="SC2086", line=12, column=5, level="warning",
                message="Double quote to prevent globbing.",
            ),
        ),
    )
    message = build_repair_message(
        evidence=evidence, anchors=("@@TU:BODY@@",), skeleton="# @@TU:BODY@@\n"
    )
    assert "SC2086" in message
    assert "第 12 行" in message
    assert "@@TU:BODY@@" in message
    assert "完整脚本" in message


def test_repair_message_carries_execute_evidence():
    evidence = FailureEvidence(
        round=2,
        stage="execute",
        execute=ExecuteEvidence(
            exit_code=1, timed_out=False, duration_ms=12,
            stdout_tail="", stderr_tail="no such file",
        ),
    )
    message = build_repair_message(evidence=evidence, anchors=(), skeleton="")
    assert "退出码 1" in message
    assert "no such file" in message


def test_repair_message_carries_generation_error_text():
    evidence = FailureEvidence(
        round=1,
        stage="contract",
        contract=ContractEvidence(reason="empty", message="StructuredOutputError"),
    )
    message = build_repair_message(evidence=evidence, anchors=(), skeleton="")
    assert "StructuredOutputError" in message


def test_shellcheck_summary_groups_by_code():
    summary = shellcheck_summary(
        [
            ShellcheckFinding("SC2086", 1, 1, "warning", "a"),
            ShellcheckFinding("SC2086", 9, 1, "warning", "b"),
            ShellcheckFinding("SC2045", 3, 1, "warning", "c"),
        ]
    )
    assert "SC2086 ×2" in summary
    assert "SC2045 ×1" in summary
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/python -m pytest tests/test_contract.py tests/test_prompt.py -q`
预期：FAIL，`ModuleNotFoundError: No module named 'tu_shell_agent.orchestrator'`

- [ ] **步骤 3：写最小实现**

```python
# tu_shell_agent/orchestrator/__init__.py
```

```python
# tu_shell_agent/orchestrator/contract.py
"""产出契约校验（规格 §7.3、§11）。"""

from __future__ import annotations

import re

from ..types import ContractResult

MAX_SCRIPT_BYTES = 64 * 1024

_ANCHOR = re.compile(r"#\s*(@@TU:[A-Z0-9_]+@@)")
_FENCE = re.compile(r"^```[a-zA-Z]*\n([\s\S]*?)\n?```$")


def extract_anchors(text: str) -> tuple[str, ...]:
    return tuple(_ANCHOR.findall(text))


def normalize_script(raw: str) -> str:
    """去 markdown 围栏、去首尾空白、归一化 LF、结尾恰好一个换行。"""
    text = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1).strip()
    return text + "\n"


def check_contract(normalized: str, required_anchors: tuple[str, ...]) -> ContractResult:
    size = len(normalized.encode("utf-8"))
    missing = tuple(anchor for anchor in required_anchors if anchor not in normalized)

    if not normalized.strip():
        return ContractResult(False, "empty", missing, size)
    if size > MAX_SCRIPT_BYTES:
        return ContractResult(False, "too_large", missing, size)
    if "\r" in normalized:
        return ContractResult(False, "has_crlf", missing, size)
    if missing:
        return ContractResult(False, "missing_anchor", missing, size)
    return ContractResult(True, None, (), size)
```

```python
# tu_shell_agent/orchestrator/prompt.py
"""提示词与结构化输出 schema（规格 §7.4、§7.5）。"""

from __future__ import annotations

from typing import Any

from ..types import FailureEvidence, ShellcheckFinding

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["script", "notes", "assumptions"],
    "properties": {
        "script": {
            "type": "string",
            "description": "完整的 shell 脚本内容；必须保留模板锚点注释；LF 换行；不要包含 markdown 代码块围栏",
        },
        "notes": {"type": "string", "description": "做了哪些取舍；方案中含糊之处如何处理"},
        "assumptions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "你假定的前提",
        },
    },
}

SYSTEM_RULES = """你是一个 shell 脚本生成器。用户会给你一份模板骨架和一份方案文档，你把方案实现进骨架。

硬规则：
1. 保留模板里的全部锚点注释（形如 # @@TU:NAME@@），一个都不能少、不能改名。
2. 保持模板的整体结构（shebang、set 选项、函数骨架、trap、参数解析）。
3. 不要引入网络下载、提权（sudo）、curl | bash、交互式命令。
4. 换行必须是 LF。
5. 只在返回 JSON 的 script 字段里给出完整脚本，不要额外解释。
6. 方案含糊时选择保守实现，并把假设写进 assumptions，不要静默猜测。"""


def build_first_message(*, skeleton: str, anchors: tuple[str, ...], plan: str, run_dir: str) -> str:
    anchor_lines = "\n".join(f"- {anchor}" for anchor in anchors) or "（本模板无锚点）"
    return "\n".join(
        [
            "## 模板骨架（必须保留结构）",
            "```bash",
            skeleton.rstrip(),
            "```",
            "",
            "## 必须保留的锚点",
            anchor_lines,
            "",
            "## 方案文档",
            plan.strip(),
            "",
            "## 运行目录（脚本将在此目录下执行）",
            run_dir,
            "",
            "请按照上述硬规则，把方案实现进骨架，返回完整脚本。",
        ]
    )


def shellcheck_summary(findings: list[ShellcheckFinding] | tuple[ShellcheckFinding, ...]) -> str:
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.code] = counts.get(finding.code, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return "、".join(f"{code} ×{count}" for code, count in ordered)


def build_repair_message(
    *, evidence: FailureEvidence, anchors: tuple[str, ...], skeleton: str
) -> str:
    lines: list[str] = [f"## 第 {evidence.round} 轮失败反馈（阶段：{evidence.stage}）", ""]

    if evidence.contract is not None:
        contract = evidence.contract
        if contract.message:
            lines.append(f"上一轮没有产出可用脚本：{contract.message}")
            lines.append("请重新返回符合 schema 的 JSON（script 字段必须是完整脚本）。")
        else:
            lines.append(f"契约校验未通过：{contract.reason}")
            if contract.missing_anchors:
                lines.append(f"缺失的锚点：{'、'.join(contract.missing_anchors)}")
        lines.append("")

    if evidence.shellcheck:
        lines.append(f"shellcheck 汇总：{shellcheck_summary(evidence.shellcheck)}")
        lines.append("")
        for finding in evidence.shellcheck:
            lines.append(
                f"- {finding.code} 第 {finding.line} 行（{finding.level}）：{finding.message}"
            )
        lines.append("")

    if evidence.execute is not None:
        exec_info = evidence.execute
        suffix = "（超时被杀）" if exec_info.timed_out else ""
        lines.append(
            f"执行失败：退出码 {exec_info.exit_code}{suffix}，耗时 {exec_info.duration_ms}ms"
        )
        lines.append("")
        stderr_tail = exec_info.stderr_tail.rstrip()
        stdout_tail = exec_info.stdout_tail.rstrip()
        if stderr_tail:
            lines.extend(["stderr 尾部：", "```", stderr_tail, "```", ""])
        if stdout_tail:
            lines.extend(["stdout 尾部：", "```", stdout_tail, "```", ""])

    if skeleton.strip():
        lines.extend(["## 模板骨架（结构不得改动）", "```bash", skeleton.rstrip(), "```", ""])

    anchor_lines = "\n".join(f"- {anchor}" for anchor in anchors) or "（无）"
    lines.extend(["## 必须保留的锚点", anchor_lines, ""])
    lines.append("只修复上述问题，保持锚点与模板结构不变，返回完整脚本。")
    return "\n".join(lines)
```

- [ ] **步骤 4：运行测试验证通过**

运行：`.venv/bin/python -m pytest tests/test_contract.py tests/test_prompt.py -q`
预期：PASS（14 passed）

- [ ] **步骤 5：Commit**

```bash
git add tu_shell_agent/orchestrator/ tests/test_contract.py tests/test_prompt.py
git commit -m "feat(orchestrator): 契约校验与提示词组装

契约覆盖 empty/too_large/has_crlf/missing_anchor；提示词把骨架、锚点、运行
目录与结构化失败证据（含生成阶段错误原文）拼成对模型友好的输入。"
```

---

### 任务 9：agent 定义生成

**文件：**
- 创建：`tu_shell_agent/opencode_adapter/__init__.py`、`tu_shell_agent/opencode_adapter/agent_file.py`、`tests/test_agent_file.py`

- [ ] **步骤 1：写失败的测试**

```python
# tests/test_agent_file.py
from pathlib import Path

from tu_shell_agent.opencode_adapter.agent_file import (
    AGENT_NAME,
    render_agent_file,
    write_agent_file,
)

DENY_KEYS = (
    "bash", "edit", "glob", "grep", "list", "lsp", "skill",
    "task", "todowrite", "question", "webfetch", "websearch", "doom_loop",
)


def test_every_dangerous_permission_is_denied_per_key():
    text = render_agent_file(run_dir="C:/Users/me/runs/r1")
    for key in DENY_KEYS:
        assert f"\n  {key}: deny\n" in text, key
    assert "\n  external_directory: deny\n" in text


def test_read_is_scoped_to_the_run_directory():
    text = render_agent_file(run_dir="C:/Users/me/runs/r1")
    assert '\n    "*": deny\n' in text
    assert '\n    "C:/Users/me/runs/r1/**": allow\n' in text


def test_model_line_absent_by_default_present_when_given():
    assert "\nmodel: " not in render_agent_file(run_dir="/tmp/r1")
    assert "\nmodel: anthropic/claude-sonnet-4\n" in render_agent_file(
        run_dir="/tmp/r1", model="anthropic/claude-sonnet-4"
    )


def test_backslashes_are_normalized_to_forward_slashes():
    text = render_agent_file(run_dir="C:\\Users\\me\\runs\\r1")
    assert "C:/Users/me/runs/r1/**" in text
    assert "\\" not in text.split("---")[1]


def test_frontmatter_is_delimited_by_exactly_two_markers():
    text = render_agent_file(run_dir="/tmp/r1")
    assert text.startswith("---\n")
    assert text.count("\n---\n") == 1


def test_write_agent_file_lands_in_the_run_directory(tmp_path):
    path = write_agent_file(str(tmp_path))
    assert path == str(tmp_path / ".opencode" / "agents" / f"{AGENT_NAME}.md")
    assert Path(path).is_file()
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/python -m pytest tests/test_agent_file.py -q`
预期：FAIL，`ModuleNotFoundError: No module named 'tu_shell_agent.opencode_adapter'`

- [ ] **步骤 3：写最小实现**

```python
# tu_shell_agent/opencode_adapter/__init__.py
```

```python
# tu_shell_agent/opencode_adapter/agent_file.py
"""生成项目级 opencode agent 定义（规格 §7.2）。

安全模型不押在总键上：源码级调研只确认了具体权限键，因此逐键显式 deny。
"""

from __future__ import annotations

from pathlib import Path

from ..orchestrator.prompt import SYSTEM_RULES

AGENT_NAME = "tu-shell-writer"

_DENY_KEYS = (
    "bash",
    "edit",
    "glob",
    "grep",
    "list",
    "lsp",
    "skill",
    "task",
    "todowrite",
    "question",
    "webfetch",
    "websearch",
    "doom_loop",
)


def _to_posix(path: str) -> str:
    """Windows 路径在 YAML 里容易被转义，统一转成正斜杠并去掉尾部斜杠。"""
    return path.replace("\\", "/").rstrip("/")


def render_agent_file(run_dir: str, model: str | None = None) -> str:
    run = _to_posix(run_dir)
    lines = [
        "---",
        "description: 把方案文档实现进给定的 shell 模板骨架；只读运行目录，不写文件、不执行命令。",
        "mode: primary",
    ]
    if model:
        lines.append(f"model: {model}")
    lines.append("permission:")
    lines.extend(f"  {key}: deny" for key in _DENY_KEYS)
    lines.extend(
        [
            "  external_directory: deny",
            "  read:",
            '    "*": deny',
            f'    "{run}/**": allow',
            "---",
            SYSTEM_RULES,
            "",
        ]
    )
    return "\n".join(lines)


def write_agent_file(run_dir: str, model: str | None = None) -> str:
    path = Path(run_dir, ".opencode", "agents", f"{AGENT_NAME}.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_agent_file(run_dir, model), encoding="utf-8")
    return str(path)
```

- [ ] **步骤 4：运行测试验证通过**

运行：`.venv/bin/python -m pytest tests/test_agent_file.py -q`
预期：PASS（6 passed）

- [ ] **步骤 5：Commit**

```bash
git add tu_shell_agent/opencode_adapter/__init__.py tu_shell_agent/opencode_adapter/agent_file.py tests/test_agent_file.py
git commit -m "feat(adapter): 生成项目级 agent 定义

逐键 deny（bash/edit/网络/子代理全禁）+ read 锁死在运行目录；
路径转正斜杠避免 YAML 转义；默认不写 model 以沿用用户全局模型。"
```

---

### 任务 10：opencode server 与 HTTP/SSE 客户端

**文件：**
- 创建：`tu_shell_agent/opencode_adapter/server.py`、`tu_shell_agent/opencode_adapter/events.py`、`tests/test_events.py`、`tests/test_server.py`
- 修改：`tu_shell_agent/opencode_adapter/__init__.py`（加 `OpencodeAdapter`）

- [ ] **步骤 1：写失败的测试**

```python
# tests/test_events.py
from tu_shell_agent.opencode_adapter.events import (
    SseParser,
    delta_text,
    event_text,
    is_permission_ask,
)


def test_parser_emits_single_line_event_immediately():
    parser = SseParser()
    assert parser.push('data: {"type": "server.connected"}\n') == [{"type": "server.connected"}]


def test_parser_merges_multi_line_data_frames():
    parser = SseParser()
    assert parser.push('data: {"type": "message.part.updated",\n') == []
    events = parser.push('data: "properties": {"x": 1}}\n')
    assert events == [{"type": "message.part.updated", "properties": {"x": 1}}]


def test_parser_ignores_comments_and_blank_lines():
    parser = SseParser()
    assert parser.push(": keep-alive\n\n") == []


def test_event_text_extracts_whole_part_text():
    event = {"type": "message.part.updated", "properties": {"part": {"type": "text", "text": "你好"}}}
    assert event_text(event) == "你好"


def test_event_text_returns_none_for_non_text_parts():
    event = {"type": "message.part.updated", "properties": {"part": {"type": "tool", "tool": "read"}}}
    assert event_text(event) is None


def test_delta_text_extracts_incremental_text():
    event = {
        "type": "message.part.delta",
        "properties": {
            "sessionID": "ses_1", "messageID": "msg_1", "partID": "prt_1",
            "field": "text", "delta": "你好",
        },
    }
    assert delta_text(event) == "你好"


def test_delta_text_ignores_non_text_fields():
    event = {"type": "message.part.delta", "properties": {"field": "reasoning", "delta": "x"}}
    assert delta_text(event) is None


def test_is_permission_ask_recognizes_real_event_name():
    event = {"type": "permission.asked", "properties": {"id": "per_1", "sessionID": "ses_1"}}
    assert is_permission_ask(event) == ("ses_1", "per_1")


def test_is_permission_ask_also_accepts_legacy_event_name():
    event = {"type": "permission.updated", "properties": {"id": "per_2", "sessionID": "ses_2"}}
    assert is_permission_ask(event) == ("ses_2", "per_2")


def test_is_permission_ask_ignores_other_events():
    assert is_permission_ask({"type": "session.idle", "properties": {}}) is None
```

```python
# tests/test_server.py
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from tu_shell_agent.opencode_adapter.server import (
    basic_auth_header,
    build_serve_args,
    pick_free_port,
    wait_healthy,
)


def test_build_serve_args_binds_loopback_with_explicit_port():
    assert build_serve_args(4123) == ["serve", "--hostname", "127.0.0.1", "--port", "4123"]


def test_basic_auth_header_encodes_opencode_user():
    import base64

    expected = base64.b64encode(b"opencode:secret").decode()
    assert basic_auth_header("secret") == f"Basic {expected}"


def test_pick_free_port_returns_usable_port():
    port = pick_free_port()
    assert 1024 < port < 65536


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = json.dumps({"healthy": True, "version": "1.18.31"}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # 静音
        return


def test_wait_healthy_returns_once_server_answers():
    server = HTTPServer(("127.0.0.1", 0), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        wait_healthy(f"http://127.0.0.1:{server.server_port}", "pw", timeout_s=5)
    finally:
        server.shutdown()


def test_wait_healthy_raises_on_dead_port():
    port = pick_free_port()
    with pytest.raises(RuntimeError, match="未就绪"):
        wait_healthy(f"http://127.0.0.1:{port}", "pw", timeout_s=1)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/python -m pytest tests/test_events.py tests/test_server.py -q`
预期：FAIL，`ModuleNotFoundError: No module named 'tu_shell_agent.opencode_adapter.events'`

- [ ] **步骤 3：写最小实现**

```python
# tu_shell_agent/opencode_adapter/events.py
"""SSE 解析（规格 §3：事件流是 SSE，信封 {type, properties}）。

自己解析而不依赖 SDK：Python 没有官方 SDK，而 SSE 是文档化接口，行为可预期。
"""

from __future__ import annotations

import json
from typing import Any

Event = dict[str, Any]


class SseParser:
    """一帧可能拆成多个 `data:` 行，也可能拆到多个网络分片里。

    策略：累积 data 行，直到拼出的字符串能被 json 解析为止；
    遇到空行（事件边界）仍未解析成功则丢弃该坏帧。
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._pending = ""

    def push(self, chunk: str) -> list[Event]:
        self._buffer += chunk
        events: list[Event] = []
        while True:
            index = self._buffer.find("\n")
            if index == -1:
                break
            line = self._buffer[:index].rstrip("\r")
            self._buffer = self._buffer[index + 1 :]

            if line.startswith("data:"):
                piece = line[len("data:") :].lstrip()
                self._pending = piece if not self._pending else f"{self._pending}\n{piece}"
                try:
                    events.append(json.loads(self._pending))
                    self._pending = ""
                except json.JSONDecodeError:
                    pass
            elif line == "" and self._pending:
                self._pending = ""
            # 注释行（以 : 开头）与其它字段直接忽略
        return events


def event_text(event: Event) -> str | None:
    part = (event.get("properties") or {}).get("part")
    if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
        return part["text"]
    return None


def delta_text(event: Event) -> str | None:
    """message.part.delta 的增量文本（1.18.31 的流式通道）。

    properties = {sessionID, messageID, partID, field, delta}；只认 field == "text"。
    与 message.part.updated 的区别：后者带的是**整个** part（累计文本），混用会重复。
    """
    if event.get("type") != "message.part.delta":
        return None
    properties = event.get("properties") or {}
    if properties.get("field") != "text":
        return None
    delta = properties.get("delta")
    return delta if isinstance(delta, str) else None


# 1.18.31 的真实事件名是 permission.asked（EventPermissionAsked）；
# permission.updated 是旧文档里的写法，一并认下来以防版本差异。
_PERMISSION_EVENTS = ("permission.asked", "permission.updated")


def is_permission_ask(event: Event) -> tuple[str, str] | None:
    """返回 (session_id, permission_id)，不是权限询问则 None。"""
    if event.get("type") not in _PERMISSION_EVENTS:
        return None
    properties = event.get("properties") or {}
    permission_id = properties.get("id")
    session_id = properties.get("sessionID")
    if isinstance(permission_id, str) and isinstance(session_id, str):
        return session_id, permission_id
    return None
```

```python
# tu_shell_agent/opencode_adapter/server.py
"""起停 opencode serve（规格 §7.1）。"""

from __future__ import annotations

import base64
import os
import secrets
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import httpx


def build_serve_args(port: int) -> list[str]:
    return ["serve", "--hostname", "127.0.0.1", "--port", str(port)]


def basic_auth_header(password: str) -> str:
    token = base64.b64encode(f"opencode:{password}".encode()).decode()
    return f"Basic {token}"


def pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_healthy(base_url: str, password: str, timeout_s: float = 20.0) -> None:
    deadline = time.monotonic() + timeout_s
    last_error = "unknown"
    while time.monotonic() < deadline:
        try:
            response = httpx.get(
                f"{base_url}/global/health",
                headers={"authorization": basic_auth_header(password)},
                timeout=2.0,
            )
            if response.status_code == 200:
                payload = response.json()
                if payload.get("healthy") is True:
                    return
                last_error = f"health 返回 {payload}"
            else:
                last_error = f"HTTP {response.status_code}"
        except Exception as error:  # noqa: BLE001 - 就绪轮询要吞掉所有连接类异常
            last_error = str(error)
        time.sleep(0.25)
    raise RuntimeError(f"opencode serve 在 {timeout_s}s 内未就绪：{last_error}")


@dataclass
class ServeHandle:
    base_url: str
    password: str
    port: int
    pid: int | None
    log_path: str
    process: subprocess.Popen
    log_file: object

    def stop(self) -> None:
        from ..shell_toolchain.execute import kill_tree

        try:
            self.log_file.close()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
        if self.pid is not None:
            kill_tree(self.pid)


def start_serve(opencode_path: str, run_dir: str, timeout_s: float = 20.0) -> ServeHandle:
    """起一个独占的 opencode serve，stdout/stderr 收进运行目录的 server.log。"""
    port = pick_free_port()
    password = secrets.token_hex(24)
    log_path = Path(run_dir, "server.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("a", encoding="utf-8", errors="replace")

    process = subprocess.Popen(
        [opencode_path, *build_serve_args(port)],
        cwd=run_dir,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env={**os.environ, "OPENCODE_SERVER_PASSWORD": password},
    )

    base_url = f"http://127.0.0.1:{port}"
    try:
        wait_healthy(base_url, password, timeout_s)
    except RuntimeError:
        from ..shell_toolchain.execute import kill_tree

        if process.pid is not None:
            kill_tree(process.pid)
        log_file.close()
        raise

    return ServeHandle(
        base_url=base_url,
        password=password,
        port=port,
        pid=process.pid,
        log_path=str(log_path),
        process=process,
        log_file=log_file,
    )
```

```python
# tu_shell_agent/opencode_adapter/__init__.py（在既有空文件基础上追加）
"""OpencodePort 的 httpx 实现。会话与结构化输出走 HTTP，事件走 SSE。"""

from __future__ import annotations

import threading
from typing import Any, Callable

import httpx

from ..types import GeneratedScript
from .agent_file import AGENT_NAME, write_agent_file
from .events import SseParser, delta_text, event_text, is_permission_ask
from .server import ServeHandle, basic_auth_header, start_serve


class OpencodeAdapter:
    def __init__(self, opencode_path: str, note: Callable[[str], None] | None = None) -> None:
        self._opencode_path = opencode_path
        self._note = note or (lambda _message: None)
        self._serve: ServeHandle | None = None
        self._client: httpx.Client | None = None
        self._auth = ""
        self._stop_events = threading.Event()
        self._events_thread: threading.Thread | None = None
        self._on_delta: Callable[[str], None] | None = None
        self._saw_delta = False

    # ── 生命周期 ────────────────────────────────────────────────────────
    def start(self, run_dir: str, agent_name: str = AGENT_NAME, model: str | None = None) -> str:
        write_agent_file(run_dir, model)
        self._serve = start_serve(self._opencode_path, run_dir)
        self._auth = basic_auth_header(self._serve.password)
        self._client = httpx.Client(
            base_url=self._serve.base_url,
            headers={"authorization": self._auth},
            timeout=httpx.Timeout(300.0, connect=10.0),
        )
        self._stop_events.clear()
        self._events_thread = threading.Thread(target=self._consume_events, daemon=True)
        self._events_thread.start()

        response = self._client.post("/session", json={"title": f"tu-shell-agent {run_dir}"})
        response.raise_for_status()
        return str(response.json()["id"])

    def dispose(self) -> None:
        self._stop_events.set()
        if self._client is not None:
            self._client.close()
            self._client = None
        if self._serve is not None:
            self._serve.stop()
            self._serve = None

    # ── 事件与权限 ──────────────────────────────────────────────────────
    def _consume_events(self) -> None:
        assert self._serve is not None
        parser = SseParser()
        try:
            with httpx.stream(
                "GET",
                f"{self._serve.base_url}/event",
                headers={"authorization": self._auth, "accept": "text/event-stream"},
                timeout=None,
            ) as response:
                for line in response.iter_lines():
                    if self._stop_events.is_set():
                        return
                    for event in parser.push(line + "\n"):
                        ask = is_permission_ask(event)
                        if ask is not None:
                            self._reject_permission(*ask)
                        # 1.18.31 的增量文本走 message.part.delta；message.part.updated
                        # 带的是整个 part（累计文本）。一旦见过 delta 就不再回退，避免重复。
                        text = delta_text(event)
                        if text is not None:
                            self._saw_delta = True
                        elif not self._saw_delta:
                            text = event_text(event)
                        if text and self._on_delta is not None:
                            self._on_delta(text)
        except Exception as error:  # noqa: BLE001 - 事件流断了不应带崩主流程
            self._note(f"事件流中断：{error}")

    def _reject_permission(self, session_id: str, permission_id: str) -> None:
        """对任何权限询问一律拒绝（规格 §7.2 的安全网）。"""
        if self._client is None:
            return
        self._client.post(
            f"/session/{session_id}/permissions/{permission_id}",
            json={"response": "reject"},
        )
        self._note(f"已自动拒绝 opencode 的权限请求 {permission_id}")

    # ── 生成 ────────────────────────────────────────────────────────────
    def generate(
        self,
        session_id: str,
        message: str,
        schema: dict[str, Any],
        timeout_ms: int,
        on_delta: Callable[[str], None] | None = None,
        cancel: Any = None,
    ) -> GeneratedScript:
        if self._client is None:
            raise RuntimeError("适配器未启动")
        self._on_delta = on_delta
        self._saw_delta = False
        try:
            response = self._client.post(
                f"/session/{session_id}/message",
                json={
                    "agent": AGENT_NAME,
                    "parts": [{"type": "text", "text": message}],
                    "format": {"type": "json_schema", "schema": schema, "retryCount": 2},
                },
                timeout=timeout_ms / 1000.0,
            )
            response.raise_for_status()
            payload = response.json()
            info = payload.get("info") or {}
            error = info.get("error")
            if isinstance(error, dict) and error.get("name"):
                detail = (error.get("data") or {}).get("message", "")
                raise RuntimeError(f"opencode 返回错误 {error['name']}：{detail}")
            # 真实 1.18.31 的 OpenAPI 把结构化结果放在 AssistantMessage.structured；
            # JS SDK 文档写的是 structured_output —— 两个都认，避免版本漂移。
            structured = info.get("structured")
            if not isinstance(structured, dict):
                structured = info.get("structured_output")
            if not isinstance(structured, dict):
                raise RuntimeError(
                    "opencode 未返回结构化输出（已查 info.structured 与 info.structured_output）"
                )
            script = structured.get("script")
            if not isinstance(script, str) or not script.strip():
                raise RuntimeError("结构化输出缺少 script 字段（或 script 为空）")
            assumptions = structured.get("assumptions") or []
            return GeneratedScript(
                script=script,
                notes=str(structured.get("notes") or ""),
                assumptions=tuple(str(item) for item in assumptions),
            )
        finally:
            self._on_delta = None

    def abort(self, session_id: str) -> None:
        if self._client is not None:
            self._client.post(f"/session/{session_id}/abort")


__all__ = ["OpencodeAdapter", "AGENT_NAME"]
```

> **已核对（控制者用真实 opencode 1.18.31 的 OpenAPI 验证，不是猜的）：**
> - 请求字段就是 `format`（`POST /session/{sessionID}/message` 的 body，指向 `OutputFormat = TextOutputFormat | OutputFormatJsonSchema`）；`outputFormat` 在规范里零命中。
> - 结构化结果落在响应 `info.structured`；`info.error` 失败时是 `StructuredOutputError{message, retries}`。
> - body 只有 `parts` 必填，`agent` / `model` / `system` / `tools` 均可选。
> - `GET /global/health` 返回 `{"healthy": true, "version": "1.18.31"}`；权限应答端点是 `POST /session/{sessionID}/permissions/{permissionID}`。
> - **事件名**：权限询问是 `permission.asked`（不是旧文档的 `permission.updated`）；增量文本是 `message.part.delta`（`field == "text"`），`message.part.updated` 带的是整个 part。
>
> 上面代码里"两个字段都读"是为兼容 JS SDK 文档的写法，不要删。

- [ ] **步骤 4：运行测试验证通过**

运行：`.venv/bin/python -m pytest tests/test_events.py tests/test_server.py -q`
预期：PASS（15 passed：events 10 + server 5）

- [ ] **步骤 5：Commit**

```bash
git add tu_shell_agent/opencode_adapter/ tests/test_events.py tests/test_server.py
git commit -m "feat(adapter): opencode serve 生命周期、SSE 订阅与结构化输出

独占 serve（随机端口 + 随机密码 + server.log）并轮询 health 就绪；
SSE 自解析以渲染增量文本；对任何权限询问（permission.asked）一律 reject 作为
安全网；结构化输出缺失时明确报错而不是交回空脚本。"
```

---

### 任务 11：编排状态机

**文件：**
- 创建：`tu_shell_agent/orchestrator/loop.py`、`tests/test_loop.py`

- [ ] **步骤 1：写失败的测试**

```python
# tests/test_loop.py
from dataclasses import dataclass, field

from tu_shell_agent.orchestrator.loop import LoopInput, run_loop
from tu_shell_agent.types import (
    DetectionReport,
    ExecuteResult,
    GeneratedScript,
    RunConfig,
    ShellcheckFinding,
)

SKELETON = "#!/usr/bin/env bash\nset -euo pipefail\n# @@TU:BODY@@\necho ok\n"
GOOD = GeneratedScript(script=SKELETON, notes="", assumptions=())


@dataclass
class FakeTemplate:
    id: str = "single"
    body: str = SKELETON
    anchors: tuple[str, ...] = ("@@TU:BODY@@",)
    trusted: bool = True
    placeholders: tuple = ()


@dataclass
class Harness:
    scripts: list[str] = field(default_factory=list)
    written: list[str] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)
    confirmed: int = 0
    events: list = field(default_factory=list)


def make_ports(
    rounds: list[GeneratedScript],
    shellcheck_for=None,
    execute_for=None,
    confirm: bool = True,
    start_error: Exception | None = None,
    seen: Harness | None = None,
):
    harness = seen or Harness()
    turn = {"n": 0}

    class FakeOpencode:
        def start(self, run_dir, agent_name, model):
            if start_error is not None:
                raise start_error
            return "ses_1"

        def generate(self, session_id, message, schema, timeout_ms, on_delta=None, cancel=None):
            harness.prompts.append(message)
            result = rounds[min(turn["n"], len(rounds) - 1)]
            turn["n"] += 1
            harness.scripts.append(result.script)
            return result

        def abort(self, session_id):
            return None

        def dispose(self):
            return None

    class FakeToolchain:
        def detect(self):
            return DetectionReport(None, None, None, ())

        def shellcheck(self, script_path):
            findings = shellcheck_for(harness.scripts[-1]) if shellcheck_for else []
            return list(findings), 0, '{"comments": []}'

        def execute(self, script_path, cwd, timeout_ms, cancel=None, on_stdout=None, on_stderr=None):
            if execute_for is not None:
                return execute_for(harness.scripts[-1])
            return ExecuteResult(0, None, False, False, 1, "ok\n", "")

    class FakeConfirm:
        def confirm(self, round_no, script_path, script, trusted):
            harness.confirmed += 1
            return confirm

    class FakeStore:
        run_dir = "/tmp/run"

        def write_script(self, round_no, script):
            harness.written.append(f"{round_no}:{script}")
            return "/tmp/run/script.sh"

        def write_attempt(self, round_no, files):
            return None

        def write_meta(self, patch):
            return None

    return {
        "opencode": FakeOpencode(),
        "toolchain": FakeToolchain(),
        "confirm": FakeConfirm(),
        "store": FakeStore(),
        "emit": harness.events.append,
    }, harness


CONFIG = RunConfig(run_root="/tmp/root", max_rounds=3, generate_timeout_ms=5000, execute_timeout_ms=5000)


def run(input_ports, template=None, plan="方案"):
    return run_loop(
        LoopInput(
            plan=plan,
            template=template or FakeTemplate(),
            values={},
            run_dir="/tmp/run",
            config=CONFIG,
            ports=input_ports,
        )
    )


def test_first_round_pass_succeeds_and_writes_once():
    ports, harness = make_ports([GOOD])
    result = run(ports)
    assert result.outcome == "succeeded"
    assert result.rounds == 1
    assert len(harness.written) == 1


def test_shellcheck_failure_then_fix_succeeds_on_round_two():
    broken = GeneratedScript(script='#!/usr/bin/env bash\n# @@TU:BODY@@\nf="a b"\nls $f\n', notes="", assumptions=())

    def shellcheck_for(script: str):
        if "ls $f" in script:
            return [ShellcheckFinding("SC2086", 4, 4, "warning", "Double quote")]
        return []

    ports, harness = make_ports([broken, GOOD], shellcheck_for=shellcheck_for)
    result = run(ports)
    assert result.outcome == "succeeded"
    assert result.rounds == 2
    assert "SC2086" in harness.prompts[1]


def test_execute_failure_feeds_exit_code_and_stderr_forward():
    failing = GeneratedScript(script="#!/usr/bin/env bash\n# @@TU:BODY@@\nexit 3\n", notes="", assumptions=())

    def execute_for(script: str):
        if "exit 3" in script:
            return ExecuteResult(3, None, False, False, 5, "", "缺少输入文件\n")
        return ExecuteResult(0, None, False, False, 5, "ok\n", "")

    ports, harness = make_ports([failing, GOOD], execute_for=execute_for)
    result = run(ports)
    assert result.outcome == "succeeded"
    assert "退出码 3" in harness.prompts[1]
    assert "缺少输入文件" in harness.prompts[1]


def test_generation_error_is_fed_back_and_next_round_succeeds():
    ports, harness = make_ports([GOOD])
    calls = {"n": 0}

    def failing_generate(session_id, message, schema, timeout_ms, on_delta=None, cancel=None):
        calls["n"] += 1
        harness.prompts.append(message)
        if calls["n"] == 1:
            raise RuntimeError("StructuredOutputError: 模型未按 schema 返回")
        return GOOD

    ports["opencode"].generate = failing_generate
    result = run(ports)
    assert result.outcome == "succeeded"
    assert result.rounds == 2
    assert "StructuredOutputError" in harness.prompts[1]


def test_start_failure_returns_aborted_dependency():
    ports, _harness = make_ports([GOOD], start_error=RuntimeError("health 不通"))
    result = run(ports)
    assert result.outcome == "aborted_dependency"
    assert result.rounds == 0


def test_three_failing_rounds_end_as_needs_human():
    broken = GeneratedScript(script="#!/usr/bin/env bash\n# @@TU:BODY@@\nls $f\n", notes="", assumptions=())
    ports, harness = make_ports(
        [broken],
        shellcheck_for=lambda _s: [ShellcheckFinding("SC2086", 3, 4, "warning", "q")],
    )
    result = run(ports)
    assert result.outcome == "needs_human"
    assert result.rounds == 3
    assert len(harness.prompts) == 3


def test_user_rejection_cancels_without_next_round():
    ports, harness = make_ports([GOOD], confirm=False)
    result = run(ports)
    assert result.outcome == "cancelled"
    assert len(harness.prompts) == 1


def test_missing_anchor_reports_contract_failure_into_next_prompt():
    no_anchor = GeneratedScript(script="#!/usr/bin/env bash\necho hi\n", notes="", assumptions=())
    ports, harness = make_ports([no_anchor, GOOD])
    result = run(ports)
    assert result.outcome == "succeeded"
    assert "@@TU:BODY@@" in harness.prompts[1]


def test_info_level_findings_do_not_block():
    ports, _harness = make_ports(
        [GOOD], shellcheck_for=lambda _s: [ShellcheckFinding("SC2006", 1, 1, "info", "use $()")]
    )
    assert run(ports).outcome == "succeeded"


def test_trusted_template_never_asks_for_confirmation():
    ports, harness = make_ports([GOOD])
    run(ports, template=FakeTemplate(trusted=True))
    assert harness.confirmed == 0


def test_untrusted_template_asks_once():
    ports, harness = make_ports([GOOD])
    run(ports, template=FakeTemplate(trusted=False))
    assert harness.confirmed == 1
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/python -m pytest tests/test_loop.py -q`
预期：FAIL，`ModuleNotFoundError: No module named 'tu_shell_agent.orchestrator.loop'`

- [ ] **步骤 3：写最小实现**

```python
# tu_shell_agent/orchestrator/loop.py
"""主状态机（规格 §6）。

只有这里知道流程；外部世界全部由 ports 注入，因此可在没有 opencode、
没有 Windows 的机器上完整单测。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from ..ports import ConfirmPort, OpencodePort, RunStorePort, ToolchainPort
from ..template_store.render import PlaceholderSpec, render_template
from ..types import (
    ExecuteResult,
    FailureEvidence,
    RunConfig,
    RunEvent,
    RunOutcome,
    ShellcheckFinding,
    blocks_run,
)
from .contract import check_contract, extract_anchors, normalize_script
from .prompt import OUTPUT_SCHEMA, build_first_message, build_repair_message

TAIL_LINES = 40


@dataclass(frozen=True, slots=True)
class TemplateSpec:
    id: str
    body: str
    anchors: tuple[str, ...] = ()
    trusted: bool = False
    placeholders: tuple[PlaceholderSpec, ...] = ()


@dataclass(frozen=True, slots=True)
class LoopPorts:
    opencode: OpencodePort
    toolchain: ToolchainPort
    confirm: ConfirmPort
    store: RunStorePort
    emit: Callable[[RunEvent], None]


@dataclass(frozen=True, slots=True)
class LoopInput:
    plan: str
    template: TemplateSpec
    values: dict[str, str]
    run_dir: str
    config: RunConfig
    ports: LoopPorts
    agent_name: str = "tu-shell-writer"
    cancel: Any = None


@dataclass(frozen=True, slots=True)
class LoopResult:
    outcome: RunOutcome
    rounds: int
    script_path: str | None = None
    last_findings: tuple[ShellcheckFinding, ...] = ()
    last_execute: ExecuteResult | None = None


def _tail(text: str) -> str:
    return "\n".join(text.split("\n")[-TAIL_LINES:])


def _cancelled(cancel: Any) -> bool:
    is_set = getattr(cancel, "is_set", None)
    return bool(callable(is_set) and is_set())


def render_findings(findings: list[ShellcheckFinding] | tuple[ShellcheckFinding, ...]) -> str:
    return (
        "\n".join(f"{f.code} {f.line}:{f.column} {f.level} {f.message}" for f in findings) + "\n"
    )


def run_loop(input_: LoopInput) -> LoopResult:
    ports = input_.ports
    config = input_.config
    emit = ports.emit

    skeleton = render_template(
        input_.template.body, list(input_.template.placeholders), input_.values
    )
    anchors = (
        input_.template.anchors if input_.template.anchors else extract_anchors(skeleton)
    )

    emit(RunEvent("phase", 0, {"phase": "precheck"}))
    detection = ports.toolchain.detect()
    if detection.problems:
        emit(RunEvent("note", 0, {"message": "\n".join(detection.problems)}))
        return LoopResult("aborted_dependency", 0)

    try:
        session_id = ports.opencode.start(input_.run_dir, input_.agent_name, config.model)
    except Exception as error:  # noqa: BLE001 - 启动失败要变成终止态，不是异常
        message = f"opencode 启动失败：{error}"
        emit(RunEvent("note", 0, {"message": message}))
        ports.store.write_attempt(0, {"start-error.txt": message + "\n"})
        ports.store.write_meta({"outcome": "aborted_dependency", "rounds": 0})
        return LoopResult("aborted_dependency", 0)

    evidence: FailureEvidence | None = None
    last_findings: tuple[ShellcheckFinding, ...] = ()
    last_execute: ExecuteResult | None = None

    for round_no in range(1, config.max_rounds + 1):
        if _cancelled(input_.cancel):
            return LoopResult("cancelled", round_no - 1, last_findings=last_findings)

        started = time.monotonic()
        emit(RunEvent("phase", round_no, {"phase": "generating" if round_no == 1 else "repairing"}))

        message = (
            build_first_message(
                skeleton=skeleton, anchors=anchors, plan=input_.plan, run_dir=input_.run_dir
            )
            if evidence is None
            else build_repair_message(evidence=evidence, anchors=anchors, skeleton=skeleton)
        )

        try:
            generated = ports.opencode.generate(
                session_id,
                message,
                OUTPUT_SCHEMA,
                config.generate_timeout_ms,
                on_delta=lambda text, r=round_no: emit(RunEvent("assistant_delta", r, {"text": text})),
                cancel=input_.cancel,
            )
        except Exception as error:  # noqa: BLE001 - 结构化输出失败计一次契约失败
            failure = str(error)
            evidence = FailureEvidence(
                round=round_no,
                stage="contract",
                contract={"reason": "empty", "missing_anchors": (), "message": failure},
            )
            ports.store.write_attempt(round_no, {"generation-error.txt": failure + "\n"})
            emit(RunEvent("note", round_no, {"message": f"第 {round_no} 轮生成失败：{failure}"}))
            continue

        script = normalize_script(generated.script)
        emit(RunEvent("script", round_no, {"script": script}))
        script_path = ports.store.write_script(round_no, script)
        ports.store.write_attempt(
            round_no,
            {
                "notes.md": f"{generated.notes}\n\n## 假设\n"
                + "\n".join(f"- {item}" for item in generated.assumptions)
                + "\n"
            },
        )

        emit(RunEvent("phase", round_no, {"phase": "checking"}))
        contract = check_contract(script, anchors)
        if not contract.ok:
            evidence = FailureEvidence(
                round=round_no,
                stage="contract",
                contract={
                    "reason": contract.reason,
                    "missing_anchors": contract.missing_anchors,
                },
            )
            ports.store.write_attempt(
                round_no, {"contract.json": f"{contract}\n"}
            )
            emit(RunEvent("note", round_no, {"message": f"第 {round_no} 轮契约失败：{contract.reason}"}))
            continue

        findings, _exit_code, raw = ports.toolchain.shellcheck(script_path)
        last_findings = tuple(findings)
        ports.store.write_attempt(
            round_no,
            {"shellcheck.json": raw, "shellcheck.txt": render_findings(findings)},
        )
        emit(RunEvent("shellcheck", round_no, {"findings": last_findings}))

        blocking = [f for f in findings if blocks_run(f.level, config.blocking_level)]
        if blocking:
            evidence = FailureEvidence(
                round=round_no, stage="shellcheck", shellcheck=tuple(findings)
            )
            continue

        emit(RunEvent("phase", round_no, {"phase": "confirming"}))
        approved = input_.template.trusted or ports.confirm.confirm(
            round_no, script_path, script, input_.template.trusted
        )
        if not approved:
            ports.store.write_meta({"outcome": "cancelled", "rounds": round_no})
            return LoopResult(
                "cancelled", round_no, script_path, last_findings, last_execute
            )

        emit(RunEvent("phase", round_no, {"phase": "executing"}))
        result = ports.toolchain.execute(
            script_path,
            input_.run_dir,
            config.execute_timeout_ms,
            cancel=input_.cancel,
        )
        last_execute = result
        ports.store.write_attempt(
            round_no,
            {
                "stdout.txt": result.stdout,
                "stderr.txt": result.stderr,
                "execute.json": (
                    f'{{"exit_code": {result.exit_code}, "timed_out": {str(result.timed_out).lower()}, '
                    f'"cancelled": {str(result.cancelled).lower()}, "duration_ms": {result.duration_ms}}}\n'
                ),
            },
        )
        emit(RunEvent("execute", round_no, {"result": result}))

        if result.exit_code == 0 and not result.timed_out and not result.cancelled:
            ports.store.write_meta(
                {
                    "outcome": "succeeded",
                    "rounds": round_no,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                }
            )
            emit(RunEvent("phase", round_no, {"phase": "settled"}))
            return LoopResult("succeeded", round_no, script_path, last_findings, result)

        if result.cancelled:
            ports.store.write_meta({"outcome": "cancelled", "rounds": round_no})
            return LoopResult("cancelled", round_no, script_path, last_findings, result)

        evidence = FailureEvidence(
            round=round_no,
            stage="execute",
            execute={
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                "stdout_tail": _tail(result.stdout),
                "stderr_tail": _tail(result.stderr),
                "duration_ms": result.duration_ms,
            },
        )

    ports.store.write_meta({"outcome": "needs_human", "rounds": config.max_rounds})
    emit(RunEvent("phase", config.max_rounds, {"phase": "settled"}))
    return LoopResult("needs_human", config.max_rounds, None, last_findings, last_execute)
```

- [ ] **步骤 4：运行测试验证通过**

运行：`.venv/bin/python -m pytest tests/test_loop.py -q`
预期：PASS（11 passed）

- [ ] **步骤 5：Commit**

```bash
git add tu_shell_agent/orchestrator/loop.py tests/test_loop.py
git commit -m "feat(orchestrator): 主状态机

生成→契约→shellcheck→确认→执行→判定；失败证据结构化回灌同一会话，
最多 max_rounds 轮；启动失败与依赖缺失各有独立终止态；信任模板跳过确认。"
```

---

### 任务 12：CLI 驱动与端到端验证

**文件：**
- 创建：`tu_shell_agent/shell_toolchain/facade.py`、`tu_shell_agent/cli.py`、`test_fixtures/plan-simple.md`、`test_fixtures/template-single.tpl.sh`、`tests/test_e2e_offline.py`、`tests/test_e2e_live.py`
- 修改：`pyproject.toml`（加 `cli` 脚本入口）

- [ ] **步骤 1：写失败的测试**

```python
# tests/test_e2e_offline.py
"""离线全链路：真实 shellcheck + 真实 bash + 假 opencode。"""

from dataclasses import dataclass, field

from tu_shell_agent.orchestrator.loop import LoopInput, LoopPorts, TemplateSpec, run_loop
from tu_shell_agent.run_store.store import RunStore
from tu_shell_agent.shell_toolchain.shellcheck import run_shellcheck
from tu_shell_agent.types import (
    DetectionReport,
    ExecuteResult,
    GeneratedScript,
    RunConfig,
)

from tu_shell_agent.shell_toolchain.execute import run_script

BROKEN = (
    "#!/usr/bin/env bash\nset -euo pipefail\n# @@TU:BODY@@\n"
    'files="a b"\nfor f in $files; do echo $f; done\necho done\n'
)
FIXED = (
    "#!/usr/bin/env bash\nset -euo pipefail\n# @@TU:BODY@@\n"
    'files="a b"\nfor f in $files; do echo "$f"; done\necho done\n'
)


@dataclass
class FakeOpencode:
    turn: int = 0

    def start(self, run_dir, agent_name, model):
        return "ses_offline"

    def generate(self, session_id, message, schema, timeout_ms, on_delta=None, cancel=None):
        self.turn += 1
        if self.turn == 1:
            return GeneratedScript(BROKEN, "首轮", ())
        return GeneratedScript(FIXED, "补引号", ())

    def abort(self, session_id):
        return None

    def dispose(self):
        return None


def test_broken_script_is_caught_then_fixed_and_executed(tmp_path, shellcheck_path, bash_path):
    run_dir = str(tmp_path / "r1")
    store = RunStore(run_dir)
    store.init()

    class Toolchain:
        def detect(self):
            return DetectionReport(None, None, None, ())

        def shellcheck(self, script_path):
            return run_shellcheck(shellcheck_path, script_path)

        def execute(self, script_path, cwd, timeout_ms, cancel=None, on_stdout=None, on_stderr=None):
            return run_script(bash_path, script_path, cwd, timeout_ms)

    ports = LoopPorts(
        opencode=FakeOpencode(),
        toolchain=Toolchain(),
        confirm=type("C", (), {"confirm": lambda self, *a: True})(),
        store=store,
        emit=lambda event: None,
    )

    result = run_loop(
        LoopInput(
            plan="把一句话拆成单词逐行打印",
            template=TemplateSpec(
                id="single",
                body="#!/usr/bin/env bash\nset -euo pipefail\n# @@TU:BODY@@\n",
                anchors=("@@TU:BODY@@",),
                trusted=True,
            ),
            values={},
            run_dir=run_dir,
            config=RunConfig(
                run_root=str(tmp_path), max_rounds=3,
                generate_timeout_ms=5000, execute_timeout_ms=10_000,
            ),
            ports=ports,
        )
    )

    assert result.outcome == "succeeded"
    assert result.rounds == 2
    attempt_one = tmp_path / "r1" / "attempts" / "1"
    assert "SC2086" in (attempt_one / "shellcheck.json").read_text(encoding="utf-8")
    assert "a" in (tmp_path / "r1" / "attempts" / "2" / "stdout.txt").read_text(encoding="utf-8")
    assert '"succeeded"' in (tmp_path / "r1" / "meta.json").read_text(encoding="utf-8")
```

```python
# tests/test_e2e_live.py
"""真实 opencode 冒烟。默认跳过，TU_LIVE=1 才跑（需要本机已装并登录 opencode 1.x）。"""

import os
from dataclasses import dataclass, field

import pytest

from tu_shell_agent.opencode_adapter import OpencodeAdapter
from tu_shell_agent.opencode_adapter.agent_file import AGENT_NAME
from tu_shell_agent.orchestrator.prompt import OUTPUT_SCHEMA
from tu_shell_agent.shell_toolchain.detect import detect_all, system_deps

pytestmark = pytest.mark.skipif(
    os.environ.get("TU_LIVE") != "1", reason="设 TU_LIVE=1 才跑真实 opencode 冒烟"
)


def test_live_generation_returns_structured_output(tmp_path):
    report = detect_all(system_deps())
    assert report.opencode is not None, "\n".join(report.problems)

    adapter = OpencodeAdapter(
        opencode_path=report.opencode.path,
        note=lambda message: print(f"[opencode] {message}"),
    )
    try:
        session_id = adapter.start(str(tmp_path), AGENT_NAME)
        generated = adapter.generate(
            session_id,
            "在这个骨架里实现：打印当前目录下所有 .md 文件的数量。\n"
            "骨架：# @@TU:BODY@@\n运行目录：" + str(tmp_path),
            OUTPUT_SCHEMA,
            timeout_ms=180_000,
        )
        assert "@@TU:BODY@@" in generated.script
        assert generated.notes != ""
    finally:
        adapter.dispose()
```

- [ ] **步骤 2：运行测试验证失败**

运行：`.venv/bin/python -m pytest tests/test_e2e_offline.py -q`
预期：FAIL，`ModuleNotFoundError: No module named 'tu_shell_agent.cli'` 或 `ModuleNotFoundError: No module named 'tu_shell_agent.shell_toolchain.facade'`

- [ ] **步骤 3：写最小实现**

```python
# tu_shell_agent/shell_toolchain/facade.py
"""把探测 / shellcheck / 执行组装成 ToolchainPort。"""

from __future__ import annotations

from typing import Any, Callable

from ..types import DetectionReport, ExecuteResult, ShellcheckFinding
from .detect import detect_all, system_deps
from .execute import run_script
from .shellcheck import run_shellcheck


class ShellToolchain:
    def __init__(
        self,
        bash_path: str,
        shellcheck_path: str,
        overrides: dict[str, str] | None = None,
    ) -> None:
        self._bash_path = bash_path
        self._shellcheck_path = shellcheck_path
        self._overrides = dict(overrides or {})

    def detect(self) -> DetectionReport:
        return detect_all(system_deps(self._overrides))

    def shellcheck(self, script_path: str) -> tuple[list[ShellcheckFinding], int, str]:
        return run_shellcheck(self._shellcheck_path, script_path)

    def execute(
        self,
        script_path: str,
        cwd: str,
        timeout_ms: int,
        cancel: Any = None,
        on_stdout: Callable[[str], None] | None = None,
        on_stderr: Callable[[str], None] | None = None,
    ) -> ExecuteResult:
        return run_script(
            self._bash_path,
            script_path,
            cwd,
            timeout_ms,
            cancel=cancel,
            on_stdout=on_stdout,
            on_stderr=on_stderr,
        )
```

```python
# tu_shell_agent/cli.py
"""无界面的开发驱动：跑完整流程并打印时间线。

用法：
  .venv/bin/python -m tu_shell_agent.cli --plan test_fixtures/plan-simple.md --template single --run-root /tmp/tu-runs --yes
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .opencode_adapter import OpencodeAdapter
from .orchestrator.contract import extract_anchors
from .orchestrator.loop import LoopInput, LoopPorts, TemplateSpec, run_loop
from .run_store.layout import make_run_id, run_dir_for
from .run_store.store import RunStore
from .shell_toolchain.detect import detect_all, system_deps
from .shell_toolchain.facade import ShellToolchain
from .template_store.render import render_template
from .template_store.store import TemplateStore
from .types import RunConfig, RunEvent


def _path_overrides(args: argparse.Namespace) -> dict[str, str]:
    """把 CLI 的 --*-path 覆盖转成 detect_all 认的 {tool: path} 映射。"""
    pairs = (
        ("opencode", args.opencode_path),
        ("bash", args.bash_path),
        ("shellcheck", args.shellcheck_path),
    )
    return {tool: path for tool, path in pairs if path}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="tu_shell_agent.cli", description="方案 → shell 脚本 → 执行 → 校验")
    parser.add_argument("--plan", required=True, help="方案文档路径")
    parser.add_argument("--template", default="single", help="模板 id（默认 single）")
    parser.add_argument("--run-root", default=str(Path.cwd() / ".tu-runs"))
    parser.add_argument("--templates-dir", default=str(Path.cwd() / ".tu-templates"))
    parser.add_argument("--opencode-path", default=None, help="覆盖 opencode 可执行文件路径（默认自动探测）")
    parser.add_argument("--bash-path", default=None, help="覆盖 bash 可执行文件路径（默认自动探测）")
    parser.add_argument("--shellcheck-path", default=None, help="覆盖 shellcheck 可执行文件路径（默认自动探测）")
    parser.add_argument("--max-rounds", type=int, default=3)
    parser.add_argument("--generate-timeout-ms", type=int, default=300_000)
    parser.add_argument("--execute-timeout-ms", type=int, default=120_000)
    parser.add_argument(
        "--yes",
        action="store_true",
        help="非交互环境必须显式加这个才会执行生成的脚本（规格 §11）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    report = detect_all(system_deps(_path_overrides(args)))
    print("环境自检：", report, flush=True)
    if report.problems:
        print("自检未通过：\n" + "\n".join(report.problems), file=sys.stderr)
        return 2
    assert report.opencode and report.bash and report.shellcheck

    store = TemplateStore(args.templates_dir)
    meta = store.get(args.template)
    body = store.read(args.template)
    skeleton = render_template(body, meta.placeholders, {})
    anchors = extract_anchors(skeleton)

    run_dir = run_dir_for(args.run_root, make_run_id())
    run_store = RunStore(run_dir)
    run_store.init()

    config = RunConfig(
        run_root=args.run_root,
        max_rounds=args.max_rounds,
        generate_timeout_ms=args.generate_timeout_ms,
        execute_timeout_ms=args.execute_timeout_ms,
        opencode_path=report.opencode.path,
        bash_path=report.bash.path,
        shellcheck_path=report.shellcheck.path,
    )
    adapter = OpencodeAdapter(
        opencode_path=report.opencode.path,
        note=lambda message: print(f"[opencode] {message}", flush=True),
    )

    def confirm(round_no: int, script_path: str, script: str, trusted: bool) -> bool:
        print(f"\n===== 第 {round_no} 轮脚本，即将执行 =====\n{script}", flush=True)
        if args.yes:
            return True
        if not sys.stdin.isatty():
            print("非交互环境：默认不执行。确认要执行请加 --yes。", file=sys.stderr)
            return False
        answer = input("执行？[y/N] ").strip().lower()
        return answer == "y"

    def emit(event: RunEvent) -> None:
        if event.type == "phase":
            print(f"[轮 {event.round}] {event.payload.get('phase')}", flush=True)
        elif event.type == "assistant_delta":
            print(event.payload.get("text", ""), end="", flush=True)
        elif event.type == "shellcheck":
            print(f"\nshellcheck：{len(event.payload.get('findings') or ())} 条", flush=True)
        elif event.type == "execute":
            result = event.payload["result"]
            print(
                f"\n执行退出码 {result.exit_code}（{result.duration_ms}ms）\n{result.stdout}",
                flush=True,
            )
        elif event.type == "note":
            print(f"\n[note] {event.payload.get('message')}", flush=True)

    ports = LoopPorts(
        opencode=adapter,
        toolchain=ShellToolchain(report.bash.path, report.shellcheck.path),
        confirm=type("CliConfirm", (), {"confirm": staticmethod(confirm)})(),
        store=run_store,
        emit=emit,
    )

    try:
        result = run_loop(
            LoopInput(
                plan=Path(args.plan).read_text(encoding="utf-8"),
                template=TemplateSpec(
                    id=args.template,
                    body=body,
                    anchors=anchors,
                    trusted=False,
                    placeholders=tuple(meta.placeholders),
                ),
                values={},
                run_dir=run_dir,
                config=config,
                ports=ports,
            )
        )
        print(f"\n结论：{result.outcome}（{result.rounds} 轮）\n运行目录：{run_dir}")
        return 0 if result.outcome == "succeeded" else 1
    finally:
        adapter.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
```

```markdown
<!-- test_fixtures/plan-simple.md -->
# 方案：整理日志目录

## 目标
把 `./logs` 下所有 `.log` 文件按修改时间从新到旧列出，并打印总数。

## 约束
- 只用 bash 内建与常见 POSIX 工具
- 路径含空格时必须正确
- 目录不存在时以非零退出码结束并给出中文提示
```

```bash
# test_fixtures/template-single.tpl.sh
#!/usr/bin/env bash
set -euo pipefail

# @@TU:BODY@@

main() {
  :
}

main "$@"
```

同时在 `pyproject.toml` 加一个入口：

```toml
[project.scripts]
tu-shell-agent = "tu_shell_agent.cli:main"
```

- [ ] **步骤 4：运行验证**

先跑离线全链路（确定性，不需要 opencode）：

运行：`.venv/bin/python -m pytest tests/test_e2e_offline.py -q`
预期：PASS，且 `attempts/1/shellcheck.json` 含 SC2086、`attempts/2/stdout.txt` 有输出、`meta.json` 的 outcome 为 `succeeded`

再跑全部单测：

运行：`.venv/bin/python -m pytest -q`
预期：全部 PASS（真实 opencode 冒烟被 skip）

最后跑一次 CLI（真实端到端）。注意本机 shellcheck 与 opencode 都装在项目内 `tools/`、**不在 PATH**，所以必须显式指路；bash 走 PATH 即可：

运行：`.venv/bin/python -m tu_shell_agent.cli --plan test_fixtures/plan-simple.md --template single --run-root /tmp/tu-runs --shellcheck-path tools/shellcheck --opencode-path tools/opencode --yes`
预期：自检通过并打印三个组件的版本与路径；随后打印每轮阶段、shellcheck 计数、执行输出。
**结论取决于本机 opencode 是否已登录供应商**：未登录时 `serve` 能起来但模型调用会失败 → 三轮契约失败 → `结论：needs_human`（这是设计中的失败路径，不是 bug）；只有已登录且模型可用时才是 `结论：succeeded`。
不依赖 opencode 的确定性闭环由 `tests/test_e2e_offline.py` 覆盖（真实 shellcheck + 真实 bash + 假适配器），那条必须 PASS。
（`--yes` 是必需的：非交互环境下没有 TTY 可确认，CLI 会按规格 §11 默认拒绝执行，结果会是 `cancelled`。）

- [ ] **步骤 5：Commit**

```bash
git add tu_shell_agent/shell_toolchain/facade.py tu_shell_agent/cli.py test_fixtures/ tests/test_e2e_offline.py tests/test_e2e_live.py pyproject.toml
git commit -m "feat(engine): CLI 驱动与端到端验证

离线全链路用真实 shellcheck + 真实 bash 验证「第 2 轮修好」闭环；
真实 opencode 冒烟默认跳过（TU_LIVE=1 启用）。"
```

---

## 完成标准（Plan 1）

全部满足才算 Plan 1 完成：

- [ ] `.venv/bin/python -m pytest -q` 全绿（真实 opencode 冒烟以 skip 计）。
- [ ] 离线全链路用例证明：坏脚本第 1 轮被 shellcheck 拦下、第 2 轮修好、真实 bash 执行成功，且 `attempts/1`、`attempts/2`、`meta.json` 产物齐全。
- [ ] `git log` 有 12 个任务各自的 commit。
- [ ] 分层规则经审查确认：`orchestrator/` 与两个 `*_store/` 不含 `import subprocess` / `import httpx` / PySide6。
- [ ] `TU_LIVE=1` 的真实 opencode 冒烟在**有 opencode 1.x 的机器上**通过（本机若缺 opencode，此项留到 Windows 手测）。

## 已知未覆盖（交给 Plan 2 与 Windows 手测）

- PySide6 界面、三区视图、设置页、环境自检页、历史回放、PyInstaller 打包（Plan 2）。
- Windows 专属：Git Bash 路径探测、`taskkill /T /F`、中文与含空格路径、UTF-8 输出、`serve` 在原生 Windows 的可用性、结构化输出是否真的返回、**权限 deny 是否真的覆盖全局 allow**（规格 §14 手测清单）。

## 自检记录

- **规格覆盖度**：§5 组件（除 `ui`）→ 任务 1–12；§6 状态机 → 任务 11；§7.1–7.6 → 任务 9–11；§8 → 任务 2–3；§9 → 任务 5；§10 → 任务 4；§11 → 任务 6–7；§13 → 任务 11 的契约与依赖分支；§14 → 任务 12 + 完成标准；§12（UI）与 §17 的 M4（打包）→ Plan 2。
- **占位符扫描**：无「TODO/待定/类似任务 N」；每个代码步骤都有可运行代码。任务 6 中故意展示了一处放错位置的 `import os` 并明确标注为错误示范（要求实现者把它移到顶部），这不是待办占位，是给实现者的显式指令。
- **类型一致性**：`types.py` 的 dataclass 字段名在任务 6–11 全程一致（`exit_code`/`timed_out`/`cancelled`/`duration_ms`/`stdout`/`stderr`、`missing_anchors`、`size_bytes`）；`ports.py` 的四个 Protocol 签名在任务 11、12 使用同一形式；`blocks_run` / `SEVERITY_RANK` 只在 `types.py` 定义一次。
- **与 TS 版计划的差异**：仅语言与库（`@opencode-ai/sdk` → `httpx` 直打 HTTP/SSE；vitest → pytest；`child_process` → `subprocess`）。任务边界、状态机、契约、提示词文案均保持一致。

