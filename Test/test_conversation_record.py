"""对话记录新格式（[meta]/[request]/[response]）测试。"""

import json

import ServiceComponent.IntelligenceAnalyzerProxy as proxy
from MyPythonUtility.FileSqliteHyridDB import HybridDB


def test_record_conversation_writes_meta_request_and_raw_response(tmp_path, monkeypatch):
    monkeypatch.setattr(proxy, "conversation_db", HybridDB(str(tmp_path)))

    messages = [
        {"role": "system", "content": "系统提示"},
        {"role": "user", "content": "正文内容"},
    ]
    response = {
        "model": "test-model-1",
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        "choices": [{"message": {"role": "assistant", "content": "{\"ok\": true}"}}],
        "_ai_client_meta": {"request_id": "req-1"},
    }

    index = proxy.record_conversation(
        "analysis", messages, response,
        meta={"prompt_version": 44, "uuid": "u-1", "elapsed_ms": 123},
    )

    content = proxy.conversation_db.get_content_by_index(index)
    assert "[meta]" in content
    meta_line = content.split("[meta]")[1].split("[request]")[0].strip()
    meta = json.loads(meta_line)
    assert meta["model"] == "test-model-1"
    assert meta["usage"]["prompt_tokens"] == 100
    assert meta["prompt_version"] == 44
    assert meta["uuid"] == "u-1"
    assert meta["elapsed_ms"] == 123
    assert meta["client_meta"]["request_id"] == "req-1"
    assert meta["category"] == "analysis"

    assert "[request]" in content and "[system]" in content and "[user]" in content
    assert "正文内容" in content

    response_block = content.split("[response]")[1]
    raw = json.loads(response_block)
    assert raw["choices"][0]["message"]["content"] == "{\"ok\": true}"


def test_record_conversation_tolerates_error_response(tmp_path, monkeypatch):
    monkeypatch.setattr(proxy, "conversation_db", HybridDB(str(tmp_path)))
    index = proxy.record_conversation(
        "analysis", [{"role": "user", "content": "x"}],
        {"error": "HTTP_503", "message": "busy"})
    content = proxy.conversation_db.get_content_by_index(index)
    assert json.loads(content.split("[response]")[1])["error"] == "HTTP_503"


def test_parse_ai_response_repairs_json_without_polluting_domain_payload(caplog):
    response = {
        "choices": [{"message": {"content": '{"answer":"ok",}'}}],
    }

    with caplog.at_level("INFO", logger=proxy.__name__):
        parsed = proxy.parse_ai_response(response)

    assert parsed == {"answer": "ok"}
    assert "warning" not in parsed
    assert "AI response JSON syntax repaired" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_parse_ai_response_expected_repair_failure_has_no_traceback(monkeypatch, caplog):
    monkeypatch.setattr(
        proxy.json_repair,
        "loads",
        lambda _value: (_ for _ in ()).throw(ValueError("known malformed output")),
    )
    response = {
        "choices": [{"message": {"content": '{"answer":'}}],
    }

    with caplog.at_level("WARNING", logger=proxy.__name__):
        parsed = proxy.parse_ai_response(response)

    assert parsed == {"error": "Cannot parse AI response to JSON."}
    assert "known malformed output" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_parse_ai_response_unexpected_repair_failure_has_traceback(monkeypatch, caplog):
    monkeypatch.setattr(
        proxy.json_repair,
        "loads",
        lambda _value: (_ for _ in ()).throw(RuntimeError("repair implementation bug")),
    )
    response = {
        "choices": [{"message": {"content": '{"answer":'}}],
    }

    with caplog.at_level("ERROR", logger=proxy.__name__):
        parsed = proxy.parse_ai_response(response)

    assert parsed == {"error": "Unexpected error while parsing AI response JSON."}
    assert "repair implementation bug" in caplog.text
    assert any(record.exc_info is not None for record in caplog.records)
