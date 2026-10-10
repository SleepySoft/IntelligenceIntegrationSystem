This folder is used for placing config files.
The purpose is to facilitate the mapping of non-external directories during deployment.

In the future, scattered configuration files will be gradually centralized in this directory.

Recommended AI pool:

1. Copy ``ai_client_config_recommended_example.py`` to ``ai_client_config.py``.
2. Set provider API keys with the environment variables documented in that file.
3. Preview the independent Event V4 benchmark with
   ``python Scripts/benchmark_ai_clients.py`` and add ``--execute`` only after checking the plan.
