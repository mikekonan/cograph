#!/usr/bin/env python3
"""Generate the social-preview image for the documentation site.

Run manually when the tagline or palette changes; the PNG is committed:

    python3 scripts/make-og-image.py

Why a script and not an SVG: `og:image` is fetched by crawlers that do not
rasterise SVG (Slack, Twitter/X, LinkedIn, Facebook all require a bitmap), so
shipping an SVG there means shipping a link preview that silently does not
render. Why not a headless browser: this needs no toolchain beyond Pillow.

Colours are the application's design tokens from web/src/styles/globals.css.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "public" / "og-image.png"

W, H = 1200, 630

# --- app tokens (dark theme) ---
INK_900 = (10, 10, 12)  # --color-bg
INK_800 = (20, 21, 24)  # --color-bg-surface
INK_700 = (39, 39, 42)  # --color-border
INK_400 = (161, 161, 170)  # --color-fg-muted
VIOLET_500 = (124, 58, 237)  # --color-accent
VIOLET_300 = (167, 139, 250)  # --color-accent-hover (dark)
OFF_WHITE = (250, 250, 250)  # --color-fg


def font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Best available grotesque. Inter is not installed system-wide, and the
    @fontsource packages ship woff2 only, which Pillow cannot read — so fall
    back through the macOS system faces, which are close in feel."""
    candidates = [
        ("/System/Library/Fonts/SFNS.ttf", 0),
        ("/System/Library/Fonts/HelveticaNeue.ttc", 1 if bold else 0),
        ("/System/Library/Fonts/Helvetica.ttc", 1 if bold else 0),
        ("/Library/Fonts/Arial Unicode.ttf", 0),
    ]
    for path, index in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size, index=index)
            except OSError:
                continue
    return ImageFont.load_default()


img = Image.new("RGB", (W, H), INK_900)
draw = ImageDraw.Draw(img, "RGBA")

# Violet bloom in the lower right — the same treatment as the site hero, which
# blurs an accent gradient behind the headline. Painted as concentric circles
# with falling alpha because Pillow has no gradient primitive.
cx, cy, radius = int(W * 0.86), int(H * 0.88), 460
for i in range(radius, 0, -6):
    t = i / radius
    alpha = int(46 * (1 - t) ** 2.2)
    if alpha <= 0:
        continue
    mix = 1 - t
    colour = tuple(
        int(VIOLET_500[c] + (VIOLET_300[c] - VIOLET_500[c]) * mix) for c in range(3)
    )
    draw.ellipse((cx - i, cy - i, cx + i, cy + i), fill=(*colour, alpha))

# Faint graph motif, top-right: three connected nodes, the product's mark.
nodes = [(980, 130), (1090, 210), (975, 268)]
for a in range(len(nodes)):
    for b in range(a + 1, len(nodes)):
        draw.line([nodes[a], nodes[b]], fill=(*INK_700, 210), width=2)
for i, (nx, ny) in enumerate(nodes):
    r = 13 if i == 0 else 9
    fill = VIOLET_500 if i == 0 else INK_800
    draw.ellipse((nx - r, ny - r, nx + r, ny + r), fill=fill, outline=VIOLET_300, width=2)

PAD = 84

# Accent rule above the wordmark.
draw.rounded_rectangle((PAD, 148, PAD + 56, 154), radius=3, fill=VIOLET_500)

draw.text((PAD, 186), "Cograph", font=font(112, bold=True), fill=OFF_WHITE)

tagline = [
    "Turn a Git repository into a searchable,",
    "source-grounded knowledge base",
]
y = 334
for line in tagline:
    draw.text((PAD, y), line, font=font(40), fill=INK_400)
    y += 54

draw.text((PAD, 468), "for humans and coding agents", font=font(40), fill=VIOLET_300)

# Footer: the domain, and what it is.
draw.text((PAD, H - 92), "cograph.cc", font=font(28, bold=True), fill=OFF_WHITE)
draw.text(
    (PAD + 152, H - 92),
    "·  self-hosted  ·  wiki + hybrid retrieval + code graph + MCP",
    font=font(28),
    fill=(*INK_400, 235),
)

OUT.parent.mkdir(parents=True, exist_ok=True)
img.save(OUT, "PNG", optimize=True)
print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB, {W}x{H})")
assert math.isclose(W / H, 1200 / 630), "og:image should stay at the 1.91:1 ratio"
