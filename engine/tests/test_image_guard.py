"""The screenshot-cost guard: `scripts/hooks.py read-guard` + `scripts/shrink_image.py`.

The hook is exercised the way Claude Code runs it — a subprocess fed the PreToolUse
JSON on stdin — so the deny/allow contract (stdout JSON, always exit 0) is what's tested.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
HOOKS = REPO / "scripts" / "hooks.py"
SHRINK = REPO / "scripts" / "shrink_image.py"


def _png(path: Path, width: int, height: int = 4, pad: int = 0) -> Path:
    """A valid PNG written by hand (no Pillow needed); `pad` bytes of an ancillary
    chunk inflate the file size without changing the pixels."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (len(data).to_bytes(4, "big") + kind + data
                + zlib.crc32(kind + data).to_bytes(4, "big"))
    ihdr = width.to_bytes(4, "big") + height.to_bytes(4, "big") + bytes([8, 0, 0, 0, 0])
    raw = b"".join(b"\x00" + b"\x00" * width for _ in range(height))
    body = chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw))
    if pad:
        body += chunk(b"tEXt", b"pad\x00" + b"x" * pad)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + body + chunk(b"IEND", b""))
    return path


def _guard(file_path: str, **env: str) -> subprocess.CompletedProcess:
    e = {k: v for k, v in os.environ.items() if k != "CLAUDE_ALLOW_BIG_IMAGES"}
    e.update({"CLAUDE_PROJECT_DIR": str(REPO), **env})
    payload = json.dumps({"tool_name": "Read", "tool_input": {"file_path": file_path}})
    return subprocess.run([sys.executable, str(HOOKS), "read-guard"], input=payload,
                          capture_output=True, text=True, env=e, timeout=30)


def _denied(r: subprocess.CompletedProcess) -> bool:
    assert r.returncode == 0  # a hook never wedges the session
    if not r.stdout.strip():
        return False
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse"
    return out["permissionDecision"] == "deny"


def test_denies_a_wide_image(tmp_path: Path) -> None:
    img = _png(tmp_path / "shot.png", 2600)
    r = _guard(str(img))
    assert _denied(r)
    reason = json.loads(r.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "scripts/shrink_image.py" in reason and str(img) in reason


def test_denies_a_heavy_image(tmp_path: Path) -> None:
    assert _denied(_guard(str(_png(tmp_path / "big.png", 800, pad=400 * 1024))))


def test_allows_a_small_image(tmp_path: Path) -> None:
    assert not _denied(_guard(str(_png(tmp_path / "ok.png", 1200))))


def test_allows_non_images(tmp_path: Path) -> None:
    big_text = tmp_path / "big.log"
    big_text.write_text("x" * (500 * 1024))
    assert not _denied(_guard(str(big_text)))


def test_fails_open(tmp_path: Path) -> None:
    assert not _denied(_guard(str(tmp_path / "missing.png")))
    bogus = tmp_path / "bogus.png"
    bogus.write_bytes(b"not a png")  # unknown width, small file
    assert not _denied(_guard(str(bogus)))
    r = subprocess.run([sys.executable, str(HOOKS), "read-guard"], input="{not json",
                       capture_output=True, text=True, timeout=30,
                       env={**os.environ, "CLAUDE_PROJECT_DIR": str(REPO)})
    assert r.returncode == 0 and not r.stdout.strip()


def test_override_env_allows(tmp_path: Path) -> None:
    img = _png(tmp_path / "shot.png", 2600)
    assert not _denied(_guard(str(img), CLAUDE_ALLOW_BIG_IMAGES="1"))


# ---- shrink_image.py ---------------------------------------------------------

def _load_shrink():
    spec = importlib.util.spec_from_file_location("shrink_image", SHRINK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_shrink_downscales_and_crops(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    Image = pytest.importorskip("PIL.Image")
    src = tmp_path / "shot.png"
    Image.new("RGB", (2600, 1100), (30, 60, 90)).save(src)
    mod = _load_shrink()

    assert mod.main([str(src)]) == 0
    out = tmp_path / "shot.small.png"
    assert str(out) in capsys.readouterr().out
    with Image.open(out) as im:
        assert im.size == (1200, 508)

    cropped = tmp_path / "crop.png"
    assert mod.main([str(src), "--crop", "100,100,800,400", "--out", str(cropped)]) == 0
    with Image.open(cropped) as im:
        assert im.size == (800, 400)  # already under max width: crop only
    # and the shrunk file passes the guard
    assert not _denied(_guard(str(out)))


def test_shrink_missing_input_is_a_clear_error(tmp_path: Path) -> None:
    pytest.importorskip("PIL")
    assert _load_shrink().main([str(tmp_path / "nope.png")]) == 2
