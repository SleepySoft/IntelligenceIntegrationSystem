"""批量分析子系统：积压情报经 Provider Batch API（凌晨空闲、半价）分析与回注。

设计约束（解耦）：
- 不 import 任何 ServiceComponent 管线实现；只依赖窄接口（见模块尾部 Protocol 注释）：
  * domain_resources：enabled_domains() 提供 cache 访问；
  * outcome_port：prompt 目录 / 协议 / prepare_analysis_outcome / archive repository；
  * runtime：只用来发 ARCHIVE_REQUESTED。
- 与实时主链的关系：只“借道”其领域机制（prompt/消息构造/校验/归档后段），
  自身持有独立的任务状态（SQLite）、HTTP 客户端和轮询线程，可整体禁用。

流程：手动触发 → 构建 JSONL → 上传+创建 batch → 轮询 → 校验 → 回注归档。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ServiceComponent.analysis_messages import build_analyze_message
from ServiceComponent.pipeline import ARCHIVE_REQUESTED
from ServiceComponent.runtime import HubEvent, HubPlugin, HubRuntime

logger = logging.getLogger(__name__)

# outcome_port 需要满足（duck typing，由 Hub 的 EventV4PipelinePorts 直接满足）：
#   .domain_definition(name)  领域定义（prompt/subsystems/storage）
#   .prepare_analysis_outcome(ctx, original_data, analysis, **kwargs) -> StageResult
#   ._require_archive_repository(ctx)


class BatchAnalysisService(HubPlugin):
    """批量分析：手动触发 + 后台轮询 + 同链校验回注。"""

    _SCHEMA = (
        "CREATE TABLE IF NOT EXISTS jobs ("
        " job_id TEXT PRIMARY KEY, status TEXT, provider TEXT, model TEXT,"
        " prompt_version INTEGER, input_file_id TEXT, output_file_id TEXT,"
        " error_file_id TEXT, item_count INTEGER, note TEXT,"
        " created_at TEXT, updated_at TEXT);"
        "CREATE TABLE IF NOT EXISTS items ("
        " job_id TEXT, uuid TEXT, status TEXT, PRIMARY KEY (job_id, uuid));"
    )

    def __init__(self, domain_resources: Any, outcome_port: Any, *,
                 base_url: str, model: str, api_key: str,
                 db_path: str | Path, lookback_days: int = 7,
                 max_items: int = 500, poll_interval_sec: float = 300):
        if not api_key:
            raise ValueError("Batch 分析需要 api_key")
        self.domain_resources = domain_resources
        self.ports = outcome_port
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.db_path = Path(db_path)
        self.lookback_days = lookback_days
        self.max_items = max_items
        self.poll_interval_sec = poll_interval_sec
        self._runtime: HubRuntime | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # ---------------- 插件生命周期 ----------------

    def register(self, runtime: HubRuntime) -> None:
        return None

    def start(self, runtime: HubRuntime) -> None:
        self._runtime = runtime
        self._thread = threading.Thread(
            target=self._poll_loop, name="BatchAnalysisPoll", daemon=True)
        self._thread.start()

    def stop(self, runtime: HubRuntime) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    # ---------------- 持久化 ----------------

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.executescript(self._SCHEMA)
        return conn

    def _job_update(self, job_id: str, **fields) -> None:
        fields["updated_at"] = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET " + ", ".join(f"{k}=?" for k in fields)
                + " WHERE job_id=?",
                (*fields.values(), job_id))

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT 20").fetchall()
            return [dict(r) for r in rows]

    # ---------------- 构建与提交 ----------------

    def _definition(self, ctx: Any) -> Any:
        resolver = getattr(self.ports, "domain_definition", None)
        if callable(resolver):
            return resolver(ctx.name)
        # 兼容独立测试中的旧式 fake port。
        return type("LegacyDomain", (), {
            "prompt_catalog": self.ports._prompt_catalog,
            "subsystems": type("LegacySubsystems", (), {"batch": object()})(),
            "storage": None,
        })()

    def submit_backlog(self, *, lookback_days: int | None = None,
                       limit: int | None = None) -> dict[str, Any]:
        """手动触发：收集积压 → 构建 JSONL → 上传 → 创建 batch。"""
        lookback = lookback_days or self.lookback_days
        cap = min(limit or self.max_items, self.max_items)
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback)

        items: list[tuple[Any, Any, int, str]] = []  # (ctx, record, version, prompt)
        for ctx in self._enabled_domains():
            definition = self._definition(ctx)
            if definition.subsystems.batch is None:
                continue
            catalog = definition.prompt_catalog
            prompt_version = max(catalog.sampling_versions())
            prompt = catalog.sampling_table()[prompt_version]
            cache = getattr(ctx, "mongo_db_cache", None)
            if cache is None:
                continue
            cursor = cache.collection.find(
                {"APPENDIX.__ARCHIVED__": "R", "collect_time": {"$gte": cutoff}},
                sort=[("collect_time", -1)])
            if not isinstance(cursor, list):
                cursor = cursor.limit(cap - len(items))
            for record in list(cursor)[:cap - len(items)]:
                record.pop("_id", None)
                items.append((ctx, record, prompt_version, prompt))
        if not items:
            return {"submitted": False, "reason": "no_backlog"}

        lines = []
        for ctx, record, prompt_version, prompt in items:
            lines.append(json.dumps({
                "custom_id": f"{ctx.name}|{prompt_version}|{record['UUID']}",
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": {
                    "model": self.model,
                    "messages": build_analyze_message(prompt, record),
                    "temperature": 0,
                    "max_tokens": 32768,  # 推理模型需放宽（实测补记 §11）
                },
            }, ensure_ascii=False))
        payload = "\n".join(lines).encode("utf-8")

        import requests
        headers = {"Authorization": f"Bearer {self.api_key}"}
        resp = requests.post(f"{self.base_url}/files", headers=headers,
                             files={"file": ("backlog.jsonl", payload)},
                             data={"purpose": "batch"}, timeout=120)
        resp.raise_for_status()
        file_id = resp.json()["id"]

        resp = requests.post(
            f"{self.base_url}/batches", headers=headers, json={
                "input_file_id": file_id,
                "endpoint": "/v1/chat/completions",
                "completion_window": "24h",
                "name": f"iis-backlog-{datetime.now():%Y%m%d-%H%M}",
            }, timeout=30)
        resp.raise_for_status()
        job = resp.json()
        job_id = job["id"]

        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO jobs (job_id, status, provider, model, prompt_version,"
                " input_file_id, item_count, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (job_id, job.get("status", "validating"), "mimo", self.model,
                 -1, file_id, len(items),
                 datetime.now(timezone.utc).isoformat(),
                 datetime.now(timezone.utc).isoformat()))
            conn.executemany(
                "INSERT OR REPLACE INTO items (job_id, uuid, status) VALUES (?,?,?)",
                [(job_id, f"{ctx.name}|{version}|{record['UUID']}", "pending")
                 for ctx, record, version, _prompt in items])
        logger.warning("批量分析已提交：job=%s，%d 条（凌晨 1-8 点执行）", job_id, len(items))
        return {"submitted": True, "job_id": job_id, "item_count": len(items),
                "prompt_version": -1}

    # ---------------- 轮询与回注 ----------------

    def _poll_loop(self) -> None:
        while not self._stop.wait(self.poll_interval_sec):
            try:
                self._poll_once()
            except Exception:
                logger.warning("批量分析轮询失败", exc_info=True)

    def _poll_once(self) -> None:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT job_id FROM jobs WHERE status IN"
                " ('validating','in_progress','finalizing')").fetchall()
        for (job_id,) in rows:
            self._poll_job(job_id)

    def _poll_job(self, job_id: str) -> None:
        import requests
        headers = {"Authorization": f"Bearer {self.api_key}"}
        resp = requests.get(f"{self.base_url}/batches/{job_id}",
                            headers=headers, timeout=30)
        resp.raise_for_status()
        job = resp.json()
        status = job.get("status")
        self._job_update(job_id, status=status,
                         output_file_id=job.get("output_file_id"),
                         error_file_id=job.get("error_file_id"))
        if status == "completed" and job.get("output_file_id"):
            self._finalize(job_id, job["output_file_id"])

    def _finalize(self, job_id: str, output_file_id: str) -> None:
        """下载结果 → 同协议链校验 → 回注归档。幂等：items 已完成的跳过。"""
        import requests
        headers = {"Authorization": f"Bearer {self.api_key}"}
        content = requests.get(
            f"{self.base_url}/files/{output_file_id}/content",
            headers=headers, timeout=180).text

        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT prompt_version FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            done = {r[0] for r in conn.execute(
                "SELECT uuid FROM items WHERE job_id=? AND status='injected'",
                (job_id,)).fetchall()}
        job_prompt_version = row[0]

        injected = failed = skipped = 0
        for line in content.splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            item_key = str(item.get("custom_id") or "")
            if not item_key or item_key in done:
                continue
            domain_name, prompt_version, uuid = self._decode_item_key(
                item_key, job_prompt_version)
            ctx = self.domain_resources.resolve(domain_name) if domain_name else None
            if ctx is None:
                ctx = self._find_context(uuid)
            if ctx is None or self._definition(ctx).subsystems.batch is None:
                skipped += 1
                self._item_mark(job_id, item_key, "skipped")
                continue
            catalog = self._definition(ctx).prompt_catalog
            protocol = self._protocol_for_batch(
                catalog, prompt_version, job_id=job_id)
            try:
                body = (item.get("response") or {}).get("body") or {}
                text = (body.get("choices") or [{}])[0].get("message", {}).get(
                    "content", "")
                start = text.find("{")
                raw = json.loads(text[start:text.rfind("}") + 1])
                analysis = protocol.validate_result(raw)
            except Exception:
                failed += 1
                self._item_mark(job_id, item_key, "failed")
                continue
            if self._inject(ctx, uuid, analysis, prompt_version):
                injected += 1
                self._item_mark(job_id, item_key, "injected")
            else:
                skipped += 1
                self._item_mark(job_id, item_key, "skipped")
        self._job_update(job_id, status="injected",
                         note=f"injected={injected} failed={failed} skipped={skipped}")
        logger.warning("批量分析回注完成：job=%s 归档 %d 条，失败 %d，跳过 %d",
                       job_id, injected, failed, skipped)

    def _item_mark(self, job_id: str, uuid: str, status: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO items (job_id, uuid, status) VALUES (?,?,?)",
                (job_id, uuid, status))

    @staticmethod
    def _decode_item_key(item_key: str, fallback_version: int) -> tuple[str | None, int, str]:
        parts = item_key.split("|", 2)
        if len(parts) == 3:
            try:
                return parts[0], int(parts[1]), parts[2]
            except ValueError:
                pass
        return None, int(fallback_version), item_key

    def _find_context(self, uuid: str) -> Any | None:
        for ctx in self._enabled_domains():
            cache = getattr(ctx, "mongo_db_cache", None)
            if cache is not None and cache.collection.find_one({"UUID": uuid}):
                return ctx
        return None

    def _enabled_domains(self):
        enabled = getattr(self.domain_resources, "enabled_domains", None)
        if not callable(enabled):
            enabled = self.domain_resources.enabled_subsystems
        return enabled()

    @staticmethod
    def _protocol_for_batch(catalog: Any, prompt_version: int, *, job_id: str) -> Any:
        if catalog.has(prompt_version):
            return catalog.protocol_for(prompt_version)
        current_versions = catalog.sampling_versions()
        if len(current_versions) != 1:
            raise ValueError(f"batch_prompt_version_not_configured:{prompt_version}")
        current_version = current_versions[0]
        logger.warning(
            "Batch job %s uses removed prompt v%s; validate with current v%s protocol",
            job_id, prompt_version, current_version,
        )
        return catalog.protocol_for(current_version)

    def _inject(self, ctx: Any, uuid: str, analysis: Any, prompt_version: int) -> bool:
        """单条回注：查 cache 原文 → 已归档则跳过 → 公共后段 → 发 ARCHIVE_REQUESTED。"""
        runtime = self._runtime
        if runtime is None:
            return False
        cache = getattr(ctx, "mongo_db_cache", None)
        if cache is None:
            return False
        record = cache.collection.find_one({"UUID": uuid})
        if not record:
            return False
        record.pop("_id", None)
        informant = str(record.get("informant") or "")
        definition = self._definition(ctx)
        repository = (
            definition.storage.repository(ctx)
            if definition.storage is not None
            else self.ports._require_archive_repository(ctx)
        )
        if repository.contains_source(informant):
            return False
        result = self.ports.prepare_analysis_outcome(
            ctx, record, analysis,
            prompt_version=prompt_version, ai_service=self.base_url,
            ai_model=self.model, intelligence_uuid=uuid,
            informant=informant, persist=True)
        if result.accepted:
            runtime.emit(HubEvent(ARCHIVE_REQUESTED, result.payload, ctx.name))
        return True
