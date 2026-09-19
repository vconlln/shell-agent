#!/usr/bin/env bash
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
