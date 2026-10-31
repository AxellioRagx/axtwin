"""Enforces `docs/ARCHITECTURE.md` §13 / `docs/CONVENTIONS.md` §4 as code, not
prose: the engine reaches a deploy platform ONLY through the protocols in
`axrange.platform`; nothing under `src/axrange/` imports a platform's package
(`axrange.adapter_proxmox`, `axrange.adapter_libvirt`) except the closed
factory and the modules that are genuinely Proxmox-only (listed in
`_ALLOWED_IMPORTERS`, each with the reason it is there). The adapter packages
themselves are outside the scan: an adapter may lift another adapter's PURE
helpers (the libvirt adapter reuses the Proxmox naming shapes, QGA value
type and gretap command builders), which is platform code calling platform
code, not the engine binding a platform. The test
is the counterpart of `discovery/tests/test_acquire_layering.py` and uses the
same static-AST approach for the same reason: a lazily-imported violation
inside a function body is exactly as real as a module-level one, and a runtime
"what did `sys.modules` pick up" check would only see it if some test happened
to call that function.

The allowlist is a hard-coded set on purpose (the acquire test's `LES-027`
shape): every entry must still exist (`test_the_allowlist_has_not_gone_stale`)
and a new importer fails loudly until someone either routes it through the seam
or adds it here with a reason.
"""
from __future__ import annotations

import ast
import pathlib

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_SRC_ROOT = _REPO_ROOT / "src"
_AXRANGE_ROOT = _SRC_ROOT / "axrange"
#: The platform packages (`adapters/<platform>/SPEC.md` each): the scan
#: skips them and forbids importing them from anywhere else.
_FORBIDDEN_ROOTS = ("adapter_proxmox", "adapter_libvirt")
_ADAPTER_ROOTS = frozenset(_AXRANGE_ROOT / r for r in _FORBIDDEN_ROOTS)

#: Modules that may import a platform package directly: each with EXACTLY the
#: dotted modules it may reach (a new crossing from an allowed file fails like
#: one from anywhere else) and the reason.
_ALLOWED_IMPORTERS: dict[pathlib.Path, tuple[frozenset[str], str]] = {
    _AXRANGE_ROOT / "platform" / "factory.py": (
        frozenset({
            "axrange.adapter_proxmox", "axrange.adapter_proxmox.config", "axrange.adapter_proxmox.rest",
            "axrange.adapter_proxmox.resolve", "axrange.adapter_proxmox.naming",
            "axrange.adapter_proxmox.guest_agent",
            "axrange.adapter_libvirt", "axrange.adapter_libvirt.connection", "axrange.adapter_libvirt.inventory",
            "axrange.adapter_libvirt.naming", "axrange.adapter_libvirt.guest",
        }),
        "the closed platform factory -- the ONE place the engine binds a platform name to its code "
        "(proxmox and libvirt alike); it also reads the shared conf-file grammar for the PLATFORM= key",
    ),
    _AXRANGE_ROOT / "imagebuild" / "proxmox_build.py": (
        frozenset({
            "axrange.adapter_proxmox.config", "axrange.adapter_proxmox.guest_agent",
            "axrange.adapter_proxmox.rest", "axrange.adapter_proxmox.ssh",
        }),
        "the Proxmox image-build backend (qm/qemu-img over the node; REST for nextid/templates); "
        "a libvirt-native build path is a follow-up per adapters/libvirt/SPEC.md",
    ),
    _AXRANGE_ROOT / "imagebuild" / "config.py": (
        frozenset({"axrange.adapter_proxmox.config"}),
        "the image-build config is the Proxmox adapter.conf plus BUILD_* keys read from the same "
        "file with the same precedence; it moves with adapters/proxmox/ (SPEC Notes: adapter.conf.example)",
    ),
    _AXRANGE_ROOT / "planner" / "egress.py": (
        frozenset({"axrange.adapter_proxmox.egress_render"}),
        "the engine's one importer of the egress render (`egress_render`: the in-guest VyOS egress "
        "engine and its phase normalisation; guest-side, not platform-side -- a move candidate; the "
        "two in-guest constants it used to re-export already live here)",
    ),
}

#: Modules that may spell `DEFAULT_PLATFORM`: the factory that defines it
#: (and its package re-export). Everything else gets the platform from the
#: loaded config (`adapter_for_config`) or, for pre-plan naming, from
#: `default_namer()` -- never by naming the default platform itself.
_ALLOWED_DEFAULT_PLATFORM = frozenset({
    _AXRANGE_ROOT / "platform" / "factory.py",
    _AXRANGE_ROOT / "platform" / "__init__.py",
})


def _module_files(root: pathlib.Path, *, exclude_dirs: frozenset[pathlib.Path]) -> list[pathlib.Path]:
    files = sorted(
        p for p in root.rglob("*.py")
        if "__pycache__" not in p.parts and not any(d in p.parents for d in exclude_dirs)
    )
    assert files, f"no .py files under {root} -- the check would pass vacuously"
    return files


def _package_of(path: pathlib.Path) -> str:
    rel = path.relative_to(_SRC_ROOT)
    return ".".join(list(rel.with_suffix("").parts)[:-1])


def _resolved_imports_from_source(source: str, path: pathlib.Path) -> list[tuple[int, str]]:
    """Every dotted module path `source` reaches via `import` / `from ... import`
    anywhere in the file (function bodies included), plus a literal
    `importlib.import_module("...")` / `__import__("...")`. For `from X import
    a` both `X` and `X.a` are yielded (an AST cannot tell a submodule from a
    name)."""
    tree = ast.parse(source, filename=str(path))
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = node.module
            else:
                pkg = _package_of(path).split(".") if _package_of(path) else []
                strip = node.level - 1
                base_parts = pkg[: len(pkg) - strip] if strip else pkg
                base = ".".join(base_parts) if base_parts else None
                if node.module:
                    base = f"{base}.{node.module}" if base else node.module
            if base:
                out.append((node.lineno, base))
                for alias in node.names:
                    out.append((node.lineno, f"{base}.{alias.name}"))
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name in ("import_module", "__import__") and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    out.append((node.lineno, arg.value))
    return out


def _is_forbidden(dotted: str) -> bool:
    parts = dotted.split(".")
    return len(parts) >= 2 and parts[0] == "axrange" and parts[1] in _FORBIDDEN_ROOTS


def _allowed_module(dotted: str, allowed: frozenset[str]) -> bool:
    """`from X import a` yields both `X` and `X.a`; the `X.a` reading is fine
    when `X` itself is allowed (a name from an allowed module)."""
    return dotted in allowed or dotted.rsplit(".", 1)[0] in allowed


def _violations() -> list[str]:
    out: list[str] = []
    for path in _module_files(_AXRANGE_ROOT, exclude_dirs=_ADAPTER_ROOTS):
        allowed = _ALLOWED_IMPORTERS.get(path, (frozenset(), ""))[0]
        for lineno, dotted in _resolved_imports_from_source(path.read_text(), path):
            if _is_forbidden(dotted) and not _allowed_module(dotted, allowed):
                out.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}: imports {dotted!r}")
    return sorted(out)


def _default_platform_mentions() -> list[str]:
    out: list[str] = []
    for path in _module_files(_AXRANGE_ROOT, exclude_dirs=_ADAPTER_ROOTS):
        if path in _ALLOWED_DEFAULT_PLATFORM:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "DEFAULT_PLATFORM":
                out.append(f"{path.relative_to(_REPO_ROOT)}:{node.lineno}: DEFAULT_PLATFORM")
            elif isinstance(node, ast.Attribute) and node.attr == "DEFAULT_PLATFORM":
                out.append(f"{path.relative_to(_REPO_ROOT)}:{node.lineno}: .DEFAULT_PLATFORM")
            elif isinstance(node, ast.ImportFrom) and any(a.name == "DEFAULT_PLATFORM" for a in node.names):
                out.append(f"{path.relative_to(_REPO_ROOT)}:{node.lineno}: imports DEFAULT_PLATFORM")
    return sorted(out)


# --- the gate ----------------------------------------------------------------


def test_the_engine_imports_a_platform_adapter_only_through_the_factory():
    violations = _violations()
    assert not violations, (
        "the engine must reach a deploy platform through `axrange.platform` "
        "(ARCHITECTURE §13; CONVENTIONS §4 'platform-specific is adapter-only') -- "
        f"found {len(violations)} direct import(s) of a platform package:\n  "
        + "\n  ".join(violations)
    )


def test_the_factory_is_the_only_importer_of_the_libvirt_adapter():
    """`adapter_libvirt` is allowed from exactly one engine module."""
    importers = sorted(
        str(p.relative_to(_REPO_ROOT)) for p in _module_files(_AXRANGE_ROOT, exclude_dirs=_ADAPTER_ROOTS)
        if any(d.startswith("axrange.adapter_libvirt") for _, d in _resolved_imports_from_source(p.read_text(), p))
    )
    assert importers == ["src/axrange/platform/factory.py"], importers


def test_the_allowlist_has_not_gone_stale():
    missing = [str(p.relative_to(_REPO_ROOT)) for p in _ALLOWED_IMPORTERS if not p.is_file()]
    assert not missing, f"_ALLOWED_IMPORTERS names modules that no longer exist: {missing}"
    # Each allowed (file, module) pair must actually still be used -- an entry
    # that no longer needs its exemption should be dropped, so the list only
    # ever shrinks (adapters/proxmox/SPEC.md Notes: "this list shrinks to nothing").
    idle = []
    for p, (allowed, _why) in _ALLOWED_IMPORTERS.items():
        reached = {d for _, d in _resolved_imports_from_source(p.read_text(), p)}
        for mod in sorted(allowed):
            if mod not in reached and not any(d.rsplit(".", 1)[0] == mod for d in reached):
                idle.append(f"{p.relative_to(_REPO_ROOT)} -> {mod}")
    assert not idle, f"_ALLOWED_IMPORTERS entries no longer used: {idle}"


def test_no_engine_module_names_the_default_platform_outside_the_factory():
    """The platform travels with the loaded config (`PlatformConfig.platform`
    from the conf's `PLATFORM=` key; `adapter_for_config`). A call site that
    spells `DEFAULT_PLATFORM` is a site that would silently run a libvirt
    target's config through the Proxmox adapter -- the reviewer's D1."""
    mentions = _default_platform_mentions()
    assert not mentions, (
        "engine modules must not name DEFAULT_PLATFORM (use adapter_for_config(cfg) / "
        f"default_namer()) -- found {len(mentions)}:\n  " + "\n  ".join(mentions)
    )


def test_the_platform_package_itself_is_import_light():
    """`axrange.platform.protocols` is imported by every platform's config
    loader and by the planner while the planner is mid-import; it must not
    import the planner or any adapter (see its module docstring)."""
    path = _AXRANGE_ROOT / "platform" / "protocols.py"
    dotted = {d for _, d in _resolved_imports_from_source(path.read_text(), path)}
    bad = sorted(d for d in dotted if d.startswith("axrange."))
    assert not bad, f"platform/protocols.py imports {bad}; it must stay import-light"


# --- the checker catches the shapes a violation could take ------------------

_FAKE = _AXRANGE_ROOT / "o2control" / "something.py"


def _hits(source: str, path: pathlib.Path = _FAKE) -> list[str]:
    return [d for _, d in _resolved_imports_from_source(source, path) if _is_forbidden(d)]


def test_resolver_catches_module_level_nested_and_dynamic_imports():
    assert _hits("from axrange.adapter_proxmox import ProxmoxAdapter\n")
    assert _hits("import axrange.adapter_proxmox.naming\n")
    assert _hits("def f():\n    from axrange.adapter_proxmox.rest import RestClient\n")
    assert _hits("import importlib\nimportlib.import_module('axrange.adapter_proxmox')\n")
    assert _hits("from axrange import adapter_proxmox\n")
    assert _hits("from axrange.adapter_libvirt import LibvirtAdapter\n")
    assert _hits("def f():\n    from axrange.adapter_libvirt.naming import guest_handle_for_op\n")


def test_resolver_does_not_false_positive_on_the_seam_or_stdlib():
    assert _hits(
        "from axrange.platform import adapter_for\n"
        "from axrange.platform.protocols import GuestExec\n"
        "import platform\n"
        "from axrange.planner.executor import Adapter\n"
    ) == []
