import numpy as np
from PIL import Image

from nightcut import thumbnails


def _noise(w, h, mean=120, std=50, seed=0):
    rng = np.random.default_rng(seed)
    a = np.clip(rng.normal(mean, std, (h, w, 3)), 0, 255).astype(np.uint8)
    return Image.fromarray(a)


def test_dark_and_flat_frames_lose():
    assert thumbnails.frame_quality(Image.new("RGB", (320, 180), (10, 10, 10))) == 0.0
    assert thumbnails.frame_quality(Image.new("RGB", (320, 180), (240, 240, 240))) == 0.0
    assert thumbnails.frame_quality(_noise(320, 180)) > thumbnails.frame_quality(_noise(320, 180, std=5))


def test_caption_frame_is_preferred():
    plain = _noise(540, 960, std=20)
    captioned = plain.copy()
    px = captioned.load()
    for y in range(330, 380):  # white text with black outline inside the caption band
        for x in range(100, 440):
            px[x, y] = (255, 255, 255) if (x // 6) % 2 else (0, 0, 0)
    band = (0.30, 0.42)
    assert thumbnails.caption_score(captioned, band) > thumbnails.caption_score(plain, band)


def test_missing_font_falls_back():
    font = thumbnails.load_font("does/not/exist.ttf", [], 40)
    assert font is not None


def test_compose_makes_a_1280x720_image():
    fonts = {"headline": lambda s: thumbnails.load_font(None, thumbnails.FONT_CANDIDATES, s),
             "badge": lambda s: thumbnails.load_font(None, thumbnails.BOLD_CANDIDATES, s)}
    img = thumbnails.compose(_noise(1280, 720), _noise(480, 270, seed=1), [_noise(540, 960, seed=2)],
                             ("VIDEOS AUF", "AUTOPILOT"), "3:43:23", "#ff5e1a", fonts)
    assert img.size == (1280, 720)
    assert img.getpixel((5, 700)) == (245, 240, 232)  # beige background in the corner


def test_duration_label():
    assert thumbnails.duration_label(13403) == "3:43:23"
    assert thumbnails.duration_label(754) == "12:34"
