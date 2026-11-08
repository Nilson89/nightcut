"""Pick the mic, game and voice chat tracks of a recording, also when they are named differently.

Order for each track:
1. the names from the config (`audio.mic.title` ...), as before
2. a fixed `index` from the config, if one is set (`index: 2`)
3. common names ("Mikrofon", "Voice", "Headset", "Discord", "Spiel" ...)
4. mic only: the content. Sample windows of every track are measured. The mic is the track that
   has sound most of the time and the biggest level range: loud while talking, nearly quiet in
   between. A game track or a mix (everything together) is never that quiet.
5. one track only: it is a mix, use it as the mic.

OBS default names like "Track1" or "Audio 2" count as no name.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .media import AudioStream, MediaInfo, run

PARTY_NAMES = ["party", "discord", "teamspeak", "ts", "ts3", "voice chat", "voicechat", "voice-chat", "chat",
               "mumble", "guilded", "call", "sprachchat"]
MIX_NAMES = ["mix", "mixed", "stream", "master", "gesamt", "aufnahme", "recording", "all", "alles", "simple_aac_recording"]
MIC_NAMES = ["mic", "mics", "mikro", "mikrofon", "microphone", "micro", "voice", "stimme", "headset", "vocals",
             "kommentar", "commentary", "sprache", "talk"]
GAME_NAMES = ["game", "spiel", "gameplay", "ingame", "game audio", "spielsound", "desktop", "desktop audio",
              "desktop-audio", "system", "systemsound"]
GENERIC = re.compile(r"^(track|audio|spur|tonspur|channel|kanal)?\s*[_ -]?\s*\d*$", re.IGNORECASE)

SILENT_DB = -100.0      # below this a 10 ms frame is digital silence
MIN_SOUND = 0.5         # a mic has sound (at least room noise) most of the time
SAMPLE_WINDOWS = 8
SAMPLE_SECONDS = 30.0


@dataclass
class TrackLevels:
    sound: float        # share of 10 ms frames that are not digital silence
    range_db: float     # p95 - p50 of the frames with sound
    loud_db: float      # p95


@dataclass
class Choice:
    stream: AudioStream | None
    how: str            # "name", "index", "common name", "content", "only track", ...


def _has_word(title: str, names: list[str]) -> bool:
    t = title.lower().replace("_", " ")
    return any(re.search(r"(?<![a-z0-9])" + re.escape(n) + r"(?![a-z0-9])", t) for n in names)


def kind_by_name(title: str) -> str | None:
    """'party', 'mix', 'mic', 'game' or None (no or a generic name). Voice chat and mix names win,
    so "Voice Chat" is not taken for the mic and "Stream Mix" not for a game track."""
    if not title or GENERIC.match(title.strip()):
        return None
    if title.lower().startswith("simple_aac_recording"):
        return "mix"
    for kind, names in (("party", PARTY_NAMES), ("mix", MIX_NAMES), ("mic", MIC_NAMES), ("game", GAME_NAMES)):
        if _has_word(title, names):
            return kind
    return None


def levels_from_db(db: np.ndarray) -> TrackLevels:
    sound = db[db > SILENT_DB]
    share = len(sound) / max(1, len(db))
    if len(sound) < 100:
        return TrackLevels(share, 0.0, float(db.max()) if len(db) else SILENT_DB)
    return TrackLevels(share, float(np.percentile(sound, 95) - np.percentile(sound, 50)), float(np.percentile(db, 95)))


def measure(info: MediaInfo, streams: list[AudioStream], windows: int = SAMPLE_WINDOWS,
            seconds: float = SAMPLE_SECONDS) -> dict[int, TrackLevels]:
    """Levels per audio stream (key: stream index) from a few windows spread over the recording.
    One FFmpeg call per window decodes all tracks at once."""
    sr, frame = 16000, 160
    if info.duration <= windows * seconds:  # short recording: measure all of it
        times, seconds = [0.0], max(1.0, info.duration)
    else:
        times = [info.duration * (0.05 + 0.9 * (k + 0.5) / windows) for k in range(windows)]
    db: dict[int, list[np.ndarray]] = {s.index: [] for s in streams}
    with tempfile.TemporaryDirectory(prefix="nightcut_tracks_") as tmp:
        for k, t in enumerate(times):
            cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-ss", f"{t:.2f}", "-t", f"{seconds:.2f}",
                   "-i", str(info.path)]
            outs = {}
            for s in streams:
                outs[s.index] = Path(tmp) / f"{k}_{s.index}.pcm"
                cmd += ["-map", f"0:{s.index}", "-ac", "1", "-ar", str(sr), "-f", "s16le", str(outs[s.index])]
            try:
                run(cmd, capture_output=True)
            except subprocess.CalledProcessError:
                continue
            for idx, path in outs.items():
                x = np.fromfile(path, np.int16).astype(np.float32) / 32768.0 if path.exists() else np.zeros(0)
                n = len(x) // frame
                if n:
                    rms = np.sqrt((x[:n * frame].reshape(n, frame) ** 2).mean(axis=1) + 1e-12)
                    db[idx].append(20 * np.log10(rms))
    return {idx: levels_from_db(np.concatenate(parts) if parts else np.full(1, -120.0)) for idx, parts in db.items()}


def best_mic(candidates: list[AudioStream], levels: dict[int, TrackLevels]) -> AudioStream | None:
    """The track with sound most of the time and the biggest level range."""
    alive = [s for s in candidates if levels[s.index].range_db > 0 and levels[s.index].loud_db > -70]
    steady = [s for s in alive if levels[s.index].sound >= MIN_SOUND] or alive
    return max(steady, key=lambda s: levels[s.index].range_db, default=None)


def _user_title(info: MediaInfo, spec: dict) -> AudioStream | None:
    return info.find_audio({"title": spec.get("title"), "index": None})


def _fixed_index(info: MediaInfo, spec: dict) -> AudioStream | None:
    pos = spec.get("index")
    if isinstance(pos, int) and not isinstance(pos, bool) and 0 <= pos < len(info.audio):
        return info.audio[pos]
    return None


def pick_streams(info: MediaInfo, audio_cfg: dict, log=None) -> dict[str, Choice]:
    """Choose the mic, game and party streams. `mic` is always set when the file has audio."""
    specs = {name: audio_cfg.get(name) for name in ("mic", "game", "party")}
    chosen: dict[str, Choice] = {}
    taken: set[int] = set()
    levels: dict[int, TrackLevels] | None = None

    def get_levels() -> dict[int, TrackLevels]:
        nonlocal levels
        if levels is None:
            levels = measure(info, info.audio)
        return levels

    def take(name: str, stream: AudioStream | None, how: str) -> bool:
        if stream is None or stream.index in taken:
            return False
        chosen[name] = Choice(stream, how)
        taken.add(stream.index)
        return True

    kinds = {s.index: kind_by_name(s.title) for s in info.audio}

    # 1 + 2: what the config says
    for name, spec in specs.items():
        if spec and not take(name, _user_title(info, spec), "name"):
            take(name, _fixed_index(info, spec), "index")

    # 3: common names
    for name in ("mic", "party", "game"):
        if name in chosen or (name != "mic" and not specs[name]):
            continue  # game: null / party: null in the config = not used
        named = [s for s in info.audio if kinds[s.index] == name and s.index not in taken]
        if len(named) == 1:
            take(name, named[0], "common name")
        elif len(named) > 1:
            pick = best_mic(named, get_levels()) if name == "mic" else named[0]
            take(name, pick or named[0], "common name")

    # 4: the mic by its sound
    if "mic" not in chosen and info.audio:
        free = [s for s in info.audio if s.index not in taken]
        unnamed = [s for s in free if kinds[s.index] in (None, "mic")]
        if len(info.audio) == 1:
            take("mic", info.audio[0], "only track")
        else:
            lv = get_levels()
            pick = best_mic(unnamed, lv) or best_mic([s for s in free if kinds[s.index] != "party"], lv)
            if pick is not None:
                take("mic", pick, "content")
            elif free:
                take("mic", free[0], "first track")

    # game without a name: only if exactly one unnamed track with sound is left. With several
    # (mix, voice chat, game) the sound alone cannot tell them apart; then highlights work without it.
    if ("game" not in chosen and specs["game"] and "mic" in chosen and len(info.audio) > 1
            and any(kinds[s.index] is None and s.index not in taken for s in info.audio)):
        lv = get_levels()
        rest = [s for s in info.audio if s.index not in taken and kinds[s.index] is None
                and lv[s.index].sound >= MIN_SOUND and lv[s.index].loud_db > -70]
        if len(rest) == 1:
            take("game", rest[0], "content")

    if log:
        log(describe(chosen, levels))
    return chosen


def describe(chosen: dict[str, Choice], levels: dict[int, TrackLevels] | None = None) -> str:
    how_text = {"name": "by its name", "index": "by its position in the config", "common name": "by a common name",
                "content": "by its sound", "only track": "the only track (a mix)", "first track": "first track, unsure"}
    parts = []
    for name in ("mic", "game", "party"):
        c = chosen.get(name)
        if c and c.stream:
            extra = ""
            if levels and c.how == "content" and c.stream.index in levels:
                extra = f", level range {levels[c.stream.index].range_db:.0f} dB"
            parts.append(f"{name} = track {c.stream.position + 1} '{c.stream.title}' ({how_text.get(c.how, c.how)}{extra})")
    return "Audio: " + "; ".join(parts) if parts else "Audio: no usable track found"


def streams_of(chosen: dict[str, Choice]) -> dict[str, AudioStream]:
    return {name: c.stream for name, c in chosen.items() if c.stream is not None}
