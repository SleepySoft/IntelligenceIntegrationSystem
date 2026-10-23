# EntityAlias/wikidata.py
# Wikidata 在线建议源：本地查不到时在线检索，AI 确认后补录回本地。
#
# 工程约束（Wikimedia API 政策，2026 年现状）：
# - 必须带描述性 User-Agent（工具名 + 联系方式），缺失会被直接拒绝
# - 未识别访问 ~10 次/分钟；合规 UA ~200 次/分钟；429 时按 Retry-After 退避
# - 大批量读取应走 dump；本模块面向“注册时少量调用 + 缓存”场景
#
# transport 可注入（测试用）：Callable[[url, params], dict]

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any, Callable, Dict, List, Mapping, Optional

from EntityAlias.normalize import normalize_name

logger = logging.getLogger(__name__)

API_URL = "https://www.wikidata.org/w/api.php"
DEFAULT_UA = "IIS-EntityAlias/1.0 (OSINT entity alias registry; contact: local-admin)"


def proxy_uses_socks(proxy: Any) -> bool:
    """兼容 requests 的字符串代理和 ``{scheme: url}`` 代理配置。"""
    values = proxy.values() if isinstance(proxy, Mapping) else (proxy,)
    return any(str(value or "").lower().startswith("socks") for value in values)


class WikidataSuggester:
    """Wikidata 检索建议器：节流 + 429 退避 + SQLite 结果缓存 + 可选代理。"""

    def __init__(self, *, user_agent: str = DEFAULT_UA,
                 proxy: Any = "", min_interval: float = 0.5,
                 timeout: float = 10.0, cache_path: str = "",
                 cache_ttl_sec: float = 7 * 24 * 3600,
                 langs: tuple = ("zh", "en"),
                 transport: Callable[[str, Dict[str, Any]], dict] = None):
        self.user_agent = user_agent
        self.proxy = proxy
        self.min_interval = min_interval
        self.timeout = timeout
        self.langs = langs
        self._transport = transport or self._http_get_json
        self._lock = threading.Lock()
        self._last_call = 0.0
        self._cache_ttl = cache_ttl_sec
        self._cache: Optional[sqlite3.Connection] = None
        if cache_path:
            os.makedirs(os.path.dirname(os.path.abspath(cache_path)), exist_ok=True)
            self._cache = sqlite3.connect(cache_path, check_same_thread=False)
            self._cache.execute(
                "CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, "
                "payload TEXT, created_at REAL)")

    # ------------------------------------------------------------------ 公开 API

    def suggest(self, name: str, *, lang: str = "zh", limit: int = 5) -> List[Dict[str, Any]]:
        """按名字检索候选实体。网络/限流失败返回 []（静默降级）。"""
        try:
            data = self._get_json(API_URL, {
                "action": "wbsearchentities", "format": "json",
                "search": name, "language": lang, "uselang": lang,
                "limit": max(1, min(int(limit), 50)),
            })
        except Exception as exc:
            logger.warning("Wikidata suggest 失败（%r）：%s", name, exc)
            return []
        results = []
        for item in data.get("search", []):
            results.append({
                "qid": item.get("id", ""),
                "label": item.get("label", ""),
                "lang": lang,
                "description": item.get("description", ""),
                "matched": (item.get("match") or {}).get("text", ""),
                "source": "wikidata",
            })
        return results

    def prefill(self, qid: str, *, primary_lang: str = "zh") -> Optional[Dict[str, Any]]:
        """拉取实体的多语言 label/别名/简介，预填注册表单（create_entity 参数形状）。"""
        try:
            data = self._get_json(API_URL, {
                "action": "wbgetentities", "format": "json",
                "ids": qid, "props": "labels|aliases|descriptions",
                "languages": "|".join(self.langs),
            })
        except Exception as exc:
            logger.warning("Wikidata prefill 失败（%s）：%s", qid, exc)
            return None
        entity = (data.get("entities") or {}).get(qid)
        if not entity:
            return None

        labels = {lang: v["value"] for lang, v in (entity.get("labels") or {}).items()}
        descriptions = {lang: v["value"]
                        for lang, v in (entity.get("descriptions") or {}).items()}
        alias_groups = {lang: [a["value"] for a in items]
                        for lang, items in (entity.get("aliases") or {}).items()}

        canonical = labels.get(primary_lang) or labels.get("en") \
            or next(iter(labels.values()), "")
        if not canonical:
            return None
        canonical_lang = primary_lang if primary_lang in labels else (
            "en" if "en" in labels else next(iter(labels), ""))

        localized = {lang: label for lang, label in labels.items()
                     if lang != canonical_lang}
        # Wikidata 不同语言组中可能出现同名别名，也可能与 label 重复。
        # 本地索引要求归一化后全局唯一，因此预填时按首次出现去重。
        seen = {normalize_name(canonical)}
        seen.update(normalize_name(value) for value in localized.values())
        aliases = []
        for lang, names in alias_groups.items():
            for name in names:
                key = normalize_name(name)
                if not key or key in seen:
                    continue
                seen.add(key)
                aliases.append({"name": name, "lang": lang})

        return {
            "canonical": canonical,
            "lang": canonical_lang,
            "localized": localized,
            "aliases": aliases,
            "description": descriptions.get(primary_lang)
                           or descriptions.get("en", ""),
            "refs": {"wikidata": qid},
        }

    # ------------------------------------------------------------------ 内部

    def _get_json(self, url: str, params: Dict[str, Any]) -> dict:
        key = url + "?" + json.dumps(params, sort_keys=True)
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        with self._lock:
            delay = self.min_interval - (time.monotonic() - self._last_call)
            if delay > 0:
                time.sleep(delay)
            self._last_call = time.monotonic()
        data = self._request_with_backoff(url, params)
        self._cache_put(key, data)
        return data

    def _request_with_backoff(self, url: str, params: Dict[str, Any]) -> dict:
        last_exc: Optional[Exception] = None
        for attempt in range(3):
            try:
                return self._transport(url, params)
            except _RateLimited as exc:
                logger.warning("Wikidata 限流，%.0fs 后重试（%d/2）",
                               exc.retry_after, attempt + 1)
                time.sleep(exc.retry_after)
                last_exc = exc
        raise last_exc

    def _http_get_json(self, url: str, params: Dict[str, Any]) -> dict:
        import requests
        if isinstance(self.proxy, Mapping):
            proxies = dict(self.proxy)
        else:
            proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
        resp = requests.get(
            url, params=params, timeout=self.timeout, proxies=proxies,
            headers={"User-Agent": self.user_agent,
                     "Accept-Encoding": "gzip, deflate"})
        if resp.status_code == 429:
            retry_after = float(resp.headers.get("Retry-After", 30))
            raise _RateLimited(retry_after)
        resp.raise_for_status()
        return resp.json()

    def _cache_get(self, key: str) -> Optional[dict]:
        if self._cache is None:
            return None
        with self._lock:
            row = self._cache.execute(
                "SELECT payload, created_at FROM cache WHERE key = ?", (key,)
            ).fetchone()
        if not row:
            return None
        if time.time() - row[1] > self._cache_ttl:
            return None
        try:
            return json.loads(row[0])
        except Exception:
            return None

    def _cache_put(self, key: str, payload: dict) -> None:
        if self._cache is None:
            return
        try:
            with self._lock:
                self._cache.execute(
                    "INSERT OR REPLACE INTO cache (key, payload, created_at) "
                    "VALUES (?, ?, ?)",
                    (key, json.dumps(payload, ensure_ascii=False), time.time()))
                self._cache.commit()
        except Exception:
            logger.warning("Wikidata 缓存写入失败", exc_info=True)


class _RateLimited(Exception):
    def __init__(self, retry_after: float):
        super().__init__(f"rate limited, retry after {retry_after}s")
        self.retry_after = retry_after
