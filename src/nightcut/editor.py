"""Choose and order the clips for the final timeline."""

from __future__ import annotations

import json

from . import llm

SYSTEM = """You are the editor of a German gaming YouTube channel.
From a list of candidate moments of one recording session you build the video:
- keep the strongest moments: kills, multikills, clutch plays, successes, fails, chaos, funny moments and big reactions
- in calm games (simulators, story games) the player's talk and reactions are the content: pick what is entertaining to hear
- a death is only worth it when it is funny or the reaction is great; never several plain deaths
- drop boring or repetitive moments
- the sum of the chosen clip lengths must be close to the target length; pick enough clips
- the first clip must be a strong hook; after that keep chronological order unless reordering clearly helps
- give every chosen clip a short German title (max 5 words) and a one-sentence reason
- titles and reasons only state what really happens; a wish, plan or guess of the player ("hopefully a shiny") is not an event
Answer only with JSON:
{"clips": [{"id": int, "title": str, "reason": str}], "video_title_ideas": [str, str, str]}"""


def _clamp(c: dict, max_len: float) -> tuple[float, float]:
    """Shorten long clips around their peak."""
    start, end = c["start"], c["end"]
    if end - start <= max_len:
        return start, end
    start = max(start, c["peak"] - 0.7 * max_len)
    return start, start + max_len


TITLE_SYSTEM = """You name clips of a German gaming YouTube video.
For every clip give a short German title (max 5 words) that says what happens or what the player says.
No generic titles like "Highlight" or "Spannender Moment".
Only claim what clearly happens. If the player only talks about something (a wish, a plan, a guess, a joke),
the title says that he talks about it, not that it happened: "Hoffen auf Shiny", not "Shiny gefangen".
Answer only with JSON: {"titles": [{"id": int, "title": str}]}"""


def _title_top_ups(chosen: list[dict], cfg: dict) -> None:
    """Clips added by score have no title yet: one short extra call names them all."""
    ecfg = cfg["editor"]
    missing = [c for c in chosen if not c.get("title")]
    if not missing:
        return
    listing = [{"id": c["id"], "what_happens": c.get("vision", {}).get("summary", "")[:120],
                "player_said": c["transcript"][:150]} for c in missing]
    try:
        answer = llm.chat_json(cfg["ollama"], ecfg["model"], TITLE_SYSTEM,
                               f"Game: {cfg['profile'].get('name', 'unknown')}. Clips:\n{json.dumps(listing, ensure_ascii=False)}",
                               temperature=0.3, max_tokens=2000)
    except llm.OllamaError:
        raise
    except Exception:  # titles are nice to have; the edit itself stands
        return
    finally:
        llm.unload(cfg["ollama"], ecfg["model"])
    titles = {t.get("id"): str(t.get("title", "")).strip() for t in answer.get("titles", []) if isinstance(t, dict)}
    for c in missing:
        if titles.get(c["id"]):
            c["title"] = titles[c["id"]]


def _top_up_order(candidates: list[dict], fill: bool = True) -> list[dict]:
    """Candidates for topping up: rated moments by final score, then 'nothing' by audio score; no deaths.

    fill=False: no 'nothing' moments, a calm session rather becomes a shorter video.
    """
    event = lambda c: c.get("vision", {}).get("event")  # noqa: E731
    rated = [c for c in candidates if event(c) not in ("death", "nothing")]
    quiet = [c for c in candidates if event(c) == "nothing"] if fill else []
    return (sorted(rated, key=lambda c: c.get("final_score", c["score"]), reverse=True)
            + sorted(quiet, key=lambda c: c["score"], reverse=True))


def heuristic(candidates: list[dict], target_s: float, max_len: float) -> list[dict]:
    """Best clips by score until the target length is reached, in chronological order."""
    chosen, total = [], 0.0
    for c in sorted(candidates, key=lambda c: c.get("final_score", c["score"]), reverse=True):
        start, end = _clamp(c, max_len)
        if total + (end - start) > target_s * 1.1:
            continue
        chosen.append({**c, "start": start, "end": end, "title": "", "reason": "highest score"})
        total += end - start
    return sorted(chosen, key=lambda c: c["start"])


def choose(candidates: list[dict], cfg: dict) -> tuple[list[dict], dict]:
    ecfg = cfg["editor"]
    target_s = ecfg["target_minutes"] * 60
    if not ecfg.get("enabled", True) or not candidates:
        return heuristic(candidates, target_s, ecfg["max_clip_s"]), {}

    # The prompt must fit the model's context: only the best candidates, short transcripts.
    # Too many candidates got the prompt cut off and the model answered nonsense.
    pool = sorted(candidates, key=lambda c: c.get("final_score", c["score"]), reverse=True)
    pool = sorted(pool[:ecfg.get("max_candidates", 60)], key=lambda c: c["start"])
    listing = [
        {
            "id": c["id"],
            "at": f"{int(c['start'] // 60)}:{int(c['start'] % 60):02d}",
            "length_s": round(min(c["end"] - c["start"], ecfg["max_clip_s"])),
            "score": c.get("final_score", c["score"]),
            "event": c.get("vision", {}).get("event"),
            "what_happens": c.get("vision", {}).get("summary", "")[:120],
            "player_said": c["transcript"][:150],
        }
        for c in pool
    ]
    user = (f"Game: {cfg['profile'].get('name', 'unknown')}. Target length: {ecfg['target_minutes']} minutes "
            f"({target_s} s). Candidates:\n{json.dumps(listing, ensure_ascii=False)}")
    try:
        answer = llm.chat_json(cfg["ollama"], ecfg["model"], SYSTEM, user, temperature=0.3, max_tokens=4000)
    except llm.OllamaError:
        raise  # setup problem (Ollama down, model missing): tell the user
    except Exception as exc:  # timeout or bad answer: fall back instead of losing the night
        return heuristic(candidates, target_s, ecfg["max_clip_s"]), {"warning": f"LLM failed, score-based edit: {exc}"}
    finally:
        llm.unload(cfg["ollama"], ecfg["model"])

    by_id = {c["id"]: c for c in candidates}
    chosen, seen = [], set()
    for item in answer.get("clips", []):
        c = by_id.get(item.get("id"))
        if not c or c["id"] in seen:
            continue
        seen.add(c["id"])
        start, end = _clamp(c, ecfg["max_clip_s"])
        chosen.append({**c, "start": start, "end": end,
                       "title": item.get("title", ""), "reason": item.get("reason", "")})
    if not chosen:  # model answered nonsense: fall back instead of failing the night
        return heuristic(candidates, target_s, ecfg["max_clip_s"]), {
            "warning": "LLM answer had no usable clips, score-based edit without titles"}
    # Models tend to pick too little. Top up with the best remaining clips, but never with plain deaths.
    # Moments the vision model saw "nothing" in come last, ranked by audio/transcript only: in calm games
    # the picture rarely changes and the reaction is what matters.
    total = sum(c["end"] - c["start"] for c in chosen)
    for c in _top_up_order(candidates, ecfg.get("fill") is not False):
        if total >= target_s * 0.9:
            break
        if c["id"] in seen:
            continue
        start, end = _clamp(c, ecfg["max_clip_s"])
        chosen.append({**c, "start": start, "end": end, "title": "", "reason": "added by score"})
        seen.add(c["id"])
        total += end - start
    _title_top_ups(chosen, cfg)
    # The model's first pick is the hook; the rest follows the session in order.
    chosen = chosen[:1] + sorted(chosen[1:], key=lambda c: c["start"])
    return chosen, {"video_title_ideas": answer.get("video_title_ideas", [])}
