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

echo "[2/4] 准备依赖…"
# 打包**只需要** PySide6 与 pyinstaller（spec 用 pathex 指向仓库根，不要求项目被安装）。
# 所以这三步的失败后果不一样，处理方式也就不一样：
#   1) 升级 pip：失败只警告（老 pip 也能干活，多半只是网络不通）；
#   2) PySide6 + pyinstaller：失败必须停 —— 没有它们打不出产物；
#   3) 可编辑安装项目（带测试依赖）：**失败只警告**。它只影响"能不能跑 pytest"与
#      控制台入口，不影响打包。实测在网络抖动时它会因为拉不到 setuptools 而失败，
#      那时把整次打包判死是没道理的（产物其实完全能出）。
"$PY" -m pip install --upgrade pip >/dev/null || echo "  （升级 pip 失败，继续）"

echo "  - PySide6 + pyinstaller（打包必需）"
"$PY" -m pip install "PySide6>=6.11" pyinstaller || exit 1

echo "  - 项目本身与测试依赖（可选，失败不影响出产物）"
if ! "$PY" -m pip install -e ".[ui,dev]"; then
    echo "  ⚠ 可编辑安装失败（多半是网络/代理拉不到构建依赖）。"
    echo "    打包继续；但 pytest 与 tu-shell-agent 控制台入口可能不可用。"
    echo "    网络恢复后单独跑一次：$PY -m pip install -e \".[ui,dev]\""
fi

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

# 插件抽查：PyInstaller 漏收 Qt 插件时**不会报错**，而后果是"窗口起不来"或
# "自绘模糊读不了壁纸、悄悄退化成半透明"。两个平台抽查同一组能力（平台插件 + jpeg 解码）。
PLUGINS="$REPO_ROOT/dist/linux/tu-shell-agent/_internal/PySide6/Qt/plugins"
for required in "platforms/libqxcb.so" "platforms/libqwayland.so" "imageformats/libqjpeg.so"; do
    if [[ ! -f "$PLUGINS/$required" ]]; then
        echo "缺 Qt 插件：$required" >&2
        echo "  平台插件缺失会导致窗口起不来；imageformats 缺失会让自绘模糊静默失效。" >&2
        exit 1
    fi
done
echo "插件抽查通过：platforms（xcb + wayland）与 imageformats/jpeg 都在"

echo
echo "============================================================"
echo " 产物：$APP"
echo " 体积：$(du -sh "$REPO_ROOT/dist/linux/tu-shell-agent" | cut -f1)"
echo " 启动界面：$APP"
echo " 生成脚本需要 opencode 已登录：opencode auth login"
echo " 手测清单见 packaging/build.md 第 6 节。"
echo "============================================================"
