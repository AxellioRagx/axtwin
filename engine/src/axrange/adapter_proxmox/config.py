"""AdapterConfig — connection params for the REST + SSH backends.

Per docs/ADAPTER.md §5:
  - REST uses a limited-scope Proxmox API token, not a root password.
  - SSH uses a dedicated `ovs-operator` key.
  - Credentials live on the orchestrator host (config file or env), never in
    the IR.

Resolution order: explicit dict > env vars > adapter.conf file > defaults.

`adapter.conf` is a simple INI/key=value file. Lines starting with `#` are
comments. Keys are env-var-style upper case to match the env precedence:

  PROXMOX_API_URL=https://10.0.0.85:8006/api2/json
  PROXMOX_API_TOKEN=PVEAPIToken=axr@pve!planner=<secret>
  PROXMOX_VERIFY_TLS=false
  OVS_SSH_HOST=10.0.0.85
  OVS_SSH_USER=ovs-operator
  OVS_SSH_KEY=~/.ssh/pxtwin1_ed25519
  DEFAULT_NODE=pxtwin1

Sensor-agnostic te (2026-07-13, docs/WORK_ORDER_sensor_agnostic_te.md; the
twin substrate then went fully tap-agnostic, docs/WORK_ORDER_tap_eligibility.md,
2026-07-28): this file carries NO `SENSOR_*` keys any more. A `te_` deploy no
longer wires taps to a sensor at all (no `install-ovs-mirror`/`attach-gre-tunnel`/
`verify-capture`) and records NO tap-related state of its own either (no more
`record-tap-intent`); the sensor's identity (transport IP + vmid) is discovered
from A1's standing O2 inventory by the A2 tap playout (`axrange tap deploy`) at
tap-bind time, not read from
this file.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# One error type for every platform's config loader (`axrange.platform.
# protocols.ConfigError`): raised for a malformed adapter.conf value that
# must fail LOUD rather than silently drop (e.g. an `EXTERNAL_SENSORS` entry
# missing a required key) — a swallowed parse error here would leave a tap
# plan unable to resolve a sensor the operator believes is configured.
from axrange.platform.protocols import ConfigError


@dataclass
class ExternalSensor:
    """A config-declared EXTERNAL sensor — an operator-managed, hand-
    provisioned node (e.g. the packetXpress `XPress-SW` sink/capture box)
    that a tap plan may direct traffic to, WITHOUT it ever appearing in A1's
    standing O2 inventory (`docs/design/06-taps-sensors.md`). Declared via the
    `EXTERNAL_SENSORS` adapter.conf key (a JSON list). axRange never
    provisions or reaches into this node — it only aims a gretap's `remote`
    at `transport_ip`; the operator is responsible for the sensor-side
    network (transport NIC/IP + the flow-based `gretap external` decap +
    whatever capture tool runs there).

    `id` is the tap-plan-facing sensor name (matched against a tap binding's
    `sensor:`, same namespace as an A1 standing sensor's `sensor_id`) and is
    ALSO what gets auto-protected from every tier's teardown (§ below) — it
    should match the node's PVE display name exactly if you want the
    teardown protect-guard to recognize it there too.
    """
    id: str
    vmid: int
    transport_ip: str
    type: str = "packetxpress"
    capture_iface: str = "gre-cap"


# O2 surface endpoint defaults (docs/WORK_ORDER_access_facts.md Part B) --
# (port, scheme) per service. `O2_<SVC>_PORT` in adapter.conf overrides a
# port; the scheme is fixed per service (wazuh is https, the rest http).
_O2_SERVICE_DEFAULTS: dict[str, tuple[int, str]] = {
    "grafana": (3000, "http"),
    "loki": (3100, "http"),
    "prometheus": (9090, "http"),
    "caldera": (8888, "http"),
    "wazuh": (443, "https"),
}


@dataclass
class AdapterConfig:
    #: The platform this config belongs to (`axrange.platform.protocols.
    #: PlatformConfig.platform`); the factory selects the adapter from it.
    #: A `PLATFORM=` key in the file is read by `axrange.platform.load_config`
    #: (it must say `proxmox` to reach this loader) and is otherwise ignored
    #: here.
    platform: str = "proxmox"
    proxmox_api_url: str = ""
    proxmox_api_token: str = ""
    proxmox_verify_tls: bool = True

    ovs_ssh_host: str = ""
    ovs_ssh_user: str = "ovs-operator"
    ovs_ssh_key: str = ""
    ovs_ssh_extra_args: list[str] = field(default_factory=list)

    # Guest-side: an SSH public key axrange injects via cloud-init so the
    # operator can log into deployed VMs. Distinct from ovs_ssh_key (which
    # authenticates to the Proxmox host). Empty = no key injection.
    guest_ssh_pubkey_file: str = ""

    default_node: str = ""

    # Deploy-wide default password (docs/WORK_ORDER_access_facts.md Part A,
    # Authority: user 2026-07-25). Fills every node/account login that has no
    # password of its own -- an explicitly-set password (a binding's
    # cloud_init.password extra field, an IdentityEntry.password, one of the
    # intentionally-weak AD accounts) still wins; the four call sites that
    # used to hardcode "Axr4nge-Lab!" now fall back to this. Default KEEPS
    # the prior constant, so an adapter.conf with no DEFAULT_PASSWORD key
    # behaves identically to before this field existed.
    default_password: str = "Axr4nge-Lab!"

    # Network sensor (OP-OVL deploy-network-sensor, Option C — sensor-tier
    # Suricata, docs/SLICE_OVERLAY.md §4b). Standing aggregation infra, like the
    # sensor: a Suricata network-sensor VM fed a mirror of the sensor's capture iface.
    # Resolved at *deploy* time (plan stays network-sensor-agnostic). vmid=0 => a
    # scenario with a network sensor fails loudly at deploy, not at plan.
    # NOTE: this Option-C sensor is NOT the A1/A2 tap-playout sensor plane
    # (docs/WORK_ORDER_sensor_agnostic_te.md) — it's a separate, already-
    # superseded/back-compat mechanism, deliberately left untouched by the
    # sensor-agnostic-te change (out of that work order's scope; still
    # config-driven via these two fields).
    network_sensor_vmid: int = 0
    network_sensor_iface: str = "cap-mirror"

    # Loki push endpoint for in-scenario sensors that set `export: { ship: loki }`
    # (sensors design Phase C). The sensor VM ships its structured artifact
    # (Suricata eve.json) here. Resolved at deploy (plan stays Loki-agnostic);
    # empty => a sensor that asks to ship to Loki gets a loud no-op note, not a
    # hard failure. Point it at the observability stack's Loki (o2/).
    loki_url: str = ""

    # O2 surface observability endpoints (`docs/WORK_ORDER_cli_lifecycle.md`
    # §3.4, D2 resolved) -- consumed by `axrange o2 info`. These live on the
    # dedicated O2 server-stack VM (`docs/WORK_ORDER_o2_server_stack.md`),
    # which is NOT in the deploy state store, so `adapter.conf` is the one
    # authoritative source. Keys: grafana/loki/prometheus/caldera/wazuh; only
    # the ones actually SET (non-empty) appear in this dict -- an
    # unconfigured endpoint is unknown, never derived from a guessed sensor
    # IP. `o2_endpoints["loki"]` is a SEPARATE knob from `loki_url` above
    # (that one is the sensor's OWN push target; this one is the operator-
    # facing "where do I browse Loki" URL -- they usually point at the same
    # host but are not required to).
    o2_endpoints: dict[str, str] = field(default_factory=dict)

    # O2-services register + auto-resolve (docs/WORK_ORDER_access_facts.md
    # Part B). The docker-host VM (docs/WORK_ORDER_o2_server_stack.md) is
    # DECLARED here (vmid and/or name) so `o2 info` can resolve its live IP
    # over the Proxmox API and derive the five endpoint URLs, instead of an
    # operator hand-copying `O2_*_URL` for each one. An explicit `O2_<SVC>_URL`
    # (o2_endpoints, above) still wins per-service -- see `resolve_o2_endpoints`.
    o2_services_vmid: int = 0
    o2_services_name: str = ""
    # Per-service port override (`O2_<SVC>_PORT`); only entries the operator
    # actually set appear here -- unset falls back to `_O2_SERVICE_DEFAULTS`.
    o2_service_ports: dict[str, int] = field(default_factory=dict)

    # In-scenario sensor capture retention (disk-%/file-count prune
    # watchdog, `render_sensor_setup`'s tcpdump-sensor branch,
    # config_exec/render.py). User decision 2026-07-17: prune oldest pcaps
    # when the capture filesystem's used% crosses `capture_high_water_pct`,
    # down to below `capture_low_water_pct`. Additive/optional --
    # CAPTURE_HIGH_WATER_PCT / CAPTURE_LOW_WATER_PCT in adapter.conf; unset
    # falls back to these 85/70 defaults. `load_config` clamps
    # `capture_low_water_pct < capture_high_water_pct` (review NIT 2026-07-17,
    # parity with the standing appliance's own sanity guard,
    # o2/tcpdump-sensor/bin/capture-watchdog) -- an operator-set LOW >= HIGH
    # would otherwise ping-pong the prune loop.
    capture_high_water_pct: int = 85
    capture_low_water_pct: int = 70
    # File-COUNT prune ceiling (review SHOULD-FIX 2026-07-17): independent of
    # disk%, catches the idle-mirror inode-growth case (many tiny rotated
    # files that barely move disk usage). CAPTURE_MAX_FILES in adapter.conf;
    # unset falls back to 2000.
    capture_max_files: int = 2000

    # Mirror-plane gretap queueing (2026-08-13, dev45 max-capture-rate run).
    # A freshly created `ip link ... type gretap` inherits the NODE's default
    # qdisc, which on PVE 9.x is `fq_codel` -- an AQM whose whole job is to DROP
    # packets to hold latency down. That is correct for a congested uplink and
    # exactly wrong for a capture path, where a dropped frame is lost evidence
    # and there is no sender to signal: the mirrored copy is fire-and-forget.
    # Measured on dev45: replacing it with a deep FIFO removed 9,349 drops from
    # a 90s window at ~2.38 Gbit/s and was worth roughly a 60% increase in
    # lossless mirror throughput.
    #
    # `attach_gre_tunnel` therefore asserts `pfifo limit N` + `txqueuelen N` on
    # every run. The qdisc KIND is deliberately not operator-tunable: it would
    # have to be interpolated into an ssh command string, and the set of kinds
    # that make sense for a mirror target is exactly one.
    #
    # MIRROR_GRETAP_TXQUEUELEN in adapter.conf. **0 disables the assert
    # entirely** -- the escape hatch for a node whose qdisc is managed
    # elsewhere; the gretap then keeps whatever the node default is, i.e. the
    # pre-2026-08-13 behaviour.
    mirror_gretap_txqueuelen: int = 20000

    # Operator-managed external nodes no teardown (any tier -- te/tap/o2) may
    # ever destroy, on top of the built-in `naming.PROTECTED_EXTERNAL_NAMES`
    # (e.g. `XPress-SW`, the packetXpress sink/capture box -- created by hand,
    # outside axRange's deploy lifecycle, and must outlive every tier).
    # PROTECTED_EXTERNAL_NAMES in adapter.conf, comma-separated; threaded into
    # `planner.teardown.destroy_deploy(protected_names=...)` by every caller.
    protected_external_names: list[str] = field(default_factory=list)
    # Review-hardening belt to protected_external_names' suspenders: a vmid
    # a teardown must refuse EVEN IF `backend.vm_name()` fails to resolve a
    # display name (the fail-open gap the name guard alone can't close).
    # Auto-populated from every declared `external_sensors[].vmid` (§ below),
    # unioned with an optional standalone `PROTECTED_EXTERNAL_VMIDS`
    # comma-list in adapter.conf. Threaded into
    # `planner.teardown.destroy_deploy(protected_vmids=...)` by every caller.
    protected_external_vmids: list[int] = field(default_factory=list)

    # Config-declared EXTERNAL sensors (`EXTERNAL_SENSORS` in adapter.conf, a
    # JSON list -- see `ExternalSensor` above) that a tap plan may direct
    # traffic to without being in A1's standing O2 inventory. Declaring one
    # here is SUFFICIENT to protect it from every tier's teardown (its `id`
    # folds into `protected_external_names`, its `vmid` into
    # `protected_external_vmids`, both above) -- no double-declaration
    # needed. axRange never provisions/reaches into an external sensor; the
    # operator owns its network + capture tool entirely by hand.
    external_sensors: list[ExternalSensor] = field(default_factory=list)

    def expand_paths(self) -> "AdapterConfig":
        """Expand `~` in `ovs_ssh_key` etc. Mutates in place; returns self."""
        if self.ovs_ssh_key:
            self.ovs_ssh_key = str(Path(self.ovs_ssh_key).expanduser())
        if self.guest_ssh_pubkey_file:
            self.guest_ssh_pubkey_file = str(
                Path(self.guest_ssh_pubkey_file).expanduser()
            )
        return self

    # ---- the neutral facts `GET /api/targets` reports (`PlatformConfig`) ----
    @property
    def endpoint(self) -> str:
        return self.proxmox_api_url or ""

    @property
    def verify_tls(self) -> bool:
        return bool(self.proxmox_verify_tls)

    @property
    def has_token(self) -> bool:
        return bool(self.proxmox_api_token)

    @property
    def has_ssh_key(self) -> bool:
        return bool(self.ovs_ssh_key)

    def resolve_o2_endpoints(self, *, services_vmid: int | None = None,
                              services_name: str | None = None,
                              services_node: str | None = None,
                              service_ports: dict[str, int] | None = None) -> dict[str, str]:
        """Resolve the five O2 surface endpoint URLs (`docs/WORK_ORDER_access_facts.md`
        Part B). Layered precedence PER service:

          1. an explicit `O2_<SVC>_URL` (`self.o2_endpoints`) -> used verbatim.
          2. else a declared services VM (`services_vmid`/`self.o2_services_vmid`
             or `self.o2_services_name`) -> resolve its live IP over the Proxmox
             API (best-effort) and derive ``http[s]://<ip>:<port>``.
          3. else -> an actionable ``"(not configured — set O2_SERVICES_VMID or a
             per-service O2_<SVC>_URL in adapter.conf)"``.

        `services_vmid`/`services_node`/`service_ports` let a caller (the
        surface-manifest `services:` stanza, Part B item 5) override the
        adapter.conf-declared services VM for ONE resolution without mutating
        this config -- adapter.conf stays the resolution source for the bare
        `o2 info` of the standing surface.

        Live IP resolution NEVER raises: an unreachable cluster or an
        agent-less VM degrades to ``"(services vmid=N — IP unresolved)"`` so
        `info` can still print the rest of its output (done-criterion #4).
        Lazy import of `resolve.py` to avoid a config<->rest module import
        cycle (same pattern as `rest.py`'s lazy ssh import).
        """
        from axrange.adapter_proxmox.resolve import resolve_vm_ip, resolve_vmid_by_name

        vmid = services_vmid if services_vmid is not None else self.o2_services_vmid
        name = services_name if services_name is not None else self.o2_services_name
        ports = {**self.o2_service_ports, **(service_ports or {})}

        out: dict[str, str] = {}
        have_services_vm = bool(vmid or name)
        ip: str | None = None
        ip_attempted = False
        node = services_node or self.default_node

        for svc, (default_port, scheme) in _O2_SERVICE_DEFAULTS.items():
            explicit = self.o2_endpoints.get(svc)
            if explicit:
                out[svc] = explicit
                continue
            if not have_services_vm:
                out[svc] = (
                    "(not configured — set O2_SERVICES_VMID or a per-service "
                    "O2_<SVC>_URL in adapter.conf)"
                )
                continue
            if not ip_attempted:
                ip_attempted = True
                resolved_vmid = vmid
                if not resolved_vmid and name:
                    found = resolve_vmid_by_name(self, name)
                    if found:
                        resolved_vmid, node = found
                        vmid = resolved_vmid  # remember for the label below
                if resolved_vmid:
                    ip = resolve_vm_ip(self, resolved_vmid, node=node)
            port = ports.get(svc, default_port)
            if ip:
                out[svc] = f"{scheme}://{ip}:{port}"
            else:
                label = vmid or name or "?"
                out[svc] = f"(services vmid={label} — IP unresolved)"
        return out


_BOOL_TRUE = {"1", "true", "yes", "on"}
_BOOL_FALSE = {"0", "false", "no", "off"}


def _to_bool(s: str | None, default: bool) -> bool:
    if s is None:
        return default
    v = s.strip().lower()
    if v in _BOOL_TRUE:
        return True
    if v in _BOOL_FALSE:
        return False
    return default


def _read_conf_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = _strip_value(v)
    return out


def _strip_value(v: str) -> str:
    """Parse a conf value: trim, drop an inline ``#`` comment, strip quotes.

    The inline comment is removed only when the value is unquoted and the
    ``#`` follows whitespace (``foo   # note`` -> ``foo``). A ``#`` that is
    not preceded by whitespace is kept, so secrets / URLs / tokens that
    legitimately contain ``#`` survive. A fully-quoted value is taken verbatim
    and any trailing comment after the closing quote is ignored.
    """
    s = v.strip()
    if s[:1] in ("'", '"'):
        quote = s[0]
        end = s.find(quote, 1)
        return s[1:end] if end != -1 else s[1:]
    for i in range(1, len(s)):
        if s[i] == "#" and s[i - 1] in " \t":
            s = s[:i]
            break
    return s.strip()


def _parse_external_sensors(raw: str) -> list[ExternalSensor]:
    """Parse the `EXTERNAL_SENSORS` adapter.conf value (a JSON list of
    objects) into `ExternalSensor`s. Empty/unset -> `[]`. A malformed value
    (bad JSON, not a list, a missing required key, a non-int `vmid`, or a
    BLANK `id`/`transport_ip` -- review nice-to-have: key PRESENCE alone
    doesn't catch `"id": ""` / `"transport_ip": ""`, which would silently
    attach a gretap with an empty remote) fails LOUD with a `ConfigError`
    naming the problem -- a silently-dropped/malformed entry would leave a
    tap plan unable to resolve a sensor the operator believes is configured,
    which is worse than refusing to start."""
    raw = raw.strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as e:
        raise ConfigError(f"EXTERNAL_SENSORS is not valid JSON: {e}") from e
    if not isinstance(data, list):
        raise ConfigError(
            f"EXTERNAL_SENSORS must be a JSON list, got {type(data).__name__}"
        )
    out: list[ExternalSensor] = []
    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise ConfigError(
                f"EXTERNAL_SENSORS[{i}] must be a JSON object, got {type(entry).__name__}"
            )
        missing = [k for k in ("id", "vmid", "transport_ip") if k not in entry]
        if missing:
            raise ConfigError(
                f"EXTERNAL_SENSORS[{i}] missing required key(s): {', '.join(missing)}"
            )
        sensor_id = str(entry["id"]).strip()
        if not sensor_id:
            raise ConfigError(f"EXTERNAL_SENSORS[{i}].id must not be blank")
        transport_ip = str(entry["transport_ip"]).strip()
        if not transport_ip:
            raise ConfigError(
                f"EXTERNAL_SENSORS[{i}].transport_ip must not be blank "
                f"(id={sensor_id!r}) -- a blank remote would attach a gretap "
                "to nowhere"
            )
        try:
            vmid = int(entry["vmid"])
        except (TypeError, ValueError) as e:
            raise ConfigError(
                f"EXTERNAL_SENSORS[{i}].vmid must be an int, got {entry['vmid']!r}"
            ) from e
        out.append(ExternalSensor(
            id=sensor_id,
            vmid=vmid,
            transport_ip=transport_ip,
            type=str(entry.get("type", "packetxpress")),
            capture_iface=str(entry.get("capture_iface", "gre-cap")),
        ))
    return out


def default_adapter_conf_paths() -> list[Path]:
    """Where `adapter.conf` is looked for when no path is given, first hit wins:
    the installed location, then the checkout's `config/adapter.conf`
    (`axrange.paths.CONFIG_DIR`, so the CLI works from any directory in the
    monorepo), then the current directory (the pre-2026-10-20 default, kept so an
    operator running beside an ad-hoc conf is not surprised).

    The one list for both `load_config` here and `imagebuild.config`, which
    re-reads the same file for its own keys."""
    from axrange.paths import ADAPTER_CONF

    return [Path("/etc/axrange/adapter.conf"), ADAPTER_CONF, Path.cwd() / "adapter.conf"]


def load_config(
    config_path: str | Path | None = None,
    *,
    overrides: dict[str, Any] | None = None,
) -> AdapterConfig:
    """Load AdapterConfig from adapter.conf + env + overrides.

    Precedence (highest -> lowest):
      1. `overrides` dict
      2. environment variables
      3. `adapter.conf` (default search: `default_adapter_conf_paths()`)
      4. dataclass defaults
    """
    paths: list[Path] = []
    if config_path is not None:
        paths.append(Path(config_path))
    else:
        paths.extend(default_adapter_conf_paths())
    file_data: dict[str, str] = {}
    for p in paths:
        if p.is_file():
            file_data = _read_conf_file(p)
            break

    def get(key: str, default: str = "") -> str:
        if overrides and key in overrides and overrides[key] is not None:
            return str(overrides[key])
        env_val = os.environ.get(key)
        if env_val is not None:
            return env_val
        return file_data.get(key, default)

    o2_endpoints = {
        name: url
        for name, url in {
            "grafana": get("O2_GRAFANA_URL", ""),
            "loki": get("O2_LOKI_URL", ""),
            "prometheus": get("O2_PROMETHEUS_URL", ""),
            "caldera": get("O2_CALDERA_URL", ""),
            "wazuh": get("O2_WAZUH_URL", ""),
        }.items()
        if url
    }

    o2_services_vmid = int(get("O2_SERVICES_VMID", "0") or "0")
    o2_services_name = get("O2_SERVICES_NAME", "")
    o2_service_ports: dict[str, int] = {}
    for _svc in _O2_SERVICE_DEFAULTS:
        _port_raw = get(f"O2_{_svc.upper()}_PORT", "")
        if not _port_raw:
            continue
        try:
            o2_service_ports[_svc] = int(_port_raw)
        except ValueError:
            # Malformed port override -- best-effort, fall back to the
            # built-in default rather than fail the whole config load over
            # one endpoint's cosmetic port number.
            pass

    capture_high = int(get("CAPTURE_HIGH_WATER_PCT", "85") or "85")
    capture_low = int(get("CAPTURE_LOW_WATER_PCT", "70") or "70")
    # Sanity clamp (review NIT 2026-07-17, parity with the standing
    # appliance's own capture-watchdog guard): LOW must be strictly below
    # HIGH, or the prune loop ping-pongs (a prune pass that "succeeds" by
    # hitting a LOW no lower than HIGH never actually relieves pressure, so
    # the very next tick re-triggers immediately). An operator typo
    # (LOW >= HIGH) is silently corrected rather than left to degrade.
    if capture_low >= capture_high:
        capture_low = capture_high - 1

    # Mirror gretap queue depth. Parsed defensively rather than with a bare
    # int(): `load_config` is called from a dozen independent CLI/API/o2control
    # entry points, so a new raise here would surface as a raw traceback on
    # paths that never expected one (LES-090). A malformed value falls back to
    # the default; a negative one clamps to 0 (= leave the node default alone).
    try:
        mirror_qlen = int(get("MIRROR_GRETAP_TXQUEUELEN", "20000") or "20000")
    except ValueError:
        mirror_qlen = 20000
    if mirror_qlen < 0:
        mirror_qlen = 0

    protected_external_names = [
        n.strip()
        for n in get("PROTECTED_EXTERNAL_NAMES", "").split(",")
        if n.strip()
    ]
    protected_external_vmids = [
        int(v.strip())
        for v in get("PROTECTED_EXTERNAL_VMIDS", "").split(",")
        if v.strip()
    ]

    external_sensors = _parse_external_sensors(get("EXTERNAL_SENSORS", ""))
    # Declaring an external sensor is SUFFICIENT to protect it -- fold its
    # id/vmid into the effective protected lists (union, not replace; an
    # operator may ALSO set PROTECTED_EXTERNAL_NAMES/_VMIDS directly).
    for ext in external_sensors:
        if ext.id not in protected_external_names:
            protected_external_names.append(ext.id)
        if ext.vmid not in protected_external_vmids:
            protected_external_vmids.append(ext.vmid)

    cfg = AdapterConfig(
        proxmox_api_url=get("PROXMOX_API_URL", ""),
        proxmox_api_token=get("PROXMOX_API_TOKEN", ""),
        proxmox_verify_tls=_to_bool(get("PROXMOX_VERIFY_TLS", "true"), True),
        ovs_ssh_host=get("OVS_SSH_HOST", ""),
        ovs_ssh_user=get("OVS_SSH_USER", "ovs-operator"),
        ovs_ssh_key=get("OVS_SSH_KEY", ""),
        guest_ssh_pubkey_file=get("GUEST_SSH_PUBKEY_FILE", ""),
        default_node=get("DEFAULT_NODE", ""),
        default_password=get("DEFAULT_PASSWORD", "Axr4nge-Lab!"),
        network_sensor_vmid=int(get("NETWORK_SENSOR_VMID", "0") or "0"),
        network_sensor_iface=get("NETWORK_SENSOR_IFACE", "cap-mirror"),
        loki_url=get("LOKI_URL", ""),
        o2_endpoints=o2_endpoints,
        o2_services_vmid=o2_services_vmid,
        o2_services_name=o2_services_name,
        o2_service_ports=o2_service_ports,
        capture_high_water_pct=capture_high,
        capture_low_water_pct=capture_low,
        capture_max_files=int(get("CAPTURE_MAX_FILES", "2000") or "2000"),
        mirror_gretap_txqueuelen=mirror_qlen,
        protected_external_names=protected_external_names,
        protected_external_vmids=protected_external_vmids,
        external_sensors=external_sensors,
    )
    return cfg.expand_paths()


def example_conf_text() -> str:
    """Return the contents of adapter.conf.example for distribution / docs."""
    return (
        "# axRange Proxmox adapter configuration\n"
        "# Copy to config/adapter.conf (or /etc/axrange/adapter.conf) and fill in the operator-side values.\n"
        "# Env vars (PROXMOX_API_URL, OVS_SSH_KEY, etc.) override this file.\n"
        "\n"
        "# --- REST backend (Proxmox API) ---\n"
        "PROXMOX_API_URL=https://10.0.0.85:8006/api2/json\n"
        "# PVEAPIToken=<user>@<realm>!<token-id>=<secret>\n"
        "PROXMOX_API_TOKEN=PVEAPIToken=axr@pve!planner=REPLACE-WITH-TOKEN-SECRET\n"
        "# Set false for self-signed cluster certs (slice default; tighten in prod).\n"
        "PROXMOX_VERIFY_TLS=false\n"
        "\n"
        "# --- SSH backend (tc/ip link + mirror hook install) ---\n"
        "OVS_SSH_HOST=10.0.0.85\n"
        "OVS_SSH_USER=ovs-operator\n"
        "OVS_SSH_KEY=~/.ssh/pxtwin1_ed25519\n"
        "\n"
        "# --- Guest cloud-init (optional) ---\n"
        "# SSH public key axrange injects into deployed VMs so the operator\n"
        "# can `ssh <ciuser>@<vm-ip>` after deploy. Leave empty to skip key\n"
        "# injection; `qm guest exec` from the Proxmox node still works.\n"
        "GUEST_SSH_PUBKEY_FILE=~/.ssh/pxtwin1_ed25519.pub\n"
        "\n"
        "# --- Cluster facts the planner needs ---\n"
        "# Default Proxmox node name (slice = single-node placement).\n"
        "DEFAULT_NODE=pxtwin1\n"
        "\n"
        "# --- Deploy-wide default password (docs/WORK_ORDER_access_facts.md Part A) ---\n"
        "# Fills every node/account login that has no password of its own (console +\n"
        "# SSH-by-password on Linux; Administrator/safemode/domain-admin on Windows).\n"
        "# An explicitly-set password (a binding's cloud_init password, a per-identity\n"
        "# IdentityEntry.password, an intentionally-weak AD account) still wins.\n"
        "# Unset keeps the historical lab-default constant shown below.\n"
        "# SECURITY: setting this (or even leaving it at the built-in default) turns ON\n"
        "# SSH password login lab-wide on every Linux node, with a well-known password --\n"
        "# understand the posture change before using this outside an isolated lab.\n"
        "# DEFAULT_PASSWORD=Axr4nge-Lab!\n"
        "\n"
        "# Tap-agnostic te (docs/WORK_ORDER_tap_eligibility.md, 2026-07-28):\n"
        "# no SENSOR_TRANSPORT_IP / SENSOR_<ID>_TRANSPORT_IP / SENSOR_SSH_* /\n"
        "# SENSOR_CAPTURE_IFACE keys any more. A te deploy carries NO tap-related\n"
        "# state at all -- no mirror/gretap/heartbeat, no tap-intent bookkeeping.\n"
        "# The sensor's transport IP + vmid are discovered from A1's standing O2\n"
        "# inventory by the A2 tap playout (`axrange tap deploy`) at bind time.\n"
        "\n"
        "# --- O2 surface (observability endpoints; consumed by `axrange o2 info`,\n"
        "# docs/WORK_ORDER_cli_lifecycle.md §3.4) ---\n"
        "# These live on the dedicated O2 server-stack VM, not in the deploy state\n"
        "# store, so this file is the one authoritative source. All optional; an\n"
        "# unconfigured endpoint is unknown, never derived from a guessed sensor IP.\n"
        "# O2_GRAFANA_URL=http://10.0.0.90:3000\n"
        "# O2_LOKI_URL=http://10.0.0.90:3100\n"
        "# O2_PROMETHEUS_URL=http://10.0.0.90:9090\n"
        "# O2_CALDERA_URL=http://10.0.0.90:8888\n"
        "# O2_WAZUH_URL=https://10.0.0.90:443\n"
        "\n"
        "# --- O2 services VM register + auto-resolve (docs/WORK_ORDER_access_facts.md\n"
        "# Part B) --- an alternative to hand-copying every O2_*_URL above: declare the\n"
        "# server-stack VM (vmid and/or name; vmid is preferred/faster) and `o2 info`\n"
        "# resolves its live IP over the Proxmox API and derives the five endpoint URLs\n"
        "# itself. A per-service O2_*_URL above still WINS over this for that service.\n"
        "# O2_SERVICES_VMID=102\n"
        "# O2_SERVICES_NAME=o2-services\n"
        "# Optional per-service port overrides; defaults shown (wazuh is https, the\n"
        "# rest are http).\n"
        "# O2_GRAFANA_PORT=3000\n"
        "# O2_LOKI_PORT=3100\n"
        "# O2_PROMETHEUS_PORT=9090\n"
        "# O2_CALDERA_PORT=8888\n"
        "# O2_WAZUH_PORT=443\n"
        "\n"
        "# --- In-scenario sensor capture retention (optional; tcpdump-sensor's\n"
        "# in-guest disk-%/file-count prune watchdog, config_exec/render.py) ---\n"
        "# Prune oldest pcaps once the capture filesystem's used% reaches HIGH,\n"
        "# down to below LOW. Defaults 85/70 if unset (LOW is clamped below HIGH).\n"
        "# CAPTURE_HIGH_WATER_PCT=85\n"
        "# CAPTURE_LOW_WATER_PCT=70\n"
        "# Independent of disk%: prune oldest once the pcap file count exceeds\n"
        "# this (catches idle-link inode growth). Defaults 2000 if unset.\n"
        "# CAPTURE_MAX_FILES=2000\n"
        "\n"
        "# --- Mirror gretap queueing (optional) ---\n"
        "# `attach-gre-tunnel` replaces the node-default qdisc on each mirror\n"
        "# gretap with `pfifo limit N` and sets txqueuelen N. The node default\n"
        "# on PVE 9.x is fq_codel -- an AQM that DROPS to control latency, which\n"
        "# is wrong for a capture path (the mirrored copy is fire-and-forget, so\n"
        "# a drop is lost evidence and no sender backs off). Measured on dev45:\n"
        "# 9,349 frames lost in 90s at ~2.38 Gbit/s before, zero after.\n"
        "# Asserted on every run, so pre-existing gretaps heal too.\n"
        "# Set 0 to leave the node default alone. Defaults 20000 if unset.\n"
        "# MIRROR_GRETAP_TXQUEUELEN=20000\n"
        "\n"
        "# --- Protected external nodes (optional; blast-radius guard) ---\n"
        "# Comma-separated allowlist of operator-managed nodes created BY HAND,\n"
        "# outside axrange's deploy lifecycle, that no teardown (te/tap/o2, ANY\n"
        "# tier) may ever destroy -- on top of the built-in default (XPress-SW,\n"
        "# the packetXpress sink/capture box).\n"
        "# PROTECTED_EXTERNAL_NAMES=XPress-SW\n"
        "# Same idea, by vmid -- a review-hardening belt: a teardown refuses these\n"
        "# vmids even if the PVE display-name lookup fails to resolve (fail-open\n"
        "# gap the name guard alone can't close). Auto-unioned with every\n"
        "# EXTERNAL_SENSORS[].vmid below -- you don't need to also list it here.\n"
        "# PROTECTED_EXTERNAL_VMIDS=250\n"
        "\n"
        "# --- Config-declared EXTERNAL sensors (optional) ---\n"
        "# A JSON list of operator-provisioned sensor nodes a tap plan may direct\n"
        "# traffic to WITHOUT them ever appearing in A1's standing O2 inventory --\n"
        "# e.g. the packetXpress XPress-SW sink/capture box. axRange NEVER\n"
        "# provisions or reaches into one of these: it only points a gretap's\n"
        "# `remote` at transport_ip. Declaring one here is SUFFICIENT to protect\n"
        "# it from every tier's teardown (id -> PROTECTED_EXTERNAL_NAMES, vmid ->\n"
        "# PROTECTED_EXTERNAL_VMIDS, both automatic) -- no double-declaration.\n"
        "#\n"
        "# Required manual steps on the XPress-SW side (operator-owned, once):\n"
        "#   1. Give it a transport NIC on the same isolated transport vnet/subnet\n"
        "#      axRange's tap playout uses, with the static IP you put in\n"
        "#      transport_ip below.\n"
        "#   2. `ip link add gre-cap type gretap external` (a FLOW-based gretap --\n"
        "#      accepts any GRE key, so it merges every tapped bridge's traffic\n"
        "#      that targets this sensor; matches capture_iface below).\n"
        "#   3. Run whatever capture tool you want against `gre-cap` (tcpdump,\n"
        "#      Suricata, the packetXpress agent, ...) -- axRange has no opinion\n"
        "#      and does not manage it.\n"
        "#\n"
        "# EXTERNAL_SENSORS=[{\"id\":\"XPress-SW\",\"vmid\":250,\"transport_ip\":\"10.255.250.20\",\"type\":\"packetxpress\",\"capture_iface\":\"gre-cap\"}]\n"
    )
