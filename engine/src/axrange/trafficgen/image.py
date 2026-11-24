"""The generator image for a platform (`SPEC.md` "Generator image", T5): the
`generator` profile of `catalog/image-profiles.yaml`, resolved per platform the
way `GET /api/image-profiles?target=` resolves it -- `platforms.<platform>`
when present, else the default `image` (null for the generator: no binding
outside Proxmox today)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from axrange.paths import CATALOG_ROOT

PROFILE = "generator"


def profiles_file() -> Path:
    return Path(os.environ.get("AXRANGE_IMAGE_PROFILES") or CATALOG_ROOT / "image-profiles.yaml")


def generator_image(platform: Optional[str], path: Optional[Path] = None) -> Optional[str]:
    """`trex-astf@v1` on Proxmox; None when the platform has no generator image
    or the file / profile is missing."""
    from ruamel.yaml import YAML

    p = path or profiles_file()
    if not p.is_file():
        return None
    raw = YAML(typ="safe").load(p.read_text(encoding="utf-8")) or {}
    prof = ((raw.get("profiles") or {}) if isinstance(raw, dict) else {}).get(PROFILE)
    if not isinstance(prof, dict):
        return None
    per = prof.get("platforms") or {}
    if platform and isinstance(per, dict) and per.get(platform):
        return str(per[platform])
    return str(prof["image"]) if prof.get("image") else None
