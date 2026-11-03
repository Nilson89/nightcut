"""Let's Play mode: find the stretches of a long recording nobody wants to watch.

The picture is checked at every keyframe. Where it stands still (menu, loading
screen, pause, AFK) or is black, the vision model says what it is. Only labels
listed for the chosen strength are removed, and never while someone is talking.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from . import llm
from .media import grab_frames

SYSTEM = """You look at frames from a gameplay recording that stands still for a while.
The small webcam image of the streamer in a corner is not part of the game; ignore it.
Answer only with JSON:
{"label": one of ["gameplay", "cutscene", "menu", "loading", "pause", "black"],
 "summary": one short sentence in German}
menu = main menu, options, character selection, map or inventory screen.
loading = loading screen or loading indicator.
pause = pause menu or game paused.
gameplay = normal game picture, the player just does not move."""

LABELS = {"gameplay", "cutscene", "menu", "loading", "pause", "black"}


def runs(flag: np.ndarray) -> list[tuple[int, int]]:
    """Index ranges [a, b) where flag is True."""
    edges = np.flatnonzero(np.diff(np.concatenate(([0], flag.astype(np.int8), [0]))))
    return list(zip(edges[::2].tolist(), edges[1::2].tolist()))


def still_stretches(scan: dict, cfg: dict, min_len: float) -> list[dict]:
    """Stretches where consecutive keyframes look the same, or the picture is black."""
    t, motion, luma = scan["time"], scan["motion"], scan["luma"]
    black = luma < cfg["black_luma"]
    flag = (motion < cfg["still_motion"]) | black
    out = []
    for a, b in runs(flag):
        # keyframe i equal to keyframe i-1 means the picture froze at t[i-1]
        start = float(t[a - 1]) if a > 0 and not black[a] else float(t[a])
        end = float(t[b - 1])
        if end - start >= min_len:
            out.append({"start": round(start, 2), "end": round(end, 2), "black": round(float(black[a:b].mean()), 2)})
    return out


def label_stretches(src: Path, stretches: list[dict], region: dict, width: int, height: int, cfg: dict,
                    progress=None) -> None:
    """Ask the vision model what is on screen; mostly black stretches need no model."""
    vcfg, ocfg = cfg["vision"], cfg["ollama"]
    n_frames = cfg["letsplay"]["vision_frames"]
    used_model = False
    for n, s in enumerate(stretches, 1):
        if s["black"] >= 0.8:
            s["label"], s["summary"] = "black", ""
        elif not vcfg["enabled"]:
            s["label"], s["summary"] = "unknown", ""
        else:
            step = (s["end"] - s["start"]) / (n_frames + 1)
            times = [round(s["start"] + step * (i + 1), 2) for i in range(n_frames)]
            frames = grab_frames(src, times, region, width, height, 768)
            try:
                answer = llm.chat_json(ocfg, vcfg["model"], SYSTEM, f"{len(frames)} frames, {step:.0f} s apart.",
                                       images=frames, keep_loaded=True)
                used_model = True
            except llm.OllamaError:
                raise
            except Exception as exc:  # one bad answer must not stop the night
                answer = {"label": "unknown", "summary": f"vision failed: {exc}"}
            label = str(answer.get("label", "unknown"))
            s["label"] = label if label in LABELS else "unknown"
            s["summary"] = str(answer.get("summary", ""))
        if progress:
            progress(n, len(stretches))
    if used_model:
        llm.unload(ocfg, vcfg["model"])


def speech_free(start: float, end: float, segments: list[dict], pad: float) -> tuple[float, float] | None:
    """Longest part of [start, end] without speech, shrunk by pad on both sides."""
    busy = sorted((max(s["start"], start), min(s["end"], end)) for s in segments
                  if s["end"] > start and s["start"] < end)
    best, cur = None, start
    for s, e in busy + [(end, end)]:
        if s - cur > (best[1] - best[0] if best else 0):
            best = (cur, s)
        cur = max(cur, e)
    if not best:
        return None
    a, b = best[0] + pad, best[1] - pad
    return (round(a, 2), round(b, 2)) if b > a else None


def chunk_regions(regions: list[tuple[float, float]], max_len: float) -> list[tuple[float, float]]:
    """Split long regions into pieces Whisper can take in one go."""
    out = []
    for a, b in regions:
        while b - a > max_len:
            out.append((round(a, 2), round(a + max_len, 2)))
            a += max_len
        if b - a > 0.5:
            out.append((round(a, 2), round(b, 2)))
    return out


def real_speech(segments: list[dict], min_words: int = 3) -> list[dict]:
    """Whisper on music or game noise invents short phrases ("Vielen Dank."); real talk has a few words."""
    return [s for s in segments if len(s["text"].split()) >= min_words]


def speech_gaps(segments: list[dict], duration: float, min_len: float) -> list[tuple[float, float]]:
    """Pauses between sentences of at least min_len seconds."""
    gaps, cur = [], 0.0
    for s in sorted(segments, key=lambda s: s["start"]):
        if s["start"] - cur >= min_len:
            gaps.append((cur, s["start"]))
        cur = max(cur, s["end"])
    if duration - cur >= min_len:
        gaps.append((cur, duration))
    return gaps


def plan_removals(stretches: list[dict], segments: list[dict], duration: float, cfg: dict) -> list[dict]:
    """Decide what to cut for the configured strength."""
    lcfg = cfg["letsplay"]
    strength = lcfg["strengths"][lcfg["strength"]]
    remove, min_gap, pad = set(strength["remove"]), strength["min_gap_s"], lcfg["keep_pad_s"]
    out = []
    for s in stretches:
        label = s.get("label", "unknown")
        quiet = not any(seg["end"] > s["start"] and seg["start"] < s["end"] for seg in segments)
        if label == "gameplay" and quiet and s["end"] - s["start"] >= lcfg["afk_s"]:
            label = "afk"
        if label not in remove:
            continue
        part = speech_free(s["start"], s["end"], segments, pad)
        if part and part[1] - part[0] >= min_gap:
            out.append({"start": part[0], "end": part[1], "label": label, "summary": s.get("summary", "")})
    if "silence" in remove:
        for a, b in speech_gaps(segments, duration, strength["silence_s"]):
            if b - a - 2 * pad >= min_gap:
                out.append({"start": round(a + pad, 2), "end": round(b - pad, 2), "label": "silence", "summary": ""})
    return merge(out)


def merge(removals: list[dict]) -> list[dict]:
    out: list[dict] = []
    for r in sorted(removals, key=lambda r: r["start"]):
        if out and r["start"] <= out[-1]["end"]:
            out[-1]["end"] = max(out[-1]["end"], r["end"])
            if r["label"] not in out[-1]["label"].split("+"):
                out[-1]["label"] += "+" + r["label"]
        else:
            out.append(dict(r))
    return out


def kept_pieces(duration: float, removals: list[dict]) -> list[tuple[float, float]]:
    """Everything that is not removed."""
    pieces, cur = [], 0.0
    for r in removals:
        if r["start"] > cur:
            pieces.append((round(cur, 2), r["start"]))
        cur = max(cur, r["end"])
    if duration > cur:
        pieces.append((round(cur, 2), round(duration, 2)))
    return pieces
