import requests
import time
import random

API_URL = "http://localhost:8000"


class RemoteSpiderSDK:
    """A lightweight wrapper for the HTTP RPC API."""

    def __init__(self, spider_name):
        self.spider = spider_name

    def register_list(self, url, group, interval=60):
        requests.post(f"{API_URL}/rpc/register_task", json={
            "spider": self.spider,
            "group": group,
            "url": url,
            "interval": interval
        })

    def should_crawl(self, url, task_type="ARTICLE"):
        resp = requests.post(f"{API_URL}/rpc/should_crawl", json={
            "spider": self.spider,
            "url": url,
            "task_type": task_type
        })
        return resp.json()['should_crawl']

    def report(self, url, group, task_type, status, http_code=0, duration=0.0, error_msg=None):
        requests.post(f"{API_URL}/rpc/report_result", json={
            "spider": self.spider,
            "group": group,
            "url": url,
            "task_type": task_type,
            "status": status,
            "http_code": http_code,
            "duration": duration,
            "error_msg": error_msg
        })


def run_spider_process():
    sdk = RemoteSpiderSDK("rpc_worker_01")

    # 1. Register a task
    group = "Remote_News"
    list_url = "http://remote-news.com/feed"
    sdk.register_list(list_url, group, interval=10)

    print(f"Spider {sdk.spider} started. Connected to Governance Core via RPC.")

    while True:
        # Mock Logic
        if sdk.should_crawl(list_url, "LIST"):
            print(f"Crawling List: {list_url}")
            # Simulate work
            time.sleep(1)
            sdk.report(list_url, group, "LIST", status=1, duration=1.2)

            # Found articles
            articles = [f"{list_url}/{i}" for i in range(random.randint(1, 3))]

            for art in articles:
                if sdk.should_crawl(art, "ARTICLE"):
                    print(f"  > Crawling Article: {art}")
                    start = time.time()
                    time.sleep(0.2)

                    # Random outcome
                    dice = random.random()
                    if dice > 0.8:
                        # Network Error
                        sdk.report(art, group, "ARTICLE", status=2, http_code=503, duration=0.2,
                                   error_msg="Gateway Timeout")
                    elif dice > 0.1:
                        # Success
                        sdk.report(art, group, "ARTICLE", status=1, http_code=200, duration=time.time() - start)
                    else:
                        # Perm Fail
                        sdk.report(art, group, "ARTICLE", status=3, http_code=404, duration=0.1, error_msg="Not Found")

        else:
            print(".", end="", flush=True)
            time.sleep(2)


if __name__ == "__main__":
    run_spider_process()
gi