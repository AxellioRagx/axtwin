"""Twins with no native switch plan byte-identically to the engine before
native switches (docs/design/native-switches.md §2.3, §11)."""
from __future__ import annotations

import json

import pytest

from tests.plan_golden_support import GOLDEN_FILE, GOLDEN_TWINS, snapshot_digest


@pytest.mark.parametrize("twin", GOLDEN_TWINS)
def test_plan_matches_golden(twin: str) -> None:
    expected = json.loads(GOLDEN_FILE.read_text(encoding="utf-8"))[twin]
    assert snapshot_digest(twin) == expected, (
        f"{twin}'s plan changed; see tests/plan_golden_support.py for how to diff it"
    )
