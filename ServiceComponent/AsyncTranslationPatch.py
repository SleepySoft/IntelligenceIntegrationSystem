# ServiceComponent/AsyncTranslationPatch.py

import re
import html
import json
import time
import queue
import logging
import pymongo
import datetime
import itertools
import threading
from copy import deepcopy
from dataclasses import dataclass
from typing import Optional, Callable, Dict, Any, Tuple, List

from AIClientCenter.core.manager import AIClientManager

logger = logging.getLogger(__name__)

DEFAULT_TRANSLATED_REV = "tr_patch_20260311"

_CJK_RE = re.compile(r'[\u4e00-\u9fff]')
_ASCII_RE = re.compile(r'[A-Za-z]')

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_JSON_OBJ_RE = re.compile(r"(\{.*\})", re.DOTALL)

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_ANALYSIS_BLOCK_RE = re.compile(r"<analysis>.*?</analysis>", re.DOTALL | re.IGNORECASE)

TRANSLATION_SYSTEM_PROMPT = (
    "你是一个严格的翻译器。只做翻译，不要总结/扩写/改写事实。"
    "不要输出任何推理过程（例如 <think>...</think> 或 <analysis>...</analysis>）。"
    "不要输出 Markdown。"
    "输出必须是严格 JSON，且只包含三个字段：title, brief, text。"
)

def strip_think_tags(text: str) -> str:
    if not text:
        return ""
    # 1) 反转义：&lt;think&gt; -> <think>
    s = html.unescape(text)

    # 2) 删除 think/analysis 块
    s = _THINK_BLOCK_RE.sub("", s)
    s = _ANALYSIS_BLOCK_RE.sub("", s)

    return s.strip()

def _extract_json_obj(text: str) -> Optional[dict]:
    """
    Best-effort JSON extraction:
    1) if whole text is JSON => loads
    2) if fenced ```json {...} ``` => loads inner
    3) if contains first {...} => loads that slice
    """
    if not text:
        return None
    s = text.strip()

    # 1) direct json
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    # 2) fenced json
    m = _JSON_FENCE_RE.search(s)
    if m:
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass

    # 3) first {...} span (greedy, then trim by last brace)
    m = _JSON_OBJ_RE.search(s)
    if m:
        raw = m.group(1)
        # try trimming to last }
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            raw2 = raw[start:end + 1]
            try:
                obj = json.loads(raw2)
                if isinstance(obj, dict):
                    return obj
            except Exception:
                return None

    return None

def _safe_str(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        # 避免把结构直接 str() 变成不可控内容
        return json.dumps(v, ensure_ascii=False)
    return str(v)

def _get_content_from_chat_response(resp: Dict[str, Any]) -> str:
    """
    Extract OpenAI-like content: resp['choices'][0]['message']['content'].
    If missing, return empty.
    """
    try:
        choices = resp.get("choices", [])
        if not choices:
            return ""
        msg = choices[0].get("message", {})
        return _safe_str(msg.get("content", "")).strip()
    except Exception:
        return ""

def _is_retryable_ai_error(resp: Dict[str, Any]) -> bool:
    """
    Decide whether we should retry based on BaseAIClient.chat() error mapping.
    Your BaseAIClient returns:
      {'error': 'unified_api_error', 'error_type': 'fatal|recoverable', 'api_error_code': 'HTTP_400', ...}
    """
    if not isinstance(resp, dict) or "error" not in resp:
        return False
    # fatal => don't retry
    if resp.get("error_type") == "fatal":
        return False
    # HTTP_400 => don't retry (prompt/content issue)
    if resp.get("api_error_code") == "HTTP_400":
        return False
    return True


def _ratio(pat, s: str) -> float:
    if not s:
        return 0.0
    return len(pat.findall(s)) / max(1, len(s))

def looks_non_zh(s: Optional[str], cjk_th=0.18, ascii_th=0.12) -> bool:
    """
    Heuristic, fast and local.
    """
    if not s:
        return False
    t = str(s).strip()
    if len(t) < 12:
        return (_ratio(_CJK_RE, t) < 0.05) and (_ratio(_ASCII_RE, t) > 0.2)
    return (_ratio(_CJK_RE, t) < cjk_th) and (_ratio(_ASCII_RE, t) > ascii_th)

def needs_translation(doc: Dict[str, Any]) -> bool:
    """
    Decide if any of title/brief/text looks non-zh.
    """
    message = (doc.get("analysis") or {}).get("message") or {}
    for k in ("title", "brief", "text"):
        v = message.get(k)
        if v and looks_non_zh(v):
            return True
    return False


@dataclass(frozen=True)
class TranslationTask:
    uuid: str
    priority: int   # 0=new, 1=backfill
    reason: str = ""


class AsyncTranslationPatch:
    """
    Unified pipeline for BOTH new and historical data:
      - Everything is INSERTED first (archived)
      - Then an async patch updates analysis.message + translation_revision

    Features:
      - Priority queue (new items first)
      - Dedupe inflight UUIDs
      - Safe updates: $set only, never change UUID/_id/archived time
      - Optional backfill producer thread scanning newest->older
      - Callback on_patched(doc) to re-vectorize
    """

    translation_group_id = "translate_small"
    translation_user_name = "AsyncTranslationPatch"

    def __init__(self,
                 mongo_db_archive: Any,
                 query_engine: Any,
                 ai_client_manager: AIClientManager,
                 shutdown_flag: threading.Event,
                 on_patched: Optional[Callable[[Dict[str, Any]], None]] = None,
                 translated_revision: str = DEFAULT_TRANSLATED_REV,
                 max_queue: int = 4000,
                 backfill_enabled: bool = True,
                 backfill_scan_limit_per_round: int = 200,
                 backfill_interval_sec: int = 3600,
                 ):
        self.mongo_db_archive = mongo_db_archive
        self.query_engine = query_engine
        self.ai_client_manager = ai_client_manager
        self.shutdown_flag = shutdown_flag
        self.on_patched = on_patched

        self.rev = translated_revision

        self._pq = queue.PriorityQueue(maxsize=max_queue)
        self._seq = itertools.count()
        self._inflight = set()
        self._lock = threading.Lock()

        self._worker_thread = threading.Thread(
            name="AsyncTranslationPatch-Worker",
            target=self._worker_loop,
            daemon=True
        )

        self._backfill_enabled = backfill_enabled
        self._backfill_scan_limit = backfill_scan_limit_per_round
        self._backfill_interval = backfill_interval_sec
        self._backfill_thread = threading.Thread(
            name="AsyncTranslationPatch-Backfill",
            target=self._backfill_loop,
            daemon=True
        )

    def start(self):
        self._worker_thread.start()
        if self._backfill_enabled:
            self._backfill_thread.start()
        logger.info("AsyncTranslationPatch started. backfill=%s", self._backfill_enabled)

    # -------------------- enqueue API --------------------

    def enqueue_new(self, uuid: str, reason: str = ""):
        self._enqueue(uuid, priority=0, reason=reason)

    def enqueue_backfill(self, uuid: str, reason: str = ""):
        self._enqueue(uuid, priority=1, reason=reason)

    def _enqueue(self, uuid: str, priority: int, reason: str):
        if not uuid:
            return
        u = str(uuid).strip().lower()

        with self._lock:
            if u in self._inflight:
                return
            self._inflight.add(u)

        try:
            seq = next(self._seq)
            self._pq.put_nowait((priority, seq, TranslationTask(uuid=u, priority=priority, reason=reason)))
        except queue.Full:
            with self._lock:
                self._inflight.discard(u)
            logger.warning("AsyncTranslationPatch queue full, dropped uuid=%s", u)

    # -------------------- worker --------------------

    def _worker_loop(self):
        logger.info("AsyncTranslationPatch worker loop started.")
        while not self.shutdown_flag.is_set():
            try:
                try:
                    _, _, task = self._pq.get(block=True, timeout=1.0)
                except queue.Empty:
                    continue

                try:
                    self._process_task(task)
                except Exception as e:
                    logger.warning("AsyncTranslationPatch task failed uuid=%s err=%s", task.uuid, e, exc_info=True)
                finally:
                    with self._lock:
                        self._inflight.discard(task.uuid)
                    self._pq.task_done()

            except Exception as outer:
                logger.error("AsyncTranslationPatch outer error: %s", outer, exc_info=True)
                time.sleep(0.5)

        logger.info("AsyncTranslationPatch worker stopped.")

    def _process_task(self, task: TranslationTask):
        # 1) Pull latest doc from archive
        doc = self.query_engine.get_intelligence(task.uuid, light_weight=False)
        if not doc:
            return
        if isinstance(doc, list):
            doc = doc[0] if doc else None
        if not doc:
            return

        # 2) Skip if already translated
        if doc.get("translation_revision"):
            return

        # 3) Check if needs translation
        if not needs_translation(doc):
            # 原生中文：不打标记（你要求：没有字段=原生）
            return

        # 4) Translate (placeholder)
        new_title, new_brief, new_text = self._translate_via_ai(doc)
        if not (new_title or new_brief or new_text):
            # fail silently as patch behavior, or log minimal warning
            logger.warning("AsyncTranslationPatch translate returned empty uuid=%s", task.uuid)
            return

        # 5) Update with $set only: 3 fields + revision flag
        update_data = {}
        if new_title: update_data["analysis.message.title"] = new_title
        if new_brief: update_data["analysis.message.brief"] = new_brief
        if new_text:  update_data["analysis.message.text"] = new_text

        trans_revision = f"{self.rev}@{datetime.datetime.now().strftime('%Y%m%d')}"

        # dot-path key is OK; MongoDBStorage.update will wrap to $set if needed
        update_data["translation_revision"] = trans_revision
        collection = getattr(self.mongo_db_archive, "collection", self.mongo_db_archive)
        collection.update_one({"_id": task.uuid}, {"$set": update_data})

        # 6) Re-vectorize with patched content (best-effort)
        if self.on_patched:
            patched_doc = deepcopy(doc)
            message = patched_doc["analysis"]["message"]
            if new_title: message["title"] = new_title
            if new_brief: message["brief"] = new_brief
            if new_text: message["text"] = new_text
            patched_doc["translation_revision"] = trans_revision
            self.on_patched(patched_doc)

        logger.info("AsyncTranslationPatch updated uuid=%s reason=%s", task.uuid, task.reason)


    def _translate_via_ai(self, doc: Dict[str, Any]) -> Tuple[str, str, str]:
        """
        Translate Event V4 message fields into zh-CN via a targeted AI client pool.

        Returns:
            (title_zh, brief_zh, text_zh)
            Empty string means "keep original".
        """
        # ---------- Build messages ----------
        message = (doc.get("analysis") or {}).get("message") or {}
        user_prompt = (
            "请将以下内容翻译为简体中文：\n"
            f"title: {message.get('title', '')}\n"
            f"brief: {message.get('brief', '')}\n"
            f"text: {message.get('text', '')}\n"
            "\n要求：输出严格 JSON，key 为 title, brief, text。"
        )
        messages = [
            {"role": "system", "content": TRANSLATION_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ]

        # ---------- Acquire client (target translation pool) ----------
        # 这里使用 group_id + allow_private 来确保只拿“小模型翻译池”
        max_wait_sec = 20
        waited = 0
        client = None
        while waited < max_wait_sec and not self.shutdown_flag.is_set():
            client = self.ai_client_manager.get_available_client(
                user_name=self.translation_user_name,
                target_group_id=getattr(self, "translation_group_id", "translate_small"),
                allow_private=True
            )
            if client:
                break
            time.sleep(1)
            waited += 1

        if not client:
            logger.warning("No available translation client (waited %ss).", max_wait_sec)
            return "", "", ""

        # ---------- Call chat with retries (lightweight) ----------
        # 注意：BaseAIClient.chat() 内部已有 API_CORE 重试；这里再做“换client/短重试”即可
        try:
            attempts = 2
            last_err = None
            for i in range(attempts):
                if self.shutdown_flag.is_set():
                    return "", "", ""

                resp = client.chat(
                    messages=messages,
                    temperature=0.2,
                    max_tokens=1500
                )

                # 1) chat-level error
                if isinstance(resp, dict) and "error" in resp:
                    last_err = resp
                    # fatal / http400 => stop
                    if not _is_retryable_ai_error(resp):
                        logger.warning(
                            "Translate AI fatal/non-retryable error. client=%s code=%s msg=%s",
                            getattr(client, "name", "?"),
                            resp.get("api_error_code", ""),
                            _safe_str(resp.get("message", ""))[:120]
                        )
                        return "", "", ""
                    # retryable => short sleep then retry (same client);你也可以 request_change=True 换一个
                    time.sleep(0.6 + 0.4 * i)
                    continue

                # 2) Validate response structure using built-in validator if available
                if hasattr(client, "validate_response"):
                    err_reason = client.validate_response(resp)
                    if err_reason:
                        # 业务结构错误：可以 complain，然后重试一次
                        try:
                            client.complain_error(err_reason)
                        except Exception:
                            pass
                        last_err = {"error": "invalid_response", "message": err_reason}
                        time.sleep(0.6 + 0.4 * i)
                        continue

                content = _get_content_from_chat_response(resp)
                content = strip_think_tags(content)
                if not content:
                    last_err = {"error": "empty_content"}
                    time.sleep(0.6 + 0.4 * i)
                    continue

                # 3) Parse JSON
                obj = _extract_json_obj(content)
                if not obj:
                    # 如果模型没按要求输出 JSON，这是常见失败点
                    # complain_error 可增加 client 的 error_count，帮助 manager 规避坏 client
                    try:
                        if hasattr(client, "complain_error"):
                            client.complain_error("translation_output_not_json")
                    except Exception:
                        pass
                    last_err = {"error": "json_parse_failed", "sample": content[:180]}
                    time.sleep(0.6 + 0.4 * i)
                    continue

                # 4) Extract fields and sanity-check
                title_zh = _safe_str(obj.get("title", "")).strip()
                brief_zh = _safe_str(obj.get("brief", "")).strip()
                text_zh  = _safe_str(obj.get("text", "")).strip()

                # 必须至少有一个字段有效，否则认为失败（避免模型返回空 JSON）
                if not (title_zh or brief_zh or text_zh):
                    last_err = {"error": "json_all_empty"}
                    time.sleep(0.6 + 0.4 * i)
                    continue

                return title_zh, brief_zh, text_zh

            # all attempts failed
            if last_err:
                logger.warning(
                    "Translate AI failed after %d attempts. client=%s err=%s",
                    attempts, getattr(client, "name", "?"), _safe_str(last_err)[:200]
                )
            return "", "", ""

        finally:
            # ---------- Release client ----------
            try:
                self.ai_client_manager.release_client(client)
            except Exception:
                pass

    # -------------------- backfill producer --------------------

    def _backfill_loop(self):
        """
        Scan from newest to older, find docs missing revision flag,
        and enqueue them as low-priority tasks.
        """
        logger.info("AsyncTranslationPatch backfill loop started.")
        while not self.shutdown_flag.is_set():
            try:
                self._scan_and_enqueue_backfill(self._backfill_scan_limit)
            except Exception as e:
                logger.warning("AsyncTranslationPatch backfill scan failed: %s", e, exc_info=True)

            # sleep in small steps to respond to shutdown quickly
            for _ in range(max(1, self._backfill_interval // 2)):
                if self.shutdown_flag.is_set():
                    break
                time.sleep(2)

        logger.info("AsyncTranslationPatch backfill loop stopped.")

    def _scan_and_enqueue_backfill(self, limit: int):
        collection = getattr(self.mongo_db_archive, "collection", self.mongo_db_archive)
        if collection is None:
            return

        # newest -> older by the native V4 archive timestamp
        cursor = collection.find(
            {"translation_revision": {"$exists": False}},
            projection={"_id": 1, "analysis.message": 1}
        ).sort("archived_at", pymongo.DESCENDING).limit(limit)

        count = 0
        for doc in cursor:
            if self.shutdown_flag.is_set():
                break
            uuid = str(doc.get("_id", "")).strip().lower()
            if not uuid:
                continue
            if needs_translation(doc):
                self.enqueue_backfill(uuid, reason="backfill_recent")
                count += 1

        if count:
            logger.info("AsyncTranslationPatch backfill enqueued %d tasks", count)
