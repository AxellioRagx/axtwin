"""`GET /api/twins/{id}/tree` -- the bundle as segment -> node group -> intent."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from axrange.api.app import create_app
from axrange.api.settings import ApiSettings
from axrange.api.twin_tree import load_twin_tree

FIXTURES = Path(__file__).parent / "fixtures" / "cast-bundles"


def _settings(imprints_dir: Path, tmp_path: Path) -> ApiSettings:
    return ApiSettings(
        db_path=str(tmp_path / "state.db"), adapter_config="/nonexistent.conf",
        imprints_dir=str(imprints_dir),
    )


def _bundle(root: Path, *, tfc: str | None = None, tap: str | None = None, personas: dict[str, str] | None = None) -> Path:
    """A legacy-layout cast bundle: one router over three segments, a fanned-out
    group, a single host that declares listeners, a host with no assignment."""
    d = root / "tree"
    d.mkdir()
    (d / "tree.yaml").write_text(
        "twin_config:\n"
        "  id: tree\n"
        "  segments:\n"
        "    - { name: lan, cidr: 10.5.0.0/24, observation: { windows_seen: 2, windows_total: 2, occupancy: 1.0 } }\n"
        "    - { name: dmz, cidr: 10.5.1.0/24 }\n"
        "    - { name: quiet, cidr: 10.5.2.0/24 }\n"
        "  nodes:\n"
        "    - { name: gw, kind: router, role: gw, image: debian-12-cloud@v1, requires: [linux],\n"
        "        nets: [{ segment: lan }, { segment: dmz }, { segment: quiet }] }\n"
        "    - { name: ws, role: corp-workstation, image: win11@v1, requires: [windows], net: lan, count: 3,\n"
        "        listen: [{ port: 3389, proto: tcp }],\n"
        "        observation: { windows_seen: 2, windows_total: 2, occupancy: 1.0, role_confidence: 0.6 } }\n"
        "    - { name: web, role: web-tier, image: debian-12-cloud@v1, requires: [linux], net: dmz,\n"
        "        install: [{ pkg: nginx }, { script: scripts/web.sh }],\n"
        "        listen: [{ port: 80, proto: tcp }, { port: 443, proto: tcp }] }\n"
        "    - { name: lonely, image: debian-12-cloud@v1, requires: [linux], net: quiet }\n"
    )
    (d / "tree.topology.json").write_text(json.dumps({"imprint_id": "tree", "as_of": "2026-10-06T19:00:00Z", "windows": 2}))
    if tfc is not None:
        (d / "tfc-cast.yaml").write_text(tfc)
    if tap is not None:
        (d / "tap-cast.yaml").write_text(tap)
    if personas:
        (d / "personas").mkdir()
        for pid, body in personas.items():
            (d / "personas" / f"{pid}.yaml").write_text(body)
    return d


_WS_PERSONA = (
    "persona:\n  id: cast-ws\n  os_family: windows\n  drive: scripted\n  emits:\n"
    "  - { proto: https, ports: [443], peer_role: web-tier, transport: tcp, cadence: cyclic:300s, volume: low }\n"
    "  - { proto: dns, ports: [53], peer_role: dns-resolver, transport: udp, optional: true }\n"
)
_TFC = (
    "traffic-profile:\n  target: tree\n  assignments:\n"
    "  - { role: corp-workstation, persona: cast-ws, seed: 7 }\n"
    "  - { node: ws01, persona: cast-ws-chatter, schedule: 24x7 }\n"
    "  - { node: ws02, persona: cast-ws-chatter, schedule: 24x7 }\n"
    "  - { node: web, persona: cast-web, schedule: business-hours }\n"
    "  - { node: ghost, persona: cast-ws }\n"
    "  - { role: nobody, persona: cast-ws }\n"
)
_TAP = (
    "tap-plan:\n  target: tree\n  bindings:\n"
    "  - { selector: { scope: segment, target: lan }, mode: exclusive, sensors: [sensor-a] }\n"
    "  - { selector: { scope: segment, target: dmz }, mode: fanout, sensors: [sensor-a, sensor-b] }\n"
)


def test_the_tree_is_segments_of_node_groups_with_their_personas(tmp_path):
    _bundle(tmp_path, tfc=_TFC, tap=_TAP, personas={"cast-ws": _WS_PERSONA})
    t = load_twin_tree("tree", fixtures_dir=tmp_path)

    assert t.id == "tree" and t.hosts == 5
    assert t.traffic_profile == "tfc-cast.yaml" and t.tap_plan == "tap-cast.yaml"
    # Largest segment first; routers once, never as a segment member.
    assert [(s.name, s.hosts) for s in t.segments] == [("lan", 3), ("dmz", 1), ("quiet", 1)]
    assert [r.name for r in t.routers] == ["gw"]
    assert t.routers[0].segments == ["lan", "dmz", "quiet"]
    assert all(g.kind == "host" for s in t.segments for g in s.nodes)

    lan, dmz, quiet = t.segments
    assert [(r.role, r.hosts) for r in lan.roles] == [("corp-workstation", 3)]
    assert lan.observation is not None and lan.observation.occupancy == 1.0
    assert [(x.mode, x.sensors) for x in lan.taps] == [("exclusive", ["sensor-a"])]
    assert [(x.mode, x.sensors) for x in dmz.taps] == [("fanout", ["sensor-a", "sensor-b"])]
    assert quiet.taps == []

    (ws,) = lan.nodes
    assert (ws.name, ws.role, ws.count, ws.instances) == ("ws", "corp-workstation", 3, ["ws01", "ws02", "ws03"])
    assert [(l.port, l.proto) for l in ws.listen] == [(3389, "tcp")]
    assert ws.observation is not None and ws.observation.role_confidence == 0.6
    # A role assignment reaches every member; a node assignment counts the
    # instances it named; the bundle's own persona document supplies emits.
    assert [(p.id, p.bound_by, p.instances, p.missing) for p in ws.personas] == [
        ("cast-ws", "role", 3, False), ("cast-ws-chatter", "node", 2, True),
    ]
    role_bound = ws.personas[0]
    assert role_bound.seed == 7 and role_bound.os_family == "windows" and role_bound.drive == "scripted"
    assert [(e.proto, e.ports, e.peer_role, e.transport, e.optional) for e in role_bound.emits] == [
        ("https", [443], "web-tier", "tcp", False), ("dns", [53], "dns-resolver", "udp", True),
    ]
    assert ws.personas[1].schedule == "24x7" and ws.personas[1].emits == []

    (web,) = dmz.nodes
    assert web.install == ["pkg nginx", "script scripts/web.sh"]
    assert [(l.port, l.proto) for l in web.listen] == [(80, "tcp"), (443, "tcp")]
    assert [(p.id, p.bound_by, p.instances, p.schedule) for p in web.personas] == [("cast-web", "node", 1, "business-hours")]

    (lonely,) = quiet.nodes
    assert lonely.role is None and lonely.personas == [] and lonely.listen == [] and lonely.observation is None
    assert [(r.role, r.hosts) for r in quiet.roles] == [(None, 1)]

    # Assignments nothing in the config answers to are SAID, never dropped silently.
    assert sorted(t.warnings) == [
        "traffic profile assigns 'cast-ws' to node 'ghost', which the twin config does not declare",
        "traffic profile assigns 'cast-ws' to role 'nobody', which no node carries",
    ]


def test_a_bundle_without_profile_or_tap_plan_is_the_config_alone(tmp_path):
    _bundle(tmp_path)
    t = load_twin_tree("tree", fixtures_dir=tmp_path)
    assert t.traffic_profile is None and t.tap_plan is None and t.warnings == []
    assert all(g.personas == [] for s in t.segments for g in s.nodes)
    assert all(s.taps == [] for s in t.segments)


def test_a_broken_profile_is_a_warning_not_a_refusal(tmp_path):
    _bundle(tmp_path, tfc="traffic-profile: [not, a, mapping]\n", tap="tap-plan: { target: tree }\n")
    t = load_twin_tree("tree", fixtures_dir=tmp_path)
    assert t.hosts == 5
    assert t.traffic_profile == "tfc-cast.yaml" and t.tap_plan == "tap-cast.yaml"
    assert any(w.startswith("twin 'tree': traffic profile tfc-cast.yaml not read") for w in t.warnings), t.warnings
    assert any(w.startswith("twin 'tree': tap plan tap-cast.yaml not read") for w in t.warnings), t.warnings


def test_a_role_assignment_reaches_every_group_carrying_the_role(tmp_path):
    d = _bundle(tmp_path, tfc="traffic-profile:\n  target: tree\n  assignments:\n  - { role: web-tier, persona: cast-web }\n")
    (d / "tree.yaml").write_text((d / "tree.yaml").read_text().replace("name: lonely, image", "name: lonely, role: web-tier, image"))
    t = load_twin_tree("tree", fixtures_dir=tmp_path)
    given = {g.name: [(p.id, p.bound_by, p.instances) for p in g.personas] for s in t.segments for g in s.nodes}
    assert given == {"ws": [], "web": [("cast-web", "role", 1)], "lonely": [("cast-web", "role", 1)]}


def test_the_route_serves_the_tree_and_refuses_what_the_summary_refuses(tmp_path):
    c = TestClient(create_app(_settings(FIXTURES, tmp_path)))
    r = c.get("/api/twins/mini-cast-prov/tree")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["id"] == "mini-cast-prov" and j["hosts"] == 5
    assert [s["name"] for s in j["segments"]] == ["seg-corp", "internet-sink", "seg-data", "seg-thin"]
    assert [r["name"] for r in j["routers"]] == ["gw"]
    web = j["segments"][0]["nodes"][0]
    assert web["name"] == "web-tier" and web["count"] == 2 and web["instances"] == ["web-tier01", "web-tier02"]
    assert web["install"] == ["pkg nginx", "script scripts/responder-web.sh"]
    assert j["traffic_profile"] is None and j["tap_plan"] is None
    assert c.get("/api/twins/no-such-twin/tree").status_code == 404
    assert c.get("/api/twins/mini-cast-prov/tree").json()["segments"][3]["observation"] == {
        "windows_seen": 2, "windows_total": 5, "occupancy": 0.4, "role_confidence": None,
    }


@pytest.mark.skipif(not (Path(__file__).parents[2] / "catalog" / "twins" / "corpapcn-twin" / "twin.yaml").is_file(), reason="no corpapcn-twin in the catalog")
def test_a_manifest_folder_reads_its_declared_tier_files(tmp_path):
    t = load_twin_tree("corpapcn-twin", fixtures_dir=Path(__file__).parents[2] / "catalog" / "twins")
    assert t.traffic_profile == "tfc/cast.yaml" and t.tap_plan == "tap/cast.yaml"
    assert t.hosts == 137 and len(t.segments) == 27
    assert any(g.personas for s in t.segments for g in s.nodes)


def test_the_tree_and_the_topology_carry_what_the_cast_wrote_as_provenance(tmp_path):
    """R4-4 (2026-11-08): a cast writes `source` / `confidence` / `vantage` on every node and
    segment, but the tree route had no field for them and the topology route dropped a
    segment's, so a check through either read every cast as provenance-free."""
    root = tmp_path / "bundles"
    root.mkdir()
    d = root / "prov"
    d.mkdir()
    (d / "prov.yaml").write_text(
        "twin_config:\n"
        "  id: prov\n"
        "  segments:\n"
        "    - { name: lan, cidr: 10.5.0.0/24, source: discovered, confidence: 0.75, vantage: hq/rdr1:12 }\n"
        "    - { name: sink, cidr: 10.200.254.0/24, source: unconfirmed }\n"
        "  nodes:\n"
        "    - { name: gw, kind: router, image: debian-12-cloud@v1, requires: [linux],\n"
        "        nets: [{ segment: lan }, { segment: sink }], source: unconfirmed, vantage: hq/rdr1:12 }\n"
        "    - { name: ws, role: corp-workstation, image: debian-12-cloud@v1, requires: [linux], net: lan, count: 2,\n"
        "        source: discovered, confidence: 0.5, vantage: hq/rdr1:12 }\n"
    )
    (d / "prov.topology.json").write_text(json.dumps({"imprint_id": "prov", "as_of": "2026-10-06T19:00:00Z", "windows": 2}))
    tree = load_twin_tree("prov", fixtures_dir=root)
    segs = {s.name: (s.source, s.confidence, s.vantage) for s in tree.segments}
    assert segs == {"lan": ("discovered", 0.75, "hq/rdr1:12"), "sink": ("unconfirmed", None, None)}
    ws = next(n for s in tree.segments for n in s.nodes if n.name == "ws")
    assert (ws.source, ws.confidence, ws.vantage) == ("discovered", 0.5, "hq/rdr1:12")
    assert [(r.name, r.source, r.confidence) for r in tree.routers] == [("gw", "unconfirmed", None)]

    c = TestClient(create_app(_settings(root, tmp_path)))
    body = c.get("/api/twins/prov/tree").json()
    assert all(s["source"] for s in body["segments"]) and all(n["source"] for s in body["segments"] for n in s["nodes"])
    topo = c.get("/api/scenarios/prov/topology")
    assert topo.status_code == 200, topo.text
    nets = {n["id"]: (n["source"], n["confidence"], n["vantage"]) for n in topo.json()["nodes"] if n["type"] == "network"}
    assert nets == {"lan": ("discovered", 0.75, "hq/rdr1:12"), "sink": ("unconfirmed", None, None)}


def test_the_twin_config_detail_carries_provenance_on_its_segment_and_node_rows(tmp_path):
    """R5-1 (2026-11-08): `GET /api/twin-configs/{id}` summarised a cast's 89 nodes and 44
    segments without the `source` / `confidence` / `vantage` the twin config carries, so a
    check through it read every cast as provenance-free. Conduits accept the fields too."""
    root = tmp_path / "bundles"
    root.mkdir()
    d = root / "prov"
    d.mkdir()
    (d / "prov.yaml").write_text(
        "twin_config:\n"
        "  id: prov\n"
        "  segments:\n"
        "    - { name: lan, cidr: 10.5.0.0/24, source: discovered, confidence: 0.75, vantage: hq/rdr1:12 }\n"
        "    - { name: dmz, cidr: 10.6.0.0/24 }\n"
        "  nodes:\n"
        "    - { name: gw, kind: router, image: debian-12-cloud@v1, requires: [linux],\n"
        "        nets: [{ segment: lan }, { segment: dmz }], source: unconfirmed, vantage: hq/rdr1:12 }\n"
        "    - { name: ws, role: corp-workstation, image: debian-12-cloud@v1, requires: [linux], net: lan,\n"
        "        source: asserted }\n"
        "  conduits:\n"
        "    - { from: lan, to: dmz, source: discovered, vantage: hq/rdr1:12 }\n"
    )
    settings = ApiSettings(db_path=str(tmp_path / "state.db"), adapter_config="/nonexistent.conf",
                           imprints_dir=str(root), twin_configs_dir=str(root))
    c = TestClient(create_app(settings))
    r = c.get("/api/twin-configs/prov")
    assert r.status_code == 200, r.text
    j = r.json()
    segs = {s["name"]: (s["source"], s["confidence"], s["vantage"]) for s in j["segments"]}
    assert segs == {"lan": ("discovered", 0.75, "hq/rdr1:12"), "dmz": (None, None, None)}
    nodes = {n["name"]: (n["source"], n["confidence"], n["vantage"]) for n in j["nodes"]}
    assert nodes == {"gw": ("unconfirmed", None, "hq/rdr1:12"), "ws": ("asserted", None, None)}
