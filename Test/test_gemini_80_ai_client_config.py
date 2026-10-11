from pathlib import Path

from AIClientCenter.cli.launcher import load_config, validate_config


CONFIG_PATH = Path(__file__).parents[1] / "_config" / "ai_client_config_gemini_80_example.py"


def test_config_registers_nothing_without_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    clients, limits = load_config(CONFIG_PATH)

    assert clients == {}
    assert limits == {"gemini_flash_80": 1}


def test_config_builds_shared_per_model_limiters_at_80_percent(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODELS", "gemini-3.5-flash-lite,gemini-3.5-flash")
    monkeypatch.setenv("GEMINI_FLASH_LITE_SLOTS", "2")
    monkeypatch.setenv("GEMINI_FLASH_QUALITY_SLOTS", "1")

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
        "rpm": 3200, "tpm": 3_200_000, "rpd": 120_000,
    }
    assert quality[0].get_rate_limit_snapshot()["effective_limits"] == {
        "rpm": 800, "tpm": 1_600_000, "rpd": 8000,
    }


def test_config_can_use_80_percent_of_unlimited_rpd_model(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODELS", "gemini-2.5-flash-lite")
    monkeypatch.setenv("GEMINI_FLASH_LITE_SLOTS", "1")

    clients, _limits = load_config(CONFIG_PATH)
    snapshot = next(iter(clients.values())).get_rate_limit_snapshot()

    assert snapshot["effective_limits"] == {
        "rpm": 3200, "tpm": 3_200_000, "rpd": None,
    }
