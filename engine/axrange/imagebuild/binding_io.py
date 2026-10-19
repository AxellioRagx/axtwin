"""Binding I/O — read the build-target version and write the new template_vmid.

docs/IMAGE_BUILD.md §3 step 8 ("Templatize"): "Write the resulting VMID into
the binding's `versions[<v>].storage.template_vmid`." §6 ("Lifecycle
integration"): the operator reviews and commits the binding diff — the build
edits the working tree, it does not commit (CLAUDE.md Git Policy).

The binding carries load-bearing comments (e.g. the i440fx-vs-q35 note on
debian-12-cloud), so writes go through ruamel **round-trip** mode to preserve
comments and layout — only the one `template_vmid` scalar changes.

Lifecycle note (slice). The done-criterion 3 "prior version demoted to
deprecated" is the general multi-version model (docs/IMAGE_MANIFEST.md §2.5,
whose `status:` field lives in the *manifest*). The slice's default reproduces
the lab's template 610 **in place**: it rewrites the current version's
`template_vmid` and leaves the prior template VMID intact on the cluster (it
stays resolvable). That keeps the manifest untouched (docs/IMAGE_BUILD.md §5.1
"the manifest is unchanged"). A true version bump (append a new version +
demote the prior `status:`) is a post-slice nicety, not built here.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from axrange.imagebuild.errors import ImageBuildError


def binding_path_for(images_root: str | Path, image: str, platform: str = "proxmox") -> Path:
    return Path(images_root) / "images" / "bindings" / platform / f"{image}.yaml"


def _rt_yaml() -> YAML:
    y = YAML(typ="rt")
    y.preserve_quotes = True
    y.width = 4096  # don't wrap long flow mappings / volids
    return y


def _find_version(doc: dict[str, Any], version_id: str) -> dict[str, Any]:
    binding = doc.get("binding")
    if not isinstance(binding, dict):
        raise ImageBuildError(
            "binding file has no top-level `binding:` mapping", stage="update-binding"
        )
    versions = binding.get("versions")
    if not isinstance(versions, list):
        raise ImageBuildError(
            "binding has no `versions:` list", stage="update-binding"
        )
    for v in versions:
        if isinstance(v, dict) and v.get("id") == version_id:
            return v
    raise ImageBuildError(
        f"binding has no version {version_id!r}", stage="update-binding"
    )


def read_template_vmid(binding_file: str | Path, version_id: str) -> int | None:
    """Return the current `template_vmid` for a version (or None if unset)."""
    yaml = _rt_yaml()
    path = Path(binding_file)
    if not path.is_file():
        raise ImageBuildError(
            f"binding file not found: {path}", stage="update-binding"
        )
    doc = yaml.load(path.read_text(encoding="utf-8"))
    version = _find_version(doc, version_id)
    storage = version.get("storage")
    if not isinstance(storage, dict):
        return None
    val = storage.get("template_vmid")
    return int(val) if val is not None else None


def write_template_vmid(
    binding_file: str | Path, version_id: str, new_vmid: int
) -> dict[str, Any]:
    """Set `versions[<version_id>].storage.template_vmid = new_vmid`, preserving
    comments + layout. Returns {version_id, old_vmid, new_vmid}."""
    yaml = _rt_yaml()
    path = Path(binding_file)
    if not path.is_file():
        raise ImageBuildError(
            f"binding file not found: {path}", stage="update-binding"
        )
    doc = yaml.load(path.read_text(encoding="utf-8"))
    version = _find_version(doc, version_id)
    storage = version.get("storage")
    if not isinstance(storage, dict):
        raise ImageBuildError(
            f"version {version_id!r} has no `storage:` mapping to write into",
            stage="update-binding",
        )
    old = storage.get("template_vmid")
    storage["template_vmid"] = int(new_vmid)

    import io

    buf = io.StringIO()
    yaml.dump(doc, buf)
    path.write_text(buf.getvalue(), encoding="utf-8")

    return {
        "version_id": version_id,
        "old_vmid": int(old) if old is not None else None,
        "new_vmid": int(new_vmid),
    }


def clear_template_vmid(binding_file: str | Path, version_id: str) -> None:
    """Unset `versions[<version_id>].storage.template_vmid` (pop the key),
    preserving comments + layout. The symmetric undo of `write_template_vmid`
    — `write_template_vmid` can only SET an int, so a build that writes a
    binding for the FIRST time (no prior `template_vmid`) had no way to fully
    revert on a later failure (code-review finding #1, 2026-07-07): the
    binding was left pointing at a `template_vmid` whose VM `build_image`'s
    own failure-unwind was about to destroy, looking valid in a diff. Called
    from `builder.build_image`'s unwind when `prior_vmid is None` (there was
    nothing to revert TO), so a failed first build leaves the binding with NO
    `template_vmid` key at all — exactly the pre-build state.
    """
    yaml = _rt_yaml()
    path = Path(binding_file)
    if not path.is_file():
        raise ImageBuildError(
            f"binding file not found: {path}", stage="update-binding"
        )
    doc = yaml.load(path.read_text(encoding="utf-8"))
    version = _find_version(doc, version_id)
    storage = version.get("storage")
    if isinstance(storage, dict):
        storage.pop("template_vmid", None)

    import io

    buf = io.StringIO()
    yaml.dump(doc, buf)
    path.write_text(buf.getvalue(), encoding="utf-8")
