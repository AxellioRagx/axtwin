"""Shared pytest fixtures for the axrange test suite."""
from __future__ import annotations

import sys
from pathlib import Path

# Make the package importable without `pip install -e .` (src layout).
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


import pytest

from axrange.manifest.loader import ManifestRegistry, load_registry


@pytest.fixture(autouse=True)
def _discovery_db_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`create_app` opens the discovery DB (`settings.discovery_db`) for the run
    table at startup. Its default is `~/.axrange/discovery.db`; a test that
    builds settings without naming one must never touch the operator's file."""
    monkeypatch.setenv("AXRANGE_DISCOVERY_DB", str(tmp_path / "axrange-home" / "discovery.db"))


@pytest.fixture(autouse=True)
def _lab_registry_clean():
    """The lab-sensor registry (`axrange.platform.lab`) is process-global,
    filled by `create_app`; never let one test's labs reach the next."""
    from axrange.api.labhealth import clear_cache
    from axrange.platform.lab import clear_registry

    clear_registry()
    clear_cache()
    yield
    clear_registry()
    clear_cache()


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def manifests(fixtures_dir: Path) -> ManifestRegistry:
    return load_registry(root=fixtures_dir)


@pytest.fixture
def empty_manifests() -> ManifestRegistry:
    return ManifestRegistry()
