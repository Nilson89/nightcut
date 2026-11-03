"""Title, description, chapters and tags for the upload, written by the local LLM."""

from __future__ import annotations

import json

from . import llm

SYSTEM = """You write YouTube metadata for a German gaming channel.
Answer only with JSON:
{"titles": [5 German title ideas, max 60 characters, curiosity without clickbait lies],
 "description": "German video description, 3 short paragraphs, first sentence is the hook, no hashtags",
 "chapters": [{"clip": int, "title": "German chapter title, max 40 characters"}],
 "tags": [10-15 German or English search tags]}
Use one chapter per listed clip, in the given order.
Only promise what really happens in the clips; a wish, plan or guess of the player is not an event."""


def fmt(t: float) -> str:
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def timeline_positions(clips: list[dict]) -> list[float]:
    pos, out = 0.0, []
    for c in clips:
        out.append(pos)
        pos += c["end"] - c["start"]
    return out


def generate(clips: list[dict], edit: dict, cfg: dict) -> dict:
    positions = timeline_positions(clips)
    listing = [
        {"clip": n, "starts_at": fmt(p), "title": c.get("title", ""), "what_happens": c.get("vision", {}).get("summary", ""),
         "player_said": c["transcript"][:200]}
        for n, (c, p) in enumerate(zip(clips, positions), 1)
    ]
    user = (f"Game: {cfg['profile'].get('name', 'unknown')}. Title ideas from the editor: "
            f"{edit.get('video_title_ideas', [])}.\nClips in video order:\n{json.dumps(listing, ensure_ascii=False)}")
    model = cfg["editor"]["model"]
    warning = None
    try:
        answer = llm.chat_json(cfg["ollama"], model, SYSTEM, user, temperature=0.6)
    except Exception as exc:  # keep the night going with what the editor already produced
        answer = {"titles": edit.get("video_title_ideas", []), "description": "", "chapters": [], "tags": []}
        warning = f"LLM failed, metadata incomplete: {exc}"
    finally:
        llm.unload(cfg["ollama"], model)

    names = {ch.get("clip"): ch.get("title", "") for ch in answer.get("chapters", [])}
    chapters = [{"at": fmt(p), "title": names.get(n) or clips[n - 1].get("title") or f"Highlight {n}"}
                for n, p in enumerate(positions, 1)]
    # YouTube only shows chapters from 0:00, at least 3 of them, each >= 10 s.
    chapters = [ch for ch, c in zip(chapters, clips) if c["end"] - c["start"] >= 10] or chapters
    chapters[0]["at"] = "0:00"
    return {
        "titles": answer.get("titles", []),
        "description": answer.get("description", ""),
        "chapters": chapters,
        "tags": answer.get("tags", []),
        **({"warning": warning} if warning else {}),
    }


def to_text(meta: dict) -> str:
    """Ready to paste into YouTube Studio."""
    lines = ["Hinweis: Titel und Kapitel sind KI-Entwürfe. Bitte prüfen, ob das Versprochene wirklich passiert.", "",
             "TITEL-IDEEN", *[f"- {t}" for t in meta["titles"]], "", "BESCHREIBUNG", meta["description"], "",
             "KAPITEL", *[f"{ch['at']} {ch['title']}" for ch in meta["chapters"]], "",
             "TAGS", ", ".join(meta["tags"]), ""]
    return "\n".join(lines)


EPISODE_SYSTEM = """You write YouTube metadata for one episode of a German Let's Play series.
You get the transcript of the episode with timestamps (player talk and game dialogue mixed).
Answer only with JSON:
{"titles": [5 German title ideas, max 50 characters, WITHOUT series name and episode number],
 "summary": "one German sentence: what happens in this episode",
 "description": "German video description, 2 short paragraphs, first sentence is the hook, no hashtags, no spoilers of the ending",
 "chapters": [{"at": "m:ss", "title": "German chapter title, max 40 characters"}],
 "tags": [10-15 German or English search tags]}
Chapters: 4 to 10, in order, the first one at 0:00, only at timestamps that appear in the transcript.
Only promise what really happens in the episode. What the player wishes, plans, guesses or jokes about
("maybe it is a shiny") did not happen; never put it into a title as if it had."""


def _seconds(stamp: str) -> float | None:
    try:
        parts = [int(x) for x in str(stamp).strip().split(":")]
    except ValueError:
        return None
    total = 0
    for p in parts:
        total = total * 60 + p
    return float(total)


def strip_series(title: str, series: str) -> str:
    """Models often repeat the series name although told not to: 'Car Dealer Simulator: Goldbarren' -> 'Goldbarren'."""
    title = title.strip()
    if series and title.lower().startswith(series.lower()):
        title = title[len(series):].lstrip(" :–-|#0123456789").strip()
    return title


def generate_episode(lines: list[tuple[float, str]], series: str, number: int, length: float, cfg: dict,
                     max_chars: int = 24000) -> dict:
    """lines: (edit time inside the episode, text)."""
    text_lines = [f"[{fmt(t)}] {text.strip()}" for t, text in lines if text.strip()]
    step = 1
    while sum(len(x) + 1 for x in text_lines[::step]) > max_chars:
        step += 1  # long episode: keep every n-th line so the model still sees all of it
    user = (f"Series: {series}. Episode {number}, length {fmt(length)}.\nTranscript:\n" + "\n".join(text_lines[::step]))
    model = cfg["editor"]["model"]
    warning = None
    try:
        answer = llm.chat_json(cfg["ollama"], model, EPISODE_SYSTEM, user, temperature=0.6)
    except Exception as exc:
        answer = {}
        warning = f"LLM failed, metadata incomplete: {exc}"
    finally:
        llm.unload(cfg["ollama"], model)

    # YouTube rules: first chapter at 0:00, at least 3, each at least 10 s long.
    chapters, last = [], -1e9
    for ch in answer.get("chapters", []):
        at = _seconds(ch.get("at", ""))
        if at is None or at >= length or at - last < 10:
            continue
        chapters.append({"at": fmt(at), "title": str(ch.get("title", "")).strip() or "Kapitel"})
        last = at
    if chapters:
        chapters[0]["at"] = "0:00"
    if len(chapters) < 3:
        chapters = []
    pattern = cfg["letsplay"].get("title_format", "{series} #{n} – {title}")
    titles = [pattern.format(series=series, n=number, title=strip_series(str(t), series))
              for t in answer.get("titles", []) if strip_series(str(t), series)]
    return {
        "titles": titles or [f"{series} #{number}"],
        "summary": answer.get("summary", ""),
        "description": answer.get("description", ""),
        "chapters": chapters,
        "tags": answer.get("tags", []),
        **({"warning": warning} if warning else {}),
    }
