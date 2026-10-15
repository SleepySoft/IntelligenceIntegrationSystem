"""对话记录按周压缩归档：散文件保留 7 天，之后每周打包进对应 ISO 周的 zip。

用法：
    python Scripts/compact_conversation_records.py                 # 默认保留 7 天
    python Scripts/compact_conversation_records.py --days 14       # 自定义保留期
    python Scripts/compact_conversation_records.py --days 0        # 全部归档

IIS 维护调度器每周日自动执行（intelligence_hub.conversation_retention_days，默认 7）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ServiceComponent.conversation_compaction import compact_conversation_records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="conversation", help="对话记录根目录")
    parser.add_argument("--days", type=int, default=7, help="散文件保留天数（默认 7）")
    args = parser.parse_args()

    summary = compact_conversation_records(args.root, older_than_days=args.days)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
