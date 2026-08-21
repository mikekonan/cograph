#!/usr/bin/env bash
# Regenerates the raster icons in docs/public/ from the two committed SVGs:
#
#   docs/public/favicon.svg   -> favicon.ico (16, 32 and 48 px, PNG-in-ICO)
#   scripts/favicon-touch.svg -> apple-touch-icon.png (180 px, dark ground)
#
# The ICO is the legacy path and not optional: browsers request /favicon.ico by
# name whenever the declared SVG is unusable, so until it existed that request
# was a 404 on every page load.
#
# macOS only, deliberately. Neither ImageMagick nor librsvg is installed here,
# and adding one to build two files that change only when the brand mark does is
# not worth the dependency; qlmanage and sips ship with the OS. Pillow writes the
# ICO container, which sips cannot do.
#
# Run this after editing either SVG and commit the outputs — they are checked in,
# because the docs build runs on Linux runners that have none of the above.
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
out="$root/docs/public"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

# qlmanage's -s is the size of the canvas, not of the drawing: it rasterises an
# SVG at the intrinsic size its width/height attributes declare and pins that in
# the top-left corner of an otherwise empty -s by -s image. Both marks declare
# 32x32, so rendering them straight gave a 32 px mark in the corner of a 1024 px
# canvas, and every icon downsampled from it was a speck. Substituting the
# declared size is what makes the drawing fill the frame. Do not drop the guard:
# a silent no-op substitution reintroduces exactly that bug, and a 16 px favicon
# is too small to notice it by eye.
render() {
  local src="$1" name="$2"
  sed 's/width="32" height="32"/width="1024" height="1024"/' "$src" > "$tmp/$name.svg"
  grep -q 'width="1024"' "$tmp/$name.svg" || {
    echo "gen-favicons: $src no longer declares width=\"32\" height=\"32\"." >&2
    echo "gen-favicons: fix the substitution above rather than removing it." >&2
    exit 1
  }
  qlmanage -t -s 1024 -o "$tmp" "$tmp/$name.svg" >/dev/null
}

render "$out/favicon.svg" mark
render "$root/scripts/favicon-touch.svg" touch

# Downsample from the 1024 px render rather than asking qlmanage for each size:
# its own small renders are visibly rougher than sips reducing a large one.
sips -z 180 180 "$tmp/touch.svg.png" --out "$out/apple-touch-icon.png" >/dev/null

python3 - "$tmp/mark.svg.png" "$out/favicon.ico" <<'PY'
import sys

from PIL import Image

src, dst = sys.argv[1], sys.argv[2]
Image.open(src).convert("RGBA").save(dst, sizes=[(16, 16), (32, 32), (48, 48)])
PY

printf 'wrote %s\n' "$out/favicon.ico" "$out/apple-touch-icon.png"
