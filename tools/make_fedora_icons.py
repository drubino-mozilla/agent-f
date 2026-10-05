"""Draw Franklin's fedora, front on, and build the toolbar icons from it.

    uv run --with resvg-py python tools/make_fedora_icons.py

The colours and outline follow the mascot artwork. Writes assets/fedora.svg and assets/fedora.png (512 px),
and extension/icons/fedora-{16,32,64}.png (for light toolbars) and fedora-light-{16,32,64}.png (with a
soft cream edge, for dark toolbars). The hat spans the icon's width and sits in the lower part of its
height, leaving room above for the hop in background/toolbar.js. The landing page's favicon
(docs/favicon.svg, and docs/favicon.png at 64 px) is the same hat, centred.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ICONS = REPO / "extension" / "icons"
DOCS = REPO / "docs"

OUTLINE = "#1e0a08"
FELT_LIGHT = "#9a5c37"
FELT = "#7a4629"
FELT_SHADOW = "#5e3220"
BAND = "#241a1c"
BAND_LIGHT = "#3a2c2c"
CREAM = "#f4e6d2"

# Drawn on a 64-unit square; the hat spans x 4-60 and y 13-55.
CROWN = ("M16.6 46 C16 37 17 29.5 19.4 23.6 C21.6 18.4 26.6 16.2 32 19.6 "
         "C37.4 16.2 42.4 18.4 44.6 23.6 C47 29.5 48 37 47.4 46 Z")
CROWN_LIGHT = "M19.6 44 C19.2 36 20 29.8 22 25 C23.6 21.2 26.8 19.6 30 20.8 C28 27 27.4 35 27.8 44 Z"
CROWN_SHADOW = "M44.4 44 C44.8 36 44 29.8 42 25 C40.4 21.2 37.2 19.6 34 20.8 C36 27 36.6 35 36.2 44 Z"
CREASE = "M32 19.6 C31.2 23 31.2 26.6 32 30"
BAND_SHAPE = "M17.2 35.5 C26 37.8 38 37.8 46.8 35.5 L47 43 C38 45.4 26 45.4 17 43 Z"
BAND_SHINE = "M19.5 37.4 C25 38.8 30 39.2 34 39.2"
BRIM = ("M4.5 41.5 C8 39.6 12.5 41.2 17 43.6 C26 46.4 38 46.4 47 43.6 C51.5 41.2 56 39.6 59.5 41.5 "
        "C57.5 49.5 46 54.5 32 54.5 C18 54.5 6.5 49.5 4.5 41.5 Z")
BRIM_LIGHT = "M8 43.2 C12 42.6 15.5 44.4 18.5 46.2 C26 48.6 38 48.6 45.5 46.2 C40 50.2 24 50.6 13.5 47.8 C10.8 46.6 9 45 8 43.2 Z"


def svg(edge_width: float = 0, centred: bool = False) -> str:
    """The hat; edge_width > 0 adds the cream edge used on dark toolbars."""
    stroke = f'stroke="{OUTLINE}" stroke-width="3.2" stroke-linejoin="round" stroke-linecap="round"'
    under = ""
    if edge_width:
        cream = f'fill="{CREAM}" stroke="{CREAM}" stroke-width="{edge_width}" stroke-linejoin="round" opacity="0.62"'
        under = f'<g {cream}><path d="{CROWN}"/><path d="{BRIM}"/></g>'
    # Stretched to reach the icon's edges, and resting low so it has room to hop (unless centred).
    shift = -2 if centred else 3.5
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">
  <g transform="matrix(1.07 0 0 1 -2.24 {shift})">
  {under}
  <path d="{CROWN}" fill="{FELT}" {stroke}/>
  <path d="{CROWN_LIGHT}" fill="{FELT_LIGHT}"/>
  <path d="{CROWN_SHADOW}" fill="{FELT_SHADOW}"/>
  <path d="{CROWN}" fill="none" {stroke}/>
  <path d="{CREASE}" fill="none" stroke="{OUTLINE}" stroke-width="2" stroke-linecap="round"/>
  <path d="{BAND_SHAPE}" fill="{BAND}" {stroke}/>
  <path d="{BAND_SHINE}" fill="none" stroke="{BAND_LIGHT}" stroke-width="1.6" stroke-linecap="round"/>
  <path d="{BRIM}" fill="{FELT}" {stroke}/>
  <path d="{BRIM_LIGHT}" fill="{FELT_LIGHT}"/>
  <path d="{BRIM}" fill="none" {stroke}/>
  </g>
</svg>
"""


def render(source: str, size: int, path: Path) -> None:
    import resvg_py

    path.write_bytes(bytes(resvg_py.svg_to_bytes(svg_string=source, width=size, height=size)))


def main() -> None:
    plain = svg()
    (REPO / "assets" / "fedora.svg").write_text(plain, encoding="utf-8")
    render(plain, 512, REPO / "assets" / "fedora.png")
    for size in (16, 32, 64):
        render(plain, size, ICONS / f"fedora-{size}.png")
        render(svg(edge_width=5 if size == 16 else 7), size, ICONS / f"fedora-light-{size}.png")
    favicon = svg(centred=True)
    (DOCS / "favicon.svg").write_text(favicon, encoding="utf-8")
    render(favicon, 64, DOCS / "favicon.png")
    print(f"icons in {ICONS}, favicon in {DOCS}")


if __name__ == "__main__":
    main()
