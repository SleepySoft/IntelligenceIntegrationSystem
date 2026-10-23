"""AI 分析效果离线报告：按 模型 × prompt版本 聚合成功率/敏感率/首过率/延迟。

数据源：_data/analysis_stats.db（IIS 侧持久统计，独立于 AIClientCenter 观测库）。
用法：
    python Scripts/analysis_model_report.py            # 近 7 天
    python Scripts/analysis_model_report.py --days 1   # 近 24 小时
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ServiceComponent.analysis_stats import AnalysisStatsRecorder


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--db", default="_data/analysis_stats.db")
    args = parser.parse_args()

    recorder = AnalysisStatsRecorder(args.db)
    groups = recorder.aggregate_by_model(days=args.days)
    if not groups:
        print(f"近 {args.days} 天无分析统计记录。")
        return

    print(f"=== 近 {args.days} 天 按 模型×prompt版本 的分析效果 ===\n")
    header = (f"{'模型':28s} {'pv':>3s} {'件数':>5s} {'成功率':>7s} {'首过率':>7s} "
              f"{'敏感率':>7s} {'调用/件':>7s} {'P50ms':>7s} {'P95ms':>7s}  错误分布")
    print(header)
    print("-" * len(header))
    for g in groups:
        errors = ", ".join(f"{k}×{v}" for k, v in
                           sorted(g["errors"].items(), key=lambda x: -x[1])) or "-"
        print(f"{g['ai_model'][:28]:28s} {str(g['prompt_version']):>3s} "
              f"{g.get('items', 0):5d} "
              f"{(g['success_rate'] or 0) * 100:6.1f}% "
              f"{(g['first_pass_rate'] or 0) * 100:6.1f}% "
              f"{(g['sensitive_rate'] or 0) * 100:6.1f}% "
              f"{g.get('attempts_per_item') or 0:7.2f} "
              f"{g.get('latency_p50_ms') or 0:7d} {g.get('latency_p95_ms') or 0:7d}  "
              f"{errors}")


if __name__ == "__main__":
    main()
