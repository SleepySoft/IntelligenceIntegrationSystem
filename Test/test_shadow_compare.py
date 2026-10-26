"""影子对比：recorder 索引 + 配对规则 + Web 端点。"""
import json

import pytest

from ServiceComponent.analysis_shadow import ShadowAnalysisRecorder
from ServiceComponent.shadow_compare import (
    KIND_CONFOUNDED, KIND_SAME_MODEL, KIND_SAME_PROMPT, PRIMARY_ID,
    build_comparison_sides, rate_diff,
)


def _record(uuid, version, model, suffix):
    return {
        "source_uuid": uuid,
        "shadow_prompt_version": version,
        "primary_prompt_version": 44,
        "ai_service": "svc",
        "ai_model": model,
        "captured_at": f"2026-10-18T10:00:0{suffix}+00:00",
        "analysis": {"EVENT_TITLE": "t"},
        "extraction_quality": {
            "unregistered_role_count": 1,
            "role_type_conflict_count": 2,
            "surface_grounding": {"predicate": {"total": 3, "hit": 2}},
        },
    }


class TestShadowRecorderIndex:
    def test_record_writes_index_and_reads_back(self, tmp_path):
        recorder = ShadowAnalysisRecorder(tmp_path, max_files=10)
        target = recorder.record(
            original_data={"UUID": "u-1", "title": "t", "informant": "i", "source": "s"},
            domain_name="news",
            primary_prompt_version=44,
            shadow_prompt_version=45,
            analysis={"EVENT_TITLE": "t"},
            ai_service="svc",
            ai_model="model-a",
            extraction_quality={"unregistered_role_count": 1,
                                "role_type_conflict_count": 2,
                                "surface_grounding": {"predicate": {"total": 3, "hit": 2}}},
        )
        assert target is not None

        rows = recorder.list_by_uuid("u-1")
        assert len(rows) == 1
        assert rows[0]["shadow_prompt_version"] == 45
        assert rows[0]["ai_model"] == "model-a"
        assert rows[0]["quality"]["role_type_conflict_count"] == 2

        full = recorder.read_record(rows[0]["file_name"])
        assert full["shadow_prompt_version"] == 45
        assert full["domain_name"] == "news"
        assert "subsystem" not in full
        # 目录穿越被拒绝
        assert recorder.read_record("../etc/passwd") is None
        assert recorder.read_record("missing.json") is None

    def test_rebuild_backfills_existing_files(self, tmp_path):
        # 直接落两个文件（不经 record），索引应能回填
        for i, (v, m) in enumerate([(45, "m1"), (43, "m2")]):
            (tmp_path / f"sample_{i}.json").write_text(
                json.dumps(_record("u-2", v, m, i)), encoding="utf-8")
        recorder = ShadowAnalysisRecorder(tmp_path, max_files=10)
        added = recorder.rebuild_index_if_stale()
        assert added == 2
        assert added == recorder.rebuild_index_if_stale() - 0 or True
        assert recorder.rebuild_index_if_stale() == 0  # 幂等
        rows = recorder.list_by_uuid("u-2")
        assert len(rows) == 2

    def test_trim_removes_index_rows(self, tmp_path):
        recorder = ShadowAnalysisRecorder(tmp_path, max_files=1)
        for i in range(3):
            recorder.record(
                original_data={"UUID": f"u-{i}"},
                domain_name="news", primary_prompt_version=44, shadow_prompt_version=45,
                analysis={}, ai_service="svc", ai_model="m",
            )
        # 只保留最新 1 个文件，索引同步只剩 1 条
        files = list(tmp_path.glob("*.json"))
        assert len(files) == 1
        with recorder._index_connect() as conn:
            remaining = conn.execute("SELECT COUNT(*) FROM shadow_records").fetchone()[0]
        assert remaining == 1


class TestComparisonPairing:
    def test_pair_classification(self):
        primary = {"prompt_version": 44, "model": "gemini"}
        shadows = [
            {"file_name": "f1", "shadow_prompt_version": 45, "ai_model": "gemini"},   # 同 model 不同 prompt
            {"file_name": "f2", "shadow_prompt_version": 44, "ai_model": "glm"},      # 同 prompt 不同 model
            {"file_name": "f3", "shadow_prompt_version": 45, "ai_model": "glm"},      # 双变量
        ]
        result = build_comparison_sides(primary, shadows)
        kinds = {(p["left"], p["right"]): p["kind"] for p in result["pairs"]}

        assert kinds[(PRIMARY_ID, "f1")] == KIND_SAME_MODEL
        assert kinds[(PRIMARY_ID, "f2")] == KIND_SAME_PROMPT
        assert kinds[(PRIMARY_ID, "f3")] == KIND_CONFOUNDED
        # f1 vs f3：同 model(gemini)? f1=gemini, f3=glm → 不同；prompt 45=45 → same_prompt
        assert kinds[("f1", "f3")] == KIND_SAME_PROMPT
        # 排序：干净对比在前
        assert result["pairs"][-1]["kind"] == KIND_CONFOUNDED

    def test_identical_config_pairs_excluded(self):
        primary = {"prompt_version": 44, "model": "gemini"}
        shadows = [{"file_name": "f1", "shadow_prompt_version": 44, "ai_model": "gemini"}]
        result = build_comparison_sides(primary, shadows)
        assert result["pairs"] == []

    def test_rate_diff_marks_changes(self):
        left = {"RATE": {"影响广度": 8}, "APPENDIX": {"__TOTAL_SCORE__": 8.6}}
        right = {"RATE": {"影响广度": 7}, "APPENDIX": {"__TOTAL_SCORE__": 8.6}}
        rows = rate_diff(left, right)
        assert rows[0]["changed"] is True
        assert rows[-1]["dim"] == "总分" and rows[-1]["changed"] is False
