"""三个配置包共用的声明构造工具，不是独立领域包。"""
from ..ir import SemanticRoleGroup
from ..core.specs import ArgumentRoleSpec, IdentitySpec, PredicateSpec, generic_spec

DEFAULT_ROLE_GROUPS = {
    "issuer": SemanticRoleGroup.AGENT,
    "instrument_asset": SemanticRoleGroup.THEME,
    "facility": SemanticRoleGroup.THEME,
    "rater": SemanticRoleGroup.AGENT,
    "security": SemanticRoleGroup.THEME,
    "actor": SemanticRoleGroup.AGENT,
    "agent": SemanticRoleGroup.AGENT,
    "acquirer": SemanticRoleGroup.AGENT,
    "builder": SemanticRoleGroup.AGENT,
    "controller": SemanticRoleGroup.AGENT,
    "developer": SemanticRoleGroup.AGENT,
    "deployer": SemanticRoleGroup.AGENT,
    "discoverer": SemanticRoleGroup.AGENT,
    "holder": SemanticRoleGroup.AGENT,
    "inspector": SemanticRoleGroup.AGENT,
    "investigator": SemanticRoleGroup.AGENT,
    "investor": SemanticRoleGroup.AGENT,
    "lender": SemanticRoleGroup.AGENT,
    "operator": SemanticRoleGroup.AGENT,
    "payer": SemanticRoleGroup.AGENT,
    "producer": SemanticRoleGroup.AGENT,
    "provider": SemanticRoleGroup.AGENT,
    "releaser": SemanticRoleGroup.AGENT,
    "sender": SemanticRoleGroup.AGENT,
    "supplier": SemanticRoleGroup.AGENT,
    "tester": SemanticRoleGroup.AGENT,
    "authority": SemanticRoleGroup.AUTHORITY,
    "target": SemanticRoleGroup.AFFECTED,
    "affected": SemanticRoleGroup.AFFECTED,
    "controlled": SemanticRoleGroup.AFFECTED,
    "dependent": SemanticRoleGroup.AFFECTED,
    "person": SemanticRoleGroup.AFFECTED,
    "borrower": SemanticRoleGroup.BENEFICIARY,
    "creditor": SemanticRoleGroup.BENEFICIARY,
    "payee": SemanticRoleGroup.BENEFICIARY,
    "recipient": SemanticRoleGroup.BENEFICIARY,
    "participant": SemanticRoleGroup.PARTICIPANT,
    "party": SemanticRoleGroup.PARTICIPANT,
    "belligerent": SemanticRoleGroup.PARTICIPANT,
    "electorate": SemanticRoleGroup.PARTICIPANT,
    "member": SemanticRoleGroup.PARTICIPANT,
    "organization": SemanticRoleGroup.PARTICIPANT,
    "theme": SemanticRoleGroup.THEME,
    "asset": SemanticRoleGroup.THEME,
    "agreement": SemanticRoleGroup.THEME,
    "capability": SemanticRoleGroup.THEME,
    "case": SemanticRoleGroup.THEME,
    "dependency": SemanticRoleGroup.THEME,
    "obligation": SemanticRoleGroup.THEME,
    "phenomenon": SemanticRoleGroup.THEME,
    "position": SemanticRoleGroup.THEME,
    "goods": SemanticRoleGroup.THEME,
    "information": SemanticRoleGroup.THEME,
    "object": SemanticRoleGroup.THEME,
    "product": SemanticRoleGroup.THEME,
    "resource": SemanticRoleGroup.THEME,
    "rule": SemanticRoleGroup.THEME,
    "subject": SemanticRoleGroup.THEME,
    "topic": SemanticRoleGroup.THEME,
    "debtor": SemanticRoleGroup.SOURCE,
    "source": SemanticRoleGroup.SOURCE,
    "seller": SemanticRoleGroup.SOURCE,
    "destination": SemanticRoleGroup.DESTINATION,
    "buyer": SemanticRoleGroup.DESTINATION,
    "instrument": SemanticRoleGroup.INSTRUMENT,
    "beneficiary": SemanticRoleGroup.BENEFICIARY,
    "location": SemanticRoleGroup.LOCATION,
    "affected_area": SemanticRoleGroup.LOCATION,
    "theater": SemanticRoleGroup.LOCATION,
}

def _arguments(subjects: str = "", objects: str = "") -> ArgumentRoleSpec:
    return ArgumentRoleSpec(frozenset(subjects.split()), frozenset(objects.split()))


def make_specs(arguments, identities=None, tags=None):
    identities = identities or {}
    tags = tags or {}
    result = {}
    for predicate, projection in arguments.items():
        identity = identities.get(predicate, generic_spec(predicate))
        roles = (projection.subject_roles | projection.object_roles
                 | set(identity.identity_roles) | set(identity.discriminator_roles)
                 | {"instrument", "location", "source", "destination", "beneficiary", "target"})
        result[predicate] = PredicateSpec(
            predicate, {r: DEFAULT_ROLE_GROUPS.get(r, SemanticRoleGroup.OTHER) for r in roles},
            identity, arguments=projection, tags=frozenset(tags.get(predicate, ())),
        )
    return result
