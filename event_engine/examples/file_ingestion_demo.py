"""从一个 JSON 文件接入事件，并输出可直接观察的查询结果。"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from event_engine.analysis.event_analyzer import EventAnalyzer
from event_engine.domain.specs import DEFAULT_PREDICATE_SPECS
from event_engine.integration.file_ingestion import ingest_event_file
from event_engine.integration.service import EventEngine
from event_engine.query.memory import InMemoryEventRepository


DEFAULT_FILE = Path(__file__).parent / "data" / "multitopic_events.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Event File v1 单文件接入演示")
    parser.add_argument("file", nargs="?", type=Path, default=DEFAULT_FILE)
    args = parser.parse_args()

    repository = InMemoryEventRepository()
    engine = EventEngine(
        repository,
        analyzer=EventAnalyzer(DEFAULT_PREDICATE_SPECS),
    )
    dataset = ingest_event_file(args.file, engine)

    predicate_counts = Counter(event.predicate.id or "unclassified" for event in dataset.events)
    frame_counts = Counter(event.frame.dynamics.value for event in dataset.events)
    relation_count = sum(len(event.relations) for event in dataset.events)
    print(f"数据集: {dataset.dataset_id}")
    print(
        f"接入结果: {len(dataset.events)} 个事件 / {len(dataset.entities)} 个实体 / "
        f"{len(predicate_counts)} 类谓词 / {relation_count} 条事件关系"
    )
    print("事件形态:", "、".join(f"{key}={value}" for key, value in sorted(frame_counts.items())))
    print("\n事件列表")
    print("序号  日期        主题                     谓词              参与实体")
    print("-" * 94)
    for index, event in enumerate(engine.timeline(frozenset(repository.data)), 1):
        topic = str(event.metadata.get("topic", ""))
        event_time = event.time.get("event_time")
        day = event_time.normalized if event_time else "-"
        names = [dataset.entity_name(entity_uuid) for entity_uuid in engine.analyzer.participants(event)]
        print(
            f"{index:>2}    {day:<10}  {topic:<23}  "
            f"{(event.predicate.id or 'null'):<16}  {'、'.join(names)}"
        )

    print("\n谓词分布（出现两次及以上）")
    repeated = [(key, value) for key, value in predicate_counts.most_common() if value > 1]
    print("、".join(f"{key}={value}" for key, value in repeated) or "无")

    print("\n战争区域视图")
    war_zones = engine.extract_war_zones(active_days=10000)
    for zone in war_zones:
        print(
            f"{dataset.entity_name(zone.location_entity_uuid)}: "
            f"事件数={zone.event_count}, 最近活动={zone.last_activity}, 活跃={zone.active}"
        )
    if not war_zones:
        print("无")

    print("\n显式事件关系")
    event_topics = {event.uuid: event.metadata.get("topic", str(event.uuid)) for event in dataset.events}
    for event in dataset.events:
        for relation in event.relations:
            print(
                f"{event.metadata.get('topic')} --{relation.predicate}--> "
                f"{event_topics[relation.target_event_uuid]}"
            )


if __name__ == "__main__":
    main()
