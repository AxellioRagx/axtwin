"""Unit tests for `axrange.twinconfig.params` — the minimal `${NAME}`/
`${NAME:default}` substitution engine (docs/OPEN_QUESTIONS.md Q2's deferred
idea, reused per docs/WORK_ORDER_twin_configs.md §2)."""
from __future__ import annotations

import pytest

from axrange.twinconfig.errors import TwinConfigParamError
from axrange.twinconfig.params import (
    coerce_scalar,
    parse_param_flags,
    resolve_params,
    substitute,
)


def test_coerce_scalar_types():
    assert coerce_scalar("25") == 25
    assert isinstance(coerce_scalar("25"), int)
    assert coerce_scalar("true") is True
    assert coerce_scalar("False") is False
    assert coerce_scalar("3.5") == 3.5
    assert coerce_scalar("corp.axr.lab") == "corp.axr.lab"


def test_resolve_params_merges_declared_and_overrides():
    declared = {"N_WORKSTATIONS": 25, "DOMAIN": "corp.axr.lab"}
    resolved = resolve_params(declared, {"N_WORKSTATIONS": "3"})
    assert resolved["N_WORKSTATIONS"] == 3  # coerced to declared default's type (int)
    assert resolved["DOMAIN"] == "corp.axr.lab"


def test_resolve_params_override_unknown_name_is_accepted_and_coerced():
    resolved = resolve_params({}, {"FABRIC": "true"})
    assert resolved["FABRIC"] is True


def test_resolve_params_bool_override_rejects_garbage():
    with pytest.raises(TwinConfigParamError):
        resolve_params({"FABRIC": False}, {"FABRIC": "maybe"})


def test_resolve_params_int_override_rejects_non_int():
    with pytest.raises(TwinConfigParamError):
        resolve_params({"N": 5}, {"N": "five"})


def test_substitute_full_token_preserves_native_type():
    params = {"N_WORKSTATIONS": 3}
    assert substitute("${N_WORKSTATIONS:25}", params) == 3
    assert substitute("${N_WORKSTATIONS}", params) == 3


def test_substitute_inline_default_used_when_param_absent():
    assert substitute("${N_WORKSTATIONS:25}", {}) == 25


def test_substitute_missing_param_no_default_raises():
    with pytest.raises(TwinConfigParamError):
        substitute("${MISSING}", {})


def test_substitute_embedded_token_concatenates_as_string():
    assert substitute("corp-${SITE:hq}-lan", {}) == "corp-hq-lan"


def test_substitute_recurses_through_dict_and_list():
    tree = {"a": ["${N:1}", {"b": "${N:1}"}], "c": 5}
    out = substitute(tree, {"N": 7})
    assert out == {"a": [7, {"b": 7}], "c": 5}


def test_parse_param_flags():
    assert parse_param_flags(["A=1", "B=two"]) == {"A": "1", "B": "two"}
    assert parse_param_flags(None) == {}
    assert parse_param_flags([]) == {}


def test_parse_param_flags_rejects_malformed():
    with pytest.raises(TwinConfigParamError):
        parse_param_flags(["NOVALUE"])
