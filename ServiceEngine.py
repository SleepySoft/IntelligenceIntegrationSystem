import os
import sys
import time
import logging
import threading
import traceback
from pathlib import Path
from typing import Optional

from GlobalConfig import *
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from GlobalConfig import DEFAULT_COLLECTOR_TOKEN
from MyPythonUtility.easy_config import EasyConfig
from PyLoggingBackend import LoggerBackend
from PyLoggingBackend.LogUtility import set_tls_logger, backup_and_clean_previous_log_file, setup_logging, \
    limit_logger_level
from MyPythonUtility.plugin_manager import PluginManager, PluginWrapper

logger = logging.getLogger(__name__)
project_root = os.path.dirname(os.path.abspath(__file__))


class ServiceContext:
    """
    Use this class to pass parameters to plugins and to selectively expose functions to plugins.
    """
    def __init__(self, module_logger: Optional[logging.Logger] = None, module_config: Optional[dict] = None):
        self.sys = sys
        self.logger = module_logger or logger
        self.config = module_config or EasyConfig()
        self.project_root = project_root

    def solve_import_path(self):
        import sys              # Import sys here because we must use the same sys with plugin.
                                # Just for test.
        if self.sys == sys:
            print('The same sys')
        else:
            if self.project_root not in sys.path:
                sys.path.insert(0, self.project_root)

            print('Different sys')

            print('------------------------------- Service sys -------------------------------')
            print(f"Search path：\n{chr(10).join(self.sys.path)}")
            print(f"Project Root path：{os.path.abspath(self.os.curdir)}")

            print('------------------------------- Plugin sys -------------------------------')
            print(f"Search path：\n{chr(10).join(sys.path)}")
            print(f"Project Root path：{os.path.abspath(os.curdir)}")


class TaskManager:
    THREAD_JOIN_TIMEOUT = 2
    THREAD_JOIN_ATTEMPTS = 10

    def __init__(self, watch_dir: str, security_config=None):
        self.watch_dir = watch_dir
        self.security = security_config     # Reserved for SecurityConfig

        # {plugin_name: (module_ref, thread, stop_event)}
        self.tasks: dict[str, tuple[PluginWrapper, threading.Thread, threading.Event]] = {}
        self.tasks_lock = threading.Lock()

        self.plugin_manager = PluginManager(['module_init', 'start_task'])
        self.scan_existing_files()

    def scan_existing_files(self):
        try:
            if not os.path.exists(self.watch_dir):
                os.makedirs(self.watch_dir, exist_ok=True)
            plugins = self.plugin_manager.scan_path(self.watch_dir)
            for plugin in plugins:
                self.__add_module(plugin)
        except OSError as e:
            logger.error(f"Failed to create directory {self.watch_dir}: {e}")
        except Exception as e:
            logger.error(f"Scan directory {self.watch_dir} crashed: {e}", exc_info=True)

    def add_task(self, file_path: str):
        plugin = None
        try:
            self.__remove_module(file_path)
            plugin = self.plugin_manager.add_plugin(file_path)
            if not plugin:
                raise ValueError('Plugin load None')
            return self.__add_module(plugin)
        except Exception as e:
            logger.error(f"Load plugin {file_path} fail: {e}", exc_info=True)
            if plugin:
                self.plugin_manager.remove_plugin(plugin.plugin_name)
            return False
    
    def remove_task(self, file_path: str):
        self.__remove_module(file_path)

    def shutdown(self):
        with self.tasks_lock:
            tasks = list(self.tasks.keys())
        for plugin_name in tasks:
            self.__remove_module(plugin_name)

    def on_model_enter(self, plugin: PluginWrapper):
        logger.info(f'Plugin {plugin.plugin_name} loaded')

    def on_model_quit(self, plugin: PluginWrapper):
        logger.info(f'Plugin {plugin.plugin_name} unloaded')

    # ---------------------------------------------------------------------------

    def __add_module(self, plugin: PluginWrapper) -> bool:
        stop_event = threading.Event()
        thread = threading.Thread(
            target=self.__drive_module,
            name=f"PluginThread-{plugin.plugin_name}",
            args=(plugin, stop_event),
            daemon=True
        )

        try:
            thread.start()
        except RuntimeError as e:
            logger.error(f"Create thread for {plugin.plugin_name} fail: {e}")
            return False

        with self.tasks_lock:
            self.tasks[plugin.plugin_name] = (plugin, thread, stop_event)

        return True

    def __remove_module(self, file_path: str):
        with self.tasks_lock:
            plugin_name = PluginManager.plugin_name(file_path)

            if plugin_name not in self.tasks:
                return

            self.plugin_manager.remove_plugin(plugin_name)
            task_info = self.tasks.pop(plugin_name)
            module, thread, stop_event = task_info
            stop_event.set()

        for _ in range(TaskManager.THREAD_JOIN_ATTEMPTS):
            thread.join(timeout=TaskManager.THREAD_JOIN_TIMEOUT)
            if not thread.is_alive():
                break

        if thread.is_alive():
            logger.warning(f"Plugin {plugin_name} thread (ID: {thread.ident}) "
                           f"still alive after {TaskManager.THREAD_JOIN_ATTEMPTS} attempts.")

    def __drive_module(self, plugin: PluginWrapper, stop_event: threading.Event):
        self.on_model_enter(plugin)

        module_logger = logging.getLogger(plugin.plugin_name)
        old_logger = set_tls_logger(module_logger)

        try:
            plugin.module_init(ServiceContext(
                module_logger=module_logger,
                module_config=EasyConfig()
            ))
            while not stop_event.is_set():
                plugin.start_task(stop_event)
        except Exception as e:
            logger.error(f"Plugin {plugin.plugin_name} crashed: {e}", exc_info=True)
        finally:
            set_tls_logger(old_logger)

        self.on_model_quit(plugin)


class FileHandler(FileSystemEventHandler):
    def __init__(self, task_manager):
        self.task_manager = task_manager
    
    def on_created(self, event):
        if self.__file_accept(event):
            self.task_manager.add_task(event.src_path)
    
    def on_deleted(self, event):
        if self.__file_accept(event):
            self.task_manager.remove_task(event.src_path)

    def on_modified(self, event):
        if self.__file_accept(event):
            self.task_manager.remove_task(event.src_path)
            self.task_manager.add_task(event.src_path)

    @staticmethod
    def __file_accept(event) -> bool:
        return not event.is_directory and \
            not os.path.basename(event.src_path).startswith(('~', '.')) and \
            event.src_path.endswith('.py')


# ----------------------------------------------------------------------------------------------------------------------

CRAWL_LOG_FILE = os.path.join(LOG_PATH, 'crawls.log')
HISTORY_LOG_FOLDER = os.path.join(LOG_PATH, 'crawls_history_log')


def config_log_level():
    # Disable 3-party library's log
    limit_logger_level("asyncio")
    limit_logger_level("werkzeug")
    limit_logger_level("pymongo.topology")
    limit_logger_level("pymongo.connection")
    limit_logger_level("pymongo.serverSelection")
    limit_logger_level('urllib3.util.retry')
    limit_logger_level('urllib3.connectionpool')

    # My modules


def main():
    Path(LOG_PATH).mkdir(parents=True, exist_ok=True)
    Path(HISTORY_LOG_FOLDER).mkdir(parents=True, exist_ok=True)

    # ------------------------------------ Logger ------------------------------------

    backup_and_clean_previous_log_file(CRAWL_LOG_FILE, HISTORY_LOG_FOLDER)
    setup_logging(CRAWL_LOG_FILE)

    config_log_level()

    log_backend = LoggerBackend(monitoring_file_path=CRAWL_LOG_FILE, cache_limit_count=100000,
                                link_file_roots={
                                    'conversation': os.path.abspath('conversation')
                                },
                                project_root=project_root,
                                with_logger_manager=True)
    log_backend.start_service(port=18000)

    # --------------------------------- Main Service ---------------------------------

    crawl_task_path = 'CrawlTasks'

    task_manager = TaskManager(crawl_task_path)
    event_handler = FileHandler(task_manager)

    observer = Observer()
    observer.schedule(event_handler, path=crawl_task_path, recursive=False)
    observer.start()

    # --------------------------------------------------------------------------------

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(e)
        traceback.print_exc()
