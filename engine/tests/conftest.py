"""Shared pytest fixtures for the axrange test suite."""
from __future__ import annotations

import sys
from pathlib import Path

# Make the repo root importable without `pip install -e .`.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


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
