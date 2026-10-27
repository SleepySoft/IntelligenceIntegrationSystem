import threading
from types import SimpleNamespace

import pytest

from CrawlerServiceEngine import (
    TaskManager,
    is_task_plugin_path,
    resolve_domain_task_dirs,
)


def test_task_discovery_only_selects_production_plugins(tmp_path):
    (tmp_path / "task_crawl_news.py").write_text("", encoding="utf-8")
    (tmp_path / "task_example.py").write_text("", encoding="utf-8")
    (tmp_path / "crawler_config_news.py").write_text("", encoding="utf-8")

    selected = TaskManager._list_task_files(str(tmp_path))

    assert selected == [str(tmp_path / "task_crawl_news.py")]
    assert is_task_plugin_path("task_crawl_news.py")
    assert not is_task_plugin_path("task_example.py")
    assert not is_task_plugin_path("crawler_config_news.py")


def test_task_domain_is_resolved_from_configured_domain_directory(tmp_path):
    news_dir = tmp_path / "news"
    finance_dir = tmp_path / "finance"
    news_dir.mkdir()
    finance_dir.mkdir()
    manager = TaskManager.__new__(TaskManager)
    manager.default_domain = "news"
    manager.domain_task_dirs = {
        "news": str(news_dir),
        "finance": str(finance_dir),
    }

    assert manager._resolve_domain(str(finance_dir / "task_crawl_market.py")) == "finance"
    assert manager._resolve_domain(str(news_dir / "task_crawl_world.py")) == "news"


def test_domain_task_directories_must_not_overlap(tmp_path):
    with pytest.raises(ValueError, match="must not overlap"):
        resolve_domain_task_dirs({
            "collector.domain_task_dirs": {
                "news": str(tmp_path),
                "finance": str(tmp_path / "finance"),
            },
        })


def test_shutdown_notifies_all_plugins_before_waiting_for_any_thread():
    manager = TaskManager.__new__(TaskManager)
    events = {name: threading.Event() for name in ("first", "second", "third")}
    manager.tasks = {
        name: (SimpleNamespace(plugin_name=name), object(), event)
        for name, event in events.items()
    }
    removed = []
    manager.plugin_manager = SimpleNamespace(remove_plugin=removed.append)

    def stop_and_join(name):
        assert all(event.is_set() for event in events.values())
        manager.tasks.pop(name, None)
        return True

    manager._stop_and_join_if_running = stop_and_join

    manager._handle_shutdown()

    assert removed == ["first", "second", "third"]
    assert manager.tasks == {}
