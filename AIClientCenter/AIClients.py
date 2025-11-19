# AI clients that matches BaseAIClient
from typing import Dict, List, Optional, Any, Union

import requests
from typing_extensions import override

from AIClientCenter.LimitMixins import BalanceMixin
from AIClientCenter.AIServiceTokenRotator import RotatableClient
from AIClientCenter.OpenAICompatibleAPI import OpenAICompatibleAPI
from AIClientCenter.AIClientManager import BaseAIClient, CLIENT_PRIORITY_NORMAL, ClientStatus


class OpenAIClient(BaseAIClient, RotatableClient):
    def __init__(
            self,
            name: str,
            openai_api: OpenAICompatibleAPI,
            priority: int = CLIENT_PRIORITY_NORMAL,
            default_available: bool = False):
        super().__init__(name, openai_api.get_api_token(), priority)

        self.api = openai_api
        if default_available:
            self._status['status'] = ClientStatus.AVAILABLE

    # ------------------------------------------------- Overrides -------------------------------------------------

    # ------------------ BaseAIClient ------------------

    @override
    def get_usage_metrics(self) -> Dict[str, float]:
        """
        Get usage metrics and return the most critical remaining percentage.

        Returns:
            Dict with usage metrics including 'remaining_percentage' (0-100)
        """
        pass

    @override
    def get_model_list(self) -> Dict[str, Any]:
        return self.api.get_model_list()

    @override
    def _chat_completion_sync(self,
                              messages: List[Dict[str, str]],
                              model: Optional[str] = None,
                              temperature: float = 0.7,
                              max_tokens: int = 4096) -> Union[Dict[str, Any], requests.Response]:
        return self.api.create_chat_completion_sync(messages, model, temperature, max_tokens)

    # ------------------ RotatableClient ------------------

    @override
    def set_api_token(self, token: str):
        self.api.set_api_token(token)
        with self._lock:
            # Ask for re-check ASAP.
            self._status['status_last_updated'] = 0
