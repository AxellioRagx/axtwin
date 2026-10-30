# adapters — deploy-platform plugins

One folder per deploy platform the engine can cast a twin onto. Each carries its
own `SPEC.md` (from the root `Spec.template.md`): the seam it implements, the
op→platform mapping, its config keys, and its acceptance.

| Folder | Platform | Code |
|---|---|---|
| `proxmox/` | Proxmox VE (REST + SSH) | `engine/src/axrange/adapter_proxmox/` today; moves here once the platform seams in `docs/ARCHITECTURE.md` §13 exist |
| `libvirt/` | native QEMU/KVM on a Linux host, driven through libvirt | spec only |

The contract every adapter satisfies is the `Adapter` protocol
(`axrange.planner.executor.Adapter`) plus the platform seams named in
`docs/ARCHITECTURE.md` §13. The version-locked op semantics are in
`docs/contracts/OP_CATALOG.md`; the Proxmox op→backend contract is
`docs/contracts/ADAPTER.md`. A new platform is a new folder here with a
`SPEC.md` owning its config and image-binding schemas, and a target that names
it (`platform:` in the targets file, `api/Spec.md` A4).
