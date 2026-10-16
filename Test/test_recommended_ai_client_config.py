from pathlib import Path

from AIClientCenter.configuration import load_config, validate_config


CONFIG_PATH = Path(__file__).parents[1] / "_config" / "ai_clients_recommended_example.yaml"


def _clear(monkeypatch):
    for name in ("GEMINI_API_KEY", "GROQ_API_KEY", "NVIDIA_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_recommended_config_is_valid_without_credentials(monkeypatch):
    _clear(monkeypatch)
    clients, limits = load_config(CONFIG_PATH)
    assert clients == {}
    assert limits == {"gemini_free": 3, "groq_free": 1, "nvidia_free": 1}
    assert validate_config(clients, limits)[0] == []


def test_recommended_config_loads_registered_presets(monkeypatch, tmp_path):
    _clear(monkeypatch)
    monkeypatch.setenv("GROQ_API_KEY", "groq-test")
    monkeypatch.setenv("GROQ_RATE_LIMIT_DB", str(tmp_path / "limits.sqlite3"))
    clients, limits = load_config(CONFIG_PATH)
    errors, _warnings = validate_config(clients, limits)
    assert not errors
    assert list(clients) == ["Groq Free Pool"]
    assert clients["Groq Free Pool"].group_id == "groq_free"
    assert limits["groq_free"] == 1
