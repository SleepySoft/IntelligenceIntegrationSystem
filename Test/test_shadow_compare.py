"""影子对比：recorder 索引 + 配对规则 + Web 端点。"""
from types import SimpleNamespace

from bson import ObjectId

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
    class Collection:
        def __init__(self):
            self.docs = []
            self.indexes = []

        def create_index(self, keys, **kwargs):
            self.indexes.append((keys, kwargs))

        def insert_one(self, document):
            value = dict(document)
            value['_id'] = ObjectId()
            self.docs.append(value)
            return SimpleNamespace(inserted_id=value['_id'])

        def find(self, query, projection=None):
            def matches(item):
                for key, expected in query.items():
                    if isinstance(expected, dict) and "$in" in expected:
                        if item.get(key) not in expected["$in"]:
                            return False
                    elif item.get(key) != expected:
                        return False
                return True

            docs = [dict(item) for item in self.docs if matches(item)]
            if projection:
                docs = [{k: v for k, v in item.items() if projection.get(k, 1)} for item in docs]

            class Cursor(list):
                def sort(self, key, direction):
                    super().sort(key=lambda item: item.get(key), reverse=direction < 0)
                    return self
            return Cursor(docs)

        def find_one(self, query):
            return next((dict(item) for item in self.docs
                         if all(item.get(k) == v for k, v in query.items())), None)

    def test_record_indexes_and_reads_back_by_domain(self):
        collection = self.Collection()
        recorder = ShadowAnalysisRecorder(collection)
        target = recorder.record(
            original_data={"UUID": "u-1", "title": "t", "informant": "i", "source": "s"},
            domain_name="news",
            primary_prompt_version=44,
            shadow_prompt_version=45,
            analysis={"EVENT_TITLE": "t"},
            ai_service="svc",
            ai_model="model-a",
            total_score=7.2,
            extraction_quality={"unregistered_role_count": 1,
                                "role_type_conflict_count": 2,
                                "surface_grounding": {"predicate": {"total": 3, "hit": 2}}},
        )
        assert target is not None

        rows = recorder.list_by_uuid("u-1", domain_name="news")
        assert len(rows) == 1
        assert rows[0]["shadow_prompt_version"] == 45
        assert rows[0]["ai_model"] == "model-a"
        assert rows[0]["quality"]["role_type_conflict_count"] == 2
        assert rows[0]["total_score"] == 7.2

        full = recorder.read_record(rows[0]["record_id"], domain_name="news")
        assert full["shadow_prompt_version"] == 45
        assert full["domain_name"] == "news"
        assert full["total_score"] == 7.2
        assert "subsystem" not in full
        assert recorder.read_record("../etc/passwd") is None
        assert recorder.read_record(str(ObjectId()), domain_name="industry") is None
        assert collection.indexes

    def test_same_uuid_isolated_by_domain(self):
        recorder = ShadowAnalysisRecorder(self.Collection())
        for domain in ("news", "industry"):
            recorder.record(
                original_data={"UUID": "same"}, domain_name=domain,
                primary_prompt_version=1, shadow_prompt_version=2,
                analysis={}, ai_service="svc", ai_model="m")
        assert len(recorder.list_by_uuid("same", domain_name="industry")) == 1

    def test_existing_source_uuids_is_batched_and_domain_scoped(self):
        recorder = ShadowAnalysisRecorder(self.Collection())
        for uuid, domain in (("a", "industry"), ("b", "industry"), ("a", "news")):
            recorder.record(
                original_data={"UUID": uuid}, domain_name=domain,
                primary_prompt_version=1, shadow_prompt_version=2,
                analysis={}, ai_service="svc", ai_model="m")

        assert recorder.existing_source_uuids(
            ["a", "b", "missing"], domain_name="industry") == {"a", "b"}


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
