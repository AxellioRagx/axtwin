"""Templates (`docs/design/canvas-add-panel.md` §5, D3): a slice of a twin
config with settings, dropped on the canvas as one undo step.

A template lives at `<catalog>/templates/<id>/template.yaml` (the folder
beside the twin write root, `catalog/templates/` in a checkout). Built-in ones
ship there marked `builtin: true` and are read-only through the API.

The file::

    template:
      id: office-segment
      builtin: true
      label: Office segment
      description: ...
      icon: office
      params:   [{name, label, type: int|percent|name|cidr, default, min?, max?}]
      ports:    [{name, device}]          # open connections wired after a drop
      fragment:                           # twin-config vocabulary + {expr}
        segments: [{name, cidr, zone_type?}]
        nodes:    [{name, kind?, role?, image? | profile?, count?, net? | nets?, ...}]
        links:    [...]
        conduits: [...]
        assignments: [{node, persona, intensity?, schedule?}]   # tfc vocabulary
      layout:   {"<node or segment name>": {x, y}}             # relative

**Expansion** (`expand`, mirrored by the canvas's `add/template.ts` -- the two
must agree): every string in `fragment`, `ports` and `layout` has its
`{expr}` parts replaced. `expr` is a parameter name or arithmetic over them
(`+ - * /`, parentheses, `round floor ceil min max`); a string that is ONE
`{expr}` with a numeric result becomes that number. A node whose `count`
comes out 0 or less is dropped, with the links and assignments naming it. A
node's `profile:` becomes its `image:` from the image profiles (an explicit
`image:` wins). Segment CIDRs are placeholders: the canvas re-allocates them
at a drop (the first segment takes a `cidr` setting that is not `auto`).
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Optional

from axrange.api import dtos
from axrange.api.service import ApiError, _is_safe_catalog_ref, _write_root

FILE = "template.yaml"
PARAM_TYPES = ("int", "percent", "name", "cidr")
_FUNCS = {"round": lambda *a: int(math.floor(a[0] + 0.5)), "floor": lambda *a: math.floor(a[0]),
          "ceil": lambda *a: math.ceil(a[0]), "min": min, "max": max}
_TOKEN = re.compile(r"\s*(?:(\d+(?:\.\d+)?)|([A-Za-z_][A-Za-z0-9_]*)|(.))")
_EXPR = re.compile(r"\{([^{}]+)\}")


class TemplateError(Exception):
    """A template that cannot be read or expanded."""


# ---------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------
def _tokens(text: str) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    pos = 0
    text = text.strip()
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            break
        pos = m.end()
        if m.group(1) is not None:
            out.append(("num", float(m.group(1))))
        elif m.group(2) is not None:
            out.append(("id", m.group(2)))
        elif m.group(3) and not m.group(3).isspace():
            out.append(("op", m.group(3)))
    return out


def evaluate(expr: str, values: dict[str, Any]) -> Any:
    """`expr` over `values`: a lone name yields its value as is (a string for
    a name/cidr setting); otherwise arithmetic, numbers only."""
    toks = _tokens(expr)
    if len(toks) == 1 and toks[0][0] == "id" and toks[0][1] not in _FUNCS:
        name = toks[0][1]
        if name not in values:
            raise TemplateError(f"unknown setting {name!r} in {{{expr}}}")
        return values[name]
    i = 0

    def peek() -> Optional[tuple[str, Any]]:
        return toks[i] if i < len(toks) else None

    def take(kind: str, val: Any = None) -> tuple[str, Any]:
        nonlocal i
        t = peek()
        if not t or t[0] != kind or (val is not None and t[1] != val):
            raise TemplateError(f"bad expression {{{expr}}}")
        i += 1
        return t

    def factor() -> float:
        nonlocal i
        t = peek()
        if t is None:
            raise TemplateError(f"bad expression {{{expr}}}")
        if t == ("op", "-"):
            i += 1
            return -factor()
        if t == ("op", "("):
            i += 1
            v = add()
            take("op", ")")
            return v
        if t[0] == "num":
            i += 1
            return t[1]
        if t[0] == "id":
            i += 1
            if peek() == ("op", "("):
                fn = _FUNCS.get(t[1])
                if fn is None:
                    raise TemplateError(f"unknown function {t[1]!r} in {{{expr}}}")
                i += 1
                args = [add()]
                while peek() == ("op", ","):
                    i += 1
                    args.append(add())
                take("op", ")")
                return float(fn(*args))
            v = values.get(t[1])
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                raise TemplateError(f"setting {t[1]!r} is not a number in {{{expr}}}")
            return float(v)
        raise TemplateError(f"bad expression {{{expr}}}")

    def mul() -> float:
        nonlocal i
        v = factor()
        while peek() in (("op", "*"), ("op", "/")):
            op = peek()[1]  # type: ignore[index]
            i += 1
            r = factor()
            if op == "*":
                v *= r
            else:
                if r == 0:
                    raise TemplateError(f"division by zero in {{{expr}}}")
                v /= r
        return v

    def add() -> float:
        nonlocal i
        v = mul()
        while peek() in (("op", "+"), ("op", "-")):
            op = peek()[1]  # type: ignore[index]
            i += 1
            r = mul()
            v = v + r if op == "+" else v - r
        return v

    v = add()
    if i != len(toks):
        raise TemplateError(f"bad expression {{{expr}}}")
    return int(v) if float(v).is_integer() else v


def substitute(value: Any, values: dict[str, Any]) -> Any:
    """`{expr}` replaced throughout `value` (strings, lists, mappings, keys too)."""
    if isinstance(value, str):
        whole = _EXPR.fullmatch(value.strip())
        if whole:
            return evaluate(whole.group(1), values)
        return _EXPR.sub(lambda m: str(evaluate(m.group(1), values)), value)
    if isinstance(value, list):
        return [substitute(v, values) for v in value]
    if isinstance(value, dict):
        return {substitute(k, values) if isinstance(k, str) else k: substitute(v, values) for k, v in value.items()}
    return value


# ---------------------------------------------------------------------------
# Settings and expansion
# ---------------------------------------------------------------------------
def settings_values(tpl: dict[str, Any], given: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Each setting's value: `given` over the default, clamped to the type."""
    given = given or {}
    out: dict[str, Any] = {}
    for p in tpl.get("params") or []:
        name, kind = p["name"], p.get("type", "int")
        v = given.get(name, p.get("default"))
        if kind in ("int", "percent"):
            try:
                v = int(v)
            except (TypeError, ValueError):
                v = int(p.get("default") or 0)
            lo = p.get("min", 0)
            hi = p.get("max", 100 if kind == "percent" else None)
            v = max(lo, v) if lo is not None else v
            v = min(hi, v) if hi is not None else v
        else:
            v = "" if v is None else str(v)
        out[name] = v
    return out


def expand(tpl: dict[str, Any], given: Optional[dict[str, Any]] = None,
           profiles: Optional[dict[str, Optional[str]]] = None) -> dict[str, Any]:
    """The template's fragment, ports and layout with its settings applied."""
    values = settings_values(tpl, given)
    frag = substitute(tpl.get("fragment") or {}, values)
    nodes = []
    dropped: set[str] = set()
    for n in frag.get("nodes") or []:
        n = dict(n)
        count = n.get("count", 1)
        if isinstance(count, (int, float)) and count <= 0:
            dropped.add(str(n.get("name")))
            continue
        prof = n.pop("profile", None)
        if prof and not n.get("image"):
            n["image"] = (profiles or {}).get(prof) or ""
        nodes.append(n)
    links = [lk for lk in frag.get("links") or []
             if (lk.get("a") or {}).get("device") not in dropped and (lk.get("b") or {}).get("device") not in dropped]
    assignments = [a for a in frag.get("assignments") or [] if a.get("node") not in dropped]
    ports = [p for p in substitute(tpl.get("ports") or [], values) if p.get("device") not in dropped]
    return {
        "values": values,
        "segments": list(frag.get("segments") or []),
        "nodes": nodes,
        "links": links,
        "conduits": list(frag.get("conduits") or []),
        "assignments": assignments,
        "ports": ports,
        "layout": substitute(tpl.get("layout") or {}, values),
    }


def template_document(tpl: dict[str, Any], twin_id: str, given: Optional[dict[str, Any]] = None,
                      profiles: Optional[dict[str, Optional[str]]] = None) -> dict[str, str]:
    """The expanded fragment wrapped in a minimal twin: the document the
    twin-config validator judges on save (§5.3)."""
    from ruamel.yaml import YAML
    import io

    x = expand(tpl, given, profiles)
    tc: dict[str, Any] = {"id": twin_id, "description": f"template {tpl.get('id')} expanded", "segments": x["segments"], "nodes": x["nodes"]}
    if x["links"]:
        tc["links"] = x["links"]
    if x["conduits"]:
        tc["conduits"] = x["conduits"]

    def dump(obj: Any) -> str:
        y = YAML(typ="safe")
        y.default_flow_style = False
        buf = io.StringIO()
        y.dump(obj, buf)
        return buf.getvalue()

    files = {"te.yaml": dump({"twin_config": tc})}
    tiers: dict[str, Any] = {"te": {"default": "te.yaml"}}
    if x["assignments"]:
        files["tfc/default.yaml"] = dump({"traffic-profile": {"target": twin_id, "assignments": x["assignments"]}})
        tiers["tfc"] = {"default": "tfc/default.yaml"}
    files["twin.yaml"] = dump({"twin": {"id": twin_id, "description": f"template {tpl.get('id')}", "containment": "uncontained", "tiers": tiers}})
    return files


# ---------------------------------------------------------------------------
# The catalog
# ---------------------------------------------------------------------------
def templates_root(settings: Any) -> Path:
    """`AXRANGE_TEMPLATES_DIR`, else `templates/` beside the catalog's write root."""
    if getattr(settings, "templates_dir", ""):
        return Path(settings.templates_dir)
    return _write_root(settings).parent / "templates"


def _load_file(path: Path) -> dict[str, Any]:
    from ruamel.yaml import YAML

    try:
        raw = YAML(typ="safe").load(path.read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001 -- a YAML error names the file
        raise TemplateError(f"{path}: unreadable: {e}") from e
    tpl = raw.get("template") if isinstance(raw, dict) else None
    if not isinstance(tpl, dict):
        raise TemplateError(f"{path}: no 'template:' mapping")
    check_shape(tpl)
    return tpl


def check_shape(tpl: dict[str, Any]) -> None:
    """The structure the canvas relies on; semantics are the validator's."""
    tid = tpl.get("id")
    if not isinstance(tid, str) or not _is_safe_catalog_ref(tid):
        raise TemplateError(f"template id {tid!r} is not a usable id")
    params = tpl.get("params") or []
    if not isinstance(params, list):
        raise TemplateError(f"template {tid!r}: params must be a list")
    seen = set()
    for p in params:
        if not isinstance(p, dict) or not isinstance(p.get("name"), str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", p["name"]):
            raise TemplateError(f"template {tid!r}: each param needs a name (letters, digits, _)")
        if p.get("type", "int") not in PARAM_TYPES:
            raise TemplateError(f"template {tid!r}: param {p['name']!r} has an unknown type {p.get('type')!r}")
        if p["name"] in seen:
            raise TemplateError(f"template {tid!r}: param {p['name']!r} twice")
        seen.add(p["name"])
    frag = tpl.get("fragment")
    if not isinstance(frag, dict) or not isinstance(frag.get("nodes") or [], list) or not isinstance(frag.get("segments") or [], list):
        raise TemplateError(f"template {tid!r}: fragment must hold segments and nodes lists")
    if not (frag.get("nodes") or frag.get("segments")):
        raise TemplateError(f"template {tid!r}: the fragment is empty")
    for key in ("ports",):
        if not isinstance(tpl.get(key) or [], list):
            raise TemplateError(f"template {tid!r}: {key} must be a list")
    if not isinstance(tpl.get("layout") or {}, dict):
        raise TemplateError(f"template {tid!r}: layout must be a mapping")


def _dto(tpl: dict[str, Any]) -> dtos.TemplateDTO:
    return dtos.TemplateDTO(
        id=tpl["id"], builtin=bool(tpl.get("builtin")), label=str(tpl.get("label") or tpl["id"]),
        description=str(tpl.get("description") or ""), icon=str(tpl.get("icon") or ""),
        params=list(tpl.get("params") or []), ports=list(tpl.get("ports") or []),
        fragment=dict(tpl.get("fragment") or {}), layout=dict(tpl.get("layout") or {}),
    )


def read_template(settings: Any, template_id: str) -> dict[str, Any]:
    if not _is_safe_catalog_ref(template_id):
        raise ApiError(404, f"template not found: {template_id}")
    path = templates_root(settings) / template_id / FILE
    if not path.is_file():
        raise ApiError(404, f"template not found: {template_id}")
    try:
        tpl = _load_file(path)
    except TemplateError as e:
        raise ApiError(500, str(e)) from e
    if tpl["id"] != template_id:
        raise ApiError(500, f"{path}: id {tpl['id']!r} does not match its folder {template_id!r}")
    return tpl


def list_templates(settings: Any) -> dtos.TemplateListDTO:
    root = templates_root(settings)
    out: list[dtos.TemplateDTO] = []
    warnings: list[str] = []
    if root.is_dir():
        for folder in sorted(root.iterdir()):
            path = folder / FILE
            if not path.is_file():
                continue
            try:
                tpl = _load_file(path)
                if tpl["id"] != folder.name:
                    raise TemplateError(f"{path}: id {tpl['id']!r} does not match its folder")
            except TemplateError as e:
                warnings.append(str(e))
                continue
            out.append(_dto(tpl))
    out.sort(key=lambda t: (not t.builtin, t.label.lower()))
    return dtos.TemplateListDTO(templates=out, warnings=warnings)


def template_detail(settings: Any, template_id: str) -> dtos.TemplateDTO:
    return _dto(read_template(settings, template_id))


# ---------------------------------------------------------------------------
# Writes (§5.3): POST / PUT / DELETE, write-token gated in `app.py`
# ---------------------------------------------------------------------------
CHECK_ID = "template-check"


def _profiles(settings: Any) -> dict[str, Optional[str]]:
    from axrange.api import image_profiles

    return {p.name: p.image for p in image_profiles.list_image_profiles(settings).profiles}


def validate_on_save(settings: Any, tpl: dict[str, Any]) -> None:
    """§5.3: the fragment expanded with its default settings and wrapped in a
    minimal twin must pass the twin-config validator -- a 422 with the
    findings otherwise (the same refusal shape a twin-config write uses)."""
    from axrange.api import service

    try:
        files = template_document(tpl, CHECK_ID, None, _profiles(settings))
    except TemplateError as e:
        raise ApiError(422, f"template {tpl.get('id')!r}: {e}", detail={"code": "invalid", "findings": []}) from e
    service._validate_twin_document(settings, CHECK_ID, files, raise_on_fail=True)


def _checked(body: dict[str, Any], *, template_id: Optional[str] = None) -> dict[str, Any]:
    tpl = dict(body or {})
    if template_id is not None and tpl.get("id") not in (None, template_id):
        raise ApiError(400, f"body id {tpl.get('id')!r} does not match the path id {template_id!r}")
    if template_id is not None:
        tpl["id"] = template_id
    if tpl.get("builtin"):
        raise ApiError(400, "built-in templates ship with the catalog; a saved template cannot be built in")
    tpl.pop("builtin", None)
    try:
        check_shape(tpl)
    except TemplateError as e:
        raise ApiError(400, str(e)) from e
    return tpl


def _dump(tpl: dict[str, Any]) -> str:
    import io

    from ruamel.yaml import YAML

    y = YAML(typ="safe")
    y.default_flow_style = False
    buf = io.StringIO()
    y.dump({"template": tpl}, buf)
    return "# Canvas template, saved from the canvas (docs/design/canvas-add-panel.md §5.3).\n" + buf.getvalue()


def create_template(settings: Any, body: dict[str, Any]) -> dtos.TemplateDTO:
    """`POST /api/templates`: a new template; an id that exists is a 409."""
    import os
    import shutil

    from axrange.api.console_files import atomic_write

    tpl = _checked(body)
    validate_on_save(settings, tpl)
    folder = templates_root(settings) / tpl["id"]
    try:
        folder.parent.mkdir(parents=True, exist_ok=True)
        os.mkdir(folder)  # the claim: exactly one writer wins
    except FileExistsError:
        raise ApiError(409, f"template {tpl['id']!r} already exists") from None
    except OSError as e:
        raise ApiError(500, f"cannot create {folder}: {e}") from e
    try:
        atomic_write(folder / FILE, _dump(tpl))
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return template_detail(settings, tpl["id"])


def _existing_saved(settings: Any, template_id: str) -> Path:
    current = read_template(settings, template_id)  # 404 when absent or unsafe
    if current.get("builtin"):
        raise ApiError(403, f"template {template_id!r} is built in and read-only; save a copy under another id", detail={"code": "builtin"})
    return templates_root(settings) / template_id


def write_template(settings: Any, template_id: str, body: dict[str, Any]) -> dtos.TemplateDTO:
    """`PUT /api/templates/{id}`: replace a saved template; a built-in is a 403."""
    from axrange.api.console_files import atomic_write

    folder = _existing_saved(settings, template_id)
    tpl = _checked(body, template_id=template_id)
    validate_on_save(settings, tpl)
    atomic_write(folder / FILE, _dump(tpl))
    return template_detail(settings, template_id)


def delete_template(settings: Any, template_id: str) -> None:
    """`DELETE /api/templates/{id}`: a saved template only; a built-in is a 403."""
    import shutil

    shutil.rmtree(_existing_saved(settings, template_id))
