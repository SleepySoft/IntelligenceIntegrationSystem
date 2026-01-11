import re
import traceback

import pytz  # Time zone handling
import logging
import pymongo
import datetime
from bson.objectid import ObjectId
from tzlocal import get_localzone
from typing import Optional, List, Tuple, Union, Dict, Any

from Tools.DateTimeUtility import ensure_timezone_aware
from Tools.MongoDBAccess import MongoDBStorage


logger = logging.getLogger(__name__)


class IntelligenceQueryEngine:
    def __init__(self, db: MongoDBStorage):
        self.__mongo_db = db

    def get_intelligence(self, _uuid: Union[str, List[str]]) -> Union[Optional[dict], List[dict]]:
        """Retrieve single intelligence entry by UUID

        Args:
            _uuid (Union[str, List[str]]): UUID string or string list to query

        Returns:
            Union[Optional[dict], List[dict]]:
        """
        is_list_input = isinstance(_uuid, (list, tuple))

        # Parameter validation
        if not _uuid:
            logger.error("UUID parameter is empty")
            return [] if is_list_input else None

        try:
            # Attempt to get database connection
            collection = self.__mongo_db.collection
            if collection is None:
                logger.error("Database connection not initialized")
                return [] if is_list_input else None

            if is_list_input:
                # 清理 UUID 列表：转为小写字符串，并过滤掉空值
                sanitized_uuids = [str(u).lower() for u in _uuid if u]

                if not sanitized_uuids:
                    logger.warning("Input list includes Empty/None UUID")
                    return []

                # 构建 MongoDB $in 查询
                query = {"UUID": {"$in": sanitized_uuids}}

                # 执行查询（find 返回一个游标）
                cursor = collection.find(query)

                # 处理游标中的所有文档
                results = [self.process_document(doc) for doc in cursor]

                # (可选) 记录有多少请求的 UUID 未被找到
                if len(results) < len(sanitized_uuids):
                    found_uuids_set = {doc.get('UUID', '').lower() for doc in results}
                    missing = [u for u in sanitized_uuids if u not in found_uuids_set]
                    logger.warning(f"Not found UUID: {missing}")

                return results
            else:
                # Build exact match UUID query
                query = {"UUID": str(_uuid).lower()}

                # Execute query - get first match
                doc = collection.find_one(query)

                if doc is None:
                    logger.warning(f"No matching UUID found: {_uuid}")
                    return None

                # Process document format
                return self.process_document(doc)

        except pymongo.errors.PyMongoError as e:
            logger.error(f"Database query failed: {str(e)}")
            return [] if is_list_input else None
        except Exception as e:
            logger.exception(f"Unknown error: {str(e)}")
            return [] if is_list_input else None

    def get_intelligence_summary(self) -> Dict[str, Union[int, Optional[str]]]:
        """
        Retrieve total intelligence count and latest document ID as base snapshot

        Returns:
            dict: Dictionary containing:
                - total_count: Total number of intelligence documents
                - base_uuid: UUID of the newest document (as stable pagination base)

        Ensures consistent pagination even when new documents are added.
        """
        collection = self.__mongo_db.collection

        try:
            # Get total document count
            total_count = collection.count_documents({})

            # Find the newest document as base reference
            newest_doc = collection.find_one(
                filter={},
                # TODO: Temporary hardcoded.
                sort=[("APPENDIX.__TIME_ARCHIVED__", pymongo.DESCENDING)]
            )

            base_uuid = newest_doc["UUID"] if newest_doc else None

            return {
                "total_count": total_count,
                "base_uuid": base_uuid
            }

        except pymongo.errors.PyMongoError as e:
            logger.error(f"Intelligence summary retrieval failed: {str(e)}")
            return {"total_count": 0, "base_uuid": None}

    # def get_paginated_intelligences(self, base_uuid: str, offset: int, limit: int) -> List[dict]:
    #     """
    #     Retrieve paginated intelligence with stable ordering
    #
    #     Args:
    #         base_uuid: Reference UUID for stable pagination anchor (None for start)
    #         offset: Number of documents to skip from the base
    #         limit: Maximum number of documents to return
    #
    #     Returns:
    #         List of processed intelligence documents
    #     """
    #     if limit <= 0:
    #         return []
    #
    #     collection = self.__mongo_db.collection
    #     sort_order = [
    #         # TODO: Temporary hardcoded.
    #         ("APPENDIX.__TIME_ARCHIVED__", pymongo.DESCENDING),
    #         ("_id", pymongo.DESCENDING)  # Secondary sort for consistency
    #     ]
    #
    #     try:
    #         if base_uuid:
    #             base_doc = collection.find_one({"UUID": base_uuid})
    #             if not base_doc:
    #                 logger.warning(f"Base UUID not found: {base_uuid}")
    #                 return []
    #
    #             cursor = collection.find(
    #                 filter={"PUB_TIME": {"$lte": base_doc["PUB_TIME"]}}
    #             ).sort(sort_order).skip(offset).limit(limit)
    #         else:
    #             cursor = collection.find().sort(sort_order).skip(offset).limit(limit)
    #
    #         return [self.process_document(doc) for doc in cursor]
    #
    #     except pymongo.errors.PyMongoError as e:
    #         logger.error(f"Pagination query failed: {str(e)}")
    #         return []
    #
    #     except Exception as e:
    #         logger.error(f"Exception on query: {str(e)}")
    #         return []

    def query_intelligence(
            self,
            *,
            period: Optional[Tuple[datetime.datetime, datetime.datetime]] = None,
            archive_period: Optional[Tuple[datetime.datetime, datetime.datetime]] = None,
            locations: Optional[Union[str, List[str]]] = None,
            peoples: Optional[Union[str, List[str]]] = None,
            organizations: Optional[Union[str, List[str]]] = None,
            keywords: Optional[str] = None,
            threshold: Optional[float] = None,  # New threshold parameter
            skip: Optional[int] = None,
            limit: Optional[int] = None
    ) -> Tuple[List[dict], int]:
        """Execute intelligence query

        Args:
            period: Article mentioned time. UTC time range (start, end)
            archive_period: Archive time. UTC time range (start, end)
            locations: Article mentioned location(s) (str or str list)
            peoples: Article mentioned person (s) (str or str list)
            organizations: Article mentioned organization (s) (str or str list)
            keywords: Keywords in raw article.
            threshold: Minimum score value for filtering APPENDIX_MAX_RATE_SCORE
            skip: Number of documents to skip (offset / page * item_per_page)
            limit: Maximum number of results to return (item_per_page)

        Returns:
            Tuple of
                List of matching intelligence documents
                All document count without specifying skip and limit.
        """
        # Get specified database collection
        collection = self.__mongo_db.collection

        try:
            # Build MongoDB query
            query = self.build_intelligence_query(
                period=period,
                archive_period=archive_period,
                locations=locations,
                peoples=peoples,
                organizations=organizations,
                keywords=keywords,
                threshold=threshold
            )

            compass_query = self.convert_to_compass_query(query)
            logger.debug(compass_query)

            # Execute query and return results with limit
            data = self.execute_query(collection, query, skip=skip, limit=limit)
            total = collection.count_documents(query)

            return data, total

        except pymongo.errors.PyMongoError as e:
            logger.error(f"Intelligence query failed: {str(e)}")
            return [], 0
        except Exception as e:
            traceback.print_exc()
            logger.error(f"Intelligence query error: {str(e)}", stack_info=True)
            return [], 0

    def build_intelligence_query(
            self,
            period: Optional[Tuple[datetime.datetime, datetime.datetime]] = None,
            archive_period: Optional[Tuple[datetime.datetime, datetime.datetime]] = None,
            locations: Optional[Union[str, List[str]]] = None,
            peoples: Optional[Union[str, List[str]]] = None,
            organizations: Optional[Union[str, List[str]]] = None,
            keywords: Optional[str] = None,
            threshold: Optional[float] = None  # Filter by score
    ) -> dict:
        query_conditions = []

        # 1. Time Period (Compatibility Mode)
        # v1 stores in "PUB_TIME", v2 stores in "APPENDIX.__TIME_PUB__"
        if period:
            # We generate two conditions: one for v1, one for v2
            v1_condition = self.build_time_condition("PUB_TIME", *period)
            v2_condition = self.build_time_condition("APPENDIX.__TIME_PUB__", *period)

            # Use $or operator: matches if the time exists in EITHER field and satisfies range
            query_conditions.append({"$or": [v1_condition, v2_condition]})

        # 2. Archive Period (Stable)
        # Both v1 and v2 store this in APPENDIX
        if archive_period:
            query_conditions.append(self.build_time_condition("APPENDIX.__TIME_ARCHIVED__", *archive_period))

        # 3. Entities (Stable)
        # Both v1 and v2 store these at the root level (LIST[str])
        if locations:
            query_conditions.append(self.build_list_condition("LOCATION", locations))

        if peoples:
            query_conditions.append(self.build_list_condition("PEOPLE", peoples))

        if organizations:
            query_conditions.append(self.build_list_condition("ORGANIZATION", organizations))

        # 4. Keywords (Suggest Updating internal method)
        # Ensure `build_keyword_or_condition` checks fields:
        # ['title', 'content', 'EVENT_TITLE', 'EVENT_BRIEF', 'EVENT_TEXT']
        if keywords:
            query_conditions.append(self.build_keyword_or_condition(keywords))

        # 5. Score Threshold (Hybrid Strategy)
        if threshold is not None:
            # We assume the Archiver calculates the max score for v2 and saves it
            # to '__MAX_RATE_SCORE__' to maintain efficient querying.
            # If not, dynamic querying on the 'RATE' dict is very slow/complex in MongoDB.
            score_field = "APPENDIX.__MAX_RATE_SCORE__"

            # Optional: If you want to support filtering by Vector Similarity Score as well
            # vector_score_field = "APPENDIX.__VECTOR_SCORE__"
            # query_conditions.append({"$or": [
            #     {score_field: {"$gte": threshold}},
            #     {vector_score_field: {"$gte": threshold}}
            # ]})

            # Default behavior: Query the standardized max score
            query_conditions.append({score_field: {"$gte": threshold}})

        return {"$and": query_conditions} if query_conditions else {}

    # def build_intelligence_query(
    #         self,
    #         period: Optional[Tuple[datetime.datetime, datetime.datetime]] = None,
    #         archive_period: Optional[Tuple[datetime.datetime, datetime.datetime]] = None,
    #         locations: Optional[Union[str, List[str]]] = None,
    #         peoples: Optional[Union[str, List[str]]] = None,
    #         organizations: Optional[Union[str, List[str]]] = None,
    #         keywords: Optional[str] = None,
    #         threshold: Optional[float] = None  # New threshold parameter
    # ) -> dict:
    #     query_conditions = []
    #
    #     if period:
    #         query_conditions.append(self.build_time_condition("PUB_TIME", *period))
    #
    #     if archive_period:
    #         query_conditions.append(self.build_time_condition("APPENDIX.__TIME_ARCHIVED__", *archive_period))
    #
    #     if locations:
    #         query_conditions.append(self.build_list_condition("LOCATION", locations))
    #
    #     if peoples:
    #         query_conditions.append(self.build_list_condition("PEOPLE", peoples))
    #
    #     if organizations:
    #         query_conditions.append(self.build_list_condition("ORGANIZATION", organizations))
    #
    #     if keywords:
    #         query_conditions.append(self.build_keyword_or_condition(keywords))
    #
    #     # Add threshold condition if provided
    #     if threshold is not None:
    #         score_field = f"APPENDIX.__MAX_RATE_SCORE__"
    #         query_conditions.append({score_field: {"$gte": threshold}})
    #
    #     return {"$and": query_conditions} if query_conditions else {}

    def common_query(
            self,
            *,
            conditions: Dict[str, Any],
            operator: str = "$and",
            skip: Optional[int] = None,
            limit: Optional[int] = None
    ) -> List[dict]:
        """
        Execute a dynamic query on the intelligence collection using flexible field conditions.

        This function constructs and executes a MongoDB query based on arbitrary field specifications,
        supporting nested fields (using dot notation) and multiple logical operators. It handles both
        simple equality checks and complex conditions (e.g., range queries, existence checks) dynamically.

        Args:
            conditions: A dictionary mapping field paths to query conditions.
                - Keys can be top-level fields (e.g., "status") or nested paths (e.g., "meta.author.organization").
                - Values can be direct values (implicit equality check) or operator dictionaries (e.g.,
                  {"$gte": 10}, {"$in": ["A", "B"]}, {"$exists": True}).
            operator: Logical operator to combine conditions ("$and" or "$or"). Defaults to "$and".
            skip: Number of documents to skip (for pagination).
            limit: Maximum number of documents to return.

        Returns:
            List[dict]: Matching documents from the collection. Returns empty list on query errors.

        Example:
            conditions = {
                "status": "active",
                "confidence": {"$gte": 0.7},
                "entities.locations": {"$in": ["Beijing", "Shanghai"]},
                "timestamp": {"$exists": True}
            }
            results = dynamic_query(conditions=conditions, operator="$and", skip=0, limit=10)
        """
        collection = self.__mongo_db.collection

        try:
            query = self.build_common_conditions(conditions, operator)
            return self.execute_query(collection, query, skip=skip, limit=limit)

        except pymongo.errors.PyMongoError as e:
            logger.error(f"Dynamic query failed: {str(e)}")
            return []
        except ValueError as e:
            logger.error(f"Invalid operator: {str(e)}")
            return []
        except Exception as e:
            logger.error(f"Dynamic query error: {str(e)}", stack_info=True)
            return []

    def aggregate(self, pipeline: list) -> list:
        try:
            collection = self.__mongo_db.collection
            results = list(collection.aggregate(pipeline))
            return results
        except ValueError:
            logger.error("Invalid time format. Please use ISO format (e.g., '2024-01-01T00:00:00Z')")
            return []
        except Exception as e:
            logger.error(f"Error processing request: {str(e)}")
            return []

    def count_documents(self, _filter: dict) -> int:
        try:
            collection = self.__mongo_db.collection
            results = collection.count_documents(_filter)
            return results
        except ValueError:
            logger.error("Invalid time format. Please use ISO format (e.g., '2024-01-01T00:00:00Z')")
            return -1
        except Exception as e:
            logger.error(f"Error processing request: {str(e)}")
            return -1

    @staticmethod
    def build_common_conditions(conditions: Dict[str, Any], operator: str = "$and") -> dict:
        """
        Construct MongoDB query conditions from dynamic field specifications.

        Transforms a dictionary of field-based conditions into a valid MongoDB query. Supports:
        - Nested field resolution using dot notation (e.g., "meta.location.city" → nested document query)
        - Explicit operator syntax (e.g., {"$gt": 100})
        - Logical combination of conditions via "$and" or "$or"

        Args:
            conditions: Dictionary of field paths to conditions.
                Each key-value pair generates a condition clause.
            operator: Logical operator ("$and" or "$or") to combine conditions.
                Raises ValueError for invalid operators.

        Returns:
            dict: Valid MongoDB query document. Returns empty dict if no conditions provided.

        Raises:
            ValueError: If operator is not "$and" or "$or".

        Example output for conditions {"a": 1, "b.c": {"$lt": 5}}:
            {"$and": [{"a": 1}, {"b.c": {"$lt": 5}}]}

        Notes:
            - Single-condition inputs return directly without logical operator wrapping.
            - Dot-notated fields are expanded into nested documents (e.g., "x.y.z" → {"x": {"y": {"z": value}}}).
        """
        if operator not in ["$and", "$or"]:
            raise ValueError("Operator must be '$and' or '$or'")

        # 处理嵌套字段和操作符
        condition_list = []
        for field_path, condition in conditions.items():
            # 处理嵌套字段（使用MongoDB的点表示法）
            if "." in field_path:
                field_parts = field_path.split(".")
                # 构建嵌套查询条件
                nested_cond = {f"{field_parts[-1]}": condition}
                for part in reversed(field_parts[:-1]):
                    nested_cond = {part: nested_cond}
                condition_list.append(nested_cond)
            else:
                # 直接字段查询
                condition_list.append({field_path: condition})

        # 组合所有条件
        if not condition_list:
            return {}  # 无任何条件
        elif len(condition_list) == 1:
            return condition_list[0]  # 单一条件无需运算符
        else:
            return {operator: condition_list}

    def process_document(self, doc: dict) -> dict:
        doc_processed = self.__mongo_db.process_document_output(doc)

        # 确保所有字段都有默认值
        fields = {
            'TIME': [],
            'LOCATION': [],
            'PEOPLE': [],
            'ORGANIZATION': [],
            'EVENT_BRIEF': "",
            'EVENT_TEXT': "",
            'RATE': {},
            'IMPACT': "",
            'TIPS': ""
        }

        for field, default in fields.items():
            if field not in doc_processed or doc_processed[field] is None:
                doc_processed[field] = default

        return doc_processed

    def build_time_condition(self, key: str, start_time: datetime.datetime, end_time: datetime.datetime) -> dict:
        """
        Builds a MongoDB time condition dictionary, correctly handling timezones
        and returning proper datetime objects for the query.

        Args:
            key: The document field key to apply the condition to.
            start_time: The start of the time range (can be naive or aware).
            end_time: The end of the time range (can be naive or aware).

        Returns:
            A dictionary for MongoDB query with the correct BSON Date type.
        """
        # 1. Ensure both datetime objects are timezone-aware
        aware_start = ensure_timezone_aware(start_time)
        aware_end = ensure_timezone_aware(end_time)

        # 2. Convert both to UTC, keeping them as datetime objects
        utc_start = aware_start.astimezone(pytz.utc)
        utc_end = aware_end.astimezone(pytz.utc)

        # 3. Return the dictionary with the actual datetime objects
        return {key: {"$gte": utc_start, "$lte": utc_end}}

    def build_list_condition(self, field: str, values: Union[str, List[str]]) -> dict:
        """构建列表字段查询条件"""
        target_list = [values] if isinstance(values, str) else values
        return {field: {"$in": target_list}}

    def build_keyword_or_condition(self, keywords: str) -> dict:
        cleaned_keywords = self.sanitize_keywords(keywords)
        # 平铺所有字段条件（无需二维列表）
        conditions = []
        for kw in cleaned_keywords:
            conditions.append({"EVENT_TITLE": {"$regex": kw, "$options": "i"}})
            conditions.append({"EVENT_BRIEF": {"$regex": kw, "$options": "i"}})
            conditions.append({"EVENT_TEXT": {"$regex": kw, "$options": "i"}})
            conditions.append({"RAW_DATA.EVENT_TITLE": {"$regex": kw, "$options": "i"}})
            conditions.append({"RAW_DATA.EVENT_BRIEF": {"$regex": kw, "$options": "i"}})
            conditions.append({"RAW_DATA.EVENT_TEXT": {"$regex": kw, "$options": "i"}})
        return {"$or": conditions}  # 匹配任一条件

    def build_keyword_and_condition(self, keywords: str) -> dict:
        """构建全文检索查询条件（同时匹配所有关键词）"""
        cleaned_keywords = self.sanitize_keywords(keywords)
        if not cleaned_keywords:
            return {}

        conditions = []
        for kw in cleaned_keywords:
            # 每个关键词在任意字段出现即可（字段间OR）
            kw_condition = {
                "$or": [
                    {"EVENT_BRIEF": {"$regex": kw, "$options": "i"}},
                    {"EVENT_TEXT": {"$regex": kw, "$options": "i"}}
                ]
            }
            conditions.append(kw_condition)  # 每个关键词独立条件组

        # 用AND组合所有关键词条件
        return {"$and": conditions}

    def sanitize_keywords(self, keywords: str) -> List[str]:
        """清洗并优化关键词"""
        # 分割关键词并移除空值
        keywords = [kw.strip() for kw in keywords.split() if kw.strip()]

        # 转义特殊字符并添加边界匹配
        return [r'\b' + re.escape(kw) + r'\b' for kw in keywords]

    def execute_query(
            self,
            collection: pymongo.collection.Collection,
            query: dict,
            skip: Optional[int] = None,
            limit: Optional[int] = None
    ) -> List[dict]:
        """Execute query and process results with pagination support

        Args:
            collection: MongoDB collection to query
            query: MongoDB query dictionary
            skip: Number of documents to skip (for pagination)
            limit: Maximum number of documents to return

        Returns:
            List of processed documents matching the query
        """
        try:
            # Apply sorting by TIME field in descending order
            # TODO: Temporary hardcoded.
            cursor = collection.find(query).sort("APPENDIX.__TIME_ARCHIVED__", pymongo.DESCENDING)

            # Apply pagination parameters if provided
            if skip is not None and skip > 0:
                cursor = cursor.skip(skip)  # Skip documents for pagination [6,7](@ref)

            if limit is not None and limit > 0:
                cursor = cursor.limit(limit)  # Limit result size [6](@ref)

            # Process and return results
            return [self.process_document(doc) for doc in cursor]

        except pymongo.errors.PyMongoError as e:
            logger.error(f"MongoDB query execution failed: {str(e)}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error during query execution: {str(e)}", stack_info=True)
            return []

    @staticmethod
    def convert_to_compass_query(query_dict: dict) -> str:
        """
        Converts a Python dictionary to a MongoDB Compass query string.

        This function recursively formats the dictionary and its values to match
        the MongoDB Shell/Compass syntax, properly handling types like
        datetime objects, ObjectIds, strings, numbers, and booleans.

        Args:
            query_dict: A Python dictionary representing the MongoDB query.

        Returns:
            A string that can be directly used in the MongoDB Compass filter bar.
        """

        def format_value(value):
            """Recursively formats a Python value into a Compass-compatible string."""
            if isinstance(value, dict):
                # Recursively format key-value pairs for sub-documents
                items = []
                for k, v in value.items():
                    # Keys in MongoDB are strings, so we quote them.
                    key_str = f'"{k}"'
                    value_str = format_value(v)
                    items.append(f"{key_str}: {value_str}")
                return f"{{ {', '.join(items)} }}"

            elif isinstance(value, list):
                # Recursively format each item in a list
                items = [format_value(item) for item in value]
                return f"[{', '.join(items)}]"

            elif isinstance(value, str):
                # Escape backslashes and double quotes, then wrap in double quotes
                escaped_value = value.replace('\\', '\\\\').replace('"', '\\"')
                return f'"{escaped_value}"'

            elif isinstance(value, datetime.datetime):
                # Format datetime objects into ISODate("...")
                # Ensure it's in UTC format with 'Z'
                iso_string = value.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
                return f'ISODate("{iso_string}")'

            elif isinstance(value, ObjectId):
                # Format ObjectId objects into ObjectId("...")
                return f'ObjectId("{str(value)}")'

            elif isinstance(value, bool):
                # Format booleans as lowercase true/false
                return "true" if value else "false"

            elif isinstance(value, (int, float)):
                # Numbers are converted directly to string
                return str(value)

            elif value is None:
                # None becomes null
                return "null"

            else:
                # For any other unhandled type, convert to a simple string representation
                return f'"{str(value)}"'

        if not isinstance(query_dict, dict):
            raise TypeError("Input must be a dictionary.")

        return format_value(query_dict)
