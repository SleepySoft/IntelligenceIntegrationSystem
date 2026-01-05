import time
import os
import threading
import json
import logging
from dataclasses import dataclass, field, asdict
from collections import deque
from typing import Dict, Optional, Any
from datetime import datetime
from enum import Enum

from Tools.CrawlRecord import CrawlRecord, STATUS_SUCCESS, STATUS_IGNORED, STATUS_ERROR


# --- Enums ---

class CrawlerState(Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"


class UrlType(Enum):
    LIST = "list"  # 列表页/索引页 (循环抓取)
    CONTENT = "content"  # 内容页/详情页 (一次性抓取)


# --- Data Structures ---

@dataclass
class CrawlerConfig:
    """静态配置：运行时一般不频繁修改"""
    name: str
    base_interval: float = 1.0  # URL抓取间隔
    round_interval: float = 60.0  # 列表循环轮次间隔
    max_retries: int = 3
    storage_path: str = "./data"


@dataclass
class RuntimeStats:
    """动态统计：高频更新"""
    start_time: float = field(default_factory=time.time)

    # 计数器
    total_processed: int = 0
    total_success: int = 0
    total_error: int = 0

    # 性能指标
    total_response_time: float = 0.0

    # 当前状态
    current_url: str = ""
    current_url_type: str = ""  # 'list' or 'content'

    # 错误分布 {"404": 10, "timeout": 5}
    error_distribution: Dict[str, int] = field(default_factory=dict)


@dataclass
class LogEntry:
    """用于前端展示的单条日志记录"""
    timestamp: str
    url: str
    url_type: str
    status: int  # HTTP status or Internal status
    latency: float
    msg: str


class CrawlerContext:
    """
    单个爬虫的完整上下文
    包含：配置、统计、数据库句柄、控制锁、暂停信号、最近日志
    """

    def __init__(self, config: CrawlerConfig, db_handler):
        self.config = config
        self.db_handler = db_handler

        self.stats = RuntimeStats()
        self.state = CrawlerState.IDLE

        # 线程安全锁：保护 stats 和 recent_logs 的并发读写
        self.lock = threading.RLock()

        # 暂停控制信号：Set=运行, Clear=暂停
        self.pause_event = threading.Event()
        self.pause_event.set()

        # 最近 N 条记录 (用于前端实时流展示，不查库)
        self.recent_logs = deque(maxlen=100)


# --- Singleton Monitor Class ---

class CrawlerMonitor:
    _instance = None
    _init_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        """Thread-safe Singleton Pattern"""
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = super(CrawlerMonitor, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        # 防止多次初始化
        if getattr(self, '_initialized', False):
            return

        self._initialized = True
        self.crawlers: Dict[str, CrawlerContext] = {}
        # 全局锁：仅保护 crawlers 字典本身的增删
        self.registry_lock = threading.RLock()

        self._setup_logger()

    def _setup_logger(self):
        self.logger = logging.getLogger('CrawlerMonitor')
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)

    # ===========================
    # 1. Registration (注册)
    # ===========================

    def register_crawler(self, name: str, storage_path: str = "./data",
                         base_interval: float = 1.0, round_interval: float = 60.0) -> bool:
        """注册一个新的爬虫实例"""
        with self.registry_lock:
            if name in self.crawlers:
                self.logger.warning(f"Crawler '{name}' already registered.")
                return False

            try:
                # 确保路径存在
                os.makedirs(storage_path, exist_ok=True)

                # 初始化 DB (复用 CrawlRecord)
                db_path = [storage_path, f"{name}_record"]
                record_handler = CrawlRecord(db_path)

                config = CrawlerConfig(
                    name=name,
                    storage_path=storage_path,
                    base_interval=base_interval,
                    round_interval=round_interval
                )

                ctx = CrawlerContext(config, record_handler)
                self.crawlers[name] = ctx
                self.logger.info(f"Crawler registered: {name}")
                return True
            except Exception as e:
                self.logger.error(f"Failed to register crawler {name}: {e}")
                return False

    # ===========================
    # 2. Flow Control & Logic (核心逻辑)
    # ===========================

    def should_crawl(self, name: str, url: str, url_type: UrlType = UrlType.CONTENT) -> bool:
        """
        判断是否需要抓取。
        - LIST 类型：通常总是允许抓取（用于发现新内容），或基于 TTL (此处简化为总是 True)
        - CONTENT 类型：检查数据库是否已成功，或是否达到重试上限
        """
        ctx = self._get_ctx(name)
        if not ctx: return False

        # 如果是列表页，通常我们需要它来发现新链接，所以除非被黑名单，否则一般允许
        if url_type == UrlType.LIST:
            return True

        # 如果是内容页，检查历史记录
        # 注意：这里直接调用 db_handler，它是线程安全的(sqlite3 connect时check_same_thread=False)
        status = ctx.db_handler.get_url_status(url, from_db=True)

        if status == 100:  # STATUS_SUCCESS
            return False

        if status == 110:  # STATUS_IGNORED
            return False

        if status >= 10:  # STATUS_ERROR
            err_count = ctx.db_handler.get_error_count(url, from_db=True)
            if err_count >= ctx.config.max_retries:
                return False  # 放弃

        return True

    def report_start_task(self, name: str, url: str, url_type: UrlType):
        """爬虫线程调用：开始抓取前"""
        ctx = self._get_ctx(name)
        if not ctx: return

        with ctx.lock:
            ctx.state = CrawlerState.RUNNING
            ctx.stats.current_url = url
            ctx.stats.current_url_type = url_type.value
            # 使用隐藏属性记录开始时间，计算耗时
            ctx.stats._temp_start_time = time.time()

    def report_finish_task(self, name: str, url: str, url_type: UrlType,
                           status: int, content: Optional[bytes] = None, error_msg: str = ""):
        """爬虫线程调用：抓取结束后"""
        ctx = self._get_ctx(name)
        if not ctx: return

        end_time = time.time()
        start_time = getattr(ctx.stats, '_temp_start_time', end_time)
        duration = end_time - start_time

        # 1. 落盘 (仅 Content 类型且成功时保存文件，List通常不需要保存历史HTML)
        file_path = ""
        if status == 100 and content and url_type == UrlType.CONTENT:
            file_path = self._save_content(ctx, url, content)

        # 2. 记录到 SQLite (复用 CrawlRecord)
        extra_info = {
            "type": url_type.value,
            "latency": round(duration, 3),
            "file": file_path,
            "msg": error_msg
        }

        if status == 100:
            ctx.db_handler.record_url_status(url, status, json.dumps(extra_info))
            if url_type == UrlType.CONTENT:
                ctx.db_handler.clear_error_count(url)
        else:
            ctx.db_handler.increment_error_count(url)
            ctx.db_handler.record_url_status(url, status, json.dumps(extra_info))

        # 3. 更新内存统计 & 最近日志 (必须加锁)
        with ctx.lock:
            ctx.stats.total_processed += 1
            ctx.stats.total_response_time += duration

            if status == 100:
                ctx.stats.total_success += 1
            else:
                ctx.stats.total_error += 1
                err_key = str(status)
                ctx.stats.error_distribution[err_key] = ctx.stats.error_distribution.get(err_key, 0) + 1

            # 添加到滚动日志 (供前端展示)
            log_entry = LogEntry(
                timestamp=datetime.now().strftime("%H:%M:%S"),
                url=url,
                url_type=url_type.value,
                status=status,
                latency=round(duration, 3),
                msg=error_msg or "OK"
            )
            ctx.recent_logs.append(asdict(log_entry))

            # 重置当前状态
            ctx.stats.current_url = ""

    # ===========================
    # 3. Blocking Control (速率控制与暂停)
    # ===========================

    def wait_interval(self, name: str):
        """
        爬虫在每次请求前调用。
        实现了：速率限制 + 暂停/恢复
        """
        ctx = self._get_ctx(name)
        if not ctx: return

        # 1. 暂停控制 (如果被 clear，这里会阻塞直到 set)
        ctx.pause_event.wait()

        # 2. 速率限制
        time.sleep(ctx.config.base_interval)

    def wait_round(self, name: str):
        """
        List 爬虫在完成一轮循环后调用。
        """
        ctx = self._get_ctx(name)
        if not ctx: return

        self.logger.info(f"[{name}] Waiting for next round ({ctx.config.round_interval}s)...")

        # 即使在 sleep 时也要支持暂停，所以分段 sleep
        target_wake_time = time.time() + ctx.config.round_interval

        while time.time() < target_wake_time:
            ctx.pause_event.wait()  # 检查是否暂停
            time.sleep(0.5)

    # ===========================
    # 4. Frontend/API Interfaces
    # ===========================

    def get_snapshot(self) -> Dict:
        """获取所有爬虫的实时快照 (JSON-friendly)"""
        snapshot = {}
        # 使用 copy 防止遍历时字典变更，但要注意 registry_lock
        with self.registry_lock:
            active_crawlers = list(self.crawlers.items())

        for name, ctx in active_crawlers:
            with ctx.lock:  # 锁定单个爬虫的上下文以读取 consistent 的数据

                # 计算平均速度
                elapsed = time.time() - ctx.stats.start_time
                avg_speed = 0
                if elapsed > 0:
                    avg_speed = ctx.stats.total_processed / elapsed * 60

                avg_latency = 0
                if ctx.stats.total_processed > 0:
                    avg_latency = ctx.stats.total_response_time / ctx.stats.total_processed

                snapshot[name] = {
                    "state": "PAUSED" if not ctx.pause_event.is_set() else ctx.state.value,
                    "config": asdict(ctx.config),
                    "stats": {
                        "processed": ctx.stats.total_processed,
                        "success": ctx.stats.total_success,
                        "error": ctx.stats.total_error,
                        "avg_speed_per_min": round(avg_speed, 2),
                        "avg_latency": round(avg_latency, 3),
                        "current_url": ctx.stats.current_url,
                        "current_type": ctx.stats.current_url_type
                    },
                    # 倒序排列，最新的在前
                    "recent_logs": list(reversed(ctx.recent_logs))
                }
        return snapshot

    def control_crawler(self, name: str, action: str, params: Any = None) -> bool:
        """前端控制接口"""
        ctx = self._get_ctx(name)
        if not ctx: return False

        self.logger.info(f"Control [{name}]: {action} -> {params}")

        if action == "pause":
            ctx.pause_event.clear()
            with ctx.lock:
                ctx.state = CrawlerState.PAUSED

        elif action == "resume":
            ctx.pause_event.set()
            with ctx.lock:
                ctx.state = CrawlerState.RUNNING

        elif action == "update_interval":
            # 动态调整抓取间隔
            ctx.config.base_interval = float(params)

        elif action == "update_url_status":
            # 手动干预 URL 状态 (例如重置失败的任务)
            url = params.get('url')
            new_status = params.get('status')
            if url and new_status is not None:
                ctx.db_handler.record_url_status(url, new_status, extra_info="Manual Update")
                # 如果是重置为未抓取，可能还需要清除错误计数
                if new_status < 10:
                    ctx.db_handler.clear_error_count(url)

        return True

    # ===========================
    # 5. Helpers
    # ===========================

    def _get_ctx(self, name) -> Optional[CrawlerContext]:
        # 读操作通常不需要加重锁，因为字典本身是线程安全的，且我们不在此处删除键
        return self.crawlers.get(name)

    def _save_content(self, ctx: CrawlerContext, url: str, content: bytes) -> str:
        """保存内容到文件系统，返回相对路径"""
        import hashlib
        try:
            url_hash = hashlib.md5(url.encode()).hexdigest()
            # 目录结构: data/files/<crawler_name>/<YYYYMMDD>/<hash>.html
            date_dir = datetime.now().strftime("%Y%m%d")
            save_dir = os.path.join(ctx.config.storage_path, "files", ctx.config.name, date_dir)
            os.makedirs(save_dir, exist_ok=True)

            filename = f"{url_hash}.html"
            full_path = os.path.join(save_dir, filename)

            with open(full_path, "wb") as f:
                f.write(content)

            return os.path.join(date_dir, filename)  # 返回相对路径即可
        except Exception as e:
            self.logger.error(f"Save file error: {e}")
            return ""


# ===========================
# USAGE DEMO (Multi-threaded)
# ===========================
if __name__ == "__main__":
    monitor = CrawlerMonitor()  # Singleton instance
    monitor.register_crawler("blog_spider", base_interval=0.5)


    def spider_worker(name):
        monitor = CrawlerMonitor()  # Gets the same instance

        # 模拟：先抓列表，再抓详情
        list_url = "http://blog.com/index"

        while True:
            # 1. 列表阶段
            monitor.wait_interval(name)
            if monitor.should_crawl(name, list_url, UrlType.LIST):
                monitor.report_start_task(name, list_url, UrlType.LIST)
                time.sleep(0.2)  # Network
                # 假设解析出了文章链接
                article_urls = [f"http://blog.com/post/{int(time.time())}_{i}" for i in range(3)]
                monitor.report_finish_task(name, list_url, UrlType.LIST, 100)

            # 2. 详情阶段
            for url in article_urls:
                monitor.wait_interval(name)  # 包含暂停/速率控制

                if monitor.should_crawl(name, url, UrlType.CONTENT):
                    monitor.report_start_task(name, url, UrlType.CONTENT)
                    print(f"[{name}] Crawling content: {url}")
                    time.sleep(0.3)  # Network

                    # 模拟结果
                    monitor.report_finish_task(
                        name, url, UrlType.CONTENT,
                        status=100,
                        content=b"<html>...</html>"
                    )

            # 3. 轮次等待
            monitor.wait_round(name)


    t = threading.Thread(target=spider_worker, args=("blog_spider",))
    t.start()

    # 模拟前端读取数据
    for _ in range(5):
        time.sleep(1)
        data = monitor.get_snapshot()
        logs = data['blog_spider']['recent_logs']
        print(f"\n--- Monitor Stats: Processed {data['blog_spider']['stats']['processed']} ---")
        if logs:
            print(f"Latest Log: {logs[0]}")  # 打印最新一条日志

    # 停止测试
    # t.join() # 实际使用中需要优雅退出机制


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
