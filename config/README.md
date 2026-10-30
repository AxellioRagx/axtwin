# config — operator configuration

Operator-side configuration for the instrument, out of the package trees.

| File | Tracked | What |
|---|---|---|
| `adapter.conf.example` | yes | template for the Proxmox adapter: API URL/token, SSH, SDN, O2 endpoints |
| `adapter.conf.libvirt.example` | yes | template for the libvirt adapter (a target with `platform: libvirt`): URI, storage pool, host SSH, bindings dir |
| `adapter.conf` | **no** (gitignored) | your filled-in copy; holds the Proxmox API token |
| `adapter.targets.example.yaml` | yes | template for the named deploy targets (`AXRANGE_TARGETS`) |

`adapter.conf` is found automatically: `/etc/axrange/adapter.conf`, then this
folder, then the current directory (`axrange.adapter_proxmox.config.
default_adapter_conf_paths`); `--adapter-config` / `AXRANGE_ADAPTER_CONFIG`
override. The observability stack's `observability.conf` lives beside its
compose files under `o2/` (same gitignored-with-example pattern).
