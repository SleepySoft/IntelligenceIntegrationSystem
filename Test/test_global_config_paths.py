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
        "'source': c.SOURCE_PATH, 'project': c.PRJ_PATH, 'runtime': c.RUNTIME_ROOT, "
        "'config': c.CONFIG_PATH, 'data': c.DATA_PATH, 'log': c.LOG_PATH, "
        "'export': c.EXPORT_PATH, 'products': c.PRODUCTS_PATH, 'models': c.MODEL_PATH, "
        "'iis': c.resolve_iis_config_files(), 'report': c.runtime_path_report()}))"
    )
    clean = {
        key: "" for key in (
            "IIS_RUNTIME_ROOT", "IIS_CONFIG_DIR", "IIS_DATA_DIR", "IIS_LOG_DIR",
            "IIS_EXPORT_DIR", "IIS_PRODUCTS_DIR", "IIS_MODEL_DIR", "IIS_CONFIG",
            "AI_CLIENT_CONFIG", "IIS_COLLECTOR_CONFIG",
        )
    }
    completed = subprocess.run(
        [sys.executable, "-c", code], cwd=PROJECT_ROOT, check=True, text=True,
        env={**os.environ, **clean, **env}, stdout=subprocess.PIPE)
    return json.loads(completed.stdout)


def test_default_runtime_paths_remain_inside_project():
    values = run_global_config({})
    assert values["runtime"] == str(PROJECT_ROOT)
    assert values["config"] == str(PROJECT_ROOT / "_config")
    assert values["data"] == str(PROJECT_ROOT / "_data")
    assert values["log"] == str(PROJECT_ROOT / "_log")
    assert values["export"] == str(PROJECT_ROOT / "_export")
    assert values["products"] == str(PROJECT_ROOT / "_products")
    assert values["models"] == str(PROJECT_ROOT / "_models")
    assert values["report"]["runtime_root"]["selected_by"] == "project_default"


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
    assert values["runtime"] == str(PROJECT_ROOT)
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


def test_runtime_root_moves_only_writable_underscore_directories(tmp_path):
    runtime = tmp_path / "runtime"
    values = run_global_config({"IIS_RUNTIME_ROOT": str(runtime)})

    assert values["source"] == str(PROJECT_ROOT)
    assert values["project"] == str(PROJECT_ROOT)
    assert values["runtime"] == str(runtime)
    assert values["config"] == str(runtime / "_config")
    assert values["data"] == str(runtime / "_data")
    assert values["log"] == str(runtime / "_log")
    assert values["export"] == str(runtime / "_export")
    assert values["products"] == str(runtime / "_products")
    assert values["models"] == str(runtime / "_models")
    assert values["iis"][0] == str(runtime / "_config" / "iis.yaml")
    assert values["report"]["runtime_root"]["selected_by"] == "IIS_RUNTIME_ROOT"


def test_individual_directory_override_wins_over_runtime_root(tmp_path):
    runtime = tmp_path / "runtime"
    external_data = tmp_path / "large-disk" / "data"
    values = run_global_config({
        "IIS_RUNTIME_ROOT": str(runtime),
        "IIS_DATA_DIR": str(external_data),
    })

    assert values["config"] == str(runtime / "_config")
    assert values["data"] == str(external_data)
    assert values["report"]["directories"]["config"]["selected_by"] == "IIS_RUNTIME_ROOT"
    assert values["report"]["directories"]["data"]["selected_by"] == "IIS_DATA_DIR"


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
