"""Read-only host probe: connectivity + storage + network/SDN inventory.

Uses the adapter's own config loader + RestClient, so the API token is read
from adapter.conf (never passed on the command line). All calls are GET.
"""
from __future__ import annotations

import json
import sys

from axrange.adapter_proxmox.config import load_config
from axrange.adapter_proxmox.rest import RestClient
from axrange.planner.executor import AdapterError


def show(title, fn):
    print(f"\n=== {title} ===")
    try:
        data = fn()
        print(json.dumps(data, indent=2, sort_keys=True))
    except AdapterError as e:
        print(f"ERROR: {e}")
    except Exception as e:  # noqa: BLE001
        print(f"ERROR ({type(e).__name__}): {e}")


def main() -> int:
    cfg = load_config()
    print(f"API URL : {cfg.proxmox_api_url}")
    tok = cfg.proxmox_api_token
    # Mask the secret; show only the user!tokenid prefix so we can confirm it.
    shown = tok.split("=", 1)[0] + "=" + (tok.split("=", 1)[1].split("=")[0] if "=" in tok else "")
    print(f"API token (prefix): {shown}=<secret-hidden>")
    print(f"verify_tls: {cfg.proxmox_verify_tls}")

    c = RestClient(cfg)
    show("version", lambda: c.request("GET", "/version"))
    nodes = []
    try:
        nodes = c.request("GET", "/nodes") or []
    except Exception as e:  # noqa: BLE001
        print(f"\n=== nodes ===\nERROR: {e}")
        print("\nAuth/connectivity failed — fix adapter.conf token user, then re-run.")
        return 1
    print("\n=== nodes ===")
    print(json.dumps(nodes, indent=2, sort_keys=True))

    for n in nodes:
        node = n.get("node")
        if not node:
            continue
        show(f"storage @ {node}", lambda node=node: c.request("GET", f"/nodes/{node}/storage"))
        show(f"network @ {node}", lambda node=node: c.request("GET", f"/nodes/{node}/network"))

    show("cluster/sdn/zones", lambda: c.request("GET", "/cluster/sdn/zones"))
    show("cluster/sdn/vnets", lambda: c.request("GET", "/cluster/sdn/vnets"))
    show("cluster/resources (vm)", lambda: c.request("GET", "/cluster/resources", params={"type": "vm"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
