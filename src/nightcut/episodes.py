"""Let's Play mode: split the kept material into episodes that end at natural breaks.

All recordings are laid end to end. "Source time" is the position in that
combined recording, "edit time" the position after the cuts.
"""

from __future__ import annotations

import re
from collections import Counter

from .segments import speech_gaps


def build_pieces(per_file: list[list[tuple[float, float]]], offsets: list[float]) -> list[dict]:
    """Kept parts of every file, with source and edit positions."""
    pieces, edit = [], 0.0
    for i, parts in enumerate(per_file):
        for start, end in parts:
            pieces.append({"file": i, "start": start, "end": end, "src": offsets[i] + start, "edit": edit})
            edit += end - start
    return pieces


def to_edit(src: float, pieces: list[dict]) -> float:
    """Edit time of a source time; inside a cut it is the point where the cut sits."""
    for p in pieces:
        length = p["end"] - p["start"]
        if src < p["src"]:
            return p["edit"]
        if src <= p["src"] + length:
            return p["edit"] + src - p["src"]
    last = pieces[-1]
    return last["edit"] + last["end"] - last["start"]


def break_points(pieces: list[dict], removals: list[dict], segments: list[dict], blacks: list[float],
                 offsets: list[float], total_src: float, cfg: dict) -> list[dict]:
    """Places where an episode may end, with how natural that place is (0..1).

    Everything here is in source time; removals, segments and blacks too.
    """
    lcfg = cfg["letsplay"]
    weight = {"loading": 1.0, "menu": 0.9, "black": 0.8, "pause": 0.7, "afk": 0.6, "silence": 0.4}
    points = []
    for r in removals:
        score = max(weight.get(label, 0.5) for label in r["label"].split("+"))
        points.append({"src": r["start"], "score": score, "reason": f"cut ({r['label']})"})
    for off in offsets[1:]:
        if lcfg["file_break_score"] > 0:
            # A new recording is a session break: worth ending there even further from the ideal length.
            points.append({"src": off, "score": lcfg["file_break_score"], "reason": "next recording",
                           "reach": lcfg.get("file_break_reach", 2.0)})
    for t in blacks:
        points.append({"src": t, "score": weight["black"], "reason": "black frame"})
    for a, b in speech_gaps(segments, total_src, 2.0):
        points.append({"src": (a + b) / 2, "score": min((b - a) / 10, 1.0) * 0.6, "reason": f"pause {b - a:.0f} s"})
    phrases = [re.compile(r"\b" + re.escape(p.lower()) + r"\b") for p in lcfg.get("break_phrases", [])]
    # A goodbye that appears word for word again and again is Whisper inventing text over music.
    counts = Counter(s["text"].strip().lower() for s in segments)
    for s in segments:
        text = s["text"].lower()
        if counts[text.strip()] >= 3:
            continue
        hit = next((p.pattern for p in phrases if p.search(text)), None)
        if hit:
            points.append({"src": s["end"] + 0.5, "score": 1.0, "reason": f"said: \"{s['text'].strip()[:60]}\""})
    for p in points:
        p["edit"] = to_edit(p["src"], pieces)
    return points


def choose_bounds(total: float, points: list[dict], target: float, tolerance: float) -> list[dict]:
    """Walk through the edit, ending each episode at the best break near its ideal length."""
    n = max(1, round(total / target))
    window = tolerance * target
    bounds, start = [], 0.0
    for k in range(n - 1):
        ideal = start + (total - start) / (n - k)
        best, best_value = None, -1e9
        for p in points:
            dist = abs(p["edit"] - ideal)
            reach = window * p.get("reach", 1.0)
            if dist > reach or p["edit"] <= start:
                continue
            value = p["score"] - 0.5 * dist / reach
            if value > best_value:
                best, best_value = p, value
        bound = dict(best) if best else {"edit": ideal, "score": 0.0, "reason": "length only"}
        bounds.append(bound)
        start = bound["edit"]
    return bounds


def slice_episodes(pieces: list[dict], bounds: list[dict]) -> list[dict]:
    """Cut the piece list at the episode bounds (edit time)."""
    edges = [0.0] + [b["edit"] for b in bounds] + [float("inf")]
    episodes = []
    for n, (a, b) in enumerate(zip(edges, edges[1:]), 1):
        parts = []
        for p in pieces:
            length = p["end"] - p["start"]
            lo, hi = max(a, p["edit"]), min(b, p["edit"] + length)
            if hi - lo > 0.05:
                parts.append({"file": p["file"], "start": round(p["start"] + lo - p["edit"], 3),
                              "end": round(p["start"] + hi - p["edit"], 3)})
        episodes.append({
            "n": n,
            "pieces": parts,
            "length": round(sum(x["end"] - x["start"] for x in parts), 2),
            "ends_at": bounds[n - 1]["reason"] if n <= len(bounds) else "end of material",
            "edit_start": a,
        })
    return [e for e in episodes if e["pieces"]]


def split(pieces: list[dict], points: list[dict], cfg: dict, offsets: list[float] | None = None) -> list[dict]:
    lcfg = cfg["letsplay"]
    if lcfg.get("episodes") == "per_recording":
        # Every recording already is one episode (own intro and outro): only the cuts are applied.
        bounds = [{"edit": to_edit(off, pieces), "score": 1.0, "reason": "next recording"} for off in (offsets or [])[1:]]
        return slice_episodes(pieces, bounds)
    total = sum(p["end"] - p["start"] for p in pieces)
    bounds = choose_bounds(total, points, lcfg["episode_minutes"] * 60, lcfg["tolerance"])
    return slice_episodes(pieces, bounds)
