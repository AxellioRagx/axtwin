"""Manifest + binding directory loader.

Loads `images/manifests/*.yaml` and `images/bindings/<platform>/*.yaml` (every
platform directory present: `proxmox/`, `libvirt/`, ...) into a
ManifestRegistry the validator can query by image-id and platform.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ruamel.yaml import YAML

from axrange.ir.yaml_util import YamlLoadError, load_yaml_raw
from axrange.manifest.schema import BindingDoc, Manifest


class ManifestError(Exception):
    pass


@dataclass
class ManifestRegistry:
    """In-memory image catalog: manifest by image-id, optional per-platform
    binding by (image-id, platform).
    """

    manifests: dict[str, Manifest] = field(default_factory=dict)
    bindings: dict[tuple[str, str], BindingDoc] = field(default_factory=dict)

    def get_manifest(self, image_id: str) -> Optional[Manifest]:
        return self.manifests.get(image_id)

    def get_binding(self, image_id: str, platform: str = "proxmox") -> Optional[BindingDoc]:
        return self.bindings.get((image_id, platform))


def _yaml_loader() -> YAML:
    y = YAML(typ="safe")
    return y


def _load(path: Path) -> dict:
    try:
        data = load_yaml_raw(path, loader=_yaml_loader()) or {}
    except YamlLoadError as e:
        if e.not_found:
            raise ManifestError(f"Manifest file not found: {path}") from e
        raise ManifestError(str(e)) from e
    if not isinstance(data, dict):
        raise ManifestError(f"Manifest root must be a mapping in {path}")
    return data


def load_registry(
    root: str | Path | None = None,
    *,
    manifest_dir: str | Path | None = None,
    binding_dirs: dict[str, str | Path] | None = None,
) -> ManifestRegistry:
    """Walk `root/images/manifests/` and `root/images/bindings/<platform>/`
    (defaults), or explicit dirs, and build a registry.

    Missing directories are tolerated — return an empty registry. The
    validator catches "manifest not found" for a referenced image via REF-120,
    so an empty registry is fine in dev/test contexts.
    """
    reg = ManifestRegistry()

    if manifest_dir is None and root is not None:
        manifest_dir = Path(root) / "images" / "manifests"
    if binding_dirs is None and root is not None:
        # One directory per deploy platform (`images/bindings/<platform>/`,
        # `docs/ARCHITECTURE.md` §4 "platform binds at plan"); each platform's
        # binding keys are its adapter's (`adapters/<platform>/SPEC.md`).
        bindings_root = Path(root) / "images" / "bindings"
        binding_dirs = {
            p.name: p for p in sorted(bindings_root.iterdir())
            if p.is_dir() and not p.name.startswith(".")
        } if bindings_root.is_dir() else {}

    if manifest_dir is not None and Path(manifest_dir).is_dir():
        for yaml_file in sorted(Path(manifest_dir).glob("*.yaml")):
            data = _load(yaml_file)
            try:
                m = Manifest.model_validate(data)
            except Exception as e:  # noqa: BLE001
                raise ManifestError(f"Manifest {yaml_file} invalid: {e}") from e
            if m.image is None or m.image.id is None:
                continue
            reg.manifests[m.image.id] = m

    if binding_dirs:
        for platform, bdir in binding_dirs.items():
            p = Path(bdir)
            if not p.is_dir():
                continue
            for yaml_file in sorted(p.glob("*.yaml")):
                data = _load(yaml_file)
                # The binding doc wraps a `binding:` section.
                binding_block = data.get("binding") if isinstance(data, dict) else None
                if not isinstance(binding_block, dict):
                    continue
                # Flatten for our typed view.
                flat = {
                    "binding_schema_version": data.get("binding_schema_version"),
                    "image": binding_block.get("image"),
                    "platform": binding_block.get("platform", platform),
                    "versions": binding_block.get("versions", []),
                }
                try:
                    b = BindingDoc.model_validate(flat)
                except Exception as e:  # noqa: BLE001
                    raise ManifestError(f"Binding {yaml_file} invalid: {e}") from e
                if b.image is None or b.platform is None:
                    continue
                reg.bindings[(b.image, b.platform)] = b

    return reg
