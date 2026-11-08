#!/usr/bin/env python3
"""手工生成与定时任务相同的实体关系图完整/增量文本快照。"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from EntityAlias.iis_integration import create_entity_alias  # noqa: E402
from EntityResolution.iis_integration import create_entity_resolution  # noqa: E402
from GlobalConfig import (  # noqa: E402
    DATA_PATH,
    DEFAULT_ALTER_CONFIG_FILE,
    DEFAULT_CONFIG_FILE,
    EXPORT_PATH,
)
from IISConfig import load_iis_config  # noqa: E402
from ServiceComponent.DomainResources import DomainResourceRegistry  # noqa: E402
from SituationViews.snapshot import create_snapshot_dumper  # noqa: E402


def main() -> int:
    config = load_iis_config(
        DEFAULT_CONFIG_FILE, fallback_file=DEFAULT_ALTER_CONFIG_FILE)
    resources = DomainResourceRegistry.build_from_config(
        config,
        mongodb_params={
            "host": config.get("mongodb.host", "localhost"),
            "port": config.get("mongodb.port", 27017),
            "username": config.get("mongodb.user", ""),
            "password": config.get("mongodb.password", ""),
        },
        db_name="IntelligenceIntegrationSystem",
    )
    aliases = create_entity_alias(config)
    resolution = create_entity_resolution(
        domain_resources=resources, entity_registry=aliases)
    dumper = create_snapshot_dumper(
        config=config,
        domain_resources=resources,
        entity_resolution_service=resolution,
        entity_alias_service=aliases,
        export_root=EXPORT_PATH,
        data_root=DATA_PATH,
    )
    if dumper is None:
        print("实体图快照已在配置中禁用。")
        return 2
    result = dumper.dump()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
