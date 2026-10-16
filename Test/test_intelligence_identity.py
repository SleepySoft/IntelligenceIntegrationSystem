from ServiceComponent.intelligence_identity import (
    normalize_or_assign_intelligence_uuid,
    parse_intelligence_uuid,
)


def test_valid_intelligence_uuid_is_preserved_and_canonicalized():
    value = normalize_or_assign_intelligence_uuid(
        "30EB0459-998D-486F-8EAC-470B9DFD98EC")

    assert str(value) == "30eb0459-998d-486f-8eac-470b9dfd98ec"


def test_invalid_intelligence_uuid_gets_a_new_random_identity_each_time():
    first = normalize_or_assign_intelligence_uuid("not-a-uuid")
    second = normalize_or_assign_intelligence_uuid("not-a-uuid")

    assert first != second
    assert parse_intelligence_uuid(first) == first
    assert parse_intelligence_uuid(second) == second
