import queue
import logging
import threading

from abc import ABC, abstractmethod
from typing import Callable

from ServiceComponent.IntelligenceHubDefines_v2 import ArchivedData
from ServiceComponent.IntelligenceVectorDBEngine import IntelligenceVectorDBEngine
from Tools.DateTimeUtility import Clock
from VectorDB.VectorDBClient import VectorDBClient


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# -------------------------------------------- Post Process --------------------------------------------

class IHubPostProcess(ABC):
    """Abstract base class defining the interface for data processing."""

    @abstractmethod
    def process_data(self, data: dict):
        """Process the given data. Must be implemented by subclasses."""
        raise NotImplementedError("Subclasses must implement process_data method")


class AsyncHubPostProcess(IHubPostProcess):
    """Asynchronous data processor that processes data in a separate thread."""

    def __init__(self, init_func: Callable[[], None], process_func: Callable[[dict], None]):
        """
        Initialize the async processor.

        Args:
            init_func: Function to run once when the worker thread starts.
            process_func: Function to process each item from the queue.
        """
        self._init_func = init_func
        self._process_func = process_func
        self.queue = queue.Queue()
        self.running = False
        self.thread = None
        self._start_processing_thread()

    def _start_processing_thread(self):
        """Start the background processing thread."""
        self.running = True
        self.thread = threading.Thread(target=self._do_process_data, daemon=True)
        self.thread.start()

    def process_data(self, data: dict):
        """
        Put data in queue for asynchronous processing.

        Args:
            data: Dictionary containing data to be processed
        """
        if not self.running:
            logger.warning("Attempting to process data while processor is not running.")
        self.queue.put(data)

    def _do_process_data(self):
        """Background thread function that processes data from the queue."""
        try:
            if self._init_func:
                self._init_func()
        except Exception as e:
            logger.critical(f"Initialization failed in background thread: {e}", exc_info=True)
            self.running = False  # 标记停止，避免假死
            return

        while self.running:
            try:
                # Get data from queue with timeout to allow checking running flag
                data = self.queue.get(timeout=1.0)
            except queue.Empty:
                continue

            try:
                # Process data using the provided processor
                if self._process_func:
                    self._process_func(data)
            except Exception as e:
                # 捕获处理逻辑本身的错误，不要让线程崩溃
                logger.error(f"Error processing data item: {e}", exc_info=True)
            finally:
                # Ensure task is marked done even if processing failed
                self.queue.task_done()

    def stop_processing(self):
        """Stop the background processing thread."""
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5.0)

    def wait_for_completion(self):  # 移除了不支持的 timeout 参数
        """
        Wait for all queued tasks to be processed.
        Note: This blocks until the queue is empty.
        """
        self.queue.join()


class PostProcessVectorize(AsyncHubPostProcess):
    def __init__(
            self,
            vector_db_engine_summary: IntelligenceVectorDBEngine,
            vector_db_engine_full_text: IntelligenceVectorDBEngine
    ):
        # 先赋值，防止线程启动时访问 self.vector_db_client 报错
        self.vector_db_engine_summary = vector_db_engine_summary
        self.vector_db_engine_full_text = vector_db_engine_full_text

        # 这里的 super().__init__ 会启动线程，在这之前成员变量要就绪
        super().__init__(self._wait_for_vector_db_ready, self._submit_to_vector_db)

    def _wait_for_vector_db_ready(self):
        # Already wait in IHub
        pass

    def _submit_to_vector_db(self, data: dict):
        clock = Clock()
        validated_data = ArchivedData.model_validate(data)
        self.vector_db_engine_summary.upsert(validated_data, timeout=3600)
        self.vector_db_engine_full_text.upsert(validated_data, timeout=3600)
        logger.debug(f"Message {data['UUID']} vectorized, time-spending: {clock.elapsed_ms()} ms")
