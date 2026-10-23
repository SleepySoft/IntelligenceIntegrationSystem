"""批量分析子系统（Provider Batch 通道）的单元测试。"""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from BatchAnalysis.service import BatchAnalysisService
from ServiceComponent.pipeline import ARCHIVE_REQUESTED


class FakeCacheCollection:
    def __init__(self, records):
        self.records = records

    def find(self, query, sort=None):
        return list(self.records)

    def find_one(self, query, projection=None):
        return next((r for r in self.records
                     if all(r.get(k) == v for k, v in query.items())), None)


class FakeCache:
    def __init__(self, records):
        self.collection = FakeCacheCollection(records)


class FakeCtx:
    name = "news"

    def __init__(self, cache):
        self.mongo_db_cache = cache
        self.event_v4_archive_repository = None


class FakeRegistry:
    def __init__(self, ctx):
        self.ctx = ctx

    def enabled_subsystems(self):
        return [self.ctx]


class FakeRepository:
    def __init__(self):
        self.sources = set()
        self.low_values = []

    def contains_source(self, informant):
        return informant in self.sources

    def save_low_value(self, record):
        self.low_values.append(record)


class FakePorts:
    """只保留批量服务用到的管线面。"""

    def __init__(self):
        from prompts_event_v4 import EVENT_V4_PROMPT_CATALOG
        self._prompt_catalog = EVENT_V4_PROMPT_CATALOG
        self.repository = FakeRepository()
        self.prepared = []

    def _require_archive_repository(self, ctx):
        return self.repository

    def prepare_analysis_outcome(self, ctx, original_data, analysis, **kwargs):
        from ServiceComponent.pipeline.contracts import StageResult
        self.prepared.append((original_data.get("UUID"), kwargs.get("prompt_version")))
        return StageResult.allow({"prepared": original_data.get("UUID")})


class FakeRuntime:
    def __init__(self):
        self.emitted = []

    def emit(self, event, priority=None):
        self.emitted.append(event)


def _service(tmp_path, records):
    ctx = FakeCtx(FakeCache(records))
    ports = FakePorts()
    service = BatchAnalysisService(
        FakeRegistry(ctx), ports,
        base_url="https://batch.test/v1", model="test-model",
        api_key="sk-test", db_path=tmp_path / "batch.db",
        poll_interval_sec=9999)
    return service, ports


def _batch_result_line(uuid, title="测试事件"):
    body = {
        "choices": [{"message": {"content": json.dumps({
            "TAXONOMY": "NonIntelligence", "SUB_CATEGORY": [],
            "REASON": "测试", "NON_INTELLIGENCE_REASON": "other_non_intelligence",
            "RATE": {}}, ensure_ascii=False)}}]}
    return {"custom_id": uuid, "response": {"status_code": 200, "body": body},
            "error": None}


def test_finalize_injects_validated_results_via_shared_path(tmp_path, monkeypatch):
    record = {"UUID": "u-1", "informant": "https://example.test/1",
              "title": "t", "content": "内容内容内容"}
    service, ports = _service(tmp_path, [record])
    runtime = FakeRuntime()
    service._runtime = runtime

    with service._lock, service._connect() as conn:
        conn.execute(
            "INSERT INTO jobs (job_id, status, prompt_version, created_at, updated_at)"
            " VALUES (?,?,?,?,?)",
            ("job-1", "completed", 46, "t", "t"))

    content = json.dumps(_batch_result_line("u-1"), ensure_ascii=False)

    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: SimpleNamespace(
        text=content, raise_for_status=lambda: None))

    service._finalize("job-1", "file-1")

    # 经公共后段 prepare + 发出归档事件；低价值（NonIntelligence）路径在 prepare 内处理
    assert ports.prepared == [("u-1", 46)]
    assert runtime.emitted[0].event_type == ARCHIVE_REQUESTED
    job = service.list_jobs()[0]
    assert job["status"] == "injected"


def test_finalize_is_idempotent(tmp_path, monkeypatch):
    record = {"UUID": "u-1", "informant": "https://example.test/1",
              "title": "t", "content": "内容内容内容"}
    service, ports = _service(tmp_path, [record])
    service._runtime = FakeRuntime()
    with service._lock, service._connect() as conn:
        conn.execute(
            "INSERT INTO jobs (job_id, status, prompt_version, created_at, updated_at)"
            " VALUES (?,?,?,?,?)", ("job-1", "completed", 46, "t", "t"))

    content = json.dumps(_batch_result_line("u-1"), ensure_ascii=False)
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: SimpleNamespace(
        text=content, raise_for_status=lambda: None))

    service._finalize("job-1", "file-1")
    service._finalize("job-1", "file-1")  # 重跑不重复注入
    assert len(ports.prepared) == 1


def test_submit_backlog_empty_returns_reason(tmp_path):
    service, _ = _service(tmp_path, [])
    result = service.submit_backlog()
    assert result == {"submitted": False, "reason": "no_backlog"}
