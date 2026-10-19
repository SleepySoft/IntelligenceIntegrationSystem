"""MiMo Batch API 积压分析 PoC：把 cache 积压构建成批处理任务并回注校验。

用法：
    # 干跑：只构建 JSONL 并报告规模（不写库、不上传）
    python Scripts/batch_backlog_analysis.py --dry-run

    # 实际提交（凌晨 1-8 点自动执行）
    python Scripts/batch_backlog_analysis.py --submit --limit 500

    # 次日拉取结果并校验（不入库，只报告质量）
    python Scripts/batch_backlog_analysis.py --download <batch_id>

设计文档：doc/20261019_ai_analysis_cache_batch_client_strategy.md §8
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BATCH_BASE = "https://batch-api-cn.xiaomimimo.com/v1"
BATCH_MODEL = "mimo-v2.6-flash"  # batch 要求小写模型名


def load_mimo_key() -> str:
    import yaml
    cfg = yaml.safe_load(open("_config/ai_clients.yaml", encoding="utf-8"))
    for client in cfg.get("clients", []):
        if "xiaomimimo" in str(client.get("api_base_url", "")):
            return client["api_key"]
    raise RuntimeError("ai_clients.yaml 中没有 MiMo 客户端")


def build_jsonl(*, lookback_days: int, limit: int, out_path: Path) -> int:
    """从 cache 的 R 状态记录构建 batch JSONL。复用生产消息构造，保证结果可同链校验。"""
    import pymongo
    from prompts_event_v4 import EVENT_V4_PROMPT_CATALOG
    from ServiceComponent.analysis_messages import build_analyze_message

    db = pymongo.MongoClient("mongodb://localhost:27017")["IntelligenceIntegrationSystem"]
    cache = db["intelligence_cached"]
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    prompt = EVENT_V4_PROMPT_CATALOG.sampling_table()[
        max(EVENT_V4_PROMPT_CATALOG.sampling_versions())]

    count = 0
    with open(out_path, "w", encoding="utf-8") as f:
        for doc in cache.find(
                {"APPENDIX.__ARCHIVED__": "R", "collect_time": {"$gte": cutoff}},
                sort=[("collect_time", -1)]).limit(limit):
            messages = build_analyze_message(prompt, doc)
            line = {
                "custom_id": doc["UUID"],
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": {
                    "model": BATCH_MODEL,
                    "messages": messages,
                    "temperature": 0,
                    "max_tokens": 32768,  # 推理模型需放宽（见实测补记 §11）
                },
            }
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
            count += 1
    return count


def submit(jsonl_path: Path) -> str:
    import requests
    key = load_mimo_key()
    headers = {"Authorization": f"Bearer {key}"}
    with open(jsonl_path, "rb") as f:
        resp = requests.post(f"{BATCH_BASE}/files", headers=headers,
                             files={"file": f}, data={"purpose": "batch"}, timeout=120)
    resp.raise_for_status()
    file_id = resp.json()["id"]
    print(f"文件已上传: {file_id}")

    resp = requests.post(f"{BATCH_BASE}/batches", headers=headers, json={
        "input_file_id": file_id,
        "endpoint": "/v1/chat/completions",
        "completion_window": "24h",
        "name": f"iis-backlog-{datetime.now():%Y%m%d-%H%M}",
    }, timeout=30)
    resp.raise_for_status()
    batch_id = resp.json()["id"]
    print(f"Batch 已创建: {batch_id}（凌晨 1:00-8:00 自动执行）")
    return batch_id


def download_and_validate(batch_id: str) -> None:
    """拉取结果并用生产协议链校验（不入库，只报告）。"""
    import requests
    from prompts_event_v4 import EVENT_V4_PROMPT_CATALOG
    from ServiceComponent.IntelligenceHubDefines_v4 import DEFAULT_EVENT_REGISTRY
    from ServiceComponent.extraction_quality import compute_extraction_quality

    key = load_mimo_key()
    headers = {"Authorization": f"Bearer {key}"}
    job = requests.get(f"{BATCH_BASE}/batches/{batch_id}", headers=headers, timeout=30)
    job.raise_for_status()
    job = job.json()
    print(f"状态: {job['status']}  完成 {job['request_counts']}")
    if job["status"] != "completed":
        return

    content = requests.get(f"{BATCH_BASE}/files/{job['output_file_id']}/content",
                           headers=headers, timeout=120).text
    protocol = EVENT_V4_PROMPT_CATALOG.protocol_for(
        max(EVENT_V4_PROMPT_CATALOG.sampling_versions()))
    total = ok = 0
    quality_stats = {"unregistered": 0, "conflicts": 0}
    for line in content.splitlines():
        if not line.strip():
            continue
        total += 1
        item = json.loads(line)
        body = (item.get("response") or {}).get("body") or {}
        text = (body.get("choices") or [{}])[0].get("message", {}).get("content", "")
        try:
            start = text.find("{")
            raw = json.loads(text[start:text.rfind("}") + 1])
            result = protocol.validate_result(raw)
            ok += 1
            quality = compute_extraction_quality(
                result.EVENT_EXTRACTION, DEFAULT_EVENT_REGISTRY) \
                if result.EVENT_EXTRACTION else None
            if quality:
                quality_stats["unregistered"] += quality["unregistered_role_count"]
                quality_stats["conflicts"] += quality["role_type_conflict_count"]
        except Exception:
            pass
    print(f"校验通过 {ok}/{total}，未登记角色 {quality_stats['unregistered']}，"
          f"角色冲突 {quality_stats['conflicts']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="只构建不提交")
    parser.add_argument("--submit", action="store_true")
    parser.add_argument("--download", default="", help="batch id")
    parser.add_argument("--lookback-days", type=int, default=7)
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args()

    out = Path("_data") / f"batch_backlog_{datetime.now():%Y%m%d_%H%M%S}.jsonl"
    if args.download:
        download_and_validate(args.download)
        return
    count = build_jsonl(lookback_days=args.lookback_days, limit=args.limit, out_path=out)
    size_kb = out.stat().st_size // 1024
    print(f"构建 {count} 条 → {out}（{size_kb} KB）")
    if args.submit and count:
        submit(out)
    elif not args.dry_run:
        print("（未加 --submit，仅构建；加 --dry-run 可纯预览）")


if __name__ == "__main__":
    main()
