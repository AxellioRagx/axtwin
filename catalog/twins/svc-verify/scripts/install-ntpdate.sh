#!/bin/sh
# Install the `ntpdate` BINARY on the probe client, whatever Debian calls the
# package this release.
#
# **Why this is a script and not a `{pkg: ntpdate}` action.** The `drive:
# command` driver's `ntp` template shells out to `ntpdate`
# (`axrange/personas/drivers/command.py`, `package="ntpdate"`), and Debian
# renamed the package that ships that binary somewhere around the ntp ->
# ntpsec transition: `ntpdate` on bullseye, `ntpsec-ntpdate` on bookworm.
# A plain `{pkg: <wrong-name>}` action does not degrade -- Ansible's apt
# module fails the task, `provision-app` fails the op, and the whole deploy
# halts on a package NAME, in the middle of a gated live-verify that costs
# two Windows VMs and a domain promotion to reach. This tries both and
# asserts the outcome that actually matters: `ntpdate` on PATH.
#
# It is deliberately NARROW -- every other client tool (`dnsutils`, `curl`,
# `ldap-utils`, `smbclient`, `krb5-user`) stays a plain `pkg:` action, where
# it is visible in the op row and in `te info`. Only the ambiguous one is
# wrapped.
#
# THE REAL FIX IS AN IMAGE BAKE (GAP-056, D3 (b)): `dnsutils`/`curl`/
# `ntpdate` belong in `debian-12-cloud`'s `build.provision:`, not in a
# standing twin's install list -- adding them to a STANDING twin trips
# `--update`'s mutation refusal. This bypass is legitimate here only because
# svc-verify is a throwaway that is deployed once and destroyed.
#
# Delivered as `install: [{script: ...}]` -> `OP-APP-030 run-script`, run
# IN-GUEST as root, honouring this shebang.
set -eu

# A cloned guest's apt index is stale by construction (the same refresh
# axRange's own `provision-app` path does since 2026-08-14, and every
# `serve-*.sh` recipe repeats).
DEBIAN_FRONTEND=noninteractive apt-get update -qq

if command -v ntpdate >/dev/null 2>&1; then
    echo "install-ntpdate: already present at $(command -v ntpdate)"
    exit 0
fi

for pkg in ntpsec-ntpdate ntpdate; do
    if DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
            --no-install-recommends "$pkg" >/dev/null 2>&1; then
        echo "install-ntpdate: installed package '$pkg'"
        break
    fi
    echo "install-ntpdate: package '$pkg' not installable here, trying next" >&2
done

# FAIL LOUD (LES-001): a client that logs `command not found` on every fire
# for the life of the twin is worse than an op that failed and said so. The
# binary lands in /usr/sbin, which is on root's PATH but not on every
# non-login PATH -- check both, and say which.
if command -v ntpdate >/dev/null 2>&1; then
    echo "install-ntpdate: ntpdate ready at $(command -v ntpdate)"
    ntpdate -q -u -p 1 -t 1 127.0.0.1 >/dev/null 2>&1 || true   # smoke the argv only
    exit 0
fi
if [ -x /usr/sbin/ntpdate ]; then
    echo "install-ntpdate: /usr/sbin/ntpdate exists but is not on PATH ($PATH)" >&2
    exit 1
fi
echo "install-ntpdate: neither 'ntpsec-ntpdate' nor 'ntpdate' provided an" >&2
echo "  ntpdate binary -- the persona's ntp emit and the README's" >&2
echo "  'ntpdate -q' probe cannot run. Check the release's package name." >&2
exit 1
