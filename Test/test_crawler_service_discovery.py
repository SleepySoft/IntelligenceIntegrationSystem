import threading
from types import SimpleNamespace

from CrawlerServiceEngine import TaskManager, is_task_plugin_path


def test_task_discovery_only_selects_production_plugins(tmp_path):
    (tmp_path / "task_crawl_news.py").write_text("", encoding="utf-8")
    (tmp_path / "task_example.py").write_text("", encoding="utf-8")
    (tmp_path / "crawler_config_news.py").write_text("", encoding="utf-8")

    selected = TaskManager._list_task_files(str(tmp_path))

    assert selected == [str(tmp_path / "task_crawl_news.py")]
    assert is_task_plugin_path("task_crawl_news.py")
    assert not is_task_plugin_path("task_example.py")
    assert not is_task_plugin_path("crawler_config_news.py")


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
