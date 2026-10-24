"""导出 PromptCatalog 中每个版本的最终完整静态 Prompt。

导出的 ``vNN.txt`` 是目录构造完成后的原样文本，不是源码片段。运行时才替换的
``{{CURRENT_DATE}}``、``{{CONTENT}}`` 等请求变量会保留为占位符。

用法：
    python Scripts/export_prompt_versions.py
    python Scripts/export_prompt_versions.py --check
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from prompts_event_v4 import EVENT_V4_PROMPT_CATALOG  # noqa: E402


DEFAULT_OUTPUT = ROOT / "doc" / "prompt_versions" / "prompts"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_export_files() -> dict[str, str]:
    """返回相对输出目录的文件名及确定性内容。"""
    versions = []
    files: dict[str, str] = {}
    for version in EVENT_V4_PROMPT_CATALOG.selectable_versions():
        spec = EVENT_V4_PROMPT_CATALOG.spec_for(version)
        filename = f"v{version}.txt"
        files[filename] = spec.text
        versions.append({
            "version": version,
            "file": filename,
            "protocol": spec.protocol,
            "sampling": spec.sampling,
            "characters": len(spec.text),
            "utf8_bytes": len(spec.text.encode("utf-8")),
            "lines": len(spec.text.splitlines()),
            "sha256": _sha256(spec.text),
        })

    manifest = {
        "schema_version": 1,
        "source": "prompts_event_v4.EVENT_V4_PROMPT_CATALOG",
        "content": "fully assembled static prompt before per-request placeholder injection",
        "versions": versions,
    }
    files["manifest.json"] = json.dumps(
        manifest, ensure_ascii=False, indent=2, sort_keys=False) + "\n"

    rows = [
        "# 完整 Prompt 清单",
        "",
        "本目录由 `python Scripts/export_prompt_versions.py` 生成。`vNN.txt` 与运行时",
        "`PromptCatalog` 中完成拼装后的静态文本逐字一致；请勿直接编辑生成文件。",
        "",
        "| 版本 | 协议 | 生产抽样 | 字符数 | UTF-8 字节 | 行数 | SHA-256 |",
        "|---:|---|:---:|---:|---:|---:|---|",
    ]
    for item in versions:
        rows.append(
            f"| [v{item['version']}](v{item['version']}.txt) | "
            f"`{item['protocol']}` | {'是' if item['sampling'] else '否'} | "
            f"{item['characters']} | {item['utf8_bytes']} | {item['lines']} | "
            f"`{item['sha256']}` |"
        )
    files["MANIFEST.md"] = "\n".join(rows) + "\n"
    return files


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def check_exports(output: Path, files: dict[str, str]) -> list[str]:
    errors = []
    expected = set(files)
    actual = {path.name for path in output.glob("v*.txt")}
    actual.update(path.name for path in output.glob("manifest.json"))
    actual.update(path.name for path in output.glob("MANIFEST.md"))
    for missing in sorted(expected - actual):
        errors.append(f"缺少 {missing}")
    for extra in sorted(actual - expected):
        errors.append(f"多余 {extra}")
    for name, content in files.items():
        path = output / name
        if path.exists() and path.read_text(encoding="utf-8") != content:
            errors.append(f"内容过期 {name}")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="只校验，不写文件")
    args = parser.parse_args()
    output = args.output.resolve()
    files = build_export_files()
    if args.check:
        errors = check_exports(output, files)
        if errors:
            raise SystemExit("Prompt 导出校验失败：\n- " + "\n- ".join(errors))
        print(f"Prompt 导出校验通过：{len(EVENT_V4_PROMPT_CATALOG.selectable_versions())} 个版本")
        return
    for name, content in files.items():
        _atomic_write(output / name, content)
    print(f"已导出 {len(EVENT_V4_PROMPT_CATALOG.selectable_versions())} 个完整 Prompt 到 {output}")


if __name__ == "__main__":
    main()
