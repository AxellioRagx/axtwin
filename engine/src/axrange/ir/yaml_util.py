"""Shared "load one YAML file, fail loudly" primitive.

`axrange.ir.parser` and `axrange.manifest.loader` each open a file, parse it
with a `ruamel.yaml` `YAML` instance, and need to turn a missing file or an
unparseable document into a caller-specific exception (`IncludeError`/
`StructuralError` in the IR parser, `ManifestError` in the manifest loader).
This module factors out just that shared open/parse/wrap step.

Shape assertion (root-must-be-a-mapping, defaulting an empty file to `{}`)
deliberately stays with each caller: the two call sites want different
exception types, messages, and — for the IR parser — sometimes no assertion
at all (fragment-vs-main-file mapping checks emit different rule IDs).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError


class YamlLoadError(Exception):
    """Raised by `load_yaml_raw` on a missing file or a YAML parse error.

    `not_found` distinguishes the two failure modes so callers can pick the
    right domain-specific exception / rule-id without needing to catch
    `FileNotFoundError`/`YAMLError` themselves.
    """

    def __init__(self, message: str, *, not_found: bool) -> None:
        super().__init__(message)
        self.not_found = not_found


def load_yaml_raw(path: str | Path, *, loader: YAML) -> Any:
    """Open `path` and parse it with `loader`.

    Returns the loaded value as-is (`None` for an empty file, a list/scalar
    if the document isn't a mapping — callers assert the shape they
    require). Raises `YamlLoadError` wrapping `FileNotFoundError`/`YAMLError`.
    """
    p = Path(path)
    try:
        with p.open("r", encoding="utf-8") as f:
            return loader.load(f)
    except FileNotFoundError as e:
        raise YamlLoadError(f"File not found: {p}", not_found=True) from e
    except YAMLError as e:
        raise YamlLoadError(f"YAML parse error in {p}: {e}", not_found=False) from e
