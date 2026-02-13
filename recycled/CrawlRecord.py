import os
import logging
import sqlite3
import threading
from datetime import datetime
from collections import OrderedDict


STATUS_NOT_EXIST = -1
STATUS_UNKNOWN = 0
STATUS_DB_ERROR = 1
STATUS_ERROR = 10
STATUS_SUCCESS = 100
STATUS_IGNORED = 110


class CrawlRecord:
    """Record crawler results with SQLite persistence and in-memory cache"""

    def __init__(self, path_parts, cache_size=1000, db_suffix=".db"):
        """
        Initialize with enhanced path handling:
        - Single string input treated as filename
        - Auto-add suffix if missing
        - Ensure parent directories exist
        """
        self.conn = None
        self.lock = threading.Lock()

        self.logger = logging.getLogger('CrawlRecord')
        self.logger.addHandler(logging.StreamHandler())
        self.logger.setLevel(logging.ERROR)

        # Handle path input types
        if isinstance(path_parts, str):
            path_parts = [path_parts]  # Convert to list

        # Extract filename and enforce suffix
        db_filename = path_parts[-1]
        if not db_filename.endswith(db_suffix):
            db_filename += db_suffix

        # Build directory path
        base_dirs = path_parts[:-1] if len(path_parts) > 1 else []
        dir_path = os.path.join(*base_dirs) if base_dirs else "."

        # Create parent directories
        os.makedirs(dir_path, exist_ok=True)

        # Final database path
        self.db_path = os.path.join(dir_path, db_filename)

        self._init_db()  # Initialize database
        self.cache_size = cache_size
        self.memory_cache = OrderedDict()
        self._load_initial_cache()

    def _init_db(self):
        """Initialize database schema"""
        try:
            self.conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False
            )
            cursor = self.conn.cursor()

            # Create table with proper schema
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS crawl_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT UNIQUE NOT NULL,
                    status INTEGER DEFAULT 0,
                    error_count INTEGER DEFAULT 0,
                    extra_info TEXT,
                    created_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Create indexes for faster lookups
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_url ON crawl_records(url)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_updated ON crawl_records(updated_time)')
            self.conn.commit()

        except sqlite3.Error as e:
            self.logger.error(f"Database initialization failed: {str(e)}")
        except Exception as e:
            self.logger.error(f"Database initialization exception: {str(e)}")

    def _load_initial_cache(self):
        """Load top N records into memory cache"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                SELECT id, url, status, error_count, extra_info 
                FROM crawl_records 
                ORDER BY id DESC 
                LIMIT ?
            ''', (self.cache_size,))

            for row in cursor.fetchall():
                url = row[1]
                self.memory_cache[url] = {
                    'id': row[0],
                    'status': row[2],
                    'error_count': row[3],
                    'extra_info': row[4]
                }

        except sqlite3.Error as e:
            self.logger.error(f"Cache loading failed: {str(e)}")
        except Exception as e:
            self.logger.error(f"Cache loading exception: {str(e)}")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __del__(self):
        self.close()

    def record_url_status(self, url, status, extra_info=None) -> bool:
        """
        Record or update URL status

        :param url: Target URL
        :param status: Status code (must be >=10 or 0)
        :param extra_info: Additional metadata (optional)
        """
        if not url:
            return False

        # Validate status input
        if status < STATUS_ERROR:
            self.logger.error(f"Invalid status {status}: Reserved for system use")
            return False

        with self.lock:
            try:
                cursor = self.conn.cursor()
                timestamp = datetime.now().isoformat()

                # Try update existing record
                cursor.execute('''
                    UPDATE crawl_records 
                    SET status = ?, extra_info = ?, updated_time = ?
                    WHERE url = ?
                ''', (status, extra_info, timestamp, url))

                # If no record exists, insert new
                if cursor.rowcount == 0:
                    cursor.execute('''
                        INSERT INTO crawl_records 
                        (url, status, extra_info, created_time, updated_time) 
                        VALUES (?, ?, ?, ?, ?)
                    ''', (url, status, extra_info, timestamp, timestamp))

                self.conn.commit()

                # Update memory cache
                if url in self.memory_cache:
                    self.memory_cache[url].update({
                        'status': status,
                        'extra_info': extra_info
                    })
                else:
                    # Add to cache and enforce size limit
                    self.memory_cache[url] = {
                        'id': cursor.lastrowid,
                        'status': status,
                        'error_count': 0,
                        'extra_info': extra_info
                    }
                    if len(self.memory_cache) > self.cache_size:
                        self.memory_cache.popitem(last=False)
                return True

            except sqlite3.Error as e:
                self.logger.error(f"Status recording failed for {url}: {str(e)}")
                return False
            except Exception as e:
                self.logger.error(f"Status recording exception for {url}: {str(e)}")
                return False

    def get_url_status(self, url, from_db=False):
        """
        Get current URL status

        :param url: Target URL
        :param from_db: Force database lookup
        :return: Status code (STATUS_NOT_EXIST if not found)
        """
        if not url:
            return False

        # Try memory cache first
        with self.lock:
            if url in self.memory_cache:
                return self.memory_cache[url]['status']
        if not from_db:
            return STATUS_NOT_EXIST

        with self.lock:
            try:
                cursor = self.conn.cursor()
                cursor.execute('''
                    SELECT status FROM crawl_records WHERE url = ?
                ''', (url,))

                result = cursor.fetchone()
                if result:
                    status = result[0]

                    # Update memory cache
                    if url not in self.memory_cache:
                        self._add_to_cache(url)
                    return status
            except sqlite3.Error as e:
                self.logger.error(f"Status query failed for {url}: {str(e)}")
                return STATUS_DB_ERROR
            except Exception as e:
                self.logger.error(f"Status query exception for {url}: {str(e)}")
                return STATUS_DB_ERROR
        return STATUS_NOT_EXIST

    def increment_error_count(self, url) -> bool:
        """Increment error counter for URL"""
        if not url:
            return False

        with self.lock:
            try:
                cursor = self.conn.cursor()
                timestamp = datetime.now().isoformat()

                # Try update existing record
                cursor.execute('''
                    UPDATE crawl_records 
                    SET error_count = error_count + 1, 
                        status = ?,
                        updated_time = ?
                    WHERE url = ?
                ''', (STATUS_ERROR, timestamp, url))

                # Create new record if not exists
                if cursor.rowcount == 0:
                    cursor.execute('''
                        INSERT INTO crawl_records 
                        (url, status, error_count, created_time, updated_time) 
                        VALUES (?, ?, 1, ?, ?)
                    ''', (url, STATUS_ERROR, timestamp, timestamp))

                self.conn.commit()

                # Update memory cache
                if url in self.memory_cache:
                    self.memory_cache[url]['error_count'] += 1
                    self.memory_cache[url]['status'] = STATUS_ERROR
                else:
                    self._add_to_cache(url)
                return True

            except sqlite3.Error as e:
                self.logger.error(f"Error count increment failed for {url}: {str(e)}")
                return False
            except Exception as e:
                self.logger.error(f"Error count increment exception for {url}: {str(e)}")
                return False

    def get_error_count(self, url, from_db=False) -> int:
        """
        Get current error count for URL

        :param url: Target URL
        :param from_db: Force database lookup
        :return: Error count (0 if not found)
        """
        if not url:
            return False

        # Try memory cache first
        with self.lock:
            if url in self.memory_cache:
                return self.memory_cache[url]['error_count']
        if not from_db:
            return -1

        try:
            cursor = self.conn.cursor()
            # cursor.execute('PRAGMA journal_mode=WAL;')
            cursor.execute('''
                SELECT error_count FROM crawl_records WHERE url = ?
            ''', (url,))

            result = cursor.fetchone()
            if result:
                count = result[0]

                # Update memory cache
                if url not in self.memory_cache:
                    self._add_to_cache(url)

                return count

        except sqlite3.Error as e:
            self.logger.error(f"Error count query failed for {url}: {str(e)}")
            return -1
        except Exception as e:
            self.logger.error(f"Error count query exception for {url}: {str(e)}")
            return -1

        return 0

    def clear_error_count(self, url) -> bool:
        """Reset error counter for URL"""
        if not url:
            return False
        try:
            cursor = self.conn.cursor()
            timestamp = datetime.now().isoformat()

            cursor.execute('''
                UPDATE crawl_records 
                SET error_count = 0, updated_time = ?
                WHERE url = ?
            ''', (timestamp, url))

            self.conn.commit()

            # Update memory cache
            if url in self.memory_cache:
                self.memory_cache[url]['error_count'] = 0
            else:
                self._add_to_cache(url)
            return True

        except sqlite3.Error as e:
            self.logger.error(f"Error count clear failed for {url}: {str(e)}")
            return False
        except Exception as e:
            self.logger.error(f"Error count clear exception for {url}: {str(e)}")
            return False

    def _add_to_cache(self, url):
        """Add specific record to memory cache"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                SELECT id, status, error_count, extra_info 
                FROM crawl_records 
                WHERE url = ?
            ''', (url,))

            result = cursor.fetchone()
            if result:
                # Add to cache and enforce size limit
                self.memory_cache[url] = {
                    'id': result[0],
                    'status': result[1],
                    'error_count': result[2],
                    'extra_info': result[3]
                }
                if len(self.memory_cache) > self.cache_size:
                    self.memory_cache.popitem(last=False)

        except sqlite3.Error as e:
            self.logger.error(f"Cache update failed for {url}: {str(e)}")
        except Exception as e:
            self.logger.error(f"Cache update exception for {url}: {str(e)}")
            return False

    def close(self):
        """Clean up resources"""
        if self.conn:
            self.conn.close()
            self.conn = None
