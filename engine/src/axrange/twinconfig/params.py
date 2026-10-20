"""Minimal `${NAME}` / `${NAME:default}` substitution — the deferred idea
from `docs/OPEN_QUESTIONS.md` Q2 ("Deploy-time parameters
(`${N_FIN_WORKSTATIONS:5}`)"), reused rather than reinvented into a second
templating language (work order §2's explicit instruction).

Resolution order for a token `${NAME}` or `${NAME:default}` found anywhere in
a twin-config's raw YAML tree (except the `params:` block itself, which is
never substituted — it's the declared name/default table, not template
body):

  1. an explicit `--param NAME=value` override (CLI/API caller-supplied),
  2. the config's own `twin_config.params: {NAME: default}` declaration,
  3. the token's own inline `${NAME:default}` fallback,
  4. otherwise: `TwinConfigParamError` — an undeclared, unoverridden,
     default-less parameter is a content bug, not a silent empty string.

A token that is the ENTIRE string value (e.g. `count: "${N_WORKSTATIONS:25}"`)
resolves to its NATIVE type (int/float/bool/str) via `_coerce` — so `count`
becomes a real `int` after substitution, not the string `"25"`. A token
embedded in a larger string (e.g. `"corp-${SITE:hq}"`) always resolves to its
string form (concatenation only makes sense as text).
"""
from __future__ import annotations

import re
from typing import Any

from axrange.twinconfig.errors import TwinConfigParamError

# A token's NAME must look like an env-style constant (matches the brief's
# own `N_WORKSTATIONS`/`DOMAIN`/`OT_PROTOCOL`/`FABRIC` examples) — this also
# keeps the regex simple and unambiguous against YAML's own `${...}`-free
# syntax (no collision risk with anything else in the file).
_TOKEN_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::([^}]*))?\}")
_FULL_TOKEN_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)(?::([^}]*))?\}$")


def coerce_scalar(raw: str) -> Any:
    """Best-effort string -> native type: bool, then int, then float, else
    the original string. Used both for a bare `${NAME:default}` inline
    default and for a CLI `--param NAME=value` override with no declared
    default to coerce against."""
    low = raw.strip().lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


def _coerce_like(raw: str, like: Any) -> Any:
    """Coerce a CLI override string `raw` to the type of the config's own
    declared default `like` (so `--param N_WORKSTATIONS=3` against a default
    of `25` (int) yields `3` (int), not the string `"3"`)."""
    if isinstance(like, bool):
        low = raw.strip().lower()
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off"):
            return False
        raise TwinConfigParamError(
            f"parameter override {raw!r} is not a valid bool "
            "(expected true/false/1/0/yes/no/on/off)"
        )
    if isinstance(like, int) and not isinstance(like, bool):
        try:
            return int(raw)
        except ValueError as e:
            raise TwinConfigParamError(
                f"parameter override {raw!r} is not a valid int"
            ) from e
    if isinstance(like, float):
        try:
            return float(raw)
        except ValueError as e:
            raise TwinConfigParamError(
                f"parameter override {raw!r} is not a valid float"
            ) from e
    return raw  # like is a str (or anything else) -> keep as string


def resolve_params(
    declared: dict[str, Any], overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Merge a twin-config's declared `params: {NAME: default}` with
    caller-supplied `--param NAME=value` string overrides into one resolved
    `dict[str, Any]`. An override for a name the config didn't declare is
    still accepted (coerced via `coerce_scalar`) — a config's `params:` block
    is a documentation/defaults table, not a closed allow-list; any
    `${NAME:default}` token anywhere can introduce a new overridable name.
    """
    resolved: dict[str, Any] = dict(declared)
    for key, raw_value in (overrides or {}).items():
        if key in resolved and resolved[key] is not None:
            resolved[key] = _coerce_like(raw_value, resolved[key])
        else:
            resolved[key] = coerce_scalar(raw_value)
    return resolved


def parse_param_flags(pairs: list[str] | None) -> dict[str, str]:
    """`["N_WORKSTATIONS=3", "DOMAIN=test.lab"]` -> `{"N_WORKSTATIONS": "3",
    "DOMAIN": "test.lab"}`. Raises `TwinConfigParamError` on a malformed
    `--param` flag (no `=`)."""
    out: dict[str, str] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise TwinConfigParamError(
                f"--param must be KEY=VALUE, got {pair!r}"
            )
        key, _, value = pair.partition("=")
        key = key.strip()
        if not key:
            raise TwinConfigParamError(f"--param has an empty key: {pair!r}")
        out[key] = value
    return out


def _resolve_token(name: str, inline_default: str | None, params: dict[str, Any]) -> Any:
    if name in params and params[name] is not None:
        return params[name]
    if inline_default is not None:
        return coerce_scalar(inline_default)
    raise TwinConfigParamError(
        f"parameter '{name}' has no override, no declared default in "
        "twin_config.params, and no inline ${...:default} — cannot resolve"
    )


def _substitute_str(value: str, params: dict[str, Any]) -> Any:
    full = _FULL_TOKEN_RE.match(value)
    if full:
        name, inline_default = full.groups()
        return _resolve_token(name, inline_default, params)

    def _repl(match: "re.Match[str]") -> str:
        name, inline_default = match.groups()
        return str(_resolve_token(name, inline_default, params))

    return _TOKEN_RE.sub(_repl, value)


def substitute(obj: Any, params: dict[str, Any]) -> Any:
    """Recursively substitute `${NAME}`/`${NAME:default}` tokens through a
    dict/list/str tree. Non-string scalars (int/float/bool/None) pass
    through unchanged."""
    if isinstance(obj, str):
        return _substitute_str(obj, params)
    if isinstance(obj, dict):
        return {k: substitute(v, params) for k, v in obj.items()}
    if isinstance(obj, list):
        return [substitute(v, params) for v in obj]
    return obj


_PARAMS_LINE_RE = re.compile(r"^(\s+)params:\s*(#.*)?$")
_PARAM_KEY_RE = re.compile(r"^(\s+)([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([^#]*?)\s*(#\s*(.*))?$")


def _top_params_index(lines: list[str]) -> int | None:
    """Line index of `twin_config:`'s OWN `params:` key -- a nested `params:`
    (inside a node, a fabric block, ...) is never it. None when absent."""
    top = next((i for i, ln in enumerate(lines) if re.match(r"^twin_config:\s*(#.*)?$", ln)), None)
    if top is None:
        return None
    child: int | None = None
    for i in range(top + 1, len(lines)):
        ln = lines[i]
        st = ln.strip()
        if not st or st.startswith("#"):
            continue
        indent = len(ln) - len(ln.lstrip())
        if indent == 0:
            return None  # left twin_config
        if child is None:
            child = indent
        if indent == child and re.match(r"^\s+params:(\s|$)", ln):
            return i
    return None


def describe_params(text: str, declared: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Every deploy-time parameter a twin config takes, for a picker
    (Unified Console fix round 2): the `params:` block's declared names (in
    file order) with their defaults and the comment written above or beside
    each, then every inline-only `${NAME:default}` token.

    `text` is the raw te.yaml; `declared` its parsed `twin_config.params`.
    Returns `[{name, default, description, declared}]`. Read-only and total:
    a comment it cannot attribute is simply not a description."""
    declared = dict(declared or {})
    docs: dict[str, str] = {}
    lines = text.splitlines()
    top = _top_params_index(lines)
    for i, ln in enumerate(lines):
        m = _PARAMS_LINE_RE.match(ln) if i == top else None
        if not m:
            continue
        base = len(m.group(1))
        buf: list[str] = []
        # (key, key indent) whose inline comment a deeper-indented comment
        # line continues (`KEY: v  # first half` / `      # second half`).
        cont: tuple[str, int] | None = None
        for body in lines[i + 1:]:
            stripped = body.strip()
            if not stripped:
                buf, cont = [], None
                continue
            indent = len(body) - len(body.lstrip())
            if stripped.startswith("#"):
                if indent <= base:
                    break
                text_ = stripped.lstrip("#").strip()
                if cont is not None and indent > cont[1]:
                    docs[cont[0]] = f"{docs.get(cont[0], '')} {text_}".strip()
                else:
                    cont = None
                    buf.append(text_)
                continue
            if indent <= base:
                break
            km = _PARAM_KEY_RE.match(body)
            cont = None
            if km and km.group(2) in declared:
                parts = [b for b in buf if b]
                if km.group(5):
                    parts.append(km.group(5).strip())
                    cont = (km.group(2), len(km.group(1)))
                if parts:
                    docs[km.group(2)] = " ".join(parts)
            buf = []
        break
    out = [
        {"name": k, "default": v, "description": docs.get(k), "declared": True}
        for k, v in declared.items()
    ]
    seen = set(declared)
    for name, inline in _TOKEN_RE.findall(text):
        if name in seen:
            continue
        seen.add(name)
        out.append({
            "name": name, "default": coerce_scalar(inline) if inline != "" else None,
            "description": None, "declared": False,
        })
    return out


_PARAMS_INLINE_RE = re.compile(r"^(\s+)params:\s*(\{.*\})\s*(#.*)?$")


def declared_params_from_text(text: str) -> dict[str, Any]:
    """The `twin_config.params:` table read from the raw text ALONE -- only
    that block is YAML-parsed, never the whole (possibly 700 KB) file, so a
    caller that already paid for one full parse does not pay for another.
    `{}` when there is no block or it does not parse."""
    from ruamel.yaml import YAML

    lines = text.splitlines()
    top = _top_params_index(lines)
    for i, ln in enumerate(lines):
        if i != top:
            continue
        mi = _PARAMS_INLINE_RE.match(ln)
        block: list[str] = []
        if mi:
            block = [f"params: {mi.group(2)}"]
        else:
            m = _PARAMS_LINE_RE.match(ln)
            if not m:
                continue
            base = len(m.group(1))
            block = ["params:"]
            for body in lines[i + 1:]:
                if body.strip() and not body.strip().startswith("#") and (
                    len(body) - len(body.lstrip()) <= base
                ):
                    break
                block.append(body[base:] if len(body) > base else "")
        try:
            doc = YAML(typ="safe").load("\n".join(block)) or {}
        except Exception:  # noqa: BLE001 -- a picker aid, never a refusal
            return {}
        vals = doc.get("params") if isinstance(doc, dict) else None
        return dict(vals) if isinstance(vals, dict) else {}
    return {}
