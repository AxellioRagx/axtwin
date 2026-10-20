"""Upstream cloud-image fetch + checksum + local cache.

docs/IMAGE_BUILD.md §3 step 2: "Pull the upstream cloud image named in the
binding's build recipe; verify it against the recipe's checksum source; cache
it locally on the orchestrator (`~/.axrange/cache/`). `--refresh-cache`
invalidates."

The cache is keyed by the recipe's `base_image.filename`. A cache hit requires
both the file to exist AND its checksum to match the recipe — a truncated or
tampered cache entry never satisfies a build.

Transport is `urllib` only (no `requests`), matching the adapter. The network
opener is injectable so tests exercise the verify/cache logic without real
downloads.
"""
from __future__ import annotations

import hashlib
import shutil
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Callable, Protocol

from axrange.imagebuild.errors import ImageBuildError
from axrange.imagebuild.recipe import BaseImage

# An opener returns a binary, readable, context-manageable stream for a URL —
# the urllib.request.urlopen surface we depend on.
Opener = Callable[[str], Any]

_CHUNK = 1 << 20  # 1 MiB streaming chunks


class _Logger(Protocol):
    def __call__(self, msg: str) -> None: ...


def _noop(_msg: str) -> None:  # pragma: no cover - trivial
    pass


def _hash_file(path: Path, algo: str) -> str:
    h = hashlib.new(algo)
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest().lower()


def _download(url: str, dest: Path, opener: Opener) -> None:
    with opener(url) as resp:
        with dest.open("wb") as out:
            shutil.copyfileobj(resp, out, length=_CHUNK)


def _expected_checksum(base: BaseImage, opener: Opener) -> str:
    """Resolve the expected hex digest: inline `checksum` wins, else fetch and
    parse the SHA256SUMS/SHA512SUMS-style file at `checksum_url`."""
    if base.checksum:
        return base.checksum.lower()
    if not base.checksum_url:
        raise ImageBuildError(
            "no checksum or checksum_url to verify against", stage="fetch"
        )
    try:
        with opener(base.checksum_url) as resp:
            sums_text = resp.read().decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        raise ImageBuildError(
            f"failed to fetch checksum file {base.checksum_url}: {e}",
            stage="fetch",
        ) from e

    # SHA*SUMS lines are "<hex>  <filename>" (two spaces, or " *" for binary).
    for line in sums_text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        digest, name = parts[0], parts[-1]
        name = name.lstrip("*")
        if Path(name).name == base.filename:
            return digest.lower()
    raise ImageBuildError(
        f"checksum file {base.checksum_url} has no entry for {base.filename!r}",
        stage="fetch",
    )


def fetch_base_image(
    base: BaseImage,
    cache_dir: str | Path,
    *,
    refresh: bool = False,
    opener: Opener = urllib.request.urlopen,
    log: _Logger = _noop,
) -> Path:
    """Return a path to the verified, cached base image, downloading if needed.

    On a cache hit (file present + checksum matches + not `refresh`), no network
    I/O happens beyond resolving the expected checksum (which itself is skipped
    when the recipe pins an inline `checksum`).
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / base.filename

    expected = _expected_checksum(base, opener)

    if target.is_file() and not refresh:
        actual = _hash_file(target, base.checksum_algo)
        if actual == expected:
            log(f"cache hit: {target} ({base.checksum_algo} ok)")
            return target
        log(
            f"cache stale: {target} {base.checksum_algo} mismatch "
            f"(have {actual[:12]}…, want {expected[:12]}…); re-downloading"
        )

    log(f"downloading {base.url} -> {target}")
    fd, tmp_name = tempfile.mkstemp(dir=cache_dir, suffix=".part")
    tmp = Path(tmp_name)
    import os

    os.close(fd)
    try:
        _download(base.url, tmp, opener)
        actual = _hash_file(tmp, base.checksum_algo)
        if actual != expected:
            raise ImageBuildError(
                f"checksum mismatch for {base.url}: got {actual}, "
                f"expected {expected} ({base.checksum_algo})",
                stage="fetch",
            )
        tmp.replace(target)
    except ImageBuildError:
        tmp.unlink(missing_ok=True)
        raise
    except Exception as e:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        raise ImageBuildError(
            f"failed to download {base.url}: {e}", stage="fetch"
        ) from e

    log(f"downloaded + verified {target} ({base.checksum_algo} ok)")
    return target
