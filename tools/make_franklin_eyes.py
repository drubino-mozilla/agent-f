"""Build the panel's shifty-eyed Franklin from the master artwork.

    uv run --with pillow python tools/make_franklin_eyes.py

Writes extension/ui/franklin-blank-eyes.png (Franklin with empty eye whites, 256 px) and
extension/ui/franklin-eyes.svg (irises clipped to each eye opening, glancing left and right).
The panel shows the untouched franklin-256.png at rest and swaps to these two layers while an agent works.
Coordinates are in master-artwork pixels (1254 x 1254); the irises start where the artwork draws them.
"""

from pathlib import Path

from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parents[1]
UI = REPO / "extension" / "ui"
MASTER = REPO / "assets" / "red-panda-master.png"
SIZE = 1254
WHITE = "#fdf8f2"

EYES = {
    "left": {
        "opening": [(424, 560), (470, 572), (518, 586), (607, 611), (606, 622), (600, 637), (587, 653),
                    (553, 669), (520, 670), (487, 660), (462, 644), (445, 628), (432, 610), (425, 590)],
        "iris": (470, 594, 46),
        "highlight": (451, 581, 11, 8),
    },
    "right": {
        "opening": [(780, 629), (815, 632), (850, 634), (927, 630), (926, 650), (922, 670), (907, 692),
                    (873, 706), (840, 706), (812, 694), (795, 684), (785, 668), (780, 645)],
        "iris": (813, 656, 37),
        "highlight": (793, 644, 9, 7),
    },
}
# How far the irises travel to the right, in master pixels.
GLANCE = 60


def blank_eyes() -> None:
    art = Image.open(MASTER).convert("RGBA")
    draw = ImageDraw.Draw(art)
    for eye in EYES.values():
        draw.polygon(eye["opening"], fill=WHITE)
    art.resize((256, 256), Image.LANCZOS).save(UI / "franklin-blank-eyes.png", optimize=True)


def points(poly) -> str:
    return " ".join(f"{x},{y}" for x, y in poly)


def eye_svg(name: str, eye: dict) -> str:
    cx, cy, r = eye["iris"]
    hx, hy, hrx, hry = eye["highlight"]
    # The opening is listed clockwise from its top-left corner, so its top edge runs up to the rightmost point.
    opening = eye["opening"]
    rightmost = opening.index(max(opening, key=lambda p: p[0]))
    top = opening[: rightmost + 1]
    shadow = top + [(x, y + 12) for x, y in reversed(top)]
    return f"""  <g clip-path="url(#{name})">
    <g class="look">
      <circle cx="{cx}" cy="{cy}" r="{r}" fill="#1a0605"/>
      <circle cx="{cx}" cy="{cy + 2}" r="{round(r * 0.85)}" fill="#5e1a13"/>
      <circle cx="{cx + round(r * 0.13)}" cy="{cy - round(r * 0.17)}" r="{round(r * 0.71)}" fill="#1a0605"/>
      <ellipse cx="{hx}" cy="{hy}" rx="{hrx}" ry="{hry}" transform="rotate(-25 {hx} {hy})" fill="#fffaf5"/>
    </g>
    <polygon points="{points(shadow)}" fill="rgb(90, 60, 50)" fill-opacity="0.16"/>
  </g>"""


def eyes_svg() -> str:
    clips = "\n".join(f'    <clipPath id="{n}"><polygon points="{points(e["opening"])}"/></clipPath>'
                      for n, e in EYES.items())
    groups = "\n".join(eye_svg(n, e) for n, e in EYES.items())
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SIZE} {SIZE}" width="{SIZE}" height="{SIZE}">
  <style>
    .look {{ animation: shifty 3.4s ease-in-out infinite; }}
    @keyframes shifty {{
      0%, 16% {{ transform: translateX(0); }}
      26%, 50% {{ transform: translateX({GLANCE}px); }}
      60%, 68% {{ transform: translateX(0); }}
      74%, 84% {{ transform: translateX({GLANCE // 2}px); }}
      92%, 100% {{ transform: translateX(0); }}
    }}
    @media (prefers-reduced-motion: reduce) {{ .look {{ animation: none; }} }}
  </style>
  <defs>
{clips}
  </defs>
{groups}
</svg>
"""


def main() -> None:
    blank_eyes()
    (UI / "franklin-eyes.svg").write_text(eyes_svg(), encoding="utf-8")
    print(f"wrote {UI / 'franklin-blank-eyes.png'} and {UI / 'franklin-eyes.svg'}")


if __name__ == "__main__":
    main()
