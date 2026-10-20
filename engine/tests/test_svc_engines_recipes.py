"""Tests for the shipped twin RECIPES -- the `run-script` payloads under
`twins/*/scripts/`.

A recipe is shell, so most of what it does is only provable on a cluster. What
is provable here has, every item of it, already cost a live deploy:

  * **A recipe installs the packages that provide the binaries it runs.** The
    `files` host's op failed on dev45 (2026-09-21) because `samba` +
    `samba-common-bin` do not ship `/usr/bin/smbclient` (its own package) and
    `snmpd` does not ship `snmpget` (the `snmp` package). The self-check then
    reported "anonymous SMB session was refused" about a server that was fine.
  * **...and every MECHANISM it uses** — the same rule where `command -v`
    cannot reach. `ldapsearch -Y GSSAPI` dlopen's a SASL plugin that ships no
    binary and that `ldap-utils` does not depend on; without it the bind fails
    with `no mechanism available`, which accuses the DC (dev45, 2026-09-21).
  * **The Samba config carries the settings that make ANONYMOUS access work**,
    which is what the file-client persona uses.
  * **The recipe and the personas that hit it agree** on share, file,
    community — and on the accounts a persona logs in as. The ssh engine ran
    as a user that existed nowhere for a whole round (dev45, 2026-09-21).
  * **Package installation precedes the DNS repoint.** A twin resolver serves
    only observed names, so an `apt-get install` after the repoint fails with
    `Could not resolve 'deb.debian.org'` — correct measure-phase behaviour
    arriving mid-provisioning.

`sh -n` on every shipped recipe rides along: it is free, and one of these files
is generated (`provision-sink.sh`).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
CATALOG = REPO.parent / "catalog"  # twin content lives at the project root
TWINS = CATALOG / "twins"
FILE_SERVER = TWINS / "svc-engines" / "scripts" / "serve-file-server.sh"
ENGINE_CLIENT = TWINS / "svc-engines" / "scripts" / "prepare-engine-client.sh"
TWIN_CONFIG = TWINS / "svc-engines" / "te.yaml"
PERSONAS = TWINS / "svc-engines" / "personas"

#: Binaries a recipe may invoke that are NOT in a Debian cloud image, mapped to
#: the package(s) that provide them. `apt` Depends count: `bind9` pulls
#: `bind9-utils`, which is where `named-checkzone` lives.
_PROVIDED_BY: dict[str, set[str]] = {
    "smbclient": {"smbclient"},
    "rpcclient": {"smbclient"},
    "snmpget": {"snmp"},
    "snmpwalk": {"snmp"},
    "dig": {"bind9-dnsutils", "dnsutils"},
    "named": {"bind9"},
    "named-checkzone": {"bind9", "bind9-utils"},
    "named-checkconf": {"bind9", "bind9-utils"},
    "kinit": {"krb5-user"},
    "ldapsearch": {"ldap-utils"},
    "nginx": {"nginx"},
    "caddy": {"caddy"},
    "socat": {"socat"},
    "nft": {"nftables"},
    "chronyd": {"chrony"},
    "chronyc": {"chrony"},
    "smbpasswd": {"samba-common-bin", "samba"},
    "testparm": {"samba-common-bin", "samba"},
}

#: Start-of-command positions in a shell line.
_CMD_AT = r"(?:^|[;|&(]|\|\||&&|\$\()\s*"


#: The HAND-WRITTEN recipe folders (what `twin-configs/*/scripts/` held before
#: the consolidation) -- not every `twins/*/scripts/`: a cast's scripts are
#: aximprint's generated output and a twin's copies of them, not recipes this
#: file's rules were written for.
_RECIPE_TWINS = ("_shared", "svc-engines", "svc-verify")


def recipes() -> list[Path]:
    found = sorted(p for t in _RECIPE_TWINS for p in (TWINS / t).glob("scripts/*.sh"))
    assert found, f"no recipes under {TWINS} -- moved? (an empty set would skip every test)"
    return found


def _code(text: str) -> str:
    """The script minus whole-line comments -- prose naming a tool is not a use."""
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))


def _installed(code: str) -> set[str]:
    pkgs: set[str] = set()
    for line in code.splitlines():
        m = re.match(r"\s*axr_pkg\s+(.*)", line)
        if m:
            pkgs.update(m.group(1).split())
        m = re.search(r"apt-get install\s+(.*)", line)
        if m:
            pkgs.update(
                w
                for w in m.group(1).split()
                if re.fullmatch(r"[a-z0-9][a-z0-9.+-]*", w)
            )
    # ...plus a `binary:package` table (`prepare-engine-client.sh`'s AXR_TOOLS),
    # which installs by iterating the pairs rather than by a literal list.
    for binary in _PROVIDED_BY:
        pkgs.update(re.findall(rf"\b{re.escape(binary)}:([a-z0-9][a-z0-9.+-]*)", code))
    return pkgs


@pytest.mark.parametrize("recipe", recipes(), ids=lambda p: p.name)
def test_every_shipped_recipe_is_valid_posix_shell(recipe: Path):
    proc = subprocess.run(["sh", "-n", str(recipe)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("recipe", recipes(), ids=lambda p: p.name)
def test_a_recipe_installs_what_it_runs(recipe: Path):
    """The class of bug that failed `run-script[files-app@files]`: a self-check
    invoking a binary no installed package provides. `command -v X || die` is
    exempt -- that IS the check for a binary the recipe expects to be present."""
    code = _code(recipe.read_text())
    if "axr_pkg" not in code and "apt-get install" not in code:
        pytest.skip("not a packaging recipe")
    installed = _installed(code)
    for binary, providers in _PROVIDED_BY.items():
        uses = re.search(_CMD_AT + re.escape(binary) + r"\b", code, re.M)
        if not uses or re.search(r"command -v " + re.escape(binary), code):
            continue
        assert installed & providers, (
            f"{recipe.name} runs `{binary}` but installs none of "
            f"{sorted(providers)} (installs: {sorted(installed)})"
        )


# ------------------------------------------------------------- guest SMB access


def _smb_conf() -> str:
    """The smb.conf the file-server recipe writes, as rendered text."""
    text = FILE_SERVER.read_text()
    body = text.split("cat > /etc/samba/smb.conf <<AXR_SMB_EOF\n", 1)[1]
    return body.split("\nAXR_SMB_EOF", 1)[0]


@pytest.mark.parametrize(
    "setting",
    [
        "security = user",
        "map to guest = Bad User",
        "guest account = nobody",
        "restrict anonymous = 0",
        "guest ok = yes",
    ],
)
def test_the_samba_config_enables_anonymous_access(setting: str):
    """Debian's Samba refuses an anonymous session unless all of these agree;
    the file-client persona authenticates with nothing at all
    (`auth_credential: none` -> `smbclient -N`), so a refusal is the whole
    emit lost."""
    assert setting in _smb_conf()


@pytest.mark.parametrize("setting", ["server signing = auto", "smb encrypt = off"])
def test_the_samba_config_does_not_require_what_a_guest_cannot_do(setting: str):
    """A guest session has no session key: a server that REQUIRES signing or
    encryption refuses anonymous access, and the client sees an
    indistinguishable ACCESS_DENIED. (`smb encrypt = off` also keeps SMB
    readable at the sensor, which is this twin's point.)"""
    assert setting in _smb_conf()


def test_the_file_server_self_checks_show_the_tools_own_error():
    """The check did its job by failing; it lied about WHY. No client check may
    send its stderr to /dev/null again."""
    code = _code(FILE_SERVER.read_text())
    for line in code.splitlines():
        if re.search(_CMD_AT + r"(smbclient|snmpget)\b", line):
            assert "/dev/null" not in line, line
    assert "2>&1" in code  # the output is captured...
    assert re.search(r"printf '%s\\n' \"\$_out\"", code)  # ...and printed


def test_the_file_server_failure_messages_name_where_to_look():
    text = FILE_SERVER.read_text()
    for hint in ("/etc/samba/smb.conf", "/var/log/samba/log.", "smbclient' package"):
        assert hint in text


# -------------------------------------------------- recipe <-> persona agreement


def _personas() -> list[dict]:
    return [yaml.safe_load(p.read_text()) for p in sorted(PERSONAS.glob("*.yaml"))]


def _knobs_for(peer_role: str, proto: str) -> dict:
    for doc in _personas():
        spec = doc.get("persona", doc)
        for emit in spec.get("emits", []):
            if emit.get("peer_role") == peer_role and emit.get("proto") == proto:
                return spec.get("knobs", {})
    raise AssertionError(f"no persona emits {proto} at {peer_role}")


def test_the_share_and_file_the_persona_reads_are_the_ones_the_recipe_serves():
    """`knobs.smb_share`/`smb_file` are opaque strings to the driver: a typo
    here deploys green and then fails every firing with NT_STATUS_NO_SUCH_FILE."""
    knobs = _knobs_for("file-server", "smb")
    recipe = FILE_SERVER.read_text()
    assert f"[{knobs['smb_share']}]" in recipe
    directory, _, name = knobs["smb_file"].rpartition("/")
    assert f"pub/{directory}" in recipe
    # `bulk-01.dat` comes from the `bulk-0$_i.dat` loop; index 1 always exists.
    stem = re.sub(r"0*\d+(?=\.)", r"0$_i", name)
    assert stem in recipe
    assert int(re.search(r"AXR_BULK_COUNT=(\d+)", recipe).group(1)) >= 1
    # anonymous, so the share it reads has to be the guest-ok one
    assert knobs["auth_credential"] == "none"
    assert "guest ok = yes" in _smb_conf()


def test_the_snmp_community_the_persona_uses_is_the_one_the_agent_answers_to():
    knobs = _knobs_for("file-server", "snmp")
    recipe = FILE_SERVER.read_text()
    assert f"AXR_SNMP_COMMUNITY={knobs['snmp_community_source']}" in recipe
    assert "rocommunity $AXR_SNMP_COMMUNITY" in recipe


def test_the_local_samba_password_is_the_lab_default():
    """The recipe cannot read `adapter.conf`, so the one lab-wide secret is
    duplicated here as a literal. If they ever diverge, a persona authoring
    `auth_credential: default` against `corp` fails at run time only."""
    from axrange.adapter_proxmox.config import AdapterConfig

    assert f"AXR_SMB_PASS='{AdapterConfig().default_password}'" in FILE_SERVER.read_text()


# ------------------------------------------- the CLIENT host's package census
#
# The same rule the `_PROVIDED_BY` test above enforces for a server recipe --
# *a recipe must install the package that provides every binary it runs* --
# applied to the engine CLIENT, and extended by the case that rule could not
# see: a **mechanism**. `ldapsearch -Y GSSAPI` dlopen's a SASL plugin that
# ships no binary, so no `command -v` probe can ever catch it missing; on
# dev45 (2026-09-21) its absence read as `Unknown authentication method (-6)
# ... no mechanism available`, i.e. as the DC refusing GSSAPI.
#
# The authority here is the driver's OWN table (`required_packages`, LES-223),
# never a list typed twice.

#: Packages `required_packages` names that the client's `install:` list
#: deliberately does NOT carry, each with the reason. Anything else missing is
#: a failure.
_CLIENT_PKG_EXEMPT = {
    # Debian renamed the package that ships `ntpdate` across the ntp->ntpsec
    # transition, and a `pkg:` naming the wrong one FAILS the deploy; step 0
    # of `prepare-engine-client.sh` tries both and then asserts the binary.
    "ntpdate",
    # in the base cloud image
    "iputils-ping",
}


def _command_personas():
    out = []
    for p in sorted(PERSONAS.glob("*.yaml")):
        spec = yaml.safe_load(p.read_text())
        spec = spec.get("persona", spec)
        if spec.get("drive") == "command":
            out.append((p.name, spec))
    return out


def _client_installed_packages() -> set[str]:
    """Every `pkg:` the twin config installs on the engine-client node."""
    doc = yaml.safe_load(TWIN_CONFIG.read_text())
    spec = doc.get("twin_config", doc)
    for node in spec.get("nodes", []):
        if node.get("role") == "engine-client":
            return {
                a["pkg"] for a in node.get("install", []) if "pkg" in a
            }
    raise AssertionError("svc-engines.yaml has no engine-client node")


def test_the_client_installs_every_package_its_personas_engines_need():
    """Derived from `command.required_packages()`, so a template that grows a
    dependency cannot land without this twin noticing."""
    from axrange.personas.drivers.command import required_packages
    from axrange.personas.schema import load_persona

    installed = _client_installed_packages()
    for name, spec in _command_personas():
        for pkg in required_packages(load_persona(spec)):
            assert pkg in installed or pkg in _CLIENT_PKG_EXEMPT, (
                f"{name} needs the '{pkg}' package and svc-engines.yaml's "
                f"engine-client installs none of it (installs: "
                f"{sorted(installed)})"
            )


def test_the_client_censuses_the_sasl_mechanism_no_binary_probe_can_catch():
    """`ldap-utils` does not pull the GSSAPI mech plugin and the plugin ships
    no binary, so `AXR_TOOLS` (a `command -v` census) is structurally blind to
    it. `AXR_MECHS` is the second census that is not."""
    code = _code(ENGINE_CLIENT.read_text())
    assert "AXR_MECHS=" in code
    assert "libgssapiv2.so" in code
    assert "libsasl2-modules-gssapi-mit" in code
    # ...and the mech census is actually consulted, not merely declared.
    assert re.search(r"for\s+_pair\s+in\s+\$AXR_MECHS", code)


def test_the_client_censuses_the_credential_helper_the_ssh_engine_needs():
    """`sshpass` is argv[0] of an authenticated ssh firing, and it is in no
    base image."""
    code = _code(ENGINE_CLIENT.read_text())
    assert "sshpass:sshpass" in code


def test_the_engine_client_repoints_dns_after_everything_that_can_install():
    """Measured on dev45 2026-09-21: once resolv.conf points at the twin
    resolver (which serves only observed names and has no upstream),
    `apt-get install` fails with `Could not resolve 'deb.debian.org'`. Correct
    measure-phase behaviour, fatal during provisioning -- so the recipe must
    install everything FIRST and repoint LAST."""
    code = _code(ENGINE_CLIENT.read_text())
    repoint = code.index("> /etc/resolv.conf")
    last_apt = max(
        m.start() for m in re.finditer(r"apt-get install", code)
    )
    assert last_apt < repoint, (
        "prepare-engine-client.sh installs a package AFTER repointing DNS at "
        "the twin resolver -- that apt cannot resolve deb.debian.org"
    )


def test_the_krb5_conf_maps_the_domain_to_its_realm():
    """Without `[domain_realm]`, any tool that maps host->realm by DNS (and a
    twin has no `_kerberos` SRV path it is allowed to use, `dns_lookup_realm =
    false`) falls back to guessing from the hostname. Both spellings -- the
    dotted-prefix form for subdomains and the bare domain -- are required, and
    both are DERIVED from the script's own realm/domain variables."""
    text = ENGINE_CLIENT.read_text()
    krb5 = text.split("cat > /etc/krb5.conf <<AXR_KRB5_EOF\n", 1)[1]
    krb5 = krb5.split("\nAXR_KRB5_EOF", 1)[0]
    assert "[domain_realm]" in krb5
    assert ".$AXR_DOMAIN = $AXR_REALM" in krb5
    assert "$AXR_DOMAIN = $AXR_REALM" in krb5
    # derived, not a second copy of the realm string
    assert "CORP.AXR.LAB" not in krb5


def test_the_file_server_provisions_the_account_the_ssh_engine_logs_in_as():
    """dev45 2026-09-21: the ssh engine ran as `axr` against a host where no
    such user existed -- `Permission denied (publickey,password)`, exit 255,
    every cycle. The account, its password and sshd's password-auth setting
    are now all the recipe's job, and all three are asserted in-guest."""
    knobs = _knobs_for("file-server", "ssh")
    recipe = FILE_SERVER.read_text()
    assert f"AXR_SSH_USER={knobs['ssh_user']}" in recipe
    code = _code(recipe)
    assert 'adduser --quiet --disabled-password' in code
    assert "chpasswd" in code          # the password the persona authenticates with
    assert "passwordauthentication" in code   # sshd -T, asserted not assumed
    # the persona must have a credential at all -- `auth_credential: none`
    # would render the old key-only BatchMode argv again
    assert knobs.get("auth_credential", "default") != "none"
