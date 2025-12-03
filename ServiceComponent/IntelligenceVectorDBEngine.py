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
    def __init__(self, vector_db_collection: RemoteCollection):
        self.collection = vector_db_collection

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
                # For now, if it's not ISO, we treat it as invalid
                return None

        # Unknown type
        return None

    def upsert(self, intelligence: ArchivedData, data_type: str):
        """
        Extracts content and metadata from ArchivedData.
        Handles potentially malformed PUB_TIME by omitting the field from metadata.
        """
        # 1. Text Construction
        if data_type == 'summary':
            # Combine Title, Brief, and Text (handle None)
            text_parts = [
                intelligence.EVENT_TITLE,
                intelligence.EVENT_BRIEF,
                intelligence.EVENT_TEXT
            ]
            full_text = "\n\n".join([str(t) for t in text_parts if t and str(t).strip()])
        else:
            full_text = intelligence.RAW_DATA.get('content', '')

        if not full_text:
            return  # Skip empty documents

        # 2. Metadata Extraction
        appendix = intelligence.APPENDIX or {}

        metadata = {
            "uuid": intelligence.UUID,
            "informant": intelligence.INFORMANT,

            # Rating Fields (Default to safe values if missing)
            "max_rate_class": str(appendix.get(APPENDIX_MAX_RATE_CLASS, "")),
            "max_rate_score": float(appendix.get(APPENDIX_MAX_RATE_SCORE, 0.0))
        }

        # 3. Time Handling

        # A. Archived Time (Mandatory & Reliable per definition)
        # We fall back to 0.0 only if the system logic is severely broken (missing key)
        raw_archived_time = appendix.get(APPENDIX_TIME_ARCHIVED)
        archived_ts = self._parse_timestamp_safe(raw_archived_time)
        if archived_ts is not None:
            metadata["archived_timestamp"] = archived_ts
        else:
            # Fallback: Use current ingestion time or 0.0 if strictly required
            metadata["archived_timestamp"] = datetime.datetime.now().timestamp()

        # B. Pub Time (Unreliable)
        # CRITICAL: If parsing fails, we DO NOT add the key to metadata.
        # This ensures that time-range queries will simply ignore this record.
        pub_ts = self._parse_timestamp_safe(intelligence.PUB_TIME)
        if pub_ts is not None:
            metadata["pub_timestamp"] = pub_ts

        # 4. Perform Upsert
        self.collection.upsert(
            doc_id=intelligence.UUID,
            text=full_text,
            metadata=metadata
        )

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
        Semantic search with conditional metadata filtering.
        """
        filters = []

        # 1. Event Period Filter (PUB_TIME)
        # Note: Records without 'pub_timestamp' in metadata will NOT match this filter
        # and thus will be excluded from results, which meets the requirement.
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
