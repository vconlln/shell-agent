"""文本落盘的唯一入口：**按字节写**，绝不让平台改写换行。

为什么不能直接用 `Path.write_text()`：它的 `newline` 默认是 None，也就是"翻译"\n 为
`os.linesep` —— 在 Windows 上是 `\r\n`。这不只影响观感：

- 生成的脚本一旦落成 CRLF，shellcheck 会给**每一行**（含 shebang）报 SC1017（error 级），
  默认阻断级别 info 必拦 → 每轮都不执行、回灌模型 → 模型交回来的内容还是被写成 CRLF，
  于是烧满轮次收在 needs_human。**Windows 上整个主循环一次都跑不到 succeeded。**
- `to_lf()` 的归一化也会被原地抵消：先归一化成 LF，写盘时又被改回 CRLF。
- 读回来时 `read_text()` 又会把 CRLF 悄悄读成 LF，所以用 read_text 写的断言在 Windows 上
  照样绿 —— 这个坑只能靠"按字节断言"发现。

本模块的 `write_text_lf` 是**唯一**该被用来写脚本/证据/配置的地方（引擎、适配器、界面都算）。
"""

from __future__ import annotations

from pathlib import Path

from .template_store.render import to_lf


def write_text_lf(path: Path, text: str) -> None:
    """把文本按 LF 落盘（先归一化，再按字节写，绕开平台换行翻译）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(to_lf(text).encode("utf-8"))
