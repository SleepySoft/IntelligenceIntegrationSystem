import os
import random
import time
import traceback
import uuid
import queue
import logging

import pymongo
import threading

from attr import dataclass
from abc import ABC, abstractmethod
from typing import Tuple, Optional, Dict, Union, Callable
from pymongo.errors import ConnectionFailure
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, retry_if_result

from prompts import ANALYSIS_PROMPT
from GlobalConfig import EXPORT_PATH
from Tools.MongoDBAccess import MongoDBStorage
from ServiceComponent.IntelligenceHubDefines import *
from Tools.DateTimeUtility import time_str_to_datetime, Clock
from AIClientCenter.AIClientManager import AIClientManager
from MyPythonUtility.DictTools import check_sanitize_dict
from MyPythonUtility.AdvancedScheduler import AdvancedScheduler
from ServiceComponent.IntelligenceAnalyzerProxy import analyze_with_ai
from ServiceComponent.RecommendationManager import RecommendationManager
from ServiceComponent.IntelligenceQueryEngine import IntelligenceQueryEngine
from ServiceComponent.IntelligenceVectorDBEngine import IntelligenceVectorDBEngine
from ServiceComponent.IntelligenceStatisticsEngine import IntelligenceStatisticsEngine
from VectorDB.VectorDBClient import VectorDBClient, VectorDBInitializationError, RemoteCollection


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
            data_processor: An object implementing IHubPostProcess interface
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
        self.queue.put(data)

    def _do_process_data(self):
        """Background thread function that processes data from the queue."""
        if self._init_func:
            self._init_func()

        while self.running:
            try:
                # Get data from queue with timeout to allow checking running flag
                data = self.queue.get(timeout=1.0)

                # Process data using the provided processor
                if self._process_func:
                    self._process_func(data)

                # Mark task as done
                self.queue.task_done()

            except queue.Empty:
                # Timeout occurred, continue loop to check running flag
                continue
            except Exception as e:
                # Handle any exceptions during processing
                print(f"Error processing data: {e}")
                self.queue.task_done()

    def stop_processing(self):
        """Stop the background processing thread."""
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5.0)

    def wait_for_completion(self, timeout=None):
        """
        Wait for all queued tasks to be processed.

        Args:
            timeout: Maximum time to wait in seconds

        Returns:
            bool: True if all tasks completed, False if timeout occurred
        """
        try:
            self.queue.join()
            return True
        except Exception as e:
            print(f"Error waiting for completion: {e}")
            return False


class PostProcessVectorize(AsyncHubPostProcess):
    def __init__(self, vector_db_client: VectorDBClient):
        super().__init__(self._wait_for_vector_db_ready, self._submit_to_vector_db)

        self.vector_db_client = vector_db_client

    def _wait_for_vector_db_ready(self):
        pass

    def _submit_to_vector_db(self, data: dict):
        pass
