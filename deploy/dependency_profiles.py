"""声明式依赖 profile 的加载、校验和拓扑展开。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml


SCHEMA = "iis-dependency-profiles/1"
KNOWN_SERVICES = {"web", "crawler"}
KNOWN_BROWSERS = {"chromium"}


class ProfileError(ValueError):
    pass


@dataclass(frozen=True)
class DependencyPlan:
    requested: tuple[str, ...]
    profiles: tuple[str, ...]
    requirement_files: tuple[Path, ...]
    services: tuple[str, ...]
    browsers: tuple[str, ...]
    fingerprint: str


class DependencyProfileCatalog:
    def __init__(self, path: Path, source_dir: Path):
        self.path = path.expanduser().resolve()
        self.source_dir = source_dir.expanduser().resolve()
        try:
            payload = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ProfileError(f"无法读取依赖 profile: {self.path}: {exc}") from exc
        except yaml.YAMLError as exc:
            raise ProfileError(f"依赖 profile YAML 无效: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
            raise ProfileError(f"依赖 profile schema 必须是 {SCHEMA}")
        self.defaults = self._name_list(payload.get("defaults"), "defaults")
        raw_profiles = payload.get("profiles")
        if not isinstance(raw_profiles, dict) or not raw_profiles:
            raise ProfileError("profiles 必须是非空映射")
        self.profiles: dict[str, dict[str, Any]] = {}
        for name, raw in raw_profiles.items():
            if not isinstance(name, str) or not name.strip() or not isinstance(raw, dict):
                raise ProfileError("profile 名称和内容无效")
            profile = {
                "description": str(raw.get("description", "")).strip(),
                "requires": self._name_list(raw.get("requires", []), f"{name}.requires"),
                "requirement_files": self._name_list(
                    raw.get("requirement_files", []), f"{name}.requirement_files"),
                "services": self._name_list(raw.get("services", []), f"{name}.services"),
                "browsers": self._name_list(raw.get("browsers", []), f"{name}.browsers"),
            }
            self.profiles[name] = profile
        self._validate_references()

    @staticmethod
    def _name_list(value: Any, field: str) -> tuple[str, ...]:
        if not isinstance(value, list) or any(
                not isinstance(item, str) or not item.strip() for item in value):
            raise ProfileError(f"{field} 必须是字符串列表")
        return tuple(dict.fromkeys(item.strip() for item in value))

    def _validate_references(self) -> None:
        for name in self.defaults:
            if name not in self.profiles:
                raise ProfileError(f"默认 profile 不存在: {name}")
        for name, profile in self.profiles.items():
            for dependency in profile["requires"]:
                if dependency not in self.profiles:
                    raise ProfileError(f"{name} 引用了不存在的 profile: {dependency}")
            unknown_services = set(profile["services"]).difference(KNOWN_SERVICES)
            if unknown_services:
                raise ProfileError(
                    f"{name} 包含未知服务: {', '.join(sorted(unknown_services))}")
            unknown_browsers = set(profile["browsers"]).difference(KNOWN_BROWSERS)
            if unknown_browsers:
                raise ProfileError(
                    f"{name} 包含未知浏览器: {', '.join(sorted(unknown_browsers))}")
            for relative in profile["requirement_files"]:
                path = (self.source_dir / relative).resolve()
                try:
                    path.relative_to(self.source_dir)
                except ValueError as exc:
                    raise ProfileError(f"依赖文件越出源码目录: {relative}") from exc
                if not path.is_file():
                    raise ProfileError(f"依赖文件不存在: {relative}")

    def resolve(self, selected: Iterable[str] | None = None) -> DependencyPlan:
        requested = tuple(dict.fromkeys(selected or self.defaults))
        if not requested:
            raise ProfileError("至少选择一个依赖 profile")
        unknown = [name for name in requested if name not in self.profiles]
        if unknown:
            raise ProfileError(
                f"未知依赖 profile: {', '.join(unknown)}；可用值: "
                f"{', '.join(sorted(self.profiles))}")

        ordered: list[str] = []
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(name: str) -> None:
            if name in visited:
                return
            if name in visiting:
                raise ProfileError(f"依赖 profile 存在循环: {name}")
            visiting.add(name)
            for dependency in self.profiles[name]["requires"]:
                visit(dependency)
            visiting.remove(name)
            visited.add(name)
            ordered.append(name)

        for name in requested:
            visit(name)

        files: list[Path] = []
        services: list[str] = []
        browsers: list[str] = []
        for name in ordered:
            profile = self.profiles[name]
            for relative in profile["requirement_files"]:
                path = (self.source_dir / relative).resolve()
                if path not in files:
                    files.append(path)
            for service in profile["services"]:
                if service not in services:
                    services.append(service)
            for browser in profile["browsers"]:
                if browser not in browsers:
                    browsers.append(browser)
        fingerprint_payload = {
            "schema": SCHEMA,
            "profiles": ordered,
            "requirements": [
                str(path.relative_to(self.source_dir)) for path in files],
            "contents": [hashlib.sha256(path.read_bytes()).hexdigest() for path in files],
        }
        fingerprint = hashlib.sha256(json.dumps(
            fingerprint_payload, sort_keys=True,
            separators=(",", ":")).encode("utf-8")).hexdigest()
        return DependencyPlan(
            requested=requested,
            profiles=tuple(ordered),
            requirement_files=tuple(files),
            services=tuple(services),
            browsers=tuple(browsers),
            fingerprint=fingerprint,
        )


def parse_profile_arguments(values: Iterable[str] | None) -> tuple[str, ...] | None:
    if not values:
        return None
    names = [
        name.strip()
        for value in values
        for name in value.split(",")
        if name.strip()
    ]
    return tuple(dict.fromkeys(names))
