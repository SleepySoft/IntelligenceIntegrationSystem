"""汇总 prompt_recall_two_stage_experiment.py 的实际调用结果。"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ServiceComponent.extraction_quality import compute_extraction_quality
from ServiceComponent.IntelligenceHubDefines_v4 import (
    DEFAULT_EVENT_REGISTRY, validate_analysis_result_v4,
)
SOURCE = ROOT / "_data/prompt_compare/recall_two_stage_results.json"
TARGET = ROOT / "_data/prompt_compare/recall_two_stage_summary.json"


def _ratio(hit, total):
    return round(hit / total, 4) if total else None


def _quality(result, raw_text):
    try:
        validated = validate_analysis_result_v4(result["analysis"])
        return compute_extraction_quality(
            validated.EVENT_EXTRACTION, DEFAULT_EVENT_REGISTRY, raw_text=raw_text)
    except Exception as exc:
        return {"error": str(exc)[:500]}


def _method(rows, method):
    results = [r[method] for r in rows]
    ok = [r for r in results if r.get("ok")]
    recalls = [r["reference_recall"] for r in ok if r.get("reference_recall")]
    quality = [r["_quality"] for r in ok if "error" not in r["_quality"]]
    totals = [r["totals"] for r in results]
    entities, roles = [], []
    for result in ok:
        extraction = result["analysis"].get("EVENT_EXTRACTION") or {}
        entities.append(len(extraction.get("entities", [])))
        roles.append(sum(len(refs) for event in extraction.get("events", [])
                         for refs in event.get("core", {}).get("roles", {}).values()))
    participant_hit = sum(r["participant_hit"] for r in recalls)
    participant_total = sum(r["participant_total"] for r in recalls)
    time_hit = sum(r["time_hit"] for r in recalls)
    time_total = sum(r["time_total"] for r in recalls)

    def quality_count(key):
        return sum(q.get(key, 0) for q in quality)

    def grounding(kind):
        hit = sum(q["surface_grounding"][kind]["hit"] for q in quality)
        total = sum(q["surface_grounding"][kind]["total"] for q in quality)
        return {"hit": hit, "total": total, "rate": _ratio(hit, total)}

    return {
        "samples": len(results), "success": len(ok), "success_rate": _ratio(len(ok), len(results)),
        "first_pass": sum(bool(r.get("first_pass")) for r in results),
        "first_pass_rate": _ratio(sum(bool(r.get("first_pass")) for r in results), len(results)),
        "participant_hit": participant_hit, "participant_total": participant_total,
        "participant_recall": _ratio(participant_hit, participant_total),
        "time_hit": time_hit, "time_total": time_total, "time_recall": _ratio(time_hit, time_total),
        "mean_entities": round(sum(entities) / len(entities), 3) if entities else None,
        "mean_role_bindings": round(sum(roles) / len(roles), 3) if roles else None,
        "unregistered_roles": quality_count("unregistered_role_count"),
        "role_type_conflicts": quality_count("role_type_conflict_count"),
        "predicate_grounding": grounding("predicate"), "time_grounding": grounding("time"),
        "calls": sum(t["calls"] for t in totals),
        "input_tokens": sum(t["input"] for t in totals),
        "output_tokens": sum(t["output"] for t in totals),
        "total_tokens": sum(t["total"] for t in totals),
        "elapsed_sec": round(sum(t["elapsed"] for t in totals), 3),
        "failures": [{"uuid": rows[i]["uuid"], "error": r.get("error")}
                     for i, r in enumerate(results) if not r.get("ok")],
    }


def main():
    payload = json.loads(SOURCE.read_text(encoding="utf-8"))
    rows = payload["results"]
    inputs = {r["uuid"]: r for r in json.loads(
        (ROOT / "_data/prompt_compare/recall_two_stage_inputs.json").read_text(encoding="utf-8"))}
    for row in rows:
        raw_text = inputs[row["uuid"]]["title"] + "\n" + inputs[row["uuid"]]["content"]
        for method in ("single", "two_stage"):
            if row[method].get("ok"):
                row[method]["_quality"] = _quality(row[method], raw_text)
    paired = [r for r in rows if r["single"].get("ok") and r["two_stage"].get("ok")]
    summary = {"model": payload["model"], "samples": len(rows),
               "started_at": payload.get("started_at"), "finished_at": payload.get("finished_at"),
               "methods": {m: _method(rows, m) for m in ("single", "two_stage")},
               "paired": {"samples": len(paired),
                          "methods": {m: _method(paired, m) for m in ("single", "two_stage")}},
               "per_sample": [{
                   "uuid": r["uuid"], "title": r["title"],
                   "reference_participants": r["reference"]["participant_total"],
                   "single_recall": (r["single"].get("reference_recall") or {}).get("participant_hit"),
                   "two_stage_recall": (r["two_stage"].get("reference_recall") or {}).get("participant_hit"),
                   "single_ok": r["single"]["ok"], "two_stage_ok": r["two_stage"]["ok"],
                   "single_tokens": r["single"]["totals"]["total"],
                   "two_stage_tokens": r["two_stage"]["totals"]["total"],
               } for r in rows]}
    TARGET.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary["methods"], ensure_ascii=False, indent=2))
    print(TARGET)


if __name__ == "__main__":
    main()
