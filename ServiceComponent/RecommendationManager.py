import logging
import datetime
import random
import threading
import time
import traceback
from collections import Counter
from dataclasses import dataclass, asdict
from typing import Optional, Tuple, List, Dict

from prompts import SUGGESTION_PROMPT
from Tools.MongoDBAccess import MongoDBStorage
from AIClientCenter.AIClientManager import AIClientManager
from Tools.DateTimeUtility import get_aware_time, ensure_timezone_aware
from ServiceComponent.IntelligenceQueryEngine import IntelligenceQueryEngine
from ServiceComponent.IntelligenceAnalyzerProxy import generate_recommendation_by_ai

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class RecommendationManager:
    """
    Manages the generation, storage, and analysis of intelligence recommendations.
    This class is thread-safe.
    """

    @dataclass
    class RecommendationData:
        recommendations: List[Dict]  # List of intelligence dicts
        generated_datetime: datetime.datetime  # The datetime when this recommendation was generated
        candidate_intelligences: List[str]  # List of UUID strings for all candidate intelligences

    def __init__(self,
                 query_engine: IntelligenceQueryEngine,
                 ai_client_manager: AIClientManager,
                 db_storage: MongoDBStorage
                 ):
        self.query_engine = query_engine
        self.ai_client_manager = ai_client_manager
        self.db = db_storage  # NOTE: Assuming `db_storage` is an object representing a specific MongoDB collection.

        self.recommendations_cache: List[RecommendationManager.RecommendationData] = []

        self.generating = False
        self.lock = threading.Lock()

        self._load_initial_recommendations()

    def _load_initial_recommendations(self):
        """Loads recommendations from the last 48 hours from the DB into the in-memory cache."""
        try:
            logger.info("Loading initial recommendations from the last 48 hours...")
            start_time = get_aware_time() - datetime.timedelta(hours=48)

            query = {"generated_datetime": {"$gte": start_time}}

            # NOTE: Verify this call. If `self.db` is a client, you may need:
            # result = self.db.get_collection("recommendations").find(query)
            result = self.db.find_many(query)

            loaded_recommendations = []
            for doc in result:
                doc.pop('_id', None)
                loaded_recommendations.append(self.RecommendationData(**doc))

            with self.lock:
                self.recommendations_cache = loaded_recommendations

            logger.info(f"Successfully loaded {len(self.recommendations_cache)} recommendation sets into cache.")
        except Exception as e:
            print(traceback.format_exc())
            logger.error(f"Failed to load initial recommendations: {e}", exc_info=True)

    def count_intelligence(self, period: Tuple[datetime.datetime, datetime.datetime], limit: int = 10000) -> Dict[
        str, int]:
        """
        Counts the occurrences of each intelligence UUID within the recommended content for a given period.
        A higher count suggests greater potential importance.
        """
        start_time, end_time = period
        all_recommended_uuids = []

        with self.lock:
            # Filter recommendation sets that fall within the specified period
            relevant_sets = [
                rec_set for rec_set in self.recommendations_cache
                if start_time <= rec_set.generated_datetime < end_time
            ]

        # Extract all UUIDs from the 'recommendations' list of each relevant set
        for rec_set in relevant_sets:
            for intelligence in rec_set.recommendations:
                if 'UUID' in intelligence:
                    all_recommended_uuids.append(intelligence['UUID'])

        if not all_recommended_uuids:
            return {}

        # Count occurrences of each UUID
        uuid_counts = Counter(all_recommended_uuids)

        # Return the top N most common UUIDs based on the limit
        most_common_uuids = uuid_counts.most_common(limit)

        return dict(most_common_uuids)

    def get_latest_recommendation(self) -> List[Dict]:
        """Gets the latest list of recommended intelligences from the in-memory cache."""
        with self.lock:
            if not self.recommendations_cache:
                return []
            # The cache is sorted, so the last element is the newest
            return self.recommendations_cache[-1].recommendations

    def generate_recommendation(self,
                                period: Optional[Tuple[datetime.datetime, datetime.datetime]] = None,
                                threshold: int = 6,
                                limit: int = 2000) -> bool:
        """
        Executes the recommendation generation process and saves the result to the DB and cache.
        This function is designed to be called by a scheduled task (e.g., hourly) or triggered manually.
        """
        with self.lock:
            if self.generating:
                logger.warning("Recommendation generation is already in progress. Skipping this run.")
                return False
            self.generating = True

        try:
            generation_time = get_aware_time().replace(minute=0, second=0)

            if isinstance(period, (list, tuple)):
                period = [ensure_timezone_aware(dt) for dt in period]
            else:
                period = (generation_time - datetime.timedelta(hours=24), generation_time)

            logger.info(f"Starting recommendation generation for period: {period[0]} to {period[1]}")

            result, total = self.query_engine.query_intelligence(archive_period=period, threshold=threshold,
                                                                 limit=limit)
            if not result:
                logger.info("No intelligence data found for the given period. Nothing to recommend.")
                return False

            if total > limit:
                logger.warning(f'Total intelligence ({total}) is larger than limit ({limit}).')

            title_brief = [
                {'UUID': item['UUID'], 'EVENT_TITLE': item['EVENT_TITLE'], 'EVENT_BRIEF': item['EVENT_BRIEF']} for item
                in result]

            retries = 0
            while True:
                if ai_client := self.ai_client_manager.get_available_client('RecommendationManager'):
                    recommendation_uuids = generate_recommendation_by_ai(ai_client, SUGGESTION_PROMPT, title_brief)
                    # Because recommendation period is very long. Release it for other task using.
                    self.ai_client_manager.release_client(ai_client)
                    break
                retries += 1
                if retries % 10 == 0:
                    logger.warning(f"Recommendation thread {threading.current_thread().name} waiting for AI client for {retries}s...")
                time.sleep(1 + random.random() * 0.5)
            if retries:
                logger.info(f"Recommendation tries to get AI client for {retries} times.")

            if not recommendation_uuids or 'error' in recommendation_uuids:
                logger.error(f"Failed to get recommendation from AI: {recommendation_uuids}")
                return False

            uuid_set = set(recommendation_uuids)
            recommendation_intelligences = [item for item in result if item['UUID'] in uuid_set]

            # Encapsulate the result into a RecommendationData object
            new_recommendation = self.RecommendationData(
                recommendations=recommendation_intelligences,
                generated_datetime=generation_time,
                candidate_intelligences=[item['UUID'] for item in result]
            )

            self._save_and_cache_recommendation(new_recommendation)

            return True

        except Exception as e:
            print(traceback.format_exc())
            logger.error(f'An exception occurred during generate_recommendation: {e}', exc_info=True)
            return False

        finally:
            with self.lock:
                self.generating = False

    def _save_and_cache_recommendation(self, recommendation_data: RecommendationData):
        """Saves recommendation data to the database and updates the in-memory cache."""
        # Use generated_datetime as the unique key for idempotency.
        query_key = {"generated_datetime": recommendation_data.generated_datetime}

        # Convert the dataclass object to a dictionary for MongoDB storage.
        update_data = asdict(recommendation_data)

        try:
            # Use update_one + upsert=True to update the record if it exists, or insert it if it doesn't.
            # NOTE: Verify this call. If `self.db` is a client, you may need:
            # self.db.get_collection("recommendations").update_one(query_key, update_data, upsert=True)
            self.db.update(query_key, update_data, upsert=True)
            logger.info(f"Successfully saved recommendation for {recommendation_data.generated_datetime} to database.")

            # Update the in-memory cache
            with self.lock:
                # Remove any existing record for the same hour (in case of a manual re-run).
                self.recommendations_cache = [r for r in self.recommendations_cache if
                                              r.generated_datetime != recommendation_data.generated_datetime]

                # Add the new record and keep the cache sorted.
                self.recommendations_cache.append(recommendation_data)
                self.recommendations_cache.sort(key=lambda r: r.generated_datetime)

                # (Optional) Prune the cache to only keep records from the last 48 hours.
                forty_eight_hours_ago = get_aware_time() - datetime.timedelta(hours=48)
                self.recommendations_cache = [r for r in self.recommendations_cache if
                                              r.generated_datetime >= forty_eight_hours_ago]

        except Exception as e:
            logger.error(f"Failed to save recommendation data to database or cache: {e}", exc_info=True)
