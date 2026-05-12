import os
import json
import logging
import sqlite3
import datetime
import threading
from typing import Optional, List, Dict, Any, Iterator

from Tools.MongoDBAccess import MongoDBStorage
from Tools.DateTimeUtility import ensure_timezone_aware
from ServiceComponent.IntelligenceHubDefines_v2 import APPENDIX_TIME_ARCHIVED

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# 实体类型常量
ENTITY_TYPE_LOCATION = "LOCATION"
ENTITY_TYPE_GEOGRAPHY = "GEOGRAPHY"
ENTITY_TYPE_PEOPLE = "PEOPLE"
ENTITY_TYPE_ORGANIZATION = "ORGANIZATION"

ALL_ENTITY_TYPES = [
    ENTITY_TYPE_LOCATION,
    ENTITY_TYPE_GEOGRAPHY,
    ENTITY_TYPE_PEOPLE,
    ENTITY_TYPE_ORGANIZATION,
]

# 支持的查询粒度
GRANULARITY_DAY = "day"
GRANULARITY_WEEK = "week"
GRANULARITY_MONTH = "month"
ALL_GRANULARITIES = [GRANULARITY_DAY, GRANULARITY_WEEK, GRANULARITY_MONTH]

# 统计字段映射（MongoDB中的字段名）
ENTITY_MONGO_FIELD = {
    ENTITY_TYPE_LOCATION: "LOCATION",
    ENTITY_TYPE_GEOGRAPHY: "GEOGRAPHY",
    ENTITY_TYPE_PEOPLE: "PEOPLE",
    ENTITY_TYPE_ORGANIZATION: "ORGANIZATION",
}


class EntityFrequencyEngine:
    """
    实体出现频率统计引擎。

    按天为最小粒度统计并缓存 intelligence_archived 中四类实体出现次数。
    查询时支持按天/周/月动态聚合。
    """

    def __init__(
        self,
        db_path: str,
        mongo_db_archive: MongoDBStorage,
        time_field: str = APPENDIX_TIME_ARCHIVED,
    ):
        self.db_path = db_path
        self.mongo_db_archive = mongo_db_archive
        self.time_field = time_field
        self.write_lock = threading.Lock()
        self._init_db()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """初始化 SQLite 表结构。"""
        conn = None
        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_entity_stats (
                    time_slot TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_name TEXT NOT NULL,
                    count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (time_slot, entity_type, entity_name)
                );
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_daily_stats_query
                ON daily_entity_stats(time_slot, entity_type, count DESC);
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_daily_stats_entity
                ON daily_entity_stats(entity_type, entity_name, time_slot);
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_placeholder (
                    time_slot TEXT PRIMARY KEY,
                    built_at TEXT NOT NULL
                );
            """)

            conn.commit()
            logger.info(f"EntityFrequencyEngine SQLite initialized: {self.db_path}")
        except Exception as e:
            logger.error(f"Failed to init EntityFrequencyEngine DB: {e}", exc_info=True)
            raise
        finally:
            if conn:
                conn.close()

    # -------------------------------------------------- 缓存构建 --------------------------------------------------

    def build_cache_with_progress(
        self,
        start_time: datetime.datetime,
        end_time: datetime.datetime,
    ) -> Iterator[Dict[str, Any]]:
        """
        生成器：扫描 MongoDB 并按天构建缓存，每完成一天 yield 一次进度。
        """
        slot_start = self._normalize_to_day(start_time)
        slot_end = self._normalize_to_day(end_time)

        if slot_start >= slot_end:
            yield {"done": 0, "total": 0, "current_slot": "", "status": "complete"}
            return

        total_days = (slot_end - slot_start).days
        done = 0
        current = slot_start
        while current < slot_end:
            try:
                self._build_single_day_cache(current)
            except Exception as e:
                logger.error(
                    f"Failed to build cache for slot {current.isoformat()}: {e}",
                    exc_info=True,
                )
            done += 1
            yield {
                "done": done,
                "total": total_days,
                "current_slot": current.strftime("%Y-%m-%d"),
                "status": "running",
            }
            current += datetime.timedelta(days=1)

        yield {
            "done": total_days,
            "total": total_days,
            "current_slot": "",
            "status": "complete",
        }

    def _build_single_day_cache(self, slot: datetime.datetime) -> None:
        """统计并缓存单天的数据。"""
        slot_str = slot.strftime("%Y-%m-%d")

        with self.write_lock:
            conn = self._get_conn()
            try:
                cursor = conn.cursor()

                # 检查是否已存在占位记录
                cursor.execute(
                    "SELECT 1 FROM daily_placeholder WHERE time_slot = ?",
                    (slot_str,),
                )
                if cursor.fetchone() is not None:
                    return

                # 删除该 slot 可能存在的旧 stats 记录（幂等）
                cursor.execute(
                    "DELETE FROM daily_entity_stats WHERE time_slot = ?",
                    (slot_str,),
                )

                # 从 MongoDB 聚合统计
                counts = self._aggregate_entities_for_day(slot)

                # 写入统计结果
                for entity_type, entity_dict in counts.items():
                    for entity_name, count in entity_dict.items():
                        cursor.execute(
                            """
                            INSERT INTO daily_entity_stats
                            (time_slot, entity_type, entity_name, count)
                            VALUES (?, ?, ?, ?)
                            """,
                            (slot_str, entity_type, entity_name, count),
                        )

                # 插入占位记录
                cursor.execute(
                    """
                    INSERT INTO daily_placeholder (time_slot, built_at)
                    VALUES (?, ?)
                    """,
                    (slot_str, datetime.datetime.now(datetime.timezone.utc).isoformat()),
                )

                conn.commit()
                total_entities = sum(len(v) for v in counts.values())
                logger.info(
                    f"EntityFrequencyEngine: cached slot {slot_str}, "
                    f"{total_entities} entities recorded."
                )
            finally:
                conn.close()

    def _aggregate_entities_for_day(
        self, slot: datetime.datetime
    ) -> Dict[str, Dict[str, int]]:
        """对单天执行 MongoDB 聚合。"""
        day_start = slot
        day_end = slot + datetime.timedelta(days=1)

        results = {
            ENTITY_TYPE_LOCATION: {},
            ENTITY_TYPE_GEOGRAPHY: {},
            ENTITY_TYPE_PEOPLE: {},
            ENTITY_TYPE_ORGANIZATION: {},
        }

        for entity_type in ALL_ENTITY_TYPES:
            field_name = ENTITY_MONGO_FIELD[entity_type]

            if entity_type == ENTITY_TYPE_GEOGRAPHY:
                pipeline = self._build_single_field_pipeline(
                    field_name, day_start, day_end
                )
            else:
                pipeline = self._build_array_field_pipeline(
                    field_name, day_start, day_end
                )

            try:
                rows = self.mongo_db_archive.aggregate(pipeline)
                for row in rows:
                    entity_name = str(row.get("_id", "")).strip()
                    count = int(row.get("count", 0))
                    if entity_name and count > 0:
                        results[entity_type][entity_name] = count
            except Exception as e:
                logger.error(
                    f"MongoDB aggregate failed for {entity_type} at {slot.isoformat()}: {e}",
                    exc_info=True,
                )

        return results

    def _build_array_field_pipeline(
        self, field_name: str, day_start: datetime.datetime, day_end: datetime.datetime
    ) -> List[Dict[str, Any]]:
        return [
            {
                "$match": {
                    f"APPENDIX.{self.time_field}": {
                        "$gte": ensure_timezone_aware(day_start),
                        "$lt": ensure_timezone_aware(day_end),
                    }
                }
            },
            {"$project": {"entities": {"$ifNull": [f"${field_name}", []]}}},
            {"$unwind": "$entities"},
            {
                "$match": {
                    "entities": {
                        "$type": "string",
                        "$ne": "",
                    }
                }
            },
            {
                "$group": {
                    "_id": "$entities",
                    "count": {"$sum": 1},
                }
            },
            {"$sort": {"count": -1}},
        ]

    def _build_single_field_pipeline(
        self, field_name: str, day_start: datetime.datetime, day_end: datetime.datetime
    ) -> List[Dict[str, Any]]:
        return [
            {
                "$match": {
                    f"APPENDIX.{self.time_field}": {
                        "$gte": ensure_timezone_aware(day_start),
                        "$lt": ensure_timezone_aware(day_end),
                    }
                }
            },
            {
                "$match": {
                    field_name: {
                        "$type": "string",
                        "$ne": "",
                    }
                }
            },
            {
                "$group": {
                    "_id": f"${field_name}",
                    "count": {"$sum": 1},
                }
            },
            {"$sort": {"count": -1}},
        ]

    # -------------------------------------------------- 查询接口 --------------------------------------------------

    def ensure_time_slots(
        self,
        start_time: datetime.datetime,
        end_time: datetime.datetime,
    ) -> List[str]:
        """
        确保 [start_time, end_time) 内所有天 slot 都已缓存。
        返回缺失并已经补全的 slot 字符串列表（YYYY-MM-DD）。
        """
        slot_start = self._normalize_to_day(start_time)
        slot_end = self._normalize_to_day(end_time)

        if slot_start >= slot_end:
            return []

        # 查询 SQLite 中已存在的 slot
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            start_str = slot_start.strftime("%Y-%m-%d")
            end_str = slot_end.strftime("%Y-%m-%d")
            cursor.execute(
                "SELECT time_slot FROM daily_placeholder WHERE time_slot >= ? AND time_slot < ?",
                (start_str, end_str),
            )
            existing_slots = {row["time_slot"] for row in cursor.fetchall()}
        finally:
            conn.close()

        missing_built = []
        current = slot_start
        while current < slot_end:
            slot_str = current.strftime("%Y-%m-%d")
            if slot_str not in existing_slots:
                try:
                    self._build_single_day_cache(current)
                    missing_built.append(slot_str)
                except Exception as e:
                    logger.error(
                        f"ensure_time_slots: failed to build cache for {slot_str}: {e}",
                        exc_info=True,
                    )
            current += datetime.timedelta(days=1)

        return missing_built

    def query_frequency(
        self,
        entity_type: str,
        start_time: datetime.datetime,
        end_time: datetime.datetime,
        granularity: str = GRANULARITY_DAY,
        top_n: int = 20,
        bottom_threshold: int = 5,
    ) -> Dict[str, Any]:
        """
        查询某类实体在时间段内的聚合统计。
        """
        if entity_type not in ALL_ENTITY_TYPES:
            raise ValueError(f"Unsupported entity_type: {entity_type}")
        if granularity not in ALL_GRANULARITIES:
            raise ValueError(f"Unsupported granularity: {granularity}")

        # 先确保天级缓存完整
        self.ensure_time_slots(start_time, end_time)

        # 读取天级数据
        raw_data = self._fetch_daily_data(entity_type, start_time, end_time)

        # 按粒度合并
        merged = self._merge_by_granularity(raw_data, granularity)

        time_slots = sorted(merged.keys())
        day_span = max((self._normalize_to_day(end_time) - self._normalize_to_day(start_time)).days, 1)
        if not time_slots:
            return {
                "time_slots": [],
                "top_entities": [],
                "bottom_entities": [],
                "summary": {
                    "total_mentions": 0,
                    "unique_entities": 0,
                    "time_span_days": day_span,
                },
            }

        # 聚合各实体总次数
        entity_totals: Dict[str, int] = {}
        entity_trend_map: Dict[str, Dict[str, int]] = {}
        for slot, entities in merged.items():
            for name, count in entities.items():
                entity_totals[name] = entity_totals.get(name, 0) + count
                if name not in entity_trend_map:
                    entity_trend_map[name] = {}
                entity_trend_map[name][slot] = count

        sorted_entities = sorted(
            [{"name": k, "total_count": v} for k, v in entity_totals.items()],
            key=lambda x: x["total_count"],
            reverse=True,
        )

        top_entities_list = sorted_entities[:top_n]
        remaining = [
            e for e in sorted_entities[top_n:] if e["total_count"] > bottom_threshold
        ]
        bottom_entities_list = remaining[-20:] if len(remaining) > 20 else remaining
        bottom_entities_list = sorted(
            bottom_entities_list, key=lambda x: x["total_count"]
        )

        # 为 TOP 实体附加趋势数据（按 time_slots 顺序填充）
        for entity in top_entities_list:
            trend_map = entity_trend_map.get(entity["name"], {})
            entity["trend"] = [trend_map.get(slot, 0) for slot in time_slots]

        for entity in bottom_entities_list:
            trend_map = entity_trend_map.get(entity["name"], {})
            entity["trend"] = [trend_map.get(slot, 0) for slot in time_slots]

        total_mentions = sum(entity_totals.values())
        unique_entities = len(entity_totals)

        return {
            "time_slots": time_slots,
            "top_entities": top_entities_list,
            "bottom_entities": bottom_entities_list,
            "summary": {
                "total_mentions": total_mentions,
                "unique_entities": unique_entities,
                "time_span_days": max(day_span, 1),
            },
        }

    def _fetch_daily_data(
        self,
        entity_type: str,
        start_time: datetime.datetime,
        end_time: datetime.datetime,
    ) -> Dict[str, Dict[str, int]]:
        """从 SQLite 读取天级原始数据，返回 {time_slot: {entity_name: count}}。"""
        start_str = start_time.strftime("%Y-%m-%d")
        end_str = end_time.strftime("%Y-%m-%d")

        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT time_slot, entity_name, count
                FROM daily_entity_stats
                WHERE time_slot >= ? AND time_slot < ? AND entity_type = ?
                ORDER BY time_slot
                """,
                (start_str, end_str, entity_type),
            )
            result: Dict[str, Dict[str, int]] = {}
            for row in cursor.fetchall():
                slot = row["time_slot"]
                if slot not in result:
                    result[slot] = {}
                result[slot][row["entity_name"]] = row["count"]
            return result
        finally:
            conn.close()

    def _merge_by_granularity(
        self,
        daily_data: Dict[str, Dict[str, int]],
        granularity: str,
    ) -> Dict[str, Dict[str, int]]:
        """将天级数据按周或月合并。"""
        if granularity == GRANULARITY_DAY:
            return daily_data

        merged: Dict[str, Dict[str, int]] = {}
        for slot_str, entities in daily_data.items():
            slot_date = datetime.datetime.strptime(slot_str, "%Y-%m-%d").date()

            if granularity == GRANULARITY_WEEK:
                # 使用 ISO 周：该周周一
                monday = slot_date - datetime.timedelta(days=slot_date.weekday())
                key = monday.strftime("%Y-%m-%d")
            elif granularity == GRANULARITY_MONTH:
                key = slot_date.strftime("%Y-%m")
            else:
                key = slot_str

            if key not in merged:
                merged[key] = {}
            for name, count in entities.items():
                merged[key][name] = merged[key].get(name, 0) + count

        return merged

    def get_entity_trend(
        self,
        entity_type: str,
        entity_name: str,
        start_time: datetime.datetime,
        end_time: datetime.datetime,
        granularity: str = GRANULARITY_DAY,
    ) -> Dict[str, Any]:
        """
        获取单个实体在时间段内的趋势数据。
        """
        if entity_type not in ALL_ENTITY_TYPES:
            raise ValueError(f"Unsupported entity_type: {entity_type}")
        if granularity not in ALL_GRANULARITIES:
            raise ValueError(f"Unsupported granularity: {granularity}")

        self.ensure_time_slots(start_time, end_time)

        raw_data = self._fetch_daily_data(entity_type, start_time, end_time)
        merged = self._merge_by_granularity(raw_data, granularity)
        time_slots = sorted(merged.keys())

        counts = [merged.get(slot, {}).get(entity_name, 0) for slot in time_slots]
        return {
            "time_slots": time_slots,
            "counts": counts,
        }

    # -------------------------------------------------- 工具方法 --------------------------------------------------

    @staticmethod
    def _normalize_to_day(dt: datetime.datetime) -> datetime.datetime:
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)
