"""情报（新闻）通用报道词汇配置；不包含推理算法。"""
from datetime import timedelta
from ...schema.specs import DomainPack
from ...schema.specs import IdentitySpec
from ..common import _arguments, _frame, frame_specs, make_specs
from ..metadata import NEWS_PREDICATE_METADATA

PREDICATE_ARGUMENT_SPECS = {
    "exist": _arguments("subject"),
    "possess": _arguments("holder", "asset"),
    "control": _arguments("controller", "controlled"),
    "member_of": _arguments("member", "organization"),
    "located_at": _arguments("subject"),
    "depend_on": _arguments("dependent", "dependency"),
    "connected_to": _arguments("participant"),
    "capable": _arguments("subject", "capability"),
    "valid": _arguments("subject"),
    "increase": _arguments("subject agent"),
    "decrease": _arguments("subject agent"),
    "improve": _arguments("subject agent"),
    "deteriorate": _arguments("subject agent"),
    "create": _arguments("actor", "object"),
    "terminate": _arguments("subject agent"),
    "gain": _arguments("subject agent", "object"),
    "lose": _arguments("subject agent", "object"),
    "damage": _arguments("actor", "affected"),
    "casualty": _arguments("actor", "affected"),
    "restore": _arguments("actor", "affected"),
    "appoint": _arguments("authority", "person position"),
    "remove": _arguments("authority", "person position"),
    "discover": _arguments("discoverer", "object"),
    "violate": _arguments("actor", "rule"),
    "move": _arguments("agent theme"),
    "transfer": _arguments("agent", "theme"),
    "aid": _arguments("provider", "recipient goods"),
    "communicate": _arguments("sender", "information recipient"),
    "inspect": _arguments("inspector", "object"),
    "investigate": _arguments("investigator", "object"),
    "negotiate": _arguments("party", "topic"),
    "agree": _arguments("party", "agreement"),
    "regulate": _arguments("authority", "target rule"),
    "sanction": _arguments("authority", "target object"),
    "restrict": _arguments("authority", "target object"),
    "elect": _arguments("electorate", "person position"),
    "adjudicate": _arguments("authority", "case party"),
    "detain": _arguments("authority", "person"),
    "seize": _arguments("authority", "object"),
    "protest": _arguments("participant", "target"),
    "cooperate": _arguments("party", "topic"),
    "release": _arguments("releaser", "object recipient"),
    "armed_conflict": _arguments("belligerent"),
    "attack": _arguments("actor", "target"),
    "defend": _arguments("actor", "target"),
    "intercept": _arguments("actor", "target"),
    "observe": _arguments("actor", "target"),
    "disrupt": _arguments("actor", "target"),
    "accident": _arguments("", "affected"),
    "natural_hazard": _arguments("phenomenon", "affected_area"),
    "outbreak": _arguments("phenomenon", "affected_area"),
}

IDENTITIES = {
    "attack": IdentitySpec(
        identity_roles=("actor", "target"), discriminator_roles=("instrument",),
        time_mode="occurrence", time_tolerance=timedelta(hours=48),
        location_mode="strict", repeatability="high",
        role_weight=.35, time_weight=.30, location_weight=.25,
        attribute_weight=.10, lifecycle_weight=0,
        auto_merge_threshold=.90, review_threshold=.72,
        max_time_uncertainty=timedelta(days=1),
    ),
    "armed_conflict": IdentitySpec(
        identity_roles=("belligerent",), time_mode="interval",
        time_tolerance=timedelta(days=365), location_mode="strict", repeatability="low",
        role_weight=.45, time_weight=.15, location_weight=.25,
        attribute_weight=0, lifecycle_weight=.15,
    ),
    "move": IdentitySpec(
        identity_roles=("theme",), discriminator_roles=("source", "destination"),
        time_mode="occurrence", time_tolerance=timedelta(days=2),
        location_mode="strict", repeatability="high",
        max_time_uncertainty=timedelta(days=1),
    ),
}
TAGS = {"attack": {"war"}, "armed_conflict": {"war"}}

FRAMES = frame_specs(
    ("exist capable", _frame("state", "intrinsic", "unknown")),
    ("valid", _frame("state", "intrinsic", "non_agentive")),
    ("possess control member_of located_at depend_on connected_to", _frame("state", "relational", "unknown")),
    ("increase decrease improve deteriorate terminate gain lose restore", _frame("change", "intrinsic", "unknown")),
    ("create damage casualty discover appoint remove adjudicate detain seize sanction restrict violate", _frame("change", "targeted")),
    ("inspect investigate protest regulate attack defend intercept observe disrupt", _frame("process", "targeted")),
    ("negotiate cooperate armed_conflict", _frame("process", "relational")),
    ("agree elect", _frame("change", "relational")),
    ("transfer aid release", _frame("change", "transfer")),
    ("communicate", _frame("process", "transfer")),
    ("move", _frame("change", "transfer", "unknown")),
    ("accident", _frame("change", "intrinsic", "non_agentive")),
    ("natural_hazard outbreak", _frame("process", "intrinsic", "non_agentive")),
)
PREDICATE_SPECS = make_specs(PREDICATE_ARGUMENT_SPECS, IDENTITIES, TAGS, FRAMES, NEWS_PREDICATE_METADATA)
PACK = DomainPack("news", "1.2", PREDICATE_SPECS)
