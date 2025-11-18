# AI clients that matches BaseAIClient
from typing import Dict, List, Optional, Any, Union

import requests
from typing_extensions import override

from AIClientCenter.AIClientManager import BaseAIClient, CLIENT_PRIORITY_NORMAL
from AIClientCenter.LimitMixins import BalanceMixin
from AIClientCenter.OpenAICompatibleAPI import OpenAICompatibleAPI


class OpenAIClient(BaseAIClient):
    def __init__(self, openai_api: OpenAICompatibleAPI, priority: int = CLIENT_PRIORITY_NORMAL):
        super().__init__(openai_api.get_api_token(), priority)

        self.api = openai_api

    # ------------------------------------------------- Overrides -------------------------------------------------

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














