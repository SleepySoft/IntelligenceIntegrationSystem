import os
import time
import json
import logging
import sqlite3
import datetime
import threading
from pathlib import Path
from enum import IntEnum, Enum
from typing import Optional, Union, List

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("CrawlGovernance")


# --- Enums ---

class Status(IntEnum):
    PENDING = 0  # In queue (Future use)
    RUNNING = 1  # Currently processing (Real-time visibility)
    SUCCESS = 2  # Finished successfully
    TEMP_FAIL = 3  # Network error, timeout (Retryable)
    PERM_FAIL = 4  # Parse error, 404 (Non-retryable)
    SKIPPED = 5  # Logic decided to skip
    STOPPED = 6  # Manually stopped


class ControlSignal(Enum):
    NORMAL = "NORMAL"
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    IMMEDIATE = "IMMEDIATE"  # Skip current wait


# --- Constants ---

DEFAULT_DB_PATH = "data/db/governance.db"
DEFAULT_FILES_PATH = "data/files"


# --- Database Handler ---

class DatabaseHandler:
    """
    Handles SQLite operations with the new schema design:
    1. task_groups: Hierarchy and config.
    2. crawl_status: The 'Dashboard' (Current state of unique URLs).
    3. crawl_log: The 'History' (Audit trail).
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

            # 1. Task Groups (Hierarchy Registry)
            # group_path: e.g., "google_bot/news_list" or "google_bot/news_list/tech"
            # seed_url: Optional, if this group represents a specific entry point.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS task_groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_path TEXT UNIQUE NOT NULL, 
                    seed_url TEXT,
                    config_json TEXT DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 2. Crawl Status (The "State" Table - Keyed by URL)
            # Stores the LATEST information about a URL.
            # file_path: Stores the path to the saved content (if any).
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

            # 3. Crawl Log (The "Flow" Table - History)
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

            # 4. System Control (For dynamic sleep/resume)
            # key: usually 'global' or spider_name
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sys_control (
                    key TEXT PRIMARY KEY,
                    signal TEXT DEFAULT 'NORMAL',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Indexes
            cur.execute("CREATE INDEX IF NOT EXISTS idx_status_group ON crawl_status(group_path)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_log_url ON crawl_log(url)")
            cur.execute("INSERT OR IGNORE INTO sys_control (key, signal) VALUES ('global', 'NORMAL')")

            self.conn.commit()

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
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

    def get_control_signal(self, key='global') -> str:
        row = self.fetch_one("SELECT signal FROM sys_control WHERE key = ?", (key,))
        return row['signal'] if row else "NORMAL"

    def set_control_signal(self, signal: str, key='global'):
        self.execute("INSERT OR REPLACE INTO sys_control (key, signal) VALUES (?, ?)", (key, signal))


# --- File Storage Handler ---

class StorageHandler:
    """
    Decoupled storage.
    User specifies the logic name/path, this class handles the physical write.
    """

    def __init__(self, base_path: str):
        self.base_path = Path(base_path)

    def save(self, content: Union[bytes, str], relative_path: str) -> str:
        """
        Saves content to a specific path defined by the caller.

        Args:
            content: The data to save.
            relative_path: e.g., "my_spider/2025/01/article_123.html"

        Returns:
            The absolute path of the saved file.
        """
        if not content:
            return ""

        if isinstance(content, str):
            content = content.encode('utf-8')

        # Construct full path
        full_path = self.base_path / relative_path

        # Ensure directory exists
        full_path.parent.mkdir(parents=True, exist_ok=True)

        with open(full_path, "wb") as f:
            f.write(content)

        return str(full_path.absolute())


# --- Context Manager (Session) ---

def normalize_group_path(group_path: str | List[str]):
    group_paths = list(group_path) \
        if isinstance(group_path, (list, tuple, set)) \
        else [str(group_path)]
    return '/'.join(group_paths)


class CrawlSession:
    """
    Manages the lifecycle of a single URL crawl.
    1. Starts: Updates Status to RUNNING.
    2. Ends: Updates Status to SUCCESS/FAIL, Updates Log, Updates Next Run (if applicable).
    """

    def __init__(self, manager, url: str, spider: str, group_path: str | List[str]):
        self.manager = manager
        self.url = url
        self.spider = spider

        self.group_path = normalize_group_path(group_path)
        self.start_time = time.time()

        self.log_id = None

        # State holders
        self.status = Status.RUNNING
        self.http_code = None
        self.state_msg = None
        self.file_path = None
        self._finished = False

    def __enter__(self):
        # 1. 记录开始：
        #    a. 更新 Status 表 (Dashboard)
        #    b. 插入 Log 表 (History) 并获取 ID
        self.log_id = self.manager._handle_task_start(self.url, self.spider, self.group_path)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if not self._finished:
            if exc_type:
                # Uncaught exception
                self.fail_perm(http_code=500, state_msg=f"Exception: {str(exc_val)}")
                logger.error(f"Session {self.url} crashed: {exc_val}")
            else:
                # Code exited block without calling success/fail
                self.fail_temp(state_msg="Exited without status")

    def save_file(self, content: Union[bytes, str], filename: str, sub_folder: str = ""):
        """
        Explicitly save file.
        Path will be: data_dir / spider_name / group_path / sub_folder / filename
        (Or user can define their own structure via sub_folder)
        """
        # Construct a logical path.
        # Example: spider_name/group_a/2023-10/file.html
        rel_path = Path(self.spider) / self.group_path / sub_folder / filename
        self.file_path = self.manager.storage.save(content, str(rel_path))
        return self.file_path

    def success(self, state_msg="OK"):
        self.state_msg = state_msg
        self._finalize(Status.SUCCESS)

    def skip(self, state_msg="Skipped"):
        self.state_msg = state_msg
        self._finalize(Status.SKIPPED)

    def fail_temp(self, http_code=0, state_msg="Retryable Error"):
        self.http_code = http_code
        self.state_msg = state_msg
        self._finalize(Status.TEMP_FAIL)

    def fail_perm(self, http_code=0, state_msg="Permanent Error"):
        self.http_code = http_code
        self.state_msg = state_msg
        self._finalize(Status.PERM_FAIL)

    def _finalize(self, status: Status):
        if self._finished: return
        self.status = status
        duration = round(time.time() - self.start_time, 3)

        # 2. 记录结束：
        #    a. 更新 Status 表
        #    b. 更新 Log 表 (使用 self.log_id)
        self.manager._handle_task_finish(
            log_id=self.log_id,  # <--- 传入 ID
            url=self.url,
            spider=self.spider,
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
    Central Controller.
    Decoupled from specific spiders.
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH, files_path: str = DEFAULT_FILES_PATH):
        self.db = DatabaseHandler(db_path)
        self.storage = StorageHandler(files_path)

        # --- 变更点 1: 初始化时将 DB 状态加载到内存 ---
        # 默认 NORMAL，如果上次是非正常关闭且处于 PAUSE 状态，重启后应保持 PAUSE
        self._control_signal = self.db.get_control_signal(key='global')
        self._signal_lock = threading.RLock()  # 保护内存变量的读写安全

        logger.info(f"System initialized with signal: {self._control_signal}")

    # --- 1. Registration (Groups & Seeds) ---

    def register_group(self, group_path: str, seed_url: Optional[str] = None, config: dict = None):
        """
        Registers a virtual group.
        If seed_url is provided, it is treated as the 'List' or 'RSS' entry point for this group.
        """
        group_path = normalize_group_path(group_path)
        config_str = json.dumps(config if config else {})
        try:
            # Upsert Group
            self.db.execute("""
                INSERT INTO task_groups (group_path, seed_url, config_json)
                VALUES (?, ?, ?)
                ON CONFLICT(group_path) DO UPDATE SET
                seed_url = excluded.seed_url,
                config_json = excluded.config_json
            """, (group_path, seed_url, config_str))

            # If there is a seed URL, ensure it exists in crawl_status so we can track it
            if seed_url:
                # We do not overwrite status if it exists, just ensure record is there
                self.db.execute("""
                    INSERT OR IGNORE INTO crawl_status (url, url_hash, group_path, spider_name, status)
                    VALUES (?, ?, ?, ?, ?)
                """, (seed_url, self._hash(seed_url), group_path, "system", Status.PENDING))

        except Exception as e:
            logger.error(f"Failed to register group {group_path}: {e}")

    def should_crawl(self, url: str, max_retries: int = 3) -> bool:
        """
        Determines if a URL should be crawled based on `crawl_status`.
        """
        row = self.db.fetch_one("""
            SELECT status, retry_count, next_run_at 
            FROM crawl_status 
            WHERE url = ?
        """, (url,))

        # 1. New URL: Never seen before -> Crawl it
        if not row:
            return True

        status = row['status']
        retry_count = row['retry_count']
        next_run_at = row['next_run_at']

        # 2. Running State: Don't touch if currently running
        if status == Status.RUNNING:
            return False

        # 3. Recurrent Logic (For Lists/RSS that have a next_run_at set)
        if next_run_at:
            # Parse DB timestamp (assuming ISO format or similar)
            # If current time >= next_run_at, then Crawl.
            if isinstance(next_run_at, str):
                target_ts = datetime.datetime.fromisoformat(next_run_at)
            else:
                target_ts = next_run_at  # Assuming it's already datetime object

            now = datetime.datetime.now()
            if now >= target_ts:
                return True
            else:
                return False

        # 4. One-off Logic (For Articles)

        # If already succeeded -> Don't crawl again
        if status == Status.SUCCESS:
            return False

        # If permanently failed or skipped -> Don't crawl again
        if status in [Status.PERM_FAIL, Status.SKIPPED, Status.STOPPED]:
            return False

        # If temporary fail -> Check retries
        if status == Status.TEMP_FAIL:
            if retry_count < max_retries:
                return True
            else:
                # Optional: Auto-mark as GIVE_UP/PERM_FAIL in DB to save future checks?
                # For now, just say No.
                return False

        # Default fallback (e.g. status PENDING)
        return True

    # --- 2. Session Factory ---

    def transaction(self, spider_name: str, url: str, group_path: str | List[str]):
        """
        Starts a crawling transaction.
        """
        return CrawlSession(self, url, spider_name, group_path)

    # --- 3. Internal DB Updates (Called by Session) ---

    def _handle_task_start(self, url: str, spider: str, group: str) -> int:
        """
        任务开始时调用：
        1. 插入 Log 表 (记录开始)，返回 log_id。
        2. 更新 Status 表 (标记为正在运行)。
        """
        url_hash = self._hash(url)

        # 1. 插入 Log 表 (Created State)
        # 注意：此时 duration 为 0 或 NULL，status 为 RUNNING
        cursor = self.db.execute("""
            INSERT INTO crawl_log (url, group_path, spider_name, status, created_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (url, group, spider, Status.RUNNING))

        log_id = cursor.lastrowid  # <--- 获取主键 ID

        # 2. 更新 Status 表 (Dashboard)
        self.db.execute("""
            INSERT INTO crawl_status (url, url_hash, group_path, spider_name, status, last_run_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(url) DO UPDATE SET
                status = ?,
                spider_name = ?,
                last_run_at = CURRENT_TIMESTAMP
        """, (url, url_hash, group, spider, Status.RUNNING, Status.RUNNING, spider))

        return log_id

    def _handle_task_finish(self, log_id, url, spider, status, duration, http_code, state_msg, file_path):
        """
        任务结束时调用：
        1. 根据 log_id 更新 Log 表。
        2. 更新 Status 表。
        """

        # 1. 回写 Log 表 (完善记录)
        # 只有存在有效的 log_id 时才更新 (防止极端情况下 __enter__ 失败)
        if log_id:
            self.db.execute("""
                UPDATE crawl_log 
                SET status = ?, 
                    duration = ?, 
                    http_code = ? 
                WHERE id = ?
            """, (status, duration, http_code, log_id))
        else:
            # 补救措施：如果丢失了 log_id，至少插入一条新的
            logger.warning(f"Missing log_id for {url}, inserting new log entry.")
            self.db.execute("""
                INSERT INTO crawl_log (url, spider_name, status, http_code, duration)
                VALUES (?, ?, ?, ?, ?)
            """, (url, spider, status, http_code, duration))

        # 2. 更新 Status 表 (逻辑不变)
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

        # Log 输出
        if status == Status.SUCCESS:
            logger.info(f"[{spider}] SUCCESS: {url} ({duration}s)")
        else:
            logger.warning(f"[{spider}] FINISHED({status}): {url}")

    # --- 4. Dynamic Wait Control ---

    def pause(self):
        """Web 端调用此方法暂停爬虫"""
        self._set_signal(ControlSignal.PAUSE.value)

    def resume(self):
        """Web 端调用此方法恢复爬虫"""
        self._set_signal(ControlSignal.NORMAL.value)

    def trigger_immediate(self):
        """Web 端调用此方法立即跳过等待"""
        self._set_signal(ControlSignal.IMMEDIATE.value)

    def _set_signal(self, signal_str: str):
        with self._signal_lock:
            self._control_signal = signal_str
            # 异步或同步写库均可，这里为了数据安全选同步写库
            self.db.set_control_signal(signal_str, key='global')
            logger.info(f"Signal changed to: {signal_str}")

    def wait_interval(self, seconds: float, stop_event: threading.Event = None):
        """
        Args:
            seconds: 计划等待时长
            stop_event: 线程级退出信号 (Ctrl+C 等)
        """
        if seconds <= 0:
            return

        end_time = time.time() + seconds

        while time.time() < end_time:
            # 1. 检查线程退出信号 (最高优先级)
            if stop_event and stop_event.is_set():
                break

            # 2. 检查业务控制信号 (直接读内存，极快)
            # 使用临时变量读取，避免锁竞争过重
            current_signal = self._control_signal

            if current_signal == "PAUSE":
                # 如果是暂停，就死循环空转，直到信号变回 NORMAL 或收到 stop_event
                time.sleep(1)
                # 注意：暂停时我们通常推迟 end_time，保证唤醒后不把剩下的时间“吞掉”
                # 或者简单的策略：暂停只是一种特殊的“无限长 sleep”，唤醒后立即执行
                continue

            elif current_signal == "IMMEDIATE":
                # 立即执行：消耗掉这个信号，改为 NORMAL，然后 break
                self._set_signal("NORMAL")
                logger.info("Immediate execution triggered.")
                break

            # 3. 正常的睡眠切片
            remaining = end_time - time.time()
            sleep_chunk = min(0.1, remaining)  # 0.1s 的响应粒度足够了
            if sleep_chunk > 0:
                time.sleep(sleep_chunk)

    # --- Helpers ---

    def _hash(self, text: str) -> str:
        import hashlib
        return hashlib.md5(text.encode()).hexdigest()

    def get_retry_count(self, url: str) -> int:
        row = self.db.fetch_one("SELECT retry_count FROM crawl_status WHERE url=?", (url,))
        return row['retry_count'] if row else 0