"""可重入的 IIS 无人值守安装执行器。

网页不导入本模块，也不会触发任何安装动作。安装器只消费已生成的配置文件；测试通过
``root`` 把 /etc、/var、/opt 等目标投影到临时目录，执行同一套规划和写入代码。
"""

from __future__ import annotations

import json
import os
import platform
import pwd
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import venv
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

class InstallError(RuntimeError):
    pass


@dataclass(frozen=True)
class InstallLayout:
    sandbox_root: Path | None
    install_root: Path
    source_dir: Path
    config_dir: Path
    data_dir: Path
    log_dir: Path
    export_dir: Path
    products_dir: Path
    model_dir: Path
    venv_dir: Path
    systemd_dir: Path

    @classmethod
    def build(
            cls, source_dir: Path, install_root: Path = Path("/opt/iis"),
            sandbox_root: Path | None = None,
    ) -> "InstallLayout":
        source = source_dir.expanduser().resolve()
        sandbox = sandbox_root.expanduser().resolve() if sandbox_root else None
        requested_install_root = install_root.expanduser()
        if not requested_install_root.is_absolute():
            raise InstallError("--install-root 必须是绝对路径。")

        def under_system(path: Path) -> Path:
            return ((sandbox / path.relative_to("/")) if sandbox else path)

        effective_install_root = under_system(requested_install_root)

        return cls(
            sandbox_root=sandbox,
            install_root=effective_install_root,
            source_dir=source,
            config_dir=effective_install_root / "_config",
            data_dir=effective_install_root / "_data",
            log_dir=effective_install_root / "_log",
            export_dir=effective_install_root / "_export",
            products_dir=effective_install_root / "_products",
            model_dir=effective_install_root / "_models",
            venv_dir=effective_install_root / ".venv",
            systemd_dir=under_system(Path("/etc/systemd/system")),
        )


@dataclass(frozen=True)
class InstallRequest:
    config_path: Path
    ai_config_path: Path
    collector_config_path: Path | None
    layout: InstallLayout
    service_user: str
    components: tuple[str, ...] = ("web", "crawler")
    profiles: tuple[str, ...] = ("core", "crawler")
    requirement_files: tuple[str, ...] = ()
    dependency_fingerprint: str = ""
    install_dependencies: bool = True
    install_browser: bool = True
    manage_systemd: bool = True
    start_services: bool = True
    force_config: bool = False
    admin_user: str | None = None
    admin_password_file: Path | None = None
    consume_admin_password_file: bool = False
    skip_admin: bool = False


@dataclass(frozen=True)
class InstallResult:
    status: str
    state_file: str
    config_dir: str
    environment_file: str
    units: tuple[str, ...]
    profiles: tuple[str, ...]
    warnings: tuple[str, ...]


def _atomic_write(path: Path, content: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _backup(path: Path, directory: Path) -> None:
    if not path.is_file():
        return
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    shutil.copy2(path, directory / f"{path.name}.{stamp}")


def _install_file(
        source: Path, destination: Path, *, force: bool, backup_dir: Path,
) -> None:
    content = source.read_text(encoding="utf-8")
    if destination.is_file():
        current = destination.read_text(encoding="utf-8")
        if current == content:
            os.chmod(destination, 0o600)
            return
        if not force:
            raise InstallError(
                f"目标配置已存在且内容不同: {destination}；"
                "使用 --force-config 前请确认需要覆盖。")
        _backup(destination, backup_dir)
    _atomic_write(destination, content, 0o600)


def _install_binary_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            with source.open("rb") as incoming:
                shutil.copyfileobj(incoming, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _dispose_clients(clients: dict[str, Any]) -> None:
    from AIClientCenter.core.manager import AIClientManager
    for client in clients.values():
        AIClientManager._dispose_client(client)


def validate_inputs(request: InstallRequest) -> tuple[Any, list[str]]:
    from AIClientCenter.factory_engine import load_config, validate_config
    from IISConfig import load_iis_config

    if not request.config_path.is_file():
        raise InstallError(f"IIS 配置不存在: {request.config_path}")
    if not request.ai_config_path.is_file():
        raise InstallError(f"AI Client 配置不存在: {request.ai_config_path}")
    config = load_iis_config(request.config_path)
    if not config.settings.access.collector_tokens:
        raise InstallError("access.collector_tokens 至少需要一个 Token。")

    clients, limits = load_config(request.ai_config_path)
    try:
        errors, warnings = validate_config(clients, limits)
        if errors:
            raise InstallError("AI Client 配置无效: " + "; ".join(errors))
        if not clients:
            raise InstallError("AI Client 配置没有启用任何客户端。")
        groups = {client.group_id for client in clients.values()}
        translation = config.settings.optional_subsystems.translation
        if translation.enabled and translation.client_group not in groups:
            raise InstallError(
                "翻译分组未由任何启用的 AI Client 提供: "
                f"{translation.client_group}")
    finally:
        _dispose_clients(clients)
    if request.collector_config_path is not None:
        _validate_collector(
            request.collector_config_path,
            set(config.settings.access.collector_tokens),
        )
    unknown = set(request.components).difference({"web", "crawler"})
    if unknown:
        raise InstallError("不支持的安装组件: " + ", ".join(sorted(unknown)))
    if "web" not in request.components:
        raise InstallError("首版安装必须包含 web 组件。")
    return config, warnings


def _validate_collector(path: Path, accepted_tokens: set[str]) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstallError(f"采集器配置无效: {exc}") from exc
    collector = raw.get("collector") if isinstance(raw, dict) else None
    if not isinstance(collector, dict):
        raise InstallError("采集器配置必须包含 collector 对象。")
    token = str(collector.get("default_token") or "").strip()
    if token not in accepted_tokens:
        raise InstallError("collector.default_token 不在 IIS collector_tokens 中。")
    if not str(collector.get("submit_ihub_url") or "").strip():
        raise InstallError("collector.submit_ihub_url 不能为空。")
    return raw


def build_default_collector(config, source_dir: Path) -> dict[str, Any]:
    settings = config.settings
    domain_task_dirs: dict[str, str] = {}
    for domain_name in settings.hub.domains.enabled:
        candidate = source_dir / "CrawlTasks" / domain_name
        if candidate.is_dir():
            domain_task_dirs[domain_name] = str(candidate)
    if not domain_task_dirs:
        domain_task_dirs[settings.hub.domains.default] = str(source_dir / "CrawlTasks")
    return {
        "collector": {
            "name": "main",
            "submit_ihub_url": f"http://127.0.0.1:{settings.server.port}",
            "default_token": settings.access.collector_tokens[0],
            "default_domain": settings.hub.domains.default,
            "global_site_proxy": {},
            "cn_site_proxy": {},
            "domain_task_dirs": domain_task_dirs,
            "disabled_tasks": [],
        },
    }


def _quoted(value: Path | str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _render_environment(request: InstallRequest, python_path: Path,
                        session_secret: str) -> str:
    layout = request.layout
    try:
        service_home = Path(pwd.getpwnam(request.service_user).pw_dir)
    except KeyError:
        service_home = Path("/home") / request.service_user
    values = {
        "PROJECT_DIR": layout.source_dir,
        "PYTHONPATH": layout.source_dir,
        "IIS_RUNTIME_ROOT": layout.install_root,
        "IIS_CONFIG": layout.config_dir / "iis.yaml",
        "AI_CLIENT_CONFIG": layout.config_dir / "ai_clients.yaml",
        "IIS_COLLECTOR_CONFIG": layout.config_dir / "collector.json",
        "IIS_SESSION_SECRET": session_secret,
        "IIS_PYTHON": python_path,
        "PATH": (
            f"{service_home / '.local/bin'}:/usr/local/sbin:/usr/local/bin:"
            "/usr/sbin:/usr/bin:/sbin:/bin"),
    }
    return "\n".join(f"{key}={value}" for key, value in values.items()) + "\n"


def _read_env_value(path: Path, key: str) -> str | None:
    if not path.is_file():
        return None
    prefix = key + "="
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            return line[len(prefix):]
    return None


def render_units(request: InstallRequest, python_path: Path,
                 environment_file: Path) -> dict[str, str]:
    source = request.layout.source_dir
    common = f"""[Service]
Type=simple
User={request.service_user}
WorkingDirectory={_quoted(source)}
EnvironmentFile={_quoted(environment_file)}
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
KillSignal=SIGTERM
TimeoutStopSec=30
"""
    units = {
        "iis-web.service": f"""[Unit]
Description=Intelligence Integration System - Web Hub
After=network-online.target
Wants=network-online.target

{common}ExecStartPre={_quoted(python_path)} {_quoted(source / 'Scripts/deployment_preflight.py')} --component web
ExecStart={_quoted(python_path)} {_quoted(source / 'IntelligenceHubLauncher.py')}
NotifyAccess=main
WatchdogSec=30

[Install]
WantedBy=multi-user.target
""",
    }
    if "crawler" in request.components:
        units["iis-crawler.service"] = f"""[Unit]
Description=Intelligence Integration System - Crawler
After=network-online.target iis-web.service
Wants=iis-web.service

{common}ExecStartPre={_quoted(python_path)} {_quoted(source / 'Scripts/deployment_preflight.py')} --component crawler
ExecStart={_quoted(python_path)} {_quoted(source / 'CrawlerServiceEngine.py')}
KillMode=mixed

[Install]
WantedBy=multi-user.target
"""
    wants = " ".join(sorted(units))
    units["iis.target"] = f"""[Unit]
Description=Intelligence Integration System Services
After=network-online.target
Wants={wants}

[Install]
WantedBy=multi-user.target
"""
    return units


def _install_dependencies(request: InstallRequest) -> Path:
    layout = request.layout
    if not request.install_dependencies:
        return Path(sys.executable).resolve()
    if Path(sys.prefix).resolve() == layout.venv_dir.resolve():
        # iis_deploy 的纯标准库阶段已经创建环境、安装依赖并重新执行自身。
        python_path = Path(sys.executable)
    else:
        # 保留可直接复用的安装后端 API；正式 CLI 通常不会进入该分支。
        builder = venv.EnvBuilder(with_pip=True, clear=False, upgrade_deps=False)
        builder.create(layout.venv_dir)
        python_path = layout.venv_dir / "bin" / "python"
        subprocess.run(
            [str(python_path), "-m", "pip", "install", "--upgrade", "pip"],
            cwd=layout.source_dir, check=True)
        requirement_files = request.requirement_files or (
            str(layout.source_dir / "requirements.txt"),)
        for requirements in requirement_files:
            subprocess.run(
                [str(python_path), "-m", "pip", "install", "-r", requirements],
                cwd=layout.source_dir, check=True)
    if request.install_browser and "crawler" in request.components:
        subprocess.run(
            [str(python_path), "-m", "playwright", "install", "chromium"],
            cwd=layout.source_dir, check=True)
    return python_path


def _base_python_path() -> str:
    selected = os.environ.get("IIS_BOOTSTRAP_BASE_PYTHON", "").strip()
    if selected:
        return str(Path(selected).expanduser().resolve())
    base = getattr(sys, "_base_executable", sys.executable)
    return str(Path(base).resolve())


def _initialize_authentication(request: InstallRequest) -> list[str]:
    target = request.layout.config_dir / "Authentication.db"
    if target.is_file():
        os.chmod(target, 0o600)
        return []
    existing = request.config_path.with_name("Authentication.db")
    if existing.is_file():
        _install_binary_file(existing, target)
        return []
    if request.admin_user and request.admin_password_file:
        password = _read_admin_password(request.admin_password_file)
        from ServiceComponent.UserManager import UserManager
        manager = UserManager(str(target))
        user_id, message = manager.create_user(request.admin_user, password, [])
        if user_id == UserManager.INVALID_USER_ID:
            raise InstallError(f"创建管理员失败: {message}")
        os.chmod(target, 0o600)
        if request.consume_admin_password_file:
            try:
                request.admin_password_file.unlink()
            except OSError as exc:
                raise InstallError(
                    f"管理员已创建，但临时密码文件删除失败: {exc}") from exc
        return []
    if request.admin_user or request.admin_password_file:
        raise InstallError("--admin-user 与 --admin-password-file 必须同时提供。")
    if request.skip_admin:
        if request.consume_admin_password_file:
            raise InstallError(
                "--consume-admin-password-file 不能与 --skip-admin 同时使用。")
        return ["按显式 --skip-admin 跳过管理员初始化；登录前必须另行创建用户。"]
    raise InstallError(
        "缺少管理员初始化信息；提供 --admin-user 和 --admin-password-file，"
        "或显式使用 --skip-admin。")


def _read_admin_password(path: Path) -> str:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise InstallError(f"无法打开管理员密码文件 {path}: {exc}") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise InstallError("管理员密码文件必须是普通文件。")
        if stat.S_IMODE(info.st_mode) & (stat.S_IRWXG | stat.S_IRWXO):
            raise InstallError("管理员密码文件权限过宽；请设置为 0600。")
        content = os.read(descriptor, 4098)
        if len(content) > 4097:
            raise InstallError("管理员密码文件过大。")
    finally:
        os.close(descriptor)
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InstallError("管理员密码文件必须使用 UTF-8 编码。") from exc
    if text.endswith("\r\n"):
        text = text[:-2]
    elif text.endswith("\n"):
        text = text[:-1]
    if not text:
        raise InstallError("管理员密码文件为空。")
    if "\x00" in text or "\n" in text or "\r" in text:
        raise InstallError("管理员密码文件必须只包含一行密码。")
    return text


def _state(layout: InstallLayout, phase: str, **extra: Any) -> Path:
    path = layout.data_dir / "deployment" / "install-state.json"
    payload = {
        "schema": "iis-install-state/1",
        "phase": phase,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        **extra,
    }
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return path


def _ensure_permissions(request: InstallRequest) -> None:
    if request.layout.sandbox_root is not None or os.geteuid() != 0:
        return
    account = pwd.getpwnam(request.service_user)
    for root in (
            request.layout.config_dir, request.layout.data_dir, request.layout.log_dir,
            request.layout.export_dir, request.layout.products_dir, request.layout.model_dir):
        for directory, subdirs, files in os.walk(root):
            os.chown(directory, account.pw_uid, account.pw_gid)
            for name in subdirs:
                os.chown(Path(directory) / name, account.pw_uid, account.pw_gid)
            for name in files:
                os.chown(Path(directory) / name, account.pw_uid, account.pw_gid)


def _health_check(port: int, attempts: int = 15) -> None:
    url = f"http://127.0.0.1:{port}/maintenance/ping"
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if 200 <= response.status < 300:
                    return
        except Exception as exc:  # 服务启动窗口内保留最后一次错误供诊断。
            last_error = exc
        time.sleep(1)
    raise InstallError(f"服务健康检查失败 {url}: {last_error}")


def install(request: InstallRequest) -> InstallResult:
    if request.layout.sandbox_root is None and os.geteuid() != 0:
        raise InstallError("生产安装需要 root；测试请使用 --sandbox-root 指向沙箱目录。")
    if request.layout.sandbox_root is not None and request.manage_systemd:
        raise InstallError("沙箱安装不得管理宿主 systemd，请使用 --no-systemd。")
    if request.service_user == "root":
        raise InstallError("拒绝把 root 配置为 IIS 长期服务用户。")

    config, warnings = validate_inputs(request)
    _state(
        request.layout, "validated",
        profiles=request.profiles,
        requirement_files=request.requirement_files,
        dependency_fingerprint=request.dependency_fingerprint,
        components=request.components,
    )
    for directory in (
            request.layout.config_dir, request.layout.data_dir, request.layout.log_dir,
            request.layout.export_dir, request.layout.products_dir,
            request.layout.model_dir, request.layout.systemd_dir):
        directory.mkdir(parents=True, exist_ok=True)

    _install_file(
        request.config_path, request.layout.config_dir / "iis.yaml",
        force=request.force_config,
        backup_dir=request.layout.data_dir / "deployment/config-backups")
    _install_file(
        request.ai_config_path, request.layout.config_dir / "ai_clients.yaml",
        force=request.force_config,
        backup_dir=request.layout.data_dir / "deployment/config-backups")
    collector_target = request.layout.config_dir / "collector.json"
    if request.collector_config_path is not None:
        _install_file(
            request.collector_config_path, collector_target,
            force=request.force_config,
            backup_dir=request.layout.data_dir / "deployment/config-backups")
    else:
        generated = json.dumps(
            build_default_collector(config, request.layout.source_dir),
            ensure_ascii=False, indent=2) + "\n"
        if collector_target.exists() and collector_target.read_text(encoding="utf-8") != generated:
            if not request.force_config:
                raise InstallError(f"目标配置已存在且内容不同: {collector_target}")
            _backup(
                collector_target,
                request.layout.data_dir / "deployment/config-backups")
        _atomic_write(collector_target, generated)
    warnings.extend(_initialize_authentication(request))
    _state(request.layout, "configured")

    python_path = _install_dependencies(request)
    _state(
        request.layout,
        "dependencies_ready",
        python=str(python_path),
        base_python=_base_python_path(),
        base_python_version=platform.python_version(),
        profiles=request.profiles,
        requirement_files=request.requirement_files,
        dependency_fingerprint=request.dependency_fingerprint,
        components=request.components,
    )
    env_path = request.layout.config_dir / "iis.env"
    session_secret = _read_env_value(env_path, "IIS_SESSION_SECRET") or secrets.token_urlsafe(48)
    _atomic_write(
        env_path, _render_environment(request, python_path, session_secret), 0o600)

    units = render_units(request, python_path, env_path)
    for name, content in units.items():
        _atomic_write(request.layout.systemd_dir / name, content, 0o644)
    _ensure_permissions(request)
    _state(request.layout, "units_ready", units=sorted(units))

    if request.manage_systemd:
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "enable", "iis.target"], check=True)
        if request.start_services:
            subprocess.run(["systemctl", "restart", "iis.target"], check=True)
            _health_check(config.settings.server.port)
    state_file = _state(
        request.layout,
        "complete",
        units=sorted(units),
        python=str(python_path),
        base_python=_base_python_path(),
        base_python_version=platform.python_version(),
        profiles=request.profiles,
        requirement_files=request.requirement_files,
        dependency_fingerprint=request.dependency_fingerprint,
        components=request.components,
    )
    return InstallResult(
        status="complete",
        state_file=str(state_file),
        config_dir=str(request.layout.config_dir),
        environment_file=str(env_path),
        units=tuple(sorted(units)),
        profiles=request.profiles,
        warnings=tuple(warnings),
    )


def result_json(result: InstallResult) -> str:
    return json.dumps(asdict(result), ensure_ascii=False, indent=2)
