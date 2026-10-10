"""不启动 IIS 主程序，使用生产 Event V4 Prompt 比较已配置 Client。"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from AIClientCenter.cli.launcher import load_config, validate_config  # noqa: E402
from ServiceComponent.ai_client_benchmark import (  # noqa: E402
    AIClientBenchmarkRunner,
    load_benchmark_suite,
)
from prompts_event_v4 import EVENT_ANALYSIS_PROMPT_TABLE  # noqa: E402


DEFAULT_CONFIG = PROJECT_ROOT / "_config" / "ai_client_config.py"
DEFAULT_SUITE = PROJECT_ROOT / "_config" / "ai_benchmark_suite_example.json"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "_data" / "ai_benchmarks"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="使用 IIS 生产 Prompt/V4 契约执行真实 AI Client 基准。默认仅预览计划。",
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                        help="AI Client 配置文件")
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE,
                        help="基准测试集 JSON")
    parser.add_argument("--clients", default="",
                        help="逗号分隔的 AI_CLIENTS 键或 Client name；默认全部")
    parser.add_argument("--repeat", type=int, default=1, help="每个 Client/样本重复次数")
    parser.add_argument("--prompt-version", type=int,
                        default=max(EVENT_ANALYSIS_PROMPT_TABLE), help="生产 Prompt 版本")
    parser.add_argument("--output-dir", type=Path,
                        help="本次输出目录；默认在 _data/ai_benchmarks 下按 UTC 时间创建")
    parser.add_argument("--raw-output-limit", type=int, default=50_000,
                        help="results.jsonl 中每次原始输出最大字符数")
    parser.add_argument("--execute", action="store_true",
                        help="确认发送真实请求；未指定时只打印执行计划")
    return parser.parse_args(argv)


def select_clients(all_clients, requested: str):
    names = [item.strip() for item in requested.split(",") if item.strip()]
    if not names:
        return dict(all_clients)
    selected = {}
    missing = []
    for name in names:
        match = next(
            ((key, client) for key, client in all_clients.items()
             if key == name or client.name == name),
            None,
        )
        if match is None:
            missing.append(name)
        else:
            selected[match[0]] = match[1]
    if missing:
        raise ValueError(f"找不到 Client: {', '.join(missing)}")
    return selected


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.repeat < 1:
        print("错误：--repeat 必须大于 0", file=sys.stderr)
        return 2
    if args.raw_output_limit < 0:
        print("错误：--raw-output-limit 不能小于 0", file=sys.stderr)
        return 2
    try:
        clients, group_limits = load_config(args.config)
        errors, warnings = validate_config(clients, group_limits)
        if errors:
            raise ValueError("; ".join(errors))
        selected = select_clients(clients, args.clients)
        suite_name, cases = load_benchmark_suite(args.suite)
        if args.prompt_version not in EVENT_ANALYSIS_PROMPT_TABLE:
            raise ValueError(f"Prompt v{args.prompt_version} 未配置")
        if not selected:
            raise ValueError("配置中没有已启用的 Client")
    except (OSError, ValueError) as exc:
        print(f"基准配置错误：{exc}", file=sys.stderr)
        return 2

    total_calls = len(selected) * len(cases) * args.repeat
    print(f"Suite: {suite_name} ({len(cases)} cases)")
    print(f"Prompt: v{args.prompt_version}")
    print(f"Clients: {', '.join(client.name for client in selected.values())}")
    print(f"Planned real calls: {total_calls}")
    for warning in warnings:
        print(f"配置警告：{warning}")
    if not args.execute:
        print("预览完成；增加 --execute 才会发送真实请求。")
        return 0

    output_dir = args.output_dir
    if output_dir is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_dir = DEFAULT_OUTPUT_ROOT / stamp
    output_dir = output_dir.resolve()
    try:
        runner = AIClientBenchmarkRunner(
            selected,
            EVENT_ANALYSIS_PROMPT_TABLE,
            raw_output_limit=args.raw_output_limit,
        )
        summary = runner.run(
            suite_name=suite_name,
            cases=cases,
            repeat=args.repeat,
            prompt_version=args.prompt_version,
            output_dir=output_dir,
        )
    except Exception as exc:
        print(f"基准执行失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"基准完成：{output_dir}")
    for item in summary["clients"]:
        print(
            f"{item['client_name']}: API={item['api_success_rate']:.1%}, "
            f"V4={item['v4_valid_rate']:.1%}, 正确={item['correctness_rate']:.1%}, "
            f"P50={item['latency_p50_seconds']:.3f}s, P95={item['latency_p95_seconds']:.3f}s"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
