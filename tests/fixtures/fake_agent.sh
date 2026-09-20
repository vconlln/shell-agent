#!/usr/bin/env bash
# 假命令行 agent：给 tests/test_agent_backends.py 驱动**真实的** CliAgentAdapter 用。
#
# 为什么必须是"假 CLI"而不是替身对象：这个适配器最容易写错的地方全在进程边界上 ——
# 命令行怎么拼、提示词怎么给、JSON 行怎么读、取消/超时怎么杀进程树。用替身对象把这些
# 全绕过去了，等于不测。本机没有 codeagent，所以用一个可执行脚本扮演它。
#
# 它做四件事：
#   1. `--version` 打印一行版本（供探测用例断言）；
#   2. 把收到的参数逐行追加到 $FAKE_AGENT_ARGS_FILE，最后一次调用以 `argc=<个数>` 与
#      `<<END>>` 收尾；
#
#      **读这个文件的坑（实测踩过）**：一行一个参数，但**带换行的参数会跨多行** ——
#      `--append-system-prompt` 的值是好几十行的系统提示。于是"某个旗标的下一行就是它的值"
#      这种断言在**多行参数**上必然读错（表现为"系统提示被拆成了很多个参数"的假象）。
#      要断"每个旗标的值是**一个** argv 元素"，用 `argc=` 那一行去对总数（多行内容不会让
#      argc 变大）；要断多行参数的完整内容，去读 $FAKE_AGENT_STDIN_FILE 或另存一份文件。
#      为什么不改成 NUL 分隔：那份记录还要人能直接看，且 `argc=` 已经能锁住"参数个数"这件事。
#   3. 把 stdin（提示词）写到 $FAKE_AGENT_STDIN_FILE；
#   4. 按 stream-json 吐事件行，形状照 claude 2.1.112 实测输出：
#      system/init（tools 里已剔除 --disallowedTools 指定的工具）→ assistant(thinking)
#      → 一行非 JSON 噪音 → 一个未知事件类型 → assistant(text，与上一条**同一个 message id**)
#      → 第二条 assistant 消息 → result。
#
# 环境变量开关（错误路径与进程树用例用）：
#   FAKE_AGENT_SLEEP       先睡这么多秒（测超时/取消）
#   FAKE_AGENT_CHILD_SLEEP 起一个后台子进程睡这么久（测"杀整棵进程树"）
#   FAKE_AGENT_PID_FILE    把自身 pid 与子进程 pid 逐行写到这里
#   FAKE_AGENT_EXIT        退出码非零（测"退出码 + stderr"这条错误路径）
#   FAKE_AGENT_ERROR_RESULT 1 = 退出码 0，但 result 事件里 is_error 为真
#   FAKE_AGENT_RESULT_ONLY  1 = 只发 result 事件（不发 assistant），测兜底取正文
#   FAKE_AGENT_SESSION_ID   覆盖事件里报回来的会话 id（测交叉核对）
#   FAKE_AGENT_BAD_TEXT    1 = 助手正文不带四段标记（测契约解析失败那条路）
#   FAKE_AGENT_SILENT      1 = 一个助手文本都不发（测"没有输出任何助手文本"）
set -u

ARGS_FILE="${FAKE_AGENT_ARGS_FILE:-}"
STDIN_FILE="${FAKE_AGENT_STDIN_FILE:-}"
PID_FILE="${FAKE_AGENT_PID_FILE:-}"

append_line() {
  [ -n "$ARGS_FILE" ] && printf '%s\n' "$1" >> "$ARGS_FILE"
}

# ── 1) 版本探测：最先处理，且不吃 stdin ─────────────────────────────────
for arg in "$@"; do
  if [ "$arg" = "--version" ] || [ "$arg" = "-v" ]; then
    echo "fake-agent 2.1.112 (Fake Code)"
    exit 0
  fi
done

# ── 2) 记录参数 ────────────────────────────────────────────────────────
for arg in "$@"; do
  append_line "$arg"
done
# argc 是"参数个数"而不是行数：多行参数在下面的记录里会跨多行，行数对不上参数个数。
append_line "argc=$#"
append_line "<<END>>"

# ── 3) 记录提示词（读干 stdin，同时也让写端不会撞 BrokenPipe）────────────
if [ -n "$STDIN_FILE" ]; then
  cat > "$STDIN_FILE"
else
  cat > /dev/null
fi

# ── 4) 进程树用例需要的 pid；子进程先起，免得还没记就被杀 ──────────────────
if [ -n "$PID_FILE" ]; then
  printf '%s\n' "$$" >> "$PID_FILE"
fi
if [ -n "${FAKE_AGENT_CHILD_SLEEP:-}" ]; then
  sleep "$FAKE_AGENT_CHILD_SLEEP" &
  if [ -n "$PID_FILE" ]; then
    printf '%s\n' "$!" >> "$PID_FILE"
  fi
fi
if [ -n "${FAKE_AGENT_SLEEP:-}" ]; then
  sleep "$FAKE_AGENT_SLEEP"
fi
if [ -n "${FAKE_AGENT_EXIT:-}" ]; then
  echo "fake-agent: 上游拒绝了这个请求（模拟失败）" >&2
  exit "$FAKE_AGENT_EXIT"
fi

# ── 拼事件 ────────────────────────────────────────────────────────────
# 会话 id：命令行里 --session-id / --resume 后面那个值；没有就用一个固定值。
SESSION_ID="${FAKE_AGENT_SESSION_ID:-}"
prev=""
if [ -z "$SESSION_ID" ]; then
  SESSION_ID="fake-session"
  for arg in "$@"; do
    case "$prev" in
      --session-id | --resume) SESSION_ID="$arg" ;;
    esac
    prev="$arg"
  done
fi

# 被禁用的工具：模拟真实 CLI 的行为（它会把它们从 init 事件的 tools 列表里剔除）。
DENIED=""
prev=""
for arg in "$@"; do
  case "$prev" in
    --disallowedTools | --disallowed-tools) DENIED="$arg" ;;
  esac
  prev="$arg"
done
TOOLS=""
for tool in Task Read Write Edit Bash Glob Grep WebFetch AskUserQuestion; do
  case ",$DENIED," in
    *",$tool,"*) continue ;;
  esac
  TOOLS="${TOOLS}\"${tool}\","
done
TOOLS="${TOOLS%,}"

# 脚本正文用 JSON 转义后的换行（字面量 \n，单引号保证 bash 不解释它）。
SCRIPT_TEXT='===TU-SCRIPT===\n#!/usr/bin/env bash\nset -euo pipefail\n# @@TU:BODY@@\necho fake-ok\n===TU-NOTES===\n按方案实现；保留全部锚点。\n===TU-ASSUMPTIONS===\n- 假设一：目标目录存在\n- 假设二：不需要提权\n===TU-END==='

printf '{"type":"system","subtype":"init","cwd":"%s","session_id":"%s","tools":[%s],"permissionMode":"default","claude_code_version":"2.1.112"}\n' \
  "$PWD" "$SESSION_ID" "$TOOLS"

if [ -n "${FAKE_AGENT_BAD_TEXT:-}" ]; then
  SCRIPT_TEXT='这是一段没有标记的回复，模型没有遵守输出格式要求。'
fi

if [ -n "${FAKE_AGENT_SILENT:-}" ]; then
  printf '{"type":"system","subtype":"init","session_id":"%s"}\n' "$SESSION_ID"
elif [ -z "${FAKE_AGENT_RESULT_ONLY:-}" ]; then
  # 同一条消息的 thinking 帧（没有 text）：增量提取必须跳过它
  printf '{"type":"assistant","message":{"id":"msg_fake_1","role":"assistant","content":[{"type":"thinking","thinking":"先想一下","signature":"sig"}]},"session_id":"%s"}\n' \
    "$SESSION_ID"
  # 命令行日志噪音：不是 JSON，解析器必须忽略
  printf 'fake-agent: 正在读取工作目录\n'
  # 未来的未知事件类型：同样必须忽略，不许把这一轮弄崩
  printf '{"type":"未来事件类型","数据":{"a":1},"session_id":"%s"}\n' "$SESSION_ID"
  # 正文（与 thinking 帧**同一个 id**：只许发一次）
  printf '{"type":"assistant","message":{"id":"msg_fake_1","role":"assistant","content":[{"type":"text","text":"%s"}]},"session_id":"%s"}\n' \
    "$SCRIPT_TEXT" "$SESSION_ID"
  # 第二条消息：增量回调再收到一段
  printf '{"type":"assistant","message":{"id":"msg_fake_2","role":"assistant","content":[{"type":"text","text":"补充说明。"}]},"session_id":"%s"}\n' \
    "$SESSION_ID"
fi

if [ -n "${FAKE_AGENT_SILENT:-}" ]; then
  # 一个助手文本都没有：连 result 也不发（否则兜底会取到 result 里的正文）
  exit 0
fi

if [ -n "${FAKE_AGENT_ERROR_RESULT:-}" ]; then
  printf '{"type":"result","subtype":"error","is_error":true,"result":"上游拒绝：no credentials","session_id":"%s"}\n' \
    "$SESSION_ID"
  exit 0
fi

printf '{"type":"result","subtype":"success","is_error":false,"result":"%s","session_id":"%s","num_turns":1,"permission_denials":[]}\n' \
  "$SCRIPT_TEXT" "$SESSION_ID"
exit 0
