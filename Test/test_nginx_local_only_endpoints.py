from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PROTECTED_PATHS = (
    "location = /maintenance/ping",
    "location = /api/runtime/state",
    "location ^~ /api/maintenance/backup/",
)


def test_nginx_local_only_snippet_blocks_all_operational_endpoints():
    text = (ROOT / "deploy/nginx/iis-local-only-locations.conf").read_text(
        encoding="utf-8")
    for location in PROTECTED_PATHS:
        assert location in text
    assert text.count("return 404;") == len(PROTECTED_PATHS)


def test_site_mode_public_proxy_keeps_local_only_endpoints_out():
    text = (ROOT / "Scripts/site_controller/deploy/service.conf").read_text(
        encoding="utf-8")
    generic_proxy = text.index("location / {")
    for location in PROTECTED_PATHS:
        assert text.index(location) < generic_proxy


def test_documented_nginx_config_keeps_local_only_endpoints_out():
    text = (ROOT / "doc/nginx/nginx.conf").read_text(encoding="utf-8")
    generic_proxy = text.index("location / {")
    for location in PROTECTED_PATHS:
        assert text.index(location) < generic_proxy
