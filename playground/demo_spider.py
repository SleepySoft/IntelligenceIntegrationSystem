import random
import time
from governance_core import GovernanceManager, TaskType


# Mock function to simulate network request
def mock_request(url):
    time.sleep(0.5)  # Simulate latency

    # Simulate random outcomes
    dice = random.random()
    if dice < 0.7:
        return 200, "<html><body>Content</body></html>"
    elif dice < 0.9:
        raise Exception("Connection Timeout")  # Network error
    else:
        return 404, ""  # Not found


def main():
    # 1. Initialize Governance
    spider_name = "bbc_spider"
    governer = GovernanceManager(spider_name)

    # 2. Register Recurrent Tasks (e.g., RSS Feeds)
    rss_feeds = [
        ("http://bbc.com/news/world", "International"),
        ("http://bbc.com/news/tech", "Technology")
    ]

    for url, group in rss_feeds:
        # Set interval to 10 seconds for demo purposes
        governer.register_task(url, group, interval=10)

    print("--- Spider Started ---")

    while True:
        # 3. Iterate over groups
        for rss_url, group in rss_feeds:

            # A. Check if List Page needs crawling
            if not governer.should_crawl(rss_url, TaskType.LIST):
                continue

            print(f"\nProcessing List: {group}")

            # Start List Transaction
            with governer.transaction(rss_url, group, TaskType.LIST) as task:
                # Simulate fetching list
                code, html = mock_request(rss_url)

                if code == 200:
                    task.success()
                    # Fake extracting 3 articles
                    article_urls = [f"{rss_url}/article_{random.randint(100, 999)}" for _ in range(3)]
                else:
                    task.fail_temp(http_code=code)
                    article_urls = []

            # B. Process Articles found in list
            for url in article_urls:

                # Check if Article needs crawling (Dedup & Retry logic)
                if not governer.should_crawl(url, TaskType.ARTICLE):
                    print(f"  Skipping {url} (Already done or cooldown)")
                    continue

                # Start Article Transaction
                with governer.transaction(url, group, TaskType.ARTICLE) as task:
                    print(f"  Crawling {url}...", end="")
                    try:
                        code, content = mock_request(url)

                        if code == 200:
                            task.save_snapshot(content)  # Save to file
                            task.success()
                            print(" OK")
                        elif code == 404:
                            task.fail_perm(http_code=404, error_msg="Not Found")
                            print(" 404 (Perm Fail)")
                        else:
                            raise Exception("Unknown Error")

                    except Exception as e:
                        # Network errors -> Retryable
                        task.fail_temp(error_msg=str(e))
                        print(f" Network Error (Will Retry)")

                # Flow Control
                governer.wait_interval(default_seconds=1.0)

        # End of loop logic
        print("Waiting for next round...")
        time.sleep(2)  # Just to prevent console spam in this demo loop


if __name__ == "__main__":
    main()
