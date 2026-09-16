import hashlib
import sys
import os
import time
import argparse
import queue
import logging
import threading
import traceback
from pathlib import Path
from threading import Thread
from dataclasses import dataclass
from typing import Optional, Tuple, Dict
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from GlobalConfig import *
from IntelligenceCrawler.CrawlerFlowScheduler import FlowScheduler
from MyPythonUtility.easy_config import EasyConfig
from PyLoggingBackend import LoggerBackend
from PyLoggingBackend.LogUtility import set_tls_logger, backup_and_clean_previous_log_file, setup_logging, \
    limit_logger_level
from MyPythonUtility.plugin_manager import PluginManager, PluginWrapper
from IntelligenceCrawler.CrawlerGovernanceBackend import CrawlerGovernanceBackend
from IntelligenceCrawler.CrawlerGovernanceCore import GovernanceManager

logger = logging.getLogger(__name__)
logger.info(f"[BOOT] pid={os.getpid()}")
project_root = os.path.dirname(os.path.abspath(__file__))


COLLECTOR_CONFIG_FILE = os.path.join(CONFIG_PATH, 'collector.json')
COLLECTOR_ALTER_CONFIG_FILE = os.path.join(CONFIG_PATH, 'collector_example.json')


def load_collector_config(config_path: Optional[str] = None) -> EasyConfig:
    """
    加载采集器独立配置（config.json 只属于 IIS 服务）。
    优先级：显式参数 / IIS_COLLECTOR_CONFIG 环境变量 / _config/collector.json
            / _config/collector_example.json / （迁移期兜底）旧 config.json 的 collector 段。
    """
    path = config_path or os.environ.get('IIS_COLLECTOR_CONFIG') or COLLECTOR_CONFIG_FILE
    if os.path.isfile(path):
        return EasyConfig(config_file=path, config_file_alter=COLLECTOR_ALTER_CONFIG_FILE)
    if os.path.isfile(COLLECTOR_ALTER_CONFIG_FILE):
        logger.warning(f"Collector config not found: {path}, fallback to example: {COLLECTOR_ALTER_CONFIG_FILE}")
        return EasyConfig(config_file=path, config_file_alter=COLLECTOR_ALTER_CONFIG_FILE)
    logger.warning(
        f"Collector config not found ({path}); "
        f"fallback to legacy config.json 'collector' section (migration period).")
    return EasyConfig(DEFAULT_CONFIG_FILE)


def resolve_task_dirs(config: EasyConfig) -> Dict[str, str]:
    """
    解析"子系统 -> 任务目录"映射（collector.task_dirs）。
    目录可以是绝对路径，或相对 collector 配置文件所在目录 / 项目根 / _config 目录，
    便于采集器作为独立仓库部署（配置文件与任务目录在同一仓库内）。
    """
    raw = config.get('collector.task_dirs') or {}
    result: Dict[str, str] = {}
    config_dir = None
    config_file = getattr(config, 'config_file', None)
    if config_file:
        config_dir = os.path.dirname(os.path.abspath(str(config_file)))
    for subsystem, directory in raw.items():
        subsystem = str(subsystem).strip()
        path = str(directory)
        if not subsystem or not path:
            continue
        if not os.path.isabs(path):
            candidates = []
            if config_dir:
                candidates.append(os.path.join(config_dir, path))
            candidates.append(os.path.join(project_root, path))
            candidates.append(os.path.join(CONFIG_PATH, path))
            existing = [c for c in candidates if os.path.isdir(c)]
            # 存在则取第一个；都不存在时回退到项目根下的规范位置（由 scan 自动创建）
            path = existing[0] if existing else os.path.join(project_root, path)
        result[subsystem] = os.path.normpath(path)
    return result


def _file_sig(path: str) -> str:
    h = hashlib.blake2b(digest_size=16)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


class ServiceContext:
    """
    Use this class to pass parameters to plugins and to selectively expose functions to plugins.
    """
    def __init__(
            self,
            module_logger: Optional[logging.Logger] = None,
            module_config: Optional[EasyConfig] = None,
            crawler_governor: GovernanceManager = None,
            subsystem: Optional[str] = None
    ):
        self.sys = sys
        self.logger = module_logger or logger
        self.config = module_config or EasyConfig()
        self.crawler_governor = crawler_governor
        self.subsystem = subsystem
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


@dataclass(frozen=True)
class TaskCmd:
    op: str          # "reload" | "remove" | "shutdown"
    path: str        # file path (for reload/remove)
    seq: int         # monotonically increasing sequence


class TaskManager:
    THREAD_JOIN_TIMEOUT = 2
    THREAD_JOIN_ATTEMPTS = 10

    def __init__(self, watch_dir: str, security_config=None, collector_config_path: Optional[str] = None):
        self.watch_dir = watch_dir
        self.security = security_config

        # 采集器独立配置（不再依赖 IIS 的 config.json）
        self.config = load_collector_config(collector_config_path)
        self.default_subsystem = str(
            self.config.get('collector.default_subsystem') or 'news').strip() or 'news'
        self.task_dirs: Dict[str, str] = resolve_task_dirs(self.config)
        if self.task_dirs:
            logger.info(f"Collector task dirs: {self.task_dirs}")
            logger.info(f"Collector default subsystem: {self.default_subsystem}")
        else:
            logger.warning(
                f"No 'collector.task_dirs' configured; scan flat '{self.watch_dir}' "
                f"as subsystem '{self.default_subsystem}' (legacy layout).")

        self.crawler_governance = GovernanceManager(
            db_path=os.path.join(DATA_PATH, 'spider_governance.db'),
            files_path=os.path.join(DATA_PATH, 'spider_governance_files'),
            scheduler=FlowScheduler(max_concurrency=5, startup_stagger=10.0)
        )

        self.plugin_manager = PluginManager(['module_init', 'start_task'])

        self._sig = {}  # plugin_name -> sig

        # {plugin_name: (PluginWrapper, Thread, Event)}
        self.tasks: Dict[str, Tuple[PluginWrapper, threading.Thread, threading.Event]] = {}

        # command queue / manager thread
        self._q: queue.Queue[TaskCmd] = queue.Queue()
        self._seq = 0
        self._stop_mgr = threading.Event()
        self._mgr_thread = threading.Thread(target=self._manager_loop, name="TaskManagerThread", daemon=True)
        self._mgr_thread.start()

        self._validate_subsystems_against_ihub()
        self.scan_existing_files()

    # ---------------- public APIs: NON-BLOCKING ----------------

    def submit_reload(self, file_path: str, source: str):
        """For created/modified/moved-in events."""
        abs_path = os.path.abspath(file_path)
        name = PluginManager.plugin_name(abs_path)

        try:
            sig = _file_sig(abs_path)
        except FileNotFoundError:
            return
        if self._sig.get(name) == sig:
            logger.info(f"[CMD] skip reload (content unchanged) {abs_path}")
            return
        self._sig[name] = sig

        self._enqueue("reload", file_path, source)

    def submit_remove(self, file_path: str, source: str):
        """For deleted/moved-out events."""
        self._enqueue("remove", file_path, source)

    def shutdown(self, source: str = "fs"):
        """Stop all tasks & manager thread."""
        self._enqueue("shutdown", "", source)
        self._mgr_thread.join(timeout=10)

    # ---------------- internal: queue & manager ----------------

    def _enqueue(self, op: str, path: str, source: str):
        self._seq += 1
        logger.info(f"[CMD] seq={self._seq} op={op} source={source} path={path}")
        self._q.put(TaskCmd(op=op, path=path, seq=self._seq))

    def _manager_loop(self):
        """
        Single writer:
          - Only this thread mutates self.tasks and calls plugin_manager add/remove.
        Coalescing:
          - Drain queue and keep the last cmd per plugin_name.
        """
        while not self._stop_mgr.is_set():
            cmd = self._q.get()  # blocking
            if cmd.op == "shutdown":
                self._handle_shutdown()
                self._stop_mgr.set()
                break

            # Drain more commands quickly to coalesce storms
            batch = [cmd]
            try:
                while True:
                    batch.append(self._q.get_nowait())
            except queue.Empty:
                pass

            # Keep only the last cmd for each plugin_name
            last_by_name: Dict[str, TaskCmd] = {}
            for c in batch:
                if c.op == "shutdown":
                    # prioritize shutdown immediately
                    self._handle_shutdown()
                    self._stop_mgr.set()
                    return
                name = PluginManager.plugin_name(c.path)
                prev = last_by_name.get(name)
                if prev is None or c.seq > prev.seq:
                    last_by_name[name] = c

            # Apply in seq order for determinism (optional)
            for name, c in sorted(last_by_name.items(), key=lambda it: it[1].seq):
                try:
                    if c.op == "remove":
                        self._do_remove(name)
                    elif c.op == "reload":
                        self._do_reload(name, c.path)
                    else:
                        logger.warning(f"Unknown op: {c.op}")
                except Exception as e:
                    logger.error(f"Manager op {c.op}({c.path}) failed: {e}", exc_info=True)

    # ---------------- internal: operations (serialized) ----------------

    def _do_reload(self, plugin_name: str, file_path: str):
        # stop old one if exists
        if not self._stop_and_join_if_running(plugin_name):
            # self._pending_reload[plugin_name] = os.path.abspath(file_path)
            logger.info(f"Defer reload for {plugin_name}, old thread still running.")
            return

        # unload module safely AFTER join
        self.plugin_manager.remove_plugin(plugin_name)

        # load new module
        plugin = self._load_plugin(file_path)
        if not plugin:
            logger.error(f"Load plugin failed: {file_path}")
            return

        # start task thread
        stop_event = threading.Event()
        thread = threading.Thread(
            target=self.__drive_module,
            name=f"PluginThread-{plugin.plugin_name}",
            args=(plugin, stop_event),
            daemon=True
        )
        self.tasks[plugin.plugin_name] = (plugin, thread, stop_event)
        thread.start()
        logger.info(f"Reloaded & started plugin: {plugin.plugin_name}")

    def _do_remove(self, plugin_name: str):
        self._stop_and_join_if_running(plugin_name)
        # unload AFTER join
        self.plugin_manager.remove_plugin(plugin_name)
        logger.info(f"Removed plugin: {plugin_name}")

    def _handle_shutdown(self):
        # stop all
        names = list(self.tasks.keys())
        for name in names:
            self._stop_and_join_if_running(name)
            self.plugin_manager.remove_plugin(name)
        logger.info("TaskManager shutdown complete.")

    def _stop_and_join_if_running(self, plugin_name: str) -> bool:
        entry = self.tasks.get(plugin_name)
        if not entry:
            return True

        plugin, thread, stop_event = entry
        stop_event.set()

        for _ in range(self.THREAD_JOIN_ATTEMPTS):
            thread.join(timeout=self.THREAD_JOIN_TIMEOUT)
            if not thread.is_alive():
                self.tasks.pop(plugin_name, None)
                return True

        logger.warning(f"Plugin {plugin.plugin_name} thread (ID:{thread.ident}) still alive after join attempts.")
        return False

    # ---------------- existing behaviors ----------------

    def scan_existing_files(self):
        try:
            targets = self.task_dirs or {self.default_subsystem: self.watch_dir}
            files = []
            for subsystem, directory in targets.items():
                os.makedirs(directory, exist_ok=True)
                for path in self._list_task_files(directory):
                    files.append(path)
                    logger.info(f"[Scan] subsystem='{subsystem}' file={path}")
            # use submit_reload to unify behavior (serialize through manager)
            for p in files:
                self.submit_reload(p, 'scan')
        except Exception as e:
            logger.error(f"Scan directory {self.watch_dir} crashed: {e}", exc_info=True)

    @staticmethod
    def _list_task_files(directory: str):
        """递归收集目录下可加载的 .py 文件（跳过 _/. 开头与缓存目录）。"""
        files = []
        for root, dirs, fnames in os.walk(directory):
            dirs[:] = [d for d in dirs if not d.startswith(('.', '_', '__pycache__'))]
            for fname in sorted(fnames):
                if fname.endswith('.py') and not fname.startswith(('.', '_')):
                    files.append(os.path.join(root, fname))
        return files

    def _load_plugin(self, file_path: str) -> Optional[PluginWrapper]:
        """加载插件前把其所在目录加入 sys.path，支持同目录模块导入（如 crawler_config_*）。"""
        dirname = os.path.dirname(os.path.abspath(file_path))
        if dirname not in sys.path:
            sys.path.insert(0, dirname)
        return self.plugin_manager.add_plugin(file_path)

    def _resolve_subsystem(self, module_path: str) -> str:
        """按 '目录 -> 子系统' 映射解析任务归属；未匹配时回退 default_subsystem。"""
        abs_path = os.path.normpath(os.path.abspath(module_path))
        for subsystem, directory in self.task_dirs.items():
            try:
                base = os.path.normpath(os.path.abspath(directory))
                if os.path.commonpath([abs_path, base]) == base:
                    return subsystem
            except ValueError:
                continue
        return self.default_subsystem

    def _validate_subsystems_against_ihub(self):
        """拉取 IIS /api/subsystems，校验本采集器声明的子系统是否与 IIS 对上。"""
        ihub_url = str(self.config.get('collector.submit_ihub_url') or '').rstrip('/')
        if not ihub_url:
            return
        try:
            import requests
            resp = requests.get(f'{ihub_url}/api/subsystems', timeout=3)
            if resp.status_code != 200:
                logger.warning(
                    f"Subsystem validation skipped: IIS /api/subsystems returned {resp.status_code}.")
                return
            data = resp.json()
            known = {str(s.get('name')) for s in data.get('subsystems', [])}
            remote_default = data.get('default')
        except Exception as e:
            logger.warning(f"Subsystem validation skipped: {e}")
            return

        declared = list(self.task_dirs.keys())
        if not declared:
            declared = [self.default_subsystem]
        for subsystem in declared:
            if subsystem in known:
                logger.info(f"Collector subsystem '{subsystem}' aligned with IIS "
                            f"(default={remote_default}).")
            else:
                logger.warning(
                    f"Collector subsystem '{subsystem}' NOT found in IIS subsystems {sorted(known)}; "
                    f"/collect will reject its submissions.")

    def on_model_enter(self, plugin: PluginWrapper):
        logger.info(f">>> Plugin {plugin.plugin_name} in thread {threading.get_ident()} plugin_obj={id(plugin)} - started.")

    def on_model_quit(self, plugin: PluginWrapper):
        logger.info(f"<<< Plugin {plugin.plugin_name} in thread {threading.get_ident()} plugin_obj={id(plugin)} - terminated.")

    def __drive_module(self, plugin: PluginWrapper, stop_event: threading.Event):
        self.on_model_enter(plugin)

        module_logger = logging.getLogger(plugin.plugin_name)
        old_logger = set_tls_logger(module_logger)
        subsystem = self._resolve_subsystem(plugin.module_path)

        try:
            plugin.module_init(ServiceContext(
                module_logger=module_logger,
                module_config=self.config,
                crawler_governor=self.crawler_governance,
                subsystem=subsystem
            ))

            # 约定：start_task 应该“短阻塞/一次迭代”，并检查 stop_event
            while not stop_event.is_set():
                plugin.start_task(stop_event)

        except Exception as e:
            logger.error(f"Plugin {plugin.plugin_name} crashed: {e}", exc_info=True)
        finally:
            set_tls_logger(old_logger)

        self.on_model_quit(plugin)


class FileHandler(FileSystemEventHandler):
    def __init__(self, task_manager, debounce_ms=300):
        self.task_manager = task_manager
        self.debounce_ms = debounce_ms
        self._last_ts = {}
        self._lock = threading.Lock()
        self._sig = {}  # plugin_name -> (mtime_ns, size)

    def on_created(self, event):
        if self.__file_accept(event) and self._debounced(event.src_path):
            self._log_event("accepted", event)
            self.task_manager.submit_reload(event.src_path, 'fs')

    def on_modified(self, event):
        if self.__file_accept(event) and self._debounced(event.src_path):
            self._log_event("accepted", event)
            self.task_manager.submit_reload(event.src_path, 'fs')

    def on_deleted(self, event):
        if self.__file_accept(event) and self._debounced(event.src_path):
            self._log_event("accepted", event)
            self.task_manager.submit_remove(event.src_path, 'fs')

    def on_moved(self, event):
        if event.is_directory:
            return
        if self.__file_accept(event) and self._debounced(event.src_path):
            self._log_event("accepted", event)
            self.task_manager.submit_remove(event.src_path, 'fs')
        if hasattr(event, "dest_path") and event.dest_path.endswith(".py") and self._debounced(event.dest_path):
            self._log_event("accepted", event)
            self.task_manager.submit_reload(event.dest_path, 'fs')

    def _debounced(self, path: str) -> bool:
        now = time.monotonic()
        with self._lock:
            last = self._last_ts.get(path, 0)
            if (now - last) * 1000 < self.debounce_ms:
                return False
            self._last_ts[path] = now
            return True

    def _log_event(self, tag, event):
        try:
            st = os.stat(event.src_path)
            logger.info(f"[FS] {tag} type={event.event_type} src={event.src_path} "
                        f"mtime_ns={st.st_mtime_ns} size={st.st_size}")
        except FileNotFoundError:
            logger.info(f"[FS] {tag} type={event.event_type} src={event.src_path} (missing)")

    @staticmethod
    def __file_accept(event) -> bool:
        return (not event.is_directory and
                not os.path.basename(event.src_path).startswith(('~', '.')) and
                event.src_path.endswith('.py'))


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
    parser = argparse.ArgumentParser(description="IIS Crawler Service")
    parser.add_argument('--config', default=None,
                        help="Collector config json path (default: _config/collector.json "
                             "or IIS_COLLECTOR_CONFIG env var)")
    args = parser.parse_args()

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

    task_manager = TaskManager(crawl_task_path, collector_config_path=args.config)
    # event_handler = FileHandler(task_manager)

    # observer = Observer()
    # observer.schedule(event_handler, path=crawl_task_path, recursive=False)
    # observer.start()

    governance_backend = CrawlerGovernanceBackend(task_manager.crawler_governance)
    governance_backend.start_service(blocking=False)

    # --------------------------------------------------------------------------------

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
        # observer.stop()
    finally:
        # observer.join()
        task_manager.shutdown()
        # governance_backend.stop_service()
        # log_backend.stop_service()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(e)
        traceback.print_exc()
