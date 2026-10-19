#!/usr/bin/env python3
"""Downscale (and optionally crop) an image before an agent reads it.

A full-resolution Console screenshot (2600x1100, 1-1.4 MB) costs far more context
than it tells a reviewer; a 1200 px-wide copy shows the same thing. The
`read-guard` hook (scripts/hooks.py) refuses `Read` on big images and points here.

    .venv/bin/python scripts/shrink_image.py shot.png                 # -> shot.small.png
    .venv/bin/python scripts/shrink_image.py shot.png --max-width 800 --out /tmp/s.png
    .venv/bin/python scripts/shrink_image.py shot.png --crop 0,0,1300,600

Prints the output path, its pixel size and its byte size. Needs Pillow (the `dev`
extra does not require it; install it in the venv to use this helper).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _parse_crop(text: str) -> tuple[int, int, int, int]:
    try:
        x, y, w, h = (int(v) for v in text.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError("--crop wants x,y,w,h (integers)")
    if w <= 0 or h <= 0 or x < 0 or y < 0:
        raise argparse.ArgumentTypeError("--crop wants x,y >= 0 and w,h > 0")
    return x, y, w, h


def shrink(src: Path, out: Path | None = None, max_width: int = 1200,
           crop: tuple[int, int, int, int] | None = None) -> Path:
    from PIL import Image  # imported lazily so --help works without Pillow

    out = out or src.with_name(f"{src.stem}.small{src.suffix or '.png'}")
    with Image.open(src) as im:
        if crop:
            x, y, w, h = crop
            im = im.crop((x, y, min(x + w, im.width), min(y + h, im.height)))
        if im.width > max_width:
            im = im.resize((max_width, max(1, round(im.height * max_width / im.width))),
                           Image.LANCZOS)
        if out.suffix.lower() in (".jpg", ".jpeg") and im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        kw = {"optimize": True} if out.suffix.lower() == ".png" else {}
        im.save(out, **kw)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("src", type=Path)
    ap.add_argument("--max-width", type=int, default=1200)
    ap.add_argument("--out", type=Path, default=None,
                    help="output path (default: <name>.small.<ext> next to the input)")
    ap.add_argument("--crop", type=_parse_crop, default=None, metavar="x,y,w,h",
                    help="crop to this box first (in source pixels)")
    a = ap.parse_args(argv)
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("shrink_image: Pillow is not installed — run this with the project venv "
              "(`.venv/bin/python scripts/shrink_image.py …`) or `pip install pillow` there.",
              file=sys.stderr)
        return 2
    if not a.src.is_file():
        print(f"shrink_image: no such file: {a.src}", file=sys.stderr)
        return 2
    if a.max_width < 1:
        print("shrink_image: --max-width must be >= 1", file=sys.stderr)
        return 2
    try:
        out = shrink(a.src, a.out, a.max_width, a.crop)
    except Exception as exc:  # not an image, unwritable --out, ...
        print(f"shrink_image: {a.src}: {exc}", file=sys.stderr)
        return 2
    from PIL import Image
    with Image.open(out) as im:
        print(f"{out}  {im.width}x{im.height}  {out.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
