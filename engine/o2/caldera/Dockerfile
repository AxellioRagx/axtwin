# axRange Caldera server build (A3 Stage B — docs/DESIGN_BRIEF_o2_attack_defense.md §3,
# docs/WORK_ORDER_attack_defense.md §3).
#
# MITRE Caldera ships no published Docker Hub image; the upstream project's own documented
# path is to `git clone --recursive` (the UI plugin `magma` + the default plugin set are git
# submodules — a non-recursive clone silently ships a broken/UI-less server) and build from
# source. This Dockerfile reproduces that path, pinned to CALDERA_VERSION (a git tag — never
# `latest`), so `docker compose build caldera` is the one command that reproduces a known-good
# image. Same "author + validate; live bring-up is human-gated" posture as the rest of this
# stack — see ../README.md "Attack/defense servers".
#
# AUTHORED + NOT BUILT HERE (this box has no network egress at author time, same posture
# `../README.md` "Validation" already documents for docker/promtool/alloy). Build + smoke-test
# it at live bring-up:
#   docker compose --env-file ../observability.conf -f ../compose/docker-compose.yml build caldera
#
# LOAD-BEARING: axrange/config_exec/render.py's render_sandcat_agent() points Sandcat at
# `http://<server>:8888` (plain HTTP, no scheme given) — so this image MUST run Caldera's HTTP
# contact channel in --insecure (no self-signed-TLS) mode to match, or every Sandcat agent's
# beacon fails TLS verification against the self-signed cert. Do not drop --insecure below
# without updating that renderer's origin scheme to https:// in lockstep.

ARG CALDERA_VERSION=5.0.0

# Single-stage: git/build-essential DO end up in the final runtime image (a couple
# hundred MB of build tooling alongside Caldera itself) -- traded deliberately for
# simplicity here (no wheel/artifact copy-between-stages plumbing to get wrong on a
# component that's authored-not-built on this box). Revisit as a real 2-stage build
# if image size becomes a live-bring-up concern.
FROM python:3.11-slim

ARG CALDERA_VERSION

RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends git build-essential nodejs npm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /usr/src/app

# --depth 1 on a pinned tag keeps the clone small; --recursive (+ --shallow-submodules,
# same rationale) is required (submodule plugins, see header). If CALDERA_VERSION ever
# needs a non-tag ref, drop --depth 1/--shallow-submodules.
#
# UNVERIFIED (no network egress on the author box, per this stack's usual "author +
# validate; live bring-up is human-gated" posture): confirm CALDERA_VERSION=5.0.0 is
# the correct upstream tag SPELLING before the first live build --
#   git ls-remote --tags https://github.com/mitre/caldera.git | grep -i 5.0.0
# -- MITRE's tag naming has varied release-to-release (some are bare "4.2.0", others
# carry a "v" prefix); a mismatched ARG fails this RUN loudly (git exits non-zero on
# an unknown --branch), so worst case is a build failure, not a silent wrong checkout.
RUN git clone --recursive --shallow-submodules --branch "${CALDERA_VERSION}" --depth 1 \
        https://github.com/mitre/caldera.git . \
    && pip install --no-cache-dir -r requirements.txt

# GAP-016 fix -- the real root cause of "every HTTP request returns a bare aiohttp 500,
# no traceback" (diagnosed LIVE 2026-07-15, in-container, on this exact image; see
# docs/PRODUCT_GAPS.md GAP-016 + docs/WORK_ORDER_o2_server_stack.md §3.3). The earlier
# apispec/webargs-version-skew hypothesis is DISPROVEN -- apispec 6.10.0 / webargs 8.7.1
# serve fine. The actual cause: aiohttp==3.9.3's BaseRequest.url passes the raw
# `Host: <host>:<port>` header straight into yarl.URL.build(host=...), and yarl>=1.13
# rejects any host containing ":" (`ValueError: Host 'localhost:8888' cannot contain
# ':'`) -- on EVERY request, before routing/middleware ever runs. requirements.txt pins
# aiohttp but NOT yarl, so yarl resolved to 1.24.2, an unpinned transitive. Pinning it
# back to 1.9.4 (pre-1.13, validated end-to-end in the built image: full boot + GET /,
# /api/docs/swagger.json, and several /api/v2/* routes all returned 200) is a
# 5.0.0-specific patch -- upstream Caldera 5.3.0 (aiohttp 3.10.11) fixes this at the
# source by switching BaseRequest.url to build via `authority=` instead of `host=`, so
# this RUN can be dropped if CALDERA_VERSION ever moves to 5.3.0+.
RUN pip install --no-cache-dir yarl==1.9.4

# The magma plugin (Caldera's web UI, a git submodule) ships SOURCE only --
# app/api/rest_api.py unconditionally serves plugins/magma/dist/assets/ at
# startup, so an unbuilt magma crashes the server on EVERY boot with
# ValueError: No directory exists at '.../plugins/magma/dist/assets'
# (caught live 2026-07-13, first real bring-up -- this Dockerfile previously
# only cloned + pip-installed, never built the UI). `npm ci` (falling back to
# `npm install` for an out-of-sync lockfile) + `npm run build` (vite) produces
# dist/assets/ before the image is committed.
RUN cd plugins/magma \
    && (npm ci || npm install) \
    && npm run build

# §3.3-step-3 build-time smoke gate (docs/WORK_ORDER_o2_server_stack.md §3.3, GAP-016).
# Boots the real server in the background and asserts a real HTTP request does NOT come
# back 500, so a regression to the yarl bug above (or anything else that breaks every
# request at the aiohttp layer) fails the `docker build` itself instead of surfacing
# later at A3 live-verify time.
#
# - No curl/wget in this base image (LES-064) -- probe with python3 urllib, which is
#   guaranteed present.
# - Probe GET / , NOT /api/v2/health -- unauthenticated /api/v2/health 500s on Caldera
#   5.0.0 for an UNRELATED reason (an upstream IndexError on anonymous access). / is the
#   anonymous-safe route (serves the login page, 200). Do not "fix" this to /health.
# - Caldera takes a while to reach "All systems ready" -- poll in a retry loop rather
#   than a single fixed sleep. Connection-refused/URLError means "still booting" (keep
#   retrying); an HTTP 500 means the real bug is back (fail immediately, non-zero exit);
#   any other response (200 etc.) is success.
RUN python3 server.py --insecure & \
    SERVER_PID=$! && \
    python3 - <<'PY' ; STATUS=$? ; kill "$SERVER_PID" 2>/dev/null ; wait "$SERVER_PID" 2>/dev/null ; exit $STATUS
import sys
import time
import urllib.error
import urllib.request

url = "http://localhost:8888/"
deadline = time.time() + 90  # up to ~90s for "All systems ready"

while time.time() < deadline:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            print(f"caldera build smoke gate: {url} -> {resp.status} (OK)")
            sys.exit(0)
    except urllib.error.HTTPError as e:
        if e.code == 500:
            print(f"caldera build smoke gate: {url} -> 500 -- the real bug is back", file=sys.stderr)
            sys.exit(1)
        # Any other HTTP error status is not the failure this gate guards against.
        print(f"caldera build smoke gate: {url} -> {e.code} (non-500, OK)")
        sys.exit(0)
    except urllib.error.URLError:
        # Connection refused / not listening yet -- still booting, keep retrying.
        time.sleep(3)
        continue

print("caldera build smoke gate: server never became reachable within 90s", file=sys.stderr)
sys.exit(1)
PY

# 8888/tcp: web UI + the default HTTP contact channel Sandcat beacons over (see header).
# 7010/tcp, 7011/udp, 7012/tcp: optional TCP/UDP/websocket contact channels (unused by the
# default Sandcat install this stack renders, exposed for parity with upstream defaults).
EXPOSE 8888 7010 7011/udp 7012

VOLUME ["/usr/src/app/data"]

ENTRYPOINT ["python3", "server.py"]
# --insecure: plain HTTP, matching render_sandcat_agent's http:// origin (see header).
CMD ["--insecure"]
