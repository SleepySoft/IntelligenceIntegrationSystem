import unittest
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4
from event_engine.core import EventAnalyzer
from event_engine.schema import *
from event_engine.domains import default_registry
from event_engine.core import EventEngine
from event_engine.query.memory import InMemoryCanonicalEventRepository, InMemoryEventRepository

class EngineTests(unittest.TestCase):
    def setUp(self):
        self.a,self.b,self.loc=uuid4(),uuid4(),uuid4()
        self.repo=InMemoryEventRepository(); self.crepo=InMemoryCanonicalEventRepository()
        self.engine=EventEngine(self.repo,self.crepo,EventAnalyzer(default_registry()))
    def event(self, day, q=None):
        return EventRecord(uuid4(),uuid4(),"E1",Frame(Dynamics.CHANGE,Topology.TRANSFER,Agency.AGENTIVE),
          Predicate("acquire","收购"),(RoleBinding("acquirer",self.a,SemanticRoleGroup.AGENT),RoleBinding("asset",self.b,SemanticRoleGroup.THEME)),
          time={"event_time":TimeExpression(day,"day",False,day)}, qualifiers=(q,) if q else (),
          observed_at=datetime.now(timezone.utc))
    def test_entity_queries(self):
        e=replace(self.event("2026-01-01"), predicate=Predicate("attack", "袭击"),
                  frame=Frame(Dynamics.PROCESS, Topology.TARGETED, Agency.AGENTIVE),
                  role_bindings=(RoleBinding("actor", self.a, SemanticRoleGroup.AGENT),
                                 RoleBinding("target", self.b, SemanticRoleGroup.AFFECTED)))
        self.engine.register_event(e)
        self.assertEqual((e,),self.engine.get_entity_actions(self.a))
        self.assertEqual((e,),self.engine.get_actions_affecting_entity(self.b))
    def test_canonical_lifecycle(self):
        p=self.event("2026-01-01",Qualifier("Q1","intention","planned")); self.engine.register_event(p)
        c,r=self.engine.resolve_canonical(p.uuid); self.assertEqual(MatchDecision.NEW_EVENT,r.decision)
        done=self.event("2026-09-01",Qualifier("Q1","phase","completed")); self.engine.register_event(done)
        c2,r2=self.engine.resolve_canonical(done.uuid)
        self.assertEqual(c.uuid,c2.uuid); self.assertEqual(MatchDecision.STATE_UPDATE,r2.decision)
        self.assertEqual("completed",c2.current_qualifiers["phase"])
    def test_separate_attack_dates(self):
        def attack(day):
            return EventRecord(uuid4(),uuid4(),"E1",Frame(Dynamics.PROCESS,Topology.TARGETED,Agency.AGENTIVE),Predicate("attack","袭击"),
              (RoleBinding("actor",self.a,SemanticRoleGroup.AGENT),RoleBinding("target",self.b,SemanticRoleGroup.AFFECTED)),
              time={"event_time":TimeExpression(day,"day",False,day)},location_entity_uuids=(self.loc,),observed_at=datetime.now(timezone.utc))
        e1=attack("2026-09-01"); e2=attack("2026-09-08")
        self.repo.add(e1); c,_=self.engine.resolve_canonical(e1.uuid); self.repo.add(e2)
        second, result = self.engine.resolve_canonical(e2.uuid)
        self.assertEqual(MatchDecision.NEW_EVENT, result.decision)
        self.assertNotEqual(c.uuid, second.uuid)

if __name__=='__main__': unittest.main()
