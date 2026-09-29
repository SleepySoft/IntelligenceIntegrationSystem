from __future__ import annotations
from datetime import datetime
from uuid import UUID
from ..domain.models import *

def event_to_document(e: EventRecord) -> dict:
    return {
      "_id": str(e.uuid), "intelligence_uuid": str(e.intelligence_uuid), "local_event_id": e.local_event_id,
      "frame": {"dynamics":e.frame.dynamics.value,"topology":e.frame.topology.value,"agency":e.frame.agency.value},
      "predicate": {"id":e.predicate.id,"surface":e.predicate.surface,"gloss":e.predicate.gloss},
      "role_bindings": [{"role":x.role,"entity_uuid":str(x.entity_uuid),"semantic_group":x.semantic_group.value,"local_entity_id":x.local_entity_id} for x in e.role_bindings],
      "time": {k:{"normalized":v.normalized,"precision":v.precision,"approximate":v.approximate,"surface":v.surface} for k,v in e.time.items()},
      "location_entity_uuids": [str(x) for x in e.location_entity_uuids], "attributes":dict(e.attributes),
      "qualifiers": [{"id":q.id,"type":q.type,"value":q.value,"scope":q.scope,"by":[str(x) for x in q.by],"surface":q.surface} for q in e.qualifiers],
      "relations": [{"predicate":r.predicate,"target_event_uuid":str(r.target_event_uuid),"surface":r.surface} for r in e.relations],
      "observed_at": e.observed_at, "is_primary":e.is_primary, "metadata":dict(e.metadata),
    }

def event_from_document(d: dict) -> EventRecord:
    return EventRecord(
      uuid=UUID(str(d["_id"])), intelligence_uuid=UUID(str(d["intelligence_uuid"])), local_event_id=d["local_event_id"],
      frame=Frame(Dynamics(d["frame"]["dynamics"]),Topology(d["frame"]["topology"]),Agency(d["frame"]["agency"])),
      predicate=Predicate(**d["predicate"]),
      role_bindings=tuple(RoleBinding(x["role"],UUID(x["entity_uuid"]),SemanticRoleGroup(x.get("semantic_group","other")),x.get("local_entity_id")) for x in d.get("role_bindings",[])),
      time={k:TimeExpression(**v) for k,v in d.get("time",{}).items()},
      location_entity_uuids=tuple(UUID(x) for x in d.get("location_entity_uuids",[])), attributes=d.get("attributes",{}),
      qualifiers=tuple(Qualifier(x["id"],x["type"],x["value"],x.get("scope","event"),tuple(UUID(y) for y in x.get("by",[])),surface=x.get("surface")) for x in d.get("qualifiers",[])),
      relations=tuple(EventRelation(x["predicate"],UUID(x["target_event_uuid"]),x.get("surface")) for x in d.get("relations",[])),
      observed_at=d.get("observed_at"), is_primary=d.get("is_primary",False), metadata=d.get("metadata",{}))
