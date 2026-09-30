"""产业词汇配置；不包含推理算法。"""
from datetime import timedelta
from ..core.registry import DomainPack
from ..core.specs import IdentitySpec
from .common import _arguments, _frame, frame_specs, make_specs

PREDICATE_ARGUMENT_SPECS = {
    "shortage": _arguments("resource", "affected"),
    "supply": _arguments("supplier", "recipient goods"),
    "operate": _arguments("subject operator"),
    "produce": _arguments("producer", "product"),
    "construct": _arguments("builder", "object"),
    "develop": _arguments("developer", "object"),
    "test": _arguments("tester", "object"),
    "deploy": _arguments("deployer", "object"),
    "maintain": _arguments("actor", "object"),
    "build_facility": _arguments("builder", "facility"),
    "expand_capacity": _arguments("operator", "facility"),
    "suspend_production": _arguments("operator", "facility"),
    "resume_production": _arguments("operator", "facility"),
}

IDENTITIES = {
    "build_facility": IdentitySpec(("builder", "facility"), time_mode="episode",
                                  time_tolerance=timedelta(days=730), location_mode="strict"),
    "expand_capacity": IdentitySpec(("operator", "facility"), time_mode="episode"),
    "suspend_production": IdentitySpec(("operator", "facility"), time_mode="occurrence",
                                      time_tolerance=timedelta(days=1)),
    "resume_production": IdentitySpec(("operator", "facility"), time_mode="occurrence",
                                     time_tolerance=timedelta(days=1)),
}
TAGS = {predicate: {"industry"} for predicate in PREDICATE_ARGUMENT_SPECS}

FRAMES = frame_specs(
    ("shortage", _frame("state", "relational", "non_agentive")),
    ("supply", _frame("process", "transfer")),
    ("operate", _frame("process", "intrinsic", "unknown")),
    ("produce", _frame("process", "intrinsic")),
    ("construct develop test maintain build_facility", _frame("process", "targeted")),
    ("deploy expand_capacity suspend_production resume_production", _frame("change", "targeted")),
)
PREDICATE_SPECS = make_specs(PREDICATE_ARGUMENT_SPECS, IDENTITIES, TAGS, FRAMES)
PACK = DomainPack("industry", "1.1", PREDICATE_SPECS)
