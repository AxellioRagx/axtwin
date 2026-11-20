"""Image profiles (`docs/design/canvas-add-panel.md` §4.3, D2): the image the
canvas's Add panel writes on a device it drops, keyed by profile
(`linux-server`, `windows-client`, `router`, ...). One file,
`image-profiles.yaml`, beside the twin catalog (`catalog/image-profiles.yaml`
in a checkout), served read-only by `GET /api/image-profiles`.

A missing file is no error: no profiles, one warning. A profile whose image
is null or absent is served as `image: null` (the canvas then says "no image
for this profile"). A malformed file is a 500 naming it -- it is content the
operator edits by hand.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from axrange.api import dtos
from axrange.api.service import ApiError, _write_root

FILE = "image-profiles.yaml"


def profiles_path(settings: Any) -> Path:
    """`<catalog>/image-profiles.yaml`: beside the catalog's write root
    (`catalog/twins` -> `catalog/`), the same parent drafts sit under."""
    return _write_root(settings).parent / FILE


def list_image_profiles(settings: Any) -> dtos.ImageProfileListDTO:
    from ruamel.yaml import YAML

    path = profiles_path(settings)
    if not path.is_file():
        return dtos.ImageProfileListDTO(profiles=[], warnings=[f"no {FILE} beside the catalog ({path})"])
    try:
        raw = YAML(typ="safe").load(path.read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001 -- a YAML error names the file
        raise ApiError(500, f"{path}: unreadable: {e}") from e
    profiles = raw.get("profiles") if isinstance(raw, dict) else None
    if profiles is None:
        profiles = {}
    if not isinstance(profiles, dict):
        raise ApiError(500, f"{path}: 'profiles' must be a mapping")
    out: list[dtos.ImageProfileDTO] = []
    warnings: list[str] = []
    for name in sorted(profiles):
        body = profiles[name]
        if body is not None and not isinstance(body, dict):
            raise ApiError(500, f"{path}: profile {name!r} must be a mapping")
        image = (body or {}).get("image")
        if image is not None and not isinstance(image, str):
            raise ApiError(500, f"{path}: profile {name!r}: 'image' must be a string")
        image = image.strip() if isinstance(image, str) and image.strip() else None
        if image is None:
            warnings.append(f"profile {name!r} has no image")
        out.append(dtos.ImageProfileDTO(name=str(name), image=image))
    return dtos.ImageProfileListDTO(profiles=out, warnings=warnings)
