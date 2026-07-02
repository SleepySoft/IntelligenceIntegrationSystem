"""
System resource monitoring module for tracking system and process metrics.
Provides thread-safe resource monitoring and management capabilities.
"""
import os
import psutil
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional, Set, Any, Callable


class SystemMonitor:
    """Main system monitoring class with thread-safe operations."""

    def __init__(self, thread_name_resolver: Optional[Callable[[int], str]] = None):
        """
        Initialize the system monitor.

        Args:
            thread_name_resolver: Optional callable that maps a native thread id (TID)
                to a human-readable name. Used to enrich per-thread stats for the
                current Python process.
        """
        self.monitored_pids: Set[int] = set()
        self.process_data: Dict[int, Dict[str, Any]] = {}
        self.system_data: Dict[str, Any] = {}
        self.lock = threading.RLock()
        self.running = False
        self.monitor_thread: Optional[threading.Thread] = None

        # 用于 Python 线程名解析（仅对本进程有效）
        self._thread_name_resolver = thread_name_resolver

        # 缓存上一次线程 CPU 时间，用于计算线程级 CPU 百分比
        self._thread_cpu_cache: Dict[int, Dict[str, Any]] = {}
        self._thread_cpu_cache_lock = threading.Lock()

    def add_process(self, pid: int) -> bool:
        """
        Add a process to monitoring list.

        Args:
            pid: Process ID to monitor

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            process = psutil.Process(pid)
            with self.lock:
                self.monitored_pids.add(pid)
                self.process_data[pid] = {
                    'process': process,
                    'name': process.name(),
                    'added_time': datetime.now()
                }
            return True
        except psutil.NoSuchProcess:
            return False
        except psutil.AccessDenied:
            return False

    def remove_process(self, pid: int) -> bool:
        """
        Remove a process from monitoring list.

        Args:
            pid: Process ID to remove from monitoring

        Returns:
            bool: True if removed, False if not found
        """
        with self.lock:
            if pid in self.monitored_pids:
                self.monitored_pids.remove(pid)
                if pid in self.process_data:
                    del self.process_data[pid]
                return True
            return False

    def get_process_stats(self, pid: int) -> Optional[Dict[str, Any]]:
        """
        Get detailed statistics for a specific process.

        Args:
            pid: Process ID to get stats for

        Returns:
            dict: Process statistics or None if not found
        """
        if pid not in self.monitored_pids:
            return None

        try:
            process = psutil.Process(pid)
            with process.oneshot():
                return {
                    'pid': pid,
                    'name': process.name(),
                    'cpu_percent': process.cpu_percent(),
                    'memory_info': process.memory_info()._asdict(),
                    'memory_percent': process.memory_percent(),
                    'num_handles': self._get_handle_count(process),
                    'num_threads': process.num_threads(),
                    'create_time': process.create_time(),
                    'status': process.status(),
                    'io_counters': process.io_counters()._asdict() if process.io_counters() else None,
                    'connections': len(process.connections()),
                    'cpu_times': process.cpu_times()._asdict()
                }
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            self.remove_process(pid)
            return None

    def get_system_stats(self) -> Dict[str, Any]:
        """
        Get comprehensive system statistics.

        Returns:
            dict: System-wide resource usage statistics
        """
        return {
            'timestamp': datetime.now().isoformat(),
            'timestamp_formatted': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'cpu': {
                'percent': psutil.cpu_percent(interval=0.1),
                'cores': psutil.cpu_count(logical=False),
                'logical_cores': psutil.cpu_count(logical=True),
                'times': psutil.cpu_times()._asdict(),
                'temperatures': self._get_cpu_temperatures()
            },
            'memory': {
                'total': psutil.virtual_memory().total,
                'available': psutil.virtual_memory().available,
                'percent': psutil.virtual_memory().percent,
                'used': psutil.virtual_memory().used,
                'free': psutil.virtual_memory().free
            },
            'swap': {
                'total': psutil.swap_memory().total,
                'used': psutil.swap_memory().used,
                'free': psutil.swap_memory().free,
                'percent': psutil.swap_memory().percent
            },
            'disk': {
                'usage': psutil.disk_usage('/')._asdict(),
                'io_counters': psutil.disk_io_counters()._asdict() if psutil.disk_io_counters() else None
            },
            'network': {
                'io_counters': psutil.net_io_counters()._asdict() if psutil.net_io_counters() else None,
                'connections': len(psutil.net_connections())
            },
            'boot_time': psutil.boot_time(),
            'users': [user._asdict() for user in psutil.users()]
        }

    def get_thread_stats(self, pid: int) -> Optional[List[Dict[str, Any]]]:
        """
        获取指定进程内每个线程的 CPU 时间、CPU 占用率等信息。

        Args:
            pid: 进程 ID

        Returns:
            list: 线程统计列表，按 CPU 占用率降序排列；进程不存在或无权访问时返回 None。
        """
        if pid not in self.monitored_pids:
            return None

        try:
            process = psutil.Process(pid)
            sample_time = time.time()
            current = []

            with self._thread_cpu_cache_lock:
                cache = self._thread_cpu_cache.get(pid, {})
                new_cache = {}

                for t in process.threads():
                    tid = t.id
                    user = t.user_time
                    system = t.system_time
                    total = user + system

                    prev = cache.get(tid)
                    cpu_percent = 0.0
                    if prev:
                        dt = total - prev.get('total_time', 0)
                        dsample = sample_time - prev.get('sample_time', sample_time)
                        if dsample > 0:
                            # psutil 返回的是系统时钟滴答数，换算成百分比
                            cpu_percent = (dt / dsample) * 100.0

                    new_cache[tid] = {
                        'sample_time': sample_time,
                        'user_time': user,
                        'system_time': system,
                        'total_time': total,
                    }

                    name = None
                    if self._thread_name_resolver and pid == os.getpid():
                        name = self._thread_name_resolver(tid)

                    current.append({
                        'tid': tid,
                        'name': name or f'thread-{tid}',
                        'user_time': user,
                        'system_time': system,
                        'cpu_percent': round(cpu_percent, 2),
                        'status': getattr(t, 'status', 'unknown'),
                    })

                self._thread_cpu_cache[pid] = new_cache

            current.sort(key=lambda x: x['cpu_percent'], reverse=True)
            return current

        except (psutil.NoSuchProcess, psutil.AccessDenied):
            self.remove_process(pid)
            return None

    def _get_cpu_temperatures(self) -> Optional[Dict[str, Any]]:
        """
        获取 CPU 温度（Linux 通常可用，Windows 可能为空）。
        返回 {sensor_label: current_temp, ...} 或 None。
        """
        try:
            temps = psutil.sensors_temperatures()
            if not temps:
                return None
            result = {}
            for name, entries in temps.items():
                for entry in entries:
                    label = entry.label or name
                    if entry.current is not None:
                        result[label] = round(entry.current, 1)
            return result if result else None
        except Exception:
            return None

    def _get_handle_count(self, process: psutil.Process) -> Optional[int]:
        """
        Get handle count for process (Windows specific).

        Args:
            process: psutil Process object

        Returns:
            int: Number of handles or None if not available
        """
        try:
            return process.num_handles()
        except (AttributeError, psutil.AccessDenied):
            return None

    def _monitoring_loop(self):
        """Main monitoring loop that runs in separate thread."""
        while self.running:
            try:
                # Update system stats
                self.system_data = self.get_system_stats()

                # Update process stats and thread stats
                with self.lock:
                    current_pids = list(self.monitored_pids)

                for pid in current_pids:
                    stats = self.get_process_stats(pid)
                    thread_stats = self.get_thread_stats(pid)
                    if stats:
                        with self.lock:
                            if pid in self.process_data:
                                self.process_data[pid]['last_stats'] = stats
                                self.process_data[pid]['last_threads'] = thread_stats
                                self.process_data[pid]['last_update'] = datetime.now()

                time.sleep(2)  # Update interval
            except Exception as e:
                print(f"Monitoring error: {e}")
                time.sleep(5)

    def start_monitoring(self):
        """Start the monitoring thread."""
        if not self.running:
            self.running = True
            self.monitor_thread = threading.Thread(name='SystemMonitor', target=self._monitoring_loop, daemon=True)
            self.monitor_thread.start()

    def stop_monitoring(self):
        """Stop the monitoring thread."""
        self.running = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)

    def get_all_stats(self) -> Dict[str, Any]:
        """
        Get all current monitoring data.

        Returns:
            dict: Combined system and process statistics
        """
        with self.lock:
            process_stats = {}
            thread_stats = {}
            for pid, data in self.process_data.items():
                if 'last_stats' in data:
                    process_stats[pid] = data['last_stats']
                if 'last_threads' in data:
                    thread_stats[pid] = data['last_threads']

            return {
                'system': self.system_data,
                'processes': process_stats,
                'threads': thread_stats,
                'monitored_pids': list(self.monitored_pids),
                'timestamp': datetime.now().isoformat()
            }

    def get_monitored_processes(self) -> List[Dict[str, Any]]:
        """
        Get list of all monitored processes with basic info.

        Returns:
            list: Information about monitored processes
        """
        with self.lock:
            return [
                {
                    'pid': pid,
                    'name': data['name'],
                    'added_time': data['added_time'].isoformat(),
                    'last_update': data.get('last_update', {}).isoformat() if 'last_update' in data else None
                }
                for pid, data in self.process_data.items()
            ]
