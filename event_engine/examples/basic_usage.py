from datetime import datetime, timezone
from uuid import uuid4
from event_engine.analysis.event_analyzer import EventAnalyzer
from event_engine.domain.models import *
from event_engine.domain.specs import DEFAULT_PREDICATE_SPECS
from event_engine.integration.service import EventEngine
from event_engine.query.memory import InMemoryCanonicalEventRepository, InMemoryEventRepository

actor,target,location = uuid4(),uuid4(),uuid4()
repo=InMemoryEventRepository(); canon=InMemoryCanonicalEventRepository()
analyzer=EventAnalyzer(DEFAULT_PREDICATE_SPECS)
engine=EventEngine(repo,canon,analyzer=analyzer)

def attack(day):
    return EventRecord(uuid4(),uuid4(),"E1",Frame(Dynamics.PROCESS,Topology.TARGETED,Agency.AGENTIVE),
      Predicate("attack","袭击"),(RoleBinding("actor",actor,SemanticRoleGroup.AGENT),RoleBinding("target",target,SemanticRoleGroup.AFFECTED)),
      time={"event_time":TimeExpression(day,"day",False,day)},location_entity_uuids=(location,),
      observed_at=datetime.fromisoformat(day).replace(tzinfo=timezone.utc))

e=attack("2026-09-28")
engine.register_event(e)
print("行动事件:",len(engine.get_entity_actions(actor)))
print("受影响事件:",len(engine.get_actions_affecting_entity(target)))
print("战争区域:",engine.extract_war_zones())
canonical,result=engine.resolve_canonical(e.uuid)
print("CanonicalEvent:",canonical.uuid,result.decision.value)
