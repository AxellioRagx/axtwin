"""Image profiles (`docs/design/canvas-add-panel.md` §4.3, D2): the image the
canvas's Add panel writes on a device it drops, keyed by profile
(`linux-server`, `windows-client`, `router`, ...). One file,
`image-profiles.yaml`, beside the twin catalog (`catalog/image-profiles.yaml`
in a checkout), served read-only by `GET /api/image-profiles`.

Per-platform images (D6, Authority: user, 2026-11-21): a profile may add
`platforms: {<platform>: <image>}` beside its default `image`; the answer is
resolved for one target's platform (`?target=`, absent: the default target)
and says which (`target`, `platform`). No target configured: the defaults,
unresolved. A profile stays canvas-side vocabulary -- the twin is written
with the concrete image, so no twin-config or IR form changes.

A missing file is no error: no profiles, one warning. A profile whose image
is null or absent is served as `image: null` (the canvas then says "no image
for this profile"). A malformed file is a 500 naming it -- it is content the
operator edits by hand.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from axrange.api import dtos
from axrange.api.service import ApiError, _write_root

FILE = "image-profiles.yaml"


def profiles_path(settings: Any) -> Path:
    """`<catalog>/image-profiles.yaml`: beside the catalog's write root
    (`catalog/twins` -> `catalog/`), the same parent drafts sit under;
    `AXRANGE_IMAGE_PROFILES` names another file."""
    if getattr(settings, "image_profiles", ""):
        return Path(settings.image_profiles)
    return _write_root(settings).parent / FILE


def _platform(settings: Any, target: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """(target name, platform) for `target` (None: the default one). A named
    target that does not exist is a 404; none configured, or one whose
    platform cannot be read, resolves nothing (the defaults are served)."""
    from axrange.api.targets import UnknownTargetError, get_target, target_facts
    from axrange.platform import check_platform

    try:
        t = get_target(settings, target)
    except UnknownTargetError as e:
        raise ApiError(404, f"unknown target {e.name!r}", detail={"targets": e.known}) from e
    except Exception:  # noqa: BLE001 -- no target configured: nothing to resolve for
        return None, None
    try:
        return t.name, target_facts(t).platform or check_platform(t.platform)
    except Exception:  # noqa: BLE001 -- an unreadable target: the defaults
        return t.name, None


def list_image_profiles(settings: Any, target: Optional[str] = None) -> dtos.ImageProfileListDTO:
    from ruamel.yaml import YAML

    path = profiles_path(settings)
    if not path.is_file():
        return dtos.ImageProfileListDTO(profiles=[], warnings=[f"no {FILE} beside the catalog ({path})"])
    target_name, platform = _platform(settings, target)
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
        per = (body or {}).get("platforms")
        if per is None:
            per = {}
        if not isinstance(per, dict) or not all(isinstance(v, str) and v.strip() for v in per.values()):
            raise ApiError(500, f"{path}: profile {name!r}: 'platforms' must map a platform to an image")
        platforms = {str(k): v.strip() for k, v in per.items()}
        if platform and platform in platforms:
            image = platforms[platform]
        if image is None:
            warnings.append(f"profile {name!r} has no image")
        out.append(dtos.ImageProfileDTO(name=str(name), image=image, platforms=platforms))
    return dtos.ImageProfileListDTO(profiles=out, warnings=warnings, target=target_name, platform=platform)
