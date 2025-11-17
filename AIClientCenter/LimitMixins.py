import os
import json
import time
import datetime
import threading
from typing import Dict, Any, Union, List, Optional


# --- DateResetMixin ---

class DateResetMixin:
    """
    Mixin for managing usage that resets based on a time period (e.g., monthly).
    Provides a mechanism to check and potentially reset accumulated usage.
    """

    def __init__(self, reset_period_days: int = 30, state_file_path: str = "client_reset_state.json", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.reset_period_days = reset_period_days
        self.state_file_path = state_file_path

        # Load or initialize the reset state
        self._load_reset_state()

    def _load_reset_state(self):
        """Loads last reset time and current usage from the state file."""
        if hasattr(self, '_lock'):
            with self._lock:
                try:
                    if os.path.exists(self.state_file_path):
                        with open(self.state_file_path, 'r') as f:
                            state = json.load(f)
                            self._last_reset_time = state.get('last_reset_time', 0)
                            self._current_period_usage = state.get('current_period_usage', {})
                    else:
                        self._last_reset_time = 0
                        self._current_period_usage = {}
                except Exception as e:
                    # Log error, but proceed with default state
                    print(f"Error loading reset state: {e}")
                    self._last_reset_time = 0
                    self._current_period_usage = {}

    def _save_reset_state(self):
        """Saves the current reset state to the file."""
        if hasattr(self, '_lock'):
            with self._lock:
                state = {
                    'last_reset_time': self._last_reset_time,
                    'current_period_usage': self._current_period_usage
                }
                try:
                    with open(self.state_file_path, 'w') as f:
                        json.dump(state, f)
                except Exception as e:
                    print(f"Error saving reset state: {e}")

    def check_and_reset_period(self):
        """
        Checks if the reset period has passed and resets usage if necessary.
        Must be called before getting usage metrics for accurate calculation.
        """
        current_time = time.time()
        reset_interval = self.reset_period_days * 86400  # seconds in a day

        if current_time - self._last_reset_time >= reset_interval:
            print(f"Quota period elapsed ({self.reset_period_days} days). Resetting usage.")
            with self._lock:
                # Reset tracked usage for this period
                self._current_period_usage = {}
                self._last_reset_time = current_time
                self._save_reset_state()
            return True
        return False

    def record_period_usage(self, key: str, value: float):
        """Records usage within the current period."""
        with self._lock:
            self._current_period_usage[key] = self._current_period_usage.get(key, 0) + value
            self._save_reset_state()

    def get_period_usage(self, key: str) -> float:
        """Returns the accumulated usage for the current period."""
        with self._lock:
            return self._current_period_usage.get(key, 0)

    def get_reset_timestamp(self) -> int:
        """Returns the timestamp of the next expected reset."""
        return self._last_reset_time + (self.reset_period_days * 86400)


# --- QuotaMixin ---

class QuotaMixin(DateResetMixin):
    """
    Mixin for managing multi-dimensional usage quotas (e.g., tokens, requests).
    Inherits DateResetMixin to handle periodic resets.
    """

    # Define a default structure for quota limits
    DEFAULT_LIMITS = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_requests": 0,
        # ... other potential limits ...
    }

    def __init__(self, quota_limits: Dict[str, float] = None, *args, **kwargs):
        # The MRO (Method Resolution Order) ensures super().__init__ calls the BaseAIClient's init
        super().__init__(*args, **kwargs)

        # Merge provided limits with defaults
        self.quota_limits = self.DEFAULT_LIMITS.copy()
        if quota_limits:
            self.quota_limits.update(quota_limits)

    def record_usage(self, usages: dict):
        """
        Overrides BaseAIClient's record_usage (if called after BaseAIClient in MRO)
        or is called explicitly. Records usage both in total stats and current period.
        """
        super().record_usage(usages)  # Call BaseAIClient's method to update total usage stats

        # Record usage for the current period tracking
        if 'prompt_tokens' in usages:
            self.record_period_usage('prompt_tokens', usages['prompt_tokens'])
        if 'completion_tokens' in usages:
            self.record_period_usage('completion_tokens', usages['completion_tokens'])
        # We can also track request count here if needed
        # self.record_period_usage('total_requests', 1)

    def get_quota_metrics(self) -> List[Dict[str, Any]]:
        """
        Returns a list of metrics for all defined quotas.
        """
        self.check_and_reset_period()  # Important: Check for reset before reporting metrics

        metrics = []
        for key, limit in self.quota_limits.items():
            if limit > 0:
                current_usage = self.get_period_usage(key)

                metrics.append({
                    "metrics_type": key.upper(),  # E.g., "PROMPT_TOKENS"
                    "usage": current_usage,
                    "limit": limit,
                    "current_value": limit - current_usage,
                    "reset_timestamp": self.get_reset_timestamp(),
                })
        return metrics


# --- BalanceMixin ---

class BalanceMixin:
    """
    Mixin for managing monetary balance. The health is determined by
    comparing the current balance against a hard consumption threshold.
    """

    def __init__(self, hard_threshold: float = 1.0, *args, **kwargs):
        # MRO ensures super().__init__ calls the next class in the chain (BaseAIClient)
        super().__init__(*args, **kwargs)
        self.hard_threshold = hard_threshold
        self._current_balance = 0.0  # Assumed to be updated from an external API call

    def update_balance(self, amount: float):
        """Updates the current real-time balance."""
        if hasattr(self, '_lock'):
            with self._lock:
                self._current_balance = amount
        else:
            self._current_balance = amount

    def is_balance_low(self) -> bool:
        """Quick check if balance is below the hard limit."""
        return self._current_balance <= self.hard_threshold

    def get_balance_metrics(self) -> List[Dict[str, Any]]:
        """
        Returns the balance metric in the standardized format.
        """
        # Note: The 'usage' (total_cost_usd) must be accumulated by the client
        # using the BaseAIClient's record_usage mechanism.
        total_cost = self.get_usage_stats().get("total_cost_usd", 0)

        return [{
            "metrics_type": "BALANCE",
            "usage": total_cost,
            "limit": None,  # No defined "limit" for balance, use current_value/threshold
            "current_value": self._current_balance,
            "hard_threshold": self.hard_threshold,
        }]
