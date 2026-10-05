# Intelligence Integration System

Intelligence Integration System (IIS) collects public news and OSINT material,
uses AI to analyse and score it, archives valuable records, and exposes search
and operational pages through a Flask web service.

The Chinese [README](README.md) is the complete installation and operational
guide. Key entry points are:

- `IntelligenceHubLauncher.py` — starts the main web service.
- `CrawlerServiceEngine.py` — starts crawl tasks.
- `VectorDB/VectorDBBService.py` — optional semantic-search service.

## Processing architecture

The Hub uses an event runtime rather than a monolithic queue manager:

```text
intake.received -> analysis.requested -> archive.completed
```

`HubRuntime` only delivers opaque payloads. IIS-specific validation, prompt
selection, scoring and storage live in `IISPipelinePorts`; recovery, translation,
vector indexing, aggregation, graphing and scheduled maintenance are optional
extensions assembled by `IntelligenceHubStartup.py`.

This separation lets future subsystems use different data structures or custom
pages without changing the core runtime. See
[the runtime refactor note](doc/hub_runtime_refactor.md) and
[the subsystem design](doc/subsystem_design.md).

## Configuration

Copy `_config/config_example.json` to `_config/config.json`. For AI clients,
copy `_config/ai_client_config_example.py` to `_config/ai_client_config.py`.
AIClientCenter also provides an independent CLI/harness configuration documented
in [AIClientCenter/README.md](AIClientCenter/README.md).
