"""Thumbnail drafts: the long recording on the left, the finished Shorts on the right.

The frames are picked automatically from the strongest clips and the rendered
Shorts. A frame counts as good when it is neither dark nor flat (map screens,
loading screens, fades). For Shorts, frames with a caption on screen are preferred.
"""

from __future__ import annotations

import io
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .media import grab_frames

W, H = 1280, 720
BEIGE, GRID, BLACK, WHITE = "#f5f0e8", "#e9e2d6", "#111111", "#ffffff"
FONT_CANDIDATES = [
    Path.home() / "AppData/Local/Microsoft/Windows/Fonts/BebasNeue-Bold.ttf",
    Path.home() / "AppData/Local/Microsoft/Windows/Fonts/BebasNeue-Regular.ttf",
    Path("C:/Windows/Fonts/BebasNeue-Bold.ttf"),
    Path("C:/Windows/Fonts/BebasNeue-Regular.ttf"),
    Path("/usr/share/fonts/truetype/bebas-neue/BebasNeue-Regular.ttf"),
]
BOLD_CANDIDATES = [Path("C:/Windows/Fonts/arialbd.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")]


def load_font(configured: str | None, candidates: list[Path], size: int) -> ImageFont.FreeTypeFont:
    """Configured font, else the first installed candidate, else Pillow's built-in font."""
    for path in ([Path(configured)] if configured else []) + candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size=size)


def frame_quality(img: Image.Image) -> float:
    """Detail of a frame; dark, washed-out or flat frames score low."""
    a = np.asarray(img.convert("L").resize((160, 90)), dtype=np.float32)
    mean, std = a.mean(), a.std()
    if mean < 45 or mean > 215:
        return 0.0
    return float(std)


def caption_score(img: Image.Image, band: tuple[float, float]) -> float:
    """Share of bright text pixels with dark outline pixels in the caption band of a Short."""
    a = np.asarray(img.convert("L"), dtype=np.float32)
    top, bottom = int(band[0] * a.shape[0]), int(band[1] * a.shape[0])
    strip = a[top:bottom]
    bright, dark = (strip > 235).mean(), (strip < 30).mean()
    return float(min(bright, dark) * 100)


def best_frame(frames: list[bytes], score) -> Image.Image | None:
    images = [Image.open(io.BytesIO(f)).convert("RGB") for f in frames]
    scored = [(score(im), im) for im in images]
    scored = [s for s in scored if s[0] > 0]
    return max(scored, key=lambda s: s[0])[1] if scored else (images[0] if images else None)


def cover(im: Image.Image, w: int, h: int) -> Image.Image:
    s = max(w / im.width, h / im.height)
    im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    left, top = (im.width - w) // 2, (im.height - h) // 2
    return im.crop((left, top, left + w, top + h))


def duration_label(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def compose(game: Image.Image, cam: Image.Image | None, shorts: list[Image.Image], texts: tuple[str, str],
            duration: str, accent: str, fonts: dict) -> Image.Image:
    img = Image.new("RGB", (W, H), BEIGE)
    d = ImageDraw.Draw(img)
    for x in range(0, W, 40):
        d.line([(x, 0), (x, H)], fill=GRID, width=1)
    for y in range(0, H, 40):
        d.line([(0, y), (W, y)], fill=GRID, width=1)

    def card(im: Image.Image, box, radius=18, border=3):
        x, y, w, h = box
        im = im.resize((w, h), Image.LANCZOS) if im.size != (w, h) else im
        shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle([x + 8, y + 12, x + w + 8, y + h + 12], radius, fill=(0, 0, 0, 70))
        shadow = shadow.filter(ImageFilter.GaussianBlur(10))
        img.paste(shadow, (0, 0), shadow)
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius, fill=255)
        img.paste(im, (x, y), mask)
        d.rounded_rectangle([x, y, x + w, y + h], radius, outline=BLACK, width=border)

    # headline: black word(s), then the key word in an accent box; shrink until it fits
    line1, line2 = texts
    size = 150
    while True:
        f = fonts["headline"](size)
        w1, w2 = d.textlength(line1 + " ", font=f), d.textlength(line2, font=f)
        if w1 + w2 + 90 <= W - 40 or size <= 60:
            break
        size -= 10
    x0 = int((W - (w1 + w2 + 40)) // 2)
    top = 30 + (150 - size) // 2
    d.text((x0, top), line1, font=f, fill=BLACK)
    bx = int(x0 + w1 - 10)
    d.rounded_rectangle([bx, top - 16, bx + int(w2) + 50, top + size + 2], 22, fill=accent)
    d.text((bx + 25, top), line2, font=f, fill=WHITE)

    # left: the long recording with facecam and its length
    lw, lh = 540, 304
    left = cover(game, lw, lh)
    if cam is not None:
        left.paste(cover(cam, 150, 84), (lw - 150 - 12, lh - 84 - 40))
    ld = ImageDraw.Draw(left)
    ld.rectangle([0, lh - 30, lw, lh], fill=(0, 0, 0))
    ld.rectangle([14, lh - 18, lw - 14, lh - 12], fill="#555555")
    ld.rectangle([14, lh - 18, 14 + int((lw - 28) * 0.05), lh - 12], fill="#ff2a2a")
    card(left, (40, 255, lw, lh))
    fb = fonts["badge"](40)
    tw = d.textlength(duration, font=fb)
    d.rounded_rectangle([40 + lw - tw - 60, 255 + lh - 20, 40 + lw - 10, 255 + lh + 40], 14, fill=BLACK)
    d.text((40 + lw - tw - 35, 255 + lh - 14), duration, font=fb, fill=WHITE)

    # arrow
    ax, ay = 600, 410
    d.line([(ax, ay), (ax + 70, ay)], fill=BLACK, width=18)
    d.polygon([(ax + 60, ay - 32), (ax + 105, ay), (ax + 60, ay + 32)], fill=BLACK)

    # right: three Shorts, the middle one in front
    shots = (shorts * 3)[:3] if shorts else [game] * 3
    pw, ph = 170, 302
    card(cover(shots[1], pw, ph), (730, 240, pw, ph))
    card(cover(shots[2], pw, ph), (1075, 240, pw, ph))
    card(cover(shots[0], 205, 364), (885, 205, 205, 364), border=4)

    # timeline strip: the cut video
    tx, ty, tl, th = 730, 600, 515, 92
    d.rounded_rectangle([tx + 6, ty + 10, tx + tl + 6, ty + th + 10], 16, fill="#d9d1c3")
    d.rounded_rectangle([tx, ty, tx + tl, ty + th], 16, fill="#1e1e1e", outline=BLACK, width=3)
    rng = random.Random(4)
    for col, y in ((accent, ty + 16), ("#4a8cff", ty + 52)):
        x = tx + 16
        while x < tx + tl - 30:
            w = min(rng.randint(34, 80), tx + tl - 16 - x)
            d.rounded_rectangle([x, y, x + w, y + 26], 5, fill=col)
            x += w + 5
    d.line([(tx + 300, ty + 6), (tx + 300, ty + th - 6)], fill=WHITE, width=3)
    return img


def make(info, clips: list[dict], short_files: list[str], cfg: dict, out_dir: Path) -> list[str]:
    """Render `count` drafts; every draft uses another strong clip and another text."""
    tcfg, layout = cfg["thumbnails"], cfg["layout"]
    out_dir.mkdir(parents=True, exist_ok=True)
    fonts = {"headline": lambda s: load_font(tcfg.get("font"), FONT_CANDIDATES, s),
             "badge": lambda s: load_font(tcfg.get("badge_font"), BOLD_CANDIDATES, s)}
    ranked = sorted(clips, key=lambda c: c.get("final_score", c["score"]), reverse=True)
    cam_region = layout.get("facecam") if not layout.get("facecam_burned_in") else None

    # Shorts frames: a few looks per Short, the one with a caption on screen wins
    band = (0.30, 0.42)  # caption sits on the edge between facecam and gameplay
    short_frames = []
    for f in short_files:
        grabs = grab_frames(Path(f), [2, 5, 8, 12, 18], {"x": 0, "y": 0, "w": 1, "h": 1}, 1080, 1920, 540)
        im = best_frame(grabs, lambda x: caption_score(x, band) + frame_quality(x) / 100)
        if im:
            short_frames.append(im)

    paths = []
    for n in range(tcfg["count"]):
        clip = ranked[n % len(ranked)]
        times = [clip["start"] + (clip["end"] - clip["start"]) * k / 6 for k in range(1, 6)]
        game = best_frame(grab_frames(info.path, times, layout["gameplay"], info.width, info.height, 1280),
                          frame_quality)
        cam = None
        if cam_region:
            grabs = grab_frames(info.path, [clip["peak"]], cam_region, info.width, info.height, 480)
            cam = Image.open(io.BytesIO(grabs[0])).convert("RGB") if grabs else None
        texts = tcfg["texts"][n % len(tcfg["texts"])]
        order = short_frames[n % len(short_frames):] + short_frames[:n % len(short_frames)] if short_frames else []
        img = compose(game, cam, order, (texts[0], texts[1]), duration_label(info.duration),
                      tcfg.get("accent", "#ff5e1a"), fonts)
        path = out_dir / f"thumb_{n + 1:02d}.png"
        img.save(path)
        paths.append(str(path))
    return paths
