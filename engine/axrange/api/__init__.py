"""axRange control-plane API (`docs/design/11-api-ui.md`).

A UI-agnostic HTTP control plane over the axRange engine. A `cli/`-class
consumer: it **imports** the validator, planner, adapter, and state store and
calls the same library entry points `axrange/cli/main.py` calls — never a
`subprocess` of the CLI. Its contract is clean DTOs (`dtos.py`); it never leaks
raw IR / Proxmox / `OpInstance` shapes upward (the project's "no leaking
platform details up the stack" rule).

Public surface:

  create_app(settings=None) -> FastAPI
      Build the FastAPI application. Used by `axrange serve` (uvicorn) and by
      the test suite (Starlette TestClient). Runs headless (no SPA) by default;
      the SPA co-serve is a Phase-4 concern.

  ApiSettings
      Where the API reads engine inputs from (state DB path, scenarios dir,
      adapter config path, auth token, SPA dir). Resolved from env / CLI flags.

The engine stays untouched (option B); `axrange/api/` is additive.

`fastapi`/`uvicorn` are an OPTIONAL extra (`pip install axrange[api]`) so the
engine/CLI run without a web stack installed (`pyproject.toml`). `create_app`
therefore resolves LAZILY via `__getattr__` (PEP 562) rather than an eager
top-level import — `axrange.api.settings` (no FastAPI import of its own) is
reused by `axrange/cli/main.py` (code-review finding #7, 2026-07-07:
`default_db_path()`), and an eager `from axrange.api.app import create_app`
here would have forced every CLI invocation to import FastAPI just to resolve
that one attribute out of this package's `__init__.py`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from axrange.api.settings import ApiSettings

if TYPE_CHECKING:  # pragma: no cover
    from axrange.api.app import create_app

__all__ = ["ApiSettings", "create_app"]


def __getattr__(name: str):
    if name == "create_app":
        from axrange.api.app import create_app

        return create_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
