"""DeepSeek 单阶段 v48 vs 事实召回→规范编码两阶段离线实验。

只读固定归档输入，不写业务集合。API Key 仅从 DEEPSEEK_API_KEY 环境变量读取。
结果保存到 _data/prompt_compare/recall_two_stage_results.json，支持断点续跑。
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ServiceComponent.analysis_messages import build_analyze_message  # noqa: E402
from ServiceComponent.extraction_quality import compute_extraction_quality  # noqa: E402
from ServiceComponent.IntelligenceHubDefines_v4 import (  # noqa: E402
    DEFAULT_EVENT_REGISTRY, validate_analysis_result_v4,
)
from prompts_event_v4 import EVENT_ANALYSIS_PROMPT_V48, EVENT_V4_PROMPT_CATALOG  # noqa: E402

INPUT_FILE = ROOT / "_data/prompt_compare/recall_two_stage_inputs.json"
OUTPUT_FILE = ROOT / "_data/prompt_compare/recall_two_stage_results.json"
MODEL = "deepseek-flash"
BASE_URL = "https://api.deepseek.com/v1/chat/completions"
MAX_ATTEMPTS = 3

RECALL_PROMPT = """你是情报事件抽取器。只从正文提取一至三项核心事件及其事实，不做分类、评级、摘要，也不要考虑任何谓词目录或角色白名单。
只输出合法 JSON：{"events":[{"description":"事件事实","participants":[{"name":"正文中的完整名称","role_in_event":"自然语言角色"}],"time":{"normalized":"可靠规范化时间或空字符串","surface":"正文逐字时间或空字符串"},"status":"ongoing/planned/completed/doubted/denied/unknown"}]}
核心参与者是缺少后会改变或严重削弱事件理解的行动方、权威方、对象/目标、承受方，以及转移类事件的来源、去向或转移对象。不得因后续可能难以编码而省略。没有可靠时间时 time 的两个值均为空字符串，不得使用报道发布时间。所有事实必须来自正文。"""

ENCODE_ADDENDUM = """

# 第一阶段事实契约（本实验附加，优先级高于省略规则）
用户消息会附带第一阶段召回结果。其 participants 是已确认的核心参与者清单，第二阶段只负责规范编码，不得重新筛选或删除这些参与者。每个参与者都必须出现在对应事件的语义合适角色中；已登记谓词无法合法表达时使用 predicate.id=null 的开放谓词。不得复制第一阶段自然语言 role_in_event 作为最终角色键，最终角色键仍须遵守 Registry 白名单或开放 Frame 拓扑。第一阶段 time.surface 非空时，对应事件必须保留可靠时间；不得用报道发布时间替代。
"""


def _extract_json(text: str) -> dict:
    text = text.strip().removeprefix("```json").removesuffix("```").strip()
    while "<think>" in text and "</think>" in text:
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(text[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("response is not a JSON object")
    return value


def _call(session: requests.Session, messages: list[dict], stage: str) -> tuple[dict, dict]:
    started = time.perf_counter()
    response = session.post(BASE_URL, json={
        "model": MODEL, "messages": messages, "temperature": 0,
        "max_tokens": 8192, "response_format": {"type": "json_object"},
    }, timeout=180)
    elapsed = time.perf_counter() - started
    response.raise_for_status()
    payload = response.json()
    usage = payload.get("usage") or {}
    meta = {
        "stage": stage, "elapsed_sec": round(elapsed, 3),
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
        "cached_tokens": (usage.get("prompt_cache_hit_tokens")
                          or usage.get("prompt_tokens_details", {}).get("cached_tokens") or 0),
    }
    content = payload["choices"][0]["message"]["content"]
    return _extract_json(content), meta


def _validate(raw: dict):
    return EVENT_V4_PROMPT_CATALOG.protocol_for(48).validate_result(copy.deepcopy(raw))


def _run_single(session: requests.Session, data: dict) -> dict:
    feedback = []
    calls = []
    error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        prompt = EVENT_ANALYSIS_PROMPT_V48
        if feedback:
            prompt += "\n\n# 上一次输出的修正要求\n" + "\n".join(feedback)
        try:
            raw, meta = _call(session, build_analyze_message(prompt, data), "single")
            calls.append(meta)
            result = _validate(raw)
            return {"ok": True, "analysis": result.model_dump(mode="json", by_alias=True),
                    "first_pass": attempt == 1, "calls": calls}
        except Exception as exc:
            error = str(exc)[:1000]
            if calls:
                feedback.append(f"此前输出未通过校验：{error}；请重新输出完整合法 JSON。")
    return {"ok": False, "error": error, "first_pass": False, "calls": calls}


def _run_two_stage(session: requests.Session, data: dict) -> dict:
    calls = []
    try:
        recall_messages = build_analyze_message(RECALL_PROMPT, data)
        recall, meta = _call(session, recall_messages, "recall")
        calls.append(meta)
        if not isinstance(recall.get("events"), list) or not recall["events"]:
            raise ValueError("recall returned no events")
    except Exception as exc:
        return {"ok": False, "error": f"recall: {str(exc)[:1000]}",
                "first_pass": False, "calls": calls}

    feedback = []
    error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        prompt = EVENT_ANALYSIS_PROMPT_V48 + ENCODE_ADDENDUM
        if feedback:
            prompt += "\n\n# 上一次输出的修正要求\n" + "\n".join(feedback)
        messages = build_analyze_message(prompt, data)
        messages[-1]["content"] += "\n\n## 第一阶段召回结果（核心参与者不得删除）\n" + json.dumps(
            recall, ensure_ascii=False)
        try:
            raw, meta = _call(session, messages, "encode")
            calls.append(meta)
            result = _validate(raw)
            return {"ok": True, "recall": recall,
                    "analysis": result.model_dump(mode="json", by_alias=True),
                    "first_pass": attempt == 1, "calls": calls}
        except Exception as exc:
            error = str(exc)[:1000]
            if calls:
                feedback.append(f"此前输出未通过校验：{error}；请保留全部核心参与者并重新输出完整合法 JSON。")
    return {"ok": False, "recall": recall, "error": error,
            "first_pass": False, "calls": calls}


def _norm(text: str) -> str:
    return re.sub(r"[\s\u3000·•,，。；;：:（）()\[\]【】'\"《》_-]", "", text or "").lower()


def _analysis_names(analysis: dict) -> list[str]:
    extraction = analysis.get("EVENT_EXTRACTION") or {}
    return [e.get("name", "") for e in extraction.get("entities", [])]


def _reference_recall(entry: dict) -> dict:
    expected = []
    for event_index, event in enumerate((entry.get("recall") or {}).get("events", []), 1):
        for participant in event.get("participants", []):
            if participant.get("name"):
                expected.append({"event": event_index, "name": participant["name"],
                                 "role": participant.get("role_in_event", "")})
    names = [_norm(n) for n in _analysis_names(entry.get("analysis") or {})]
    text = _norm(json.dumps(entry.get("analysis") or {}, ensure_ascii=False))
    rows = []
    for item in expected:
        target = _norm(item["name"])
        hit = bool(target) and (any(target in n or n in target for n in names if n) or target in text)
        rows.append({**item, "hit": hit})
    times = []
    for event_index, event in enumerate((entry.get("recall") or {}).get("events", []), 1):
        surface = (event.get("time") or {}).get("surface", "")
        if surface:
            times.append({"event": event_index, "surface": surface,
                          "hit": _norm(surface) in _norm(json.dumps(
                              (entry.get("analysis") or {}).get("EVENT_EXTRACTION") or {},
                              ensure_ascii=False))})
    return {"participants": rows, "participant_hit": sum(r["hit"] for r in rows),
            "participant_total": len(rows), "times": times,
            "time_hit": sum(r["hit"] for r in times), "time_total": len(times)}


def _score_reference(analysis: dict, reference: dict) -> dict:
    names = [_norm(n) for n in _analysis_names(analysis)]
    text = _norm(json.dumps(analysis, ensure_ascii=False))
    participants = []
    for item in reference["participants"]:
        target = _norm(item["name"])
        hit = bool(target) and (any(target in n or n in target for n in names if n) or target in text)
        participants.append({k: v for k, v in item.items() if k != "hit"} | {"hit": hit})
    extraction_text = _norm(json.dumps(analysis.get("EVENT_EXTRACTION") or {}, ensure_ascii=False))
    times = [{"event": item["event"], "surface": item["surface"],
              "hit": _norm(item["surface"]) in extraction_text}
             for item in reference["times"]]
    return {"participants": participants,
            "participant_hit": sum(r["hit"] for r in participants),
            "participant_total": len(participants), "times": times,
            "time_hit": sum(r["hit"] for r in times), "time_total": len(times)}


def _quality(analysis: dict, raw_text: str) -> dict:
    extraction = analysis.get("EVENT_EXTRACTION") or {}
    if not extraction:
        return {}
    result = validate_analysis_result_v4(analysis)
    quality = compute_extraction_quality(result.EVENT_EXTRACTION, DEFAULT_EVENT_REGISTRY, raw_text)
    return quality


def _totals(result: dict) -> dict:
    calls = result.get("calls", [])
    return {"calls": len(calls), "input": sum(c["prompt_tokens"] for c in calls),
            "output": sum(c["completion_tokens"] for c in calls),
            "total": sum(c["total_tokens"] for c in calls),
            "elapsed": round(sum(c["elapsed_sec"] for c in calls), 3)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise SystemExit("DEEPSEEK_API_KEY is required")
    samples = json.loads(INPUT_FILE.read_text(encoding="utf-8"))
    if args.limit:
        samples = samples[:args.limit]
    payload = {"started_at": datetime.now(timezone.utc).isoformat(), "model": MODEL,
               "results": []}
    if args.resume and OUTPUT_FILE.exists():
        payload = json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
    done = {r["uuid"] for r in payload["results"]}
    session = requests.Session()
    session.headers["Authorization"] = "Bearer " + os.environ["DEEPSEEK_API_KEY"]
    for index, sample in enumerate(samples, 1):
        if sample["uuid"] in done:
            continue
        print(f"[{index}/{len(samples)}] {sample['uuid'][:8]} {sample['title']}", flush=True)
        data = {"UUID": sample["uuid"], "title": sample["title"],
                "content": sample["content"], "pub_time": sample.get("pub_time", "")}
        raw_text = sample["title"] + "\n" + sample["content"]
        single = _run_single(session, data)
        two = _run_two_stage(session, data)
        reference = _reference_recall(two)
        entry = {"uuid": sample["uuid"], "title": sample["title"],
                 "reference": reference, "single": single, "two_stage": two}
        for method, result in (("single", single), ("two_stage", two)):
            result["totals"] = _totals(result)
            result["reference_recall"] = (_score_reference(result["analysis"], reference)
                                           if result.get("ok") else None)
            if result.get("ok"):
                try:
                    result["quality"] = _quality(result["analysis"], raw_text)
                except Exception as exc:
                    result["quality"] = {"error": str(exc)[:300]}
        payload["results"].append(entry)
        payload["finished_at"] = datetime.now(timezone.utc).isoformat()
        OUTPUT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  single={single['ok']} two={two['ok']} ref={reference['participant_hit']}/{reference['participant_total']}", flush=True)
    print(OUTPUT_FILE)


if __name__ == "__main__":
    main()
