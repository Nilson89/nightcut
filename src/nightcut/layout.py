"""Find where gameplay and facecam sit in a recording: `nightcut layout recording.mkv`.

Two kinds of recordings:
- Canvas: gameplay and camera are separate areas of one bigger picture (e.g. gameplay on top,
  camera below, rest black). Found without AI: areas that stay black and still in every sampled
  frame are empty, the rest splits into rectangles.
- One picture: the camera is burned into the gameplay (often a person with the background
  removed). The vision model marks the webcam on several frames; the boxes are combined.
"""

from __future__ import annotations

import io
import re
from datetime import date
from pathlib import Path

import numpy as np

from . import llm
from .media import grab_frames, probe, run

FULL = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}

SYSTEM = "You locate objects in screenshots and answer with JSON only."
PROMPT = ("This is a frame of a gameplay recording. Many streamers show themselves with a webcam overlay "
          "(a person, usually in a corner, sometimes with the background removed). "
          "Output the bounding box of the webcam overlay as JSON: "
          '{"webcam": true, "bbox_2d": [x1, y1, x2, y2]} or {"webcam": false} if there is none.')


# ---------------------------------------------------------------- canvas (no AI)

def content_mask(frames: np.ndarray, black: float = 8.0, still: float = 0.5) -> np.ndarray:
    """True where a pixel shows something in at least one frame.

    Unused canvas is pure black and does not change at all. A dark, static corner of a camera
    picture is still a little brighter and has sensor noise, so it counts as content.
    """
    return (frames.max(axis=0) > black) | (frames.std(axis=0) > still)


def _bands(mask: np.ndarray, tol: float = 0.02, min_size: float = 0.01) -> list[tuple[int, int, int, int]]:
    """Split the mask into horizontal bands with the same content extent -> (x0, y0, x1, y1) pixels."""
    h, w = mask.shape
    extents = []
    for row in mask:
        idx = np.flatnonzero(np.convolve(row, np.ones(5), "same") >= 3)
        extents.append((idx[0], idx[-1] + 1) if len(idx) >= 0.02 * w else None)
    bands: list[list] = []  # [x0, x1, y0, y1]
    for y, ext in enumerate(extents):
        last = bands[-1] if bands else None
        same = (last is not None and (ext is None) == (last[0] is None)
                and (ext is None or (abs(ext[0] - last[0]) <= tol * w and abs(ext[1] - last[1]) <= tol * w)))
        if same:
            last[3] = y + 1
        else:
            bands.append([*(ext or (None, None)), y, y + 1])
    # tiny bands (noise, a thin HUD line) join the band above
    merged: list[list] = []
    for b in bands:
        if merged and b[3] - b[2] < min_size * h:
            merged[-1][3] = b[3]
        else:
            merged.append(b)
    return [(b[0], b[2], b[1], b[3]) for b in merged if b[0] is not None]


def _ratio_error(rects: list[tuple[int, int, int, int]]) -> float:
    """How far the rectangles are from 16:9 (0 = all exactly 16:9)."""
    return sum(abs(np.log(max(x1 - x0, 1) / max(y1 - y0, 1) / (16 / 9))) for x0, y0, x1, y1 in rects)


def split_twins(rects: list[tuple[int, int, int, int]], tol: float = 0.04) -> list[tuple[int, int, int, int]]:
    """Gameplay and camera both 16:9 and touching without a gap (e.g. 3840x1080 side by side,
    or 1920x2160 stacked) look like one wide or tall area. Split such an area in two."""
    out = []
    for x0, y0, x1, y1 in rects:
        ratio = (x1 - x0) / max(y1 - y0, 1)
        if abs(ratio / (32 / 9) - 1) <= tol:
            mid = (x0 + x1) // 2
            out += [(x0, y0, mid, y1), (mid, y0, x1, y1)]
        elif abs(ratio / (8 / 9) - 1) <= tol:
            mid = (y0 + y1) // 2
            out += [(x0, y0, x1, mid), (x0, mid, x1, y1)]
        else:
            out.append((x0, y0, x1, y1))
    return out


def content_rects(mask: np.ndarray) -> list[dict]:
    """Rectangles of content as fractions, largest first.

    Tries a split into rows (camera above/below the gameplay) and into columns (camera next to it)
    and keeps the simpler one. With the same number of areas, the one closer to 16:9 wins: for a
    camera at the top right of a side-by-side canvas both splits find two areas, but only the
    column split gives two 16:9 pictures."""
    h, w = mask.shape
    by_rows = split_twins(_bands(mask))
    by_cols = split_twins([(y0, x0, y1, x1) for (x0, y0, x1, y1) in _bands(mask.T)])
    options = [r for r in (by_rows, by_cols) if r]
    rects = min(options, key=lambda r: (len(r), _ratio_error(r))) if options else []
    out = [{"x": x0 / w, "y": y0 / h, "w": (x1 - x0) / w, "h": (y1 - y0) / h} for x0, y0, x1, y1 in rects]
    out = [r for r in out if r["w"] * r["h"] >= 0.02]
    return sorted(out, key=lambda r: r["w"] * r["h"], reverse=True)


def motion(frames: np.ndarray, region: dict) -> float:
    """Mean change between the sampled frames inside a region. Gameplay changes a lot from one
    sample to the next (minutes apart); a webcam keeps showing the same room."""
    n, h, w = frames.shape
    y0 = int(region["y"] * h)
    x0 = int(region["x"] * w)
    y1 = max(int((region["y"] + region["h"]) * h), y0 + 1)
    x1 = max(int((region["x"] + region["w"]) * w), x0 + 1)
    return float(np.abs(np.diff(frames[:, y0:y1, x0:x1], axis=0)).mean()) if n > 1 else 0.0


def gameplay_first(rects: list[dict], frames: np.ndarray, size_ratio: float = 1.5) -> list[dict]:
    """The bigger area is the gameplay. If the two biggest are about the same size, the one that
    changes more between the samples is the gameplay."""
    if len(rects) < 2:
        return rects
    a, b = rects[0], rects[1]
    if a["w"] * a["h"] < size_ratio * b["w"] * b["h"] and motion(frames, b) > motion(frames, a):
        return [b, a, *rects[2:]]
    return rects


def snap_16_9(rects: list[dict], width: int, height: int, tol: float = 0.015) -> list[dict]:
    """Gameplay and camera are almost always 16:9. A region that is nearly 16:9 gets exactly 16:9:
    its inner edge moves (never an edge on the frame border), and a neighbour sharing that edge follows."""
    rects = [dict(r) for r in rects]
    for r in rects:
        pw, ph = r["w"] * width, r["h"] * height
        if not ph or abs(pw / ph / (16 / 9) - 1) > tol:
            continue
        if r["y"] + r["h"] < 0.999:      # bottom edge is inside the frame: fix the height
            old = r["y"] + r["h"]
            r["h"] = pw * 9 / 16 / height
            new = r["y"] + r["h"]
            for o in rects:
                if o is not r and abs(o["y"] - old) < 0.01:
                    o["h"] += o["y"] - new
                    o["y"] = new
        elif r["x"] + r["w"] < 0.999:    # right edge is inside the frame: fix the width
            old = r["x"] + r["w"]
            r["w"] = ph * 16 / 9 / width
            new = r["x"] + r["w"]
            for o in rects:
                if o is not r and abs(o["x"] - old) < 0.01:
                    o["w"] += o["x"] - new
                    o["x"] = new
    return rects


# ---------------------------------------------------------------- burned-in cam (vision model)

def model_size(w: int, h: int, pixels: int = 1_000_000) -> tuple[int, int]:
    """Image size for the vision model: multiples of 28 and about 1 MP.

    Ollama rescales smaller images internally, and the model then answers in the rescaled
    coordinates. Around 1 MP it keeps the picture 1:1, so the answer maps straight back.
    """
    s = (pixels / (w * h)) ** 0.5
    return max(28, round(w * s / 28) * 28), max(28, round(h * s / 28) * 28)


def box_from_answer(answer: dict, w: int, h: int) -> dict | None:
    """Model answer in pixels of the sent image -> fractions, or None if no usable webcam box."""
    box = answer.get("bbox_2d") if answer.get("webcam", True) else None
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in box)
    except (TypeError, ValueError):
        return None
    x0, x1 = sorted((max(0.0, min(x0 / w, 1.0)), max(0.0, min(x1 / w, 1.0))))
    y0, y1 = sorted((max(0.0, min(y0 / h, 1.0)), max(0.0, min(y1 / h, 1.0))))
    area = (x1 - x0) * (y1 - y0)
    if not 0.003 <= area <= 0.4:  # a dot or half the screen is not a webcam
        return None
    return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}


def combine_boxes(boxes: list[dict], pad: float = 0.02, snap: float = 0.025) -> dict:
    """One region from the per-frame boxes: robust spread (the person moves a little), some padding,
    and edges near the frame border snapped to it (cams usually sit in a corner)."""
    x0 = np.percentile([b["x"] for b in boxes], 25) - pad
    y0 = np.percentile([b["y"] for b in boxes], 25) - pad
    x1 = np.percentile([b["x"] + b["w"] for b in boxes], 75) + pad
    y1 = np.percentile([b["y"] + b["h"] for b in boxes], 75) + pad
    x0, y0 = (0.0 if v < snap else v for v in (x0, y0))
    x1, y1 = (1.0 if v > 1 - snap else v for v in (x1, y1))
    return {"x": round(x0, 3), "y": round(y0, 3), "w": round(x1 - x0, 3), "h": round(y1 - y0, 3)}


# ---------------------------------------------------------------- config text

def _fmt(region: dict | None) -> str:
    if region is None:
        return "null"
    return "{ " + ", ".join(f"{k}: {round(region[k], 4)}" for k in ("x", "y", "w", "h")) + " }"


def _where(region: dict) -> str:
    cx, cy = region["x"] + region["w"] / 2, region["y"] + region["h"] / 2
    v = "top" if cy < 0.4 else "bottom" if cy > 0.6 else ""
    h = "left" if cx < 0.4 else "right" if cx > 0.6 else ""
    return f"{v} {h}".strip() or "middle"


def describe(found: dict) -> str:
    """One line for the log: what kind of layout and where the camera is."""
    cam, game = found.get("facecam"), found.get("gameplay")
    if found.get("kind") == "canvas" and cam and game:
        if cam["x"] >= game["x"] + game["w"] - 0.01 or cam["x"] + cam["w"] <= game["x"] + 0.01:
            side = "next to"
        elif cam["y"] >= game["y"] + game["h"] - 0.01:
            side = "below"
        elif cam["y"] + cam["h"] <= game["y"] + 0.01:
            side = "above"
        else:
            side = "beside"
        return f"Layout: canvas, facecam {side} the gameplay ({_where(cam)}) {_fmt(cam)}."
    if cam:
        return f"Layout: facecam inside the gameplay picture ({_where(cam)}) {_fmt(cam)}."
    return "Layout: no facecam, gameplay is the whole picture."


def to_yaml(layout: dict, source: str = "") -> str:
    head = f"# found by nightcut layout{': ' + source if source else ''} ({date.today().isoformat()})\n"
    return (head + "layout:\n"
            "  detect: off\n"
            f"  gameplay: {_fmt(layout['gameplay'])}\n"
            f"  facecam:  {_fmt(layout['facecam'])}\n"
            f"  facecam_burned_in: {'true' if layout['facecam_burned_in'] else 'false'}\n")


def save_layout(path: Path, block: str) -> None:
    """Write the layout block into a config file. An existing top-level `layout:` block (and the
    comment line nightcut put above it) is replaced; everything else stays as it is."""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    lines, out, skip = text.splitlines(), [], False
    for line in lines:
        if re.match(r"layout\s*:", line):
            if out and out[-1].startswith("# found by nightcut layout"):
                out.pop()
            skip = True
            continue
        if skip and line and not line[0].isspace() and not line.startswith("#"):
            skip = False
        if not skip:
            out.append(line)
    while out and not out[-1].strip():
        out.pop()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(("\n".join(out) + "\n\n" if out else "") + block, encoding="utf-8")


# ---------------------------------------------------------------- recording -> layout

def _gray_frames(src: Path, times: list[float], width: int, height: int) -> np.ndarray:
    frames = []
    for t in times:
        out = run(["ffmpeg", "-hide_banner", "-v", "error", "-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1",
                   "-vf", f"scale={width}:{height}:flags=neighbor,format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
        if len(out) == width * height:
            frames.append(np.frombuffer(out, np.uint8).reshape(height, width))
    return np.array(frames, dtype=np.float32)


def sample_times(duration: float, n: int) -> list[float]:
    """n moments spread over the recording, away from intro and outro."""
    return [duration * (0.05 + 0.9 * (i + 0.5) / n) for i in range(n)]


def detect(src: Path, cfg: dict, n_frames: int = 8, log=print) -> dict:
    from PIL import Image

    info = probe(src)
    times = sample_times(info.duration, n_frames)
    sw = min(1024, info.width - info.width % 2)  # no pixel blending: region borders stay sharp
    sh = max(2, round(sw * info.height / info.width / 2) * 2)
    log(f"{src.name}: {info.width}x{info.height}, {info.duration / 60:.0f} min, checking {len(times)} frames ...")

    frames = _gray_frames(src, times, sw, sh)
    rects = gameplay_first(snap_16_9(content_rects(content_mask(frames)), info.width, info.height), frames)
    if len(rects) >= 2:
        log("Canvas layout: gameplay and camera are separate areas.")
        return {"gameplay": rects[0], "facecam": rects[1], "facecam_burned_in": False,
                "kind": "canvas", "times": times, "info": info}

    gameplay = rects[0] if rects and rects[0]["w"] * rects[0]["h"] < 0.95 else FULL
    mw, mh = model_size(info.width, info.height)
    model = cfg["vision"]["model"]
    log(f"One picture: looking for a webcam with {model} ...")
    boxes = []
    try:
        for i, t in enumerate(times, 1):
            jpg = grab_frames(src, [t], FULL, info.width, info.height, mw)
            if not jpg:
                continue
            img = Image.open(io.BytesIO(jpg[0])).convert("RGB").resize((mw, mh))
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=90)
            try:
                answer = llm.chat_json(cfg["ollama"], model, SYSTEM, PROMPT, images=[buf.getvalue()],
                                       temperature=0.0, keep_loaded=True, max_tokens=200)
            except llm.BadAnswer:
                continue
            box = box_from_answer(answer, mw, mh)
            log(f"  frame {i}/{len(times)}: " + (_fmt(box) if box else "no webcam"))
            if box:
                boxes.append(box)
    finally:
        llm.unload(cfg["ollama"], model)

    facecam = combine_boxes(boxes) if len(boxes) >= max(2, len(times) // 2) else None
    return {"gameplay": gameplay, "facecam": facecam, "facecam_burned_in": facecam is not None,
            "kind": "burned_in" if facecam else "no_cam", "times": times, "info": info}


def draw_preview(src: Path, result: dict, out: Path, width: int = 1280) -> Path:
    """Middle frame with the found regions drawn in, to check by eye."""
    from PIL import Image, ImageDraw

    info = result["info"]
    jpg = grab_frames(src, [result["times"][len(result["times"]) // 2]], FULL, info.width, info.height, width)
    img = Image.open(io.BytesIO(jpg[0])).convert("RGB")
    draw = ImageDraw.Draw(img)
    for key, colour in (("gameplay", (60, 220, 90)), ("facecam", (255, 140, 0))):
        r = result.get(key)
        if not r:
            continue
        box = (r["x"] * img.width, r["y"] * img.height,
               (r["x"] + r["w"]) * img.width - 1, (r["y"] + r["h"]) * img.height - 1)
        draw.rectangle(box, outline=colour, width=4)
        draw.rectangle((box[0] + 4, box[1] + 4, box[0] + 110, box[1] + 26), fill=colour)
        draw.text((box[0] + 10, box[1] + 8), key, fill=(0, 0, 0))
    img.save(out)
    return out
