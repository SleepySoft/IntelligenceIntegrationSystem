import json
import stat

from deploy import iis_deploy


def test_read_os_release(tmp_path):
    path = tmp_path / "os-release"
    path.write_text(
        'ID=ubuntu\nVERSION_ID="24.04"\nNAME="Ubuntu"\n', encoding="utf-8")
    assert iis_deploy._read_os_release(path) == {
        "ID": "ubuntu",
        "VERSION_ID": "24.04",
        "NAME": "Ubuntu",
    }


def test_private_config_permission_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(iis_deploy, "PROJECT_DIR", tmp_path)
    config = tmp_path / "_config" / "iis.yaml"
    config.parent.mkdir()
    config.write_text("access: {}\n", encoding="utf-8")
    config.chmod(stat.S_IRUSR | stat.S_IWUSR)
    result = iis_deploy._permission_result(config)
    assert result.status == "pass"
    assert "0600" in result.summary


def test_shared_config_permission_warns(tmp_path, monkeypatch):
    monkeypatch.setattr(iis_deploy, "PROJECT_DIR", tmp_path)
    config = tmp_path / "_config" / "collector.json"
    config.parent.mkdir()
    config.write_text("{}\n", encoding="utf-8")
    config.chmod(0o644)
    result = iis_deploy._permission_result(config)
    assert result.status == "warn"


def test_json_output_never_reads_config_contents(tmp_path, monkeypatch, capsys):
    secret = "THIS_MUST_NOT_APPEAR"
    config_dir = tmp_path / "_config"
    config_dir.mkdir()
    for name in ("iis.yaml", "ai_clients.yaml", "collector.json"):
        (config_dir / name).write_text(secret, encoding="utf-8")
    monkeypatch.setattr(iis_deploy, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(iis_deploy, "run_doctor", iis_deploy.check_configs)
    assert iis_deploy.main(["doctor", "--json"]) == 0
    output = capsys.readouterr().out
    assert secret not in output
    assert json.loads(output)["schema"] == "iis-deployment-doctor/1"
