"""The layout document's optional `annotations` list (`axr-layout/1`,
`axrange.api.console_files`): the canvas's text and shapes as positioned
SVG snippets, bounded, with every snippet refused by name when it carries
what must never be served back to a browser. Old documents without the key
stay valid and round-trip unchanged; the etag discipline is untouched.
Reuses `tests/test_api_console.py`'s writable-bundle fixture.
"""
from __future__ import annotations

import json

import pytest

from axrange.api.console_files import validate_layout
from tests.test_api_console import _LAYOUT, _client, _copy_bundle, _settings

ID = "mini-cast-prov"
URL = f"/api/twins/{ID}/layout"

_ANN = [
    {"id": "note-1", "kind": "text", "x": 10, "y": 20, "svg": '<text x="0" y="0">DMZ</text>'},
    {"id": "box.a", "kind": "rect", "x": 0, "y": 0, "w": 120, "h": 40,
     "svg": '<rect width="120" height="40" fill="none" stroke="#999"/>', "locked": True},
    {"id": "oval:1", "kind": "ellipse", "x": 5, "y": 5, "w": 30, "h": 20,
     "svg": '<ellipse rx="15" ry="10"/>', "hidden": True},
    {"id": "l1", "kind": "line", "x": 0, "y": 0, "w": 50, "h": 0, "svg": '<line x2="50" y2="0"/>'},
]


def _c(tmp_path):
    return _client(_settings(_copy_bundle(tmp_path, ID), tmp_path))


def test_annotations_round_trip_canonical_and_through_the_file(tmp_path):
    c = _c(tmp_path)
    put = c.put(URL, json=dict(_LAYOUT, annotations=_ANN))
    assert put.status_code == 200, put.text
    j = put.json()
    assert [a["id"] for a in j["annotations"]] == ["note-1", "box.a", "oval:1", "l1"]
    assert j["annotations"][0] == {
        "id": "note-1", "kind": "text", "x": 10.0, "y": 20.0, "w": None, "h": None,
        "svg": '<text x="0" y="0">DMZ</text>', "locked": False, "hidden": False,
    }
    assert j["annotations"][1]["locked"] is True and j["annotations"][2]["hidden"] is True
    got = c.get(URL).json()
    assert got["annotations"] == j["annotations"] and got["etag"] == j["etag"]
    on_disk = json.loads((tmp_path / "imprints" / ID / "layout.json").read_text())
    assert on_disk["schema"] == "axr-layout/1"
    assert [a["id"] for a in on_disk["annotations"]] == ["note-1", "box.a", "oval:1", "l1"]
    assert "w" not in on_disk["annotations"][0]  # absent sizes stay absent
    # The etag discipline is unchanged: a stale etag is 412, the current one goes.
    stale = c.put(URL, json=dict(_LAYOUT, annotations=_ANN[:1], etag="0" * 64))
    assert stale.status_code == 412
    ok = c.put(URL, json=dict(_LAYOUT, annotations=_ANN[:1], etag=j["etag"]))
    assert ok.status_code == 200 and [a["id"] for a in ok.json()["annotations"]] == ["note-1"]
    # An empty list is "no annotations" -- the same state as no key, so the
    # file loses the key rather than carrying `[]`.
    r = c.put(URL, json=dict(_LAYOUT, annotations=[], etag=ok.json()["etag"]))
    assert r.status_code == 200 and r.json()["annotations"] == []
    assert "annotations" not in json.loads((tmp_path / "imprints" / ID / "layout.json").read_text())


def test_a_document_without_the_key_still_validates_and_is_not_rewritten(tmp_path):
    c = _c(tmp_path)
    put = c.put(URL, json=_LAYOUT)
    assert put.status_code == 200 and put.json()["annotations"] == []
    on_disk = (tmp_path / "imprints" / ID / "layout.json").read_text()
    assert "annotations" not in on_disk  # absent stays absent (as `plane` does)
    assert validate_layout(_LAYOUT, ID, where="t") == {
        "schema": "axr-layout/1", "imprint": ID,
        "pins": {"site:10.80": {"x": 40.0, "y": 12.5}, "segment:10.80.31.0/24": {"x": 0.0, "y": 0.0}},
    }
    # Re-putting the GET payload verbatim is a no-op write, not a refusal.
    got = c.get(URL).json()
    r = c.put(URL, json=got)
    assert r.status_code == 200 and r.json()["etag"] == got["etag"]


@pytest.mark.parametrize("svg, what", [
    ('<text><script>alert(1)</script></text>', "<script>"),
    ('<SCRIPT src="x"/>', "<script>"),
    ('<rect onclick="x()"/>', "on*="),
    ('<rect ONLOAD = "x()"/>', "on*="),
    ('<foreignObject><div/></foreignObject>', "<foreignObject>"),
    ('<a href="http://x"><text>x</text></a>', "href"),
    ('<use xlink:href="#x"/>', "href"),
    ('<image href="data:x"/>', "href"),
])
def test_each_refused_svg_construct_is_named(tmp_path, svg, what):
    c = _c(tmp_path)
    r = c.put(URL, json=dict(_LAYOUT, annotations=[dict(_ANN[0], svg=svg)]))
    assert r.status_code == 400, r.text
    assert what in r.json()["error"] and "annotations[0]" in r.json()["error"]
    assert not (tmp_path / "imprints" / ID / "layout.json").exists()


@pytest.mark.parametrize("ann, expect", [
    (dict(_ANN[0], id="x" * 65), "id"),
    (dict(_ANN[0], id="bad id!"), "id"),
    (dict(_ANN[0], id=""), "id"),
    (dict(_ANN[0], kind="polygon"), "kind"),
    (dict(_ANN[0], x="10"), "`x`"),
    (dict(_ANN[0], y=True), "`y`"),
    (dict(_ANN[0], w=-1), "`w`"),
    (dict(_ANN[0], h="tall"), "`h`"),
    (dict(_ANN[0], svg=""), "`svg`"),
    (dict(_ANN[0], svg="<text>" + "x" * (8 * 1024) + "</text>"), "at most 8192"),
    (dict(_ANN[0], locked="yes"), "`locked`"),
    (dict(_ANN[0], hidden=1), "`hidden`"),
    (dict(_ANN[0], color="red"), "unknown field"),
    ("not-a-mapping", "must be a mapping"),
])
def test_each_shape_refusal_is_named(tmp_path, ann, expect):
    c = _c(tmp_path)
    r = c.put(URL, json=dict(_LAYOUT, annotations=[ann]))
    assert r.status_code == 400, r.text
    assert expect in r.json()["error"]


def test_annotation_bounds_duplicates_and_type(tmp_path):
    c = _c(tmp_path)
    r = c.put(URL, json=dict(_LAYOUT, annotations=[dict(_ANN[0], id=f"a{i}") for i in range(201)]))
    assert r.status_code == 400 and "at most 200" in r.json()["error"]
    r = c.put(URL, json=dict(_LAYOUT, annotations=[_ANN[0], dict(_ANN[1], id="note-1")]))
    assert r.status_code == 400 and "used twice" in r.json()["error"]
    r = c.put(URL, json=dict(_LAYOUT, annotations={"a": 1}))
    assert r.status_code == 400 and "must be a list" in r.json()["error"]
    # Exactly 200 is fine; an 8 KiB snippet is fine.
    big = "<text>" + "x" * (8 * 1024 - len("<text></text>")) + "</text>"
    r = c.put(URL, json=dict(_LAYOUT, annotations=[dict(_ANN[0], id=f"a{i}", svg=big) for i in range(200)]))
    assert r.status_code == 200, r.text
