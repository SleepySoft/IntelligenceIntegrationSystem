"""为 Flask 应用安装界面语言选择与 gettext 翻译能力。

这里仅处理网站自身的界面文案。情报标题、摘要和事件抽取等内容字段必须保持原值，
不得通过本扩展隐式翻译。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from flask import Flask, g, request, session
from flask_babel import Babel
from jinja2 import ChoiceLoader, FileSystemLoader


_SESSION_KEY = "ui_locale"
_TRANSLATIONS_DIR = Path(__file__).with_name("translations")
_SHARED_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"


def _canonical_supported_tag(
        value: object, supported_locales: Mapping[str, str]) -> str | None:
    candidate = str(value or "").strip().casefold()
    if not candidate:
        return None
    return next(
        (tag for tag in supported_locales if tag.casefold() == candidate),
        None,
    )


def install_web_i18n(app: Flask, settings: Mapping[str, Any] | None = None) -> Babel:
    """安装一次 Flask-Babel，并向模板提供对外语言标签。

    `supported_locales` 的键是 URL、HTML 与 HTTP 使用的 BCP 47 标签；值是 gettext
    catalog 目录名。两者显式映射，使新增语言不依赖脆弱的连字符/下划线猜测。
    """

    settings = dict(settings or {})
    enabled = bool(settings.get("enabled", True))
    supported_locales = dict(settings.get("supported_locales") or {
        "zh-CN": "zh_Hans",
        "en": "en",
    })
    default_locale = _canonical_supported_tag(
        settings.get("default_locale", "zh-CN"), supported_locales)
    if default_locale is None:
        raise ValueError("i18n.default_locale 必须包含在 supported_locales 中")

    app.config["BABEL_TRANSLATION_DIRECTORIES"] = str(_TRANSLATIONS_DIR)
    app.config["BABEL_DEFAULT_LOCALE"] = supported_locales[default_locale]
    # 子系统 Blueprint 可被独立 Flask app 挂载；同时提供 IIS 公共模板片段。
    app.jinja_loader = ChoiceLoader([
        app.jinja_loader,
        FileSystemLoader(str(_SHARED_TEMPLATES_DIR)),
    ])

    def select_locale() -> str:
        selected_tag: str | None = None
        if enabled:
            query_value = request.args.get("lang")
            selected_tag = _canonical_supported_tag(query_value, supported_locales)
            if query_value and selected_tag:
                session[_SESSION_KEY] = selected_tag
                session.permanent = True

            if selected_tag is None:
                selected_tag = _canonical_supported_tag(
                    session.get(_SESSION_KEY), supported_locales)
            if selected_tag is None:
                selected_tag = request.accept_languages.best_match(
                    list(supported_locales), default=default_locale)
        selected_tag = selected_tag or default_locale
        g.ui_locale = selected_tag
        return supported_locales[selected_tag]

    babel = Babel(app, locale_selector=select_locale)

    @app.context_processor
    def inject_web_i18n_context():
        # 主动触发 selector，确保即使模板没有调用 gettext，也能得到正确的 html lang。
        from flask_babel import get_locale
        get_locale()
        current_tag = getattr(g, "ui_locale", default_locale)
        return {
            "ui_locale": current_tag,
            "supported_ui_locales": tuple(supported_locales),
        }

    @app.after_request
    def set_content_language(response):
        from flask_babel import get_locale
        get_locale()
        response.headers["Content-Language"] = getattr(
            g, "ui_locale", default_locale)
        if enabled:
            response.vary.add("Accept-Language")
            response.vary.add("Cookie")
        return response

    return babel


def ensure_web_i18n(app: Flask) -> Babel | None:
    """让可独立挂载的 UI 子系统在没有 IIS 组合根时仍能安全渲染。

    正常 IIS 启动由组合根带配置安装；独立测试或复用只获得默认语言配置。
    """

    if "babel" in app.extensions:
        return None
    return install_web_i18n(app)
