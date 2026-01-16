import os
import time
import json
import hashlib
import logging
import sqlite3
import datetime
import threading
from pathlib import Path
from enum import IntEnum, Enum
from typing import Optional, Dict, Union

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("CrawlGoverner")


# --- Enums & Constants ---

class Status(IntEnum):
    PENDING = 0     # Ready to be crawled
    SUCCESS = 1     # Successfully crawled and parsed
    TEMP_FAIL = 2   # Network error, timeout (Retryable)
    PERM_FAIL = 3   # Parse error, 404, empty content (Non-retryable)
    SKIPPED = 4     # Skipped by logic
    GIVE_UP = 5     # Retries exhausted


class TaskType(Enum):
    LIST = "LIST"  # Recurrent task (e.g., RSS, Category Page)
    ARTICLE = "ARTICLE"  # One-off task (e.g., Detail Page)


DEFAULT_DB_PATH = "data/db/spider_governance.db"
DEFAULT_FILES_PATH = "data/files"


def calculate_wait_time(future_time_str: str) -> float:
    if 'Z' in future_time_str:
        target = datetime.datetime.fromisoformat(future_time_str.replace('Z', '+00:00'))
    else:
        target = datetime.datetime.fromisoformat(future_time_str)

    if target.tzinfo is None:
        target = target.replace(tzinfo=datetime.timezone.utc)

    now = datetime.datetime.now(datetime.timezone.utc)

    wait_seconds = (target - now).total_seconds()

    # 调试信息
    print(f"目标时间(UTC): {target}")
    print(f"当前时间(UTC): {now}")
    print(f"等待秒数: {wait_seconds}")

    return wait_seconds


# --- Database Handler ---

class DatabaseHandler:
    """
    Handles raw SQLite operations with schema management.
    """

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row  # Access columns by name
        self.lock = threading.RLock()
        self._init_schema()

    def _init_schema(self):
        with self.lock:
            cur = self.conn.cursor()

            # Table 1: Task Registry (For Recurrent Lists/RSS)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS task_registry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    spider TEXT NOT NULL,
                    group_name TEXT NOT NULL,
                    url TEXT UNIQUE NOT NULL,
                    interval INTEGER DEFAULT 3600,
                    next_run TIMESTAMP,
                    status TEXT DEFAULT 'WAITING',
                    stats TEXT DEFAULT '{}'
                )
            """)

            # Table 2: Crawl Log (For specific Articles/Items)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS crawl_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    spider TEXT NOT NULL,
                    group_name TEXT NOT NULL,
                    url_hash TEXT UNIQUE NOT NULL,
                    url TEXT NOT NULL,
                    status INTEGER DEFAULT 0,
                    retry_count INTEGER DEFAULT 0,
                    http_code INTEGER,
                    duration REAL,
                    content_path TEXT,
                    state_msg TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Indexes for performance
            cur.execute("CREATE INDEX IF NOT EXISTS idx_log_status ON crawl_log(status)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_log_spider ON crawl_log(spider, group_name)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_task_next ON task_registry(next_run)")

            self.conn.commit()

    def execute(self, sql: str, params: tuple = ()):
        with self.lock:
            try:
                cur = self.conn.cursor()
                cur.execute(sql, params)
                self.conn.commit()
                return cur
            except sqlite3.Error as e:
                logger.error(f"DB Error: {e} | SQL: {sql}")
                raise

    def fetch_one(self, sql: str, params: tuple = ()):
        with self.lock:
            cur = self.conn.cursor()
            cur.execute(sql, params)
            return cur.fetchone()


# --- File Storage Handler ---

class StorageHandler:
    """
    Handles file system operations (saving snapshots).
    """

    def __init__(self, base_path: str):
        self.base_path = Path(base_path)

    def save(self, spider: str, content: bytes, ext: str = ".html") -> str:
        """
        Saves content to: base_path/spider/YYYY-MM-DD/hash.ext
        Returns: Absolute path string
        """
        if not content:
            return ""

        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        content_hash = hashlib.md5(content).hexdigest()

        # Structure: data/files/spider_name/2025-01-01/
        save_dir = self.base_path / spider / date_str
        save_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{content_hash}{ext}"
        file_path = save_dir / filename

        # Write only if not exists (deduplication by hash)
        if not file_path.exists():
            with open(file_path, "wb") as f:
                f.write(content)

        return str(file_path.absolute())


# --- Context Manager (Session) ---

class CrawlSession:
    """
    Context manager for a single crawl transaction.
    Usage:
        with governer.transaction(...) as task:
            task.save_snapshot(...)
            task.success()
    """

    def __init__(self, manager, url: str, spider: str, group: str, context_type: TaskType):
        self.manager = manager
        self.url = url
        self.spider = spider
        self.group = group
        self.context_type = context_type
        self.start_time = time.time()
        self.status = Status.PENDING
        self.http_code = None
        self.state_msg = None
        self.content_path = None
        self._finished = False

    def __enter__(self):
        # Could log "Started crawling..." here
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if not self._finished:
            if exc_type:
                self.fail_perm(http_code=500, state_msg=str(exc_val))
                logger.error(f"Session {self.url} exited without unhandled exception. PERMANENT.")
            else:
                self.ignore()
                logger.warning(f"Session {self.url} exited without explicit status. Just mark as ignore.")

    def save_snapshot(self, content: Union[bytes, str], ext=".html"):
        """Save request content to disk"""
        if isinstance(content, str):
            content = content.encode('utf-8')
        self.content_path = self.manager.storage.save(self.spider, content, ext)

    def success(self, state_msg=""):
        """Mark transaction as SUCCESS"""
        self.state_msg = state_msg
        self._finalize(Status.SUCCESS)

    def skip(self, state_msg="Empty Content"):
        """Mark transaction as IGNORE"""
        self.state_msg = state_msg
        self._finalize(Status.SKIPPED)

    def ignore(self):
        """Ignored like should_crawl() returns false."""
        self._finished = True

    def fail_temp(self, http_code=0, state_msg="Unknown Network Error"):
        """Mark as TEMPORARY FAILURE (Retryable)"""
        self.http_code = http_code
        self.state_msg = state_msg
        self._finalize(Status.TEMP_FAIL)

    def fail_perm(self, http_code=0, state_msg="Unknown Permanent Error"):
        """Mark as PERMANENT FAILURE (Non-retryable)"""
        self.http_code = http_code
        self.state_msg = state_msg
        self._finalize(Status.PERM_FAIL)

    def _finalize(self, status: Status):
        if self._finished:
            return

        duration = round(time.time() - self.start_time, 3)
        self.status = status

        self.manager._commit_transaction(
            task_type=self.context_type,
            spider=self.spider,
            group=self.group,
            url=self.url,
            status=status,
            duration=duration,
            http_code=self.http_code,
            state_msg=self.state_msg,
            content_path=self.content_path
        )
        self._finished = True


# --- Main Governance Class ---

class GovernanceManager:
    """
    The centralized controller for crawler state, logging, and flow control.
    """

    def __init__(self, spider_name: str, db_path: str = DEFAULT_DB_PATH, files_path: str = DEFAULT_FILES_PATH):
        self.spider_name = spider_name
        self.db = DatabaseHandler(db_path)
        self.storage = StorageHandler(files_path)

        # Configurable retries
        self.max_retries = 3

    # --- 1. Task Registration (For Lists/RSS) ---

    def register_task(self, url: str, group_name: str, interval: int = 3600):
        """
        Register a recurrent task. If exists, update interval.
        """
        try:
            self.db.execute("""
                INSERT INTO task_registry (spider, group_name, url, interval, next_run)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(url) DO UPDATE SET
                interval = excluded.interval,
                spider = excluded.spider,
                group_name = excluded.group_name
            """, (self.spider_name, group_name, url, interval))
        except Exception as e:
            logger.error(f"Failed to register task {url}: {e}")

    # --- 2. Decision Logic (Should I Crawl?) ---

    def should_crawl(self, url: str, task_type: TaskType = TaskType.ARTICLE) -> bool:
        """
        Determines if a URL needs to be crawled based on history and retry logic.
        """
        if task_type == TaskType.LIST:
            # For Lists: Check 'next_run' timestamp
            row = self.db.fetch_one("""
                SELECT next_run FROM task_registry 
                WHERE url = ? AND spider = ?
            """, (url, self.spider_name))

            if not row: return False  # Must register first

            next_run = datetime.datetime.fromisoformat(row['next_run'])
            if datetime.datetime.now() >= next_run:
                return True
            return False

        else:  # TaskType.ARTICLE
            # For Articles: Check crawl_log for status and retries
            url_hash = hashlib.md5(url.encode()).hexdigest()
            row = self.db.fetch_one("""
                SELECT status, retry_count FROM crawl_log WHERE url_hash = ?
            """, (url_hash,))

            if not row:
                return True  # New URL, go ahead

            status = row['status']
            retry_count = row['retry_count']

            if status == Status.SUCCESS:
                return False  # Already done

            if status in [Status.PERM_FAIL, Status.GIVE_UP, Status.SKIPPED]:
                return False  # Given up

            if status == Status.TEMP_FAIL:
                if retry_count < self.max_retries:
                    return True  # Retry allowed
                else:
                    # Mark as GIVE_UP if we see it again and retries exhausted
                    self.db.execute("UPDATE crawl_log SET status = ? WHERE url_hash = ?",
                                    (Status.GIVE_UP, url_hash))
                    return False

            return True

    # --- 3. Transaction Factory ---

    def transaction(self, url: str, group_name: str, task_type: TaskType = TaskType.ARTICLE):
        """
        Returns a context manager to handle the crawl process.
        """
        return CrawlSession(self, url, self.spider_name, group_name, task_type)

    def _commit_transaction(self, task_type, spider, group, url, status, duration, http_code, state_msg, content_path):
        """
        Internal method called by CrawlSession to write to DB.
        """
        timestamp = datetime.datetime.now()

        if task_type == TaskType.LIST:
            # Update Task Registry
            if status == Status.SUCCESS:
                # Calculate next run time based on interval
                # Note: We query the interval first to be safe, or cache it
                row = self.db.fetch_one("SELECT interval FROM task_registry WHERE url=?", (url,))
                interval = row['interval'] if row else 3600
                next_run = timestamp + datetime.timedelta(seconds=interval)

                self.db.execute("""
                    UPDATE task_registry 
                    SET status = 'WAITING', next_run = ?, stats = json_set(ifnull(stats, '{}'), '$.last_success', ?)
                    WHERE url = ?
                """, (next_run, timestamp.isoformat(), url))
            else:
                # If list fails, maybe retry sooner? For now, keep running
                self.db.execute("UPDATE task_registry SET status = 'ERROR' WHERE url = ?", (url,))

        else:  # ARTICLE
            url_hash = hashlib.md5(url.encode()).hexdigest()

            # Logic for Retries
            if status == Status.TEMP_FAIL:
                # Increment retry count, keep updated_at current
                self.db.execute("""
                    INSERT INTO crawl_log (spider, group_name, url_hash, url, status, retry_count, http_code, state_msg, duration, updated_at)
                    VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
                    ON CONFLICT(url_hash) DO UPDATE SET
                        status = excluded.status,
                        retry_count = retry_count + 1,
                        http_code = excluded.http_code,
                        state_msg = excluded.state_msg,
                        updated_at = excluded.updated_at
                """, (spider, group, url_hash, url, status, http_code, state_msg, duration, timestamp))

            else:
                # Success or Perm Fail
                self.db.execute("""
                    INSERT INTO crawl_log (spider, group_name, url_hash, url, status, http_code, duration, content_path, state_msg, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(url_hash) DO UPDATE SET
                        status = excluded.status,
                        http_code = excluded.http_code,
                        duration = excluded.duration,
                        content_path = excluded.content_path,
                        state_msg = excluded.state_msg,
                        updated_at = excluded.updated_at
                """, (spider, group, url_hash, url, status, http_code, duration, content_path, state_msg, timestamp))

                if status == Status.SUCCESS:
                    logger.info(f"[{group}] SUCCESS: {url}")
                else:
                    logger.warning(f"[{group}] FAIL({status}): {url}")

    # --- 4. Flow Control (Wait & Pause) ---

    def wait_round(self, group_name: str, stop_event: threading.Event = None):
        """
        Wait logic for Recurrent Tasks (Lists).
        Simple implementation: Just logs for now.
        Real implementation would calculate sleep time until next_run of the group.

        Args:
            group_name: Name of the task group
            stop_event: Optional event to signal external stop request
        """
        # Logic: Find the earliest next_run for this group
        row = self.db.fetch_one("""
            SELECT min(next_run) as earliest FROM task_registry 
            WHERE spider = ? AND group_name = ?
        """, (self.spider_name, group_name))

        if row and row['earliest']:
            wait_seconds = calculate_wait_time(row['earliest'])
            if wait_seconds > 0:
                logger.info(f"[{group_name}] Round finished. Waiting {wait_seconds:.1f}s for next round...")
                # We can reuse wait_interval logic here to support interruption
                self._sleep_interruptible(wait_seconds, stop_event)
            else:
                logger.info(f"[{group_name}] Round finished. Immediate restart.")

    def wait_interval(self, default_seconds=2.0, stop_event: threading.Event = None):
        """
        Wait logic between URLs.
        Checks for PAUSE signals every 0.1s.

        Args:
            default_seconds: Default wait interval in seconds
            stop_event: Optional event to signal external stop request
        """
        # TODO: Read dynamic interval from DB config table
        # For now, use default
        self._sleep_interruptible(default_seconds, stop_event)

    def _sleep_interruptible(self, seconds, stop_event: threading.Event = None):
        """
        Sleeps for `seconds` but checks for pause signals and stop events.

        Args:
            seconds: Total seconds to sleep
            stop_event: Optional event to signal external stop request
        """
        end_time = time.time() + seconds
        while time.time() < end_time:
            # Check for external stop event first
            if stop_event and stop_event.is_set():
                logger.info("Sleep interrupted by stop event")
                break

            # Check for PAUSE signal from DB (Future feature)
            # if self.db.is_paused(self.spider_name):
            #     time.sleep(1)
            #     end_time += 1 # Extend deadline
            #     continue

            # Sleep in small chunks to remain responsive
            remaining = end_time - time.time()
            sleep_chunk = min(0.1, remaining)
            if sleep_chunk > 0:
                time.sleep(sleep_chunk)
