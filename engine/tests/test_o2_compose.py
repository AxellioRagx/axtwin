"""Static validation for the o2/ docker-compose stack (A3 Stage B — Caldera + Wazuh
manager servers, `docs/WORK_ORDER_attack_defense.md` §3).

No docker/`docker compose` needed: parses `o2/compose/docker-compose.yml` with
PyYAML and asserts structure the way `docker compose config -q` would validate
schema-wise (image/build/ports/healthchecks present, referenced env vars
documented). Docker isn't installed on the pipeline box (same posture the
existing Grafana/Loki/Prometheus stack's own README "Validation" table already
documents); a real `docker compose config -q` / `docker compose build` run is
part of the gated live-verify, not this suite.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_PATH = REPO_ROOT / "o2" / "compose" / "docker-compose.yml"
CONF_EXAMPLE_PATH = REPO_ROOT / "o2" / "observability.conf.example"
CALDERA_DOCKERFILE = REPO_ROOT / "o2" / "caldera" / "Dockerfile"

VAR_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)")


@pytest.fixture(scope="module")
def compose() -> dict:
    with COMPOSE_PATH.open() as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def conf_example_keys() -> set[str]:
    keys: set[str] = set()
    for line in CONF_EXAMPLE_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            keys.add(line.split("=", 1)[0].strip())
    return keys


def test_compose_file_is_valid_yaml_with_services(compose):
    assert isinstance(compose, dict)
    assert "services" in compose
    assert isinstance(compose["services"], dict)


def test_pre_existing_services_still_present(compose):
    # A3 Stage B only ADDS services; it must not disturb the existing stack.
    for name in ("prometheus", "loki", "grafana"):
        assert name in compose["services"], f"{name} service missing"


@pytest.mark.parametrize("service_name", ["caldera", "wazuh-manager"])
def test_new_server_service_exists(compose, service_name):
    assert service_name in compose["services"], f"{service_name} service missing"


def test_caldera_service_shape(compose):
    svc = compose["services"]["caldera"]
    # No published Docker Hub image -- built locally, per the Dockerfile.
    assert "build" in svc, "caldera must build locally (no upstream Docker Hub image)"
    assert svc["build"]["context"] == "../caldera"
    assert "image" in svc  # tags the local build so `ps`/`up` reuse it
    assert "container_name" in svc
    assert svc.get("restart") == "unless-stopped"

    ports = svc.get("ports") or []
    assert any(":8888" in str(p) for p in ports), "Caldera UI/HTTP-contact port missing"

    assert "healthcheck" in svc
    hc = svc["healthcheck"]
    assert hc.get("test"), "caldera healthcheck must have a test command"
    assert "interval" in hc and "retries" in hc
    test_str = " ".join(hc["test"])
    # python:3.11-slim + git/build-essential (../caldera/Dockerfile) ships no
    # wget/curl -- a wget-based healthcheck returns 127 and the container is
    # permanently "unhealthy" even while serving. Guard the regression.
    assert "wget" not in test_str and "curl" not in test_str, (
        "caldera healthcheck must not depend on wget/curl (not installed in the "
        "build image, see ../caldera/Dockerfile) -- use python3, which is"
    )
    assert "python3" in test_str

    volumes = svc.get("volumes") or []
    assert any("caldera-data" in v for v in volumes), "caldera must persist /usr/src/app/data"
    # conf/local.yml (encryption_key/crypt_salt + API keys) is generated on first
    # boot; without persisting it too, a down+up regenerates the key and the
    # persisted data/object_store (run history) becomes undecryptable.
    assert any("caldera-conf" in v for v in volumes), "caldera must persist /usr/src/app/conf"


def test_wazuh_manager_service_shape(compose):
    svc = compose["services"]["wazuh-manager"]
    assert svc.get("image", "").startswith(
        "${WAZUH_MANAGER_IMAGE"
    ), "wazuh-manager image must be a pinned, overridable ${..._IMAGE} var"
    assert "container_name" in svc
    assert svc.get("restart") == "unless-stopped"

    ports = svc.get("ports") or []
    port_str = " ".join(str(p) for p in ports)
    # Wazuh 4.x manager + agent default the event channel to TCP, not UDP -- a
    # /udp-only publish leaves the real (TCP) event channel unreachable: agents
    # enroll fine on 1515/tcp, then silently never ship an event. Guard the
    # protocol, not just the port number.
    assert "1514/tcp" in port_str, "agent event port must be published as 1514/tcp"
    assert "1514/udp" not in port_str, (
        "1514/udp alone doesn't cover the Wazuh 4.x TCP-default event channel"
    )
    assert "1515" in port_str, "agent enrollment port (1515) missing"

    assert "healthcheck" in svc
    hc = svc["healthcheck"]
    assert hc.get("test"), "wazuh-manager healthcheck must have a test command"
    assert "interval" in hc and "retries" in hc
    # wazuh-remoted is the daemon agents actually talk to (1514/1515); a
    # wazuh-modulesd-only check can report healthy while remoted is down.
    assert "wazuh-remoted" in " ".join(hc["test"])

    volumes = svc.get("volumes") or []
    assert any("wazuh-manager-etc" in v for v in volumes), (
        "wazuh-manager must persist /var/ossec/etc (agent keys/config survive a restart)"
    )
    assert any("wazuh-manager-logs" in v for v in volumes)


def test_every_service_has_a_healthcheck(compose):
    # Project convention (CLAUDE.md): every long-running component is
    # health-checkable. Applies uniformly, not just to the new services.
    for name, svc in compose["services"].items():
        assert "healthcheck" in svc, f"{name} is missing a healthcheck"


def test_no_image_pinned_to_latest(compose):
    for name, svc in compose["services"].items():
        image = svc.get("image")
        if image is None:
            continue
        assert ":latest" not in image, f"{name} must not pin :latest"


def test_new_volumes_declared_at_top_level(compose):
    top_volumes = set((compose.get("volumes") or {}).keys())
    for expected in (
        "caldera-data",
        "caldera-conf",
        "wazuh-manager-etc",
        "wazuh-manager-logs",
        "wazuh-manager-queue",
    ):
        assert expected in top_volumes, f"top-level volumes: missing {expected}"


def test_every_referenced_env_var_is_documented(compose, conf_example_keys):
    """Every ${VAR} the compose file interpolates must have a matching
    KEY=value in observability.conf.example (the single source of truth for
    tunables — o2/README.md "What's here"), so `--env-file` bring-up never
    silently falls back to a compose-file default in production."""
    raw = COMPOSE_PATH.read_text()
    referenced = set(VAR_RE.findall(raw))
    missing = sorted(v for v in referenced if v not in conf_example_keys)
    assert not missing, f"vars referenced in compose but undocumented: {missing}"


def test_observability_conf_example_documents_caldera_and_wazuh(conf_example_keys):
    for key in (
        "CALDERA_UI_PORT",
        "CALDERA_TCP_PORT",
        "CALDERA_UDP_PORT",
        "CALDERA_VERSION",
        "WAZUH_AGENT_PORT",
        "WAZUH_ENROLL_PORT",
        "WAZUH_API_PORT",
        "WAZUH_MANAGER_IMAGE",
    ):
        assert key in conf_example_keys, f"observability.conf.example missing {key}"


def test_caldera_dockerfile_exists_and_is_pinned():
    assert CALDERA_DOCKERFILE.exists(), "o2/caldera/Dockerfile must exist"
    text = CALDERA_DOCKERFILE.read_text()
    assert "ARG CALDERA_VERSION=" in text
    assert "git clone" in text and "--recursive" in text, (
        "Caldera's UI/plugin submodules require a --recursive clone"
    )
    assert "--insecure" in text, (
        "must run Caldera's HTTP contact channel to match render_sandcat_agent's "
        "http:// origin (axrange/config_exec/render.py)"
    )
    assert ":latest" not in text.split("ARG CALDERA_VERSION=")[0]


def test_render_sandcat_agent_bare_host_vs_full_url_contract():
    """Guards the Stage-A<->Stage-B address contract o2/README.md's "Pointing
    agents at the servers" section depends on: render_sandcat_agent appends
    Caldera's default :8888 to a BARE HOST server:, passes a full URL through
    unchanged, and does NOT sanitize a bare "host:port" (no scheme) -- that
    form doubles the port, which is exactly the malformed-URL bug the README
    now explicitly warns operators away from. Read-only import against
    axrange/config_exec/render.py (not edited here -- a concurrent thread
    owns that file); if this test starts failing, render_sandcat_agent's
    origin logic changed and o2/README.md + observability.conf.example's
    guidance need updating in lockstep.
    """
    from axrange.config_exec.render import render_sandcat_agent

    bare_host = render_sandcat_agent({"id": "a"}, server="h")
    assert "SERVER=http://h:8888" in bare_host

    full_url = render_sandcat_agent({"id": "a"}, server="http://h:9999")
    assert "SERVER=http://h:9999" in full_url
    assert "9999:9999" not in full_url

    malformed = render_sandcat_agent({"id": "a"}, server="h:8888")
    assert "SERVER=http://h:8888:8888" in malformed, (
        "the malformed bare host:port form no longer double-ports -- if "
        "render_sandcat_agent was fixed to sanitize this, relax o2/README.md's "
        "warning (and observability.conf.example's) accordingly"
    )


def test_caldera_default_version_matches_compose_default(compose):
    """The Dockerfile's ARG default and the compose file's ${CALDERA_VERSION:-...}
    fallback should agree, so an operator who never touches observability.conf
    still gets a self-consistent build+tag."""
    dockerfile_text = CALDERA_DOCKERFILE.read_text()
    m = re.search(r"ARG CALDERA_VERSION=(\S+)", dockerfile_text)
    assert m, "Dockerfile ARG CALDERA_VERSION default not found"
    dockerfile_default = m.group(1)

    caldera_svc = compose["services"]["caldera"]
    compose_arg = caldera_svc["build"]["args"]["CALDERA_VERSION"]
    assert dockerfile_default in compose_arg, (
        f"compose CALDERA_VERSION fallback {compose_arg!r} does not embed the "
        f"Dockerfile default {dockerfile_default!r}"
    )
