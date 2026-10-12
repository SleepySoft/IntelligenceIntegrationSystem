from pathlib import Path

import pytest

from AIClientCenter.cli.launcher import load_config, validate_config


CONFIG_PATH = Path(__file__).parents[1] / "_config" / "ai_client_config_gemini_80_example.py"


@pytest.fixture(autouse=True)
def isolated_rate_limit_database(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_RATE_LIMIT_DB", str(tmp_path / "gemini_rate_limits.sqlite3"))
    monkeypatch.setenv("GROQ_RATE_LIMIT_DB", str(tmp_path / "groq_rate_limits.sqlite3"))
    monkeypatch.delenv("GROQ_API_KEY", raising=False)


def test_config_registers_nothing_without_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    clients, limits = load_config(CONFIG_PATH)

    assert clients == {}
    assert limits == {"gemini_flash_80": 1}


def test_config_builds_shared_per_model_limiters_at_80_percent(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODELS", "gemini-3.5-flash-lite,gemini-3.5-flash")
    monkeypatch.setenv("GEMINI_PRIMARY_SLOTS", "2")
    monkeypatch.setenv("GEMINI_RESERVE_SLOTS", "1")

    clients, limits = load_config(CONFIG_PATH)
    errors, warnings = validate_config(clients, limits)

    assert not errors
    assert not warnings
    assert len(clients) == 3
    assert limits == {"gemini_flash_80": 3}
    lite = [client for client in clients.values() if "flash-lite" in client.name]
    quality = [client for client in clients.values() if "flash-lite" not in client.name]
    assert lite[0].rate_limiter is lite[1].rate_limiter
    assert lite[0].get_rate_limit_snapshot()["effective_limits"] == {
        "rpm": 12, "tpm": 200_000, "rpd": 400, "tpd": None,
    }
    assert quality[0].get_rate_limit_snapshot()["effective_limits"] == {
        "rpm": 4, "tpm": 200_000, "rpd": 16, "tpd": None,
    }
    assert lite[0].get_rate_limit_snapshot()["persistent"] is True
    assert lite[0].get_rate_limit_snapshot()["storage_namespace"] == (
        "google-generative-language:gemini-3.5-flash-lite"
    )


def test_config_applies_free_quota_to_legacy_lite_model(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODELS", "gemini-2.5-flash-lite")
    monkeypatch.setenv("GEMINI_RESERVE_SLOTS", "1")

    clients, _limits = load_config(CONFIG_PATH)
    snapshot = next(iter(clients.values())).get_rate_limit_snapshot()

    assert snapshot["effective_limits"] == {
        "rpm": 8, "tpm": 200_000, "rpd": 16, "tpd": None,
    }


def test_config_can_register_only_the_groq_large_model(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")

    clients, limits = load_config(CONFIG_PATH)
    errors, warnings = validate_config(clients, limits)

    assert not errors
    assert not warnings
    assert len(clients) == 1
    client = next(iter(clients.values()))
    assert client.get_current_model() == "openai/gpt-oss-120b"
    assert limits == {"gemini_flash_80": 1, "groq_free_large": 1}


def test_config_contains_all_confirmed_free_models(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_PRIMARY_SLOTS", "1")
    monkeypatch.setenv("GEMINI_RESERVE_SLOTS", "1")

    clients, limits = load_config(CONFIG_PATH)

    models = {client.get_current_model() for client in clients.values()}
    assert models == {
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash-lite",
        "gemini-2.5-flash-lite",
        "gemini-2.5-flash",
        "gemini-3-flash",
        "gemini-3.5-flash",
        "gemini-3.6-flash",
        "gemini-3.7-flash",
        "gemini-3.8-flash",
    }
    assert limits == {"gemini_flash_80": 9}
