from ServiceComponent import IntelligenceAnalyzerProxy as proxy


class FakeClient:
    def chat(self, **kwargs):
        return {
            "choices": [{"message": {"content": '{"TAXONOMY":"无情报价值"}'}}]
        }


def test_transient_analyzer_parses_response_without_recording_conversation(monkeypatch):
    def fail_if_recorded(*args, **kwargs):
        raise AssertionError("transient analysis must not record conversation")

    monkeypatch.setattr(proxy, "record_conversation", fail_if_recorded)
    result = proxy.analyze_with_ai_transient(
        FakeClient(),
        "Prompt",
        {
            "UUID": "debug-1",
            "title": "调试",
            "content": "这是一段长度足够的人工调试正文。",
            "informant": "test://manual/debug-1",
        },
    )
    assert result == {"TAXONOMY": "无情报价值"}
