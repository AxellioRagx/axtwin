"""Typed models for image manifests + per-platform bindings per
docs/IMAGE_MANIFEST.md (v1).

Same permissive-field philosophy as the IR schema — Pydantic shapes the
object graph; the validator runs the rules.
"""
from __future__ import annotations

import warnings
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class ManifestBase(BaseModel):
    model_config = ConfigDict(extra="allow", str_strip_whitespace=False)


class ImageStatus(str, Enum):
    CURRENT = "current"
    DEPRECATED = "deprecated"
    WITHDRAWN = "withdrawn"


class InstallSupport(ManifestBase):
    """IMAGE_MANIFEST §2.6 — mirrors IR §4.1 closed action set.

    `pkg` and `role` may be `false` (Python: False) or a list of supported
    package-manager / role names. `config`, `container`, `script` are bools.
    """

    pkg: Optional[Any] = None  # list[str] | False
    config: Optional[bool] = None
    role: Optional[Any] = None  # list[str] | False
    container: Optional[bool] = None
    script: Optional[bool] = None

    def supports(self, kind: str) -> bool:
        """True if this image accepts an install-action of kind `kind`.

        kind ∈ {pkg, pkg@version, config, role, container, script}.
        """
        if kind in ("pkg", "pkg@version"):
            return bool(self.pkg)
        if kind == "config":
            return bool(self.config)
        if kind == "role":
            return bool(self.role)
        if kind == "container":
            return bool(self.container)
        if kind == "script":
            return bool(self.script)
        return False

    def valid_role_names(self) -> list[str]:
        if isinstance(self.role, list):
            return [str(r) for r in self.role]
        return []


class ManifestVersion(ManifestBase):
    id: Optional[str] = None
    released: Optional[Any] = None  # date or str; informational
    status: Optional[Any] = None  # ImageStatus value or arbitrary
    replacement: Optional[str] = None
    provides: list[str] = Field(default_factory=list)
    install_support: Optional[InstallSupport] = None
    first_boot_budget: Optional[int] = None
    size_estimate: Optional[str] = None


class ManifestOS(ManifestBase):
    family: Optional[str] = None  # windows | linux | bsd
    distribution: Optional[str] = None
    version: Optional[str] = None
    architecture: Optional[str] = None


class ManifestImage(ManifestBase):
    id: Optional[str] = None
    description: Optional[str] = None
    os: Optional[ManifestOS] = None
    versions: list[ManifestVersion] = Field(default_factory=list)
    notes: Optional[list[str]] = None


class Manifest(ManifestBase):
    manifest_schema_version: Optional[int] = None
    image: Optional[ManifestImage] = None

    def get_version(self, version_id: str) -> Optional[ManifestVersion]:
        if self.image is None:
            return None
        for v in self.image.versions:
            if v.id == version_id:
                return v
        return None

    def current_version(self) -> Optional[ManifestVersion]:
        """First version with status: current, or first listed if none is.

        Falling back to the first-listed version is a tolerant default, not a
        silently-correct one — a manifest with zero `status: current` versions
        is a real authoring error (nothing marks which version is the intended
        default), so warn when we take the fallback path.
        """
        if self.image is None:
            return None
        for v in self.image.versions:
            if v.status == ImageStatus.CURRENT.value:
                return v
        if self.image.versions:
            warnings.warn(
                f"manifest {self.image.id!r} has no version with "
                f"status: current — falling back to the first listed version "
                f"({self.image.versions[0].id!r}). This is likely a manifest-"
                f"authoring error; mark the intended default version "
                f"status: current.",
                stacklevel=2,
            )
            return self.image.versions[0]
        return None


# --- Binding side ---


class BindingStorage(ManifestBase):
    type: Optional[str] = None
    node: Optional[Any] = None  # "any" | str | list[str]
    volid: Optional[str] = None
    template_vmid: Optional[int] = None


class BindingCloudInit(ManifestBase):
    supported: Optional[bool] = None
    user: Optional[str] = None
    sysprep_required: Optional[bool] = None


class BindingTPM(ManifestBase):
    enabled: Optional[bool] = None
    version: Optional[str] = None


class BindingHardware(ManifestBase):
    machine: Optional[str] = None
    bios: Optional[str] = None
    nic_model: Optional[str] = None
    disk_bus: Optional[str] = None
    scsi_controller: Optional[str] = None
    agent: Optional[bool] = None
    tpm: Optional[BindingTPM] = None


class PlacementConstraints(ManifestBase):
    requires_host_caps: list[str] = Field(default_factory=list)
    avoid_nodes: list[str] = Field(default_factory=list)


class BindingVersion(ManifestBase):
    id: Optional[str] = None
    storage: Optional[BindingStorage] = None
    clone_mode: Optional[str] = None
    cloud_init: Optional[BindingCloudInit] = None
    hardware: Optional[BindingHardware] = None
    placement_constraints: Optional[PlacementConstraints] = None
    clone_estimate: Optional[int] = None


class Binding(ManifestBase):
    binding_schema_version: Optional[int] = None
    binding: Optional[dict] = None  # raw nested-binding container


class BindingDoc(ManifestBase):
    """Top-level Binding YAML wrapper (binding_schema_version + binding)."""

    binding_schema_version: Optional[int] = None
    image: Optional[str] = None
    platform: Optional[str] = None
    versions: list[BindingVersion] = Field(default_factory=list)

    def get_version(self, version_id: str) -> Optional[BindingVersion]:
        for v in self.versions:
            if v.id == version_id:
                return v
        return None
