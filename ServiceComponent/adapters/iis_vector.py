"""Event V4 intelligence indexing extension."""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

from ServiceComponent.pipeline import ARCHIVE_COMPLETED
from ServiceComponent.runtime import HubEvent, HubPlugin, HubRuntime
from ServiceComponent.extensions import TRANSLATION_COMPLETED


logger = logging.getLogger(__name__)


class IISVectorExtension(HubPlugin):
    """订阅 IIS 归档事件并异步维护两套向量索引。

    VectorDB 的连接、集合、V4 数据模型都封装在此适配器；Hub runtime
    只看见 ``archive.completed`` 事件。构造函数允许替换工厂，因此没有外部
    服务时也可完整测试生命周期、筛选和检索合并逻辑。
    """

    def __init__(
            self,
            vector_client: Any,
            *,
            default_subsystem: str,
            engine_factory: Callable[[Any], Any],
            record_factory: Callable[[Dict[str, Any]], Any],
            skip_archive_predicate: Optional[Callable[[HubEvent], bool]] = None,
            retry_interval: float = 5.0,
    ):
        if not default_subsystem:
            raise ValueError("default_subsystem 不能为空。")
        if retry_interval <= 0:
            raise ValueError("retry_interval 必须大于 0。")
        self.vector_client = vector_client
        self.default_subsystem = default_subsystem
        self.engine_factory = engine_factory
        self.record_factory = record_factory
        self.skip_archive_predicate = skip_archive_predicate
        self.retry_interval = retry_interval
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._pending: queue.Queue[Dict[str, Any]] = queue.Queue(maxsize=1000)
        self._initializer: Optional[threading.Thread] = None
        self._indexer: Optional[threading.Thread] = None
        self._summary_engine: Any = None
        self._full_text_engine: Any = None

    def register(self, runtime: HubRuntime) -> None:
        runtime.subscribe(ARCHIVE_COMPLETED, self._on_archived)
        runtime.subscribe(TRANSLATION_COMPLETED, self._on_archived)

    def start(self, runtime: HubRuntime) -> None:
        if self.vector_client is None:
            logger.info("Vector extension is not configured; indexing stays disabled.")
            return
        self._initializer = threading.Thread(
            target=self._initialize_forever, name="IISVectorInitializer", daemon=True)
        self._indexer = threading.Thread(
            target=self._index_forever, name="IISVectorIndexer", daemon=True)
        self._initializer.start()
        self._indexer.start()

    def stop(self, runtime: HubRuntime) -> None:
        self._stop.set()
        for thread in (self._initializer, self._indexer):
            if thread and thread.is_alive():
                thread.join(timeout=2.0)

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    @property
    def summary_engine(self) -> Any:
        with self._lock:
            return self._summary_engine

    @property
    def full_text_engine(self) -> Any:
        with self._lock:
            return self._full_text_engine

    def wait_for_index_idle(self, timeout: Optional[float] = None) -> bool:
        """等待已排队归档记录完成索引，供集成测试和受控关闭使用。"""
        done = threading.Event()

        def wait() -> None:
            self._pending.join()
            done.set()

        threading.Thread(target=wait, daemon=True).start()
        return done.wait(timeout)

    def search(self, text: str, *, in_summary: bool = True,
               in_fulltext: bool = False, top_n: int = 10,
               score_threshold: float = 0.5, score_threshold_max: float = 1.0,
               **filters: Any) -> List[Tuple[str, float, dict]]:
        """查询可用索引，并以最高分去重。"""
        with self._lock:
            engines = ((in_summary, self._summary_engine),
                       (in_fulltext, self._full_text_engine))
        records: List[dict] = []
        for enabled, engine in engines:
            if enabled and engine is not None:
                records.extend(engine.query(text, top_n, score_threshold, **filters) or [])
        best: Dict[str, Tuple[float, dict]] = {}
        for record in records:
            identifier = record.get("doc_id")
            score = record.get("score", 0.0)
            if identifier is None or score > score_threshold_max:
                continue
            if identifier not in best or score > best[identifier][0]:
                best[identifier] = (score, record)
        result = [(identifier, score, record) for identifier, (score, record) in best.items()]
        return sorted(result, key=lambda item: item[1], reverse=True)[:top_n]

    def _on_archived(self, event: HubEvent, runtime: HubRuntime) -> None:
        if event.subsystem != self.default_subsystem or not isinstance(event.payload, dict):
            return
        if event.event_type == ARCHIVE_COMPLETED and self.skip_archive_predicate and \
                self.skip_archive_predicate(event):
            return
        try:
            self._pending.put_nowait(dict(event.payload))
        except queue.Full:
            logger.warning("Vector index queue is full; dropping archived record.")

    def _initialize_forever(self) -> None:
        while not self._stop.is_set():
            try:
                self.vector_client.wait_until_ready(timeout=2.0, poll_interval=1.0)
                summary = self.vector_client.create_collection(
                    name="intelligence_summary", chunk_size=256, chunk_overlap=30)
                full_text = self.vector_client.create_collection(
                    name="intelligence_full_text", chunk_size=512, chunk_overlap=50)
                with self._lock:
                    self._summary_engine = self.engine_factory(summary)
                    self._full_text_engine = self.engine_factory(full_text)
                self._ready.set()
                logger.info("Vector extension initialized.")
                return
            except Exception as exc:
                logger.warning("Vector extension initialization failed; retrying: %s", exc)
                self._stop.wait(self.retry_interval)

    def _index_forever(self) -> None:
        while not self._stop.is_set():
            try:
                payload = self._pending.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                if not self._ready.wait(timeout=0.2):
                    self._pending.put(payload)
                    continue
                record = self.record_factory(payload)
                with self._lock:
                    summary, full_text = self._summary_engine, self._full_text_engine
                if summary:
                    summary.upsert(record, data_type="summary")
                if full_text:
                    full_text.upsert(record, data_type="full")
            except Exception:
                logger.exception("Vector indexing failed for archived record.")
            finally:
                self._pending.task_done()
