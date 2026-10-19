"""Cluster-free tests for the two pipeline helper scripts under `scripts/`.

Covers:
  * `scripts/inject_lessons.py` — the lessons-injection matcher
    (`parse_entries()` shape + `scope_matches()` precision).
  * `scripts/doc-stats.py` — the volatile-counter computation. Config-driven
    since workflow-template v7: the counters live in the repo-root
    `.claude-doc-stats.json`, not in the script, so these tests assert that
    THIS PROJECT'S CONFIG reproduces the op/rule registries, that its claim
    patterns actually bind to the policed docs, and that the `check(config,
    stats)` drift gate flags and clears correctly.

The scripts live in `scripts/` (not a package); `doc-stats.py` also has a
hyphen and so is not importable by name. Both are loaded here via
`importlib.util.spec_from_file_location` from the repo-root `scripts/` dir.

Counter expectations are DERIVED from the source registries (not hardcoded),
and lesson expectations are asserted on the SET of matched `id`s (not exact
counts), so both stay green as ops/rules/lessons are added later.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

from axrange.ops import list_actions_and_waits, list_barriers
from axrange.validator import list_rules

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"


def _load(mod_name: str, filename: str) -> ModuleType:
    """Load a `scripts/<filename>` file as an importable module object.

    Using the real file path keeps each script's own `__file__`-derived
    `REPO`/`LESSONS` paths pointing at the real repo, so the loaded module
    reads the real `docs/LESSONS.md` / registries with no extra wiring.
    """
    spec = importlib.util.spec_from_file_location(mod_name, SCRIPTS / filename)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


inject_lessons = _load("inject_lessons", "inject_lessons.py")
doc_stats = _load("doc_stats", "doc-stats.py")


# ---------------------------------------------------------------------------
# scripts/inject_lessons.py
# ---------------------------------------------------------------------------
def _matched_ids(target: str) -> set[str]:
    """The set of LES ids whose Scope matches `target`, via the real matcher."""
    return {
        e["id"]
        for e in inject_lessons.parse_entries()
        if inject_lessons.scope_matches(e.get("scope", ""), target)
    }


def test_parse_entries_nonempty_and_shaped() -> None:
    entries = inject_lessons.parse_entries()
    assert entries, "docs/LESSONS.md yielded no entries"
    # Every parsed entry carries the header-derived id/title and a Scope field.
    for e in entries:
        assert {"id", "title", "scope"} <= set(e), f"malformed entry: {e}"
        assert e["id"].startswith("LES-")
        assert e["title"]
        assert e["scope"]


def test_scope_matches_is_precise_for_ssh_path() -> None:
    # A path scope resolves to the right module file; the executor-scoped
    # LES-007 (`planner/executor.py`) must NOT over-match a sibling adapter file.
    matched = _matched_ids("axrange/adapter_proxmox/ssh.py")
    assert "LES-005" in matched  # Scope: adapter_proxmox/ssh.py
    assert "LES-011" in matched  # Scope: adapter_proxmox/naming.py + ssh.py
    assert "LES-007" not in matched  # Scope: planner/executor.py — different file


def test_scope_matches_fabric_render_path() -> None:
    matched = _matched_ids("axrange/adapter_proxmox/fabric_render.py")
    assert "LES-010" in matched  # Scope: adapter_proxmox/fabric_render.py
    assert "LES-014" in matched  # Scope: adapter_proxmox/fabric_render.py


def test_scope_matches_no_overmatch_on_unknown_path() -> None:
    # A path that no lesson is scoped to matches nothing — generic prose words
    # in the various Scope fields must never match an arbitrary file.
    assert _matched_ids("README_none.txt") == set()


def test_scope_matches_unit_cases() -> None:
    target = "axrange/adapter_proxmox/ssh.py"
    # A bare generic phrase (no path/filename/known-module token) never matches.
    assert inject_lessons.scope_matches("axrange deploy --fresh", target) is False
    # A filename token matches the file with that basename.
    assert inject_lessons.scope_matches("ssh.py", target) is True
    # A module directory token matches any file under that module...
    assert inject_lessons.scope_matches("adapter_proxmox/", target) is True
    assert (
        inject_lessons.scope_matches(
            "adapter_proxmox/", "axrange/adapter_proxmox/rest.py"
        )
        is True
    )


def _executor_matched() -> list[dict]:
    target = "axrange/planner/executor.py"
    return [
        e for e in inject_lessons.parse_entries()
        if inject_lessons.scope_matches(e.get("scope", ""), target)
    ]


def test_render_matched_is_compact() -> None:
    # Compact per-file form: id + title + a CLIPPED Rule, no Origin, and a
    # footer pointing at --full. Each lesson costs a few hundred bytes at most.
    matched = _executor_matched()
    assert matched
    text = inject_lessons.render_matched("axrange/planner/executor.py", matched, None)
    for e in matched:
        assert f"{e['id']} — {e['title']}" in text
    assert "origin:" not in text
    assert "--full" in text
    for line in text.splitlines():
        if line.strip().startswith("Rule:"):
            assert len(line) <= inject_lessons.RULE_CLIP + 20


def test_clip_rule_keeps_first_sentence_and_marks_the_cut() -> None:
    assert inject_lessons.clip_rule("Do X. Then Y.") == "Do X. …"
    assert inject_lessons.clip_rule("Only one.") == "Only one."
    long = "word " * 200
    clipped = inject_lessons.clip_rule(long, limit=50)
    assert clipped.endswith("…") and len(clipped) <= 52


def test_render_matched_dedupes_per_seen_file(tmp_path: Path) -> None:
    matched = _executor_matched()
    seen = tmp_path / "seen.json"
    first = inject_lessons.render_matched("f.py", matched, str(seen))
    assert all(e["id"] in first for e in matched)
    second = inject_lessons.render_matched("f.py", matched, str(seen))
    # Second edit in the same session: only a one-line id reminder remains.
    assert len(second) < 300
    assert "surfaced earlier this session" in second
    assert "Rule:" not in second
    # A different (session, agent) file starts fresh.
    other = inject_lessons.render_matched("f.py", matched, str(tmp_path / "o.json"))
    assert other == first


def test_render_matched_fails_open_on_bad_seen_file(tmp_path: Path) -> None:
    matched = _executor_matched()
    bad = tmp_path / "seen.json"
    bad.write_text("{not json", encoding="utf-8")
    text = inject_lessons.render_matched("f.py", matched, str(bad))
    assert all(f"{e['id']} — " in text for e in matched)
    # An unwritable location (a directory where the file should be) also fails open.
    as_dir = tmp_path / "dir.json"
    as_dir.mkdir()
    text2 = inject_lessons.render_matched("f.py", matched, str(as_dir))
    assert all(f"{e['id']} — " in text2 for e in matched)


def test_full_returns_verbatim_entry() -> None:
    blocks = inject_lessons.full_entries(["LES-007", "12"])
    assert blocks[0].startswith("## LES-007 — ")
    assert "**Rule:**" in blocks[0]
    assert blocks[1].startswith("## LES-012 — ")
    assert inject_lessons.full_entries(["LES-999999"]) == []


hooks = _load("hooks", "hooks.py")


def test_seen_file_keys_on_session_and_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_LESSONS_DEDUPE", raising=False)
    main = hooks._seen_file({"session_id": "s1"})
    sub = hooks._seen_file({"session_id": "s1", "agent_id": "a1"})
    assert main and sub and main != sub
    assert hooks._seen_file({"session_id": "s1"}) == main
    assert hooks._seen_file({}) is None  # no session id -> no dedupe
    monkeypatch.setenv("CLAUDE_LESSONS_DEDUPE", "0")
    assert hooks._seen_file({"session_id": "s1"}) is None


# ---------------------------------------------------------------------------
# scripts/doc-stats.py
# ---------------------------------------------------------------------------
# doc-stats became config-driven at workflow-template v7: the counters no longer
# live in the script (`count_ops()`/`count_rules()` are gone) but in the repo-root
# `.claude-doc-stats.json`, and the API is now `compute(config)` / `check(config,
# stats)`. So the axRange-specific assertions moved down a layer — they now test
# that THIS PROJECT'S CONFIG reproduces the registries, which is the property the
# old script-level tests were really protecting.
def _project_config() -> dict:
    cfg = doc_stats.load_config()
    assert cfg is not None, (
        ".claude-doc-stats.json is missing or unparseable. Without it doc-stats "
        "is a no-op that exits 0, so doc-counter drift stops being enforced "
        "silently — which is exactly the failure this file exists to catch."
    )
    return cfg


def _one_counter(cfg: dict, name: str) -> int | None:
    """Compute a single configured counter.

    Deliberately not `compute(cfg)`: that would also run the `tests` counter,
    which shells out to `pytest --collect-only` and would spawn a nested pytest
    inside this one (the old suite stubbed `count_tests` for the same reason).
    """
    spec = cfg["counters"][name]
    return doc_stats.compute({"counters": {name: spec}})[name]


def test_project_config_loads_and_declares_counters() -> None:
    cfg = _project_config()
    assert cfg.get("counters"), "config declares no counters"
    assert cfg.get("claims"), "config declares no claims — nothing would be policed"
    assert cfg.get("files"), "config policies no files"


def test_configured_ops_counter_matches_op_registry() -> None:
    # Derived, not hardcoded — tracks the op catalog through the config.
    expected = len(list_actions_and_waits()) + len(list_barriers())
    assert _one_counter(_project_config(), "ops") == expected


def test_configured_rules_counter_matches_validator() -> None:
    # Derived, not hardcoded — tracks the validator catalog through the config.
    assert _one_counter(_project_config(), "rules") == len(list_rules())


def test_configured_claims_actually_bind_to_policed_docs() -> None:
    """Every claim pattern must match at least one line in the policed files.

    A config whose regexes match nothing still prints "No counter drift" and
    exits 0 — indistinguishable from a genuinely clean repo. That silent pass is
    the failure mode the v7 rewrite made possible, so pin it: a claim that stops
    binding (a doc reworded, a pattern typo'd) is a broken guard, not a clean one.
    """

    cfg = _project_config()
    for claim in cfg["claims"]:
        pat = re.compile(claim["pattern"])
        hits = sum(
            len(pat.findall(line))
            for rel in cfg["files"]
            for line in (doc_stats.REPO / rel).read_text(encoding="utf-8").splitlines()
        )
        assert hits > 0, (
            f"claim {claim.get('label', claim['counter'])!r} "
            f"(pattern {claim['pattern']!r}) matches no line in {cfg['files']} — "
            "it is policing nothing and would pass silently"
        )


def test_compute_structure() -> None:
    # argv-list form: no shell, no dependency on the project's real counters.
    cfg = {
        "counters": {
            "alpha": {"command": [sys.executable, "-c", "print(7)"], "extract": r"(\d+)"},
            "beta": {"command": [sys.executable, "-c", "print('n=42')"], "extract": r"n=(\d+)"},
        }
    }
    stats = doc_stats.compute(cfg)
    assert stats == {"alpha": 7, "beta": 42}


def test_compute_skips_a_counter_whose_command_fails() -> None:
    # A command that exits non-zero must yield None, not a partial/wrong number:
    # a confident wrong count blocks or passes a commit for the wrong reason.
    cfg = {
        "counters": {
            "bad": {
                "command": [sys.executable, "-c", "print(5); raise SystemExit(3)"],
                "extract": r"(\d+)",
            }
        }
    }
    assert doc_stats.compute(cfg)["bad"] is None


def test_check_flags_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Point `check()` at a synthetic policed doc that claims a wrong op count.
    doc = tmp_path / "tiny.md"
    doc.write_text("The engine ships 41 op types today.\n", encoding="utf-8")
    monkeypatch.setattr(doc_stats, "REPO", tmp_path)
    cfg = {
        "claims": [{"counter": "ops", "pattern": r"(\d+)\s+op types", "label": "op types"}],
        "files": ["tiny.md"],
    }
    assert doc_stats.check(cfg, {"ops": 40}) == 1


def test_check_passes_when_matching(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Same doc, correct op count → no drift → exit 0.
    doc = tmp_path / "tiny.md"
    doc.write_text("The engine ships 40 op types today.\n", encoding="utf-8")
    monkeypatch.setattr(doc_stats, "REPO", tmp_path)
    cfg = {
        "claims": [{"counter": "ops", "pattern": r"(\d+)\s+op types", "label": "op types"}],
        "files": ["tiny.md"],
    }
    assert doc_stats.check(cfg, {"ops": 40}) == 0


def test_check_skips_a_counter_that_could_not_be_computed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An unavailable counter (None) must not be compared against the docs —
    # otherwise a failed command would report every quoted number as drift.
    doc = tmp_path / "tiny.md"
    doc.write_text("The engine ships 40 op types today.\n", encoding="utf-8")
    monkeypatch.setattr(doc_stats, "REPO", tmp_path)
    cfg = {
        "claims": [{"counter": "ops", "pattern": r"(\d+)\s+op types", "label": "op types"}],
        "files": ["tiny.md"],
    }
    assert doc_stats.check(cfg, {"ops": None}) == 0
