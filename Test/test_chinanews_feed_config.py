from CrawlTasks.news import task_crawl_chinanews as chinanews


def test_chinanews_excludes_high_volume_mixed_scroll_feed():
    assert chinanews.feed_list == {
        "要闻导读": "https://www.chinanews.com.cn/rss/importnews.xml",
        "时政新闻": "https://www.chinanews.com.cn/rss/china.xml",
        "国际新闻": "https://www.chinanews.com.cn/rss/world.xml",
        "财经新闻": "https://www.chinanews.com.cn/rss/finance.xml",
    }
    assert all("scroll-news.xml" not in url
               for url in chinanews.feed_list.values())
