import datetime
import random
import time
import traceback
import uuid
import queue
import logging
import pymongo
import threading

from attr import dataclass
from typing import Tuple, Optional, Dict, Union
from pymongo.errors import ConnectionFailure
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, retry_if_result, TryAgain

from AIClientCenter.AIClientManager import AIClientManager
from ServiceComponent.IntelligenceStatisticsEngine import IntelligenceStatisticsEngine
from ServiceComponent.RecommendationManager import RecommendationManager
from VectorDB.VectorDBService import VectorDBService, VectorStoreManager
from prompts import ANALYSIS_PROMPT, SUGGESTION_PROMPT
from Tools.MongoDBAccess import MongoDBStorage
from Tools.DateTimeUtility import time_str_to_datetime, get_aware_time, Clock
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
                 vector_db_service: Optional[VectorDBService] = None,
                 db_cache: Optional[MongoDBStorage] = None,
                 db_archive: Optional[MongoDBStorage] = None,
                 db_recommendation: Optional[MongoDBStorage] = None,
                 ai_client_manager: AIClientManager = None):
        """
        Init IntelligenceHub.
        :param ref_url: The reference url for sub-resource url generation.
        :param self.vector_db_service: Vector DB for text RAG indexing.
        :param db_cache: The mongodb for caching collected data.
        :param db_archive: The mongodb for archiving processed data.
        :param db_recommendation: The mongodb for storing recommendation data.
        :param ai_client: The openai-like client for data processing.
        """

        # ---------------- Parameters ----------------

        self.reference_url = ref_url
        self.vector_db_service = vector_db_service
        self.mongo_db_cache = db_cache
        self.mongo_db_archive = db_archive
        self.mongo_db_recommendation = db_recommendation
        self.ai_client_manager = ai_client_manager

        # -------------- Queues Related --------------

        self.original_queue = queue.Queue()             # Original intelligence queue
        self.processed_queue = queue.Queue()            # Processed intelligence queue
        self.archived_counter = 0
        self.drop_counter = 0
        self.error_counter = 0

        self.conversation_warning = 0
        self.conversation_error = 0
        self.conversation_total = 0

        # --------------- Components ----------------

        self.cache_db_query_engine = IntelligenceQueryEngine(self.mongo_db_cache)
        self.archive_db_query_engine = IntelligenceQueryEngine(self.mongo_db_archive)
        self.archive_db_statistics_engine = IntelligenceStatisticsEngine(self.mongo_db_archive)

        self.vector_db_summary: Optional[VectorStoreManager] = None
        self.vector_db_full_text: Optional[VectorStoreManager] = None

        self.scheduler = AdvancedScheduler(logger=logging.getLogger('Scheduler'))
        # TODO: This cache seems to be ugly.
        # self.intelligence_cache = IntelligenceCache(self.mongo_db_archive, 6, 2000, None)       # datetime.timedelta(days=1)

        self.recommendations_manager = RecommendationManager(
            query_engine = self.archive_db_query_engine,
            ai_client_manager=self.ai_client_manager,
            db_storage=self.mongo_db_recommendation
        )

        # ------------------ Loads ------------------

        self._load_vector_db()
        self._load_unarchived_data()
        # self.intelligence_cache.load_cache()

        # ----------------- Threads -----------------

        self.lock = threading.Lock()
        self.shutdown_flag = threading.Event()

        # self.analysis_thread = threading.Thread(name='AnalysisThread', target=self._ai_analysis_thread, daemon=True)
        self.post_process_thread = threading.Thread(name='PostProcessThread', target=self._post_process_worker, daemon=True)

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
        pass

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
        # self.analysis_thread.start()
        self.start_analysis_threads(3)
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

    def start_analysis_threads(self, thread_count):
        for i in range(thread_count):
            t = threading.Thread(target=self._ai_analysis_worker, name=f"AI-Worker-{i}", daemon=True, args=(i,))
            t.start()
        logger.info(f"Started {thread_count} AI analysis threads.")

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
        if self.mongo_db_cache:
            self.mongo_db_cache.close()

        if self.mongo_db_archive:
            self.mongo_db_archive.close()

    # ---------------------------------------------- Statistics and Debug ----------------------------------------------

    @property
    def statistics(self):
        return {
            'waiting_process': self.original_queue.qsize(),
            'post_process': self.processed_queue.qsize(),
            'archived': self.archived_counter,
            'dropped': self.drop_counter,
            'error': self.error_counter,
            'conversation_warning': self.conversation_warning,
            'conversation_error': self.conversation_error ,
            'conversation_total': self.conversation_total ,
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

    def get_intelligence(self,
                         _uuid: Union[str, List[str]],
                         db: str = 'archive'
                         ) -> dict:
        if db == 'cache':
            query_engine = self.cache_db_query_engine
        else:
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

    def vector_search_intelligence(self,
                                   text: str,
                                   in_summary: bool = True,
                                   in_fulltext: bool = False,
                                   top_n: int = 10,
                                   score_threshold: float = 0.5) -> List[Tuple[str, float, str]]:
        summary_result = []
        fulltext_result = []

        if in_summary and self.vector_db_summary:
            summary_result = self.vector_db_summary.search(text, top_n, score_threshold)
        if in_fulltext and self.vector_db_full_text:
            fulltext_result = self.vector_db_full_text.search(text, top_n, score_threshold)

        combined_results = summary_result + fulltext_result

        best_records = {}
        for result in combined_results:
            doc_id = result["doc_id"]
            score = result["score"]

            if doc_id not in best_records or score > best_records[doc_id][0]:
                best_records[doc_id] = (score, result["chunk_text"])

        # [(doc_id, score, chunk_text)]
        result_list = [(doc_id, record[0], record[1]) for doc_id, record in best_records.items()]

        return result_list

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
    def __robust_analyze_with_ai(self, original_data: dict, worker_index: int):
        """
        A robust wrapper for the AI analysis function that will be automatically retried.
        """
        if self.shutdown_flag.is_set():
            return None
        prefix = f'AI Worker [{worker_index}]'
        client_user = f'IntelligenceHub-{worker_index}'

        # --------------------------- Wait until one AI client available ---------------------------

        retries = 0
        while True:
            if ai_client := self.ai_client_manager.get_available_client(client_user):
                result = analyze_with_ai(ai_client, ANALYSIS_PROMPT, original_data)
                break
            retries += 1
            if retries % 10 == 0:
                logger.warning(f"{prefix} Thread {threading.current_thread().name} waiting for AI client for {retries}s...")
            time.sleep(1 + random.random() * 0.5)
        if retries:
            logger.info(f"{prefix} Analysis tries to get AI client for {retries} times.")

        # ------------------------------------------------------------------------------------------

        # Check warning and error for statistics
        if 'error' in result:
            self.conversation_error += 1
        elif 'warning' in result:
            self.conversation_warning += 1
        self.conversation_total += 1

        return result

    def _ai_analysis_worker(self, worker_index: int = 0):
        prefix = f'AI Worker [{worker_index}]'

        if not self.ai_client_manager:
            logger.info(f'{prefix} **** NO AI API client - Thread QUIT ****')
            return

        # ai_process_max_retry = 3

        while not self.shutdown_flag.is_set():
            original_uuid = None

            try:
                original_data = self.original_queue.get(block=True, timeout=1)
            except queue.Empty:
                continue

            try:
                # If there's no UUID...
                if not (original_uuid := str(original_data.get('UUID', '')).strip()):
                    original_data['UUID'] = original_uuid = str(uuid.uuid4())

                # ---------------------- Check Duplication First Avoiding Wasting Token ----------------------

                if self._check_data_duplication(original_data, True):
                    raise IntelligenceHub.Exception('drop', 'Article duplicated')

                # ---------------------------------- AI Analysis with Retry ----------------------------------

                result = self.__robust_analyze_with_ai(original_data, worker_index)

                if not result or 'error' in result:
                    error_msg = f"AI process error after all retries."
                    raise ValueError(error_msg)

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
                # TODO: 20251028 - 绝妙的主意：使用向量搜索来查找近似内容，减少聚合分析的工作量。
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
                    with self.lock:
                        self.error_counter += 1

            except IntelligenceHub.Exception as e:
                if e.name == 'drop':
                    with self.lock:
                        self.drop_counter += 1
                    self._mark_cache_data_archived_flag(original_uuid, ARCHIVED_FLAG_DROP)
            except Exception as e:
                with self.lock:
                    self.error_counter += 1
                logger.error(f"{prefix} Analysis error: {str(e)}")
                self._mark_cache_data_archived_flag(original_uuid, ARCHIVED_FLAG_ERROR)
            finally:
                self.original_queue.task_done()

    def _post_process_worker(self):
        # ---------------- If a vector database is specified, wait until it is ready ----------------

        if self.vector_db_service is not None:
            logger.info('Waiting for vector DB init...')
            clock = Clock()
            while not self.shutdown_flag.is_set():
                status = self.vector_db_service.get_status().get('status', '')
                if status == "ready":
                    self.vector_db_summary = self.vector_db_service.get_store(VECTOR_DB_SUMMARY)
                    self.vector_db_full_text = self.vector_db_service.get_store(VECTOR_DB_FULL_TEXT)
                    logger.info(f'Vector DB init successful. Time spending: {clock.elapsed_ms()}ms.')
                elif status == 'error':
                    self.vector_db_service = None
                    # self.vector_db_summary and self.vector_db_full_text are also None
                    logger.error(f'Vector DB init fail. Time spending: {clock.elapsed_s()}s.')
                else:
                    time.sleep(1)
                    continue
                break

        # -------------------------------------- Post process loop --------------------------------------

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

                # ------------------------------- Post Process: Indexing -------------------------------

                if self.vector_db_service:
                    clock = Clock()

                    _uuid = data.get('UUID')

                    if self.vector_db_summary is not None:
                        title = data.get('EVENT_TITLE', '') or ''
                        brief = data.get('EVENT_BRIEF', '') or ''
                        text = data.get('EVENT_TEXT', '') or ''

                        text_summary = f"{title}\n{brief}\n{text}".strip()
                        if text_summary:
                            self.vector_db_summary.add_document(text_summary, _uuid)

                    if self.vector_db_full_text is not None:
                        raw_data = data.get('RAW_DATA', {}).get('content')
                        if raw_data:
                            self.vector_db_full_text.add_document(str(raw_data), _uuid)

                    logger.debug(f"Message {data['UUID']} vectorized, time-spending: {clock.elapsed_ms()} ms")

                # ------------------ Post Process: Archive, To RSS (deprecated), ... -------------------

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

                # ---------------------------------------------------------------------------------------
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
            print(traceback.format_exc())
            return IntelligenceHub.Error(e, [str(e)])

    # ---------------------------- Archive Related ----------------------------

    def _index_archived_data(self, data: dict):
        pass

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

    def _aggressive_intelligence(self, article: dict):
        pass
