from pathlib import Path

import pytest

from AIClientCenter.factory_engine import load_config, validate_config


CONFIG_PATH = Path(__file__).parents[1] / "_config" / "ai_clients_gemini_80_example.yaml"


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_RATE_LIMIT_DB", str(tmp_path / "limits.sqlite3"))


def test_config_registers_nothing_without_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    clients, limits = load_config(CONFIG_PATH)
    assert clients == {}
    assert limits == {"gemini_flash_80": 3}
    assert validate_config(clients, limits)[0] == []


def test_config_builds_shared_per_model_limiters(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODELS", "gemini-3.5-flash-lite,gemini-3.5-flash")
    clients, limits = load_config(CONFIG_PATH)
    errors, _warnings = validate_config(clients, limits)
    assert not errors
    assert len(clients) == 3
    assert limits == {"gemini_flash_80": 3}
    lite = [client for client in clients.values() if "flash-lite" in client.name]
    assert lite[0].rate_limiter is lite[1].rate_limiter
    assert lite[0].get_rate_limit_snapshot()["effective_limits"] == {
        "rpm": 12, "tpm": 200_000, "rpd": 400, "tpd": None,
    }
    assert {client.group_id for client in clients.values()} == {"gemini_flash_80"}


def test_config_rejects_url_style_group_override(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    path = tmp_path / "bad.yaml"
    path.write_text(
        "clients:\n"
        "  - factory: gemini_free\n"
        "    name: Gemini\n"
        "    group: configured\n"
        "    group_id: hidden-override\n"
        "group_limits:\n"
        "  configured: 1\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="构造失败"):
        load_config(path)
