import datetime
import time
import uuid
import queue
import logging
import pymongo
import threading

from attr import dataclass
from typing import Tuple, Optional, Dict
from pymongo.errors import ConnectionFailure
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, retry_if_result, TryAgain

from ServiceComponent.IntelligenceStatisticsEngine import IntelligenceStatisticsEngine
from ServiceComponent.RecommendationManager import RecommendationManager
from prompts import ANALYSIS_PROMPT, SUGGESTION_PROMPT
from Tools.MongoDBAccess import MongoDBStorage
from Tools.OpenAIClient import OpenAICompatibleAPI
from Tools.DateTimeUtility import time_str_to_datetime
from MyPythonUtility.DictTools import check_sanitize_dict
from MyPythonUtility.AdvancedScheduler import AdvancedScheduler
from ServiceComponent.IntelligenceHubDefines import *
from ServiceComponent.IntelligenceCache import IntelligenceCache
from ServiceComponent.IntelligenceQueryEngine import IntelligenceQueryEngine
from ServiceComponent.IntelligenceAnalyzerProxy import analyze_with_ai, aggressive_by_ai, generate_recommendation_by_ai


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class IntelligenceHub:
    @dataclass
    class Error:
        exception: Exception | None = None
        error_list: List[str] = []
        warning_list: List[str] = []

        def __bool__(self):
            return False

    class Exception(Exception):
        def __init__(self, name: str, message: str = '', *args, **kwargs):
            self.name = name
            self.msg = message
            self.args = args
            self.kwargs = kwargs

        def __str__(self):
            return f"[{self.name}]: {self.args}, {self.kwargs}"

    def __init__(self, *,
                 ref_url: str = 'http://locohost:8080',
                 db_vector: Optional[object] = None,
                 db_cache: Optional[MongoDBStorage] = None,
                 db_archive: Optional[MongoDBStorage] = None,
                 db_recommendation: Optional[MongoDBStorage] = None,
                 ai_client: OpenAICompatibleAPI = None):
        """
        Init IntelligenceHub.
        :param ref_url: The reference url for sub-resource url generation.
        :param db_vector: Vector DB for text RAG indexing.
        :param db_cache: The mongodb for caching collected data.
        :param db_archive: The mongodb for archiving processed data.
        :param ai_client: The openai-like client for data processing.
        """

        # ---------------- Parameters ----------------

        self.reference_url = ref_url
        self.vector_db_idx = db_vector
        self.mongo_db_cache = db_cache
        self.mongo_db_archive = db_archive
        self.mongo_db_recommendation = db_recommendation
        self.open_ai_client = ai_client

        # -------------- Queues Related --------------

        self.original_queue = queue.Queue()             # Original intelligence queue
        self.processed_queue = queue.Queue()            # Processed intelligence queue
        self.archived_counter = 0
        self.drop_counter = 0
        self.error_counter = 0

        # --------------- Components ----------------

        self.cache_db_query_engine = IntelligenceQueryEngine(self.mongo_db_cache)
        self.archive_db_query_engine = IntelligenceQueryEngine(self.mongo_db_archive)
        self.archive_db_statistics_engine = IntelligenceStatisticsEngine(self.mongo_db_archive)

        self.scheduler = AdvancedScheduler(logger=logging.getLogger('Scheduler'))
        # TODO: This cache seems to be ugly.
        # self.intelligence_cache = IntelligenceCache(self.mongo_db_archive, 6, 2000, None)       # datetime.timedelta(days=1)

        # self.recommendations = []
        # self.recommendation_data = []       # List of dict: ''
        # self.generating_recommendation = False

        self.recommendations_manager = RecommendationManager(
            query_engine = self.archive_db_query_engine,
            open_ai_client=self.open_ai_client,
            db_storage=self.mongo_db_recommendation
        )

        # ------------------ Loads ------------------

        self._load_vector_db()
        self._load_unarchived_data()
        # self.intelligence_cache.load_cache()

        # ----------------- Threads -----------------

        self.lock = threading.Lock()
        self.shutdown_flag = threading.Event()

        self.analysis_thread = threading.Thread(target=self._ai_analysis_thread, daemon=True)
        self.post_process_thread = threading.Thread(target=self._post_process_worker, daemon=True)

        # ------------------ Tasks ------------------

        self._init_scheduler()
        self._trigger_generate_recommendation()

        logger.info('***** IntelligenceHub init complete *****')

    # ----------------------------------------------------- Setups -----------------------------------------------------

    def _init_scheduler(self):
        self.scheduler.add_hourly_task(
            func=self._do_generate_recommendation,
            task_id=f'generate_recommendation_task',
            use_new_thread=True
        )
        self.scheduler.add_weekly_task(
            func=self._do_export_mongodb_weekly,
            task_id = 'export_mongodb_weekly_task',
            day_of_week='sun',
            use_new_thread=True
        )
        self.scheduler.add_monthly_task(
            func=self._do_export_mongodb_monthly,
            task_id = '_do_export_mongodb_monthly_task',
            day=1,
            use_new_thread=True
        )
        self.scheduler.start_scheduler()

    def _load_vector_db(self):
        if self.vector_db_idx:
            self.vector_db_idx.load()

    def _load_unarchived_data(self):
        """Load unarchived data into a queue, compatible with both old and new archival markers."""
        if not self.mongo_db_cache:
            return

        try:
            # 兼容查询条件：同时支持旧版（顶层__ARCHIVED__）和新版（APPENDIX.__ARCHIVED__）
            query = {
                "$and": [
                    # Old design: Flag is at root level
                    {APPENDIX_ARCHIVED_FLAG: {"$exists": False}},
                    # New design: Flag is under "APPENDIX"
                    {f"APPENDIX.{APPENDIX_ARCHIVED_FLAG}": {"$exists": False}}
                ]
            }

            cursor = self.mongo_db_cache.collection.find(query)
            for doc in cursor:
                doc['_id'] = str(doc['_id'])  # 转换ObjectId
                try:
                    self.original_queue.put(doc, block=True, timeout=5)
                except queue.Full:
                    logger.error("Queue full, failed to add document")
                    break

            logger.info(f'Unarchived data loaded, item count: {self.original_queue.qsize()}')

        except pymongo.errors.PyMongoError as e:
            logger.error(f"Database operation failed: {str(e)}")

    # ----------------------------------------------- Startup / Shutdown -----------------------------------------------

    def startup(self):
        self.analysis_thread.start()
        self.post_process_thread.start()

    def shutdown(self, timeout=10):
        logger.info("Intelligence hub shutting down...")

        # 设置关闭标志
        self.shutdown_flag.set()

        # Clear and persists unprocessed data. Put None to un-block all threads.
        self._clear_queues()

        # 等待工作线程结束
        self.analysis_thread.join(timeout=timeout)
        self.post_process_thread.join(timeout=timeout)

        # 清理资源
        self._cleanup_resources()
        logger.info("Intelligence hub has stopped.")

    # --------------------------------------- Shutdowns ---------------------------------------

    def _clear_queues(self):
        unprocessed = []
        with self.lock:
            while not self.original_queue.empty():
                item = self.original_queue.get()
                unprocessed.append(item)
                self.original_queue.task_done()
        # 保存到文件或数据库
        # self._save_to_file(unprocessed, 'pending_tasks.json')

    def _cleanup_resources(self):
        if self.vector_db_idx:
            self.vector_db_idx.save()

        if self.mongo_db_cache:
            self.mongo_db_cache.close()

        if self.mongo_db_archive:
            self.mongo_db_archive.close()

    # ---------------------------------------------- Statistics and Debug ----------------------------------------------

    @property
    def statistics(self):
        return {
            'original': self.original_queue.qsize(),
            'processed': self.processed_queue.qsize(),
            'archived': self.archived_counter,
            'dropped': self.drop_counter,
            'error': self.error_counter,
        }

    # ------------------------------------------------ Public Functions ------------------------------------------------

    # --------------------------------------- Data Submission ---------------------------------------

    def submit_collected_data(self, data: dict) -> True or Error:
        try:
            if self._check_data_duplication(data, False):
                return IntelligenceHub.Error(error_list=[f"Collected message duplicated {data.get('UUID', '')}."])

            validated_data, error_text = check_sanitize_dict(dict(data), CollectedData)

            return IntelligenceHub.Error(error_list=[error_text]) \
                if error_text else self._enqueue_collected_data(validated_data)

        except Exception as e:
            logger.error(f"Submit collected data API exception: {str(e)}")
            return IntelligenceHub.Error(e, [str(e)])

    def submit_archived_data(self, data: dict) -> True or Error:
        try:
            if self._check_data_duplication(data, False):
                return IntelligenceHub.Error(error_list=[f"Archived message duplicated {data.get('UUID', '')}."])

            validated_data, error_text = check_sanitize_dict(dict(data), ArchivedData)

            return IntelligenceHub.Error(error_list=[error_text]) \
                if error_text else self._enqueue_processed_data(validated_data)

        except Exception as e:
            logger.error(f"Submit archived data API exception: {str(e)}")
            return IntelligenceHub.Error(e, [str(e)])

    # -------------------------------------- Gets and Queries --------------------------------------

    def get_intelligence(self, _uuid: str) -> dict:
        query_engine = self.archive_db_query_engine
        return query_engine.get_intelligence(_uuid)

    def query_intelligence(self,
                           *,
                           db: str = 'archive',
                           period:      Optional[Tuple[datetime.datetime, datetime.datetime]] = None,
                           locations:   Optional[List[str]] = None,
                           peoples:     Optional[List[str]] = None,
                           organizations: Optional[List[str]] = None,
                           keywords: Optional[str] = None,
                           threshold: Optional[int] = 4,
                           skip: Optional[int] = 0,
                           limit: int = 100,
                           ) -> Tuple[List[dict], int]:
        if db == 'cache':
            query_engine = self.cache_db_query_engine
        else:
            query_engine = self.archive_db_query_engine
        result, total = query_engine.query_intelligence(
            period = period, locations = locations, peoples = peoples,
            organizations = organizations, keywords = keywords,
            threshold=threshold, skip=skip, limit=limit)
        return result, total

    def get_intelligence_summary(self) -> Tuple[int, str]:
        query_engine = self.archive_db_query_engine
        summary = query_engine.get_intelligence_summary()
        return summary["total_count"], summary["base_uuid"]

    def aggregate(self, pipeline: list) -> list:
        query_engine = self.archive_db_query_engine
        result = query_engine.aggregate(pipeline)
        return result

    def count_documents(self, _filter) -> int:
        query_engine = self.archive_db_query_engine
        result = query_engine.count_documents(_filter)
        return result

    def get_recommendations(self) -> List[Dict]:
        return self.recommendations_manager.get_latest_recommendation()

    # ------------------------------------------------ Directly Access ------------------------------------------------

    def get_query_engine(self) -> IntelligenceQueryEngine:
        return self.archive_db_query_engine

    def get_statistics_engine(self) -> IntelligenceStatisticsEngine:
        return IntelligenceStatisticsEngine(self.mongo_db_archive)

    # ---------------------------------------------------- Updates -----------------------------------------------------

    def submit_intelligence_manual_rating(self, _uuid: str, rating: dict):
        if not isinstance(rating, dict):
            return IntelligenceHub.Error(error_list=['Invalid rating'])

        self.mongo_db_archive.update(
            { 'UUID': _uuid },
            {f"APPENDIX.{APPENDIX_MANUAL_RATING}": rating})

        return True

    # ---------------------------------------------------- Workers -----------------------------------------------------

    @staticmethod
    def __is_result_an_error(result):
        """Return True if the result is considered an error."""
        return result is None or 'error' in result

    @retry(
        # The wait strategy: start at 1s, multiply by 2 each time, max out at 30s
        wait=wait_exponential(multiplier=1, min=1, max=30),
        # The stop condition: stop after max_retry attempts
        stop=stop_after_attempt(3),
        # The retry condition: retry if an exception occurs OR the result is an error
        retry=(retry_if_exception_type(Exception) | retry_if_result(__is_result_an_error))
    )
    def __robust_analyze_with_ai(self, original_data):
        """
        A robust wrapper for the AI analysis function that will be automatically retried.
        """
        if self.shutdown_flag.is_set():
            raise TryAgain
        result = analyze_with_ai(self.open_ai_client, ANALYSIS_PROMPT, original_data)
        return result

    def _ai_analysis_thread(self):
        if not self.open_ai_client:
            logger.info('**** NO AI API client - Thread QUIT ****')
            return

        # ai_process_max_retry = 3

        while not self.shutdown_flag.is_set():
            try:
                original_data = self.original_queue.get(block=True)
                if not original_data:
                    self.original_queue.task_done()
                    continue
            except queue.Empty:
                continue

            # If there's no UUID...
            if not (original_uuid := str(original_data.get('UUID', '')).strip()):
                original_data['UUID'] = original_uuid = str(uuid.uuid4())

            try:
                # ---------------------- Check Duplication First Avoiding Wasting Token ----------------------

                if self._check_data_duplication(original_data, True):
                    raise IntelligenceHub.Exception('drop', 'Article duplicated')

                # ---------------------------------- AI Analysis with Retry ----------------------------------

                result = self.__robust_analyze_with_ai(original_data)

                # retry = 0
                # result = None
                # # Add retry to get correct answer from AI
                # while retry < ai_process_max_retry and not self.shutdown_flag.is_set():
                #     start_time = time.time()
                #     result = analyze_with_ai(self.open_ai_client, ANALYSIS_PROMPT, original_data)
                #     time_spending = time.time() - start_time
                #
                #     # Cooling down the API limitation.
                #     if time_spending < 1.0:
                #         time.sleep(1)
                #
                #     if 'error' not in result:
                #         break
                #     retry += 1

                if not result or 'error' in result:
                    error_msg = f"AI process error after all retries."
                    raise ValueError(error_msg)

                # if retry:
                #     logger.info(f'Got AI match format answer after {retry} retires.')

                # ----------------------- Check Analysis Result and Fill Other Fields ------------------------

                # If this article has no value. No EVENT_TEXT field.
                if 'EVENT_TEXT' not in result:
                    raise IntelligenceHub.Exception('drop', 'Article has no value')

                # Just user original UUID and Informant. The value from AI can be a reference.

                result['UUID'] = original_uuid
                if original_informant := str(original_data.get('INFORMANT', '')).strip():
                    result['INFORMANT'] = original_informant

                validated_data, error_text = check_sanitize_dict(dict(result), ProcessedData)
                if error_text:
                    raise ValueError(error_text)

                # --------------------------------- AI Aggressive with Retry ---------------------------------

                # TODO: 暂时不做，因为需要考虑的事情太多，且消耗token，后续可以考虑采用小模型实现。
                #
                # history_data_brief = self._get_cached_data_brief()
                # aggressive_result = aggressive_by_ai(self.open_ai_client, AGGRESSIVE_PROMPT, result, history_data_brief)
                #
                # if aggressive_result:
                #     # dict is ordered in python 3.7+
                #     related_intelligence_uuid = next(iter(aggressive_result))
                #     if aggressive_result[related_intelligence_uuid] > 1:
                #         self._add_item_link(related_intelligence_uuid, validated_data['UUID'])
                #         validated_data['APPENDIX'][APPENDIX_PARENT_ITEM] = related_intelligence_uuid

                # -------------------------------- Fill Extra Data and Enqueue --------------------------------

                validated_data['RAW_DATA'] = original_data
                validated_data['SUBMITTER'] = 'Analysis Thread'

                if not self._enqueue_processed_data(validated_data):
                    self.error_counter += 1

            except IntelligenceHub.Exception as e:
                if e.name == 'drop':
                    with self.lock:
                        self.drop_counter += 1
                    self._mark_cache_data_archived_flag(original_uuid, ARCHIVED_FLAG_DROP)
            except Exception as e:
                with self.lock:
                    self.error_counter += 1
                logger.error(f"Analysis error: {str(e)}")
                self._mark_cache_data_archived_flag(original_uuid, ARCHIVED_FLAG_ERROR)
            finally:
                self.original_queue.task_done()

    def _post_process_worker(self):
        while not self.shutdown_flag.is_set():
            try:
                try:
                    data = self.processed_queue.get(block=True)
                    if not data:
                        self.processed_queue.task_done()
                        continue
                except queue.Empty:
                    continue

                # ----------------------- Record the max rate for easier filter -----------------------

                if 'APPENDIX' not in data:
                    data['APPENDIX'] = {}
                rate_dict = data.get('RATE', {'N/A': '0'})
                numeric_rates = {k: int(v) for k, v in rate_dict.items() if k != APPENDIX_MAX_RATE_CLASS_EXCLUDE}
                if numeric_rates:
                    max_key, max_value = max(numeric_rates.items(), key=lambda x: x[1])
                else:
                    max_key, max_value = 'N/A', 0
                data['APPENDIX'][APPENDIX_MAX_RATE_CLASS] = max_key
                data['APPENDIX'][APPENDIX_MAX_RATE_SCORE] = max_value

                # -------------------- Post Process: Archive, Indexing, To RSS, ... --------------------

                try:
                    self._archive_processed_data(data)
                    with self.lock:
                        self.archived_counter += 1
                    self._mark_cache_data_archived_flag(data['UUID'], ARCHIVED_FLAG_ARCHIVED)

                    logger.info(f"Message {data['UUID']} archived.")

                    self._index_archived_data(data)
                    # self._publish_article_to_rss(data)

                    # TODO: Call post processor plugins
                except Exception as e:
                    with self.lock:
                        self.error_counter += 1
                    logger.error(f"Archived fail with exception: {str(e)}")
                    self._mark_cache_data_archived_flag(data['UUID'], ARCHIVED_FLAG_ERROR)
                finally:
                    self.processed_queue.task_done()
            except queue.Empty:
                continue

    # ------------------------------------------------ Scheduled Tasks -------------------------------------------------

    def _do_export_mongodb_weekly(self):
        now = datetime.datetime.now()
        logger.info(f'Export mongodb weekly start at: {now}')
        # TODO: Export mongodb.
        logger.info(f'Export mongodb weekly finished at: {datetime.datetime.now()}')

    def _do_export_mongodb_monthly(self):
        now = datetime.datetime.now()
        logger.info(f'Export mongodb monthly start at: {now}')
        # TODO: Export mongodb.
        logger.info(f'Export mongodb monthly finished at: {datetime.datetime.now()}')

    def _do_generate_recommendation(self):
        now = datetime.datetime.now()
        logger.info(f'Generate recommendation start at: {now}')

        # TODO: Test, so using a wide datetime range.
        # period = (now - datetime.timedelta(days=60), now)
        # period = (now - datetime.timedelta(days=14), now)
        period = (now- datetime.timedelta(hours=24), now)

        self.recommendations_manager.generate_recommendation(period=period, threshold=6, limit=500)
        # self._generate_recommendation(period=period, threshold=6, limit=1000)
        logger.info(f'Generate recommendation finished at: {datetime.datetime.now()}')

    def _trigger_generate_recommendation(self):
        now = datetime.datetime.now()
        logger.info(f'Trigger recommendation generation at: {now}')
        self.scheduler.execute_task('generate_recommendation_task', 2)

    # ------------------------------------------------ Helpers ------------------------------------------------

    # ---------------------------- Before Process ----------------------------

    def _check_data_duplication(self, data: dict, allow_empty_informant: bool) -> bool:
        _uuid = data.get('UUID', '')
        informant = data.get('informant', '')

        if not _uuid.strip():
            raise ValueError('No valid uuid.')

        if not allow_empty_informant and not informant:
            raise ValueError('No valid informant.')

        conditions = { 'UUID': _uuid, 'INFORMANT': informant } if informant else { 'UUID': _uuid }

        query_engine = self.archive_db_query_engine
        exists_record = query_engine.common_query(conditions=conditions, operator="$or")

        return bool(exists_record)

    def _enqueue_collected_data(self, data: dict) -> True or Error:
        del data['token']
        data[APPENDIX_TIME_GOT] = time.time()

        self._cache_original_data(data)
        self.original_queue.put(data)

        return True

    def _enqueue_processed_data(self, data: dict) -> True or Error:
        try:
            ts = datetime.datetime.now()
            article_time = data.get('PUB_TIME', None)

            if article_time and isinstance(article_time, str):
                article_time = time_str_to_datetime(article_time)
            if not isinstance(article_time, datetime.datetime) or article_time > ts:
                article_time = ts

            data['PUB_TIME'] = article_time
            if 'APPENDIX' not in data:
                data['APPENDIX'] = {}
            data['APPENDIX'][APPENDIX_TIME_ARCHIVED] = ts

            self.processed_queue.put(data)

            return True

        except Exception as e:
            self._mark_cache_data_archived_flag(data['UUID'], ARCHIVED_FLAG_ERROR)
            logger.error(f"Enqueue archived data error: {str(e)}")
            return IntelligenceHub.Error(e, [str(e)])

    # ---------------------------- Archive Related ----------------------------

    def _index_archived_data(self, data: dict):
        if self.vector_db_idx:
            self.vector_db_idx.add_text(data['UUID'], data['EVENT_TEXT'])
            # TODO: Decrease save frequency.
            self.vector_db_idx.save()

    def _cache_original_data(self, data: dict):
        try:
            if self.mongo_db_cache:
                self.mongo_db_cache.insert(data)
        except Exception as e:
            logger.error(f'Cache original data fail: {str(e)}')

    def _archive_processed_data(self, data: dict):
        try:
            if self.mongo_db_archive:
                self.mongo_db_archive.insert(data)
                # self.intelligence_cache.encache(data)
        except Exception as e:
            logger.error(f'Archive processed data fail: {str(e)}')

    def _mark_cache_data_archived_flag(self, _uuid: str, archived: bool or str):
        """
        20250530: Extend the archived parameter as str. It can be the following values:
            'T' - True. Archived
            'F' - False. Low value data so not archived
            'E' - Error. We should go back and check the error, then analysis again.
        :param _uuid:
        :param archived:
        :return:
        """
        try:
            if isinstance(archived, bool):
                archived = ARCHIVED_FLAG_ARCHIVED if archived else ARCHIVED_FLAG_DROP
            if self.mongo_db_cache:
                self.mongo_db_cache.update({
                    'UUID': _uuid},
                    {f'APPENDIX.{APPENDIX_ARCHIVED_FLAG}': archived})
        except Exception as e:
            logger.error(f'Mark archived data flag fail: {str(e)}')

    def _add_item_link(self, parent_item_uuid: str, child_item_uuid):
        try:
            if self.mongo_db_archive:
                self.mongo_db_archive.update({
                    'UUID': parent_item_uuid},
                    {"$push": {"APPENDIX.__PARENT_ITEM__": child_item_uuid}}
                )
        except Exception as e:
            logger.error(f'Add item link fail: {str(e)}')

    # def _get_cached_data_brief(self, threshold: int = 6) -> List[dict]:
    #     return self.intelligence_cache.get_cached_data(
    #         filter_func=lambda data: data.get('APPENDIX', {}).get(APPENDIX_MAX_RATE_SCORE, 0) >= threshold,
    #         map_function=lambda data: {
    #             'UUID': data['UUID'],
    #             'EVENT_TITLE': data['EVENT_TITLE'],
    #             'EVENT_BRIEF': data['EVENT_BRIEF'],
    #         }
    #     )

    def _aggressive_intelligence(self, article: dict):
        pass

    # def _generate_recommendation(self,
    #                              period: Optional[Tuple[datetime.datetime, datetime.datetime]] = None,
    #                              threshold: int = 6,
    #                              limit: int = 2000):
    #     with self.lock:
    #         if self.generating_recommendation:
    #             return
    #     try:
    #         with self.lock:
    #             self.generating_recommendation = True
    #
    #         if not period:
    #             period = (datetime.datetime.now() - datetime.timedelta(hours=24), datetime.datetime.now())
    #
    #         query_engine = self.archive_db_query_engine
    #         result, total = query_engine.query_intelligence(archive_period = period, threshold=threshold, limit=limit)
    #
    #         if not result:
    #             return []
    #         if total > limit:
    #             logger.warning(f'Total intelligence ({total}) is larger than limit ({limit}).')
    #
    #         title_brief = [{
    #             'UUID': item['UUID'],
    #             'EVENT_TITLE': item['EVENT_TITLE'],
    #             'EVENT_BRIEF': item['EVENT_BRIEF'],
    #         } for item in result]
    #
    #         recommendation_uuids = generate_recommendation_by_ai(self.open_ai_client, SUGGESTION_PROMPT, title_brief)
    #
    #         if not recommendation_uuids or 'error' in recommendation_uuids:
    #             return []
    #
    #         uuid_set = set(recommendation_uuids)
    #
    #         recommendation_intelligences = [
    #             item for item in result
    #             if item['UUID'] in uuid_set
    #         ]
    #
    #         with self.lock:
    #             self.recommendations = recommendation_intelligences
    #     except Exception as e:
    #         logger.error(f'generate_recommendation exception: {str(e)}')
    #     finally:
    #         with self.lock:
    #             self.generating_recommendation = False
