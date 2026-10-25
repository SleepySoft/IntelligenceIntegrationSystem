from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def test_public_reading_pages_declare_chinese_source_language():
    pages = (
        "templates/intelligence_list.html",
        "templates/intelligence_search.html",
        "templates/intelligence_detail.html",
        "templates/intelligence_cluster_list.html",
        "IntelligenceViews/templates/intelligence_views.html",
        "SituationViews/templates/situation_views.html",
        "SituationViews/templates/entity_timeline.html",
    )

    dynamic_pages = {
        "templates/intelligence_list.html",
        "templates/intelligence_detail.html",
        "templates/intelligence_cluster_list.html",
        "IntelligenceViews/templates/intelligence_views.html",
            "SituationViews/templates/situation_views.html",
            "SituationViews/templates/entity_timeline.html",
    }
    for page in pages:
        source = _read(page)
        if page in dynamic_pages:
            assert 'lang="{{ ui_locale|default(\'zh-CN\') }}"' in source, page
        else:
            assert '<html lang="zh-CN">' in source, page


def test_dynamic_cards_protect_machine_identifiers_from_page_translation():
    card_renderer = _read("static/js/intelligence_table_renderer.js")
    detail_renderer = _read("static/js/article_detail_renderer.js")
    modal_manager = _read("static/js/article_modal_manager.js")

    assert 'class="notranslate" translate="no">${uuid}' in card_renderer
    assert 'class="notranslate" translate="no">${uuid}' in detail_renderer
    assert "state.uuidEl.setAttribute('translate', 'no')" in modal_manager
    assert 'debug-value-truncate notranslate' in card_renderer


def test_primary_reading_ui_no_longer_uses_english_fallback_copy():
    sources = (
        "static/js/intelligence_table_renderer.js",
        "static/js/article_detail_renderer.js",
        "static/js/article_modal_manager.js",
        "static/js/intelligence_cluster_list.js",
    )
    obsolete = (
        "No Title", "No Brief", "Unknown Source", "Loading Intelligences",
        "Failed to load", "Find Similar", "No Aggregated Clusters Available",
    )

    combined = "\n".join(_read(path) for path in sources)
    for text in obsolete:
        assert text not in combined
