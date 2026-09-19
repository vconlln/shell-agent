#!/usr/bin/env bash
set -euo pipefail

LOG_PREFIX="{{log_prefix:task}}"
WORK_DIR=""
cleanup() {
  [[ -n "$WORK_DIR" && -d "$WORK_DIR" ]] && rm -rf "$WORK_DIR"
}
trap cleanup EXIT
trap 'echo "[$LOG_PREFIX] 第 $LINENO 行失败" >&2' ERR

log() { printf '[%s] %s\n' "$LOG_PREFIX" "$*"; }
die() { printf '[%s] 错误: %s\n' "$LOG_PREFIX" "$*" >&2; exit 1; }

WORK_DIR="$(mktemp -d)"
log "临时目录 $WORK_DIR"

# @@TU:BODY@@

log "完成"
