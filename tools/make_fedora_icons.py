"""Cut Franklin's fedora out of the master artwork and build the toolbar icons from it.

    uv run --with pillow --with numpy --with scipy python tools/make_fedora_icons.py

Writes assets/fedora.png (the hat at full size) and extension/icons/fedora-{16,32,64}.png
(dark outline, for light toolbars) and fedora-light-{16,32,64}.png (a soft cream edge, for dark toolbars).
"""

from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

REPO = Path(__file__).resolve().parents[1]
ICONS = REPO / "extension" / "icons"
# Degrees to straighten the hat. 0 keeps Franklin's angle, which also fills a square icon best.
TILT = 0
CREAM = (244, 230, 210)


def cut_fedora() -> Image.Image:
    rgba = np.asarray(Image.open(REPO / "assets" / "red-panda-master.png").convert("RGBA")).astype(np.float32)
    rgb, alpha = rgba[..., :3] / 255.0, rgba[..., 3]
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    mx, mn = rgb.max(-1), rgb.min(-1)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    height, width = alpha.shape
    rows = np.arange(height)[:, None].repeat(width, 1)

    # The hat's felt is the only mid-brown, mid-saturation area in the top half (the coat is lower down).
    brown = (alpha > 128) & (r >= g) & (g >= b * 0.9) & (sat > 0.2) & (sat < 0.72) & (mx > 0.22) & (mx < 0.72)
    brown &= rows < int(height * 0.56)
    labels, count = ndimage.label(brown)
    sizes = ndimage.sum(brown, labels, range(1, count + 1))
    parts = [i + 1 for i, s in enumerate(sizes) if s > sizes.max() * 0.08]
    parts.sort(key=lambda k: ndimage.center_of_mass(labels == k)[0])
    crown, brim = labels == parts[0], labels == parts[1]
    hat = crown | brim

    # The dark band separates crown and brim; take the dark and brown pixels between them, column by column.
    band = (alpha > 128) & ((mx < 0.35) | brown)
    for x in range(width):
        above, below = np.nonzero(crown[:, x])[0], np.nonzero(brim[:, x])[0]
        if len(above) and len(below) and below.min() > above.max():
            span = slice(above.max(), below.min() + 1)
            hat[span, x] |= band[span, x]

    hat = ndimage.binary_fill_holes(ndimage.binary_closing(hat, structure=np.ones((3, 3)), iterations=12))
    fur = (alpha > 128) & (((sat > 0.72) & (r > 0.55)) | ((mx > 0.8) & (sat < 0.35)))
    body = hat & ~fur
    outline = ndimage.binary_dilation(body, iterations=14) & (alpha > 128) & (mx < 0.3)
    mask = ndimage.binary_opening(ndimage.binary_fill_holes(body | outline), iterations=2)
    labels, count = ndimage.label(mask)
    mask = labels == (int(np.argmax(ndimage.sum(mask, labels, range(1, count + 1)))) + 1)

    out = rgba.copy()
    out[..., 3] = np.minimum(alpha, ndimage.gaussian_filter(mask.astype(np.float32), 1.0) * 255)
    ys, xs = np.nonzero(mask)
    hat_img = Image.fromarray(out.astype(np.uint8), "RGBA").crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))
    level = hat_img.rotate(TILT, resample=Image.BICUBIC, expand=True)
    return level.crop(level.getbbox())


def icon(level: Image.Image, size: int, edge_alpha: int = 0) -> Image.Image:
    """The hat as wide as the icon allows, vertically centred, with an optional cream edge."""
    margin = 1 if edge_alpha else 0
    width = size - 2 * margin
    hat = level.resize((width, round(level.height * width / level.width)), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size))
    canvas.alpha_composite(hat, (margin, (size - hat.height) // 2))
    if edge_alpha:
        grown = ndimage.binary_dilation(np.asarray(canvas)[..., 3] > 40, iterations=max(1, size // 32))
        edge = Image.new("RGBA", (size, size), CREAM + (0,))
        edge.putalpha(Image.fromarray((grown * edge_alpha).astype(np.uint8)))
        edge.alpha_composite(canvas)
        canvas = edge
    return canvas


def main() -> None:
    level = cut_fedora()
    level.save(REPO / "assets" / "fedora.png", optimize=True)
    for size in (16, 32, 64):
        icon(level, size).save(ICONS / f"fedora-{size}.png", optimize=True)
        icon(level, size, 100 if size == 16 else 165).save(ICONS / f"fedora-light-{size}.png", optimize=True)
    print(f"fedora {level.size}; icons in {ICONS}")


if __name__ == "__main__":
    main()
