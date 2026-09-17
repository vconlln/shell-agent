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
