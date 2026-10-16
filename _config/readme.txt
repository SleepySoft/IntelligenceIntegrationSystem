This directory contains deployment configuration kept separate from program code.

AI Client configuration
-----------------------

1. Copy ``ai_clients_example.yaml`` or another factory-based example to
   ``ai_clients.yaml``.
2. Each client item reserves only ``factory``, ``name`` and ``group``. Every
   other field is passed directly to the selected factory as a keyword argument.
3. Group concurrency is configured once in the top-level ``group_limits`` map.
4. Built-in factories come from ``AIClientCenter.presets``. API keys should be
   supplied through the environment variables referenced by each factory.
5. For a client that cannot be expressed by a registered factory, use a Python
   configuration defining ``build_ai_client_config()`` and point the
   ``AI_CLIENT_CONFIG`` environment variable to that file.

Examples:

- ``ai_clients_recommended_example.yaml``: recommended Gemini/Groq/NVIDIA pools.
- ``ai_clients_gemini_80_example.yaml``: Gemini free-tier quota preset.
- ``AIClientCenter/config/custom_example.py``: custom Python configuration.

Validate without sending requests:

    python -m AIClientCenter --config _config/ai_clients.yaml --validate-only

Preview the Event V4 benchmark plan:

    python Scripts/benchmark_ai_clients.py
