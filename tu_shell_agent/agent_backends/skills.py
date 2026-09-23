"""技能（skill）：一段可复用的"怎么做这件事"的说明，注入到系统提示里。

用户问："可不可以增加 skill 什么的"。这里的 skill 就是**纯提示词资产**：

```
skills/                       ← 仓库自带（随代码版本化、可评审、可分享）
  shell-strict/SKILL.md       ← 一个技能一个目录，正文是给模型看的规矩
```

文件形状（front matter 可选，用 `---` 包住）：

```markdown
---
name: shell-strict
description: 写 bash 脚本时的硬规矩（引号、set -euo pipefail、危险命令保护）
---
正文：真正的指令，会被原样拼进系统提示。
```

设计上刻意保持"最小可信"：
- **不是代码、不执行任何东西** —— 技能只是文本，和方案文档一样进提示词；
- 解析**宽容**：没有 front matter、没有 description、文件是用 `.md` 直接放在根目录，
  都能认（用户手写的文件不该因为格式挑剔而被无视）；
- 读不出来的文件**跳过并说明**（在界面上能看到"哪个技能没被加载"），而不是静默消失。

与 opencode 的关系：opencode 那边的"技能"是它自己的插件/agent 定义，装在它自己的目录里、
只有用它的时候才有；这里的技能是我们自己的资产，任何后端都能用（见 `compose_system_prompt`）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SKILL_FILENAME = "SKILL.md"
_FRONT_MATTER = "---"

# 目录说明文件不是技能：`README.md` 与下划线开头的文件跳过。
# （不跳过的话它会被当成一个叫 README 的技能注入系统提示 —— 自己验的时候立刻撞到了。）
_NOT_A_SKILL = {"readme", "index", "skill"}


@dataclass(frozen=True, slots=True)
class Skill:
    """一个技能：名字、一句话说明、正文。"""

    name: str
    description: str
    body: str
    path: str = ""

    def render(self) -> str:
        """拼进系统提示时的那一段（带标题，便于模型区分多个技能）。"""
        head = f"## 技能：{self.name}"
        if self.description:
            head += f"（{self.description}）"
        return f"{head}\n{self.body.strip()}"


def parse_skill(text: str, *, fallback_name: str, path: str = "") -> Skill:
    """把一份技能文本解析成 `Skill`（front matter 可有可无，解析不了就用整篇当正文）。"""
    body = text.strip()
    name = fallback_name
    description = ""
    if body.startswith(_FRONT_MATTER):
        end = body.find(_FRONT_MATTER, len(_FRONT_MATTER))
        if end > 0:
            meta = body[len(_FRONT_MATTER) : end]
            body = body[end + len(_FRONT_MATTER) :].strip()
            for line in meta.splitlines():
                key, _, value = line.partition(":")
                key = key.strip().lower()
                value = value.strip().strip('"').strip("'")
                if key == "name" and value:
                    name = value
                elif key in ("description", "desc", "说明") and value:
                    description = value
    return Skill(name=name, description=description, body=body, path=path)


def discover_skills(directory: str | Path) -> tuple[list[Skill], list[str]]:
    """扫描技能目录，返回 (技能列表, 问题列表)。

    支持两种摆放：`<dir>/<name>/SKILL.md`（推荐）与 `<dir>/<name>.md`（懒人写法）。
    问题列表里放"读不了的文件"的说明 —— 界面上要能显示出来，否则用户会以为技能没生效。
    """
    root = Path(directory) if directory else None
    if root is None or not root.is_dir():
        return [], []
    skills: list[Skill] = []
    problems: list[str] = []
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        if child.is_dir():
            candidate = child / SKILL_FILENAME
            if not candidate.is_file():
                nested = sorted(child.glob("*.md"))
                if not nested:
                    continue
                candidate = nested[0]
            fallback = child.name
        elif child.suffix.lower() == ".md":
            candidate = child
            fallback = child.stem
        else:
            continue
        if candidate.parent == root and candidate.stem.lower() in _NOT_A_SKILL:
            continue
        if candidate.name.startswith("_"):
            continue
        # 提示里带上**相对路径**：技能目录下往往有好几个，只说"SKILL.md 是空的"
        # 用户根本不知道是哪一个。
        try:
            label = str(candidate.relative_to(root))
        except ValueError:  # pragma: no cover - candidate 一定在 root 下
            label = str(candidate)
        try:
            text = candidate.read_text(encoding="utf-8")
        except OSError as error:
            problems.append(f"{label} 读不出来：{error}")
            continue
        skill = parse_skill(text, fallback_name=fallback, path=str(candidate))
        if not skill.body.strip():
            problems.append(f"{label} 是空的，已跳过")
            continue
        skills.append(skill)
    return skills, problems


# 随应用交付的技能。**打包产物里没有 `skills/` 目录**（PyInstaller 只收代码），所以
# 首次用到技能时按需写进用户数据目录 —— 与内置模板（`template_store/builtins.py`）同一套做法：
# 一个平台无关的可靠来源，不依赖工作目录、也不依赖打包时收了哪些文件。
# 仓库检出里那份 `skills/shell-strict/SKILL.md` 与这里必须一致（有用例比对）。
BODY_SHELL_STRICT = """\
# 写 shell 脚本的硬规矩

生成脚本时按这份清单自查；每一条都是评审与 shellcheck 会盯的地方。

## 严格模式

- 第一行 shebang 必须是 `#!/usr/bin/env bash`（不是 `sh`：脚本里要用 `[[ ]]`、数组）。
- 紧跟着 `set -euo pipefail`；不要用 `set -x`（会把敏感值打进日志）。

## 引号与变量

- 所有变量展开都加双引号：`"$dir"`、`"${arr[@]}"`；数组展开不要漏 `[@]`。
- 用 `[[ ]]` 而不是 `[ ]`；字符串比较用 `==`，数值比较用 `-eq` 系列。
- 命令替换一律 `"$(...)"`；不要用反引号（难以嵌套，也容易被引号规则坑）。

## 参数与默认值

- 必需的参数用 `${1:?用法: script.sh <dir>}` 这种写法，缺失时**立刻**报错退出。
- 可选参数给默认值：`target="${1:-.}"`。

## 危险动作

- 删除类命令必须先校验目标非空且不是 `/`：`[[ -n "$target" && "$target" != "/" ]]`。
- `rm -rf` 只允许出现在明确的、已校验的变量上；能用 `find ... -delete` 就用它。
- 覆盖文件前先备份（`cp -a "$file" "$file.bak"`），并在输出里说明备份位置。

## 错误与输出

- 错误信息走 stderr：`echo "错误：…" >&2`；正常输出走 stdout。
- 退出码要有意义：用法错误用 2，运行失败用 1。
- 关键步骤前后各打一行进度（`echo "==> 正在 …"`），让执行日志能读懂。

## 可重复执行

- 脚本应当可以重复跑：先判断再动作（`[[ -d "$dir" ]] || mkdir -p "$dir"`）。
- 不要依赖当前工作目录：需要目录时显式 `cd` 或用绝对路径。"""


BUILTIN_SKILLS: tuple[Skill, ...] = (
    Skill(
        name="shell-strict",
        description="写 bash 脚本时的硬规矩：引号、严格模式、危险命令保护、可回滚",
        body=BODY_SHELL_STRICT,
    ),
)


def ensure_builtin_skills(directory: str | Path) -> list[str]:
    """把内置技能写进目录（已存在的不动），返回**这次新建了哪些**。

    只在缺失时写：用户改过或删过某个技能，不该被下一次启动又还原回去。
    写不进去（目录只读等）就静默跳过 —— 技能是锦上添花，不该让对话起不来。
    """
    root = Path(directory)
    created: list[str] = []
    for skill in BUILTIN_SKILLS:
        target = root / skill.name / SKILL_FILENAME
        if target.exists():
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            text = f"---\nname: {skill.name}\ndescription: {skill.description}\n---\n\n{skill.body}\n"
            target.write_text(text, encoding="utf-8")
            created.append(skill.name)
        except OSError:
            continue
    return created


def select_skills(skills: list[Skill], wanted: str = "") -> list[Skill]:
    """按名字筛选（逗号分隔）；留空表示全用。名字对不上的会被忽略（不报错）。"""
    names = [item.strip() for item in (wanted or "").replace("，", ",").split(",")]
    names = [item for item in names if item]
    if not names:
        return list(skills)
    wanted_set = {item.lower() for item in names}
    return [skill for skill in skills if skill.name.lower() in wanted_set]


def compose_system_prompt(base: str, skills: list[Skill]) -> str:
    """把基础系统提示与技能正文拼起来（技能在后：它们是"这次任务的具体规矩"）。

    没有任何技能时原样返回 `base` —— 于是这个功能对不用它的人零影响。
    """
    text = (base or "").strip()
    if not skills:
        return text
    parts = [skill.render() for skill in skills]
    joined = "\n\n".join(parts)
    if not text:
        return joined
    return f"{text}\n\n# 可用技能（按需遵守）\n\n{joined}"
