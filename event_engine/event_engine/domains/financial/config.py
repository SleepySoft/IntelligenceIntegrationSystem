"""金融词汇配置；不包含推理算法。"""
from datetime import timedelta
from ...schema.specs import DomainPack
from ...schema.specs import IdentitySpec
from ..common import _arguments, _frame, frame_specs, make_specs

PREDICATE_ARGUMENT_SPECS = {
    "default": _arguments("debtor", "obligation"),
    "insolvency": _arguments("subject"),
    "trade": _arguments("buyer seller", "goods"),
    "acquire": _arguments("acquirer", "asset"),
    "invest": _arguments("investor", "recipient resource"),
    "fund": _arguments("provider", "recipient resource"),
    "pay": _arguments("payer", "payee"),
    "lend": _arguments("lender", "borrower"),
    "repay": _arguments("debtor", "creditor"),
    "issue_bond": _arguments("issuer", "security"),
    "rating_change": _arguments("rater", "security"),
    "declare_dividend": _arguments("issuer", "security"),
}

IDENTITIES = {
    "acquire": IdentitySpec(
        identity_roles=("acquirer", "asset"), discriminator_roles=("seller",),
        time_mode="episode", time_tolerance=timedelta(days=730),
        location_mode="ignore", repeatability="low",
        role_weight=.65, time_weight=.10, location_weight=0,
        attribute_weight=.15, lifecycle_weight=.10,
        auto_merge_require_time=True,
    ),
    "issue_bond": IdentitySpec(("issuer", "security"), time_mode="episode",
                               location_mode="ignore", identity_attributes=("currency", "maturity"),
                               auto_merge_require_time=True),
    "rating_change": IdentitySpec(("rater", "security"), time_mode="occurrence",
                                  time_tolerance=timedelta(days=1), location_mode="ignore"),
    "declare_dividend": IdentitySpec(("issuer", "security"), time_mode="occurrence",
                                    time_tolerance=timedelta(days=1), location_mode="ignore"),
}
TAGS = {predicate: {"financial"} for predicate in PREDICATE_ARGUMENT_SPECS}

FRAMES = frame_specs(
    ("trade acquire pay lend repay issue_bond", _frame("change", "transfer")),
    ("invest fund", _frame("process", "transfer")),
    ("default", _frame("change", "intrinsic", "unknown")),
    ("insolvency", _frame("change", "intrinsic", "non_agentive")),
    ("rating_change declare_dividend", _frame("change", "targeted")),
)
PREDICATE_SPECS = make_specs(PREDICATE_ARGUMENT_SPECS, IDENTITIES, TAGS, FRAMES)
PACK = DomainPack("financial", "1.1", PREDICATE_SPECS)
