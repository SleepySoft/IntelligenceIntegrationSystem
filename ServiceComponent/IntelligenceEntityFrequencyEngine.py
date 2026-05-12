import os
import json
import logging
import sqlite3
import datetime
import threading
from typing import Optional, List, Dict, Any

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

class EntityFrequencyEngine:
    """
    实体出现频率统计引擎。

    按小时 time slot 统计 intelligence_archived 中四类实体（地点、地域、人物、组织）的出现次数，
    缓存到 SQLite，支持按时间段查询并合并结果。
    """

    def __init__(
        self,
        db_path: str,
        mongo_db_archive: MongoDBStorage,
        time_field: str = APPENDIX_TIME_ARCHIVED,
    ):
        """
        :param db_path: SQLite 数据库文件路径
        :param mongo_db_archive: MongoDB 归档存储
        :param time_field: 用于划分 time slot 的字段名（默认 APPENDIX_TIME_ARCHIVED）
        """
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
                CREATE TABLE IF NOT EXISTS hourly_entity_stats (
                    time_slot TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_name TEXT NOT NULL,
                    count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (time_slot, entity_type, entity_name)
                );
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_stats_query
                ON hourly_entity_stats(time_slot, entity_type, count DESC);
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_stats_entity
                ON hourly_entity_stats(entity_type, entity_name, time_slot);
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS hourly_placeholder (
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

    def build_hourly_cache(
        self,
        hour_start: datetime.datetime,
        hour_end: datetime.datetime,
    ) -> None:
        """
        扫描 MongoDB，统计 [hour_start, hour_end) 内每个小时各实体出现次数并写入 SQLite。
        实际会按小时拆分为多个 slot 分别统计。
        """
        hour_start = self._normalize_to_hour(hour_start)
        hour_end = self._normalize_to_hour(hour_end)

        if hour_start >= hour_end:
            logger.warning(f"build_hourly_cache: invalid range {hour_start} ~ {hour_end}")
            return

        current = hour_start
        while current < hour_end:
            next_hour = current + datetime.timedelta(hours=1)
            try:
                self._build_single_hour_cache(current)
            except Exception as e:
                logger.error(
                    f"Failed to build cache for slot {current.isoformat()}: {e}",
                    exc_info=True,
                )
            current = next_hour

    def _build_single_hour_cache(self, slot: datetime.datetime) -> None:
        """统计并缓存单个 hour slot 的数据。"""
        slot_str = slot.strftime("%Y-%m-%dT%H:%M:%S")

        with self.write_lock:
            conn = self._get_conn()
            try:
                cursor = conn.cursor()

                # 检查是否已存在占位记录
                cursor.execute(
                    "SELECT 1 FROM hourly_placeholder WHERE time_slot = ?",
                    (slot_str,),
                )
                if cursor.fetchone() is not None:
                    # 已缓存过，跳过
                    return

                # 删除该 slot 可能存在的旧 stats 记录（幂等）
                cursor.execute(
                    "DELETE FROM hourly_entity_stats WHERE time_slot = ?",
                    (slot_str,),
                )

                # 从 MongoDB 聚合统计
                counts = self._aggregate_entities_for_hour(slot)

                # 写入统计结果
                for entity_type, entity_dict in counts.items():
                    for entity_name, count in entity_dict.items():
                        cursor.execute(
                            """
                            INSERT INTO hourly_entity_stats
                            (time_slot, entity_type, entity_name, count)
                            VALUES (?, ?, ?, ?)
                            """,
                            (slot_str, entity_type, entity_name, count),
                        )

                # 插入占位记录
                cursor.execute(
                    """
                    INSERT INTO hourly_placeholder (time_slot, built_at)
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

    def _aggregate_entities_for_hour(
        self, slot: datetime.datetime
    ) -> Dict[str, Dict[str, int]]:
        """
        对单个 hour slot 执行 MongoDB 聚合，返回 {entity_type: {entity_name: count}}。
        """
        slot_start = slot
        slot_end = slot + datetime.timedelta(hours=1)

        results = {
            ENTITY_TYPE_LOCATION: {},
            ENTITY_TYPE_GEOGRAPHY: {},
            ENTITY_TYPE_PEOPLE: {},
            ENTITY_TYPE_ORGANIZATION: {},
        }

        for entity_type in ALL_ENTITY_TYPES:
            field_name = entity_type  # MongoDB 字段名与类型名一致

            if entity_type == ENTITY_TYPE_GEOGRAPHY:
                pipeline = self._build_single_field_pipeline(
                    field_name, slot_start, slot_end
                )
            else:
                pipeline = self._build_array_field_pipeline(
                    field_name, slot_start, slot_end
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
        self,
        field_name: str,
        slot_start: datetime.datetime,
        slot_end: datetime.datetime,
    ) -> List[Dict[str, Any]]:
        """为数组型字段（LOCATION, PEOPLE, ORGANIZATION）构建聚合管道。"""
        return [
            {
                "$match": {
                    f"APPENDIX.{self.time_field}": {
                        "$gte": ensure_timezone_aware(slot_start),
                        "$lt": ensure_timezone_aware(slot_end),
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
        self,
        field_name: str,
        slot_start: datetime.datetime,
        slot_end: datetime.datetime,
    ) -> List[Dict[str, Any]]:
        """为单字符串型字段（GEOGRAPHY）构建聚合管道。"""
        return [
            {
                "$match": {
                    f"APPENDIX.{self.time_field}": {
                        "$gte": ensure_timezone_aware(slot_start),
                        "$lt": ensure_timezone_aware(slot_end),
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
    ) -> None:
        """
        确保 [start_time, end_time) 内所有 hour slot 都已缓存。
        缺失的 slot 会先执行 MongoDB 统计并写入占位（包括实体数为 0 的情况）。
        """
        slot_start = self._normalize_to_hour(start_time)
        slot_end = self._normalize_to_hour(end_time)

        if slot_start >= slot_end:
            return

        # 查询 SQLite 中已存在的 slot
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            start_str = slot_start.strftime("%Y-%m-%dT%H:%M:%S")
            end_str = slot_end.strftime("%Y-%m-%dT%H:%M:%S")
            cursor.execute(
                "SELECT time_slot FROM hourly_placeholder WHERE time_slot >= ? AND time_slot < ?",
                (start_str, end_str),
            )
            existing_slots = {row["time_slot"] for row in cursor.fetchall()}
        finally:
            conn.close()

        current = slot_start
        while current < slot_end:
            slot_str = current.strftime("%Y-%m-%dT%H:%M:%S")
            if slot_str not in existing_slots:
                try:
                    self._build_single_hour_cache(current)
                except Exception as e:
                    logger.error(
                        f"ensure_time_slots: failed to build cache for {slot_str}: {e}",
                        exc_info=True,
                    )
            current += datetime.timedelta(hours=1)

    def query_frequency(
        self,
        entity_type: str,
        start_time: datetime.datetime,
        end_time: datetime.datetime,
        top_n: int = 20,
        bottom_threshold: int = 0,
    ) -> Dict[str, Any]:
        """
        查询某类实体在时间段内的聚合统计。

        :return: {
            "time_slots": ["2026-05-12T08:00:00", ...],
            "top_entities": [{"name": "", "total_count": 0, "trend": [...]}, ...],
            "bottom_entities": [{"name": "", "total_count": 0, "trend": [...]}, ...],
            "summary": {
                "total_mentions": 0,
                "unique_entities": 0,
                "peak_hour": "",
                "peak_hour_count": 0,
            }
        }
        """
        if entity_type not in ALL_ENTITY_TYPES:
            raise ValueError(f"Unsupported entity_type: {entity_type}")

        # 确保缓存完整
        self.ensure_time_slots(start_time, end_time)

        start_str = start_time.strftime("%Y-%m-%dT%H:%M:%S")
        end_str = end_time.strftime("%Y-%m-%dT%H:%M:%S")

        conn = self._get_conn()
        try:
            cursor = conn.cursor()

            # 获取所有 time slot 列表（按小时）
            cursor.execute(
                """
                SELECT DISTINCT time_slot FROM hourly_entity_stats
                WHERE time_slot >= ? AND time_slot < ? AND entity_type = ?
                ORDER BY time_slot
                """,
                (start_str, end_str, entity_type),
            )
            time_slots = [row["time_slot"] for row in cursor.fetchall()]

            if not time_slots:
                return {
                    "time_slots": [],
                    "top_entities": [],
                    "bottom_entities": [],
                    "summary": {
                        "total_mentions": 0,
                        "unique_entities": 0,
                        "peak_hour": "",
                        "peak_hour_count": 0,
                    },
                }

            # 聚合各实体总次数
            cursor.execute(
                """
                SELECT entity_name, SUM(count) as total_count
                FROM hourly_entity_stats
                WHERE time_slot >= ? AND time_slot < ? AND entity_type = ?
                GROUP BY entity_name
                ORDER BY total_count DESC
                """,
                (start_str, end_str, entity_type),
            )
            all_entities = [
                {"name": row["entity_name"], "total_count": row["total_count"]}
                for row in cursor.fetchall()
            ]

            # TOP N
            top_entities_list = all_entities[:top_n]

            # BOTTOM: 高于阈值且不在 TOP N 中的最后若干名
            # 过滤掉 count <= threshold 的，取剩余中 count 最低的前 20 个
            remaining = [e for e in all_entities[top_n:] if e["total_count"] > bottom_threshold]
            bottom_entities_list = remaining[-20:] if len(remaining) > 20 else remaining
            # 按升序排列便于展示
            bottom_entities_list = sorted(
                bottom_entities_list, key=lambda x: x["total_count"]
            )

            # 为 TOP 实体生成趋势数据
            for entity in top_entities_list:
                entity["trend"] = self._get_entity_trend_internal(
                    cursor, entity_type, entity["name"], time_slots
                )

            for entity in bottom_entities_list:
                entity["trend"] = self._get_entity_trend_internal(
                    cursor, entity_type, entity["name"], time_slots
                )

            # 汇总统计
            total_mentions = sum(e["total_count"] for e in all_entities)
            unique_entities = len(all_entities)

            # 最活跃时段
            cursor.execute(
                """
                SELECT time_slot, SUM(count) as slot_total
                FROM hourly_entity_stats
                WHERE time_slot >= ? AND time_slot < ? AND entity_type = ?
                GROUP BY time_slot
                ORDER BY slot_total DESC
                LIMIT 1
                """,
                (start_str, end_str, entity_type),
            )
            row = cursor.fetchone()
            peak_hour = row["time_slot"] if row else ""
            peak_hour_count = row["slot_total"] if row else 0

            return {
                "time_slots": time_slots,
                "top_entities": top_entities_list,
                "bottom_entities": bottom_entities_list,
                "summary": {
                    "total_mentions": total_mentions,
                    "unique_entities": unique_entities,
                    "peak_hour": peak_hour,
                    "peak_hour_count": peak_hour_count,
                },
            }
        finally:
            conn.close()

    def _get_entity_trend_internal(
        self,
        cursor: sqlite3.Cursor,
        entity_type: str,
        entity_name: str,
        time_slots: List[str],
    ) -> List[int]:
        """内部方法：获取单个实体在各 time slot 中的次数序列。"""
        placeholders = ",".join("?" for _ in time_slots)
        cursor.execute(
            f"""
            SELECT time_slot, count FROM hourly_entity_stats
            WHERE time_slot IN ({placeholders})
              AND entity_type = ? AND entity_name = ?
            """,
            (*time_slots, entity_type, entity_name),
        )
        count_map = {row["time_slot"]: row["count"] for row in cursor.fetchall()}
        return [count_map.get(slot, 0) for slot in time_slots]

    def get_entity_trend(
        self,
        entity_type: str,
        entity_name: str,
        start_time: datetime.datetime,
        end_time: datetime.datetime,
    ) -> Dict[str, Any]:
        """
        获取单个实体在时间段内的趋势数据。

        :return: {
            "time_slots": [...],
            "counts": [...],
        }
        """
        self.ensure_time_slots(start_time, end_time)

        start_str = start_time.strftime("%Y-%m-%dT%H:%M:%S")
        end_str = end_time.strftime("%Y-%m-%dT%H:%M:%S")

        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT DISTINCT time_slot FROM hourly_entity_stats
                WHERE time_slot >= ? AND time_slot < ? AND entity_type = ?
                ORDER BY time_slot
                """,
                (start_str, end_str, entity_type),
            )
            time_slots = [row["time_slot"] for row in cursor.fetchall()]

            counts = self._get_entity_trend_internal(
                cursor, entity_type, entity_name, time_slots
            )

            return {
                "time_slots": time_slots,
                "counts": counts,
            }
        finally:
            conn.close()

    # -------------------------------------------------- 工具方法 --------------------------------------------------

    @staticmethod
    def _normalize_to_hour(dt: datetime.datetime) -> datetime.datetime:
        """将时间截断到整点（向下取整到小时）。"""
        return dt.replace(minute=0, second=0, microsecond=0)
