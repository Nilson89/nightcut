"""Turn audio tracks and transcript into a highlight score over time.

Everything runs on a fixed grid of HOP seconds so signals can be added up.
"""

from __future__ import annotations

import re

import numpy as np

HOP = 0.25  # seconds per frame


def envelope_db(pcm: np.ndarray, sample_rate: int, chunk_frames: int = 20000) -> np.ndarray:
    """RMS loudness per HOP frame in dBFS. Works chunk-wise on memory-mapped audio."""
    hop = int(sample_rate * HOP)
    n_frames = len(pcm) // hop
    out = np.empty(n_frames, dtype=np.float32)
    for start in range(0, n_frames, chunk_frames):
        stop = min(start + chunk_frames, n_frames)
        block = np.asarray(pcm[start * hop:stop * hop], dtype=np.float32).reshape(-1, hop) / 32768.0
        rms = np.sqrt(np.mean(block * block, axis=1) + 1e-12)
        out[start:stop] = 20 * np.log10(rms)
    return out


def moving_average(x: np.ndarray, window: int) -> np.ndarray:
    if window <= 1:
        return x.astype(np.float32)
    kernel = np.ones(window, dtype=np.float32) / window
    return np.convolve(x, kernel, mode="same").astype(np.float32)


def spike_score(db: np.ndarray, baseline_s: float = 60.0, full_scale_db: float = 15.0) -> np.ndarray:
    """0..1 score for how much louder a moment is than its surroundings.

    Quiet frames (bottom 30 %) never count, so a single noise in a silent
    stretch does not look like a highlight.
    """
    baseline = moving_average(db, int(baseline_s / HOP))
    excess = np.clip((db - baseline) / full_scale_db, 0.0, 1.0)
    floor = np.percentile(db, 30)
    excess[db < floor] = 0.0
    return excess


def voice_regions(db: np.ndarray, margin_db: float, max_chunk_s: float,
                  merge_gap_s: float = 0.8, pad_s: float = 0.3, min_len_s: float = 0.6) -> list[tuple[float, float]]:
    """Find stretches where the mic is clearly above its noise floor."""
    noise_floor = np.percentile(db, 20)
    active = db > noise_floor + margin_db
    regions: list[list[float]] = []
    idx = np.flatnonzero(np.diff(np.concatenate(([0], active.astype(np.int8), [0]))))
    for start, stop in zip(idx[::2], idx[1::2]):
        s, e = start * HOP - pad_s, stop * HOP + pad_s
        if regions and s - regions[-1][1] <= merge_gap_s:
            regions[-1][1] = e
        else:
            regions.append([max(s, 0.0), e])
    out: list[tuple[float, float]] = []
    for s, e in regions:
        if e - s < min_len_s:
            continue
        while e - s > max_chunk_s:  # Whisper works on <=30 s windows
            out.append((s, s + max_chunk_s))
            s += max_chunk_s
        out.append((s, e))
    return out


def keyword_score(segments: list[dict], keywords: dict, n_frames: int) -> np.ndarray:
    """Add keyword weights over the time of each matching sentence."""
    score = np.zeros(n_frames, dtype=np.float32)
    patterns = [
        (float(weight), re.compile(r"\b" + re.escape(word.lower()) + r"\b"))
        for weight, words in (keywords or {}).items()
        for word in words
    ]
    for seg in segments:
        text = seg["text"].lower()
        weight = sum(w for w, pat in patterns if pat.search(text))
        weight += 0.5 * text.count("!")
        if weight <= 0:
            continue
        a, b = int(seg["start"] / HOP), int(seg["end"] / HOP) + 1
        score[a:min(b, n_frames)] += weight
    peak = score.max()
    return score / peak if peak > 0 else score


def combine(signals: dict[str, np.ndarray], weights: dict, smooth_s: float) -> np.ndarray:
    n = min(len(s) for s in signals.values())
    total = np.zeros(n, dtype=np.float32)
    for name, sig in signals.items():
        total += float(weights.get(name, 0.0)) * sig[:n]
    return moving_average(total, int(smooth_s / HOP))


def pick_candidates(score: np.ndarray, segments: list[dict], cfg: dict, duration: float) -> list[dict]:
    """Greedy peak picking with suppression, then cut windows around each peak."""
    threshold = np.percentile(score, cfg["min_score_percentile"])
    gap = int(cfg["min_gap_s"] / HOP)
    work = score.copy()
    peaks = []
    hours = len(score) * HOP / 3600
    max_count = max(cfg["max_count"], int(cfg.get("per_hour", 0) * hours))
    # A single mixed track (game music everywhere) has few clear peaks: then go below the
    # percentile until the editor has enough to choose from, but never below the median.
    min_count = min(int(cfg.get("min_count", 0)), max_count)
    floor = np.median(score)
    while len(peaks) < max_count:
        i = int(np.argmax(work))
        limit = threshold if len(peaks) >= min_count else floor
        if work[i] <= limit or work[i] <= 0:
            break
        peaks.append(i)
        work[max(i - gap, 0):i + gap] = -np.inf

    candidates = []
    for i in sorted(peaks):
        t = i * HOP
        start = max(t - cfg["pre_roll_s"], 0.0)
        end = min(t + cfg["post_roll_s"], duration)
        # Do not cut in the middle of a sentence.
        for seg in segments:
            if seg["start"] < end < seg["end"] and seg["end"] - end <= cfg["snap_to_speech_s"]:
                end = seg["end"] + 0.3
            if seg["start"] < start < seg["end"] and start - seg["start"] <= cfg["snap_to_speech_s"]:
                start = max(seg["start"] - 0.3, 0.0)
        candidates.append({
            "peak": round(t, 2),
            "start": round(start, 2),
            "end": round(min(end, duration), 2),
            "score": round(float(score[i]), 4),
            "transcript": " ".join(s["text"].strip() for s in segments if s["end"] > start and s["start"] < end),
        })
    return _merge_overlaps(candidates)


def _merge_overlaps(cands: list[dict]) -> list[dict]:
    merged: list[dict] = []
    for c in cands:
        if merged and c["start"] <= merged[-1]["end"]:
            m = merged[-1]
            m["end"] = max(m["end"], c["end"])
            m["score"] = max(m["score"], c["score"])
            m["transcript"] = (m["transcript"] + " " + c["transcript"]).strip()
        else:
            merged.append(dict(c))
    for n, c in enumerate(merged, 1):
        c["id"] = n
    return merged
