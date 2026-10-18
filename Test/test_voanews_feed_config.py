import time
from types import SimpleNamespace

from CrawlTasks.news import task_crawl_voanews as voa


def _feed(published):
    return SimpleNamespace(
        fatal=False,
        errors=[],
        entries=[SimpleNamespace(published=published)],
    )


def test_voa_uses_active_language_service_english_feeds():
    assert len(voa.feed_list) == 8
    assert all(name.endswith(" English") for name in voa.feed_list)
    assert all(url.startswith("https://www.voanews.com/api/")
               for url in voa.feed_list.values())
    assert "USA" not in voa.feed_list


def test_voa_current_feed_passes_freshness_check(monkeypatch):
    result = _feed(time.gmtime(1_800_000_000))
    monkeypatch.setattr(voa, "fetch_feed", lambda *args, **kwargs: result)
    monkeypatch.setattr(voa.time, "time", lambda: 1_800_000_000)

    checked = voa.fetch_current_voa_feed("https://example.invalid/feed", None)

    assert checked.fatal is False
    assert checked.errors == []


def test_voa_stale_feed_is_rejected(monkeypatch):
    now = 1_800_000_000
    stale = time.gmtime(now - (voa.MAX_FEED_AGE_DAYS + 1) * 24 * 60 * 60)
    result = _feed(stale)
    monkeypatch.setattr(voa, "fetch_feed", lambda *args, **kwargs: result)
    monkeypatch.setattr(voa.time, "time", lambda: now)

    checked = voa.fetch_current_voa_feed("https://example.invalid/feed", None)

    assert checked.fatal is True
    assert "stale" in checked.errors[0]
