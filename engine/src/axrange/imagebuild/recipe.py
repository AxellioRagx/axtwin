"""BuildRecipe — the per-image build recipe parsed from the Proxmox binding.

docs/IMAGE_BUILD.md §5.1: the build recipe (base image source, checksum source,
bake/provisioning steps) lives in the **Proxmox binding**, not the manifest.
The manifest stays the pure, platform-independent capability contract;
conformance (docs/IMAGE_BUILD.md §4) bridges "the manifest says X" to "the
built image does X".

This module owns the *interpretation* of the binding's `build:` section. The
manifest loader (`axrange.manifest.loader`) keeps the raw `build:` dict on the
binding version as an extra field (BindingVersion uses `extra="allow"`); we do
not push a typed build schema into `manifest/schema.py`, because the recipe is
a build-pipeline concern and `manifest/` must not grow build knowledge
(CLAUDE.md "don't mix concerns across submodules").

Linux binding `build:` schema (docs/IMAGE_MANIFEST.md §3.8):

    build:
      base_image:
        url: https://cloud.debian.org/.../debian-12-genericcloud-amd64.qcow2
        checksum_url: https://cloud.debian.org/.../SHA512SUMS   # optional
        checksum: <hex>                                          # optional (inline)
        checksum_algo: sha512                                    # sha256 | sha512
        filename: debian-12-genericcloud-amd64.qcow2             # cache name + SHA*SUMS key
      disk_size: 8G            # optional; resize the imported cloud disk
      memory: 2048             # optional; build VM RAM (MB); default 2048
      cores: 2                 # optional; build VM cores; default 2
      provision: []            # optional; extra bake steps (IR §4.1 action dicts)
      probes: {}               # optional; per-capability probe command overrides

Windows builds add a `windows:` sub-block (docs/SLICE_WINDOWS_AD.md §4). The
`base_image` is the **install ISO** (not a cloud qcow2), and the build installs
from it via an autounattend.xml rather than importing a disk:

    build:
      base_image: { url: .../SERVER_EVAL.iso, checksum: <hex>, checksum_algo: sha256, filename: ws2022.iso }
      windows:
        edition: "Windows Server 2022 SERVERSTANDARD"   # autounattend /IMAGE/NAME
        image_index: 2                                   # or /IMAGE/INDEX
        locale: en-US
        timezone: UTC
        admin_password: "..."                            # build-VM pw; sysprep wipes it
        product_key: ""                                  # optional (empty for eval)
        virtio_iso: { url: .../virtio-win.iso, checksum: <hex>, checksum_algo: sha256, filename: virtio-win.iso }

`qemu-guest-agent` preinstall + enable is **implicit** — the pipeline always
bakes it (docs/IMAGE_BUILD.md §3 step 4 for Linux; SetupComplete for Windows),
so it never appears in `provision:`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from axrange.imagebuild.errors import ImageBuildError
from axrange.manifest.schema import BindingVersion

_VALID_ALGOS = {"sha256", "sha512"}


@dataclass
class BaseImage:
    url: str
    filename: str
    checksum_algo: str = "sha512"
    checksum_url: str | None = None
    checksum: str | None = None  # inline hex; takes precedence over checksum_url


@dataclass
class WindowsRecipe:
    """The `build.windows:` sub-block — Windows-only build parameters
    (docs/SLICE_WINDOWS_AD.md §4). `virtio_iso` is required: Setup cannot see the
    virtio-scsi target disk without the virtio storage driver."""

    virtio_iso: BaseImage
    edition: str | None = None  # autounattend /IMAGE/NAME (WIM image name)
    image_index: int | None = None  # or /IMAGE/INDEX
    locale: str = "en-US"
    timezone: str = "UTC"
    admin_password: str = ""
    product_key: str | None = None
    org: str = "axRange"
    owner: str = "axRange"


@dataclass
class BuildRecipe:
    base_image: BaseImage
    disk_size: str | None = None
    memory: int = 2048
    cores: int = 2
    provision: list[dict[str, Any]] = field(default_factory=list)
    probes: dict[str, str] = field(default_factory=dict)
    # Present only for Windows images (os_family == "windows"); None for Linux.
    windows: WindowsRecipe | None = None


def _raw_build_section(bv: BindingVersion) -> dict[str, Any] | None:
    """Pull the raw `build:` dict off a binding version (an extra field)."""
    extra = getattr(bv, "model_extra", None) or {}
    raw = extra.get("build")
    if raw is None:
        raw = getattr(bv, "build", None)
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ImageBuildError(
            "binding `build:` section must be a mapping", stage="read-inputs"
        )
    return raw


def _parse_image_source(d: Any, *, what: str) -> BaseImage:
    """Parse a {url, filename, checksum, checksum_url, checksum_algo} block into
    a BaseImage. Shared by `base_image` (the cloud qcow2 / Windows ISO) and the
    Windows `virtio_iso` — both are fetch-and-verify-one-file sources."""
    if not isinstance(d, dict):
        raise ImageBuildError(
            f"binding `build.{what}` must be a mapping with at least `url`",
            stage="read-inputs",
        )
    url = d.get("url")
    if not url or not isinstance(url, str):
        raise ImageBuildError(
            f"binding `build.{what}.url` is required", stage="read-inputs"
        )

    algo = str(d.get("checksum_algo", "sha512")).lower()
    if algo not in _VALID_ALGOS:
        raise ImageBuildError(
            f"binding `build.{what}.checksum_algo` must be one of "
            f"{sorted(_VALID_ALGOS)} (got {algo!r})",
            stage="read-inputs",
        )

    filename = d.get("filename") or url.rstrip("/").rsplit("/", 1)[-1]
    if not filename:
        raise ImageBuildError(
            f"could not derive a filename for `build.{what}`; set "
            f"`build.{what}.filename`",
            stage="read-inputs",
        )

    checksum = d.get("checksum")
    checksum_url = d.get("checksum_url")
    if checksum is None and checksum_url is None:
        raise ImageBuildError(
            f"binding `build.{what}` needs `checksum` (inline hex) or "
            f"`checksum_url` (a SHA256SUMS/SHA512SUMS file) to verify the "
            f"download",
            stage="read-inputs",
        )

    return BaseImage(
        url=url,
        filename=str(filename),
        checksum_algo=algo,
        checksum_url=str(checksum_url) if checksum_url else None,
        checksum=str(checksum).strip().lower() if checksum else None,
    )


def _parse_windows(raw: Any) -> WindowsRecipe:
    """Parse the `build.windows:` sub-block (Windows builds only)."""
    if not isinstance(raw, dict):
        raise ImageBuildError(
            "binding `build.windows` must be a mapping", stage="read-inputs"
        )

    edition = raw.get("edition")
    image_index = raw.get("image_index")
    if not edition and image_index is None:
        raise ImageBuildError(
            "binding `build.windows` needs `edition` (the WIM image name) or "
            "`image_index` to select which Windows edition Setup installs",
            stage="read-inputs",
        )

    admin_pw = raw.get("admin_password")
    if not admin_pw:
        raise ImageBuildError(
            "binding `build.windows.admin_password` is required (the build-VM "
            "admin password; sysprep /generalize wipes it away)",
            stage="read-inputs",
        )

    virtio = raw.get("virtio_iso")
    if virtio is None:
        raise ImageBuildError(
            "binding `build.windows.virtio_iso` is required — Setup cannot see "
            "the virtio-scsi target disk without the virtio-win storage driver",
            stage="read-inputs",
        )

    return WindowsRecipe(
        virtio_iso=_parse_image_source(virtio, what="windows.virtio_iso"),
        edition=str(edition) if edition else None,
        image_index=int(image_index) if image_index is not None else None,
        locale=str(raw.get("locale", "en-US")),
        timezone=str(raw.get("timezone", "UTC")),
        admin_password=str(admin_pw),
        product_key=str(raw["product_key"]) if raw.get("product_key") else None,
        org=str(raw.get("org", "axRange")),
        owner=str(raw.get("owner", "axRange")),
    )


def recipe_from_binding_version(bv: BindingVersion) -> BuildRecipe:
    """Parse + validate the `build:` recipe from one binding version.

    Raises ImageBuildError when the section is absent or malformed — a build
    cannot proceed without a base image source.
    """
    raw = _raw_build_section(bv)
    if raw is None:
        raise ImageBuildError(
            f"binding version {bv.id!r} has no `build:` section; "
            "add a build recipe (docs/IMAGE_MANIFEST.md §3.8) before building",
            stage="read-inputs",
        )

    base_image = _parse_image_source(raw.get("base_image"), what="base_image")

    provision = raw.get("provision") or []
    if not isinstance(provision, list):
        raise ImageBuildError(
            "binding `build.provision` must be a list of install-action dicts",
            stage="read-inputs",
        )

    probes = raw.get("probes") or {}
    if not isinstance(probes, dict):
        raise ImageBuildError(
            "binding `build.probes` must be a mapping of capability -> command",
            stage="read-inputs",
        )

    windows = _parse_windows(raw["windows"]) if raw.get("windows") is not None else None

    return BuildRecipe(
        base_image=base_image,
        disk_size=str(raw["disk_size"]) if raw.get("disk_size") else None,
        memory=int(raw.get("memory", 2048)),
        cores=int(raw.get("cores", 2)),
        provision=[dict(p) for p in provision if isinstance(p, dict)],
        probes={str(k): str(v) for k, v in probes.items()},
        windows=windows,
    )
