import json
from pathlib import Path

from AIClientCenter.core.manager import BaseAIClient
from Scripts import benchmark_ai_clients as benchmark_cli
from ServiceComponent.ai_client_benchmark import (
    AIClientBenchmarkRunner,
    load_benchmark_suite,
)
from ServiceComponent.analysis_messages import build_analyze_message
from prompts_event_v4 import EVENT_ANALYSIS_PROMPT_V47


def _valuable_payload():
    return {
        "TAXONOMY": "政治与安全", "SUB_CATEGORY": ["国防军事"],
        "EVENT_TITLE": "A军袭击B设施", "EVENT_BRIEF": "A军袭击B设施。", "EVENT_TEXT": "A军对B设施实施袭击。",
        "IMPACT": "造成局部安全影响。", "REASON": "正文包含明确军事行动。",
        "RATE": {
                "影响广度": 4, "影响深度": 5, "新颖性与异常性": 5,
                "演化与连锁潜力": 4, "舆情及认知影响": 4, "可行动性": 5,
        },
        "TIPS": "",
        "EVENT_EXTRACTION": {
            "schema_version": "event-extraction/1.0", "primary_event_id": "E1",
            "entities": [
                {"id": "ENT1", "name": "A军", "type": "organization"},
                {"id": "ENT2", "name": "B设施", "type": "facility"},
            ],
            "events": [{
                "id": "E1",
                "core": {
                    "frame": {"dynamics": "process", "topology": "targeted", "agency": "agentive"},
                    "predicate": {"id": "attack", "surface": "袭击"},
                    "roles": {"actor": ["ENT1"], "target": ["ENT2"]},
                },
            }],
        },
    }


class FakeBenchmarkClient(BaseAIClient):
    def __init__(self, name, output):
        super().__init__(name=name, api_token="test")
        self.output = output

    def get_model_list(self):
        return {"data": [{"id": "fake-model"}]}

    def get_current_model(self):
        return "fake-model"

    def get_api_base_url(self):
        return "fake://benchmark"

    def _chat_completion_sync(self, messages, model=None, temperature=0.7,
                              max_tokens=4096, is_health_check=False):
        return {
            "success": True,
            "error": None,
            "data": {
                "model": "fake-model",
                "choices": [{
                    "message": {"content": self.output},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            },
        }


def _write_suite(path):
    path.write_text(json.dumps({
        "name": "unit-suite",
        "cases": [{
            "id": "attack-1",
            "tags": ["attack"],
            "collected_data": {
                "UUID": "benchmark-1", "token": "local", "source": "benchmark",
                "target": "benchmark", "title": "测试袭击", "authors": [],
                "content": "A军今天袭击了B设施，B方确认设施受损但没有人员伤亡。",
                "informant": "benchmark://1", "subsystem": "news",
            },
            "expected": {
                "route": "valuable", "required_event_predicates": ["attack"],
                "forbidden_claims": ["人员死亡"],
            },
        }],
    }, ensure_ascii=False), encoding="utf-8")


def test_suite_loader_validates_collected_data(tmp_path):
    suite_path = tmp_path / "suite.json"
    _write_suite(suite_path)
    name, cases = load_benchmark_suite(suite_path)
    assert name == "unit-suite"
    assert cases[0].collected_data["UUID"] == "benchmark-1"


def test_checked_in_example_suite_is_valid():
    suite_path = Path(__file__).parents[1] / "_config" / "ai_benchmark_suite_example.json"
    name, cases = load_benchmark_suite(suite_path)
    assert name == "event-v4-smoke"
    assert len(cases) == 3


def test_v47_keeps_system_prefix_stable_and_dynamic_data_in_user_message():
    first = {
        "UUID": "cache-1", "title": "第一篇", "content": "正文甲",
        "informant": "test://cache/1",
    }
    second = {
        "UUID": "cache-2", "title": "第二篇", "content": "正文乙",
        "informant": "test://cache/2",
    }

    messages_a = build_analyze_message(EVENT_ANALYSIS_PROMPT_V47, first)
    messages_b = build_analyze_message(EVENT_ANALYSIS_PROMPT_V47, second)

    assert [item["role"] for item in messages_a] == ["system", "user"]
    assert messages_a[0] == messages_b[0]
    assert "正文甲" not in messages_a[0]["content"]
    assert "cache-1" not in messages_a[0]["content"]
    assert "## 当前参考时间" in messages_a[1]["content"]
    assert "正文甲" in messages_a[1]["content"]
    assert messages_a[1] != messages_b[1]


def test_cli_preview_never_calls_client(tmp_path, monkeypatch, capsys):
    suite_path = tmp_path / "suite.json"
    _write_suite(suite_path)

    class PreviewOnlyClient(FakeBenchmarkClient):
        def _chat_completion_sync(self, *args, **kwargs):
            raise AssertionError("预览模式不应调用 Client")

    client = PreviewOnlyClient("Preview Client", "")
    monkeypatch.setattr(
        benchmark_cli,
        "load_config",
        lambda _path: ({"preview": client}, {"default": 1}),
    )
    monkeypatch.setattr(
        benchmark_cli,
        "validate_config",
        lambda _clients, _limits: ([], []),
    )

    result = benchmark_cli.main([
        "--config", str(tmp_path / "unused.py"),
        "--suite", str(suite_path),
        "--repeat", "2",
    ])

    assert result == 0
    assert "Planned real calls: 2" in capsys.readouterr().out
    assert client.get_status()["chat_count"] == 0


def test_benchmark_uses_production_message_builder_and_writes_detailed_reports(tmp_path):
    suite_path = tmp_path / "suite.json"
    _write_suite(suite_path)
    suite_name, cases = load_benchmark_suite(suite_path)
    valid = json.dumps(_valuable_payload(), ensure_ascii=False)
    runner = AIClientBenchmarkRunner({
        "valid": FakeBenchmarkClient("Valid Client", f"```json\n{valid}\n```"),
        "invalid": FakeBenchmarkClient("Invalid Client", '{"TAXONOMY":"政治与安全"}'),
        "repaired": FakeBenchmarkClient(
            "Repaired Client", f"{valid[:-1]},}}",
        ),
    }, {40: "Today={{CURRENT_DATE}}\n{{CONTENT}}"})

    summary = runner.run(
        suite_name=suite_name, cases=cases, repeat=1, prompt_version=40,
        output_dir=tmp_path / "run",
    )

    assert build_analyze_message("{{CONTENT}}", cases[0].collected_data)[0]["role"] == "user"
    records = [json.loads(line) for line in (tmp_path / "run" / "results.jsonl").read_text(
        encoding="utf-8").splitlines()]
    assert records[0]["api_success"] is True
    assert records[0]["v4_valid"] is True
    assert records[0]["correctness_pass"] is True
    assert records[0]["usage"]["total_tokens"] == 150
    assert records[1]["error_stage"] == "v4_validation"
    assert records[2]["json_repaired"] is True
    assert records[2]["v4_valid"] is True
    assert records[2]["correctness_pass"] is True
    assert records[2]["error_stage"] is None
    assert summary["clients"][0]["v4_valid_rate"] == 1.0
    assert summary["clients"][1]["v4_valid_rate"] == 0.0
    assert summary["client_models"][0]["model"] == "fake-model"
    assert (tmp_path / "run" / "manifest.json").is_file()
    assert (tmp_path / "run" / "summary.json").is_file()
    assert (tmp_path / "run" / "summary.md").is_file()
