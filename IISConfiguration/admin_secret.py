"""配置页面生成的临时管理员安装秘密。"""

from __future__ import annotations

import os
import re
import secrets
import stat
import tempfile
from pathlib import Path


USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{3,50}$")
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 1024


class AdminSecretError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def default_admin_secret_dir() -> Path:
    explicit = os.environ.get("IIS_BOOTSTRAP_SECRET_DIR", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if runtime:
        return Path(runtime).expanduser().resolve() / "iis-bootstrap"
    candidate = Path("/run/user") / str(os.getuid())
    if candidate.is_dir() and os.access(candidate, os.W_OK | os.X_OK):
        return candidate / "iis-bootstrap"
    return Path(tempfile.gettempdir()) / f"iis-bootstrap-{os.getuid()}"


class AdminSecretService:
    def __init__(self, directory: Path | None = None):
        self.directory = (directory or default_admin_secret_dir()).expanduser().resolve()

    def create(self, username: str, password: str, confirmation: str) -> dict[str, str]:
        username = str(username or "").strip()
        if not USERNAME_PATTERN.fullmatch(username):
            raise AdminSecretError("invalid_username")
        if not isinstance(password, str) or not isinstance(confirmation, str):
            raise AdminSecretError("password_required")
        if password != confirmation:
            raise AdminSecretError("password_mismatch")
        if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
            raise AdminSecretError("password_length")
        if "\x00" in password or "\n" in password or "\r" in password:
            raise AdminSecretError("password_multiline")

        self._ensure_directory()
        destination = self.directory / (
            f"admin-password-{secrets.token_urlsafe(12)}")
        descriptor, temporary = tempfile.mkstemp(
            prefix=".admin-password.", suffix=".tmp", dir=self.directory)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(password)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
            os.chmod(destination, 0o600)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
        return {
            "username": username,
            "password_file": str(destination),
        }

    def _ensure_directory(self) -> None:
        try:
            self.directory.mkdir(parents=True, mode=0o700, exist_ok=False)
        except FileExistsError:
            pass
        info = self.directory.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise AdminSecretError("invalid_secret_directory")
        if info.st_uid != os.getuid():
            raise AdminSecretError("secret_directory_owner")
        if stat.S_IMODE(info.st_mode) & (stat.S_IRWXG | stat.S_IRWXO):
            raise AdminSecretError("secret_directory_permissions")
