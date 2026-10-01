#!/usr/bin/env python3
"""Assemble docs/media/tour.gif from the frames the media spec captured.

    cd frontend && E2E_MEDIA=1 npx playwright test media.spec.ts --project=desktop
    uv run --no-project --with pillow python scripts/make_demo_gif.py

The spec writes frames and their timings to $MEDIA_FRAMES, or the system temp directory's
secondmind-media-frames (both sides use the same default). This script keeps
the file under the repository's size limit (it refuses files over 512 KB): one palette for every
frame, so unchanged areas compress away; identical neighbours merged into one longer frame; and
the largest width and palette that still fit, tried in order.
"""

import io
import json
import os
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
FRAMES = Path(os.environ.get("MEDIA_FRAMES") or Path(tempfile.gettempdir()) / "secondmind-media-frames")
OUT = ROOT / "docs" / "media" / "tour.gif"
LADDER = [(880, 128), (800, 96), (800, 64), (760, 48), (720, 48), (640, 32)]  # (width, colours)
MIN_MS, MAX_MS = 80, 250  # frames taken as the screen changed are shown for how long it took
MAX_BYTES = 500 * 1024  # .pre-commit-config.yaml: check-added-large-files --maxkb=512


def durations(frames: list[dict[str, int | str | None]]) -> list[int]:
    out: list[int] = []
    for i, f in enumerate(frames):
        hold = f["hold"]
        if isinstance(hold, int):
            out.append(hold)
        elif i + 1 < len(frames):
            gap = int(frames[i + 1]["at"] or 0) - int(f["at"] or 0)
            out.append(max(MIN_MS, min(MAX_MS, gap)))
        else:
            out.append(MAX_MS)
    return out


def build(sources: list[Image.Image], times: list[int], width: int, colours: int) -> bytes:
    images = [im.resize((width, round(im.height * width / im.width)), Image.Resampling.LANCZOS) for im in sources]

    # One palette for the whole tour, built from a sample of its frames.
    sample = images[:: max(1, len(images) // 8)]
    sheet = Image.new("RGB", (width, sum(s.height for s in sample)))
    top = 0
    for s in sample:
        sheet.paste(s, (0, top))
        top += s.height
    palette = sheet.quantize(colors=colours, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)

    kept: list[Image.Image] = []
    shown: list[int] = []
    for im, ms in zip(images, times, strict=True):
        q = im.quantize(palette=palette, dither=Image.Dither.NONE)
        if kept and ImageChops.difference(kept[-1].convert("RGB"), q.convert("RGB")).getbbox() is None:
            shown[-1] += ms  # nothing moved: keep the picture, show it longer
        else:
            kept.append(q)
            shown.append(ms)

    buffer = io.BytesIO()
    kept[0].save(buffer, format="GIF", save_all=True, append_images=kept[1:], duration=shown, loop=0, optimize=True, disposal=1)
    print(f"  {width}px, {colours} colours: {len(kept)} frames, {sum(shown) / 1000:.1f} s, {buffer.tell() / 1024:.0f} KB")
    return buffer.getvalue()


def main() -> int:
    index = FRAMES / "frames.json"
    if not index.exists():
        print(f"no frames at {index}: run the media spec first", file=sys.stderr)
        return 1
    frames = json.loads(index.read_text())
    times = durations(frames)
    sources = [Image.open(FRAMES / f["file"]).convert("RGB") for f in frames]
    for width, colours in LADDER:
        data = build(sources, times, width, colours)
        if len(data) <= MAX_BYTES:
            OUT.parent.mkdir(parents=True, exist_ok=True)
            OUT.write_bytes(data)
            print(f"wrote {OUT.relative_to(ROOT)} ({len(data) / 1024:.0f} KB)")
            return 0
    print(f"no setting fits {MAX_BYTES // 1024} KB: thin the frames in the spec", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
