"""Makes `tests/` a real package rather than an implicit namespace package.

Not optional, and not merely a style match with `tests/acquire/__init__.py` (which
predates this file and is itself empty for the same reason). Without this file, pytest's
default `prepend` import mode stops its package-root walk at `tests/` (the first
directory without an `__init__.py`) and imports `tests/conftest.py` under the BARE name
`"conftest"` -- while `pythonpath = ["."]` (pyproject.toml) lets test files ALSO import
the identical file as `tests.conftest` via an ordinary `from tests.conftest import X`.
Those are two independent imports under two different dotted names, so Python creates
two separate module objects for the one file -- and, in turn, two non-identical classes
for anything defined in it. `pytest.raises(SomeExceptionDefinedThere)` then never
matches an instance raised from the OTHER copy (the one pytest's own fixture machinery
actually uses), and the exception escapes as if uncaught, even though the guard fired
exactly as intended. Demonstrated for real: `tests/conftest.py`'s `RealNetworkEgressDenied`
(the repo-wide network guard) and `tests/acquire/conftest.py`'s `_deny_connect`/
`SocketUsedInTest` were BOTH already double-imported this way (`sys.modules` held both
`"conftest"`/`"tests.conftest"` and `"acquire.conftest"`/`"tests.acquire.conftest"`
simultaneously) -- silently harmless everywhere an existing test only ever asserted
`is not` (which holds regardless of which copy either side holds), and not silent at
all the moment a test needed `pytest.raises(...)` to match by identity. With this file
present, the package-root walk continues past `tests/` to the repo root (which has no
`__init__.py`), so both `tests/conftest.py` and `tests/acquire/conftest.py` are now
imported exactly once, under exactly the dotted names `tests.conftest` and
`tests.acquire.conftest` that the rest of the suite already imports them by.
"""
