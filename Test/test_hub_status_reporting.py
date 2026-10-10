from ServiceComponent.runtime.status import (
    HubStatusLogGate,
    format_hub_status,
)


def _statistics(*, emitted=1, subsystem_name="news"):
    return {
        "runtime": {
            "emitted": emitted,
            "processed": 0,
            "handler_failures": 0,
            "pending_events": 2,
            "active_handlers": 1,
            "in_flight_submissions": 0,
            "queued_by_type": {
                "analysis.requested": 1,
                "analysis.completed": 1,
            },
            "active_by_type": {"archive.requested": 1},
            "processed_by_type": {"intake.received": 3},
        },
        "analysis": {
            "waiting_client": 1,
            "ai_running": 1,
            "validation_running": 0,
            "attempts": 3,
            "responses": 2,
            "retries": 1,
            "validated": 0,
            "failed": 0,
            "call_errors": 0,
            "api_errors": 0,
            "validation_errors": 2,
        },
        "subsystems": [{"name": subsystem_name}],
    }


def test_status_is_a_single_line_with_pipeline_stage_counts():
    line = format_hub_status(_statistics())

    assert "\n" not in line
    assert "queued total=2 [intake=0/analysis=1/result=1/archive=0" in line
    assert "active total=1 [intake=0/analysis=0/result=0/archive=1" in line
    assert "ai wait=1/run=1/validate=0 attempt=3/response=2/retry=1/valid=0/fail=0" in line
    assert "errors(call=0/api=0/validation=2)" in line
    assert "done [intake=3/analysis=0" in line
    assert "subsystems" not in line


def test_status_gate_logs_changes_and_one_minute_heartbeat_only():
    gate = HubStatusLogGate(unchanged_log_interval=60)
    initial = _statistics()

    assert gate.should_log(initial, now=100) is True
    assert gate.should_log(initial, now=159) is False
    assert gate.should_log(initial, now=160) is True
    assert gate.should_log(_statistics(emitted=2), now=161) is True


def test_status_gate_ignores_non_numeric_subsystem_metadata_changes():
    gate = HubStatusLogGate(unchanged_log_interval=60)

    assert gate.should_log(_statistics(subsystem_name="news"), now=100) is True
    assert gate.should_log(_statistics(subsystem_name="dry_run"), now=101) is False
