"""`apply_vyos_sets` is all-or-nothing and a retry over a half-applied config succeeds.

dev45 2026-11-21, three-tier-dc-manual: configure-routing's attempt 1 committed
every line before the rejected `passive enable`, and attempts 2-3 then failed on
that same line under a page of "already exists". The generated vbash script is
run here under local bash against a fake VyOS that behaves like vyos@2026.06 as
probed live: `set` exits 0 even on "Set failed", an existing path prints
"already exists" (exit 0), and nothing reaches the running config until `commit`.
"""
from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

import axrange.adapter_proxmox.ssh as ssh_mod
from axrange.adapter_proxmox.config import AdapterConfig
from axrange.adapter_proxmox.fabric_render import render_routing
from axrange.planner.executor import AdapterError

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

# Fake script-template: running/staged config as line files under $FAKE_VYOS.
_FAKE_VYOS = r"""
configure() { cp "$FAKE_VYOS/running" "$FAKE_VYOS/staged"; }
set() {
  local p="$*"
  if [[ "$p" == *"passive enable"* ]]; then
    echo "  Configuration path: ${p% enable} [enable] is not valid"; echo "  Set failed"; return 0
  fi
  if grep -qxF -- "$p" "$FAKE_VYOS/staged"; then
    echo "  Configuration path: [$p] already exists"; return 0
  fi
  echo "$p" >>"$FAKE_VYOS/staged"
}
commit() {
  if cmp -s "$FAKE_VYOS/staged" "$FAKE_VYOS/running"; then echo "No configuration changes to commit"; return 1; fi
  cp "$FAKE_VYOS/staged" "$FAKE_VYOS/running"
}
discard() { cp "$FAKE_VYOS/running" "$FAKE_VYOS/staged"; echo "  Changes have been discarded"; }
save() { echo "Saving configuration... Done"; }
"""


@pytest.fixture
def fake_router(tmp_path, monkeypatch):
    (tmp_path / "running").write_text("")

    def fake_guest(cfg, host, user, cmd, *, stdin_text=None, timeout_s=60):
        script = stdin_text.replace(
            "source /opt/vyatta/etc/functions/script-template\n", _FAKE_VYOS, 1)
        p = subprocess.run(["bash", "-s"], input=script, capture_output=True, text=True,
                           env={"FAKE_VYOS": str(tmp_path), "PATH": "/usr/bin:/bin"})
        return p.returncode, p.stdout + p.stderr

    monkeypatch.setattr(ssh_mod, "run_ssh_guest", fake_guest)
    return tmp_path / "running"


def _core1() -> dict:
    return {
        "router_os": "vyos", "region": "dc", "igp": "ospf", "role": None,
        "loopback": "10.255.0.1/32", "router_id": "10.255.0.1",
        "interfaces": [
            {"eth": "eth1", "net": "core1-agg1", "p2p": True, "ospf_area": 0, "vni": None},
            {"eth": "eth2", "net": "core1-agg2", "p2p": True, "ospf_area": 0, "vni": None},
        ],
        "redistribute": [], "bfd": False,
    }


def _apply(sets):
    return ssh_mod.apply_vyos_sets(AdapterConfig(), "10.80.72.147", "vyos", sets,
                                   op_label="configure-routing: node=core-1")


def _paths(sets: list[str]) -> list[str]:
    """`set a 'b'` -> `a b`, the form the fake router stores (bash's "$*")."""
    return sorted(" ".join(shlex.split(ln)[1:]) for ln in sets)


def _lines(running: Path) -> list[str]:
    return [ln for ln in running.read_text().splitlines() if ln]


def test_rejected_line_discards_the_whole_attempt(fake_router):
    good = render_routing(_core1())
    bad = [ln.replace("dum0 passive", "dum0 passive enable") for ln in good]
    with pytest.raises(AdapterError) as ei:
        _apply(bad)
    assert ei.value.error_class == "router-config-failed"
    assert "discarded, nothing committed" in str(ei.value)
    assert "passive [enable] is not valid" in str(ei.value)  # the real error, not "already exists"
    assert _lines(fake_router) == []  # nothing half-applied


def test_retry_over_a_partially_applied_config_succeeds(fake_router):
    """The state the old envelope left behind: every line before the rejected one
    committed. The retry (fixed render) reports "already exists" for those and
    commits the rest."""
    sets = render_routing(_core1())
    cut = next(i for i, ln in enumerate(sets) if "dum0 passive" in ln)
    fake_router.write_text("".join(f"{ln}\n" for ln in _paths(sets[:cut])))
    out = _apply(sets)
    assert "already exists" in out
    assert ssh_mod.VYOS_DISCARDED_MARKER not in out
    assert sorted(_lines(fake_router)) == _paths(sets)


def test_rerun_of_an_applied_config_is_a_no_op(fake_router):
    sets = render_routing(_core1())
    _apply(sets)
    out = _apply(sets)  # resume after a fully committed attempt
    assert "No configuration changes to commit" in out
    assert sorted(_lines(fake_router)) == _paths(sets)


def test_failed_commit_is_not_saved(fake_router, monkeypatch):
    def fake_guest(cfg, host, user, cmd, *, stdin_text=None, timeout_s=60):
        script = stdin_text.replace(
            "source /opt/vyatta/etc/functions/script-template\n",
            _FAKE_VYOS + 'commit() { echo "Commit failed"; }\n', 1)
        p = subprocess.run(["bash", "-s"], input=script, capture_output=True, text=True,
                           env={"FAKE_VYOS": str(fake_router.parent), "PATH": "/usr/bin:/bin"})
        return p.returncode, p.stdout + p.stderr

    monkeypatch.setattr(ssh_mod, "run_ssh_guest", fake_guest)
    with pytest.raises(AdapterError) as ei:
        _apply(render_routing(_core1()))
    assert "Commit failed" in str(ei.value)
    assert "commit failed, not saved" in str(ei.value)
