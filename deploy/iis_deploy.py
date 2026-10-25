#!/usr/bin/env python3
"""IIS Linux 部署控制器；M0 仅提供只读 doctor。"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable


DEPLOY_DIR = Path(__file__).resolve().parent
PROJECT_DIR = DEPLOY_DIR.parent
SUPPORTED_SYSTEMS = {
    ("ubuntu", "22.04"),
    ("ubuntu", "24.04"),
    ("debian", "12"),
}


@dataclass(frozen=True)
class CheckResult:
    code: str
    status: str
    summary: str
    detail: str


def _read_os_release(path: Path = Path("/etc/os-release")) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"').strip("'")
    return values


def check_platform() -> CheckResult:
    if platform.system() != "Linux":
        return CheckResult(
            "platform", "fail", "当前不是 Linux",
            "首版单命令安装器只支持 Linux。")
    release = _read_os_release()
    system_id = release.get("ID", "unknown").lower()
    version = release.get("VERSION_ID", "unknown")
    if (system_id, version) in SUPPORTED_SYSTEMS:
        return CheckResult(
            "platform", "pass", f"支持的系统：{system_id} {version}",
            "属于首版安装器支持矩阵。")
    return CheckResult(
        "platform", "warn", f"尚未列入支持矩阵：{system_id} {version}",
        "doctor 可运行，但正式安装器不会在未知发行版上自动调用包管理器。")


def check_architecture() -> CheckResult:
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        return CheckResult(
            "architecture", "pass", f"架构：{machine}",
            "属于首版支持架构。")
    return CheckResult(
        "architecture", "warn", f"尚未验证的架构：{machine}",
        "部分 Python wheel、Chromium 或 VectorDB 依赖可能不可用。")


def check_python() -> CheckResult:
    version = sys.version_info
    rendered = f"{version.major}.{version.minor}.{version.micro}"
    if version < (3, 11):
        return CheckResult(
            "python", "fail", f"Python {rendered} 过旧",
            "IIS 要求 Python 3.11+。")
    if version[:2] not in {(3, 11), (3, 12)}:
        return CheckResult(
            "python", "warn", f"Python {rendered} 尚未列入首版验证矩阵",
            "部署工具可运行，但生产依赖将优先验证 Python 3.11/3.12。")
    return CheckResult(
        "python", "pass", f"Python {rendered}",
        "版本符合首版验证矩阵。")


def check_resources() -> list[CheckResult]:
    results: list[CheckResult] = []
    memory_kib = 0
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                memory_kib = int(line.split()[1])
                break
    except (OSError, ValueError, IndexError):
        pass
    if memory_kib:
        memory_gib = memory_kib / 1024 / 1024
        status = "pass" if memory_gib >= 3.5 else "warn"
        results.append(CheckResult(
            "memory", status, f"内存：{memory_gib:.1f} GiB",
            "core profile 建议至少 4 GiB。"))
    else:
        results.append(CheckResult(
            "memory", "warn", "无法读取内存容量",
            "请人工确认 core profile 至少有 4 GiB 内存。"))

    usage = shutil.disk_usage(PROJECT_DIR)
    free_gib = usage.free / 1024 ** 3
    results.append(CheckResult(
        "disk", "pass" if free_gib >= 30 else "warn",
        f"项目所在文件系统可用空间：{free_gib:.1f} GiB",
        "core profile 建议至少保留 30 GiB；VectorDB 另计模型和索引空间。"))
    return results


def check_commands() -> list[CheckResult]:
    results: list[CheckResult] = []
    for command, required in (
            ("git", True), ("systemctl", True), ("curl", True),
            ("tmux", False)):
        found = shutil.which(command)
        if found:
            results.append(CheckResult(
                f"command.{command}", "pass", f"找到 {command}", found))
        else:
            status = "fail" if required else "warn"
            purpose = "安装器运行前必需" if required else "可由后续安装阶段补齐"
            results.append(CheckResult(
                f"command.{command}", status, f"缺少 {command}", purpose))
    return results


def check_submodules() -> CheckResult:
    if not (PROJECT_DIR / ".gitmodules").is_file():
        return CheckResult(
            "submodules", "warn", "仓库没有 .gitmodules",
            "若使用 Release 源码包，此项可以忽略。")
    try:
        output = subprocess.run(
            ["git", "submodule", "status", "--recursive"],
            cwd=PROJECT_DIR, check=True, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError) as exc:
        return CheckResult(
            "submodules", "fail", "无法检查 Git submodule", str(exc))
    missing = [line for line in output if line.startswith("-")]
    divergent = [line for line in output if line.startswith("+") or line.startswith("U")]
    if missing:
        return CheckResult(
            "submodules", "fail", f"有 {len(missing)} 个 submodule 未初始化",
            "执行 git submodule update --init --recursive。")
    if divergent:
        return CheckResult(
            "submodules", "warn", f"有 {len(divergent)} 个 submodule 偏离锁定提交",
            "开发环境可以保留；生产 Release 应使用锁定提交。")
    return CheckResult(
        "submodules", "pass", f"{len(output)} 个 submodule 已初始化",
        "所有 submodule 位于仓库记录的提交。")


def _permission_result(path: Path) -> CheckResult:
    if not path.is_file():
        return CheckResult(
            f"config.{path.name}", "warn", f"缺少 {path.relative_to(PROJECT_DIR)}",
            "M3 配置初始化器将负责安全生成；当前需人工从示例创建。")
    mode = stat.S_IMODE(path.stat().st_mode)
    exposed = mode & (stat.S_IRWXG | stat.S_IRWXO)
    status = "warn" if exposed else "pass"
    detail = (
        "配置可能含 Token、数据库密码或供应商引用，生产建议权限 0600。"
        if exposed else "文件仅所有者可访问。")
    return CheckResult(
        f"config.{path.name}", status,
        f"{path.relative_to(PROJECT_DIR)} 权限为 {mode:04o}", detail)


def check_configs() -> list[CheckResult]:
    config_dir = PROJECT_DIR / "_config"
    return [
        _permission_result(config_dir / "iis.yaml"),
        _permission_result(config_dir / "ai_clients.yaml"),
        _permission_result(config_dir / "collector.json"),
    ]


def check_systemd_portability() -> CheckResult:
    paths = list((PROJECT_DIR / "systemd").glob("*.service"))
    paths += list((PROJECT_DIR / "systemd").glob("*.desktop"))
    paths += list((PROJECT_DIR / "systemd").glob("*.env"))
    affected: list[str] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if "/home/" in text or "anaconda" in text.lower():
            affected.append(path.name)
    if affected:
        return CheckResult(
            "systemd.portability", "warn",
            f"{len(affected)} 个部署模板仍含本机绝对路径",
            "M1 将其改为安装器渲染模板：" + ", ".join(sorted(affected)))
    return CheckResult(
        "systemd.portability", "pass", "systemd 模板未发现用户目录硬编码",
        "仍会在 M1 验证实际生成结果。")


def run_doctor() -> list[CheckResult]:
    checks: list[Callable[[], CheckResult | list[CheckResult]]] = [
        check_platform,
        check_architecture,
        check_python,
        check_resources,
        check_commands,
        check_submodules,
        check_configs,
        check_systemd_portability,
    ]
    results: list[CheckResult] = []
    for check in checks:
        value = check()
        results.extend(value if isinstance(value, list) else [value])
    return results


def print_human(results: list[CheckResult]) -> None:
    labels = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}
    for result in results:
        print(f"[{labels[result.status]}] {result.code}: {result.summary}")
        print(f"       {result.detail}")
    counts = {
        status: sum(item.status == status for item in results)
        for status in ("pass", "warn", "fail")
    }
    print(
        f"\nSummary: {counts['pass']} passed, {counts['warn']} warnings, "
        f"{counts['fail']} failed")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="IIS Linux deployment controller")
    subparsers = parser.add_subparsers(dest="command", required=True)
    doctor = subparsers.add_parser("doctor", help="只读检查主机与当前仓库的部署准备度")
    doctor.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        results = run_doctor()
        if args.json:
            payload = {
                "schema": "iis-deployment-doctor/1",
                "project_dir": str(PROJECT_DIR),
                "results": [asdict(result) for result in results],
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print_human(results)
        return 1 if any(result.status == "fail" for result in results) else 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
