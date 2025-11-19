import os
import json
import time
import threading
from typing import Dict, Any, List, Optional, Union
from collections import Counter

# Constants for Metric Types
METRIC_TYPE_USAGE = "USAGE_LIMIT"           # Logic: Healthy when current < target
METRIC_TYPE_BALANCE = "BALANCE_THRESHOLD"   # Logic: Healthy when current > target


class ClientMetricsMixin:
    """
    A unified Mixin for managing AI Client statistics, quotas, balances, and health.
    Supports simultaneous usage of periodic quotas (e.g., monthly tokens) and
    financial thresholds (e.g., minimum wallet balance).
    """

    def __init__(self,
                 quota_config: Optional[Dict[str, Any]] = None,
                 balance_config: Optional[Dict[str, float]] = None,
                 state_file_path: Optional[str] = None,
                 *args, **kwargs):
        """
        Initialize the metrics subsystem.

        Args:
            quota_config: Dict defining limits (e.g., {'period_days': 30, 'limits': {'total_tokens': 1000}}).
            balance_config: Dict defining balance rules (e.g., {'hard_threshold': 1.0}).
            state_file_path: Path to a JSON file for persisting periodic usage data across restarts.
            *args, **kwargs: Passed to super() to maintain MRO chain.
        """
        super().__init__(*args, **kwargs)

        self.quota_config = quota_config or {}
        self.balance_config = balance_config or {}
        self.state_file_path = state_file_path

        self._metrics_lock = threading.Lock()

        # Lifetime stats (reset on process restart)
        self._lifetime_stats = Counter()

        # Periodic stats (persisted, reset on time intervals)
        self._periodic_stats = Counter()
        self._last_reset_time = 0.0
        self._current_balance = 0.0

        if self.state_file_path:
            self._load_periodic_state()

    def set_usage_constraints(self,
                              max_tokens: Optional[int] = None,
                              period_days: int = 30,
                              min_balance: Optional[float] = None,
                              target_metric: str = 'total_tokens'):
        """
        Convenience method to quickly configure usage limits and balance thresholds.

        Args:
            max_tokens: Max allowed usage for the target metric. None to disable quota.
            period_days: Number of days before the usage counter resets (default: 30).
            min_balance: Minimum funds required. None to disable balance check.
            target_metric: The metric key to track (default: 'total_tokens').
        """
        with self._metrics_lock:
            # Configure Quota
            if max_tokens is not None:
                self.quota_config = {
                    'period_days': period_days,
                    'limits': {target_metric: max_tokens}
                }
                if self._last_reset_time == 0:
                    self._last_reset_time = time.time()
            else:
                self.quota_config = {}

            # Configure Balance
            if min_balance is not None:
                self.balance_config = {'hard_threshold': min_balance}
            else:
                self.balance_config = {}

            # Persist changes immediately if file path is set
            if self.state_file_path:
                self._save_periodic_state_unsafe()

    def record_usage(self, usage_data: Dict[str, Any]):
        """
        Records usage statistics (deltas) into lifetime and periodic counters.

        Key Conventions:
        ----------------
        Although this method accepts any numeric keys, the following standard keys
        are recommended to ensure compatibility with the quota system:

        1. 'total_tokens' (int):
           Sum of input + output tokens.
           *REQUIRED* if you use the default quota configuration.

        2. 'prompt_tokens' (int):
           Input tokens. Useful for analytics.

        3. 'completion_tokens' (int):
           Output tokens. Useful for analytics.

        4. 'request_count' (int):
           Usually set to 1 per call. Useful for limiting API calls per month.

        5. 'cost_usd' (float):
           Estimated cost. Useful for tracking spending logic internally.

        Example:
            # Good Practice
            client.record_usage({
                'prompt_tokens': 50,
                'completion_tokens': 150,
                'total_tokens': 200,  # Explicitly provided for quota check
                'request_count': 1
            })

        Args:
            usage_data: Dict containing numeric values to be accumulated.
                        Non-numeric values are ignored.
        """
        numeric_data = {k: v for k, v in usage_data.items() if isinstance(v, (int, float))}
        increment = Counter(numeric_data)

        with self._metrics_lock:
            # Update lifetime stats
            self._lifetime_stats.update(increment)
            self._lifetime_stats['last_update'] = time.time()

            # Update periodic stats if quota is active
            if self.quota_config:
                self._check_and_reset_period_unsafe()
                self._periodic_stats.update(increment)
                self._save_periodic_state_unsafe()

    def update_balance(self, amount: float):
        """Updates the current wallet/API balance."""
        with self._metrics_lock:
            self._current_balance = amount

    def get_standardized_metrics(self) -> List[Dict[str, Any]]:
        """
        Returns a list of standardized metrics for health calculation.
        Schema: {'type': str, 'key': str, 'current': float, 'target': float}
        """
        metrics = []
        with self._metrics_lock:
            # 1. Quota Metrics
            if self.quota_config:
                self._check_and_reset_period_unsafe()
                limits = self.quota_config.get('limits', {})
                for key, limit in limits.items():
                    metrics.append({
                        "type": METRIC_TYPE_USAGE,
                        "key": key,
                        "current": self._periodic_stats.get(key, 0),
                        "target": limit
                    })

            # 2. Balance Metrics
            if self.balance_config:
                metrics.append({
                    "type": METRIC_TYPE_BALANCE,
                    "key": "balance",
                    "current": self._current_balance,
                    "target": self.balance_config.get('hard_threshold', 0.0)
                })
        return metrics

    def calculate_health(self) -> float:
        """
        Calculates health score (0-100) based on the worst-performing metric.
        """
        metrics = self.get_standardized_metrics()
        if not metrics:
            return 100.0

        lowest_score = 100.0

        for m in metrics:
            score = 100.0
            current = float(m['current'])
            target = float(m['target'])

            if m['type'] == METRIC_TYPE_USAGE:
                # 0 if usage >= limit
                if target > 0:
                    if current >= target:
                        score = 0.0
                    else:
                        score = 100.0 * (target - current) / target
                else:
                    score = 0.0 if current > 0 else 100.0

            elif m['type'] == METRIC_TYPE_BALANCE:
                # 0 if balance <= threshold
                if current <= target:
                    score = 0.0
                else:
                    # Use a dynamic buffer (max of target or 10.0) for smooth scoring
                    safe_buffer = max(target, 10.0)
                    score = min(100.0, 100.0 * (current - target) / safe_buffer)

            if score < lowest_score:
                lowest_score = score

        return round(lowest_score, 2)

    # --- Internal Helpers ---

    def _check_and_reset_period_unsafe(self):
        """Internal: Checks if period elapsed and resets stats."""
        period_days = self.quota_config.get('period_days', 30)
        if period_days <= 0: return

        if time.time() - self._last_reset_time >= period_days * 86400:
            self._periodic_stats.clear()
            self._last_reset_time = time.time()

    def _save_periodic_state_unsafe(self):
        """Internal: Saves state to JSON file."""
        if not self.state_file_path: return
        try:
            data = {
                'last_reset_time': self._last_reset_time,
                'periodic_usage': dict(self._periodic_stats)
            }
            with open(self.state_file_path, 'w') as f:
                json.dump(data, f)
        except Exception as e:
            print(f"Metrics save error: {e}")

    def _load_periodic_state(self):
        """Internal: Loads state from JSON file."""
        if not self.state_file_path or not os.path.exists(self.state_file_path): return
        try:
            with self._metrics_lock:
                with open(self.state_file_path, 'r') as f:
                    data = json.load(f)
                    self._last_reset_time = data.get('last_reset_time', 0)
                    self._periodic_stats = Counter(data.get('periodic_usage', {}))
        except Exception:
            pass  # Ignore load errors, start fresh
