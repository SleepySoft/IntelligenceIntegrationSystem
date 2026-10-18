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
