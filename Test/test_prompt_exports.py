import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_committed_prompt_exports_match_runtime_catalog():
    result = subprocess.run(
        [sys.executable, "Scripts/export_prompt_versions.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
