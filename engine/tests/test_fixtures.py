"""End-to-end fixture tests:
- the corrected ransomware-hospital scenario validates clean (modulo
  warnings/info for things like REF-150 if behavior refs are unpinned, etc.).
- the spec-verbatim ransomware-hospital scenario fails (dangling host).
"""
from __future__ import annotations

from pathlib import Path

from axrange.validator.engine import Disposition, validate_full


FIXTURES = Path(__file__).parent / "fixtures"


def test_minimal_fixture_passes(manifests) -> None:
    result = validate_full(
        str(FIXTURES / "ir" / "valid" / "minimal.yaml"),
        manifests=manifests,
    )
    assert result.disposition != Disposition.FAIL, [
        (f.rule_id, f.message) for f in result.errors
    ]


def test_ransomware_hospital_corrected_passes(manifests) -> None:
    result = validate_full(
        str(FIXTURES / "ir" / "valid" / "ransomware-hospital-corrected.yaml"),
        manifests=manifests,
    )
    # Errors blocking deploy. Warnings/infos are acceptable.
    assert result.disposition != Disposition.FAIL, [
        (f.rule_id, f.message) for f in result.errors
    ]


def test_ransomware_hospital_as_specced_fails_REF_070(manifests) -> None:
    """The IR_SCHEMA §10 example has `host: h-dmz-ids` not declared in
    layers.infra.nodes — REF-070 should fire."""
    result = validate_full(
        str(FIXTURES / "ir" / "invalid" / "ransomware-hospital-as-specced.yaml"),
        manifests=manifests,
    )
    rule_ids = {f.rule_id for f in result.errors}
    assert "REF-070" in rule_ids
    assert result.disposition == Disposition.FAIL
