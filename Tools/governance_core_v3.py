import os
import time
import json
import logging
import sqlite3
import datetime
import threading
import hashlib
from pathlib import Path
from enum import IntEnum, Enum
from typing import Optional, Union, List, Dict

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("CrawlGovernance")


# --- Enums ---

class Status(IntEnum):
    PENDING = 0  # Ready to be crawled (or newly discovered)
    RUNNING = 1  # Currently processing (Real-time visibility)
    SUCCESS = 2  # Finished successfully
    TEMP_FAIL = 3  # Network error, timeout (Retryable)
    PERM_FAIL = 4  # Parse error, 404 (Non-retryable)
    SKIPPED = 5  # Skipped by logic (e.g., filtered content)
    STOPPED = 6  # Manually stopped or interrupted


class ControlSignal(Enum):
    NORMAL = "NORMAL"
    PAUSE = "PAUSE"  # Pause execution loop
    IMMEDIATE = "IMMEDIATE"  # Skip current wait interval


# --- Constants ---

DEFAULT_DB_PATH = "data/db/governance.db"
DEFAULT_FILES_PATH = "data/files"


def _normalize_group_path(raw_input: Union[str, List[str], None]) -> str:
    """
    Standardizes the group path.
    Accepts:
      - str: "spider/news//tech"
      - list: ["spider", "news", "tech"]
      - None/Empty
    Returns:
      - str: "spider/news/tech"
      - default: "default"
    """
    if not raw_input:
        return "default"

    parts = []

    # 1. Flatten inputs into a list of strings
    if isinstance(raw_input, str):
        parts = raw_input.split('/')
    elif isinstance(raw_input, (list, tuple)):
        for item in raw_input:
            if item:
                # Handle case where list item contains slashes: ['spider/v1', 'news']
                parts.extend(str(item).split('/'))
    else:
        parts = [str(raw_input)]

    # 2. Clean inputs (remove empty strings, whitespace)
    clean_parts = [p.strip() for p in parts if p and p.strip()]

    # 3. Join or Fallback
    if not clean_parts:
        return "default"

    return "/".join(clean_parts)


def _extract_spider_name(normalized_group_path: str) -> str:
    """
    Derives spider name from the first segment of the group path.
    e.g., "google_bot/news/tech" -> "google_bot"
    Assumes _normalize_group_path has already been called.
    """
    return normalized_group_path.split("/")[0]


# --- Database Handler ---

class DatabaseHandler:
    """
    Handles SQLite operations.
    Manages three core tables:
    1. task_groups: Metadata registry (UI Hierarchy & Key Entry Points).
    2. crawl_status: Dashboard (Latest state of every unique URL).
    3. crawl_log: Audit Trail (History of all transaction attempts).
    """

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self._init_schema()

    def _init_schema(self):
        with self.lock:
            cur = self.conn.cursor()

            # 1. Task Groups (Metadata Registry)
            # Used for UI aggregation. linking a group to a specific entry URL (list_url).
            # 'list_url' serves as a logical foreign key to crawl_status.url
            cur.execute("""
                CREATE TABLE IF NOT EXISTS task_groups (
                    group_path TEXT PRIMARY KEY,
                    list_url TEXT, 
                    name TEXT,
                    config_json TEXT DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 2. Crawl Status (The "Dashboard" - Current State)
            # Stores the LATEST known state of a URL.
            # 'spider_name' is stored for fast filtering/stats, derived from group_path.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS crawl_status (
                    url TEXT PRIMARY KEY,
                    url_hash TEXT NOT NULL, 
                    group_path TEXT NOT NULL,
                    spider_name TEXT NOT NULL,
                    status INTEGER DEFAULT 0,
                    retry_count INTEGER DEFAULT 0,
                    http_code INTEGER,
                    file_path TEXT,
                    last_run_at TIMESTAMP,
                    next_run_at TIMESTAMP, 
                    duration REAL,
                    state_msg TEXT
                )
            """)

            # 3. Crawl Log (The "Flow" - History)
            # Records every attempt. Linked to Session via 'id'.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS crawl_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT NOT NULL,
                    group_path TEXT NOT NULL,
                    spider_name TEXT NOT NULL,
                    status INTEGER,
                    http_code INTEGER,
                    duration REAL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 4. System Control (For persistent signaling)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sys_control (
                    key TEXT PRIMARY KEY,
                    signal TEXT DEFAULT 'NORMAL',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Initialize global control signal if not present
            cur.execute("INSERT OR IGNORE INTO sys_control (key, signal) VALUES ('global', 'NORMAL')")

            # Indexes for performance
            cur.execute("CREATE INDEX IF NOT EXISTS idx_status_group ON crawl_status(group_path)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_status_spider ON crawl_status(spider_name)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_log_url ON crawl_log(url)")

            self.conn.commit()

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self.lock:
            try:
                cur = self.conn.cursor()
                cur.execute(sql, params)
                self.conn.commit()
                return cur  # Return cursor to access lastrowid
            except sqlite3.Error as e:
                logger.error(f"DB Error: {e} | SQL: {sql}")
                raise

    def fetch_one(self, sql: str, params: tuple = ()):
        with self.lock:
            cur = self.conn.cursor()
            cur.execute(sql, params)
            return cur.fetchone()

    def fetch_all(self, sql: str, params: tuple = ()):
        with self.lock:
            cur = self.conn.cursor()
            cur.execute(sql, params)
            return cur.fetchall()

    def get_control_signal(self, key='global') -> str:
        row = self.fetch_one("SELECT signal FROM sys_control WHERE key = ?", (key,))
        return row['signal'] if row else "NORMAL"

    def set_control_signal(self, signal: str, key='global'):
        self.execute("INSERT OR REPLACE INTO sys_control (key, signal, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                     (key, signal))


# --- File Storage Handler ---

class StorageHandler:
    """
    Decoupled file storage.
    Accepts explicit relative paths from the caller (e.g., spider/group/filename.html).
    """

    def __init__(self, base_path: str):
        self.base_path = Path(base_path)

    def save(self, content: Union[bytes, str], relative_path: str) -> str:
        """
        Saves content to: base_path / relative_path
        Returns: Absolute path string
        """
        if not content:
            return ""

        if isinstance(content, str):
            content = content.encode('utf-8')

        full_path = self.base_path / relative_path

        try:
            full_path.parent.mkdir(parents=True, exist_ok=True)
            with open(full_path, "wb") as f:
                f.write(content)
            return str(full_path.absolute())
        except Exception as e:
            logger.error(f"Storage Error: {e}")
            return ""


# --- Context Manager (Session) ---

class CrawlSession:
    """
    Manages the lifecycle of a single URL crawl.
    1. Start: Updates DB to RUNNING, creates a Log entry (gets ID).
    2. Execution: Allows saving files and marking intermediate states.
    3. End: Updates Log entry (by ID) and Status table (by URL).
    """

    def __init__(self, manager, url: str, spider: str, group_path: Union[str, List[str], None]):
        self.manager = manager
        self.url = url
        self.spider = spider
        self.group_path = _normalize_group_path(group_path)
        self.start_time = time.time()

        # Unique ID for the specific log entry of this session
        self.log_id: Optional[int] = None

        self.status = Status.RUNNING
        self.http_code = None
        self.state_msg = None
        self.file_path = None
        self._finished = False

    def __enter__(self):
        # Notify manager to start transaction (Insert Log, Update Status)
        self.log_id = self.manager._handle_task_start(self.url, self.spider, self.group_path)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if not self._finished:
            if exc_type:
                # Handle unhandled exceptions/crashes
                self.fail_perm(http_code=500, state_msg=f"Exception: {str(exc_val)}")
                logger.error(f"Session crashed for {self.url}: {exc_val}")
            else:
                # Handle context exit without explicit status
                self.fail_temp(state_msg="Exited without explicit status")

    def save_file(self, content: Union[bytes, str], filename: str, sub_folder: str = ""):
        """
        Save content to disk.
        Logic: base_dir / spider / group_path / sub_folder / filename
        """
        # Construct path preserving hierarchy
        rel_path = Path(self.spider) / self.group_path / sub_folder / filename
        self.file_path = self.manager.storage.save(content, str(rel_path))
        return self.file_path

    def success(self, state_msg="OK"):
        self.state_msg = state_msg
        self._finalize(Status.SUCCESS)

    def skip(self, state_msg="Skipped"):
        self.state_msg = state_msg
        self._finalize(Status.SKIPPED)

    def ignore(self):
        self._finished = True

    def fail_temp(self, http_code=0, state_msg="Retryable Error"):
        self.http_code = http_code
        self.state_msg = state_msg
        self._finalize(Status.TEMP_FAIL)

    def fail_perm(self, http_code=0, state_msg="Permanent Error"):
        self.http_code = http_code
        self.state_msg = state_msg
        self._finalize(Status.PERM_FAIL)

    def set_next_run(self, interval_seconds: int):
        """
        Helper for list pages to set the next scheduled run time in DB.
        This updates the 'next_run_at' field in crawl_status.
        """
        if interval_seconds > 0:
            next_run = datetime.datetime.now() + datetime.timedelta(seconds=interval_seconds)
            self.manager.db.execute(
                "UPDATE crawl_status SET next_run_at = ? WHERE url = ?",
                (next_run, self.url)
            )

    def _finalize(self, status: Status):
        if self._finished: return
        self.status = status
        duration = round(time.time() - self.start_time, 3)

        # Commit changes to DB
        self.manager._handle_task_finish(
            log_id=self.log_id,
            url=self.url,
            spider=self.spider,
            group_path=self.group_path,
            status=status,
            duration=duration,
            http_code=self.http_code,
            state_msg=self.state_msg,
            file_path=self.file_path
        )
        self._finished = True


# --- Main Governance Class ---

class GovernanceManager:
    """
    Central Controller for Spider Governance.
    Manages State, Storage, and Flow Control.
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH, files_path: str = DEFAULT_FILES_PATH):
        self.db = DatabaseHandler(db_path)
        self.storage = StorageHandler(files_path)

        # Sync control signal from DB to memory on startup
        self._control_signal = self.db.get_control_signal(key='global')
        self._signal_lock = threading.RLock()

        logger.info(f"Governance Manager initialized. Signal: {self._control_signal}")

    # --- Helper: Path Normalization & Name Extraction ---

    def _hash(self, text: str) -> str:
        return hashlib.md5(text.encode()).hexdigest()

    # --- 1. Metadata Registration (UI & Entry Points) ---

    def register_group_metadata(self, group_path: Union[str, List[str]], list_url: str, friendly_name: str = None):
        """
        Registers a group and its associated List/Index URL.
        group_path: Can be "spider/news" or ["spider", "news"]
        This establishes the node in the Dashboard.
        Also pre-fills the 'crawl_status' table with the list_url as PENDING.
        """
        # STEP 1: Normalize Input
        norm_group_path = _normalize_group_path(group_path)
        spider_name = _extract_spider_name(norm_group_path)

        try:
            # 1. Update Metadata Registry
            self.db.execute("""
                INSERT INTO task_groups (group_path, list_url, name)
                VALUES (?, ?, ?)
                ON CONFLICT(group_path) DO UPDATE SET
                list_url = excluded.list_url,
                name = excluded.name
            """, (norm_group_path, list_url, friendly_name))

            # 2. Ensure the List URL exists in Status table (for visibility)
            if list_url:
                self.db.execute("""
                    INSERT OR IGNORE INTO crawl_status (url, url_hash, group_path, spider_name, status)
                    VALUES (?, ?, ?, ?, ?)
                """, (list_url, self._hash(list_url), norm_group_path, spider_name, Status.PENDING))

        except Exception as e:
            logger.error(f"Failed to register group {norm_group_path}: {e}")

    # --- 2. Crawl Decision Logic ---

    def should_crawl(self, url: str, max_retries: int = 3) -> bool:
        """
        Determines if a URL should be crawled based on `crawl_status`.
        Handles both recurring Lists (via next_run_at) and one-off Articles (via status).
        """
        row = self.db.fetch_one("""
            SELECT status, retry_count, next_run_at 
            FROM crawl_status 
            WHERE url = ?
        """, (url,))

        # Case 1: New URL
        if not row:
            return True

        status = row['status']
        retry_count = row['retry_count']
        next_run_at = row['next_run_at']

        # Case 2: Currently Running (Concurrency check)
        if status == Status.RUNNING:
            return False

        # Case 3: Recurring Task (List/RSS)
        # If next_run_at is set, we strictly follow the schedule
        if next_run_at:
            if isinstance(next_run_at, str):
                target_ts = datetime.datetime.fromisoformat(next_run_at)
            else:
                target_ts = next_run_at

            # Allow crawl if current time is past the scheduled time
            return datetime.datetime.now() >= target_ts

        # Case 4: One-off Task (Articles)
        if status == Status.SUCCESS:
            return False  # Already done

        if status in [Status.PERM_FAIL, Status.SKIPPED, Status.STOPPED]:
            return False  # Dead end

        if status == Status.TEMP_FAIL:
            return retry_count < max_retries  # Retry if limit not reached

        return True  # Default (e.g., PENDING)

    # --- 3. Session Factory ---

    def transaction(self, url: str, group_path: Union[str, List[str]]):
        """
        Starts a crawling session.
        group_path: Can be "spider/news" or ["spider", "news"]
        """
        # STEP 1: Normalize Input
        norm_group_path = _normalize_group_path(group_path)
        spider_name = _extract_spider_name(norm_group_path)

        return CrawlSession(self, url, spider_name, norm_group_path)

    # --- 4. Internal State Management (Called by Session) ---

    def _handle_task_start(self, url: str, spider: str, group: str) -> int:
        """
        Called when transaction starts.
        1. Insert into Log (Created State), return Log ID.
        2. Update Status to RUNNING.
        """
        url_hash = self._hash(url)

        # 1. Insert Log (Audit Trail)
        cursor = self.db.execute("""
            INSERT INTO crawl_log (url, group_path, spider_name, status, created_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (url, group, spider, Status.RUNNING))
        log_id = cursor.lastrowid

        # 2. Update Status (Dashboard)
        self.db.execute("""
            INSERT INTO crawl_status (url, url_hash, group_path, spider_name, status, last_run_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(url) DO UPDATE SET
                status = ?,
                spider_name = ?,
                last_run_at = CURRENT_TIMESTAMP
        """, (url, url_hash, group, spider, Status.RUNNING, Status.RUNNING, spider))

        return log_id

    def _handle_task_finish(self, log_id, url, spider, group_path, status, duration, http_code, state_msg, file_path):
        """
        Called when transaction ends.
        1. Update Log entry.
        2. Update Status table (handle retries).
        """
        # 1. Close Log Entry
        if log_id:
            self.db.execute("""
                UPDATE crawl_log 
                SET status = ?, duration = ?, http_code = ? 
                WHERE id = ?
            """, (status, duration, http_code, log_id))
        else:
            # Fallback if log_id missing
            self.db.execute("""
                INSERT INTO crawl_log (url, group_path, spider_name, status, http_code, duration)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (url, group_path, spider, status, http_code, duration))

        # 2. Update Current Status
        # Increment retry if TEMP_FAIL, else reset retry count
        retry_inc = 1 if status == Status.TEMP_FAIL else 0
        retry_reset_clause = "retry_count = 0," if status != Status.TEMP_FAIL else ""

        self.db.execute(f"""
            UPDATE crawl_status 
            SET status = ?,
                duration = ?,
                http_code = ?,
                state_msg = ?,
                file_path = ?,
                spider_name = ?,
                {retry_reset_clause}
                retry_count = retry_count + ?
            WHERE url = ?
        """, (status, duration, http_code, state_msg, file_path, spider, retry_inc, url))

        # FIXED: Use group_path in logs
        if status == Status.SUCCESS:
            logger.info(f"[{group_path}] SUCCESS: {url}")
        elif status != Status.SKIPPED:
            logger.warning(f"[{group_path}] FAIL({status.name}): {url}")

    # --- 5. Flow Control & Signals ---

    def pause(self):
        self._set_signal(ControlSignal.PAUSE.value)

    def resume(self):
        self._set_signal(ControlSignal.NORMAL.value)

    def trigger_immediate(self):
        self._set_signal(ControlSignal.IMMEDIATE.value)

    def _set_signal(self, signal_str: str):
        with self._signal_lock:
            self._control_signal = signal_str
            # Persist to DB for consistency across restarts
            self.db.set_control_signal(signal_str)
            logger.info(f"Control signal set to: {signal_str}")

    def wait_interval(self, seconds: float, stop_event: threading.Event = None):
        """
        Smart sleep. Reads memory signal (fast) for Pause/Immediate.
        """
        if seconds <= 0: return

        end_time = time.time() + seconds

        while time.time() < end_time:
            # 1. Stop Event (Highest Priority - Immediate Exit)
            if stop_event and stop_event.is_set():
                break

            # 2. Control Signal (Memory Check)
            current_signal = self._control_signal

            if current_signal == "PAUSE":
                time.sleep(1)
                # While paused, we effectively extend the wait indefinitely
                # until resumed. We do not break the loop.
                continue

            elif current_signal == "IMMEDIATE":
                self._set_signal("NORMAL")  # Consume signal
                logger.info("Immediate execution triggered.")
                break

            # 3. Sleep Chunk
            remaining = end_time - time.time()
            time.sleep(min(0.1, remaining))

    # --- 6. Dashboard Statistics ---

    def get_dashboard_summary(self, spider_filter: str = None) -> List[Dict]:
        """
        Aggregates data for UI.
        Returns groups with their stats AND the specific status of their List URL.
        """
        # 1. Get Metadata Groups
        groups_sql = "SELECT group_path, list_url, name FROM task_groups"
        groups = self.db.fetch_all(groups_sql)

        result = []
        for g in groups:
            g_path = g['group_path']

            # Filter by spider (prefix of group_path)
            if spider_filter and not g_path.startswith(spider_filter):
                continue

            # 2. Aggregate Stats for this group
            stats_row = self.db.fetch_one("""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 2 THEN 1 ELSE 0 END) as success,
                    SUM(CASE WHEN status = 1 THEN 1 ELSE 0 END) as running,
                    SUM(CASE WHEN status IN (3,4) THEN 1 ELSE 0 END) as failed,
                    SUM(CASE WHEN status = 0 THEN 1 ELSE 0 END) as pending
                FROM crawl_status 
                WHERE group_path = ?
            """, (g_path,))

            # 3. Get Status of the specific List/Index URL (The "Anchor")
            list_url_status = None
            if g['list_url']:
                row = self.db.fetch_one("""
                    SELECT status, last_run_at, next_run_at, http_code, state_msg 
                    FROM crawl_status WHERE url = ?
                """, (g['list_url'],))
                if row:
                    list_url_status = dict(row)
                    list_url_status['url'] = g['list_url']

            result.append({
                'group_path': g_path,
                'name': g['name'],
                'stats': dict(stats_row) if stats_row else {},
                'list_url_status': list_url_status
            })

        return result
