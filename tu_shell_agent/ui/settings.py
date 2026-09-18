"""设置持久化：一个 JSON 文件，缺省值必须与引擎默认一致（blocking_level="info"）。

为什么不用 QSettings：这里的值大半是"路径"（opencode / bash / shellcheck / 运行根），
用户会想直接打开看、直接改、甚至复制给别人；Windows 上 QSettings 写注册表，
出了问题用户无从检查，而纯文本 JSON 谁都能读能比对。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from ..filetext import write_text_lf

# 应用名只有一个来源：app.py 用它设 QApplication.applicationName，设置路径也由它决定。
# 两处写死成不同的字符串，就会变成"设置保存在 A、读取时找 B"。
APP_NAME = "tu-shell-agent"


def _type_matches(default: object, value: object) -> bool:
    """JSON 值的类型是否与字段默认值同型。

    bool 要单独挡：`True` 在 Python 里是 int 的实例，放进去会让 max_rounds 变成 1。
    """
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, int):
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, type(default))


@dataclass
class AppSettings:
    """界面设置。字段名即 JSON 键名，默认值必须与 `types.RunConfig` 的默认值一致。"""

    run_root: str = ""
    templates_dir: str = ""
    blocking_level: str = "info"      # 与 RunConfig 默认一致（实测 SC2086 是 info 级）
    max_rounds: int = 3
    generate_timeout_ms: int = 300_000
    execute_timeout_ms: int = 120_000
    opencode_path: str = ""
    bash_path: str = ""
    shellcheck_path: str = ""

    def __post_init__(self) -> None:
        # _loaded_from 刻意**不做** dataclass 字段（连类级注解都不能留，否则会被当成字段）：
        # 它只是"这份设置从哪个文件读来的"记忆，一旦成为字段就会被 asdict() 写进 JSON。
        self._loaded_from: Path | None = None

    @classmethod
    def defaults_for(cls, path: Path) -> AppSettings:
        """默认值 + 已记住的保存路径，用于"这份设置属于 path，但还没从它读出内容"。

        两个调用点：`load()` 里文件不存在时；以及界面读到坏文件要退回默认值、却仍要知道
        该往哪写的时候。
        """
        settings = cls()
        settings._loaded_from = Path(path)
        return settings

    @classmethod
    def load(cls, path: Path) -> AppSettings:
        """读设置文件；文件不存在就返回默认值，但**依然记住路径**。

        记住路径这一条不能只在"文件已存在"的分支里做：界面第一次运行（文件还不存在）时
        用户改完设置点"保存"正是这个场景，那时 save() 没有路径可用。
        """
        settings = cls.defaults_for(path)
        if not path.exists():
            return settings
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            # 顶层不是对象的（比如手写成数组）当"坏文件"报错，让调用方决定怎么提示用户
            raise ValueError(f"设置文件顶层必须是 JSON 对象，实际是 {type(raw).__name__}")
        known = {field.name: field for field in fields(cls)}
        for key, value in raw.items():
            spec = known.get(key)
            if spec is None:
                continue                          # 未知键直接忽略（向前兼容：新版本写的键不炸旧版本）
            if not _type_matches(spec.default, value):
                continue                          # 类型也被手改坏了（"3" 这种）就当没写，用默认值兜住
            setattr(settings, key, value)
        return settings

    @property
    def loaded_from(self) -> Path | None:
        """这份设置对应的文件路径（从未 load() 过则为 None）——界面用它如实显示"存到哪"。"""
        return self._loaded_from

    def save(self, path: Path | None = None) -> None:
        """无参时写回 load() 记住的那个路径（界面里最常见的用法）。

        显式传入 path 只写那一个文件，不改动"记忆"——避免"另存一份"把后续的无参保存也带走。
        """
        target = Path(path) if path is not None else self._loaded_from
        if target is None:
            raise ValueError("未指定保存路径，且此前没有 load() 过")
        target.parent.mkdir(parents=True, exist_ok=True)
        write_text_lf(target, json.dumps(asdict(self), ensure_ascii=False, indent=2) + "\n")


def default_templates_dir() -> Path:
    """模板库的默认位置：应用数据目录下的 templates/。

    不用 CLI 那个 CWD 相对的 `.tu-templates`：打包成 exe 之后工作目录是"从哪双击就从哪"，
    模板库会随启动位置忽有忽无。CLI 保持自己的默认值不变（它本来就从项目目录里跑）。
    """
    return default_settings_path().parent / "templates"


def default_settings_path() -> Path:
    """设置的默认位置：Windows 是 %APPDATA%\\<应用名>\\settings.json，Linux 是 ~/.local/share/<应用名>/settings.json。

    用 `QStandardPaths.GenericDataLocation` + 自己的常量拼，而**不用** `AppDataLocation`：
    后者按 `QCoreApplication.applicationName()` 分目录，于是"设置存到哪"取决于调用时应用名
    设没设、以及是谁在跑（无 QApplication 时它会退成 ~/.local/share/settings.json，
    pytest 下又变成 <临时目录>/pytest-qt-qapp/）。那会让保存与读取指向两个不同的文件，
    症状是"设置保存了、重启却没生效"。路径只由一个常量决定，就没有这个自由度。
    Qt 的导入放在函数内部：这个模块因此不依赖 Qt，测试里可以纯文件层面读写设置。
    """
    from PySide6.QtCore import QStandardPaths

    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation)
    return Path(base) / APP_NAME / "settings.json"
