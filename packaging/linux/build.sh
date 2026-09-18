#!/usr/bin/env bash
# 一键打包（Linux）：建 venv → 装依赖 → PyInstaller → 产物自检。
#
# 产物：dist/linux/tu-shell-agent/tu-shell-agent
# 用法：bash packaging/linux/build.sh      （在仓库任意位置执行都可以）
#
# 与 Windows 的对应脚本是 packaging/windows/build.bat，两者共用同一份 spec 与入口：
#   packaging/tu-shell-agent.spec  ← 平台无关，PyInstaller 不支持交叉编译，各打各的
#   packaging/entry.py             ← 绝对导入的薄壳入口（相对导入的 app.py 不能直接当入口）
set -euo pipefail

# 脚本在 packaging/linux/ 下，仓库根是它上面两层。用 BASH_SOURCE 而不是 $0：
# 从别处 `bash packaging/linux/build.sh` 调用时 $0 是相对路径。
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"
echo "仓库根目录：$REPO_ROOT"

VENV="$REPO_ROOT/.venv"
PY="$VENV/bin/python"

if [[ -x "$PY" ]]; then
    echo "[1/4] 已存在 .venv，跳过创建"
else
    echo "[1/4] 创建虚拟环境 .venv …"
    python3 -m venv "$VENV"
    PY="$VENV/bin/python"
fi

echo "[2/4] 安装依赖（PySide6 + pyinstaller + 测试依赖）…"
"$PY" -m pip install --upgrade pip >/dev/null
"$PY" -m pip install -e ".[ui,dev]"
"$PY" -m pip install pyinstaller

echo "[3/4] 打包（one-folder）…"
# --distpath/--workpath 是相对当前目录解析的，所以上面必须 cd 到仓库根。
# 两个平台分目录：dist/linux 与 dist/windows 各自独立，互不覆盖。
"$PY" -m PyInstaller --clean --noconfirm \
    --distpath "$REPO_ROOT/dist/linux" \
    --workpath "$REPO_ROOT/build/linux" \
    "$REPO_ROOT/packaging/tu-shell-agent.spec"

APP="$REPO_ROOT/dist/linux/tu-shell-agent/tu-shell-agent"
echo "[4/4] 产物自检（应打印 exit=0）…"
# 必须无头：这会真的构造并绘制一遍主窗口，没有显示环境时要用 Qt 的 offscreen 后端。
if QT_QPA_PLATFORM=offscreen "$APP" --self-test; then
    echo "自检通过：exit=0"
else
    status=$?
    echo "自检失败：exit=$status" >&2
    exit 1
fi

echo
echo "============================================================"
echo " 产物：$APP"
echo " 体积：$(du -sh "$REPO_ROOT/dist/linux/tu-shell-agent" | cut -f1)"
echo " 启动界面：$APP"
echo " 生成脚本需要 opencode 已登录：opencode auth login"
echo " 手测清单见 packaging/build.md 第 6 节。"
echo "============================================================"
