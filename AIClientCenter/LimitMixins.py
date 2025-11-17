import datetime
from typing import Dict

from AIClientCenter.AIClientManager import ClientStatus


class DailyLimitMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._daily_limit_tokens = 500000
        self._last_reset_day = datetime.date.today()
        # 假设 BaseAIClient 已经有 self._usage_stats

    def _reset_daily_usage(self):
        today = datetime.date.today()
        if today > self._last_reset_day:
            # 重置当天的累计使用量
            self._usage_stats['total_prompt_tokens'] = 0
            self._usage_stats['total_completion_tokens'] = 0
            self._last_reset_day = today
            self._update_status(ClientStatus.AVAILABLE)  # 额度重置，变回可用

    def get_usage_metrics(self) -> Dict[str, float]:
        """计算剩余额度百分比"""
        self._reset_daily_usage()

        used = self._usage_stats.get('total_prompt_tokens', 0) + \
               self._usage_stats.get('total_completion_tokens', 0)

        remaining = self._daily_limit_tokens - used
        remaining_percentage = max(0.0, (remaining / self._daily_limit_tokens) * 100)

        if remaining <= 0:
            # 额度用尽，设置为 UNABVAILABLE
            self._update_status(ClientStatus.UNAVAILABLE)

        return {
            "daily_limit_tokens": self._daily_limit_tokens,
            "tokens_used_today": used,
            "remaining_percentage": remaining_percentage
        }