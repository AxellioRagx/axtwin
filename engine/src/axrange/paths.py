"""Checkout-layout paths: where this package's CONTENT lives relative to the code.

The monorepo keeps content out of the package tree (root `Claude.md`, "Layout"):

    <project>/
      catalog/            twin + persona content -- the catalog roots below
      engine/src/axrange  this package
      config/             operator config (adapter.conf + examples)
      ai-crafted/         agentic twin-config experiment (not read by code)
      reports/            rendered reports (not read by code)

These constants are the UNSET defaults for `--twins-dir` / `AXRANGE_TWINS_DIR`
and friends; an explicit path always wins. They are derived from this file's
location, so they are meaningful in a checkout (editable install) only -- the
same posture the per-package `parents[2]` derivation had before the move.
"""
from __future__ import annotations

from pathlib import Path

# engine/src/axrange/paths.py -- parents: [0]=axrange [1]=src [2]=engine [3]=project
PROJECT_ROOT: Path = Path(__file__).resolve().parents[3]
ENGINE_ROOT: Path = Path(__file__).resolve().parents[2]

#: Twin content root. One folder per format, all under `catalog/`.
CATALOG_ROOT: Path = PROJECT_ROOT / "catalog"
#: `twins/<id>/twin.yaml` bundles -- the live catalog (hand-authored + casts).
TWINS_DIR: Path = CATALOG_ROOT / "twins"
#: Legacy `twin-configs/<id>/<id>.yaml` -- searched by the compat reader, never written.
LEGACY_TWIN_CONFIGS_DIR: Path = CATALOG_ROOT / "twin-configs"
#: `target-environments/<id>/scenario.yaml` -- the scenario (IR) library.
TARGET_ENVIRONMENTS_DIR: Path = CATALOG_ROOT / "target-environments"
#: Operator config root. `adapter.conf` (gitignored, holds the Proxmox token) and
#: the tracked `*.example` templates live here; see `adapter_proxmox.config.
#: default_adapter_conf_paths` for how it is searched.
CONFIG_DIR: Path = PROJECT_ROOT / "config"
ADAPTER_CONF: Path = CONFIG_DIR / "adapter.conf"
#: `personas/<id>.yaml` -- the shipped persona library (`axrange.personas.registry`);
#: `AXRANGE_PERSONAS_DIR` adds colon-separated extra dirs on top, never replaces it.
PERSONAS_DIR: Path = CATALOG_ROOT / "personas"
