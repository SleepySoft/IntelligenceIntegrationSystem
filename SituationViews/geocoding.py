"""态势地图的可拆卸地名解析器；使用持久缓存并在后台限速查询。"""

from __future__ import annotations

import json
import logging
import queue
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path


logger = logging.getLogger(__name__)


class CachedNominatimGeocoder:
    """按需解析真实地名；未命中项排队处理，HTTP 请求不阻塞地图接口。"""

    endpoint = "https://nominatim.openstreetmap.org/search"

    def __init__(self, cache_path, *, request_interval=1.05, timeout=12):
        self.cache_path = Path(cache_path)
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.request_interval = max(1.0, float(request_interval))
        self.timeout = float(timeout)
        self._queue = queue.Queue()
        self._pending = set()
        self._lock = threading.Lock()
        self._initialize()
        self._worker = threading.Thread(
            target=self._run, name="situation-geocoder", daemon=True)
        self._worker.start()

    def resolve_many(self, locations):
        resolved = []
        pending = 0
        for item in locations:
            name = str(item.get("name") or "").strip()
            country_code = str(item.get("country_code") or "").strip().upper()
            key = self._key(name, country_code)
            cached = self._read(key)
            if cached is None:
                self._enqueue(key, name, country_code)
                pending += 1
                continue
            if not cached.get("resolved"):
                continue
            resolved.append({**item, **{
                "latitude": cached["latitude"],
                "longitude": cached["longitude"],
                "display_name": cached.get("display_name"),
                "address_type": cached.get("address_type"),
            }})
        return {
            "locations": resolved,
            "pending_count": pending,
            "attribution": "地理坐标 © OpenStreetMap contributors, ODbL",
        }

    def _initialize(self):
        with sqlite3.connect(self.cache_path) as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS geocodes (
                    cache_key TEXT PRIMARY KEY,
                    resolved INTEGER NOT NULL,
                    latitude REAL,
                    longitude REAL,
                    display_name TEXT,
                    address_type TEXT,
                    updated_at REAL NOT NULL
                )
            """)

    @staticmethod
    def _key(name, country_code):
        return f"{country_code}|{' '.join(name.casefold().split())}"

    def _read(self, key):
        with sqlite3.connect(self.cache_path) as connection:
            row = connection.execute(
                "SELECT resolved, latitude, longitude, display_name, address_type "
                "FROM geocodes WHERE cache_key = ?", (key,)).fetchone()
        if row is None:
            return None
        return {
            "resolved": bool(row[0]),
            "latitude": row[1],
            "longitude": row[2],
            "display_name": row[3],
            "address_type": row[4],
        }

    def _enqueue(self, key, name, country_code):
        with self._lock:
            if key in self._pending:
                return
            self._pending.add(key)
        self._queue.put((key, name, country_code))

    def _run(self):
        while True:
            key, name, country_code = self._queue.get()
            started = time.monotonic()
            try:
                result = self._lookup(name, country_code)
                self._write(key, result)
            except Exception as exc:
                # 网络瞬时失败不写负缓存，后续缩放或轮询可以重试。
                logger.warning("地名解析失败 %s/%s: %s", country_code, name, exc)
            finally:
                with self._lock:
                    self._pending.discard(key)
                self._queue.task_done()
                elapsed = time.monotonic() - started
                if elapsed < self.request_interval:
                    time.sleep(self.request_interval - elapsed)

    def _lookup(self, name, country_code):
        params = urllib.parse.urlencode({
            "format": "jsonv2",
            "limit": 1,
            "addressdetails": 1,
            "countrycodes": country_code.lower(),
            "q": name,
        })
        request = urllib.request.Request(
            f"{self.endpoint}?{params}",
            headers={"User-Agent": "IntelligenceIntegrationSystem/1.0 situation-map"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            values = json.loads(response.read().decode("utf-8"))
        if not values:
            return None
        value = values[0]
        # 国家本身不是“更细地点”，不作为放大后的细化标记。
        if str(value.get("addresstype") or "").lower() == "country":
            return None
        return {
            "latitude": float(value["lat"]),
            "longitude": float(value["lon"]),
            "display_name": value.get("display_name"),
            "address_type": value.get("addresstype") or value.get("type"),
        }

    def _write(self, key, result):
        result = result or {}
        with sqlite3.connect(self.cache_path) as connection:
            connection.execute("""
                INSERT INTO geocodes (
                    cache_key, resolved, latitude, longitude,
                    display_name, address_type, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    resolved=excluded.resolved,
                    latitude=excluded.latitude,
                    longitude=excluded.longitude,
                    display_name=excluded.display_name,
                    address_type=excluded.address_type,
                    updated_at=excluded.updated_at
            """, (
                key, int(bool(result)), result.get("latitude"),
                result.get("longitude"), result.get("display_name"),
                result.get("address_type"), time.time(),
            ))
