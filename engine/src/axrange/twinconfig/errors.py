"""Twin-config error taxonomy — mirrors the shape of `axrange.ir.parser`'s
`ParseError` family (a few coarse, typed failure modes; everything else is
deferred to the four-pass validator once expansion hands off a normal IR)."""
from __future__ import annotations


class TwinConfigError(Exception):
    """Base for all twin-config errors."""


class TwinConfigNotFoundError(TwinConfigError):
    """No twin-config with the given id in the catalog directory."""


class TwinConfigShapeError(TwinConfigError):
    """The twin-config YAML doesn't match the `twin_config:` schema."""


class DeployGateRefusalError(TwinConfigShapeError):
    """The DEPLOY GATE itself refused the expansion -- an authored
    `identity:`/`data:` entry pointing at a node the threshold removed, or a
    segment gated out from under a non-router node still legging it
    (`docs/WORK_ORDER_deploy_gate.md` §4.3).

    A `TwinConfigShapeError` subclass on purpose: every existing
    `except TwinConfigShapeError` keeps catching it unchanged. It exists so a
    surface can distinguish "the gate refused this threshold" (an operator
    can turn the dial) from "this config does not expand at all" (a content
    bug) -- `axrange twin-config show` prints REFUSED vs. UNAVAILABLE.
    """


class TwinManifestError(TwinConfigShapeError):
    """A `twins/<id>/twin.yaml` root manifest is malformed, or disagrees with
    the folder it sits in (a missing tier file, an id that is not the folder
    name, a `containment:` claim the `te` file does not back up) --
    `docs/WORK_ORDER_twin_consolidation.md` §2, `axrange.twinconfig.manifest`.

    A `TwinConfigShapeError` subclass so every existing `--config` error path
    (which catches the base `TwinConfigError`) reports it unchanged."""


class TwinConfigParamError(TwinConfigError):
    """A `${NAME}`/`${NAME:default}` token has no value and no default, or an
    override doesn't coerce to a sane type."""


# `TapPointMaterializationError` REMOVED (`docs/WORK_ORDER_tap_eligibility.md`
# D3, Authority: user, 2026-07-28) -- the `tap_points[]` catalog it guarded
# is retired; a `tap_points:` key in a config now raises `TwinConfigShapeError`
# instead (a stale-content shape problem, same class every other retired/
# unrecognized twin-config field raises).
