"""Generate the placeholder PWA icons for Ticket 040.

A honeycomb hexagon (honey on paper, ink outline) in the dashboard palette — a
stand-in until real branding lands. This is the *reproducible source*: the
committed PNG/ICO outputs ship as static assets, so Hive needs no runtime image
dependency (Pillow is build-time only). Re-run to regenerate:

    python -m venv /tmp/iconvenv && /tmp/iconvenv/bin/pip install pillow
    /tmp/iconvenv/bin/python scripts/gen_pwa_icons.py

Palette comes from src/hive/web/static/landing.css (:7-23).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

PAPER = (250, 247, 237)  # --paper #faf7ed
INK = (31, 24, 18)  # --ink   #1f1812
HONEY = (224, 167, 38)  # --honey #e0a726

OUT_DIR = Path(__file__).resolve().parent.parent / "src" / "hive" / "web" / "static" / "icons"
SUPERSAMPLE = 4  # render large, downscale with LANCZOS for clean antialiased edges


def _hexagon(size: int, mark_frac: float) -> Image.Image:
    """An opaque paper square with a centered honey hexagon, ink-outlined."""
    big = size * SUPERSAMPLE
    img = Image.new("RGB", (big, big), PAPER)
    draw = ImageDraw.Draw(img)
    radius = big * mark_frac / 2
    draw.regular_polygon(
        (big / 2, big / 2, radius),
        n_sides=6,
        rotation=0,
        fill=HONEY,
        outline=INK,
        width=max(2, int(big * 0.035)),
    )
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # Regular icons + apple-touch (opaque, no alpha — iOS dislikes transparency).
    _hexagon(192, 0.62).save(OUT_DIR / "icon-192.png")
    _hexagon(512, 0.62).save(OUT_DIR / "icon-512.png")
    _hexagon(180, 0.62).save(OUT_DIR / "apple-touch-icon-180.png")
    # Maskable: smaller mark so it survives the platform's circular/rounded crop.
    _hexagon(512, 0.50).save(OUT_DIR / "icon-512-maskable.png")
    # Favicon: multi-size .ico from a single render.
    _hexagon(64, 0.66).save(OUT_DIR / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    print(f"wrote icons to {OUT_DIR}")


if __name__ == "__main__":
    main()
