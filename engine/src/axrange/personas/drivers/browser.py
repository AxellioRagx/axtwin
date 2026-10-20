"""The `browser` driver (docs/WORK_ORDER_protocol_engines.md §5 E4,
`docs/design/07-traffic-personas.md` "drive kinds"): render a per-persona
in-guest client that, at each emit's firing instant, **drives a real headless
Chromium** at a real name instead of opening a socket and pushing filler
bytes at a port.

**Why this exists — one engine, most of the wire.** The `scripted` driver
emits connectivity SHAPE (right peer/port/timing, `axrange-persona-payload`
filler on the wire); the `command` driver fixes that one protocol at a time
with one tool per proto. A real browser fixes the whole web stack at once:
a single page load produces **DNS** (when it resolves), a **real TLS
handshake with a real SNI and a real JA3/JA4 fingerprint**, **HTTP/1.1** or
**HTTP/2** by ALPN negotiation, and — when the origin is forced — **QUIC /
HTTP-3**. On the corp-apcn cast that family is ~83% of flows and ~73% of
bytes, which is what makes this the highest-coverage single engine of the
protocol-engine round (work order §1/§5 E4).

**This is a fourth sibling of an existing mechanism, not a new pipeline.**
Everything around `fire()` is reused verbatim, exactly as `command` reuses
it: the persona document (`emits[]` already carries proto/ports/peer_role/
cadence/volume; content rides `knobs:`, work order §3.2 — no schema field),
orchestrator-side peer resolution (`scripted.resolve_emit_peers`), the
cadence/gate/volume resolver (`scripted._resolve_emit_schedule`), the launch
primitive (`config_exec.render.systemd_daemon_start_block` — a detached
transient unit, NEVER `nohup ... &`, LES-086), the instance identity/
lifecycle helpers (`drivers.base`), and the delivery path (`ga.write_file`
base64-in-command, LES-002). One new `_DRIVERS` entry is the entire
execution-side wiring change.

---

**Containment — the choice a reviewer will question.** A browser cannot take
`curl --resolve`, so `command`'s structural rule ("every address in the argv
is an IP literal the orchestrator baked") cannot be reproduced token for
token. Two modes, both explicit:

1. **Default — `--host-resolver-rules=MAP * <resolved_peer_ip>`.** The
   orchestrator resolves the emit's `peer_role` to a twin host IP exactly as
   it does for `command`, and that literal is baked into the rendered client.
   Chromium then performs **no name resolution at all**: every hostname is
   mapped, in-process, to that address. The URL still carries the REAL name,
   so the **Host header and the TLS SNI carry the real name** — which is the
   entire fidelity point — while the connection lands on the baked twin
   address. This keeps the existing containment property (no guest-side DNS,
   no dependency on a resolver being up, a fully inspectable client) and
   works TODAY, before the work order's E1/E2/E3 containment triangle
   (router egress phases + any-IP sink + twin name service) exists.
2. **Opt-in — `knobs.resolve_via_dns: true`.** The MAP rule is omitted and
   the guest resolves normally. This is the mode the user ratified for the
   finished twin (ruling 1, 2026-09-21: containment becomes a NETWORK
   property — the twin's own DNS answers, an any-IP sink impersonates every
   external server, and the router denies real egress). It is **opt-in, not
   the default**, because in a twin without that triangle in place a guest
   that resolves normally is a guest that can reach the real internet.
   In this mode the emit needs no resolved peer at all (a `peer_role:
   internet`/`egress` browser persona is exactly the intended shape), so the
   render produces ONE invocation set rather than one per peer.

Mode 1 is checked structurally at render (`_check_browser_args`): the MAP
rule must be present exactly once and must carry the peer's IP LITERAL, the
argv must carry exactly one URL, and that URL's host must be the real name.
Mode 2 asserts the opposite (no MAP rule) so the two modes can never silently
blur into each other.

**Which names it visits.** `knobs.browse_names: [...]` — **names, not URLs**
(no scheme, no path, no port, not an IP literal), seeded-shuffled per persona
instance so two hosts running the same persona do not lock-step through the
same site order. A malformed entry is refused at render: that string becomes
the TLS SNI and the HTTP Host header, and a wrong shape there is write-path
garbage that a sensor would faithfully record as truth. With no
`browse_names`, the driver synthesizes ONE name from the emit's `peer_role`
(`<role>.twin.invalid` — RFC 6761 guarantees `.invalid` never resolves
globally) and **announces the substitution once in the guest log** rather
than pretending the persona authored it.

**Volume -> pages per firing** (the `command` driver's precedent, §3.3):
`volume:` resolves to the number of PAGE LOADS per firing (`low` 2 / `med` 8
/ `high` 24 / `bulk` 64 — the shared `scripted._VOLUME_PROFILE` numbers,
reused rather than re-tabulated) and `gap_s` to the pause between them.
**Byte volume is not controllable through a browser** — how many bytes a page
load costs is the SERVER's choice (page weight, subresources, compression),
not the client's — so an emit's `bytes:`/volume-derived byte budget is
**ignored, and the ignoring is logged once at arm time**, never silently
dropped.

**One Chromium invocation per page load (v1).** A fork costs ~0.3-1s of CPU
and ~150-250 MB RSS, which is affordable at the measured aggregate rates
(~3-40 flows/s across a whole twin) and buys a clean, stateless, inspectable
fetch: no shared profile state between pages, no long-lived process to
supervise, and a hard `subprocess.run(timeout=...)` bound on every fetch. A
persistent-browser variant (one Chromium held open, driven over CDP) would
cut the fork cost and produce more realistic connection REUSE (HTTP/2
multiplexing and QUIC 0-RTT across page loads, which this v1 never exercises
because every fetch starts cold) at the cost of a CDP client dependency in
the guest and a supervised long-lived process. If live measurement shows the
fork rate is the bottleneck, that is the upgrade — and it changes only
`fire()`, not this module's contract.

**Linux only.** `os_family: windows` raises a loud, specific error: the
Windows engine is its own slice (work order §5 E6 — PowerShell built-ins
against a real domain member), and silently falling through to the Linux
branch would send `sh`/`systemctl` at a guest that has neither (LES-001).

**Deterministic**, exactly like its siblings: the same persona + peers + seed
+ instance_id renders byte-identical output. The seed shows up in exactly two
places — the literal `SEED` the guest draws its runtime jitter from, and the
render-time shuffle of `browse_names`.
"""
from __future__ import annotations

import ipaddress
import random
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from axrange.personas.drivers.base import (
    PersonaDriver,
    PersonaDriverError,
    effective_seed,
    heartbeat_unit,
    instance_paths,
    start_linux_python_client,
    stop_unit,
    unsupported_os_family_error,
)
from axrange.personas.drivers.scripted import (
    _BUSINESS_END_MIN,
    _BUSINESS_START_MIN,
    _resolve_emit_schedule,
    render_scripted_launch,
    resolve_emit_peers,
)
from axrange.personas.schema import Emit, Persona

# The binary Debian bookworm's `chromium` package installs is `/usr/bin/
# chromium`; Ubuntu (and older Debian) ship the same browser as
# `chromium-browser`. The REQUIRED package is `chromium` — the probe accepts
# EITHER binary name so a guest built from an Ubuntu cloud image isn't
# refused for a packaging detail, and the fail-loud message names both plus
# the package (`CommandDriver._assert_tools_present`'s posture, §3.4).
# Which one a guest actually has is resolved IN the guest at arm time
# (`find_browser()`), not baked at render — a probe result must never be a
# render input, or the render stops being deterministic.
_BROWSER_BINARIES = ("chromium", "chromium-browser")
_BROWSER_PACKAGE = "chromium"

# Hard per-fetch bound. Chromium can sit on a dead origin far longer than a
# page load has any business taking; `subprocess.run(timeout=...)` kills it.
# Overridable per persona via `knobs.browse_timeout_s`.
_DEFAULT_FETCH_TIMEOUT_S = 30
_MIN_FETCH_TIMEOUT_S = 2
_MAX_FETCH_TIMEOUT_S = 300

# Chromium's virtual clock budget: it fast-forwards timers so a page that
# schedules lazy work finishes deterministically instead of idling until the
# hard timeout above. Deliberately well UNDER the fetch timeout, so a normal
# page exits on its own and the timeout only ever catches a genuinely stuck
# fetch.
_VIRTUAL_TIME_BUDGET_MS = 5000
_WINDOW_SIZE = "1280,800"

# --- what counts as a page that LOADED (2026-09-21) ------------------------
# Chromium exits 0 after rendering its own network-error page, so the exit
# code cannot be the success criterion (dev45: `pages=8 ok=8 fails=0` while
# the sensor saw one tls flow -- LES-001). The evidence is the `--dump-dom`
# output, judged on two things: it exists, and it is not the error page.
#
#: Below this many non-whitespace bytes, `--dump-dom` produced an empty shell
#: rather than a document. An empty `<html><head></head><body></body></html>`
#: is 45 bytes; the smallest page the sink serves is far larger.
_MIN_DOM_BYTES = 64
#: Structural markers of Chromium's own net-error interstitial. Deliberately
#: STRUCTURAL rather than a bare `ERR_` scan: a real page may legitimately
#: contain that substring in script, and a false failure would be as
#: misleading as the false success this replaces. The specific `ERR_*` code,
#: when one is present, is extracted as the REASON once a marker has fired.
_ERROR_MARKERS: tuple[str, ...] = (
    "chrome-error://",
    'id="main-frame-error"',
)

# The synthetic browse name when a persona authors none. `.invalid` is
# RFC 6761-reserved and can never resolve globally — the same reasoning
# `command._DEFAULT_DNS_QNAME` uses for its default query name.
_SYNTHETIC_NAME_SUFFIX = "twin.invalid"

# One DNS label: 1-63 chars of [A-Za-z0-9-], not starting or ending with `-`.
_LABEL_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9.-]")


@dataclass(frozen=True)
class _BrowserProto:
    """One proto this engine can drive through a browser.

    `force_quic` marks the row that additionally pins the origin to
    HTTP/3: Chromium only speaks QUIC to an origin it has already seen
    advertise `Alt-Svc`, so an emit that asks for `quic` must force it
    explicitly or it would silently be a TCP HTTP/2 flow wearing a QUIC
    label — the exact "plausible-looking wrong answer" LES-001 is about.

    `explicit_transports` mirrors `command._CommandTemplate`'s field: the
    set of EXPLICITLY-authored `emits[].transport` values this row can
    honour on the wire. A DERIVED default (`emit.transport` unset, filled
    in by `Emit.effective_transport()`) is never checked against it.
    """

    scheme: str
    default_port: int
    force_quic: bool
    explicit_transports: frozenset[str]


# `http3` is deliberately NOT a second spelling: `quic` is the one token in
# `personas.schema.KNOWN_PROTOS` (PSN-040's open-but-curated vocabulary), and
# two names for one row is how a catalog ends up with half its HTTP/3 emits
# under each. The refusal message below names the supported set, so an author
# who typed `http3` is pointed straight at `quic`.
_BROWSER_PROTOS: dict[str, _BrowserProto] = {
    "http": _BrowserProto("http", 80, False, frozenset({"tcp"})),
    "https": _BrowserProto("https", 443, False, frozenset({"tcp"})),
    "quic": _BrowserProto("https", 443, True, frozenset({"udp"})),
}


def supported_protos() -> tuple[str, ...]:
    """The protos a `drive: browser` persona can emit today (sorted)."""
    return tuple(sorted(_BROWSER_PROTOS))


# ---------------------------------------------------------------------------
# knobs (free-form `Persona.knobs`, work order §3.2 — no schema change)
# ---------------------------------------------------------------------------
def _fail(persona: Persona, instance_id: str, msg: str) -> PersonaDriverError:
    return PersonaDriverError(f"persona[{instance_id}] ({persona.id}): {msg}")


def resolve_browser_knobs(persona: Persona, instance_id: str) -> dict[str, Any]:
    """Validate + normalize this persona's browser knobs, once per render.

    Fails loud on a malformed knob rather than falling back to a default: a
    typo'd `resolve_via_dns` that silently reads as False would leave an
    operator believing the twin's DNS is being exercised when it is not, and
    a typo'd `browse_names` would silently visit the synthetic fallback name
    forever (LES-001 — a wrong answer nobody is told about).
    """
    knobs = persona.knobs if isinstance(persona.knobs, dict) else {}

    raw_names = knobs.get("browse_names")
    names: list[str] = []
    if raw_names is not None:
        if isinstance(raw_names, str) or not isinstance(raw_names, (list, tuple)):
            raise _fail(
                persona, instance_id,
                f"knobs.browse_names must be a LIST of hostnames, got "
                f"{type(raw_names).__name__} ({raw_names!r})",
            )
        names = [_check_browse_name(persona, instance_id, n) for n in raw_names]
        if not names:
            raise _fail(
                persona, instance_id,
                "knobs.browse_names is present but empty — drop the knob to "
                "use the peer_role-derived synthetic name, or list at least "
                "one hostname",
            )

    def flag(key: str) -> bool:
        raw = knobs.get(key, False)
        if not isinstance(raw, bool):
            raise _fail(
                persona, instance_id,
                f"knobs.{key} must be a boolean, got {type(raw).__name__} "
                f"({raw!r})",
            )
        return raw

    timeout_raw = knobs.get("browse_timeout_s", _DEFAULT_FETCH_TIMEOUT_S)
    try:
        timeout_s = int(timeout_raw)
    except (TypeError, ValueError):
        raise _fail(
            persona, instance_id,
            f"knobs.browse_timeout_s must be an integer number of seconds, "
            f"got {timeout_raw!r}",
        ) from None
    if not _MIN_FETCH_TIMEOUT_S <= timeout_s <= _MAX_FETCH_TIMEOUT_S:
        raise _fail(
            persona, instance_id,
            f"knobs.browse_timeout_s {timeout_s} is outside "
            f"{_MIN_FETCH_TIMEOUT_S}..{_MAX_FETCH_TIMEOUT_S} seconds",
        )

    return {
        "browse_names": names,
        # See the module docstring: opt-in, because with no sink/router-phase
        # in place a guest that resolves normally can reach the real internet.
        "resolve_via_dns": flag("resolve_via_dns"),
        # Default OFF, deliberately: the lab CA belongs in the image's trust
        # store (work order §5 E7), and silently ignoring certificate errors
        # would hide the ONE thing the sink has to get right — a real
        # certificate for the SNI that actually arrived.
        "ignore_cert_errors": flag("browser_ignore_cert_errors"),
        "timeout_s": timeout_s,
    }


def _check_browse_name(persona: Persona, instance_id: str, raw: Any) -> str:
    """Refuse anything that is not a bare hostname.

    This string becomes the TLS **SNI** and the HTTP **Host** header. A URL
    (`https://x/`), a host:port, a path, or an IP literal there does not fail
    loudly at runtime — it produces a flow a sensor records as truth with the
    wrong name on it, or (for an IP) a TLS handshake carrying NO SNI at all,
    which is precisely the fidelity gap this engine exists to close.
    """
    name = raw if isinstance(raw, str) else ""
    if not name or not isinstance(raw, str):
        raise _fail(
            persona, instance_id,
            f"knobs.browse_names entry {raw!r} is not a non-empty string",
        )
    if _looks_like_ip(name):
        raise _fail(
            persona, instance_id,
            f"knobs.browse_names entry {name!r} is an IP literal — a browse "
            "target must be a NAME (it becomes the TLS SNI and the Host "
            "header; an IP literal produces a handshake with no SNI at all, "
            "which is the gap this engine exists to close). The address is "
            "supplied separately, by the orchestrator's peer resolution.",
        )
    bad_chars = sorted(set(_UNSAFE_NAME_CHARS.findall(name)))
    if bad_chars or name.endswith(".") or ".." in name or len(name) > 253:
        raise _fail(
            persona, instance_id,
            f"knobs.browse_names entry {name!r} is not a bare hostname "
            "(no scheme, no port, no path, no trailing dot, <=253 chars)"
            + (f"; offending characters: {bad_chars}" if bad_chars else ""),
        )
    for label in name.split("."):
        if not _LABEL_RE.match(label):
            raise _fail(
                persona, instance_id,
                f"knobs.browse_names entry {name!r} has a malformed DNS label "
                f"{label!r} (1-63 chars of [A-Za-z0-9-], not starting or "
                "ending with '-')",
            )
    return name


def _looks_like_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _synthetic_name(persona: Persona, instance_id: str, emit: Emit) -> str:
    """The fallback browse name for a persona that authors no
    `knobs.browse_names`: `<peer_role>.twin.invalid`. Announced at arm time
    (see `names_note`) so an operator reading the guest log is never left
    believing the persona chose it."""
    role = _UNSAFE_NAME_CHARS.sub("-", (emit.peer_role or "").strip().lower())
    role = role.strip("-.")
    if not role or not _LABEL_RE.match(role.split(".")[0]):
        raise _fail(
            persona, instance_id,
            f"emit proto {emit.proto!r} has no usable peer_role "
            f"({emit.peer_role!r}) to synthesize a browse name from, and the "
            "persona authors no `knobs.browse_names` — a browser must be "
            "pointed at a NAME, and guessing one is not a thing this driver "
            "will do",
        )
    return f"{role.split('.')[0]}.{_SYNTHETIC_NAME_SUFFIX}"


# ---------------------------------------------------------------------------
# argv build + the containment check
# ---------------------------------------------------------------------------
_MAP_FLAG = "--host-resolver-rules"

#: Chromium's own background traffic, off. See `_build_args`' docstring for
#: what each one is for and what was measured without them; the ORDER is
#: fixed so a rendered client stays byte-stable.
_QUIET_FLAGS: tuple[str, ...] = (
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-networking",
    "--disable-extensions",
    "--disable-component-update",
    "--disable-domain-reliability",
    "--disable-sync",
    "--metrics-recording-only",
    "--disable-client-side-phishing-detection",
)


def _build_args(
    *,
    name: str,
    spec: _BrowserProto,
    port: int,
    map_ip: str | None,
    profile_dir: str,
    ignore_cert_errors: bool,
) -> list[str]:
    """One headless-Chromium invocation's arguments (the BINARY is prepended
    in the guest, where `find_browser()` resolved which of
    `_BROWSER_BINARIES` actually exists).

    Flag by flag, and why:

    * `--headless=new` — the modern headless mode. The legacy one is a
      separate, quietly divergent browser build; `new` is the same Chrome
      that a user's desktop runs, which is the whole point of using a real
      browser for JA3/JA4 and ALPN fidelity.
    * `--no-sandbox` — the in-guest client runs as **root** (every axRange
      guest-agent exec does), and Chromium's setuid/namespace sandbox
      refuses to start as root. The guest is a disposable twin VM whose
      entire job is to be attacked, so the sandbox buys nothing here; it
      would simply mean no browser at all.
    * `--user-data-dir=` — a per-INSTANCE profile under the instance's own
      path, so two personas on one host never fight over one profile.
    * `--disable-gpu`, `--disable-dev-shm-usage` — a VM has no GPU, and
      `/dev/shm` in a small guest is routinely too small for Chromium's
      default shared-memory use (the classic "Chromium crashes in a
      container" failure).
    * **`_QUIET_FLAGS`** — `--no-first-run`, `--no-default-browser-check`,
      `--disable-background-networking`, `--disable-extensions`,
      `--disable-component-update`, `--disable-domain-reliability`,
      `--disable-sync`, `--metrics-recording-only`,
      `--disable-client-side-phishing-detection`. These are here to keep the
      twin's **DNS census honest**, not to make the browser faster. Measured
      on dev45 2026-09-21: with only the first four, one 8-page persona cycle
      produced **976** queries for `dl.google.com` plus `edgedl.me.gvt1.com`
      and friends — Chromium's component updater and telemetry — against a
      twin resolver that is authoritative for the 613 observed names and has
      no upstream, i.e. **6,250 NXDOMAINs** that swamped the persona's own
      names in the very census the fidelity read consumes. A name the cast
      never observed, invented by the browser, is measurement pollution
      whichever way the resolver answers it.
      *Deliberately NOT here:* `--safebrowsing-disable-auto-update`. It was
      in the diagnosed list, it cannot be verified against a Chromium from
      this repo, and it has been dropped from Puppeteer's default set — its
      job (Safe Browsing's own update fetches) is background networking,
      which `--disable-background-networking` already covers. A flag that may
      no longer exist is a claim this file should not make.
    * `--window-size`, `--virtual-time-budget` — fixed viewport and a bounded
      virtual clock, so the same page renders the same way every run.
    * `--dump-dom` — loads the page (and its subresources) fully, prints the
      resulting DOM and EXITS. This is what makes a one-shot invocation a
      complete page load rather than a browser that idles forever — and,
      since 2026-09-21, it is also the **evidence the client judges success
      from** (`judge()` in the rendered client): Chromium exits 0 having
      rendered its own error page, so the DOM, not the exit code, is what
      separates a page that loaded from one that did not.
    """
    scheme = spec.scheme
    host_part = name if port == spec.default_port else f"{name}:{port}"
    url = f"{scheme}://{host_part}/"
    args = [
        "--headless=new",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        f"--user-data-dir={profile_dir}",
        *_QUIET_FLAGS,
        f"--window-size={_WINDOW_SIZE}",
        f"--virtual-time-budget={_VIRTUAL_TIME_BUDGET_MS}",
    ]
    if map_ip is not None:
        # Containment mode 1 (module docstring): resolve NOTHING in the
        # guest; every name maps, in-process, to the orchestrator-baked twin
        # address. The real name still rides in SNI and Host.
        args.append(f"{_MAP_FLAG}=MAP * {map_ip}")
    if spec.force_quic:
        args += ["--enable-quic", f"--origin-to-force-quic-on={name}:{port}"]
    if ignore_cert_errors:
        args.append("--ignore-certificate-errors")
    args += ["--dump-dom", url]
    return args


def _check_browser_args(
    persona: Persona,
    instance_id: str,
    emit: Emit,
    args: list[str],
    *,
    name: str,
    map_ip: str | None,
    ignore_cert_errors: bool,
) -> None:
    """Refuse an invocation whose containment or naming is not what the
    render intended — the `browser` counterpart of
    `command._check_containment`, checking the two properties that survive
    the move from an argv of addresses to a browser of names:

    1. **exactly one URL**, and its host is the REAL name (so SNI/Host carry
       the name, not an address, and no second endpoint hides in the argv);
    2. **the resolver mode is unambiguous** — in the default mode exactly one
       `--host-resolver-rules` mapping every name to the peer's IP LITERAL;
       in `resolve_via_dns` mode, none at all. The two must never blur.

    Plus a self-check that `--ignore-certificate-errors` appears only when
    the knob asked for it: a default-on cert bypass would hide a sink serving
    the wrong certificate, which is the one thing the sink must get right.
    """

    def fail(reason: str) -> PersonaDriverError:
        return _fail(
            persona, instance_id,
            f"the {emit.proto!r} browser invocation {reason} "
            "(docs/WORK_ORDER_protocol_engines.md §5 E4)",
        )

    urls = [a for a in args if "://" in a]
    if len(urls) != 1:
        raise fail(f"carries {len(urls)} URL tokens, expected exactly 1: {urls}")
    host = urlsplit(urls[0]).hostname or ""
    if host != name.lower():
        raise fail(
            f"addresses host {host!r} but was built for name {name!r} — the "
            "URL host is what becomes the TLS SNI and the Host header"
        )
    maps = [a for a in args if a.split("=", 1)[0] == _MAP_FLAG]
    if map_ip is None:
        if maps:
            raise fail(
                "carries a --host-resolver-rules mapping even though "
                "knobs.resolve_via_dns asked for real guest-side resolution"
            )
    else:
        if len(maps) != 1:
            raise fail(
                f"carries {len(maps)} --host-resolver-rules flags, expected "
                "exactly 1 (the default containment mode maps every name to "
                "the orchestrator-resolved peer)"
            )
        value = maps[0].split("=", 1)[1]
        expected = f"MAP * {map_ip}"
        if value != expected or not _looks_like_ip(map_ip):
            raise fail(
                f"maps names with {value!r}, expected {expected!r} with an IP "
                "literal — the peer address is resolved by the orchestrator "
                "and baked here, never resolved in the guest"
            )
    if "--ignore-certificate-errors" in args and not ignore_cert_errors:
        raise fail(
            "disables certificate validation without "
            "knobs.browser_ignore_cert_errors"
        )


def _resolve_ports(
    persona: Persona, instance_id: str, emit: Emit, spec: _BrowserProto
) -> list[int]:
    """This emit's ports, with the schema's `0`/unset placeholder falling
    back to the proto's well-known port (a browser cannot dial port 0)."""
    ports = list(emit.ports) or [0]
    return [p if p else spec.default_port for p in ports]


def build_emit_invocations(
    persona: Persona,
    instance_id: str,
    emit: Emit,
    peer_ips: list[str],
    *,
    names: list[str],
    knobs: dict[str, Any],
) -> list[list[list[str]]]:
    """`args_by_target[target][i]` — the fully-resolved Chromium arguments
    for one emit, mirroring `command.build_emit_invocations`' shape (and the
    scripted client's `peer -> message -> ports[i % len(ports)]` iteration):
    one entry per TARGET, each holding the (port x name) round-robin the
    guest walks `pages` times per firing.

    A "target" is a resolved peer IP in the default containment mode, and a
    single nameless entry in `resolve_via_dns` mode (where the emit has no
    peer dependency at all — see the module docstring).
    """
    spec = _BROWSER_PROTOS.get(emit.proto)
    if spec is None:
        raise _fail(
            persona, instance_id,
            f"emit proto {emit.proto!r} cannot be driven by a browser "
            f"(supported: {list(supported_protos())}) — use `drive: command` "
            "for a non-web protocol with a real client tool, or `drive: "
            "scripted` for connectivity shape; refusing to silently skip the "
            "emit or substitute a plausible-looking wrong client",
        )
    if emit.peer_role in ("broadcast", "multicast"):
        raise _fail(
            persona, instance_id,
            f"emit proto {emit.proto!r} uses peer_role {emit.peer_role!r}, "
            "which a browser cannot speak to — use `drive: scripted` for "
            "segment chatter",
        )
    if emit.transport and emit.transport not in spec.explicit_transports:
        # EXPLICIT only — `emit.transport` is `""` for a derived default, and
        # a derived value must never reach this check (the `command` driver's
        # code-review item 5, same rule).
        raise _fail(
            persona, instance_id,
            f"emit proto {emit.proto!r} pins transport {emit.transport!r} "
            f"explicitly, but a browser drives {emit.proto!r} over "
            f"{sorted(spec.explicit_transports)} — drop the explicit "
            "`transport:` or use the proto whose transport you meant "
            "(`quic` is the HTTP/3-over-UDP row)",
        )
    ports = _resolve_ports(persona, instance_id, emit, spec)
    profile_dir = browser_profile_dir(instance_id)
    targets: list[str | None] = (
        [None] if knobs["resolve_via_dns"] else list(peer_ips)
    )
    by_target: list[list[list[str]]] = []
    for map_ip in targets:
        per_target: list[list[str]] = []
        # Port-major so CONSECUTIVE page loads visit different names — the
        # seeded shuffle is pointless if a fire walks one name N times.
        for port in ports:
            for name in names:
                args = _build_args(
                    name=name, spec=spec, port=port, map_ip=map_ip,
                    profile_dir=profile_dir,
                    ignore_cert_errors=knobs["ignore_cert_errors"],
                )
                _check_browser_args(
                    persona, instance_id, emit, args, name=name, map_ip=map_ip,
                    ignore_cert_errors=knobs["ignore_cert_errors"],
                )
                per_target.append(args)
        by_target.append(per_target)
    return by_target


def browser_profile_dir(instance_id: str) -> str:
    """The per-instance Chromium profile directory, under the instance's own
    path (`drivers.base.instance_paths`) — created by the client at arm time,
    torn down with the instance's directory."""
    return f"{instance_paths(instance_id)['dir']}/chrome-profile"


# ---------------------------------------------------------------------------
# The rendered in-guest client
#
# Structurally the SAME loop/gate/jitter/next_fire/one-summary-line-per-fire
# machinery as `command._CLIENT_TEMPLATE`; `fire()` runs a browser instead of
# a protocol client tool, and the binary is resolved IN the guest.
# ---------------------------------------------------------------------------
_CLIENT_TEMPLATE = '''#!/usr/bin/env python3
# axRange browser persona client: {persona_id} (instance {instance_id})
import os, random, shutil, subprocess, time
from datetime import datetime

INSTANCE = {instance_id!r}
PERSONA = {persona_id!r}
LOG = {log_path!r}
SEED = {seed!r}      # None -> unseeded (non-reproducible timing)
EMITS = {emits!r}
BINARIES = {binaries!r}
PACKAGE = {package!r}
PROFILE_DIR = {profile_dir!r}
BIZ_START = {biz_start!r}
BIZ_END = {biz_end!r}
MIN_DOM_BYTES = {min_dom_bytes!r}
ERROR_MARKERS = {error_markers!r}

rng = random.Random(SEED)


def log(msg):
    with open(LOG, 'a') as f:
        f.write('[axrange] persona ' + INSTANCE + ' ' + msg + ' '
                 + datetime.now().isoformat() + chr(10))


def in_business_hours(now):
    if now.weekday() >= 5:
        return False
    minute = now.hour * 60 + now.minute
    return BIZ_START <= minute < BIZ_END


def find_browser():
    # Debian bookworm installs `chromium`; Ubuntu ships the same browser as
    # `chromium-browser`. The start-time probe already refused a guest with
    # NEITHER; this is the second guard, for a binary that disappears after
    # start (the `command` client's MISSING TOOL precedent).
    for b in BINARIES:
        found = shutil.which(b)
        if found:
            return found
    return ''


BROWSER = find_browser()


def judge(returncode, dom):
    # Decide whether a page LOADED, from the evidence -- never from the exit
    # code alone. Chromium exits 0 having rendered its own error page, so
    # `returncode == 0` says "the process ran", not "the page loaded"
    # (measured on dev45 2026-09-21: pages=8 ok=8 fails=0 while the sensor saw
    # ONE tls flow -- LES-001, the exact class of silent wrong answer this
    # platform exists to catch). Returns (verdict, reason) where verdict is
    # one of 'ok' / 'errpage' / 'empty' / 'proc', kept separate so the summary
    # line distinguishes "loaded an error" from "the process itself failed".
    if returncode != 0:
        return ('proc', 'exit=' + str(returncode))
    body = dom.strip()
    if len(body) < MIN_DOM_BYTES:
        # `--dump-dom` always prints SOMETHING for a real document; a DOM this
        # small is an empty shell, not a page.
        return ('empty', 'dom=' + str(len(body)) + 'B')
    for marker in ERROR_MARKERS:
        if marker in dom:
            # Name the network error if the page carries one -- that is the
            # difference between "the sink refused" and "the name does not
            # resolve", and it costs one scan.
            reason = marker
            at = dom.find('ERR_')
            if at >= 0:
                end = at
                while end < len(dom) and (dom[end].isupper() or dom[end].isdigit()
                                          or dom[end] == '_'):
                    end += 1
                reason = dom[at:end]
            return ('errpage', reason)
    return ('ok', '')


def clear_profile_locks():
    # Chromium refuses to start against a profile directory it believes
    # another process holds. A fetch killed by the hard timeout leaves those
    # singleton files behind, which would make EVERY later fetch fail for the
    # life of the twin -- a silent, permanent stall. Safe to remove here
    # because this client runs its fetches strictly one at a time.
    for n in ('SingletonLock', 'SingletonSocket', 'SingletonCookie'):
        try:
            os.unlink(os.path.join(PROFILE_DIR, n))
        except OSError:
            pass


def fire(emit):
    args_by_target = emit['args']
    if not args_by_target:
        log('skip ' + emit['proto'] + ' -- peer_role ' + str(emit['peer_role'])
            + ' not resolved')
        return
    if not BROWSER:
        log('skip ' + emit['proto'] + ' -- MISSING TOOL '
            + ' or '.join(BINARIES) + ' -- install ' + PACKAGE
            + ' (bake it into the image, docs/IMAGE_BUILD.md)')
        return
    try:
        os.makedirs(PROFILE_DIR, exist_ok=True)
    except OSError as e:
        log('skip ' + emit['proto'] + ' -- profile dir ' + PROFILE_DIR
            + ' unusable: ' + str(e))
        return
    ok = 0
    fails = 0
    timeouts = 0
    errpages = 0
    empties = 0
    pages = 0
    gap = emit['gap_s']
    for per_target in args_by_target:
        for i in range(emit['pages']):
            args = per_target[i % len(per_target)]
            pages += 1
            clear_profile_locks()
            try:
                # stdout is the `--dump-dom` output, and it is the EVIDENCE
                # `judge` reads -- not a diagnostic. A page DOM is a few
                # hundred KB at most here (the sink serves generic bodies),
                # so buffering it costs nothing and buys a real verdict.
                res = subprocess.run([BROWSER] + args, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL,
                                     timeout=emit['timeout_s'])
                raw = res.stdout or b''
                if not isinstance(raw, str):
                    raw = raw.decode('utf-8', 'replace')
                verdict, reason = judge(res.returncode, raw)
                if verdict == 'ok':
                    ok += 1
                else:
                    fails += 1
                    if verdict == 'errpage':
                        errpages += 1
                    elif verdict == 'empty':
                        empties += 1
                    if fails <= 3:
                        log('load ' + emit['proto'] + ' ' + args[-1]
                            + ' ' + verdict + ' ' + reason)
            except subprocess.TimeoutExpired:
                timeouts += 1
                fails += 1
                if timeouts <= 3:
                    log('load ' + emit['proto'] + ' ' + args[-1]
                        + ' timed out after ' + str(emit['timeout_s']) + 's')
            except (OSError, subprocess.SubprocessError) as e:
                fails += 1
                if fails <= 3:
                    log('load ' + emit['proto'] + ' ' + args[-1]
                        + ' failed: ' + str(e))
            if gap > 0:
                time.sleep(gap)
    # ONE summary line per fire, matching both siblings' contract. `ok=` is
    # the positive control and it now MEANS something: a page whose DOM came
    # back non-empty and was not Chromium's own error page (LES-245 -- read
    # the counter on a green run; a counter that cannot be zero proves
    # nothing). The three failure counters are kept apart because they send
    # you to three different places: `errpage=` is the network (a name, a
    # refused connection, a certificate), `empty=` is a page that rendered
    # nothing, `timeouts=`/the remainder is the process itself.
    log('browsed ' + emit['proto'] + ' browser=' + BROWSER
        + ' targets=' + str(len(args_by_target)) + ' pages=' + str(pages)
        + ' ok=' + str(ok) + ' fails=' + str(fails)
        + ' errpage=' + str(errpages) + ' empty=' + str(empties)
        + ' timeouts=' + str(timeouts))


log('armed pid=' + str(os.getpid()) + ' emits=' + str(len(EMITS))
    + ' seed=' + str(SEED) + ' browser=' + (BROWSER or '<none>'))
for _e in EMITS:
    # An ignored knob or a substituted name is announced ONCE, at arm time,
    # rather than silently applied.
    for _n in _e['notes']:
        log('note ' + _e['proto'] + ' ' + _n)
next_fire = [0.0] * len(EMITS)
while True:
    now = time.time()
    for idx, emit in enumerate(EMITS):
        if now < next_fire[idx]:
            continue
        if emit['gate'] == 'business_hours' and not in_business_hours(datetime.now()):
            next_fire[idx] = now + 60.0
            continue
        fire(emit)
        jitter = rng.uniform(-0.1, 0.1) * emit['interval_s']
        next_fire[idx] = now + emit['interval_s'] + jitter
    time.sleep(1.0)
'''


def render_browser_client(
    persona: Persona,
    *,
    instance_id: str,
    peers: dict[str, str] | None = None,
    seed: Any = None,
    schedule: str | None = None,
    peer_sets: dict[str, list[str]] | None = None,
) -> str:
    """Render the deterministic in-guest python3 client that drives a real
    headless Chromium for `persona`'s emits, bound to `instance_id`.

    Same contract as `render_command_client`: `peers`/`peer_sets` are already
    resolved by the orchestrator, an unresolved `peer_role` is not fatal (the
    guest logs a skip), and `schedule` is the already-normalized
    traffic-profile gate override.

    Raises `PersonaDriverError` — fail-loud, never a silent skip — when an
    emit's proto is not browser-drivable, uses a segment-scoped peer role,
    pins a transport a browser cannot drive for that proto, or when a knob
    (`browse_names`, `resolve_via_dns`, `browser_ignore_cert_errors`,
    `browse_timeout_s`) is malformed.
    """
    peers = peers or {}
    paths = instance_paths(instance_id)
    eff_seed = effective_seed(seed, instance_id)
    knobs = resolve_browser_knobs(persona, instance_id)
    # One rng for the whole render, seeded exactly like the guest's: the
    # browse order is therefore stable for a given (persona, instance, seed)
    # and DIFFERENT across instances, so two hosts running one persona do not
    # walk the same sites in the same order. An unseeded render keeps the
    # authored order (still deterministic, just not shuffled).
    rng = random.Random(eff_seed) if eff_seed is not None else None
    emit_configs = []
    for emit in persona.emits:
        sched = _resolve_emit_schedule(emit, schedule_override=schedule)
        peer_ips = resolve_emit_peers(emit, peers, peer_sets)
        notes: list[str] = []
        names = list(knobs["browse_names"])
        if not names:
            names = [_synthetic_name(persona, instance_id, emit)]
            notes.append(
                f"no knobs.browse_names authored -- browsing the synthesized "
                f"name {names[0]} derived from peer_role "
                f"{emit.peer_role!r} (it becomes the TLS SNI)"
            )
        elif rng is not None:
            rng.shuffle(names)
        notes.append(
            f"bytes={int(sched['bytes'])} ignored -- page weight is the "
            "server's choice, not the browser's (volume drives PAGES per "
            "firing for a browser emit)"
        )
        if knobs["resolve_via_dns"]:
            notes.append(
                "knobs.resolve_via_dns -- names are resolved IN THE GUEST; "
                "containment is the twin's DNS + sink + router egress policy, "
                "not this client"
            )
        emit_configs.append(
            {
                "proto": emit.proto,
                "peer_role": emit.peer_role,
                "peer_ips": peer_ips,
                "names": names,
                "args": build_emit_invocations(
                    persona, instance_id, emit, peer_ips,
                    names=names, knobs=knobs,
                ),
                "timeout_s": knobs["timeout_s"],
                "notes": notes,
                # `bytes` is deliberately NOT threaded into the guest config:
                # a browser cannot spend a byte budget, and carrying it would
                # imply the client still honours it.
                "pages": sched["messages"],
                "gate": sched["gate"],
                "interval_s": sched["interval_s"],
                "gap_s": sched["gap_s"],
            }
        )
    return _CLIENT_TEMPLATE.format(
        persona_id=persona.id,
        instance_id=instance_id,
        log_path=paths["log"],
        seed=eff_seed,
        emits=emit_configs,
        binaries=list(_BROWSER_BINARIES),
        package=_BROWSER_PACKAGE,
        profile_dir=browser_profile_dir(instance_id),
        biz_start=_BUSINESS_START_MIN,
        biz_end=_BUSINESS_END_MIN,
        min_dom_bytes=_MIN_DOM_BYTES,
        error_markers=_ERROR_MARKERS,
    )


def render_browser_launch(
    instance_id: str, persona: Persona, *, script_path: str
) -> str:
    """The bash bootstrap (re)starting the already-written client as a
    detached, deterministically-named transient systemd unit — NEVER
    `nohup ... &` (LES-086).

    IDENTICAL to the scripted/command drivers' bootstrap (all three clients
    are `exec python3 <script>` under the same transient unit with the same
    fail-loud `systemctl is-active` check), so this DELEGATES rather than
    duplicating it (LES-025 — reuse downward, don't reimplement). It exists
    as a named entry point so a future divergence has an obvious home."""
    return render_scripted_launch(instance_id, persona, script_path=script_path)


# A self-identifying marker in the browser probe, so a test fake (and an
# operator reading a guest-agent log) can tell this exec apart from the
# `command -v python3` probe and from the `command` driver's tool probe.
_BROWSER_PROBE_MARKER = "axr-persona-browser-probe"
_FOUND_TAG = "FOUND:"


def render_browser_probe(binaries: list[str]) -> str:
    """The shell one-liner that prints one `FOUND:<binary>`-tagged line per
    PRESENT candidate browser binary (and nothing at all when none is
    installed), always exiting 0.

    Deliberately the INVERSE of `command.render_tool_probe`'s missing-report:
    this driver needs ANY-OF semantics (`chromium` on Debian bookworm,
    `chromium-browser` on Ubuntu), not all-of, so absence is "no tagged line
    at all" rather than one line per missing name.

    The tag matters for the same reason `command`'s `MISSING:` tag does: the
    probe runs under `sh -lc`, a LOGIN shell, which sources `/etc/profile`
    and friends first — a guest image's login banner lands on the SAME stdout
    and, untagged, would be misread as a found binary."""
    names = " ".join(binaries)
    return (
        f": {_BROWSER_PROBE_MARKER}; "
        f'for b in {names}; do command -v "$b" >/dev/null 2>&1 && '
        f"printf '{_FOUND_TAG}%s\\n' \"$b\"; done; :"
    )


def _windows_unsupported(persona: Persona, instance_id: str) -> PersonaDriverError:
    """The loud, specific refusal for a Windows `drive: browser` instance.
    Deliberately NOT `unsupported_os_family_error`: `windows` IS a recognized
    os_family, so the honest message is "this driver is Linux-only", not
    "unrecognized OS". Never a silent no-op — an operator who authors a
    Windows browser persona must find out at launch, not from a twin that
    quietly emits nothing (LES-001)."""
    return PersonaDriverError(
        f"persona[{instance_id}] ({persona.id}): `drive: browser` is "
        "Linux-only — the Windows web engine is its own slice "
        "(docs/WORK_ORDER_protocol_engines.md §5 E6: PowerShell built-ins on "
        "a real domain member, launched as a Scheduled Task, with no "
        "headless-Chromium dependency at all); use `drive: scripted` for a "
        "Windows persona until it lands"
    )


class BrowserDriver(PersonaDriver):
    """Renders + launches the headless-Chromium client (above) as a detached
    transient systemd unit. Linux only."""

    def start(
        self,
        ga: Any,
        vmid: int,
        persona: Persona,
        *,
        instance_id: str,
        peers: dict[str, str] | None = None,
        seed: Any = None,
        schedule: str | None = None,
        peer_sets: dict[str, list[str]] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if persona.os_family == "linux":
            return self._start_linux(
                ga, vmid, persona, instance_id=instance_id, peers=peers,
                seed=seed, schedule=schedule, peer_sets=peer_sets,
            )
        if persona.os_family == "windows":
            raise _windows_unsupported(persona, instance_id)
        raise unsupported_os_family_error(persona, instance_id)

    def _start_linux(
        self,
        ga: Any,
        vmid: int,
        persona: Persona,
        *,
        instance_id: str,
        peers: dict[str, str] | None,
        seed: Any,
        schedule: str | None = None,
        peer_sets: dict[str, list[str]] | None = None,
    ) -> dict[str, Any]:
        # Render FIRST: every authoring error (un-drivable proto, malformed
        # browse name, bad knob) surfaces before anything is written into the
        # guest. Everything else (write, python3 check, launch) is the shared
        # Linux python3-client start sequence the scripted/command drivers
        # already use; the one thing this driver adds is the browser probe,
        # threaded in as `pre_launch` so it runs after the python3 check and
        # before the launch script is written.
        client_src = render_browser_client(
            persona, instance_id=instance_id, peers=peers, seed=seed,
            schedule=schedule, peer_sets=peer_sets,
        )
        return start_linux_python_client(
            ga, vmid, persona, instance_id, client_src,
            pre_launch=lambda: self._assert_browser_present(
                ga, vmid, persona, instance_id
            ),
        )

    def _assert_browser_present(
        self, ga: Any, vmid: int, persona: Persona, instance_id: str
    ) -> None:
        """Fail loud on a guest with no browser at all, rather than starting a
        client that would log a skip on every fire for the life of the twin
        (`CommandDriver._assert_tools_present`'s posture, work order §3.4)."""
        res = ga.exec(
            vmid,
            ["sh", "-lc", render_browser_probe(list(_BROWSER_BINARIES))],
            timeout_s=30,
        )
        if not res.ok:
            raise PersonaDriverError(
                f"persona[{instance_id}] ({persona.id}): could not probe for a "
                f"browser binary in vmid={vmid} (exit {res.exit_code}): "
                f"{(res.stderr or res.stdout)[:200]}"
            )
        # Only a `FOUND:`-tagged line counts — see `render_browser_probe`.
        found = [
            ln[len(_FOUND_TAG):].strip()
            for ln in (res.stdout or "").splitlines()
            if ln.startswith(_FOUND_TAG)
        ]
        if not found:
            raise PersonaDriverError(
                f"persona[{instance_id}] ({persona.id}): no browser binary in "
                f"vmid={vmid} (looked for "
                f"{' or '.join(_BROWSER_BINARIES)}; apt: {_BROWSER_PACKAGE}); "
                "bake it into the image (docs/IMAGE_BUILD.md, "
                "docs/WORK_ORDER_protocol_engines.md §5 E7) rather than adding "
                "an `install:` action to a standing twin"
            )

    def stop(self, ga: Any, vmid: int, persona: Persona, *, instance_id: str) -> dict[str, Any]:
        if persona.os_family == "linux":
            return stop_unit(ga, vmid, instance_id)
        if persona.os_family == "windows":
            raise _windows_unsupported(persona, instance_id)
        raise unsupported_os_family_error(persona, instance_id)

    def heartbeat(
        self, ga: Any, vmid: int, persona: Persona, *, instance_id: str
    ) -> dict[str, Any]:
        if persona.os_family == "linux":
            return heartbeat_unit(ga, vmid, instance_id)
        if persona.os_family == "windows":
            raise _windows_unsupported(persona, instance_id)
        raise unsupported_os_family_error(persona, instance_id)
