import datetime
from typing import Optional, Tuple, List, Dict, Any, Union

from VectorDB.VectorDBClient import RemoteCollection
from ServiceComponent.IntelligenceHubDefines import (
    ArchivedData,
    APPENDIX_TIME_ARCHIVED,
    APPENDIX_MAX_RATE_CLASS,
    APPENDIX_MAX_RATE_SCORE
)


class IntelligenceVectorDBEngine:
    def __init__(self, vector_db_collection: RemoteCollection, batch_size: int = 50):
        self.collection = vector_db_collection
        self.batch_size = batch_size
        self._buffer: List[Dict] = []

    def _parse_timestamp_safe(self, time_val: Any) -> Optional[float]:
        """
        Safely attempts to convert various time formats to a float timestamp.
        Returns None if conversion fails.
        """
        if time_val is None:
            return None

        # Case 1: Already numeric (timestamp)
        if isinstance(time_val, (int, float)):
            return float(time_val)

        # Case 2: Datetime object
        if isinstance(time_val, datetime.datetime):
            return time_val.timestamp()

        # Case 3: String Parsing
        if isinstance(time_val, str):
            if not time_val.strip():
                return None
            try:
                # 1. Try standard ISO format (e.g., '2023-01-01T12:00:00')
                return datetime.datetime.fromisoformat(time_val).timestamp()
            except ValueError:
                # 2. Add more formats here if needed (e.g., '%Y-%m-%d')
                return None

        # Unknown type
        return None

    def _prepare_document(self, intelligence: ArchivedData, data_type: str) -> Optional[Dict]:
        """
        Pure function: Prepares the document dictionary for VectorDB.
        ADAPTATION: Adds 'timestamp' for DB clustering compatibility.
        """
        # 1. Text Construction
        if data_type == 'summary':
            text_parts = [
                intelligence.EVENT_TITLE,
                intelligence.EVENT_BRIEF,
                intelligence.EVENT_TEXT
            ]
            full_text = "\n\n".join([str(t) for t in text_parts if t and str(t).strip()])
        else:
            full_text = intelligence.RAW_DATA.get('content', '') if intelligence.RAW_DATA else ''

        if not full_text:
            return None

        # 2. Basic Metadata Extraction
        appendix = intelligence.APPENDIX or {}

        # Parse times first to determine the primary 'timestamp'
        raw_archived_time = appendix.get(APPENDIX_TIME_ARCHIVED)
        archived_ts = self._parse_timestamp_safe(raw_archived_time)
        # Fallback to now() if archived time is missing, ensuring every doc has a timestamp
        if archived_ts is None:
            archived_ts = datetime.datetime.now().timestamp()

        pub_ts = self._parse_timestamp_safe(intelligence.PUB_TIME)

        # 3. Construct Metadata
        metadata = {
            # Business Identifiers
            "uuid": intelligence.UUID,
            "informant": intelligence.INFORMANT,

            # Filtering Fields (Used in query method)
            "archived_timestamp": archived_ts,

            # Rate Scores
            "max_rate_class": str(appendix.get(APPENDIX_MAX_RATE_CLASS, "")),
            "max_rate_score": float(appendix.get(APPENDIX_MAX_RATE_SCORE, 0.0))
        }

        # Optional Pub Time
        if pub_ts is not None:
            metadata["pub_timestamp"] = pub_ts

        # --- CRITICAL ADAPTATION FOR DB ENGINE ---
        # The VectorStorageEngine's default clustering analysis ('analyze_clusters')
        # looks for a specific key "timestamp" to calculate temporal density.
        # We map the most relevant business time (Pub Time) to it, falling back to Archived Time.
        metadata["timestamp"] = pub_ts if pub_ts is not None else archived_ts

        return {
            "doc_id": intelligence.UUID,
            "text": full_text,
            "metadata": metadata
        }

    def upsert(self, intelligence: ArchivedData, data_type: str):
        """
        Upserts a single document.
        """
        doc = self._prepare_document(intelligence, data_type)
        if doc:
            # Unpacking dictionary to match signature: (doc_id, text, metadata)
            self.collection.upsert(**doc)

    def add_to_batch(self, intelligence: ArchivedData, data_type: str):
        doc = self._prepare_document(intelligence, data_type)
        if doc:
            self._buffer.append(doc)

        if len(self._buffer) >= self.batch_size:
            self.commit()

    def commit(self):
        if not self._buffer:
            return

        try:
            self.collection.upsert_batch(self._buffer)
        except Exception as e:
            print(f"Error committing batch: {e}")
        finally:
            self._buffer.clear()

    def query(self,
              text: str,
              top_n: int = 5,
              score_threshold: float = 0.0,
              event_period: Optional[Tuple[datetime.datetime, datetime.datetime]] = None,
              archive_period: Optional[Tuple[datetime.datetime, datetime.datetime]] = None,
              rate_class: Optional[str] = None,
              rate_threshold: Optional[float] = None
              ) -> List[Dict]:
        """
        Semantic search with metadata filtering.
        """
        filters = []

        # 1. Event Period Filter (PUB_TIME)
        if event_period:
            start_ts = event_period[0].timestamp()
            end_ts = event_period[1].timestamp()
            filters.append({
                "pub_timestamp": {"$gte": start_ts, "$lte": end_ts}
            })

        # 2. Archive Period Filter
        if archive_period:
            start_ts = archive_period[0].timestamp()
            end_ts = archive_period[1].timestamp()
            filters.append({
                "archived_timestamp": {"$gte": start_ts, "$lte": end_ts}
            })

        # 3. Rate Class Filter
        if rate_class:
            filters.append({
                "max_rate_class": rate_class
            })

        # 4. Rate Threshold Filter
        if rate_threshold is not None:
            filters.append({
                "max_rate_score": {"$gte": rate_threshold}
            })

        # Construct Where Clause
        where_clause = None
        if len(filters) == 1:
            where_clause = filters[0]
        elif len(filters) > 1:
            where_clause = {"$and": filters}

        # Execute
        results = self.collection.search(
            query=text,
            top_n=top_n,
            score_threshold=score_threshold,
            filter_criteria=where_clause
        )

        return results
