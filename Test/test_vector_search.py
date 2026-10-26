from ServiceComponent.intelligence_projections import IntelligenceProjectionProfile
from VectorIntelligence.search import do_vector_search


def _params(**overrides):
    values = {
        "search_mode": "vector_text",
        "keywords": "能源安全",
        "reference": "",
        "page": 1,
        "per_page": 10,
        "in_summary": True,
        "in_fulltext": False,
        "start_time": "",
        "end_time": "",
        "archive_start_time": "",
        "archive_end_time": "",
        "score_threshold_min": 0.6,
        "score_threshold_max": 1.0,
    }
    values.update(overrides)
    return values


def test_vector_result_backfill_uses_card_profile():
    calls = []

    def get_intelligence(identifiers, *, domain_name=None, profile=None):
        calls.append((identifiers, domain_name, profile))
        return [{"UUID": identifier, "EVENT_TITLE": identifier}
                for identifier in identifiers]

    result, total = do_vector_search(
        _params(),
        vector_search=lambda **_kwargs: [("b", 0.8, {}), ("a", 0.9, {})],
        get_intelligence=get_intelligence,
        domain_name="news",
        default_domain_name="news",
    )

    assert total == 2
    assert [item["UUID"] for item in result] == ["a", "b"]
    assert calls == [(["b", "a"], "news", IntelligenceProjectionProfile.CARD)]

