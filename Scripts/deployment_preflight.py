#!/usr/bin/env python3
"""Validate deployment inputs without starting IIS services or exposing secrets."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from GlobalConfig import (  # noqa: E402
    CONFIG_PATH, DATA_PATH, MODEL_PATH, resolve_ai_client_config_file,
    resolve_collector_config_file, resolve_iis_config_files, runtime_path_report,
)

CONFIG_DIR = Path(CONFIG_PATH)
DATA_DIR = Path(DATA_PATH)
MODEL_DIR = Path(MODEL_PATH)


def fail(message: str) -> None:
    raise ValueError(message)


def validate_web() -> None:
    from AIClientCenter import load_config, validate_config
    from IISConfig import load_iis_config

    iis_path = Path(resolve_iis_config_files()[0])
    ai_path = Path(resolve_ai_client_config_file())
    if not iis_path.is_file():
        fail(f"missing IIS_CONFIG: {iis_path} (the example fallback is not valid for deployment)")
    if not ai_path.is_file():
        fail(f"missing AI_CLIENT_CONFIG: {ai_path}")

    config = load_iis_config(iis_path)
    clients, limits = load_config(ai_path)
    errors, warnings = validate_config(clients, limits)
    if errors:
        fail("invalid AI client config: " + "; ".join(errors))
    if not clients:
        fail(f"AI client config has no enabled clients: {ai_path}")
    if not config.settings.access.collector_tokens:
        fail("access.collector_tokens must contain at least one deployment token")
    configured_groups = {client.group_id for client in clients.values()}
    translation = config.settings.optional_subsystems.translation
    if translation.enabled and translation.client_group not in configured_groups:
        fail(
            "translation client group is not provided by any enabled AI client: "
            f"{translation.client_group}"
        )
    for warning in warnings:
        print(f"WARNING: {warning}")
    print(f"OK web: {iis_path} ({len(clients)} enabled AI clients)")


def load_collector(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"invalid collector config {path}: {exc}")
    collector = data.get("collector") if isinstance(data, dict) else None
    if not isinstance(collector, dict):
        fail(f"collector config must contain an object named 'collector': {path}")
    return collector


def resolve_collector_task_dirs(collector: dict) -> dict:
    """读取当前领域目录契约，并兼容尚未迁移的旧配置。"""
    task_dirs = collector.get("domain_task_dirs")
    if task_dirs is None:
        task_dirs = collector.get("task_dirs")
    if not isinstance(task_dirs, dict) or not task_dirs:
        fail("collector.domain_task_dirs must contain at least one task directory")
    return task_dirs


def validate_crawler() -> None:
    path = Path(resolve_collector_config_file())
    if not path.is_file():
        fail(f"missing IIS_COLLECTOR_CONFIG: {path} (the example fallback is not valid for deployment)")
    collector = load_collector(path)
    if not str(collector.get("submit_ihub_url") or "").strip():
        fail("collector.submit_ihub_url is required")
    if not str(collector.get("default_token") or "").strip():
        fail("collector.default_token is required")
    task_dirs = resolve_collector_task_dirs(collector)
    missing = []
    for value in task_dirs.values():
        task_path = Path(value)
        candidates = (path.parent / task_path, PROJECT_DIR / task_path, CONFIG_DIR / task_path)
        if not task_path.is_absolute() and not any(item.is_dir() for item in candidates):
            missing.append(str(value))
        elif task_path.is_absolute() and not task_path.is_dir():
            missing.append(str(value))
    if missing:
        fail("collector task directories do not exist: " + ", ".join(missing))
    importlib.import_module("crawl4ai")
    print(f"OK crawler: {path} ({len(task_dirs)} task directories; Crawl4AI importable)")


def validate_cross_config() -> None:
    from IISConfig import load_iis_config

    iis_path = Path(resolve_iis_config_files()[0])
    collector_path = Path(resolve_collector_config_file())
    config = load_iis_config(iis_path)
    collector = load_collector(collector_path)
    token = str(collector.get("default_token") or "").strip()
    if token not in config.settings.access.collector_tokens:
        fail("collector.default_token is not present in access.collector_tokens")
    print("OK integration: collector token is accepted by the web service")


def validate_vectordb() -> None:
    model = Path(os.getenv("VECTOR_MODEL", MODEL_DIR / "bge-m3"))
    db_path = Path(os.getenv("VECTOR_DB_PATH", DATA_DIR / "VectorDB"))
    agg_path = Path(os.getenv(
        "VECTOR_AGG_STORE_DIR", PROJECT_DIR / "aggregation_store"))
    if not model.is_dir():
        fail(f"vector model directory does not exist: {model}")
    if not (model / "config.json").is_file():
        fail(f"vector model is incomplete (config.json missing): {model}")
    for path in (db_path, agg_path):
        path.mkdir(parents=True, exist_ok=True)
        if not os.access(path, os.W_OK):
            fail(f"vector storage directory is not writable: {path}")
    importlib.import_module("chromadb")
    importlib.import_module("sentence_transformers")
    print(f"OK vectordb: model={model}, db={db_path}, aggregation={agg_path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--component", choices=("web", "crawler", "vectordb", "all"), default="all")
    args = parser.parse_args()
    report = runtime_path_report()
    print(
        "Selected runtime root: "
        f"{report['runtime_root']['path']} "
        f"(by {report['runtime_root']['selected_by']})")
    for name, selected in report["configs"].items():
        print(
            f"Selected {name} config: {selected['path']} "
            f"(by {selected['selected_by']})")
    try:
        if args.component in ("web", "all"):
            validate_web()
        if args.component in ("crawler", "all"):
            validate_crawler()
        if args.component == "all":
            validate_cross_config()
        if args.component in ("vectordb", "all"):
            validate_vectordb()
    except Exception as exc:
        print(f"PREFLIGHT FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print("Deployment preflight passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
