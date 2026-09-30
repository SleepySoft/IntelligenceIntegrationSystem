from __future__ import annotations
from uuid import UUID
from ..core.queries import EventPage, EventQuery
from .serialization import event_from_document, event_to_document

class MongoQueryTranslator:
    def to_filter(self, q: EventQuery) -> dict:
        f={}; ands=[]
        if q.event_uuids: f["_id"]={"$in":[str(x) for x in q.event_uuids]}
        if q.intelligence_uuid: f["intelligence_uuid"]=str(q.intelligence_uuid)
        if q.predicate_ids: f["predicate.id"]={"$in":list(q.predicate_ids)}
        if q.location_entity_uuids: f["location_entity_uuids"]={"$in":[str(x) for x in q.location_entity_uuids]}
        if q.entity_uuid:
            elem={"entity_uuid":str(q.entity_uuid)}
            if q.roles: elem["role"]={"$in":list(q.roles)}
            if q.semantic_groups: elem["semantic_group"]={"$in":[x.value for x in q.semantic_groups]}
            f["role_bindings"]={"$elemMatch":elem}
        if q.qualifier_types: ands.append({"qualifiers.type":{"$in":list(q.qualifier_types)}})
        if q.qualifier_values: ands.append({"qualifiers.value":{"$in":list(q.qualifier_values)}})
        if q.observed_from or q.observed_to:
            r={}
            if q.observed_from: r["$gte"]=q.observed_from
            if q.observed_to: r["$lte"]=q.observed_to
            f["observed_at"]=r
        if ands: f["$and"]=ands
        return f

class MongoEventRepository:
    def __init__(self, collection):
        self.collection=collection; self.translator=MongoQueryTranslator()
    def ensure_indexes(self):
        self.collection.create_index("predicate.id")
        self.collection.create_index([("role_bindings.entity_uuid",1),("role_bindings.semantic_group",1)])
        self.collection.create_index("location_entity_uuids")
        self.collection.create_index("intelligence_uuid")
        self.collection.create_index("observed_at")
    def add(self,event): self.collection.insert_one(event_to_document(event))
    def get(self,event_uuid):
        d=self.collection.find_one({"_id":str(event_uuid)})
        return event_from_document(d) if d else None
    def search(self,q):
        f=self.translator.to_filter(q); total=self.collection.count_documents(f)
        cur=self.collection.find(f).sort([("observed_at",1),("_id",1)]).skip(q.offset)
        if q.limit is not None: cur=cur.limit(q.limit)
        return EventPage(tuple(event_from_document(x) for x in cur),total,q.offset,q.limit)
