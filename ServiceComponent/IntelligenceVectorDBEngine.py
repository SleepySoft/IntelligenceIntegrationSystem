import datetime
import logging
from functools import lru_cache
from typing import Optional, Tuple, List, Dict, Any

from VectorDB.VectorDBClient import RemoteCollection
from ServiceComponent.event_v4_archive import ArchivedIntelligenceV4

logger = logging.getLogger(__name__)


class IntelligenceVectorDBEngine:
    """
    Event V4 intelligence vectorization and search wrapper.
    """

    def __init__(self, vector_db_collection: RemoteCollection, batch_size: int = 50,
                 query_cache_size: int = 256):
        self.collection = vector_db_collection
        self.batch_size = batch_size
        self._buffer: List[Dict] = []
        # Cache at the business-engine level as well: the same query may hit both
        # summary and full-text collections, and may be repeated across pages.
        self._query_cache = lru_cache(maxsize=query_cache_size)(self._raw_query)

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

    def _prepare_document(self, intelligence: ArchivedIntelligenceV4,
                          data_type: str) -> Optional[Dict]:
        if data_type == 'summary':
            text_parts = [
                intelligence.analysis.message.title,
                intelligence.analysis.message.brief,
                intelligence.analysis.message.text,
            ]
            full_text = "\n\n".join([str(t) for t in text_parts if t and str(t).strip()])
        else:
            full_text = str(intelligence.raw_data.get('content', '') or '')

        if not full_text:
            logger.warning(
                "Empty text for intelligence %s, skipping vectorization.",
                intelligence.intelligence_uuid)
            return None

        pub_ts = self._parse_timestamp_safe(intelligence.raw_data.get('pub_time'))
        archived_ts = self._parse_timestamp_safe(intelligence.archived_at)
        identifier = str(intelligence.intelligence_uuid)
        metadata = {
            "uuid": identifier,
            "informant": intelligence.informant,
            "archived_timestamp": archived_ts,
            "total_score": float(intelligence.total_score),
            "subsystem": intelligence.subsystem,
            "schema_version": intelligence.schema_version,
            "timestamp": pub_ts if pub_ts is not None else archived_ts
        }

        if pub_ts is not None:
            metadata["pub_timestamp"] = pub_ts

        return {
            "doc_id": identifier,
            "text": full_text,
            "metadata": metadata
        }

    def upsert(self, intelligence: ArchivedIntelligenceV4, data_type: str,
               timeout: float = 120):
        doc = self._prepare_document(intelligence, data_type)
        if doc:
            self.collection.upsert(**doc, timeout=timeout)

    def add_to_batch(self, intelligence: ArchivedIntelligenceV4, data_type: str,
                     timeout: float = 120):
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

    def _raw_query(self,
                   text: str,
                   top_n: int = 5,
                   score_threshold: float = 0.0,
                   event_period: Optional[Tuple[datetime.datetime, datetime.datetime]] = None,
                   archive_period: Optional[Tuple[datetime.datetime, datetime.datetime]] = None,
                   rate_threshold: Optional[float] = None,
                   timeout: int = 30,
                   force_db_filter: bool = False,
                   post_filter_multiplier: int = 10,
                   ) -> List[Dict]:
        """
        Internal query implementation. Use self.query() to take advantage of LRU cache.
        """
        filters = []

        # NOTE: The timestamp and pub_timestamp fields are int type. But archived_timestamp field is float.
        if event_period:
            filters.append({
                "timestamp": {"$gte": int(event_period[0].timestamp())}
            })
            filters.append({
                "timestamp": {"$lte": int(event_period[1].timestamp())}
            })

        if archive_period:
            filters.append({"archived_timestamp": {"$gte": archive_period[0].timestamp()}})
            filters.append({"archived_timestamp": {"$lte": archive_period[1].timestamp()}})

        if rate_threshold is not None:
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
            filter_criteria=where_clause,
            timeout=timeout,
            force_db_filter=force_db_filter,
            post_filter_multiplier=post_filter_multiplier,
        )

    def query(self,
              text: str,
              top_n: int = 5,
              score_threshold: float = 0.0,
              event_period: Optional[Tuple[datetime.datetime, datetime.datetime]] = None,
              archive_period: Optional[Tuple[datetime.datetime, datetime.datetime]] = None,
              rate_threshold: Optional[float] = None,
              timeout: int = 30,
              force_db_filter: bool = False,
              post_filter_multiplier: int = 10,
              ) -> List[Dict]:
        """
        Query Event V4 metadata fields.
        Caches results by (text, top_n, score_threshold, periods, rate filters,
        force_db_filter, post_filter_multiplier).
        """
        return self._query_cache(
            text,
            top_n,
            score_threshold,
            event_period,
            archive_period,
            rate_threshold,
            timeout,
            force_db_filter,
            post_filter_multiplier,
        )

    @staticmethod
    def build_search_text(intelligence_dict: Dict[str, Any], data_type: str = 'summary') -> str:
        """Build search text from a serialized Event V4 archive envelope."""
        if data_type == 'summary':
            message = (intelligence_dict.get('analysis') or {}).get('message') or {}
            text_parts = [
                message.get('title', ''),
                message.get('brief', ''),
                message.get('text', ''),
            ]
            full_text = "\n\n".join([str(t) for t in text_parts if t and str(t).strip()])
        else:
            raw = intelligence_dict.get('raw_data') or {}
            full_text = raw.get('content', '')

        return full_text
