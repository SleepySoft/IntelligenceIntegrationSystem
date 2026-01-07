import uuid
import time
import threading
import requests
from flask import Flask, request, jsonify

from IntelligenceHub import IntelligenceHub, CollectedData
from IntelligenceHubWebService import post_collected_intelligence
from recycled.TestContent import CONTENT_TRUMP_GOT_FUCKED
from prompts_v1x import ANALYSIS_PROMPT
from MyPythonUtility.DictTools import DictPrinter


class MockCollector:
    """模拟数据采集客户端"""

    def __init__(self, hub_url):
        self.hub_url = hub_url
        self.sent_data = []

    def generate_data(self, count=1, auto_uuid=True):
        """生成测试数据"""
        test_data = []
        for i in range(count):
            data = {
                "type": "sensor_data",
                "value": f"test_{i}",
                "timestamp": time.time()
            }
            if auto_uuid:
                data["UUID"] = str(uuid.uuid4())
            test_data.append(data)
        return test_data

    def send_batch(self, data_list):
        """批量发送数据"""
        results = []
        for data in data_list:
            resp = requests.post(
                f"{self.hub_url}/collect",
                json=data,
                timeout=3
            )
            results.append((data['UUID'], resp))
        return results


class MockIntelligenceProcessor:
    """模拟智能处理服务"""

    def __init__(self, hub_url, process_delay=0, failure_rate=0):
        self.server = None
        self.app = Flask(__name__)
        self.hub_url = hub_url
        self.process_delay = process_delay
        self.failure_rate = failure_rate
        self.received_data = []

        @self.app.route('/process', methods=['POST'])
        def process():
            try:
                data = request.json

                print(DictPrinter.pretty_print(
                    data,
                    indent=2,
                    sort_keys=True,
                    colorize=True,
                    max_depth=4
                ))

                self.received_data.append(data)

                # # 模拟随机失败
                # if random.random() < self.failure_rate:
                #     return jsonify({"status": "error"}), 500

                # 异步反馈处理结果
                threading.Thread(target=self.send_feedback, args=(data,)).start()
                return jsonify({"status": "accepted"})

            except Exception as e:
                print(f'Process error: {str(e)}')
                return jsonify({"status": "rejected"})

    def send_feedback(self, original_data):
        """模拟处理完成后的反馈"""
        processed_data = {
            "UUID": original_data["UUID"],
            "processed": True,
            "analysis": f"",
            "timestamp": time.time()
        }
        requests.post(
            f"{self.hub_url}/processed",
            json=processed_data,
            timeout=3
        )

    def start(self, port=5001):
        self.server = threading.Thread(
            target=lambda: self.app.run(port=port, use_reloader=False)
        )
        self.server.start()


def hub():
    """测试用IntelligenceHub实例"""
    hub = IntelligenceHub(
        serve_port=5000,
        mongo_db_uri="mongodb://localhost:27017/test_db",
        intelligence_processor_uri="http://localhost:5001/process",
        intelligence_process_timeout=2,
        intelligence_process_max_retries=3
    )
    yield hub
    # 测试结束后清理测试数据库
    hub.db.drop_collection("processed_data")
    hub.shutdown()


def processor():
    """模拟处理服务"""
    processor = MockIntelligenceProcessor("http://localhost:5000")
    processor.start(port=5001)
    yield processor
    requests.get("http://localhost:5001/shutdown")  # 需要给Mock处理器添加关闭端点


def test_normal_processing_flow(hub, processor):
    """测试完整处理流程"""
    collector = MockCollector("http://localhost:5000")
    test_data = collector.generate_data(3)

    # 发送测试数据
    collector.send_batch(test_data)

    # 验证处理结果
    start_time = time.time()
    while time.time() - start_time < 5:  # 等待处理完成
        processed = list(hub.archive_col.find())
        if len(processed) == 3:
            break
        time.sleep(0.1)

    assert len(processed) == 3
    for doc in processed:
        assert "processed" in doc
        assert doc["analysis"].startswith("result_test_")


def test_retry_mechanism(hub):
    """测试超时重试机制"""
    # 使用会超时的处理器
    slow_processor = MockIntelligenceProcessor(
        "http://localhost:5000",
        process_delay=3  # 超过2秒超时设定
    )
    slow_processor.start(port=5002)
    hub.intelligence_processor_uri = "http://localhost:5002/process"

    collector = MockCollector("http://localhost:5000")
    data = collector.generate_data(1)[0]

    # 发送数据并验证重试
    collector.send_batch([data])
    time.sleep(1)

    # 检查重试次数
    with hub.lock:
        item = hub.processing_map.get(data['UUID'])
        assert item[0] == 0  # 初始重试次数

    # 等待超时检测
    time.sleep(3)
    with hub.lock:
        assert data['UUID'] not in hub.processing_map
        assert hub.original_queue.qsize() >= 1


def test_error_handling(hub, processor):
    """测试错误处理流程"""
    # 配置高故障率处理器
    processor.failure_rate = 1.0  # 100%失败

    collector = MockCollector("http://localhost:5000")
    data = collector.generate_data(1)[0]

    collector.send_batch([data])

    # 等待重试耗尽
    time.sleep(5)

    # 验证是否丢弃
    with hub.lock:
        assert hub.original_queue.qsize() == 0
        assert data['UUID'] not in hub.processing_map

    # 验证未进入归档
    assert hub.archive_col.count_documents({"UUID": data["UUID"]}) == 0


def main():
    mock_processor = MockIntelligenceProcessor('http://localhost:5000')
    mock_processor.start()

    while True:
        data = CollectedData(
            UUID=str(uuid.uuid4()),
            token='SleepySoft',
            source='IntelligenceHubTest',
            target='',
            prompt=ANALYSIS_PROMPT,

            content=CONTENT_TRUMP_GOT_FUCKED,
        )
        try:
            print('------------------------------------------------------------------------------')
            result = post_collected_intelligence('http://127.0.0.1:5000', data, timeout=5)
            print(result)
        except Exception as e:
            print(str(e))
        time.sleep(1)


if __name__ == '__main__':
    main()

