from ServiceComponent import IntelligenceAnalyzerProxy as proxy


class FakeClient:
    def __init__(self):
        self.messages = None

    def chat(self, **kwargs):
        self.messages = kwargs["messages"]
        return {
            "choices": [{"message": {"content": '{"TAXONOMY":"无情报价值"}'}}]
        }


def test_transient_analyzer_parses_response_without_recording_conversation(monkeypatch):
    def fail_if_recorded(*args, **kwargs):
        raise AssertionError("transient analysis must not record conversation")

    monkeypatch.setattr(proxy, "record_conversation", fail_if_recorded)
    client = FakeClient()
    result = proxy.analyze_with_ai_transient(
        client,
        "Date={{CURRENT_DATE}}\n{{CONTENT}}\nSimilar={{SIMILAR_MESSAGES}}",
        {
            "UUID": "debug-1",
            "title": "调试",
            "content": "这是一段长度足够的人工调试正文。",
            "informant": "test://manual/debug-1",
        },
    )
    assert result == {"TAXONOMY": "无情报价值"}
    assert len(client.messages) == 1
    assert "## metadata" in client.messages[0]["content"]
    assert "## 正文内容" in client.messages[0]["content"]
    assert "{{CONTENT}}" not in client.messages[0]["content"]
    assert "{{CURRENT_DATE}}" not in client.messages[0]["content"]
