"""Twin recipe shell hygiene: no backtick in an UNQUOTED heredoc body.

An unquoted delimiter (`<<EOF`, not `<<'EOF'`) expands `$VAR` -- usually the
reason it is unquoted -- and ALSO runs every `...` in the body as a command.
A comment that quoted a tool name in backticks therefore ran it: the shared
sink recipe's nftables heredoc said `tcpdump`, so every apply ran tcpdump
in-guest, forever, and the corp-twin live run 1 (2026-09-26) spent the whole
600 s run-script budget there (docs/WORK_ORDER_corp_twin.md §11.D).

Escaped backticks (\\`) are literal and fine.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CATALOG = REPO.parent / "catalog"  # twin content lives at the project root

#: Generated sink payloads that embed an OLDER copy of the shared recipe and
#: are rebuilt by their twin's own `build-sink-payload.sh`. svc-engines' is
#: left as built for its live deploy; rebuild it before its next deploy.
_KNOWN_STALE = {"twins/svc-engines/scripts/provision-sink.sh"}

_OPEN = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")


def _recipes() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "twins/**.sh", "target-environments/**.sh"],
        cwd=CATALOG, capture_output=True, text=True, check=True,
    ).stdout.split()
    return sorted(p for p in out if p not in _KNOWN_STALE)


def unquoted_heredoc_backticks(text: str) -> list[tuple[int, str]]:
    """(line number, line) of every unescaped backtick in an unquoted heredoc."""
    lines = text.splitlines()
    hits: list[tuple[int, str]] = []
    i = 0
    while i < len(lines):
        m = _OPEN.search(lines[i])
        if m and not lines[i].lstrip().startswith("#"):
            quoted, tag = bool(m.group(1)), m.group(2)
            j = i + 1
            while j < len(lines) and lines[j].strip() != tag:
                j += 1
            if not quoted:
                for k in range(i + 1, j):
                    if re.search(r"(?<!\\)`", lines[k]):
                        hits.append((k + 1, lines[k].strip()))
            i = j
        i += 1
    return hits


def test_the_detector_finds_the_live_run_1_shape():
    bad = 'cat > f <<EOF\n# while `tcpdump` shows the SYN\nEOF\n'
    ok = "cat > f <<'EOF'\n# while `tcpdump` shows\nEOF\ncat > g <<EOF\n# \\`x\\` $V\nEOF\n"
    assert unquoted_heredoc_backticks(bad) == [(2, "# while `tcpdump` shows the SYN")]
    assert unquoted_heredoc_backticks(ok) == []


@pytest.mark.parametrize("path", _recipes())
def test_no_backtick_runs_inside_an_unquoted_heredoc(path):
    hits = unquoted_heredoc_backticks((CATALOG / path).read_text(errors="replace"))
    assert not hits, f"{path}: backtick command substitution in a heredoc body: {hits}"


def test_corp_twin_sink_payload_embeds_the_current_shared_recipe():
    """provision-sink.sh is GENERATED (corpus + shared serve-sink.sh); a stale
    one ships a fixed bug straight back to the guest."""
    shared = (CATALOG / "twins/_shared/scripts/serve-sink.sh").read_text()
    payload = (CATALOG / "twins/corp-twin/scripts/provision-sink.sh").read_text()
    assert payload.endswith(shared), (
        "twins/corp-twin/scripts/provision-sink.sh is stale -- run "
        "sh twins/corp-twin/scripts/build-sink-payload.sh")


def test_sink_quiesces_apt_daily_before_its_first_package_step():
    """Live run 1: apt-daily-upgrade fired mid-provision, restarted QGA and held
    the dpkg lock (apt-get exit 100)."""
    text = (CATALOG / "twins/_shared/scripts/serve-sink.sh").read_text()
    quiesce = re.search(r"^axr_apt_quiesce$", text, re.M)
    first_pkg = re.search(r"^axr_pkg ", text, re.M)
    assert quiesce and first_pkg and quiesce.start() < first_pkg.start()
    assert "apt-daily-upgrade.timer" in text
    calls = [ln for ln in text.splitlines()
             if "apt-get " in ln and not ln.lstrip().startswith("#")]
    assert calls
    for call in calls:
        assert "DPkg::Lock::Timeout" in call, call
