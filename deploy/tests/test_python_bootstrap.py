from __future__ import annotations

import hashlib
import os
import subprocess
import tarfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = ROOT / "deploy/python_bootstrap.sh"


def _fake_python(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env bash
set -eu
if [[ "${1:-}" == "-m" && "${2:-}" == "venv" ]]; then
    mkdir -p "$3/bin"
    cp "$(readlink -f "$0")" "$3/bin/python"
    chmod 755 "$3/bin/python"
    exit 0
fi
if [[ "${1:-}" == "-c" ]]; then
    exit 0
fi
exit 0
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _bash(script: str, *, env: dict[str, str] | None = None):
    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(
        ["bash", "-c", f'source "{BOOTSTRAP}"\n{script}'],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=merged,
        check=False,
    )


def test_runtime_selection_prefers_312(tmp_path):
    binaries = tmp_path / "bin"
    binaries.mkdir()
    _fake_python(binaries / "python3.11")
    _fake_python(binaries / "python3.12")

    result = _bash(
        f'iis_select_existing_runtime_python "{tmp_path / "install"}"',
        env={"PATH": f"{binaries}:/usr/bin:/bin"},
    )

    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()) == (binaries / "python3.12").resolve()


def test_recorded_runtime_is_stable_across_later_candidates(tmp_path):
    binaries = tmp_path / "bin"
    runtime = tmp_path / "install/runtime"
    binaries.mkdir()
    runtime.mkdir(parents=True)
    _fake_python(binaries / "python3.11")
    _fake_python(binaries / "python3.12")
    (runtime / "selected-python").symlink_to(binaries / "python3.11")

    result = _bash(
        f'iis_select_existing_runtime_python "{tmp_path / "install"}"',
        env={"PATH": f"{binaries}:/usr/bin:/bin"},
    )

    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()) == (binaries / "python3.11").resolve()


def test_standalone_archive_is_verified_extracted_and_reused(tmp_path):
    payload = tmp_path / "payload/python/bin"
    payload.mkdir(parents=True)
    _fake_python(payload / "python3.12")
    archive = tmp_path / "python.tar.gz"
    with tarfile.open(archive, "w:gz") as stream:
        stream.add(tmp_path / "payload/python", arcname="python")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    install_root = tmp_path / "install"
    command = f'iis_install_standalone_python "{install_root}"'
    environment = {
        "IIS_STANDALONE_PYTHON_ARCHIVE": str(archive),
        "IIS_STANDALONE_PYTHON_SHA256": digest,
    }

    first = _bash(command, env=environment)
    second = _bash(command, env=environment)

    expected = install_root / "runtime/python-3.12.14/bin/python3.12"
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert Path(first.stdout.strip()) == expected
    assert Path(second.stdout.strip()) == expected
    assert expected.is_file()


def test_standalone_archive_rejects_wrong_checksum(tmp_path):
    archive = tmp_path / "invalid.tar.gz"
    archive.write_bytes(b"not an archive")
    install_root = tmp_path / "install"

    result = _bash(
        f'iis_install_standalone_python "{install_root}"',
        env={
            "IIS_STANDALONE_PYTHON_ARCHIVE": str(archive),
            "IIS_STANDALONE_PYTHON_SHA256": "0" * 64,
        },
    )

    assert result.returncode != 0
    assert "SHA-256" in result.stderr
    assert not (install_root / "runtime/python-3.12.14").exists()


def test_install_roots_apply_sandbox_projection(tmp_path):
    result = _bash(
        "iis_install_roots install --install-root /opt/custom "
        f'--sandbox-root "{tmp_path}"'
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == str(tmp_path / "opt/custom")


def test_install_help_does_not_require_runtime_bootstrap():
    result = subprocess.run(
        [str(ROOT / "deploy/iisctl"), "install", "--help"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--profile" in result.stdout
    assert "下载 CPython" not in result.stderr
