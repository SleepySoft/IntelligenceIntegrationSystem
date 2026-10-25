from flask import Flask, render_template_string

from WebI18n import install_web_i18n


def _make_app():
    app = Flask(__name__)
    app.secret_key = "test-secret"
    install_web_i18n(app, {
        "enabled": True,
        "default_locale": "zh-CN",
        "supported_locales": {"zh-CN": "zh_Hans", "en": "en"},
    })

    @app.get("/probe")
    def probe():
        return render_template_string("{{ ui_locale }}|{{ _('Intelligence list') }}")

    return app


def test_default_and_browser_locale_selection():
    client = _make_app().test_client()

    default_response = client.get("/probe")
    english_response = client.get("/probe", headers={"Accept-Language": "en-US,en;q=0.9"})

    assert default_response.text == "zh-CN|情报列表"
    assert default_response.headers["Content-Language"] == "zh-CN"
    assert english_response.text == "en|Intelligence list"
    assert "Accept-Language" in english_response.vary


def test_valid_query_locale_persists_in_session():
    client = _make_app().test_client()

    selected = client.get("/probe?lang=en")
    persisted = client.get("/probe")

    assert selected.text == "en|Intelligence list"
    assert persisted.text == "en|Intelligence list"
    assert "Cookie" in persisted.vary


def test_invalid_query_locale_does_not_override_browser_choice():
    client = _make_app().test_client()

    response = client.get(
        "/probe?lang=not-supported",
        headers={"Accept-Language": "en"},
    )

    assert response.text == "en|Intelligence list"
