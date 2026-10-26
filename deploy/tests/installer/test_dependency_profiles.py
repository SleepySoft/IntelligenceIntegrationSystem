from __future__ import annotations

from pathlib import Path

import pytest

from deploy.dependency_profiles import (
    DependencyProfileCatalog, ProfileError, parse_profile_arguments,
)


ROOT = Path(__file__).resolve().parents[3]
CATALOG = ROOT / "deploy/dependency_profiles.yaml"


def test_default_profiles_expand_in_dependency_order():
    plan = DependencyProfileCatalog(CATALOG, ROOT).resolve()

    assert plan.requested == ("core", "crawler")
    assert plan.profiles == ("core", "crawler")
    assert plan.services == ("web", "crawler")
    assert plan.browsers == ("chromium",)
    assert [path.name for path in plan.requirement_files] == [
        "core.txt", "crawler.txt"]
    assert len(plan.fingerprint) == 64


def test_vector_profile_automatically_includes_core():
    plan = DependencyProfileCatalog(CATALOG, ROOT).resolve(("vector",))

    assert plan.profiles == ("core", "vector")
    assert plan.services == ("web",)
    assert plan.browsers == ()
    assert [path.name for path in plan.requirement_files] == [
        "core.txt", "vector.txt"]


def test_full_profile_preserves_legacy_requirements_entry():
    plan = DependencyProfileCatalog(CATALOG, ROOT).resolve(("full",))

    assert plan.profiles == ("full",)
    assert plan.requirement_files == ((ROOT / "requirements.txt").resolve(),)
    assert plan.services == ("web", "crawler")


def test_profile_arguments_allow_repeat_and_comma_syntax():
    assert parse_profile_arguments(["core,crawler", "dev", "core"]) == (
        "core", "crawler", "dev")


def test_unknown_profile_reports_available_values():
    catalog = DependencyProfileCatalog(CATALOG, ROOT)

    with pytest.raises(ProfileError, match="未知依赖 profile"):
        catalog.resolve(("missing",))


def test_catalog_rejects_requirement_outside_source(tmp_path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("example\n", encoding="utf-8")
    manifest = tmp_path / "profiles.yaml"
    manifest.write_text(
        "schema: iis-dependency-profiles/1\n"
        "defaults: [bad]\n"
        "profiles:\n"
        "  bad:\n"
        "    requires: []\n"
        "    requirement_files: [../outside.txt]\n"
        "    services: []\n"
        "    browsers: []\n",
        encoding="utf-8",
    )

    with pytest.raises(ProfileError, match="越出源码目录"):
        DependencyProfileCatalog(manifest, tmp_path)
