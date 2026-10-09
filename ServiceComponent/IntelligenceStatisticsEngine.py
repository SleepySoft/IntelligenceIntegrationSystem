import datetime
from typing import Any, Tuple

from Tools.DateTimeUtility import ensure_timezone_aware
class IntelligenceStatisticsEngine:
    def __init__(self, db: Any):
        self.__collection = getattr(db, "collection", db)
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
                    "archived_at": {
                        "$gte": ensure_timezone_aware(start_time),
                        "$lte": ensure_timezone_aware(end_time)
                    },
                    "total_score": {
                        "$gte": 1,
                        "$lte": 10
                    }
                }
            },
            {
                "$group": {
                    "_id": "$total_score",
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
        score_distribution = {str(i): 0 for i in range(1, 11)}  # Initialize all scores 1-10 with count 0

        for result in results:
            score = str(result['_id'])
            if score in score_distribution:
                score_distribution[score] = result['count']

        return score_distribution

    def get_hourly_stats(self, start_time: datetime.datetime, end_time: datetime.datetime) -> list:
        """Get record counts grouped by hour for the specified time range"""
        # MongoDB aggregation pipeline for hourly statistics

        date_in_local_tz = {
            "date": "$archived_at",
            "timezone": self.__local_timezone  # <-- 在这里使用本地时区
        }

        pipeline = [
            {
                "$match": {
                    "archived_at": {
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
            "date": "$archived_at",
            "timezone": self.__local_timezone
        }

        pipeline = [
            {
                "$match": {
                    "archived_at": {
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
            "date": "$archived_at",
            "timezone": self.__local_timezone
        }

        pipeline = [
            {
                "$match": {
                    "archived_at": {
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
            "date": "$archived_at",
            "timezone": self.__local_timezone
        }

        pipeline = [
            {
                "$match": {
                    "archived_at": {
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
            "archived_at": {
                "$gte": ensure_timezone_aware(start_time),
                "$lte": ensure_timezone_aware(end_time)
            }
        })

        # Count by informant
        informant_pipeline = [
            {
                "$match": {
                    "archived_at": {
                        "$gte": ensure_timezone_aware(start_time),
                        "$lte": ensure_timezone_aware(end_time)
                    }
                }
            },
            {
                "$group": {
                    "_id": "$informant",
                    "count": {"$sum": 1}
                }
            },
            {
                "$sort": {"count": -1}
            },
            {
                "$limit": 10  # Top 10 informants
            }
        ]

        informant_stats = list(self.__collection.aggregate(informant_pipeline))

        return total_count, informant_stats
