import datetime
import logging
from typing import Optional, Tuple, List, Dict, Any

from VectorDB.VectorDBClient import RemoteCollection
from ServiceComponent.IntelligenceHubDefines_v2 import (
    ArchivedData,
    APPENDIX_TOTAL_SCORE,
    APPENDIX_TIME_ARCHIVED,
    APPENDIX_TIME_PUB,  # Added for v2 compatibility
)

logger = logging.getLogger(__name__)


class IntelligenceVectorDBEngine:
    """
    Business logic wrapper for VectorDB, compatible with both v1 and v2 schemas.
    """

    def __init__(self, vector_db_collection: RemoteCollection, batch_size: int = 50):
        self.collection = vector_db_collection
        self.batch_size = batch_size
        self._buffer: List[Dict] = []

    def _parse_timestamp_safe(self, time_val: Any) -> Optional[float]:
        if time_val is None:
            return None
        if isinstance(time_val, (int, float)):
            return float(time_val)
        if isinstance(time_val, datetime.datetime):
            return time_val.timestamp()
        if isinstance(time_val, str):
            if not time_val.strip():
                return None
            try:
                return datetime.datetime.fromisoformat(time_val.replace('Z', '+00:00')).timestamp()
            except ValueError:
                return None
        return None

    def _prepare_document(self, intelligence: ArchivedData, data_type: str) -> Optional[Dict]:
        """
        Transforms ArchivedData to VectorDB dict with v1/v2 compatibility.
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
            # Compatibility: v2 uses RAW_DATA['content'], v1 might have content elsewhere or raw
            raw = intelligence.RAW_DATA or {}
            full_text = raw.get('content', '') or getattr(intelligence, 'content', '')

        if not full_text:
            logger.warning(f"Empty text for UUID {intelligence.UUID}, skipping vectorization.")
            return None

        # 2. Advanced Time Compatibility (v1 vs v2)
        appendix = intelligence.APPENDIX or {}

        # Determine Publish Time (v1: root.PUB_TIME | v2: appendix.__TIME_PUB__)
        raw_pub_time = getattr(intelligence, 'PUB_TIME', None)  # Try v1 root field
        if raw_pub_time is None:
            raw_pub_time = appendix.get(APPENDIX_TIME_PUB)  # Try v2 appendix field

        pub_ts = self._parse_timestamp_safe(raw_pub_time)

        # Determine Archive Time
        raw_archived_time = appendix.get(APPENDIX_TIME_ARCHIVED)
        archived_ts = self._parse_timestamp_safe(raw_archived_time) or datetime.datetime.now().timestamp()

        # 3. Score Compatibility (v1: MAX_RATE_SCORE | v2: TOTAL_SCORE)
        # We try v2 key first, then fallback to v1 specific key if present in appendix
        total_score = appendix.get(APPENDIX_TOTAL_SCORE)
        if total_score is None:
            total_score = appendix.get('__MAX_RATE_SCORE__', 0.0)  # Fallback to v1 string key

        # 4. Metadata Construction
        metadata = {
            "uuid": intelligence.UUID,
            "informant": intelligence.INFORMANT,
            "archived_timestamp": archived_ts,
            "total_score": float(total_score) if total_score else 0.0,
            # 'timestamp' is the primary key for temporal analysis in the DB engine
            "timestamp": pub_ts if pub_ts is not None else archived_ts
        }

        if pub_ts is not None:
            metadata["pub_timestamp"] = pub_ts

        # Optional: v1 Rate Class compatibility
        rate_class = appendix.get('__MAX_RATE_CLASS__')
        if rate_class:
            metadata["max_rate_class"] = str(rate_class)

        return {
            "doc_id": intelligence.UUID,
            "text": full_text,
            "metadata": metadata
        }

    def upsert(self, intelligence: ArchivedData, data_type: str, timeout: float = 120):
        doc = self._prepare_document(intelligence, data_type)
        if doc:
            self.collection.upsert(**doc, timeout=timeout)

    def add_to_batch(self, intelligence: ArchivedData, data_type: str, timeout: float = 120):
        doc = self._prepare_document(intelligence, data_type)
        if doc:
            self._buffer.append(doc)
        if len(self._buffer) >= self.batch_size:
            self.commit(timeout)

    def commit(self, timeout: float = 120):
        if not self._buffer:
            return
        try:
            self.collection.upsert_batch(self._buffer, timeout=timeout)
        except Exception as e:
            logger.error(f"Error committing batch: {e}")
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
        Query with support for both v1 and v2 metadata fields.
        """
        filters = []

        if event_period:
            filters.append({
                "pub_timestamp": {"$gte": event_period[0].timestamp(), "$lte": event_period[1].timestamp()}
            })

        if archive_period:
            filters.append({
                "archived_timestamp": {"$gte": archive_period[0].timestamp(), "$lte": archive_period[1].timestamp()}
            })

        if rate_class:
            filters.append({"max_rate_class": rate_class})

        if rate_threshold is not None:
            # Query against total_score (v2) or fallback logic in DB
            filters.append({"total_score": {"$gte": rate_threshold}})

        where_clause = None
        if len(filters) == 1:
            where_clause = filters[0]
        elif len(filters) > 1:
            where_clause = {"$and": filters}

        return self.collection.search(
            query=text,
            top_n=top_n,
            score_threshold=score_threshold,
            filter_criteria=where_clause
        )
