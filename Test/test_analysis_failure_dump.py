import json

from pydantic import BaseModel, ValidationError

from ServiceComponent.analysis_failure_dump import AnalysisFailureRecorder


class ExpectedResponse(BaseModel):
    count: int


def _validation_error():
    try:
        ExpectedResponse.model_validate({"count": "invalid"})
    except ValidationError as exc:
        return exc
    raise AssertionError("expected validation error")


def _record(recorder, attempt):
    return recorder.record(
        category="validation",
        error=_validation_error(),
        raw_response={"count": "invalid", "attempt": attempt},
        original_data={
            "UUID": "source-id",
            "title": "sample",
            "informant": "https://example.test/item",
            "source": "crawler",
            "content": "must not be copied into failure metadata",
        },
        subsystem="news",
        trace_id="trace-id",
        attempt=attempt,
        prompt_version=4,
        prompt="prompt text",
        ai_service="test://ai",
        ai_model="test-model",
        transient=False,
    )


def test_failure_recorder_persists_raw_response_and_structured_reason(tmp_path):
    recorder = AnalysisFailureRecorder(tmp_path, max_files=5)

    target = _record(recorder, 1)
    document = json.loads(target.read_text(encoding="utf-8"))

    assert document["category"] == "validation"
    assert document["source_uuid"] == "source-id"
    assert document["attempt"] == 1
    assert document["raw_response"] == {"count": "invalid", "attempt": 1}
    assert document["error"]["type"] == "ValidationError"
    assert document["error"]["details"][0]["loc"] == ["count"]
    assert "content" not in document["input"]
    assert len(document["prompt_sha256"]) == 64


def test_failure_recorder_keeps_only_configured_number_of_samples(tmp_path):
    recorder = AnalysisFailureRecorder(tmp_path, max_files=2)

    _record(recorder, 1)
    _record(recorder, 2)
    _record(recorder, 3)

    files = list(tmp_path.glob("*.json"))
    assert len(files) == 2
    attempts = {
        json.loads(path.read_text(encoding="utf-8"))["attempt"] for path in files
    }
    assert attempts == {2, 3}
