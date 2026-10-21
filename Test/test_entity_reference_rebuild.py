from Scripts.rebuild_entity_reference_index import event_index_has_entity_refs


def test_event_index_ref_coverage_requires_every_snapshot_to_have_ent_id():
    complete = [{
        "roles": {
            "actor": [{"local_entity_id": "ENT1", "name": "甲"}],
            "target": [{"local_entity_id": "ENT2", "name": "乙"}],
        },
        "event_location": [{"local_entity_id": "ENT3", "name": "巴黎"}],
        "qualifiers": [{"by": [{"local_entity_id": "ENT1", "name": "甲"}]}],
    }]
    incomplete = [{
        "roles": {"actor": [{"name": "甲"}]},
        "event_location": [], "qualifiers": [],
    }]

    assert event_index_has_entity_refs(complete) is True
    assert event_index_has_entity_refs(incomplete) is False
    assert event_index_has_entity_refs([]) is False
