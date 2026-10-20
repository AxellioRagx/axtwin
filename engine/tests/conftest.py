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


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def manifests(fixtures_dir: Path) -> ManifestRegistry:
    return load_registry(root=fixtures_dir)


@pytest.fixture
def empty_manifests() -> ManifestRegistry:
    return ManifestRegistry()
