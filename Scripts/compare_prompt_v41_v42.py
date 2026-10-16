"""Prompt v41（完整契约直出）vs v42（核心子集+桥接后处理）真实模型对比。

对同一批归档原文分别用两个 prompt 版本走真实 AI 分析（含与生产一致的
校验反馈重试），输出每篇×每版本的详细结果与聚合对比指标。
仅读 Mongo 归档的 RAW_DATA，不写任何业务集合；结果落盘到输出目录。

用法：
    python Scripts/compare_prompt_v41_v42.py                 # 调查报告的 11 条样本
    python Scripts/compare_prompt_v41_v42.py --sample 5      # 另加 5 条近期随机样本
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymongo

from AIClientCenter.configuration import build_manager, load_config
from ServiceComponent.IntelligenceAnalyzerProxy import analyze_with_ai
from ServiceComponent.IntelligenceHubDefines_v4 import (
    DEFAULT_EVENT_REGISTRY,
    validate_analysis_result_v4,
)
from ServiceComponent.extraction_quality import compute_extraction_quality
from prompts_event_v4 import EVENT_V4_PROMPT_CATALOG

# 调查报告（doc/20261012）中 10 条最新高分样本 + NAZA 重点案例。
INVESTIGATION_UUIDS = [
    "bb8922f0-86f9-43d3-a7c4-0992059d2bcc",
    "4e4696a0-8101-4516-8e9f-758d614ca6fd",
    "8ef4d42a-646e-4e77-8840-a1696ec1cfa3",
    "745b0e81-0b39-4fa4-b08e-2eea36d257ae",
    "bf690bcb-a351-4595-87c7-93676aa562e5",
    "aeaee497-1ddf-4815-a71f-ffb97bbffc6b",
    "c458db6e-e6a0-423c-913a-4a17e7c1f71e",
    "40620156-5ad5-4b50-a2a3-ce3a4d29ae42",
    "b2220b56-8ed4-4dc3-97c3-acb5e6e8d3cb",
    "a97e23b0-0439-4f25-8a60-bfaf18685393",
    "8150b416-e90e-4269-bf22-1eee538721ab",
]

PROMPTS = EVENT_V4_PROMPT_CATALOG.selectable_table()
MAX_ATTEMPTS = 3


def validate_with_mode(raw_result: dict, version: int):
    """与 EventV4PipelinePorts._validate_analysis 同逻辑：按版本绑定协议处理。"""
    return EVENT_V4_PROMPT_CATALOG.protocol_for(version).validate_result(raw_result)


def analyze_with_retry(client_manager, prompt: str, version: int, data: dict,
                       group: str | None = None) -> dict:
    """模拟生产重试：校验失败把错误摘要拼回 prompt，最多 MAX_ATTEMPTS 次。"""
    base_prompt = prompt
    feedback: list[str] = []
    attempts = 0
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        attempts = attempt
        kwargs = {"target_group_id": group} if group else {}
        client = client_manager.wait_for_available_client(
            f"prompt-compare-{attempt}", timeout=120, **kwargs)
        if client is None:
            raise RuntimeError("no available AI client")
        try:
            raw = analyze_with_ai(client, prompt, data)
        finally:
            client_manager.release_client(client)
        if isinstance(raw, dict):
            raw = dict(raw)
            raw.pop("record_file", None)
        if not isinstance(raw, dict) or raw.get("error"):
            last_error = RuntimeError(str(raw.get("error") if isinstance(raw, dict) else raw))
            continue
        try:
            validated = validate_with_mode(raw, version)
            return {
                "ok": True,
                "attempts": attempts,
                "analysis": validated.model_dump(mode="json", by_alias=True),
                "raw_first": None,
            }
        except Exception as exc:  # 校验失败 → 反馈重试
            last_error = exc
            text = str(exc).replace("\n", " ")[:800]
            feedback.append(text)
            prompt = (
                base_prompt + "\n\n# 上一次输出的修正要求\n"
                + "此前 JSON 未通过服务端校验。请同时修正以下全部错误，重新生成完整 JSON，不要解释：\n"
                + "\n".join(f"{i}. {item}" for i, item in enumerate(feedback, 1))
            )
    return {"ok": False, "attempts": attempts, "error": str(last_error)[:500]}


def quality_of(result: dict, raw_text: str) -> dict | None:
    analysis = result.get("analysis")
    extraction = (analysis or {}).get("EVENT_EXTRACTION")
    if not extraction:
        return None
    validated = validate_analysis_result_v4(analysis)  # 已是完整契约，直接复用
    return compute_extraction_quality(
        validated.EVENT_EXTRACTION, DEFAULT_EVENT_REGISTRY, raw_text=raw_text)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=int, default=0, help="追加随机近期样本数")
    parser.add_argument("--uuids", default="", help="只跑指定 UUID（逗号分隔）")
    parser.add_argument("--output", default="_data/prompt_compare", help="输出目录")
    parser.add_argument("--versions", default="41,42", help="对比的 prompt 版本")
    parser.add_argument("--group", default="",
                        help="AI 客户端分组；空字符串为不限制（与生产 news 子系统一致）")
    parser.add_argument("--exclude", default="",
                        help="排除名称包含该子串的客户端（如 groq，其 TPM 放不下大 prompt）")
    args = parser.parse_args()
    versions = [int(v) for v in args.versions.split(",")]
    args.group = args.group or None

    mongo = pymongo.MongoClient("mongodb://localhost:27017")["IntelligenceIntegrationSystem"]
    archived = mongo["intelligence_v4_archived"]

    uuids = [u.strip() for u in args.uuids.split(",") if u.strip()] or list(INVESTIGATION_UUIDS)
    if args.sample > 0:
        recent = archived.find(
            {"UUID": {"$nin": uuids}},
            {"UUID": 1},
        ).sort("APPENDIX.__TIME_ARCHIVED__", -1).limit(200)
        pool = [doc["UUID"] for doc in recent]
        uuids.extend(random.sample(pool, min(args.sample, len(pool))))

    records = {}
    for uuid in uuids:
        doc = archived.find_one({"UUID": uuid}, {"APPENDIX.RAW_DATA": 1, "EVENT_TITLE": 1})
        raw = ((doc or {}).get("APPENDIX") or {}).get("RAW_DATA") or {}
        if not raw.get("content"):
            print(f"[skip] {uuid}: 归档中无 RAW_DATA")
            continue
        records[uuid] = raw
    print(f"样本数: {len(records)}")

    clients, group_limits = load_config("_config/ai_clients.yaml")
    excludes = [s.strip().lower() for s in args.exclude.split(",") if s.strip()]
    clients = {
        name: client for name, client in clients.items()
        if not any(ex in name.lower() for ex in excludes)
    }
    used_groups = {client.group_id for client in clients.values()}
    group_limits = {
        group: limit for group, limit in group_limits.items()
        if group in used_groups
    }
    client_manager = build_manager(clients, group_limits)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc)

    all_results = []
    for index, (uuid, raw) in enumerate(records.items(), 1):
        data = {
            "UUID": uuid,
            "title": raw.get("title", ""),
            "content": raw.get("content", ""),
            "informant": raw.get("informant", ""),
            "pub_time": str(raw.get("pub_time", "")),
        }
        raw_text = f"{data['title']}\n{data['content']}"
        for version in versions:
            t0 = time.perf_counter()
            result = analyze_with_retry(client_manager, PROMPTS[version], version, data,
                                        group=args.group)
            elapsed = time.perf_counter() - t0
            entry = {
                "uuid": uuid,
                "prompt_version": version,
                "mode": EVENT_V4_PROMPT_CATALOG.spec_for(version).protocol,
                "elapsed_sec": round(elapsed, 1),
                **result,
            }
            if result["ok"]:
                try:
                    entry["quality"] = quality_of(result, raw_text)
                except Exception as exc:
                    entry["quality"] = {"error": str(exc)[:200]}
            all_results.append(entry)
            status = "OK" if result["ok"] else f"FAIL({result.get('error', '')[:60]})"
            print(f"[{index}/{len(records)}] {uuid[:8]} v{version} ({entry['mode']}): "
                  f"{status}, attempts={result['attempts']}, {elapsed:.1f}s", flush=True)

    payload = {
        "started_at": started.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "versions": versions,
        "results": all_results,
    }
    out_file = output_dir / f"compare_{started.strftime('%Y%m%dT%H%M%SZ')}.json"
    out_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8")

    # 聚合对比
    print("\n===== 聚合对比 =====")
    for version in versions:
        entries = [e for e in all_results if e["prompt_version"] == version]
        ok = [e for e in entries if e["ok"]]
        attempts = [e["attempts"] for e in entries]
        q = [e["quality"] for e in ok if isinstance(e.get("quality"), dict)
             and "error" not in e["quality"] and e["quality"]]
        unreg = sum(x["unregistered_role_count"] for x in q)
        conflicts = sum(x["role_type_conflict_count"] for x in q)
        pred_hit = sum(x["surface_grounding"]["predicate"]["hit"] for x in q)
        pred_total = sum(x["surface_grounding"]["predicate"]["total"] for x in q)
        time_hit = sum(x["surface_grounding"]["time"]["hit"] for x in q)
        time_total = sum(x["surface_grounding"]["time"]["total"] for x in q)
        elapsed = [e["elapsed_sec"] for e in entries]
        print(f"v{version} ({EVENT_V4_PROMPT_CATALOG.spec_for(version).protocol}): "
              f"成功 {len(ok)}/{len(entries)}, "
              f"平均尝试 {sum(attempts)/max(len(attempts),1):.2f}, "
              f"未登记角色 {unreg}, 角色类型冲突 {conflicts}, "
              f"predicate接地 {pred_hit}/{pred_total}, time接地 {time_hit}/{time_total}, "
              f"平均耗时 {sum(elapsed)/max(len(elapsed),1):.1f}s")
    print(f"\n详细结果: {out_file}")


if __name__ == "__main__":
    main()
