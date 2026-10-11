This folder is used for placing config files.
The purpose is to facilitate the mapping of non-external directories during deployment.

In the future, scattered configuration files will be gradually centralized in this directory.

Recommended AI pool:

1. Copy ``ai_client_config_recommended_example.py`` to ``ai_client_config.py``.
2. Set provider API keys with the environment variables documented in that file.
3. Preview the independent Event V4 benchmark with
   ``python Scripts/benchmark_ai_clients.py`` and add ``--execute`` only after checking the plan.

Gemini Flash 80% quota pool:

1. Copy ``ai_client_config_gemini_80_example.py`` to ``ai_client_config.py``.
2. Set ``GEMINI_API_KEY`` (and optional ``GEMINI_PROXY``).
3. The defaults create two slots per Flash Lite model and one per quality Flash model.
   Use ``GEMINI_FLASH_LITE_SLOTS`` / ``GEMINI_FLASH_QUALITY_SLOTS`` to match Hub workers.
4. ``GEMINI_QUOTA_SAFETY_RATIO`` defaults to 0.8. ``GEMINI_MODELS`` can restrict the
   model list after the standalone benchmark confirms the model IDs and Event V4 quality.
