import json
import os
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).parents[1]


def run_global_config(env):
    code = (
        "import json, GlobalConfig as c; "
        "print(json.dumps({"
        "'source': c.SOURCE_PATH, 'project': c.PRJ_PATH, "
        "'config': c.CONFIG_PATH, 'data': c.DATA_PATH, 'log': c.LOG_PATH, "
        "'export': c.EXPORT_PATH, 'products': c.PRODUCTS_PATH, 'models': c.MODEL_PATH, "
        "'iis': c.resolve_iis_config_files()}))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code], cwd=PROJECT_ROOT, check=True, text=True,
        env={**os.environ, **env}, stdout=subprocess.PIPE)
    return json.loads(completed.stdout)


def test_runtime_directories_can_be_externalized(tmp_path):
    values = run_global_config({
        "IIS_CONFIG_DIR": str(tmp_path / "config"),
        "IIS_DATA_DIR": str(tmp_path / "data"),
        "IIS_LOG_DIR": str(tmp_path / "log"),
        "IIS_EXPORT_DIR": str(tmp_path / "export"),
        "IIS_PRODUCTS_DIR": str(tmp_path / "products"),
        "IIS_MODEL_DIR": str(tmp_path / "models"),
        "IIS_CONFIG": "",
    })

    assert values["source"] == str(PROJECT_ROOT)
    assert values["project"] == str(PROJECT_ROOT)
    assert values["config"] == str(tmp_path / "config")
    assert values["data"] == str(tmp_path / "data")
    assert values["log"] == str(tmp_path / "log")
    assert values["export"] == str(tmp_path / "export")
    assert values["products"] == str(tmp_path / "products")
    assert values["models"] == str(tmp_path / "models")
    assert values["iis"][0] == str(tmp_path / "config" / "iis.yaml")
    assert values["iis"][1] == str(PROJECT_ROOT / "_config" / "iis_example.yaml")


def test_explicit_iis_config_disables_example_fallback(tmp_path):
    explicit = tmp_path / "production.yaml"
    values = run_global_config({"IIS_CONFIG": str(explicit)})
    assert values["iis"] == [str(explicit), None]


def test_preflight_uses_external_config_and_data_directories(tmp_path):
    code = (
        "import json; from Scripts import deployment_preflight as d; "
        "print(json.dumps({'config': str(d.CONFIG_DIR), 'data': str(d.DATA_DIR), "
        "'models': str(d.MODEL_DIR)}))"
    )
    env = {
        **os.environ,
        "IIS_CONFIG_DIR": str(tmp_path / "config"),
        "IIS_DATA_DIR": str(tmp_path / "data"),
        "IIS_MODEL_DIR": str(tmp_path / "models"),
    }
    completed = subprocess.run(
        [sys.executable, "-c", code], cwd=PROJECT_ROOT, check=True, text=True,
        env=env, stdout=subprocess.PIPE)
    values = json.loads(completed.stdout)
    assert values == {
        "config": str(tmp_path / "config"),
        "data": str(tmp_path / "data"),
        "models": str(tmp_path / "models"),
    }
