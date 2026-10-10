from pathlib import Path

from AIClientCenter.cli.launcher import load_config, validate_config


CONFIG_PATH = Path(__file__).parents[1] / "_config" / "ai_client_config_recommended_example.py"
KEY_ENV_NAMES = (
    "SILICON_API_KEY",
    "MODELSCOPE_API_KEY",
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "CLOUDFLARE_API_TOKEN",
    "CLOUDFLARE_ACCOUNT_ID",
    "OPENROUTER_API_KEY",
    "MISTRAL_API_KEY",
    "NVIDIA_API_KEY",
    "ENABLE_NVIDIA_RESEARCH_CLIENT",
)


def _clear_provider_environment(monkeypatch):
    for name in KEY_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_recommended_config_registers_nothing_without_credentials(monkeypatch):
    _clear_provider_environment(monkeypatch)
    clients, limits = load_config(CONFIG_PATH)

    assert clients == {}
    assert limits["groq_free"] == 1
    assert validate_config(clients, limits)[0] == []


def test_recommended_config_builds_rotating_provider_pools(monkeypatch):
    _clear_provider_environment(monkeypatch)
    monkeypatch.setenv("GROQ_API_KEY", "groq-test")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-test")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "cloudflare-test")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "account-test")
    clients, limits = load_config(CONFIG_PATH)

    assert set(clients) == {"groq_free", "openrouter_free", "cloudflare_free"}
    assert [clients["groq_free"].model_rotator.get_next() for _ in range(3)] == [
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
        "qwen/qwen3.8-27b",
    ]
    assert clients["openrouter_free"].model_rotator.get_next() == "openrouter/free"
    assert clients["cloudflare_free"].get_api_base_url().endswith(
        "/accounts/account-test/ai/v1"
    )
    assert all(limits[client.group_id] == 1 for client in clients.values())
    assert validate_config(clients, limits)[0] == []


def test_recommended_config_uses_current_gemini_free_models(monkeypatch):
    _clear_provider_environment(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test")
    clients, _limits = load_config(CONFIG_PATH)

    assert [clients["gemini_free"].model_rotator.get_next() for _ in range(2)] == [
        "gemini-3.5-flash-lite",
        "gemini-3.5-flash",
    ]
