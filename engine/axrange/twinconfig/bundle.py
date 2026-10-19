"""Where a bundle's files live -- ONE rule for every reader and writer of a
cast/imprint bundle (`docs/WORK_ORDER_twin_consolidation.md` §2/Phase 3).

Two layouts are legal, told apart by a `twin.yaml` in the directory:

    legacy (aximprint's `install-cast` output; the packaged fixtures):
      <bundle>/<id>.yaml                  the twin config
      <bundle>/<id>.topology.json         topology export
      <bundle>/<id>.provenance.json       provenance sidecar
      <bundle>/<id>.scorecard.<deploy>.json, layout.json, .history/

    a `twins/<id>/` folder (manifest: `axrange.twinconfig.manifest`):
      <bundle>/twin.yaml                  names the te file (normally te.yaml)
      <bundle>/generated/<same names>     every MACHINE-WRITTEN file above

The machine-written files keep their id-stemmed names under `generated/` --
the aximprint names (D4: its installer is unchanged), so a reader changes
only the directory it looks in, never the name it looks for. Operator-
authored files (`<id>.assertions.yaml`) stay at the bundle root beside
`te.yaml`: they are content, not output.

The legacy layout is the compatibility reader the consolidation keeps until
aximprint's installer (or an axRange import step) files a cast into a twin
folder (§7 D4). An `aximprint install-cast` into a twin folder would write
the legacy names beside `te.yaml`, so the Console's apply refuses a twin
folder rather than half-updating it (`api.service.imprint_apply`).

Boundary: content-side, like the rest of `axrange.twinconfig` -- stats
paths and (for a twin folder) loads the manifest; imports nothing from the
planner, adapter or API.
"""
from __future__ import annotations

from pathlib import Path

from axrange.twinconfig.manifest import MANIFEST_FILENAME, load_twin_manifest

GENERATED_DIRNAME = "generated"


def is_twin_folder(bundle_dir: str | Path) -> bool:
    """True when `bundle_dir` is a `twins/<id>/` folder (has a `twin.yaml`)."""
    return (Path(bundle_dir) / MANIFEST_FILENAME).is_file()


def has_cast_artifacts(bundle_dir: str | Path, bundle_id: str) -> bool:
    """True when the bundle carries a cast's machine-written evidence -- its
    topology export or provenance sidecar. What tells a CAST twin folder
    (`twins/corpapcn-golden/`) from a hand-authored one (`twins/sme-flat/`)
    now that both share one root: the fidelity reader's default resolution
    treats only the former as a reference (a hand-authored twin scored
    against its own config would be circular)."""
    return any(
        bundle_artifact_path(bundle_dir, f"{bundle_id}.{kind}.json").is_file()
        for kind in ("topology", "provenance")
    )


def bundle_config_path(bundle_dir: str | Path, bundle_id: str) -> Path:
    """The twin-config file of the bundle: a twin folder's manifest te file
    (validated -- a broken `twin.yaml` raises `TwinManifestError`, never a
    silent fall back to a sibling file), else the legacy `<id>.yaml`. The
    path is returned whether or not the legacy file exists; the caller
    reports a missing one in its own error vocabulary."""
    d = Path(bundle_dir)
    if is_twin_folder(d):
        return load_twin_manifest(d).te_path
    return d / f"{bundle_id}.yaml"


def bundle_artifact_path(bundle_dir: str | Path, name: str) -> Path:
    """Where a MACHINE-WRITTEN bundle file named `name` (a sidecar, the
    topology export, a scorecard, `layout.json`, `.history`) lives:
    `<bundle>/generated/<name>` in a twin folder, `<bundle>/<name>` in a
    legacy bundle. For reads and writes alike -- one rule, so a writer can
    never put a file where its reader will not look."""
    d = Path(bundle_dir)
    return d / GENERATED_DIRNAME / name if is_twin_folder(d) else d / name
