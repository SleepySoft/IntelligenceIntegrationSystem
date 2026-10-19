"""影子对比的配对规则：多 prompt × 多 model 场景下哪些比较是有意义的。

规则：只有单变量差异的配对才是"干净对比"——
- same_model：同模型不同 prompt，差异归因于 prompt；
- same_prompt：同 prompt 不同模型，差异归因于模型；
- confounded：prompt 与模型都不同，差异不可归因，仅作参考展示。
"""

from __future__ import annotations

from typing import Any, Mapping

KIND_SAME_MODEL = "same_model"
KIND_SAME_PROMPT = "same_prompt"
KIND_CONFOUNDED = "confounded"

_KIND_LABELS = {
    KIND_SAME_MODEL: "同模型 · 不同 Prompt（评估 prompt 影响）",
    KIND_SAME_PROMPT: "同 Prompt · 不同模型（评估模型影响）",
    KIND_CONFOUNDED: "双变量（差异不可归因，仅供参考）",
}

PRIMARY_ID = "__primary__"


def _side(side_id: str, prompt_version: Any, model: str) -> dict[str, Any]:
    return {
        "id": side_id,
        "prompt_version": prompt_version,
        "model": str(model or ""),
    }


def build_comparison_sides(
        primary: Mapping[str, Any],
        shadows: list[Mapping[str, Any]]) -> dict[str, Any]:
    """构造对比候选与配对。

    primary: {"prompt_version": int, "model": str}（主链归档的 APPENDIX 信息）
    shadows: recorder.list_by_uuid() 的每项（file_name/shadow_prompt_version/ai_model/...）

    返回 {"sides": [...], "pairs": [...]}；
    sides 首项为主链（id=__primary__），pairs 按 same_model → same_prompt → confounded 排序。
    """
    sides = [_side(PRIMARY_ID, primary.get("prompt_version"), primary.get("model"))]
    for record in shadows:
        sides.append(_side(
            str(record.get("file_name") or ""),
            record.get("shadow_prompt_version"),
            record.get("ai_model"),
        ))

    pairs = []
    for i, left in enumerate(sides):
        for right in sides[i + 1:]:
            same_model = (left["model"] and left["model"] == right["model"])
            same_prompt = (left["prompt_version"] is not None
                           and left["prompt_version"] == right["prompt_version"])
            if same_model and same_prompt:
                continue  # 完全同配置，无可比性
            if same_model:
                kind = KIND_SAME_MODEL
            elif same_prompt:
                kind = KIND_SAME_PROMPT
            else:
                kind = KIND_CONFOUNDED
            pairs.append({"left": left["id"], "right": right["id"], "kind": kind})

    order = {KIND_SAME_MODEL: 0, KIND_SAME_PROMPT: 1, KIND_CONFOUNDED: 2}
    pairs.sort(key=lambda p: order[p["kind"]])
    for pair in pairs:
        pair["kind_label"] = _KIND_LABELS[pair["kind"]]
    return {"sides": sides, "pairs": pairs}


def rate_diff(left: Mapping[str, Any], right: Mapping[str, Any]) -> list[dict[str, Any]]:
    """RATE 六维 + 总分的并排差异（供对比页渲染）。"""
    keys: list[str] = []
    for source in (left.get("RATE") or {}, right.get("RATE") or {}):
        for key in source:
            if key not in keys:
                keys.append(key)
    rows = []
    for key in keys:
        lv = (left.get("RATE") or {}).get(key)
        rv = (right.get("RATE") or {}).get(key)
        rows.append({"dim": key, "left": lv, "right": rv, "changed": lv != rv})
    left_total = ((left.get("APPENDIX") or {}).get("__TOTAL_SCORE__"))
    right_total = ((right.get("APPENDIX") or {}).get("__TOTAL_SCORE__"))
    rows.append({"dim": "总分", "left": left_total, "right": right_total,
                 "changed": left_total != right_total})
    return rows


def extraction_summary(analysis: Mapping[str, Any]) -> list[dict[str, Any]]:
    """EVENT_EXTRACTION 的事件结构摘要（谓词 + 角色 → 实体名）。"""
    extraction = (analysis or {}).get("EVENT_EXTRACTION") or {}
    entities = {e.get("id"): e for e in extraction.get("entities") or []}
    rows = []
    for event in extraction.get("events") or []:
        core = event.get("core") or {}
        predicate = core.get("predicate") or {}
        roles = {
            role: [str((entities.get(eid) or {}).get("name") or eid) for eid in ids]
            for role, ids in (core.get("roles") or {}).items()
        }
        rows.append({
            "id": event.get("id"),
            "primary": event.get("id") == extraction.get("primary_event_id"),
            "predicate": predicate.get("id"),
            "surface": predicate.get("surface"),
            "roles": roles,
        })
    return rows
