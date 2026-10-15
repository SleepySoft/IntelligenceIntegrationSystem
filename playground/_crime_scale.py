"""临时脚本：量化 attack 谓词被用于平民犯罪的规模。"""
import pymongo

db = pymongo.MongoClient('mongodb://localhost:27017')['IntelligenceIntegrationSystem']
events = db['intelligence_v4_events']

attack_events = list(events.find({'predicate.id': 'attack'}, {
    'roles': 1, 'predicate.surface': 1, 'intelligence_uuid': 1}))
print(f'attack 谓词事件总数: {len(attack_events)}')

person_vs_person = []
for ev in attack_events:
    roles = ev.get('roles') or {}
    actors = roles.get('actor') or []
    targets = roles.get('target') or []
    if actors and targets:
        actor_types = {a.get('type') for a in actors}
        target_types = {t.get('type') for t in targets}
        if actor_types <= {'person'} and target_types <= {'person'}:
            person_vs_person.append(ev)

print(f'其中 actor/target 均为 person（疑似平民犯罪）: {len(person_vs_person)}')
for ev in person_vs_person[:10]:
    roles = ev['roles']
    print(f"  surface={ev['predicate'].get('surface')!r} "
          f"actor={[a.get('name') for a in roles['actor']]} "
          f"target={[t.get('name') for t in roles['target']]}")
