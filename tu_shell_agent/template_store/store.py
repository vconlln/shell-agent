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
        metas = self.list()
        for meta in metas:
            if meta.id == template_id:
                meta.trusted = trusted
                self._write_index(metas)
                return
        raise KeyError(f"模板不存在：{template_id}")
