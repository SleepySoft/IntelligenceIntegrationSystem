import datetime
from urllib.parse import urlparse
from typing import Any, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from Tools.DateTimeUtility import ensure_timezone_aware
from ServiceComponent.intelligence_contracts import (
    APPENDIX_TIME_ARCHIVED,
    APPENDIX_TIME_GOT,
    APPENDIX_TOTAL_SCORE,
    CACHE_ARCHIVED_FLAG_FIELD,
    CACHE_LEGACY_ARCHIVED_FLAG_FIELD,
    CacheArchiveState,
    NON_INTELLIGENCE_CONTENT_DEFECT_REASONS,
    NonIntelligenceReason,
    appendix_path,
)

ARCHIVE_TIME_FIELD = appendix_path(APPENDIX_TIME_ARCHIVED)
SUBMISSION_TIME_FIELD = appendix_path(APPENDIX_TIME_GOT)
TOTAL_SCORE_FIELD = appendix_path(APPENDIX_TOTAL_SCORE)

class IntelligenceStatisticsEngine:
    def __init__(self, db: Any, *, cache_collection: Any = None,
                 low_value_collection: Any = None,
                 intake_rejected_collection: Any = None):
        # PyMongo Collection 会通过 __getattr__ 把任意属性解释成子集合；
        # getattr(collection, "collection") 因此不会报错，而会悄悄查询
        # `<原集合>.collection`，导致所有统计为空。优先按集合协议识别。
        if callable(getattr(db, "aggregate", None)) and callable(
                getattr(db, "count_documents", None)):
            self.__collection = db
        else:
            self.__collection = db.collection
        self.__cache_collection = self._as_collection(cache_collection)
        self.__low_value_collection = self._as_collection(low_value_collection)
        self.__intake_rejected_collection = self._as_collection(intake_rejected_collection)
        try:
            from tzlocal import get_localzone_name
            self.__local_timezone = get_localzone_name()
            print(f"StatisticsEngine: Using local timezone: {self.__local_timezone}")
        except Exception as e:
            print(f"Warning: Could not determine local timezone. Falling back to UTC. Error: {e}")
            self.__local_timezone = "UTC"

    def get_score_distribution(self, start_time: datetime.datetime, end_time: datetime.datetime):
        """
        API endpoint to get score distribution within a specified time range
        Expected query parameters:
        - start_time: ISO format start timestamp (e.g., '2024-01-01T00:00:00Z')
        - end_time: ISO format end timestamp (e.g., '2024-12-31T23:59:59Z')
        """
        # try:

        # MongoDB aggregation pipeline for score distribution[4,9](@ref)
        pipeline = [
            {
                "$match": {
                    ARCHIVE_TIME_FIELD: {
                        "$gte": ensure_timezone_aware(start_time),
                        "$lte": ensure_timezone_aware(end_time)
                    },
                    TOTAL_SCORE_FIELD: {
                        "$gte": 1,
                        "$lte": 10
                    }
                }
            },
            {
                "$group": {
                    "_id": f"${TOTAL_SCORE_FIELD}",
                    "count": {"$sum": 1}
                }
            },
            {
                "$sort": {"_id": 1}
            }
        ]

        # Execute aggregation query[4](@ref)
        results = self.__collection.aggregate(pipeline)

        # Format results for frontend
        score_distribution = {str(i): 0 for i in range(1, 11)}

        for result in results:
            # 评分引擎输出一位小数；旧实现只接收字符串 "1".."10"，
            # 因而 4.4 之类的正常分数全部被静默丢弃。这里按最近整数分桶。
            try:
                score = max(1, min(10, int(float(result['_id']) + 0.5)))
            except (TypeError, ValueError):
                continue
            score_distribution[str(score)] += result['count']

        return score_distribution

    def get_hourly_stats(self, start_time: datetime.datetime, end_time: datetime.datetime) -> list:
        """Get record counts grouped by hour for the specified time range"""
        # MongoDB aggregation pipeline for hourly statistics

        date_in_local_tz = {
            "date": f"${ARCHIVE_TIME_FIELD}",
            "timezone": self.__local_timezone  # <-- 在这里使用本地时区
        }

        pipeline = [
            {
                "$match": {
                    ARCHIVE_TIME_FIELD: {
                        "$gte": ensure_timezone_aware(start_time),
                        "$lte": ensure_timezone_aware(end_time)
                    }
                }
            },
            {
                "$group": {
                    "_id": {
                        "year": {"$year": date_in_local_tz},
                        "month": {"$month": date_in_local_tz},
                        "day": {"$dayOfMonth": date_in_local_tz},
                        "hour": {"$hour": date_in_local_tz}
                    },
                    "count": {"$sum": 1}
                }
            },
            {
                "$sort": {
                    "_id.year": 1,
                    "_id.month": 1,
                    "_id.day": 1,
                    "_id.hour": 1
                }
            }
        ]

        return list(self.__collection.aggregate(pipeline))

    def get_daily_stats(self, start_time: datetime.datetime, end_time: datetime.datetime):
        """Get record counts grouped by day for the specified time range"""
        # MongoDB aggregation pipeline for daily statistics

        date_in_local_tz = {
            "date": f"${ARCHIVE_TIME_FIELD}",
            "timezone": self.__local_timezone
        }

        pipeline = [
            {
                "$match": {
                    ARCHIVE_TIME_FIELD: {
                        "$gte": ensure_timezone_aware(start_time),
                        "$lte": ensure_timezone_aware(end_time)
                    }
                }
            },
            {
                "$group": {
                    "_id": {
                        "year": {"$year": date_in_local_tz},
                        "month": {"$month": date_in_local_tz},
                        "day": {"$dayOfMonth": date_in_local_tz}
                    },
                    "count": {"$sum": 1}
                }
            },
            {
                "$sort": {
                    "_id.year": 1,
                    "_id.month": 1,
                    "_id.day": 1
                }
            }
        ]

        return list(self.__collection.aggregate(pipeline))

        # return jsonify(result)

    def get_weekly_stats(self, start_time: datetime.datetime, end_time: datetime.datetime):
        """Get record counts grouped by week for the specified time range"""
        # MongoDB aggregation pipeline for weekly statistics

        date_in_local_tz = {
            "date": f"${ARCHIVE_TIME_FIELD}",
            "timezone": self.__local_timezone
        }

        pipeline = [
            {
                "$match": {
                    ARCHIVE_TIME_FIELD: {
                        "$gte": ensure_timezone_aware(start_time),
                        "$lte": ensure_timezone_aware(end_time)
                    }
                }
            },
            {
                "$group": {
                    "_id": {
                        "year": {"$year": date_in_local_tz},
                        "week": {"$week": date_in_local_tz}
                    },
                    "count": {"$sum": 1}
                }
            },
            {
                "$sort": {
                    "_id.year": 1,
                    "_id.week": 1
                }
            }
        ]

        return list(self.__collection.aggregate(pipeline))

    def get_monthly_stats(self, start_time: datetime.datetime, end_time: datetime.datetime):
        """Get record counts grouped by month for the specified time range"""
        # MongoDB aggregation pipeline for monthly statistics

        date_in_local_tz = {
            "date": f"${ARCHIVE_TIME_FIELD}",
            "timezone": self.__local_timezone
        }

        pipeline = [
            {
                "$match": {
                    ARCHIVE_TIME_FIELD: {
                        "$gte": ensure_timezone_aware(start_time),
                        "$lte": ensure_timezone_aware(end_time)
                    }
                }
            },
            {
                "$group": {
                    "_id": {
                        "year": {"$year": date_in_local_tz},
                        "month": {"$month": date_in_local_tz}
                    },
                    "count": {"$sum": 1}
                }
            },
            {
                "$sort": {
                    "_id.year": 1,
                    "_id.month": 1
                }
            }
        ]

        return list(self.__collection.aggregate(pipeline))

    def get_stats_summary(self, start_time: datetime.datetime, end_time: datetime.datetime) -> Tuple[int, list]:
        """Get overall statistics for the specified time range"""
        # Total count in time range
        total_count = self.__collection.count_documents({
            ARCHIVE_TIME_FIELD: {
                "$gte": ensure_timezone_aware(start_time),
                "$lte": ensure_timezone_aware(end_time)
            }
        })

        # Count by informant
        informant_pipeline = [
            {
                "$match": {
                    ARCHIVE_TIME_FIELD: {
                        "$gte": ensure_timezone_aware(start_time),
                        "$lte": ensure_timezone_aware(end_time)
                    }
                }
            },
            {
                "$group": {
                    "_id": "$INFORMANT",
                    "count": {"$sum": 1}
                }
            },
            {"$sort": {"count": -1}}
        ]

        # INFORMANT 是文章 URL，直接按它分组会把每篇文章都误算成一个“来源”。
        # 先由 Mongo 压缩重复 URL，再在应用层按域名合并。
        source_counts = {}
        for item in self.__collection.aggregate(informant_pipeline):
            source = self._extract_source(item.get('_id'))
            if source:
                source_counts[source] = source_counts.get(source, 0) + item.get('count', 0)
        informant_stats = [
            {'_id': source, 'informant': source, 'count': count}
            for source, count in sorted(
                source_counts.items(), key=lambda pair: (-pair[1], pair[0]))
        ]

        return total_count, informant_stats

    def get_hourly_source_submissions(
            self,
            start_time: datetime.datetime,
            end_time: datetime.datetime,
            top_n: int | None = None,
    ) -> dict:
        """按采集提交时间统计各来源域名的小时序列。"""
        start = ensure_timezone_aware(start_time)
        end = ensure_timezone_aware(end_time)
        if start > end:
            raise ValueError("start_time must be before end_time")

        # cache 是 intake 接受事实源，包含归档、低价值、重试和失败；旧调用方未传
        # cache 时回退到归档集合，保持独立使用的兼容性。
        collection = (self.__cache_collection
                      if self.__cache_collection is not None else self.__collection)
        time_field = "collect_time" if self.__cache_collection is not None else SUBMISSION_TIME_FIELD
        informant_field = "informant" if self.__cache_collection is not None else "INFORMANT"
        date_in_local_tz = {
            "date": f"${time_field}",
            "timezone": self.__local_timezone,
        }
        pipeline = [
            {"$match": {
                time_field: {"$gte": start, "$lte": end},
                informant_field: {"$type": "string", "$ne": ""},
            }},
            {"$group": {
                "_id": {
                    "year": {"$year": date_in_local_tz},
                    "month": {"$month": date_in_local_tz},
                    "day": {"$dayOfMonth": date_in_local_tz},
                    "hour": {"$hour": date_in_local_tz},
                    "informant": f"${informant_field}",
                },
                "count": {"$sum": 1},
            }},
        ]

        by_source = {}
        totals = {}
        for item in collection.aggregate(pipeline):
            identity = item.get("_id") or {}
            source = self._extract_source(identity.get("informant"))
            if not source:
                continue
            slot = (identity.get("year"), identity.get("month"),
                    identity.get("day"), identity.get("hour"))
            count = int(item.get("count", 0))
            source_slots = by_source.setdefault(source, {})
            source_slots[slot] = source_slots.get(slot, 0) + count
            totals[source] = totals.get(source, 0) + count

        slots, slot_keys = self._hour_slots(start, end)
        ranked = sorted(totals, key=lambda source: (-totals[source], source))
        selected = ranked
        if top_n is not None:
            selected = ranked[:max(1, int(top_n))]
        series = {
            source: [by_source[source].get(key, 0) for key in slot_keys]
            for source in selected
        }

        return {
            "granularity": "hour",
            "time_range": {"start": start.isoformat(), "end": end.isoformat()},
            "slots": slots,
            "sources": list(series),
            "series": series,
            "totals": {source: sum(values) for source, values in series.items()},
            "source_count": len(ranked),
            "total_count": sum(totals.values()),
            "count_basis": "accepted_intake" if self.__cache_collection is not None else "archived",
        }

    def get_source_quality(self, start_time: datetime.datetime,
                           end_time: datetime.datetime) -> dict:
        """按来源汇总 intake 及处理终态，并细分低价值原因。"""
        start = ensure_timezone_aware(start_time)
        end = ensure_timezone_aware(end_time)
        if start > end:
            raise ValueError("start_time must be before end_time")
        if self.__cache_collection is None:
            raise RuntimeError("source quality statistics require cache_collection")

        cache_pipeline = [
            {"$match": {
                "collect_time": {"$gte": start, "$lte": end},
                "informant": {"$type": "string", "$ne": ""},
            }},
            {"$group": {
                "_id": {
                    "informant": "$informant",
                    "state": {"$ifNull": [
                        f"${CACHE_ARCHIVED_FLAG_FIELD}",
                        {"$ifNull": [f"${CACHE_LEGACY_ARCHIVED_FLAG_FIELD}", "__PENDING__"]},
                    ]},
                },
                "count": {"$sum": 1},
                "last_submission": {"$max": "$collect_time"},
            }},
        ]
        sources: dict[str, dict] = {}
        for item in self.__cache_collection.aggregate(cache_pipeline):
            identity = item.get("_id") or {}
            source = self._extract_source(identity.get("informant"))
            if not source:
                continue
            row = sources.setdefault(source, self._empty_source_quality_row(source))
            state = str(identity.get("state") or "__PENDING__")
            count = int(item.get("count", 0))
            row["accepted"] += count
            state_field = {
                CacheArchiveState.ARCHIVED.value: "archived",
                CacheArchiveState.DROP.value: "low_value",
                CacheArchiveState.RETRY.value: "retrying",
                CacheArchiveState.ERROR.value: "failed",
                CacheArchiveState.SENSITIVE.value: "sensitive",
                CacheArchiveState.DUPLICATED.value: "duplicate",
                CacheArchiveState.DEPRECATED.value: "deprecated",
                "__PENDING__": "pending",
            }.get(state, "pending")
            row[state_field] += count
            last = item.get("last_submission")
            if last is not None and (row["last_submission"] is None
                                     or last > row["last_submission"]):
                row["last_submission"] = last

        if self.__low_value_collection is not None:
            low_pipeline = [
                {"$match": {
                    SUBMISSION_TIME_FIELD: {"$gte": start, "$lte": end},
                    "INFORMANT": {"$type": "string", "$ne": ""},
                }},
                {"$group": {
                    "_id": {
                        "informant": "$INFORMANT",
                        "reason": {"$ifNull": [
                            "$NON_INTELLIGENCE_REASON",
                            NonIntelligenceReason.UNSPECIFIED.value,
                        ]},
                    },
                    "count": {"$sum": 1},
                }},
            ]
            for item in self.__low_value_collection.aggregate(low_pipeline):
                identity = item.get("_id") or {}
                source = self._extract_source(identity.get("informant"))
                if not source:
                    continue
                row = sources.setdefault(source, self._empty_source_quality_row(source))
                reason = str(identity.get("reason") or NonIntelligenceReason.UNSPECIFIED.value)
                count = int(item.get("count", 0))
                row["low_value_reasons"][reason] = (
                    row["low_value_reasons"].get(reason, 0) + count)
                if reason in NON_INTELLIGENCE_CONTENT_DEFECT_REASONS:
                    row["content_defect"] += count

        if self.__intake_rejected_collection is not None:
            rejected_pipeline = [
                {"$match": {
                    "rejected_at": {"$gte": start, "$lte": end},
                    "informant": {"$type": "string", "$ne": ""},
                }},
                {"$group": {"_id": "$informant", "count": {"$sum": 1}}},
            ]
            for item in self.__intake_rejected_collection.aggregate(rejected_pipeline):
                source = self._extract_source(item.get("_id"))
                if source:
                    row = sources.setdefault(source, self._empty_source_quality_row(source))
                    row["intake_rejected"] += int(item.get("count", 0))

        rows = []
        for row in sources.values():
            accepted = row["accepted"]
            row["archive_rate"] = self._ratio(row["archived"], accepted)
            row["low_value_rate"] = self._ratio(row["low_value"], accepted)
            row["content_defect_rate"] = self._ratio(row["content_defect"], accepted)
            failures = row["retrying"] + row["failed"] + row["sensitive"]
            row["processing_issue_rate"] = self._ratio(failures, accepted)
            row["total_attempts"] = accepted + row["intake_rejected"]
            row["last_submission"] = self._isoformat(row["last_submission"])
            row["status"], row["status_reasons"] = self._evaluate_source(row)
            rows.append(row)
        rows.sort(key=lambda item: (item["accepted"], item["source"]))
        return {
            "time_range": {"start": start.isoformat(), "end": end.isoformat()},
            "sources": rows,
            "source_count": len(rows),
            "thresholds": {
                "minimum_sample": 5,
                "poor_processing_issue_rate": 0.20,
                "poor_content_defect_rate": 0.30,
                "attention_processing_issue_rate": 0.05,
                "attention_content_defect_rate": 0.10,
                "attention_low_value_rate": 0.80,
            },
        }

    @staticmethod
    def _empty_source_quality_row(source: str) -> dict:
        return {
            "source": source, "accepted": 0, "intake_rejected": 0,
            "archived": 0, "low_value": 0, "content_defect": 0,
            "retrying": 0, "failed": 0, "sensitive": 0, "duplicate": 0,
            "deprecated": 0, "pending": 0, "last_submission": None,
            "low_value_reasons": {},
        }

    @staticmethod
    def _ratio(value: int, total: int) -> float:
        return round(value / total, 4) if total else 0.0

    @staticmethod
    def _isoformat(value):
        return value.isoformat() if hasattr(value, "isoformat") else value

    @staticmethod
    def _evaluate_source(row: dict) -> tuple[str, list[str]]:
        if row["accepted"] < 5:
            return "insufficient_data", ["样本不足"]
        reasons = []
        if row["processing_issue_rate"] >= 0.20:
            reasons.append("处理异常偏高")
        if row["content_defect_rate"] >= 0.30:
            reasons.append("抓取内容缺陷偏高")
        if reasons:
            return "poor", reasons
        if row["processing_issue_rate"] >= 0.05:
            reasons.append("存在处理异常")
        if row["content_defect_rate"] >= 0.10:
            reasons.append("存在抓取内容缺陷")
        if row["low_value_rate"] >= 0.80:
            reasons.append("低价值率偏高")
        return ("attention", reasons) if reasons else ("healthy", ["指标正常"])

    @staticmethod
    def _as_collection(value):
        if value is None:
            return None
        if callable(getattr(value, "aggregate", None)):
            return value
        return getattr(value, "collection", None)

    def _hour_slots(self, start: datetime.datetime, end: datetime.datetime):
        try:
            timezone = ZoneInfo(self.__local_timezone)
        except ZoneInfoNotFoundError:
            timezone = datetime.timezone.utc
        current = start.astimezone(timezone).replace(
            minute=0, second=0, microsecond=0)
        last = end.astimezone(timezone).replace(minute=0, second=0, microsecond=0)
        labels = []
        keys = []
        while current <= last:
            labels.append(current.strftime("%Y-%m-%d %H:00"))
            keys.append((current.year, current.month, current.day, current.hour))
            current += datetime.timedelta(hours=1)
        return labels, keys

    @staticmethod
    def _extract_source(informant) -> str:
        text = str(informant or "").strip()
        if not text:
            return ""
        parsed = urlparse(text if "://" in text else f"//{text}")
        source = (parsed.hostname or "").lower().strip(".")
        return source[4:] if source.startswith("www.") else source
