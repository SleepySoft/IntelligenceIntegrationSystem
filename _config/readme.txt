This directory contains deployment configuration kept separate from program code.

IIS configuration
-----------------

Copy ``iis_example.yaml`` to ``iis.yaml`` and only set deployment differences.
Defaults and strict validation live in ``IISConfig.py``. AI clients and the
collector intentionally use their own files below.

Optional components are configured under ``optional_subsystems``. Each child
key is the subsystem name and contains its own ``enabled`` flag and settings;
for example, ``translation.client_group`` selects a group from
``ai_clients.yaml``.

AI Client configuration
-----------------------

1. Copy ``ai_clients_example.yaml`` or another factory-based example to
   ``ai_clients.yaml``.
2. Each client item reserves only ``factory``, ``name`` and ``group``. Every
   other field is passed directly to the selected factory as a keyword argument.
3. Group concurrency is configured once in the top-level ``group_limits`` map.
4. Built-in factory names call presets implemented in ``AIClientCenter/providers``
   (or ``harness`` for local CLI clients). API keys should be supplied through
   the environment variables referenced by each factory.
5. For a custom Client, set ``factory`` to an importable
   ``module:callable``, or use ``./file.py:callable`` for a file relative to
   the YAML directory. The callable accepts ``name``, ``group`` and the
   remaining YAML fields, and returns a Client or Client mapping. Refer to
   ``AIClientCenter/providers`` functions named ``create_*_preset``.

Examples:

- ``ai_clients_recommended_example.yaml``: recommended Gemini/Groq/NVIDIA pools.
- ``ai_clients_gemini_80_example.yaml``: Gemini free-tier quota preset.

Validate without sending requests:

    python -m AIClientCenter --config _config/ai_clients.yaml --validate-only

Preview the Event V4 benchmark plan:

    python Scripts/benchmark_ai_clients.py
