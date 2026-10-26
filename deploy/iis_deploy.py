#!/usr/bin/env python3
"""IIS Linux 部署控制器；提供只读诊断与本机配置向导。"""

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
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))
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
    try:
        display_path = str(path.relative_to(PROJECT_DIR))
    except ValueError:
        display_path = str(path)
    if not path.is_file():
        return CheckResult(
            f"config.{path.name}", "warn", f"缺少 {display_path}",
            "M3 配置初始化器将负责安全生成；当前需人工从示例创建。")
    mode = stat.S_IMODE(path.stat().st_mode)
    exposed = mode & (stat.S_IRWXG | stat.S_IRWXO)
    status = "warn" if exposed else "pass"
    detail = (
        "配置可能含 Token、数据库密码或供应商引用，生产建议权限 0600。"
        if exposed else "文件仅所有者可访问。")
    return CheckResult(
        f"config.{path.name}", status,
        f"{display_path} 权限为 {mode:04o}", detail)


def check_configs() -> list[CheckResult]:
    from GlobalConfig import runtime_path_report
    configs = runtime_path_report()["configs"]
    return [
        _permission_result(Path(configs["iis"]["path"])),
        _permission_result(Path(configs["ai_clients"]["path"])),
        _permission_result(Path(configs["collector"]["path"])),
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
    configure = subparsers.add_parser(
        "configure", help="启动仅监听本机的 IIS 引导配置页面")
    configure.add_argument("--config", type=Path)
    configure.add_argument("--fallback", type=Path)
    configure.add_argument("--host", default="127.0.0.1")
    configure.add_argument("--port", type=int, default=5070)
    paths = subparsers.add_parser(
        "paths", help="显示最终使用的源码根、运行目录和配置文件")
    paths.add_argument("--json", action="store_true")
    install = subparsers.add_parser(
        "install", help="读取已验证配置并执行无人值守安装")
    install.add_argument("--config", type=Path, required=True)
    install.add_argument("--ai-config", type=Path)
    install.add_argument("--collector-config", type=Path)
    install.add_argument("--install-root", type=Path, default=Path("/opt/iis"),
                         help="IIS 虚拟环境与共享下划线目录的安装根")
    install.add_argument("--sandbox-root", "--root", dest="sandbox_root", type=Path,
                         help="把绝对路径投影到该目录，用于沙箱测试")
    install.add_argument("--source", type=Path, default=PROJECT_DIR)
    install.add_argument("--service-user")
    install.add_argument("--components", default="web,crawler")
    install.add_argument("--non-interactive", action="store_true", required=True)
    install.add_argument("--skip-dependencies", action="store_true")
    install.add_argument("--skip-browser", action="store_true")
    install.add_argument("--no-systemd", action="store_true")
    install.add_argument("--no-start", action="store_true")
    install.add_argument("--force-config", action="store_true")
    install.add_argument("--admin-user")
    install.add_argument("--admin-password-file", type=Path)
    install.add_argument("--skip-admin", action="store_true")
    install.add_argument("--json", action="store_true")
    return parser


def run_configure(args) -> int:
    command = [
        sys.executable, "-m", "IISConfiguration",
        "--host", args.host, "--port", str(args.port),
    ]
    if args.config is not None:
        command.extend(("--config", str(args.config)))
    if args.fallback is not None:
        command.extend(("--fallback", str(args.fallback)))
    try:
        return subprocess.run(command, cwd=PROJECT_DIR, check=False).returncode
    except KeyboardInterrupt:
        return 130


def run_paths(*, as_json: bool = False) -> int:
    from GlobalConfig import runtime_path_report
    report = runtime_path_report()
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    runtime = report["runtime_root"]
    print(f"Source root: {report['source_root']}")
    print(f"Runtime root: {runtime['path']} (by {runtime['selected_by']})")
    for name, selected in report["directories"].items():
        print(f"Directory {name}: {selected['path']} (by {selected['selected_by']})")
    for name, selected in report["configs"].items():
        exists = "exists" if Path(selected["path"]).is_file() else "missing"
        print(
            f"Config {name}: {selected['path']} "
            f"(by {selected['selected_by']}; {exists})")
    return 0


def _service_user(explicit: str | None) -> str:
    if explicit:
        return explicit
    return os.environ.get("SUDO_USER") or os.environ.get("USER") or "root"


def run_install(args) -> int:
    from deploy.installer import (
        InstallError, InstallLayout, InstallRequest, install, result_json,
    )

    config_path = args.config.expanduser().resolve()
    ai_path = (args.ai_config or config_path.with_name("ai_clients.yaml"))
    collector_path = args.collector_config
    try:
        request = InstallRequest(
            config_path=config_path,
            ai_config_path=ai_path.expanduser().resolve(),
            collector_config_path=(collector_path.expanduser().resolve()
                                   if collector_path else None),
            layout=InstallLayout.build(
                args.source, args.install_root, args.sandbox_root),
            service_user=_service_user(args.service_user),
            components=tuple(
                item.strip() for item in args.components.split(",") if item.strip()),
            install_dependencies=not args.skip_dependencies,
            install_browser=not args.skip_browser,
            manage_systemd=not args.no_systemd,
            start_services=not args.no_start,
            force_config=args.force_config,
            admin_user=args.admin_user,
            admin_password_file=(args.admin_password_file.expanduser().resolve()
                                 if args.admin_password_file else None),
            skip_admin=args.skip_admin,
        )
        result = install(request)
    except (InstallError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        if args.json:
            print(json.dumps({
                "status": "error", "error": f"{type(exc).__name__}: {exc}",
            }, ensure_ascii=False, indent=2))
        else:
            print(f"安装失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(result_json(result))
    else:
        print(f"安装完成。配置目录: {result.config_dir}")
        print(f"状态文件: {result.state_file}")
        for warning in result.warnings:
            print(f"警告: {warning}")
    return 0


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
    if args.command == "configure":
        return run_configure(args)
    if args.command == "paths":
        return run_paths(as_json=args.json)
    if args.command == "install":
        return run_install(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
