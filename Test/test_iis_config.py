from pathlib import Path

import pytest
from pydantic import ValidationError

from IISConfig import load_iis_config
from VectorIntelligence.config import load_vector_db_settings


PROJECT_ROOT = Path(__file__).parents[1]


def test_example_is_valid_and_projects_component_settings():
    config = load_iis_config(PROJECT_ROOT / "_config" / "iis_example.yaml")

    assert config.get("mongodb.host") == "localhost"
    assert config.get("intelligence_hub.ai_analysis_thread") == 1
    assert config.get("intelligence_hub.translation.client_group") == "translate_small"
    assert config.get("intelligence_hub.subsystems.default") == "news"
    assert config.get(
        "intelligence_hub_web_service.intelligence_views.situation.min_score"
    ) == 6.0


def test_minimal_yaml_uses_code_defaults(tmp_path):
    path = tmp_path / "iis.yaml"
    path.write_text("{}\n", encoding="utf-8")

    config = load_iis_config(path)

    assert config.settings.server.port == 5000
    assert config.settings.hub.max_inflight == 2000
    assert config.get("intelligence_hub.vectordb.enabled") is False
    assert config.get("intelligence_hub.subsystems.list") == [{
        "name": "news",
        "display_name": "国际新闻",
        "enabled": True,
    }]


def test_unknown_fields_fail_fast(tmp_path):
    path = tmp_path / "iis.yaml"
    path.write_text("hub:\n  workerz: 3\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="workerz"):
        load_iis_config(path)


def test_default_subsystem_must_be_enabled(tmp_path):
    path = tmp_path / "iis.yaml"
    path.write_text(
        "hub:\n"
        "  subsystems:\n"
        "    default: finance\n"
        "    enabled:\n"
        "      news: News\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="finance"):
        load_iis_config(path)


def test_vector_endpoint_and_subsystem_detail_are_projected(tmp_path):
    subsystem_dir = tmp_path / "subsystems"
    subsystem_dir.mkdir()
    detail = subsystem_dir / "finance.json"
    detail.write_text("{}\n", encoding="utf-8")
    path = tmp_path / "iis.yaml"
    path.write_text(
        "hub:\n"
        "  subsystems:\n"
        "    default: finance\n"
        "    enabled:\n"
        "      finance: Finance\n"
        "optional_subsystems:\n"
        "  vector:\n"
        "    enabled: true\n"
        "    endpoint: http://vector.example:9000\n"
        "    database: db\n"
        "    model: model\n",
        encoding="utf-8",
    )

    config = load_iis_config(path)
    vector = load_vector_db_settings(config)
    entries = config.get("intelligence_hub.subsystems.list")

    assert vector == {
        "url": "http://vector.example:9000",
        "db_path": "db",
        "embedding_model_name": "model",
    }
    assert entries[0]["config_file"] == str(detail)


def test_translation_subsystem_projects_selected_client_group(tmp_path):
    path = tmp_path / "iis.yaml"
    path.write_text(
        "optional_subsystems:\n"
        "  translation:\n"
        "    enabled: true\n"
        "    client_group: translator_pool\n",
        encoding="utf-8",
    )

    config = load_iis_config(path)

    assert config.get("intelligence_hub.translation.enabled") is True
    assert config.get("intelligence_hub.translation.client_group") == "translator_pool"


def test_log_dump_redacts_credentials(tmp_path):
    path = tmp_path / "iis.yaml"
    path.write_text(
        "database:\n"
        "  password: mongo-secret\n"
        "access:\n"
        "  rpc_tokens: [rpc-secret]\n"
        "  collector_tokens: [collector-secret]\n",
        encoding="utf-8",
    )

    rendered = load_iis_config(path).dump_text()

    assert "mongo-secret" not in rendered
    assert "rpc-secret" not in rendered
    assert "collector-secret" not in rendered
    assert "***" in rendered
