"""Image build pipeline (docs/IMAGE_BUILD.md).

The `axrange image build <image>` verb: reproduce a Proxmox template from an
upstream Linux cloud image plus a `qemu-guest-agent` bake, verify it against
its manifest (capability probes + a one-host deploy smoke-test), and write the
new `template_vmid` into the Proxmox binding.

This submodule is an orchestration consumer (CLAUDE.md Conventions): it reuses
`adapter_proxmox`'s REST/SSH/config transports and runs a real `planner`+adapter
deploy for its smoke-test — the same way `cli/` wires validate→plan→deploy. It
does not know about scenarios, the canvas, or AI.

Public surface:

  build_image(image, *, images_root, build_cfg, options=...) -> BuildReport
  BuildOptions / BuildReport
  BuildConfig / load_build_config
  ImageBuildError
"""
from __future__ import annotations

from axrange.imagebuild.builder import BuildOptions, BuildReport, build_image
from axrange.imagebuild.config import BuildConfig, load_build_config
from axrange.imagebuild.errors import ImageBuildError

__all__ = [
    "BuildConfig",
    "BuildOptions",
    "BuildReport",
    "ImageBuildError",
    "build_image",
    "load_build_config",
]
