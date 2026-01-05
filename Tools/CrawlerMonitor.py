import time
import os
import threading
import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Dict, Optional, List, Any
from datetime import datetime
from enum import Enum

from Tools.CrawlRecord import CrawlRecord, STATUS_SUCCESS, STATUS_IGNORED, STATUS_ERROR


# Import your existing class
# from crawl_record import CrawlRecord, STATUS_SUCCESS, STATUS_ERROR, STATUS_IGNORED, STATUS_NOT_EXIST
# Assuming the code provided above is in the same file or imported

# --- Constants & Enums ---

class CrawlerState(Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"


class TaskType(Enum):
    LIST = "list"  # Loop crawling (e.g., index pages)
    CONTENT = "content"  # One-time crawling (e.g., article details)


# --- Data Structures for In-Memory State ---

@dataclass
class CrawlerConfig:
    """Runtime configuration for a crawler instance"""
    name: str
    task_type: str = TaskType.CONTENT.value
    base_interval: float = 1.0  # Seconds to wait between items
    round_interval: float = 60.0  # Seconds to wait between loops (for lists)
    max_retries: int = 3
    storage_path: str = "./data"  # Where to save DB and content files


@dataclass
class RuntimeStats:
    """Real-time statistics (kept in memory)"""
    start_time: float = field(default_factory=time.time)
    total_items: int = 0  # Total discovered/queue size (if known)
    processed_count: int = 0
    success_count: int = 0
    error_count: int = 0
    total_response_time: float = 0.0  # For avg response time calc
    last_activity: float = field(default_factory=time.time)
    current_url: str = ""
    status_map: Dict[str, int] = field(default_factory=dict)  # e.g., {"404": 5, "500": 1}


@dataclass
class CrawlerContext:
    """Holds the full context for a single crawler instance"""
    config: CrawlerConfig
    stats: RuntimeStats
    state: CrawlerState = CrawlerState.IDLE
    db_handler: 'CrawlRecord' = None
    control_lock: threading.RLock = field(default_factory=threading.RLock)
    pause_event: threading.Event = field(default_factory=lambda: threading.Event())  # Set = Running, Clear = Paused

    def __post_init__(self):
        self.pause_event.set()  # Default to running


# --- Main Monitor Class ---

class CrawlerMonitor:
    """
    Central Logic Controller for Crawler Monitoring.
    Singleton-like usage recommended.
    """

    def __init__(self):
        self.crawlers: Dict[str, CrawlerContext] = {}
        self.global_lock = threading.RLock()

        # Setup Logger
        self.logger = logging.getLogger('CrawlerMonitor')
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)

    # ===========================
    # 1. Registration & Setup
    # ===========================

    def register_crawler(self, name: str, task_type: str, storage_path: str = "./data",
                         base_interval: float = 1.0, round_interval: float = 60.0) -> bool:
        """
        Register a new crawler instance to be monitored.

        :param name: Unique identifier for the crawler
        :param task_type: 'list' or 'content'
        :param storage_path: Directory for DB and content files
        :param base_interval: Wait time between requests
        :param round_interval: Wait time between major loops
        """
        with self.global_lock:
            if name in self.crawlers:
                self.logger.warning(f"Crawler {name} already registered.")
                return False

            # Ensure storage exists
            os.makedirs(storage_path, exist_ok=True)

            # Initialize existing CrawlRecord logic
            # DB name logic: {name}_record.db
            db_path = [storage_path, f"{name}_record"]
            record_handler = CrawlRecord(db_path)

            config = CrawlerConfig(
                name=name,
                task_type=task_type,
                storage_path=storage_path,
                base_interval=base_interval,
                round_interval=round_interval
            )

            stats = RuntimeStats()

            ctx = CrawlerContext(
                config=config,
                stats=stats,
                db_handler=record_handler
            )

            self.crawlers[name] = ctx
            self.logger.info(f"Crawler registered: {name} [{task_type}]")
            return True

    def unregister_crawler(self, name: str):
        """Cleanup resources"""
        with self.global_lock:
            if name in self.crawlers:
                self.crawlers[name].db_handler.close()
                del self.crawlers[name]

    # ===========================
    # 2. Crawler Flow Interface
    # ===========================

    def should_crawl(self, name: str, url: str) -> bool:
        """
        Check if URL needs crawling based on DB history and current settings.
        Also acts as a "Gatekeeper" for retries.
        """
        ctx = self._get_ctx(name)
        if not ctx: return False

        # Check DB status using underlying CrawlRecord
        status = ctx.db_handler.get_url_status(url, from_db=True)

        if status == STATUS_SUCCESS:
            return False  # Already done

        if status == STATUS_IGNORED:
            return False

        if status >= STATUS_ERROR:
            # Check error count for Retry Logic
            err_count = ctx.db_handler.get_error_count(url, from_db=True)
            if err_count >= ctx.config.max_retries:
                self.logger.info(f"Skip {url}: Max retries reached ({err_count})")
                return False
            else:
                return True  # Retry allowed

        # STATUS_NOT_EXIST or STATUS_UNKNOWN
        return True

    def report_start_task(self, name: str, url: str):
        """Call this immediately before network request"""
        ctx = self._get_ctx(name)
        if not ctx: return

        with ctx.control_lock:
            ctx.state = CrawlerState.RUNNING
            ctx.stats.current_url = url
            ctx.stats.last_activity = time.time()
            # Store temp start time for latency calc (using a hidden attribute on stats)
            ctx.stats._temp_req_start = time.time()

    def report_finish_task(self, name: str, url: str, status: int,
                           content: Optional[bytes] = None,
                           error_msg: str = None):
        """
        Call this after network request finishes.
        Handles: Stats update, DB update, Content saving.
        """
        ctx = self._get_ctx(name)
        if not ctx: return

        end_time = time.time()
        duration = 0.0
        if hasattr(ctx.stats, '_temp_req_start'):
            duration = end_time - ctx.stats._temp_req_start

        # 1. Save Content (if success and content provided)
        saved_path = None
        if status == STATUS_SUCCESS and content:
            saved_path = self._save_content_to_file(ctx, url, content)

        # 2. Update Persistence (CrawlRecord)
        extra_info_dict = {
            "latency": f"{duration:.2f}s",
            "file_path": saved_path,
            "error": error_msg
        }

        if status == STATUS_SUCCESS:
            ctx.db_handler.record_url_status(url, status, json.dumps(extra_info_dict))
            ctx.db_handler.clear_error_count(url)  # Reset error count on success
        else:
            # Increment error count logic inside CrawlRecord
            ctx.db_handler.increment_error_count(url)
            # Update the specific error status
            ctx.db_handler.record_url_status(url, status, json.dumps(extra_info_dict))

        # 3. Update Memory Stats
        with ctx.control_lock:
            ctx.stats.processed_count += 1
            if status == STATUS_SUCCESS:
                ctx.stats.success_count += 1
            else:
                ctx.stats.error_count += 1
                # Track error types distribution
                err_key = str(status)
                ctx.stats.status_map[err_key] = ctx.stats.status_map.get(err_key, 0) + 1

            # Update moving average for performance
            # Simple average for now, could be Exponential Moving Average
            total_reqs = ctx.stats.processed_count
            current_avg = ctx.stats.total_response_time
            ctx.stats.total_response_time = current_avg + duration  # Store total seconds

            ctx.stats.current_url = ""  # Idle

    def save_crawl_content(self, name: str, url: str, content: str, extension=".html"):
        """Explicitly save content if not done in report_finish_task"""
        # Utility wrapper
        ctx = self._get_ctx(name)
        if ctx:
            return self._save_content_to_file(ctx, url, content.encode('utf-8'), extension)

    # ===========================
    # 3. Control Interface (The "Brakes")
    # ===========================

    def wait_task_interval(self, name: str):
        """
        Call this inside the loop.
        Blocks execution based on 'base_interval' or if 'paused'.
        """
        ctx = self._get_ctx(name)
        if not ctx: return

        # 1. Handle Pause (Block until set)
        ctx.pause_event.wait()

        # 2. Handle Rate Limiting
        time.sleep(ctx.config.base_interval)

    def wait_round_interval(self, name: str):
        """Call this between major loops (for list crawlers)"""
        ctx = self._get_ctx(name)
        if not ctx: return

        self.logger.info(f"Crawler {name} sleeping for round interval...")
        ctx.state = CrawlerState.IDLE

        # We assume round wait is long, so we check pause status periodically
        wait_time = ctx.config.round_interval
        start = time.time()
        while time.time() - start < wait_time:
            ctx.pause_event.wait()  # Block if paused
            time.sleep(1)  # Check every second

        ctx.state = CrawlerState.RUNNING

    # ===========================
    # 4. Management & Dashboard API
    # ===========================

    def control_action(self, name: str, action: str, value: Any = None):
        """
        Unified method for frontend to control crawlers.
        Actions: 'pause', 'resume', 'set_interval', 'set_retries'
        """
        ctx = self._get_ctx(name)
        if not ctx: return False

        self.logger.info(f"Control Action for {name}: {action} -> {value}")

        if action == "pause":
            ctx.pause_event.clear()
            ctx.state = CrawlerState.PAUSED
        elif action == "resume":
            ctx.pause_event.set()
            ctx.state = CrawlerState.RUNNING
        elif action == "set_interval":
            ctx.config.base_interval = float(value)
        elif action == "set_round_wait":
            ctx.config.round_interval = float(value)
        elif action == "reset_stats":
            # Keep configuration, wipe runtime stats
            ctx.stats = RuntimeStats()
        return True

    def get_dashboard_data(self) -> Dict:
        """
        Returns a snapshot of all crawlers for the frontend.
        Format designed to be JSON-serializable.
        """
        data = {}
        with self.global_lock:
            for name, ctx in self.crawlers.items():

                # Calculate derived metrics
                elapsed = time.time() - ctx.stats.start_time
                speed = 0
                if elapsed > 0:
                    speed = ctx.stats.processed_count / elapsed * 60  # items per minute

                avg_latency = 0
                if ctx.stats.processed_count > 0:
                    avg_latency = ctx.stats.total_response_time / ctx.stats.processed_count

                data[name] = {
                    "status": ctx.state.value,
                    "config": asdict(ctx.config),
                    "stats": {
                        "processed": ctx.stats.processed_count,
                        "success": ctx.stats.success_count,
                        "failed": ctx.stats.error_count,
                        "current_url": ctx.stats.current_url,
                        "uptime_seconds": int(elapsed),
                        "avg_speed_per_min": round(speed, 2),
                        "avg_latency_sec": round(avg_latency, 3),
                        "error_distribution": ctx.stats.status_map
                    },
                    "last_updated": datetime.now().isoformat()
                }
        return data

    # ===========================
    # 5. Helpers
    # ===========================

    def _get_ctx(self, name) -> Optional[CrawlerContext]:
        return self.crawlers.get(name)

    def _save_content_to_file(self, ctx: CrawlerContext, url: str, content: bytes, ext=".html") -> str:
        """Hashes URL to create filename and saves content"""
        import hashlib

        # Create a safe filename hash
        url_hash = hashlib.md5(url.encode('utf-8')).hexdigest()

        # Organize by date to avoid massive folders
        date_folder = datetime.now().strftime('%Y%m%d')
        save_dir = os.path.join(ctx.config.storage_path, "files", ctx.config.name, date_folder)
        os.makedirs(save_dir, exist_ok=True)

        filename = f"{url_hash}{ext}"
        full_path = os.path.join(save_dir, filename)

        try:
            mode = 'wb' if isinstance(content, bytes) else 'w'
            with open(full_path, mode) as f:
                f.write(content)
            return full_path
        except Exception as e:
            self.logger.error(f"Failed to save content for {url}: {e}")
            return ""


# ===========================
# USAGE EXAMPLE
# ===========================
if __name__ == "__main__":
    monitor = CrawlerMonitor()

    # 1. Register a crawler
    CRAWLER_NAME = "news_spider_v1"
    monitor.register_crawler(CRAWLER_NAME, "list", base_interval=2.0)


    # 2. Simulate the Crawler Loop
    def run_mock_crawler():
        urls_to_crawl = [f"http://example.com/page/{i}" for i in range(10)]

        for url in urls_to_crawl:
            # A. Check Control (Wait/Pause)
            monitor.wait_task_interval(CRAWLER_NAME)

            # B. Check Logic (Should we crawl?)
            if not monitor.should_crawl(CRAWLER_NAME, url):
                print(f"Skipping {url}")
                continue

            # C. Report Start
            monitor.report_start_task(CRAWLER_NAME, url)

            # --- Network Request Here ---
            print(f"Crawling {url}...")
            time.sleep(0.5)  # Simulate network lag
            # ----------------------------

            # D. Report Finish (Simulate Success)
            monitor.report_finish_task(
                CRAWLER_NAME,
                url,
                status=100,  # STATUS_SUCCESS
                content=b"<html>Content</html>"
            )


    # Run crawler in thread
    t = threading.Thread(target=run_mock_crawler)
    t.start()

    # 3. Simulate Frontend Monitoring
    for _ in range(5):
        time.sleep(1.5)
        # Get JSON data suitable for frontend
        dashboard_data = monitor.get_dashboard_data()
        print(f"\n--- Dashboard Snapshot ---\n{json.dumps(dashboard_data, indent=2)}")

        # Test Pause
        if _ == 2:
            print("!!! ADMIN PAUSING CRAWLER !!!")
            monitor.control_action(CRAWLER_NAME, "pause")
            time.sleep(2)
            print("!!! ADMIN RESUMING CRAWLER !!!")
            monitor.control_action(CRAWLER_NAME, "resume")

    t.join()


"""
{
  "news_spider_v1": {
    "status": "running",
    "config": { "base_interval": 1.0, ... },
    "stats": {
      "processed": 42,
      "success": 40,
      "failed": 2,
      "avg_speed_per_min": 12.5,
      "avg_latency_sec": 0.45,
      "error_distribution": { "404": 1, "500": 1 }
    }
  }
}
"""