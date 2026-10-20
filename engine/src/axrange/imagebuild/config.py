"""BuildConfig — connection + resource params for the image build pipeline.

docs/IMAGE_BUILD.md §5.2: the build pipeline uses a credential scope **broader
than the deploy-time adapter token**. Deploy-time auth (docs/ADAPTER.md §5) is
scoped to SDN + VM lifecycle + VM config; building additionally needs VM
create, disk import, VM destroy (cleanup of the transient build VM), and
template conversion.

Rather than re-implement transport, this module layers build-scope settings on
top of the adapter's `AdapterConfig` and synthesizes `AdapterConfig` *views* so
the build pipeline reuses the adapter's `RestClient` and `run_ssh` /
`scp_to_remote` verbatim (docs/IMAGE_BUILD.md §8: "Reuses the adapter's REST
client, SSH backend, and config loading").

Resolution order matches the adapter: overrides > env > adapter.conf > default.
Every `BUILD_*` key **falls back to its deploy-scope counterpart**, so a
single-account lab can run builds with one credential set, while a hardened
deploy can keep the broader build token out of the deploy path.

Config keys (adapter.conf / env), all optional:

  BUILD_API_TOKEN        broader REST token; default = PROXMOX_API_TOKEN
  BUILD_SSH_USER         SSH user with `qm` rights; default = root
  BUILD_SSH_KEY          SSH key for that user; default = OVS_SSH_KEY
  BUILD_SSH_HOST         build node SSH host; default = OVS_SSH_HOST
  BUILD_NODE             build target Proxmox node; default = DEFAULT_NODE
  BUILD_STORAGE          storage for the build disk + resulting template; default = local-lvm
  BUILD_BRIDGE           NIC bridge with internet for the bake; default = vmbr0
  BUILD_SNIPPET_STORAGE  storage holding cloud-init snippets; default = local
  BUILD_CACHE_DIR        local cloud-image cache; default = ~/.axrange/cache
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from axrange.adapter_proxmox.config import (
    AdapterConfig,
    _read_conf_file,
    load_config,
)


@dataclass
class BuildConfig:
    """Build-scope settings + a handle to the deploy-scope adapter config.

    The deploy config (`deploy`) is what the smoke-test deploy runs through —
    it exercises *the real deploy path*, so it must use the deploy-scope
    credentials, not the broader build token. The build-scope fields below are
    used only for the build VM's create/import/template/destroy lifecycle.
    """

    deploy: AdapterConfig
    api_token: str
    ssh_user: str
    ssh_key: str
    ssh_host: str
    node: str
    storage: str
    bridge: str
    snippet_storage: str
    cache_dir: str
    # Build VM's first-boot IP config (PVE ipconfig0 form). Default DHCP; set
    # a static (e.g. "ip=10.0.0.90/24,gw=10.0.0.1") when the build bridge has
    # no DHCP — the bake needs internet to apt-install qemu-guest-agent.
    ipconfig: str = "ip=dhcp"

    def rest_config(self) -> AdapterConfig:
        """An AdapterConfig view carrying the broader build REST token, for
        constructing a build-scope RestClient."""
        return replace(self.deploy, proxmox_api_token=self.api_token)

    def ssh_config(self) -> AdapterConfig:
        """An AdapterConfig view carrying the build SSH identity (root + `qm`
        rights), for reuse with adapter.ssh.run_ssh / scp_to_remote."""
        return replace(
            self.deploy,
            ovs_ssh_user=self.ssh_user,
            ovs_ssh_key=self.ssh_key,
            ovs_ssh_host=self.ssh_host,
        )


def load_build_config(
    config_path: str | Path | None = None,
    *,
    overrides: dict[str, Any] | None = None,
) -> BuildConfig:
    """Load a BuildConfig from adapter.conf + env + overrides.

    The deploy-scope AdapterConfig is loaded first (same precedence as the
    adapter), then each BUILD_* key is resolved with the same precedence and
    falls back to its deploy-scope counterpart.
    """
    deploy = load_config(config_path, overrides=overrides)

    # Re-read the same conf file the adapter used, so BUILD_* keys resolve from
    # the same source with the same precedence (overrides > env > file > dflt).
    from axrange.adapter_proxmox.config import default_adapter_conf_paths

    paths: list[Path] = []
    if config_path is not None:
        paths.append(Path(config_path))
    else:
        paths.extend(default_adapter_conf_paths())
    file_data: dict[str, str] = {}
    for p in paths:
        if p.is_file():
            file_data = _read_conf_file(p)
            break

    def get(key: str, default: str = "") -> str:
        if overrides and key in overrides and overrides[key] is not None:
            return str(overrides[key])
        env_val = os.environ.get(key)
        if env_val is not None:
            return env_val
        return file_data.get(key, default)

    cache_dir = get("BUILD_CACHE_DIR", "") or str(Path.home() / ".axrange" / "cache")
    ssh_key = get("BUILD_SSH_KEY", "") or deploy.ovs_ssh_key

    cfg = BuildConfig(
        deploy=deploy,
        api_token=get("BUILD_API_TOKEN", "") or deploy.proxmox_api_token,
        ssh_user=get("BUILD_SSH_USER", "") or "root",
        ssh_key=str(Path(ssh_key).expanduser()) if ssh_key else "",
        ssh_host=get("BUILD_SSH_HOST", "") or deploy.ovs_ssh_host,
        node=get("BUILD_NODE", "") or deploy.default_node,
        storage=get("BUILD_STORAGE", "") or "local-lvm",
        bridge=get("BUILD_BRIDGE", "") or "vmbr0",
        snippet_storage=get("BUILD_SNIPPET_STORAGE", "") or "local",
        cache_dir=str(Path(cache_dir).expanduser()),
        ipconfig=get("BUILD_IPCONFIG", "") or "ip=dhcp",
    )
    return cfg
