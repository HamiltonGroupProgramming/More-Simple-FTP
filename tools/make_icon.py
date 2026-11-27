"""Draw the crayon-style app icon: assets/icon.png (512 px) and assets/icon.ico.

Run with:  python tools/make_icon.py   (needs Pillow). The output is committed, so the
build doesn't need this script; rerun it only to change the icon.
"""

from __future__ import annotations

import math
import os
import random

from PIL import Image, ImageChops, ImageDraw, ImageFilter

S = 1024                      # draw big, shrink at the end
INK = (42, 42, 42)
PAPER = (253, 248, 236)
GREEN = (140, 233, 154)       # "Send it" button colour
BLUE = (165, 216, 255)        # "Get it" button colour
PINK = (240, 101, 149)        # title squiggle colour
rng = random.Random(7)


def wobble(points, amount):
    return [(x + rng.uniform(-amount, amount), y + rng.uniform(-amount, amount)) for x, y in points]


def densify(points, step=18, closed=True):
    pts = list(points) + ([points[0]] if closed else [])
    out = []
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        n = max(1, int(math.hypot(x2 - x1, y2 - y1) / step))
        out += [(x1 + (x2 - x1) * i / n, y1 + (y2 - y1) * i / n) for i in range(n)]
    out.append(pts[-1])
    return out


def crayon_line(img, points, colour, width, closed=True, passes=3):
    """A waxy stroke: a few jittered passes, then a grainy mask so paper shows through."""
    layer = Image.new("L", img.size, 0)
    d = ImageDraw.Draw(layer)
    base = densify(points, closed=closed)
    for p in range(passes):
        pts = wobble(base, width * 0.18)
        if closed and p:   # overshoot the start a touch, like a hand going round twice
            pts = pts + pts[1:4]
        d.line(pts, fill=255, width=int(width * rng.uniform(0.8, 1.05)), joint="curve")
        for x, y in (pts[0], pts[-1]):
            r = width * 0.45
            d.ellipse((x - r, y - r, x + r, y + r), fill=255)
    grain = Image.effect_noise(img.size, 90).point(lambda v: 255 if v > 70 else 120)
    layer = ImageChops.multiply(layer, grain).filter(ImageFilter.GaussianBlur(1.2))
    img.paste(Image.new("RGBA", img.size, colour + (255,)), (0, 0), layer)


def crayon_fill(img, polygon, colour):
    """Scribbled fill: diagonal strokes clipped to the shape, slightly over the edges."""
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).polygon(wobble(polygon, 6), fill=255)
    hatch = Image.new("L", img.size, 0)
    d = ImageDraw.Draw(hatch)
    for k in range(-S, 2 * S, 22):
        d.line([(k, 0), (k + S * 0.6, S)], fill=rng.randint(200, 255), width=30)
    grain = Image.effect_noise(img.size, 60).point(lambda v: 255 if v > 60 else 170)
    fill = ImageChops.multiply(ImageChops.multiply(mask, hatch), grain)
    img.paste(Image.new("RGBA", img.size, colour + (255,)), (0, 0), fill)


def arrow(cx, top, bottom, up=True, w=150, head=250, head_h=190):
    """An arrow shape pointing up (or down) between top and bottom."""
    if up:
        return [(cx, top), (cx + head / 2 * 1.0, top + head_h), (cx + w / 2, top + head_h),
                (cx + w / 2, bottom), (cx - w / 2, bottom), (cx - w / 2, top + head_h),
                (cx - head / 2, top + head_h)]
    return [(x, top + bottom - y) for x, y in arrow(cx, top, bottom, True, w, head, head_h)]


def main() -> None:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # paper card with rounded corners
    card = Image.new("L", (S, S), 0)
    ImageDraw.Draw(card).rounded_rectangle((62, 62, S - 62, S - 62), radius=170, fill=255)
    img.paste(Image.new("RGBA", (S, S), PAPER + (255,)), (0, 0), card)

    # hand-drawn border round the card
    r, m = 170, 60
    corner = []
    for cx, cy, a0 in ((S - m - r, m + r, -90), (S - m - r, S - m - r, 0),
                       (m + r, S - m - r, 90), (m + r, m + r, 180)):
        corner += [(cx + r * math.cos(math.radians(a0 + t)), cy + r * math.sin(math.radians(a0 + t)))
                   for t in range(0, 91, 10)]
    crayon_line(img, corner, INK, 34)

    # green "send" arrow up, blue "get" arrow down
    up = arrow(372, 190, 740, up=True)
    down = arrow(652, 190, 740, up=False)
    crayon_fill(img, up, GREEN)
    crayon_fill(img, down, BLUE)
    crayon_line(img, up, INK, 30)
    crayon_line(img, down, INK, 30)

    # pink squiggle underneath, like the title underline
    sq = [(220 + 584 * i / 10, 845 + 28 * math.sin(i * 1.7) + rng.uniform(-8, 8)) for i in range(11)]
    crayon_line(img, sq, PINK, 40, closed=False, passes=2)

    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "assets")
    big = img.resize((512, 512), Image.LANCZOS)
    big.save(os.path.join(root, "icon.png"))
    img.save(os.path.join(root, "icon.ico"),
             sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("wrote assets/icon.png and assets/icon.ico")


if __name__ == "__main__":
    main()
