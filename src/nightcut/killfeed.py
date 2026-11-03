"""Find your kills in the kill feed, also the ones you did not react to out loud.

Audio peaks only catch moments where you or the game get loud. Shooters show
every kill as a text line ("KILLER [WEAPON] VICTIM"). nightcut cuts the kill
feed out of a keyframe every few seconds, stacks several of these small crops
into one image and lets the vision model read them. Reading text in a small
crop is far cheaper than judging whole frames, so the whole session can be
checked. Your kills become extra candidates.
"""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path

from . import llm
from .media import region_pixels, run

SYSTEM = """You read the kill feed of a shooter game.
The image is a vertical stack of {n} panels of equal height, numbered 1 to {n} from top to bottom.
Each panel shows the kill feed at a different moment.
A kill line reads: KILLER [WEAPON] VICTIM. Lines without a weapon in brackets
(revive requests, objective messages, squad orders) are not kills.
Answer only with JSON:
{{"panels": [{{"panel": <panel number>, "kills": [{{"killer": <killer as written>, "victim": <victim as written>}}]}}]}}
Only list panels that contain kill lines. If no panel has one, answer {{"panels": []}}."""

PLACEHOLDERS = {"", "name", "killer", "victim", "<killer>", "<victim>"}


def feed_region(gameplay: dict, feed: dict) -> dict:
    """Kill feed region (given inside the gameplay picture) as fractions of the full frame."""
    return {"x": gameplay["x"] + feed["x"] * gameplay["w"], "y": gameplay["y"] + feed["y"] * gameplay["h"],
            "w": feed["w"] * gameplay["w"], "h": feed["h"] * gameplay["h"]}


def extract_tiles(src: Path, region: dict, src_w: int, src_h: int, every_s: float, per_tile: int,
                  out_dir: Path) -> list[dict]:
    """One pass over the keyframes: crop the feed, keep one crop every few seconds, stack per_tile of them.

    The tiles are kept, so an interrupted night does not read the whole recording again.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    x, y, w, h = region_pixels(region, src_w, src_h)
    settings = {"source": str(src), "every_s": every_s, "per_tile": per_tile, "crop": [x, y, w, h]}
    index = out_dir / "tiles.json"
    if index.exists():
        saved = json.loads(index.read_text(encoding="utf-8"))
        if saved["settings"] == settings and all(Path(t["file"]).exists() for t in saved["tiles"]):
            return saved["tiles"]
    for old in out_dir.glob("tile_*.jpg"):  # left over from a run with other settings
        old.unlink()
    (out_dir / "readings.jsonl").unlink(missing_ok=True)
    select = f"select='isnan(prev_selected_t)+gte(t-prev_selected_t\\,{every_s - 0.1})'"
    graph = f"crop={w}:{h}:{x}:{y},{select},showinfo,tile=1x{per_tile}"
    proc = run(["ffmpeg", "-hide_banner", "-nostats", "-v", "info", "-y", "-skip_frame", "nokey", "-i", str(src),
                "-map", "0:v:0", "-vf", graph, "-fps_mode", "passthrough", "-q:v", "3",
                str(out_dir / "tile_%05d.jpg")], capture_output=True)
    times = [float(t) for t in re.findall(rb"pts_time:\s*(-?[\d.]+)", proc.stderr)]
    tiles = []
    for n, path in enumerate(sorted(out_dir.glob("tile_*.jpg"))):
        panel_times = times[n * per_tile:(n + 1) * per_tile]
        if panel_times:
            tiles.append({"file": str(path), "times": panel_times})
    index.write_text(json.dumps({"settings": settings, "tiles": tiles}), encoding="utf-8")
    return tiles


def parse_answer(answer: dict, times: list[float]) -> list[dict]:
    """Model answer -> readings with the time of each panel."""
    readings = []
    for panel in answer.get("panels", []):
        idx = panel.get("panel") if isinstance(panel, dict) else None
        if isinstance(idx, int) and 1 <= idx <= len(times):
            kills = [k for k in panel.get("kills", []) if isinstance(k, dict)
                     and str(k.get("killer", "")).strip().lower() not in PLACEHOLDERS]
            if kills:
                readings.append({"time": times[idx - 1], "kills": kills})
    return readings


def read_tiles(tiles: list[dict], cfg: dict, progress=None, cache: Path | None = None) -> list[dict]:
    """Ask the vision model for all kill lines; the player's are picked out in code.

    Asking the model for "only the player's kills" made it report other people's kills
    too. Copying every line and matching the name afterwards is more reliable.
    Every answer is appended to `cache` right away, so a restart continues where it stopped.
    """
    kcfg, ocfg = cfg["killfeed"], cfg["ollama"]
    model = kcfg.get("model") or cfg["vision"]["model"]
    done: dict[str, list[dict]] = {}
    if cache and cache.exists():
        for line in cache.read_text(encoding="utf-8").splitlines():
            entry = json.loads(line)
            done[entry["file"]] = entry["readings"]
    used_model = False
    for n, tile in enumerate(tiles, 1):
        if tile["file"] not in done:
            count = len(tile["times"])
            try:
                answer = llm.chat_json(ocfg, model, SYSTEM.format(n=count), f"{count} panels.",
                                       images=[Path(tile["file"]).read_bytes()], temperature=0.0,
                                       keep_loaded=True, max_tokens=800)
            except llm.OllamaError:
                raise
            except Exception:  # one unreadable tile must not stop the night
                answer = {}
            used_model = True
            done[tile["file"]] = parse_answer(answer, tile["times"])
            if cache:
                with open(cache, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"file": tile["file"], "readings": done[tile["file"]]}, ensure_ascii=False) + "\n")
        if progress:
            progress(n, len(tiles))
    if used_model:
        llm.unload(ocfg, model)
    return sorted((r for t in tiles for r in done.get(t["file"], [])), key=lambda r: r["time"])


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def is_player(name: str, players: list[str], min_ratio: float = 0.8) -> bool:
    """Fuzzy match: the model may misread a character or two (l/1, O/0)."""
    n = _norm(name)
    return bool(n) and any(difflib.SequenceMatcher(None, n, _norm(p)).ratio() >= min_ratio for p in players)


def player_kills(readings: list[dict], players: list[str], memory_s: float = 12.0) -> list[dict]:
    """A kill line stays visible for several seconds: count each victim once."""
    kills, last_seen = [], {}
    for r in readings:
        for k in r["kills"]:
            if not is_player(k.get("killer", ""), players):
                continue
            victim = _norm(k.get("victim", ""))
            if victim in last_seen and r["time"] - last_seen[victim] <= memory_s:
                last_seen[victim] = r["time"]
                continue
            last_seen[victim] = r["time"]
            kills.append({"time": r["time"], "victim": k.get("victim", "")})
    return kills


def group_kills(kills: list[dict], gap_s: float) -> list[dict]:
    """Kills close together form one moment (a multikill)."""
    groups: list[dict] = []
    for k in kills:
        if groups and k["time"] - groups[-1]["end"] <= gap_s:
            groups[-1]["end"] = k["time"]
            groups[-1]["victims"].append(k["victim"])
        else:
            groups.append({"start": k["time"], "end": k["time"], "victims": [k["victim"]]})
    for g in groups:
        g["kills"] = len(g["victims"])
    return groups


def killer_stats(readings: list[dict]) -> dict[str, int]:
    """Who appears as killer how often; helps to find your in-game name."""
    counts: dict[str, int] = {}
    for r in readings:
        for k in r["kills"]:
            name = str(k.get("killer", "")).strip()
            if name:
                counts[name] = counts.get(name, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def merge_into(candidates: list[dict], groups: list[dict], segments: list[dict], score, duration: float,
               cfg: dict) -> list[dict]:
    """Mark candidates that contain kills and add new ones for kills nobody reacted to."""
    ccfg = cfg["candidates"]
    hop = 0.25
    out = [dict(c) for c in candidates]
    for g in groups:
        # the line shows up shortly after the kill; the sample may be a few seconds late
        start = max(g["start"] - ccfg["pre_roll_s"] - cfg["killfeed"]["every_s"], 0.0)
        end = min(g["end"] + ccfg["post_roll_s"], duration)
        hit = next((c for c in out if c["start"] < end and c["end"] > start), None)
        if hit:
            hit["kills"] = hit.get("kills", 0) + g["kills"]
            continue
        for seg in segments:  # do not cut mid-sentence
            if seg["start"] < end < seg["end"] and seg["end"] - end <= ccfg["snap_to_speech_s"]:
                end = seg["end"] + 0.3
        window = score[int(start / hop):int(end / hop) + 1]
        out.append({
            "peak": round(g["start"], 2),
            "start": round(start, 2),
            "end": round(min(end, duration), 2),
            "score": round(float(window.max()) if len(window) else 0.0, 4),
            "transcript": " ".join(s["text"].strip() for s in segments if s["end"] > start and s["start"] < end),
            "kills": g["kills"],
            "vision": {"event": "multikill" if g["kills"] > 1 else "kill",
                       "intensity": min(10, 5 + 2 * g["kills"]),
                       "summary": f"{g['kills']} Abschuss/Abschüsse laut Killfeed: {', '.join(g['victims'])}",
                       "source": "killfeed"},
        })
    out.sort(key=lambda c: c["start"])
    for n, c in enumerate(out, 1):
        c["id"] = n
    return out
