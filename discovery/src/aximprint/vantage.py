"""The vantage manifest — **where the tap sat**, written beside the cold tier.

`docs/WORK_ORDER_visibility_and_vantage.md` D1/D2, build step 1. One file format, shared by the
only two layers that may ever touch it: `acquire/harvest` **writes** it next to the Zeek logs it
produced, and `ingest/zeek/sources.py` **reads** it back. Nothing else imports this.

**Why a module rather than two copies of a dict literal.** `acquire` and `ingest` are siblings that
must not import each other (CONVENTIONS §3), and the obvious way to satisfy that rule here is for
each side to spell the same JSON by hand. That is `LES-005`'s failure exactly — a format is a
contract with two ends, and a change to one end is a change to both. This module sits *below* both,
like `provenance.py`: it imports nothing from `aximprint`, so importing it can never create an
upward edge, and `tests/test_acquire_layering.py` classifies it accordingly.

**What a vantage IS, and what it is not.** It is the identity of the *tap point* — which appliance,
which capture streams — not of the capture, the directory, or the network. Three things follow, and
each is a failure mode the comparator has today (D1):

  * `ir_pit.source` is a filesystem path. Re-harvesting **one** tap into a new directory changes it,
    and the comparator refuses a pair it should accept.
  * Two **different** taps writing to the same path share a source, and the comparator accepts a
    pair it should refuse — reporting their view difference as network drift.
  * A vantage is metadata that was **thrown away**, not an inference problem. `acquire` knows the
    appliance and the streams; before this module nothing carried them past the disk boundary, and
    no model can recover what nobody wrote down (D8).

**`None` means unknown and is never a default string** (`LES-006`). A `--from-pcaps` harvest has no
appliance to name, so it writes a manifest whose `vantage` is null — a real state, distinct from
both "tap A" and "there is no manifest here at all". The operator supplies one with
`aximprint ingest --vantage` if they know it; nothing guesses.

**Deliberately no wall-clock field.** Every value here is derived from the capture or the run's own
parameters, so a manifest is a pure function of what was harvested — this file is read by `ingest`,
and anything `ingest` reads can reach an output (`LES-002`). `manifest.jsonl`, the *other* manifest
`acquire` writes, does carry timestamps; it is a resume ledger nothing downstream reads.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

__all__ = [
    "VANTAGE_MANIFEST",
    "Vantage",
    "VantageError",
    "read_vantage",
    "vantage_id",
    "write_vantage",
]

#: Written into the directory `ingest --source` is pointed at — the cold-tier root, beside the
#: per-window log directories rather than inside one of them. A sibling JSON file is invisible to
#: `LogDirSource`'s window-tree gate, which validates *directory* names only, so adding this file
#: cannot change how a tree is read.
VANTAGE_MANIFEST = "vantage.json"


class VantageError(ValueError):
    """A vantage manifest exists and cannot be trusted.

    Raised rather than degraded to `None`, because the two are opposite findings: `None` means
    nobody recorded a vantage, while an unparseable manifest means somebody did and this build
    cannot read it. Silently reading the second as the first would file a capture under "unknown
    tap" while its identity sat on disk one line away (CONVENTIONS §3, fail loud/fail closed).
    """


@dataclass(frozen=True)
class Vantage:
    """One tap point's identity, plus the provenance that justifies it.

    `vantage` is the only field anything downstream compares — it reaches `runs.vantage` and then
    `ir_pit.vantage`. The rest is the evidence behind it, kept on disk so a human can audit the
    identity rather than trust it.
    """

    #: The tap identity, or `None` when nothing could name it. `vantage_id()` derives it.
    vantage: Optional[str]
    #: The appliance host the pcaps came from. `None` for a `--from-pcaps` run.
    appliance: Optional[str]
    #: The stream spec captured (`"1-4"`). `None` for a `--from-pcaps` run.
    streams: Optional[str]
    #: How this cold tier was produced — `"harvest"` or `"harvest --from-pcaps"`. Free-form
    #: provenance, never parsed by a consumer.
    produced_by: str
    #: The pcap source a `--from-pcaps` run read, or `None`.
    pcap_source: Optional[str] = None
    #: Capture extent as the harvester understood it, epoch seconds. Advisory: the authoritative
    #: extent is the rows' own timestamps, which is what `ir_pit.observed_from`/`observed_to`
    #: carry. Recorded so a human can tell one harvest from another at a glance.
    planned_from: Optional[float] = None
    planned_to: Optional[float] = None
    #: The build that harvested. Not the build that ingests — that one is stamped separately.
    tool_version: Optional[str] = None


def vantage_id(appliance: Optional[str], streams: Optional[str]) -> Optional[str]:
    """`(appliance, streams) -> "host:spec"`, or `None` when the appliance is unknown.

    Two taps differ when they watch different links, and on an Axellio appliance a link is a
    **stream**: one host capturing streams `1-4` and the same host capturing `5-8` are two
    vantages, not one. Both halves are therefore in the identity.

    `None` when there is no appliance — a `--from-pcaps` run. Deliberately not a synthetic
    `"unknown:..."` string: a fabricated identity would compare equal to another fabricated one,
    which is precisely the false-accept D1 exists to close.
    """
    if not appliance:
        return None
    return f"{appliance}:{streams}" if streams else appliance


def manifest_path(directory) -> Path:
    return Path(directory) / VANTAGE_MANIFEST


def write_vantage(directory, record: Vantage) -> None:
    """Write `<directory>/vantage.json`. Overwrites: the manifest describes the tree as it now
    stands, and a resumed harvest that appended windows to it has not changed which tap they came
    from. Sorted keys and a trailing newline, like every other artifact this project writes."""
    path = manifest_path(directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(record), sort_keys=True, indent=2) + "\n")


def read_vantage(directory) -> Optional[Vantage]:
    """`<directory>/vantage.json` -> `Vantage`, or `None` when there is no manifest.

    A missing file is the ordinary case — a log directory of unknown origin, which is exactly what
    D2's tier 2 (`--vantage`) exists for — so it returns `None` rather than raising. Anything else
    wrong with a manifest that *does* exist raises `VantageError`: unreadable, not JSON, not an
    object, or carrying a key this build does not know.

    An **unknown key is refused**, not ignored. This file's whole job is to be read by a build that
    did not write it, and a key silently dropped here is a fact a future harvester recorded and this
    ingest threw away without saying so — the same closed-vocabulary posture `catalog/roles.py`
    takes with `roles.yaml`, for the same reason.
    """
    path = manifest_path(directory)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
    except OSError as exc:
        raise VantageError(f"{path}: unreadable ({exc})") from exc
    except json.JSONDecodeError as exc:
        raise VantageError(f"{path}: not valid JSON ({exc})") from exc
    if not isinstance(raw, dict):
        raise VantageError(f"{path}: expected a JSON object, got {type(raw).__name__}")
    known = set(Vantage.__dataclass_fields__)
    if unknown := sorted(set(raw) - known):
        raise VantageError(
            f"{path}: unrecognized key(s) {unknown} — written by a build that records more than "
            f"this one reads. Ignoring them would discard recorded facts silently; update this "
            f"build rather than the manifest.")
    if missing := sorted(k for k in ("vantage", "appliance", "streams", "produced_by")
                         if k not in raw):
        raise VantageError(f"{path}: missing required key(s) {missing}")
    return Vantage(**raw)
