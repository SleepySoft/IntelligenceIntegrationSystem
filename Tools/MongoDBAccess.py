import json
import logging
import datetime
import calendar
from pathlib import Path
from bson import ObjectId
from typing import Dict, Optional, List, Any, Sequence, Union, Tuple
from pymongo.database import Database
from pymongo.collection import Collection
from pymongo.errors import PyMongoError
from pymongo import MongoClient, ASCENDING, DESCENDING, IndexModel
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# --- Timezone Setup ---
# Use UTC as the base timezone for all internal and database operations.
UTC = datetime.timezone.utc

# Try to get the local timezone, fall back to UTC if not found.
try:
    import tzlocal

    LOCAL_TZ = ZoneInfo(tzlocal.get_localzone_name())
except (ImportError, ZoneInfoNotFoundError):
    print("Warning: tzlocal not found or local timezone could not be determined. Falling back to UTC.")
    LOCAL_TZ = UTC

logger = logging.getLogger(__name__)

IndexSpec = Sequence[Tuple[str, Union[int, str]]]


# Custom Encoder to handle datetime and ObjectId for JSON serialization
class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime.datetime):
            # Return ISO 8601 formatted string
            return obj.isoformat()
        if isinstance(obj, ObjectId):
            return str(obj)
        return super().default(obj)


class MongoDBError(Exception):
    """Base exception for MongoDB operations"""


class MongoDBConnectionError(MongoDBError):
    """Error establishing database connection"""


class MongoDBOperationError(MongoDBError):
    """Error executing database operation"""


class MongoDBStorage:
    """
    A thread-safe, general-purpose MongoDB storage handler with automatic
    timezone management.

    Key Features:
    - Centralized connection management following PyMongo best practices.
    - Automatic Timezone Conversion:
        - On Write (insert, update): All datetime objects in input data
          are automatically converted to timezone-aware UTC before storage.
          Naive datetimes are assumed to be in the local timezone.
        - On Read (find_one, find_many, aggregate): All datetime objects in the output
          are automatically converted from UTC to the local timezone.
    - Automatic ObjectId to String Conversion:
        - The '_id' field of returned documents is converted from ObjectId to string.
    """

    def __init__(self,
                 host: str = 'localhost',
                 port: int = 27017,
                 db_name: str = 'my_app_db',
                 collection_name: str = 'default_collection',
                 username: Optional[str] = None,
                 password: Optional[str] = None,
                 auth_source: str = 'admin',
                 max_pool_size: int = 100,
                 indexes: Optional[List[IndexSpec]] = None,
                 **kwargs):
        """
        Initializes the MongoDB connection and the storage handler.
        """
        self.connection_uri = f"mongodb://{username}:{password}@{host}:{port}/?authSource={auth_source}" \
            if username and password else f"mongodb://{host}:{port}/"

        try:
            self.client = MongoClient(
                self.connection_uri,
                maxPoolSize=max_pool_size,
                connectTimeoutMS=3000,
                serverSelectionTimeoutMS=5000,
                tz_aware=True,  # Crucial for reading aware datetimes
                **kwargs
            )
            # Verify connection
            self.client.admin.command('ping')
            logger.info("MongoDB connection successful.")
        except PyMongoError as e:
            logger.critical(f"MongoDB connection failed: {e}")
            raise MongoDBConnectionError(f"Failed to connect to MongoDB: {e}") from e

        self.db: Database = self.client[db_name]
        self.collection: Collection = self.db[collection_name]

        if indexes:
            self._create_indexes(indexes)

    def _create_indexes(self, indexes: List[IndexSpec]) -> None:
        """Create indexes on the collection."""
        try:
            # PyMongo > 4.0 requires a list of IndexModel objects.
            index_models = [IndexModel(index) for index in indexes]
            self.collection.create_indexes(index_models)
            logger.info(f"Indexes ensured for collection '{self.collection.name}'.")
        except PyMongoError as e:
            logger.error(f"Failed to create indexes: {e}")
            raise MongoDBOperationError(f"Index creation failed: {e}") from e

    # --- Helper Methods ---

    def _normalize_to_utc(self, dt: datetime.datetime) -> datetime.datetime:
        """Converts a datetime object to timezone-aware UTC."""
        if dt.tzinfo is None:
            # Assume naive datetime is in local timezone
            return dt.replace(tzinfo=LOCAL_TZ).astimezone(UTC)
        # If already aware, just convert to UTC
        return dt.astimezone(UTC)

    def _process_dates_recursive(self, data: Any, conversion_func: callable) -> Any:
        """Recursively traverses data to apply a conversion function to datetime objects."""
        if isinstance(data, dict):
            return {k: self._process_dates_recursive(v, conversion_func) for k, v in data.items()}
        elif isinstance(data, list):
            return [self._process_dates_recursive(item, conversion_func) for item in data]
        elif isinstance(data, datetime.datetime):
            return conversion_func(data)
        return data

    def process_document_output(self, document: Optional[Dict]) -> Optional[Dict]:
        """Handles common processing for documents coming from the database."""
        if not document:
            return None
        # Convert _id if it's an ObjectId
        if '_id' in document and isinstance(document['_id'], ObjectId):
            document['_id'] = str(document['_id'])
        # Convert all UTC datetimes to local time
        return self._process_dates_recursive(document, lambda dt: dt.astimezone(LOCAL_TZ))

    # --- CRUD Methods ---

    def insert(self, data: Dict[str, Any], **kwargs) -> str:
        """
        Inserts a single document, converting any datetimes to UTC.
        Returns the string representation of the inserted document's _id.
        """
        try:
            processed_data = self._process_dates_recursive(data, self._normalize_to_utc)
            result = self.collection.insert_one(processed_data, **kwargs)
            return str(result.inserted_id)
        except PyMongoError as e:
            logger.error(f"Insert operation failed: {e}")
            raise MongoDBOperationError from e

    def bulk_insert(self, data_list: List[Dict[str, Any]], **kwargs) -> List[str]:
        """
        Inserts multiple documents, converting datetimes in each to UTC.
        Returns a list of string representations of the inserted _ids.
        """
        if not data_list:
            return []
        try:
            processed_list = [self._process_dates_recursive(doc, self._normalize_to_utc) for doc in data_list]
            result = self.collection.insert_many(processed_list, ordered=False, **kwargs)
            return [str(id) for id in result.inserted_ids]
        except PyMongoError as e:
            logger.error(f"Bulk insert operation failed: {e}")
            raise MongoDBOperationError from e

    def find_one(self, query_dict: Dict[str, Any], **kwargs) -> Optional[Dict]:
        """
        Finds a single document. Converts datetimes in the query to UTC for searching,
        and converts datetimes and _id in the result.
        """
        try:
            # Convert top-level string _id to ObjectId for querying
            if '_id' in query_dict and isinstance(query_dict['_id'], str):
                try:
                    query_dict['_id'] = ObjectId(query_dict['_id'])
                except Exception:  # Catches bson.errors.InvalidId
                    logger.warning(f"Invalid format for _id: '{query_dict['_id']}'. Cannot be converted to ObjectId.")
                    return None  # No document can match an invalid ID format

            processed_query = self._process_dates_recursive(query_dict, self._normalize_to_utc)
            document = self.collection.find_one(processed_query, **kwargs)
            return self.process_document_output(document)
        except PyMongoError as e:
            logger.error(f"Find_one operation failed: {e}")
            raise MongoDBOperationError from e

    def find_many(self,
                  query_dict: Dict[str, Any],
                  sort: Optional[IndexSpec] = None,
                  limit: int = 0,
                  **kwargs) -> List[Dict]:
        """
        Finds multiple documents with sorting and limit options.
        Handles timezone and _id conversions for query and results.
        """
        try:
            # Convert top-level string _id to ObjectId for querying
            if '_id' in query_dict and isinstance(query_dict['_id'], str):
                try:
                    query_dict['_id'] = ObjectId(query_dict['_id'])
                except Exception:
                    logger.warning(f"Invalid format for _id: '{query_dict['_id']}'. Query will return no results.")
                    return []  # No document can match an invalid ID format

            processed_query = self._process_dates_recursive(query_dict, self._normalize_to_utc)
            cursor = self.collection.find(processed_query, **kwargs)

            if sort:
                cursor = cursor.sort(sort)
            if limit > 0:
                cursor = cursor.limit(limit)

            return [self.process_document_output(doc) for doc in cursor]
        except PyMongoError as e:
            logger.error(f"Find_many operation failed: {e}")
            raise MongoDBOperationError from e

    def update(self, filter_query: Dict[str, Any], update_data: Dict[str, Any], **kwargs) -> Tuple[int, int]:
        """
        Updates documents matching the filter. Handles timezone conversion for
        both the filter and the update data.
        """
        try:
            # Convert top-level string _id to ObjectId for querying
            if '_id' in filter_query and isinstance(filter_query['_id'], str):
                try:
                    filter_query['_id'] = ObjectId(filter_query['_id'])
                except Exception:
                    logger.warning(
                        f"Invalid format for _id in filter: '{filter_query['_id']}'. Update will match 0 documents.")
                    return 0, 0

            processed_filter = self._process_dates_recursive(filter_query, self._normalize_to_utc)
            processed_update = self._process_dates_recursive(update_data, self._normalize_to_utc)

            if not any(key.startswith('$') for key in processed_update.keys()):
                processed_update = {'$set': processed_update}

            result = self.collection.update_many(processed_filter, processed_update, **kwargs)
            return result.matched_count, result.modified_count
        except PyMongoError as e:
            logger.error(f"Update operation failed: {e}")
            raise MongoDBOperationError from e

    # --- Advanced Query Methods ---

    def count_documents(self, query_dict: Dict[str, Any], **kwargs) -> int:
        """
        Counts documents matching the query.
        Handles timezone conversion for any datetimes in the query.
        """
        try:
            # Convert top-level string _id to ObjectId for querying
            if '_id' in query_dict and isinstance(query_dict['_id'], str):
                try:
                    query_dict['_id'] = ObjectId(query_dict['_id'])
                except Exception:
                    logger.warning(f"Invalid format for _id: '{query_dict['_id']}'. Count will be 0.")
                    return 0

            processed_query = self._process_dates_recursive(query_dict, self._normalize_to_utc)
            return self.collection.count_documents(processed_query, **kwargs)
        except PyMongoError as e:
            logger.error(f"Count_documents operation failed: {e}")
            raise MongoDBOperationError from e

    def aggregate(self, pipeline: List[Dict[str, Any]], **kwargs) -> List[Dict]:
        """
        Executes an aggregation pipeline.
        Handles timezone conversion for datetimes within the pipeline and
        converts datetimes and _ids in the results.
        Note: Automatic string '_id' to ObjectId conversion is not supported for
        complex pipelines; provide ObjectIds directly in stages like $match.
        """
        try:
            processed_pipeline = self._process_dates_recursive(pipeline, self._normalize_to_utc)
            cursor = self.collection.aggregate(processed_pipeline, **kwargs)
            return [self.process_document_output(doc) for doc in cursor]
        except PyMongoError as e:
            logger.error(f"Aggregation operation failed: {e}")
            raise MongoDBOperationError from e

    def close(self) -> None:
        """Closes the client connection."""
        self.client.close()
        logger.info("MongoDB connection closed.")

    # ------------------------------------------ Export ------------------------------------------

    def _generate_filename(self,
                           prefix: str,
                           time_str: str,
                           directory: str,
                           add_timestamp: bool = False) -> str:
        """
        Generates a standardized filename.
        Format: {directory}/{prefix}_{time_str}[_timestamp].json
        """
        # We prepare the path here, but the directory creation is doubly ensured
        # in _write_json_file for robustness.
        filename = f"{prefix}_{time_str}"

        if add_timestamp:
            # Use compact timestamp format, e.g., 20231129103005
            ts = datetime.datetime.now(LOCAL_TZ).strftime("%Y%m%d%H%M%S")
            filename += f"_{ts}"

        return str(Path(directory) / f"{filename}.json")

    def _write_json_file(self, data: List[Dict], filepath: str) -> None:
        """
        Writes data to a JSON file.
        CRITICAL: Automatically creates the parent directory if it does not exist.
        """
        try:
            path_obj = Path(filepath)

            # Ensure the parent directory exists immediately before writing.
            # parents=True: creates missing parent directories (mkdir -p)
            # exist_ok=True: does not raise error if directory already exists
            if not path_obj.parent.exists():
                path_obj.parent.mkdir(parents=True, exist_ok=True)
                logger.info(f"Created missing directory: {path_obj.parent}")

            with open(filepath, 'w', encoding='utf-8') as f:
                # ensure_ascii=False allows non-ASCII characters (like Chinese) to be readable
                json.dump(data, f, cls=DateTimeEncoder, ensure_ascii=False, indent=2)

            logger.info(f"Successfully exported {len(data)} records to {filepath}")

        except OSError as e:
            # Catching OSError handles both directory creation errors and file writing errors
            logger.error(f"Failed to write file {filepath}: {e}")
            raise

    def export_by_time_range(self,
                             start_dt: datetime.datetime,
                             end_dt: datetime.datetime,
                             directory: str,
                             time_field: str = "created_at",
                             file_prefix: str = "export",
                             add_timestamp: bool = False,
                             filename_override: Optional[str] = None) -> str:
        """
        Core export function: Exports data within a specific time range.

        Args:
            start_dt: Start datetime (inclusive).
            end_dt: End datetime (exclusive).
            directory: The output directory path.
            time_field: The database field to filter by (default: "created_at").
            file_prefix: Prefix for the filename.
            add_timestamp: If True, appends the current timestamp to the filename to avoid overwriting.
            filename_override: If provided, uses this string for the time part of the filename.
        """
        # 1. Normalize Timezones
        # Ensure input datetimes are timezone-aware. If naive, assume LOCAL_TZ.
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=LOCAL_TZ)
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=LOCAL_TZ)

        # 2. Build Query
        # Note: We manually build the query here. The find_many method will handle
        # converting the query values to UTC and the result values to Local Time.
        query = {
            time_field: {
                "$gte": start_dt,
                "$lt": end_dt
            }
        }

        # 3. Fetch Data
        data = self.find_many(query)

        if not data:
            logger.warning(f"No data found between {start_dt} and {end_dt}.")
            return ""

        # 4. Generate Filename
        if filename_override:
            time_str = filename_override
        else:
            # Standard format: 20231101_20231130
            fmt = "%Y%m%d"
            # Include HHMM if time is not 00:00
            if start_dt.hour != 0 or start_dt.minute != 0:
                fmt = "%Y%m%d%H%M"
            time_str = f"{start_dt.strftime(fmt)}_{end_dt.strftime(fmt)}"

        filepath = self._generate_filename(file_prefix, time_str, directory, add_timestamp)

        # 5. Write to File
        self._write_json_file(data, filepath)
        return filepath

    def export_by_month(self,
                        year: int,
                        month: int,
                        directory: str,
                        time_field: str = "created_at",
                        add_timestamp: bool = False) -> str:
        """
        Exports data for a specific month.
        Filename format example: monthly_2023_11.json
        """
        try:
            # Start of the month
            start_dt = datetime.datetime(year, month, 1, tzinfo=LOCAL_TZ)

            # Handle year rollover for the end date (start of next month)
            if month == 12:
                end_dt = datetime.datetime(year + 1, 1, 1, tzinfo=LOCAL_TZ)
            else:
                end_dt = datetime.datetime(year, month + 1, 1, tzinfo=LOCAL_TZ)

            # Filename identifier: 2023_11
            fname_str = f"{year}_{month:02d}"

            return self.export_by_time_range(
                start_dt, end_dt, directory, time_field, "monthly", add_timestamp, filename_override=fname_str
            )
        except ValueError as e:
            logger.error(f"Invalid year or month: {e}")
            return ""

    def export_by_week(self,
                       year: int,
                       week: int,
                       directory: str,
                       time_field: str = "created_at",
                       add_timestamp: bool = False) -> str:
        """
        Exports data for a specific ISO week.
        Filename format example: weekly_2023_W42.json
        """
        try:
            # Parse ISO year and week to get Monday of that week
            # %G-W%V-%u means ISO Year - ISO Week - Monday
            start_dt = datetime.datetime.strptime(f"{year}-W{week}-1", "%G-W%V-%u").replace(tzinfo=LOCAL_TZ)
            # End date is exactly one week later
            end_dt = start_dt + datetime.timedelta(weeks=1)

            # Filename identifier: 2023_W42
            fname_str = f"{year}_W{week:02d}"

            return self.export_by_time_range(
                start_dt, end_dt, directory, time_field, "weekly", add_timestamp, filename_override=fname_str
            )
        except ValueError as e:
            logger.error(f"Invalid year or week: {e}")
            return ""

    def export_all(self,
                   directory: str,
                   split_by: Optional[str] = None,
                   time_field: str = "created_at",
                   add_timestamp: bool = False) -> List[str]:
        """
        Exports all data found in the collection.

        Args:
            directory: The output directory.
            split_by: 'year', 'month', 'week', or None (single file).
            time_field: The field used to determine the time range.
            add_timestamp: Whether to add a timestamp to the filename.

        Returns:
            List of generated file paths.
        """
        # If no split is requested, export everything into one huge file
        if not split_by:
            # Use extreme dates to cover all possible data
            start_dt = datetime.datetime(1970, 1, 1, tzinfo=LOCAL_TZ)
            # Use current time + buffer as the upper bound
            end_dt = datetime.datetime.now(LOCAL_TZ) + datetime.timedelta(days=1)
            path = self.export_by_time_range(
                start_dt, end_dt, directory, time_field, "all_data", add_timestamp, filename_override="full_dump"
            )
            return [path] if path else []

        # Find the global min and max dates in the DB to determine loop range
        min_doc = self.find_many({}, sort=[(time_field, ASCENDING)], limit=1)
        max_doc = self.find_many({}, sort=[(time_field, DESCENDING)], limit=1)

        if not min_doc or not max_doc:
            logger.warning("No data available to export.")
            return []

        min_date: datetime.datetime = min_doc[0].get(time_field)
        max_date: datetime.datetime = max_doc[0].get(time_field)

        if not isinstance(min_date, datetime.datetime) or not isinstance(max_date, datetime.datetime):
            logger.error(f"Field '{time_field}' is not a valid datetime object in the database.")
            return []

        generated_files = []
        current_date = min_date

        # Iterate based on the requested granularity
        if split_by == 'month':
            # Reset to the 1st of the starting month
            current_date = current_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            while current_date <= max_date:
                path = self.export_by_month(current_date.year, current_date.month, directory, time_field, add_timestamp)
                if path: generated_files.append(path)

                # Move to the next month
                year = current_date.year + (current_date.month // 12)
                month = (current_date.month % 12) + 1
                current_date = current_date.replace(year=year, month=month)

        elif split_by == 'week':
            # Reset to the Monday of the starting week
            iso_year, iso_week, _ = current_date.isocalendar()
            current_date = datetime.datetime.strptime(f"{iso_year}-W{iso_week}-1", "%G-W%V-%u").replace(tzinfo=LOCAL_TZ)

            while current_date <= max_date:
                y, w, _ = current_date.isocalendar()
                path = self.export_by_week(y, w, directory, time_field, add_timestamp)
                if path: generated_files.append(path)
                current_date += datetime.timedelta(weeks=1)

        elif split_by == 'year':
            # Reset to Jan 1st
            current_date = current_date.replace(month=1, day=1, hour=0, minute=0, second=0)
            while current_date <= max_date:
                next_year = current_date.replace(year=current_date.year + 1)
                fname = f"{current_date.year}"
                path = self.export_by_time_range(
                    current_date, next_year, directory, time_field, "yearly", add_timestamp, filename_override=fname
                )
                if path: generated_files.append(path)
                current_date = next_year

        return generated_files


# ----------------------------------------------------------------------------------------------------------------------

def run_test_suite():
    """Comprehensive test suite for MongoDBStorage."""
    print("\n" + "=" * 20 + " Starting MongoDBStorage Test Suite " + "=" * 20)
    print(f"INFO: Using local timezone: {LOCAL_TZ.key if hasattr(LOCAL_TZ, 'key') else LOCAL_TZ}")

    storage = None
    try:
        storage = MongoDBStorage(
            db_name="test_db",
            collection_name="test_collection",
            indexes=[[("event_time", DESCENDING)]]
        )
        storage.collection.delete_many({})

        print("\n--- Testing Timezone Conversions ---")
        inserted_id = _test_timezone_handling(storage)

        print("\n--- Testing Advanced Queries ---")
        _test_advanced_queries(storage, inserted_id)

    except MongoDBError as e:
        print(f"\n[✗] A test failed with a MongoDB error: {e}")
    except Exception as e:
        print(f"\n[✗] An unexpected error occurred during tests: {e}")
        raise
    finally:
        if storage:
            storage.collection.delete_many({})
            storage.close()
        print("\n" + "=" * 20 + " Test Suite Finished " + "=" * 20)


def _test_timezone_handling(storage: MongoDBStorage) -> str:
    """Tests automatic timezone conversions and returns the ID of the created document."""
    # 1. Define a naive time. This is the base for our test.
    naive_time = datetime.datetime(2025, 10, 18, 15, 0, 0)

    # 2. Calculate the single, correct UTC equivalent for this naive time, based on the application's logic.
    # This removes the dependency on the execution environment's timezone from the assertion itself.
    expected_utc_time = naive_time.replace(tzinfo=LOCAL_TZ).astimezone(UTC)

    # 3. For robustness, create another aware time object that represents the exact same moment in time.
    tokyo_tz = ZoneInfo("Asia/Tokyo")
    tokyo_time = expected_utc_time.astimezone(tokyo_tz)

    doc_to_insert = {
        "description": "timezone test",
        "event_time": naive_time,  # Will be converted from local to UTC
        "utc_time": expected_utc_time,  # Should be stored as is
        "tokyo_time": tokyo_time,  # Will be converted from Tokyo time to UTC
    }
    inserted_id = storage.insert(doc_to_insert)
    print(f"[✓] Inserted document with various time types. ID: {inserted_id}")

    # Verify storage (directly query MongoDB to check raw data)
    raw_doc = storage.collection.find_one({"_id": ObjectId(inserted_id)})
    # All three times should have been converted to the SAME UTC timestamp in the database.
    assert raw_doc["event_time"] == expected_utc_time, "Naive time not stored as correct UTC"
    assert raw_doc["utc_time"] == expected_utc_time, "UTC time was altered during storage"
    assert raw_doc["tokyo_time"] == expected_utc_time, "Tokyo time was not correctly converted to UTC for storage"
    print("[✓] Verified all datetimes are stored as UTC.")

    # Verify read (use the class method to check output conversion)
    found_doc = storage.find_one({"_id": inserted_id})
    # The expected local time is simply our original naive time, but now it's timezone-aware.
    local_time_expected = naive_time.replace(tzinfo=LOCAL_TZ)

    assert found_doc is not None, "find_one returned None for a valid ID"
    assert found_doc["event_time"] == local_time_expected, "Stored time did not convert back to local correctly"
    print(f"[✓] Verified datetimes convert to local time on read.")
    return inserted_id


def _test_advanced_queries(storage: MongoDBStorage, base_doc_id: str):
    """Tests count_documents and aggregate methods."""
    # Setup: Insert more data for meaningful aggregation
    docs = [
        {"category": "A", "value": 10, "event_time": datetime.datetime(2025, 10, 1, 10, 0, tzinfo=LOCAL_TZ)},
        {"category": "A", "value": 20, "event_time": datetime.datetime(2025, 10, 5, 10, 0, tzinfo=LOCAL_TZ)},
        {"category": "B", "value": 30, "event_time": datetime.datetime(2025, 11, 1, 10, 0, tzinfo=LOCAL_TZ)},
    ]
    storage.bulk_insert(docs)

    # 1. Test count_documents
    october_start = datetime.datetime(2025, 10, 1, 0, 0, 0, tzinfo=LOCAL_TZ)
    november_start = datetime.datetime(2025, 11, 1, 0, 0, 0, tzinfo=LOCAL_TZ)

    count = storage.count_documents({"event_time": {"$lt": november_start}})
    # Expects 3 docs: the one from timezone test + two from this test
    assert count == 3, f"Expected 3 documents, but found {count}"
    print(f"[✓] count_documents with a date query passed. Found {count} documents.")

    # 2. Test aggregate
    pipeline = [
        {
            "$match": {
                "event_time": {"$gte": october_start},
                "category": {"$exists": True}  # Only include docs with a category
            }
        },
        {"$group": {"_id": "$category", "total_value": {"$sum": "$value"}}},
        {"$sort": {"_id": ASCENDING}}
    ]

    results = storage.aggregate(pipeline)
    assert len(results) == 2, f"Aggregation should return 2 groups, but got {len(results)}"
    assert results[0]['_id'] == 'A' and results[0]['total_value'] == 30
    assert results[1]['_id'] == 'B' and results[1]['total_value'] == 30
    print("[✓] Aggregate with $match, $group, and $sort passed.")
    print(f"    Aggregation result: {results}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    run_test_suite()

