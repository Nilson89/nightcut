"""Pick the mic, game and voice chat tracks of a recording, also when they are named differently.

Order for the mic:
1. the names from the config (`audio.mic.title` ...), as before
2. a fixed `index` from the config, if one is set (`index: 2`)
3. your earlier answer for recordings with the same tracks (config/known_tracks.yaml)
4. common names ("Mikrofon", "Voice", "Headset" ...)
5. the level: sample windows of every track are measured. The mic is loud while you talk and
   nearly quiet in between, so it has the biggest level range. Game sound and a mix (everything
   together) never get that quiet. Only taken when one track is clearly ahead.
6. unsure: Whisper listens to the samples of the candidate tracks and finds where someone talks.
7. still unsure: a 10 s sample of every candidate track is written to a folder, you listen and
   type the number of the one with your voice. The answer is remembered (see 3).
   Without a terminal (scheduled run) nightcut takes its best guess and says so.
A single track is used as a full mix. Game and voice chat are found by names only; by sound
they cannot be told apart from a mix.

OBS default names like "Track1" or "Audio 2" count as no name.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml

from .media import AudioStream, MediaInfo, run

PARTY_NAMES = ["party", "discord", "teamspeak", "ts", "ts3", "voice chat", "voicechat", "voice-chat", "chat",
               "mumble", "guilded", "call", "sprachchat"]
MIX_NAMES = ["mix", "mixed", "stream", "master", "gesamt", "aufnahme", "recording", "all", "alles", "simple_aac_recording"]
MIC_NAMES = ["mic", "mics", "mikro", "mikrofon", "microphone", "micro", "voice", "stimme", "headset", "vocals",
             "kommentar", "commentary", "sprache", "talk"]
GAME_NAMES = ["game", "spiel", "gameplay", "ingame", "game audio", "spielsound", "desktop", "desktop audio",
              "desktop-audio", "system", "systemsound"]
GENERIC = re.compile(r"^(track|audio|spur|tonspur|channel|kanal)?\s*[_ -]?\s*\d*$", re.IGNORECASE)

KNOWN_TRACKS = Path(os.path.abspath(__file__)).parents[2] / "config" / "known_tracks.yaml"

SR = 16000
SILENT_DB = -100.0      # below this a 10 ms frame is digital silence
MIN_SOUND = 0.5         # an ungated mic has sound (at least room noise) most of the time
CLEAR_DB = 10.0         # the mic's level range must be this far ahead of every other track
MIN_SPEECH_S = 3.0      # seconds of real sentences in the samples for a track to count as "someone talks"
SAMPLE_WINDOWS = 8
SAMPLE_SECONDS = 30.0
LISTEN_SECONDS = 10.0


@dataclass
class TrackLevels:
    sound: float        # share of 10 ms frames that are not digital silence
    range_db: float     # p95 - p50 of the frames with sound
    loud_db: float      # p95


@dataclass
class Sample:
    """Levels and the sampled audio (16 kHz mono, windows back to back) per stream index."""
    levels: dict[int, TrackLevels]
    pcm: dict[int, np.ndarray] = field(default_factory=dict)
    windows: list[tuple[float, float]] = field(default_factory=list)  # (start in recording, length)

    def to_recording(self, t: float) -> float:
        """Time in the sample -> time in the recording."""
        for start, length in self.windows:
            if t < length:
                return start + t
            t -= length
        return self.windows[-1][0] if self.windows else 0.0


@dataclass
class Choice:
    stream: AudioStream | None
    how: str            # "name", "index", "answer", "common name", "level", "speech", "only track", "guess" ...
    note: str = ""


# ---------------------------------------------------------------- names

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


# ---------------------------------------------------------------- measuring

def levels_from_db(db: np.ndarray) -> TrackLevels:
    sound = db[db > SILENT_DB]
    share = len(sound) / max(1, len(db))
    if len(sound) < 100:
        return TrackLevels(share, 0.0, float(db.max()) if len(db) else SILENT_DB)
    # loudness only over the frames with sound: a gated mic of someone who talks little is silent
    # 90 % of the time, but when it has sound, it is as loud as any mic
    p95 = float(np.percentile(sound, 95))
    return TrackLevels(share, p95 - float(np.percentile(sound, 50)), p95)


def _db(pcm: np.ndarray, frame: int = 160) -> np.ndarray:
    n = len(pcm) // frame
    if not n:
        return np.full(1, -120.0)
    x = pcm[:n * frame].astype(np.float32).reshape(n, frame) / 32768.0
    return 20 * np.log10(np.sqrt((x * x).mean(axis=1) + 1e-12))


def measure(info: MediaInfo, streams: list[AudioStream], windows: int = SAMPLE_WINDOWS,
            seconds: float = SAMPLE_SECONDS) -> Sample:
    """Sample a few windows spread over the recording (one FFmpeg call per window for all tracks)."""
    if info.duration <= windows * seconds:  # short recording: take all of it
        times, seconds = [0.0], max(1.0, info.duration)
    else:
        times = [info.duration * (0.05 + 0.9 * (k + 0.5) / windows) for k in range(windows)]
    parts: dict[int, list[np.ndarray]] = {s.index: [] for s in streams}
    spans = []
    with tempfile.TemporaryDirectory(prefix="nightcut_tracks_") as tmp:
        for k, t in enumerate(times):
            cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-ss", f"{t:.2f}", "-t", f"{seconds:.2f}",
                   "-i", str(info.path)]
            outs = {}
            for s in streams:
                outs[s.index] = Path(tmp) / f"{k}_{s.index}.pcm"
                cmd += ["-map", f"0:{s.index}", "-ac", "1", "-ar", str(SR), "-f", "s16le", str(outs[s.index])]
            try:
                run(cmd, capture_output=True)
            except subprocess.CalledProcessError:
                continue
            chunks = {idx: np.fromfile(p, np.int16) if p.exists() else np.zeros(0, np.int16) for idx, p in outs.items()}
            n = min(len(c) for c in chunks.values()) if chunks else 0
            if not n:
                continue
            for idx, c in chunks.items():
                parts[idx].append(c[:n])
            spans.append((t, n / SR))
    pcm = {idx: np.concatenate(p) if p else np.zeros(0, np.int16) for idx, p in parts.items()}
    return Sample({idx: levels_from_db(_db(p)) for idx, p in pcm.items()}, pcm, spans)


def _alive(streams: list[AudioStream], lv: dict[int, TrackLevels]) -> list[AudioStream]:
    return [s for s in streams if lv[s.index].range_db > 0 and lv[s.index].loud_db > -70]


def best_mic(candidates: list[AudioStream], levels: dict[int, TrackLevels]) -> AudioStream | None:
    """The track with sound most of the time and the biggest level range (best guess)."""
    alive = _alive(candidates, levels)
    steady = [s for s in alive if levels[s.index].sound >= MIN_SOUND] or alive
    return max(steady, key=lambda s: levels[s.index].range_db, default=None)


def clear_mic(candidates: list[AudioStream], levels: dict[int, TrackLevels], margin: float = CLEAR_DB) -> AudioStream | None:
    """The mic by level only if there is no doubt: it has sound most of the time and its level range
    is `margin` dB ahead of every other track with sound, also of mostly silent ones (a gated mic of
    someone who talks little looks like that, so it must not simply be passed over)."""
    alive = _alive(candidates, levels)
    steady = [s for s in alive if levels[s.index].sound >= MIN_SOUND]
    if not steady:
        return None
    best = max(steady, key=lambda s: levels[s.index].range_db)
    if all(levels[best.index].range_db - levels[o.index].range_db >= margin for o in alive if o is not best):
        return best
    return None


# ---------------------------------------------------------------- speech check (Whisper)

def speech_per_track(sample: Sample, candidates: list[AudioStream], tcfg: dict,
                     max_seconds: float = 120.0) -> dict[int, tuple[float, float | None]]:
    """Seconds of real speech per track in the sample, and the middle of its longest sentence
    (sample time). One Whisper run for all candidate tracks: their samples are put back to back."""
    from . import segments, signals
    from .transcribe import transcribe_regions

    pcm_all, regions, owner, offset = [], [], [], 0.0
    for s in candidates:
        pcm = sample.pcm.get(s.index)
        if pcm is None or not len(pcm):
            continue
        env = signals.envelope_db(pcm, SR)
        found = signals.voice_regions(env, tcfg.get("voice_margin_db", 12), tcfg.get("max_chunk_s", 28))
        total = 0.0
        for a, b in found:
            if total >= max_seconds:
                break
            regions.append((offset + a, offset + min(b, len(pcm) / SR)))
            owner.append((s.index, offset))
            total += b - a
        pcm_all.append(pcm)
        offset += len(pcm) / SR
    secs = {s.index: 0.0 for s in candidates}
    longest: dict[int, tuple[float, float | None]] = {s.index: (0.0, None) for s in candidates}
    if regions:
        segs = segments.real_speech(transcribe_regions(np.concatenate(pcm_all), SR, regions, tcfg))
        for seg in segs:
            for (a, b), (idx, off) in zip(regions, owner):
                if a <= seg["start"] < b:
                    length = seg["end"] - seg["start"]
                    secs[idx] += length
                    if length > longest[idx][0]:
                        longest[idx] = (length, (seg["start"] + seg["end"]) / 2 - off)
                    break
    return {idx: (secs[idx], longest[idx][1]) for idx in secs}


def mic_by_speech(candidates: list[AudioStream], levels: dict[int, TrackLevels],
                  speech: dict[int, tuple[float, float | None]], margin: float = CLEAR_DB) -> AudioStream | None:
    """Only one track has real speech: that is the mic. Several (mic, mix, voice chat): the one
    whose level range is clearly ahead. Otherwise None (ask).

    A fixed minimum, not a share of the most talkative track: the mix holds every voice, so
    it always has at least as much speech as the mic of someone who talks little."""
    talkers = [s for s in candidates if speech.get(s.index, (0.0, None))[0] >= MIN_SPEECH_S]
    if len(talkers) == 1:
        return talkers[0]
    if len(talkers) > 1:
        best = max(talkers, key=lambda s: levels[s.index].range_db)
        if all(levels[best.index].range_db - levels[o.index].range_db >= margin for o in talkers if o is not best):
            return best
    return None


# ---------------------------------------------------------------- asking you

def listen_files(info: MediaInfo, sample: Sample, candidates: list[AudioStream],
                 speech: dict[int, tuple[float, float | None]], folder: Path) -> list[tuple[AudioStream, Path]]:
    """A 10 s piece of every candidate track, from where it talks (or is loudest)."""
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for s in candidates:
        mid = speech.get(s.index, (0.0, None))[1]
        if mid is None:  # no speech found: the loudest second of the sample
            db = _db(sample.pcm.get(s.index, np.zeros(0, np.int16)), SR)
            mid = float(np.argmax(db)) + 0.5 if len(db) > 1 else 0.0
        t = max(0.0, min(sample.to_recording(mid) - LISTEN_SECONDS / 2, info.duration - LISTEN_SECONDS))
        path = folder / f"track{s.position + 1}.m4a"
        run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-ss", f"{t:.2f}", "-t", f"{LISTEN_SECONDS}",
             "-i", str(info.path), "-map", f"0:{s.index}", "-c:a", "aac", "-b:a", "160k", str(path)],
            capture_output=True)
        out.append((s, path))
    return out


def open_folder(folder: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(folder)  # noqa: S606 - opens Explorer
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(folder)])
    except OSError:
        pass


def terminal_ask(options: list[tuple[AudioStream, Path]], recording: str) -> AudioStream | None:
    """Ask in the terminal which file has your voice. Enter = let nightcut guess."""
    folder = options[0][1].parent
    print(f"\nWhich audio track of {recording} is your microphone? nightcut is not sure.")
    print(f"Listen to these short pieces (folder: {folder}):")
    for n, (s, p) in enumerate(options, 1):
        print(f"  {n}: {p.name}   (track {s.position + 1} '{s.title}')")
    while True:
        try:
            answer = input("Number of the file with YOUR voice (o = open folder, Enter = let nightcut guess): ").strip().lower()
        except EOFError:
            return None
        if answer == "o":
            open_folder(folder)
        elif not answer:
            return None
        elif answer.isdigit() and 1 <= int(answer) <= len(options):
            return options[int(answer) - 1][0]


def signature(info: MediaInfo) -> str:
    return f"{len(info.audio)} tracks: " + " | ".join(s.title for s in info.audio)


def known_mic(info: MediaInfo, path: Path = KNOWN_TRACKS) -> AudioStream | None:
    try:
        known = yaml.safe_load(path.read_text(encoding="utf-8")) or {} if path.exists() else {}
    except (OSError, yaml.YAMLError):
        return None
    pos = known.get(signature(info))
    return info.audio[pos - 1] if isinstance(pos, int) and 1 <= pos <= len(info.audio) else None


def remember_mic(info: MediaInfo, stream: AudioStream, path: Path = KNOWN_TRACKS) -> None:
    known = {}
    if path.exists():
        try:
            known = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            known = {}
    known[signature(info)] = stream.position + 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Which track is your mic, answered by you (track numbers from 1).\n"
                    "# Delete a line to be asked again, or run: nightcut tracks <recording>\n"
                    + yaml.safe_dump(known, allow_unicode=True, sort_keys=True), encoding="utf-8")


# ---------------------------------------------------------------- choosing

def _user_title(info: MediaInfo, spec: dict) -> AudioStream | None:
    return info.find_audio({"title": spec.get("title"), "index": None})


def _fixed_index(info: MediaInfo, spec: dict) -> AudioStream | None:
    pos = spec.get("index")
    if isinstance(pos, int) and not isinstance(pos, bool) and 0 <= pos < len(info.audio):
        return info.audio[pos]
    return None


def pick_streams(info: MediaInfo, cfg: dict, log=None, ask="auto", folder: Path | None = None,
                 force_ask: bool = False, known_file: Path | None = None,
                 sample: Sample | None = None) -> dict[str, Choice]:
    """Choose the mic, game and party streams. `mic` is always set when the file has audio.

    ask: "auto" = in the terminal if there is one, None = never ask, or a function
         (options, recording name) -> stream | None.
    folder: where the listening pieces go (default: a temporary folder).
    force_ask: always ask about the mic (`nightcut tracks`).
    sample: an existing measurement of all tracks (else measured when needed)."""
    known_file = known_file or KNOWN_TRACKS
    audio_cfg = cfg.get("audio", cfg)
    tcfg = cfg.get("transcribe") or {}
    if ask == "auto":
        ask = terminal_ask if sys.stdin.isatty() else None
    specs = {name: audio_cfg.get(name) for name in ("mic", "game", "party")}
    chosen: dict[str, Choice] = {}
    taken: set[int] = set()

    def get_sample() -> Sample:
        nonlocal sample
        if sample is None:
            sample = measure(info, info.audio)
        return sample

    def take(name: str, stream: AudioStream | None, how: str, note: str = "") -> bool:
        if stream is None or stream.index in taken:
            return False
        chosen[name] = Choice(stream, how, note)
        taken.add(stream.index)
        return True

    kinds = {s.index: kind_by_name(s.title) for s in info.audio}

    # 1 + 2: what the config says
    for name, spec in specs.items():
        if name == "mic" and force_ask:
            continue
        if spec and not take(name, _user_title(info, spec), "name"):
            take(name, _fixed_index(info, spec), "index")

    # 3: your earlier answer
    if "mic" not in chosen and not force_ask:
        take("mic", known_mic(info, known_file), "answer")

    # 4: common names
    for name in ("mic", "party", "game"):
        if name in chosen or (name != "mic" and not specs[name]) or (name == "mic" and force_ask):
            continue  # game: null / party: null in the config = not used
        named = [s for s in info.audio if kinds[s.index] == name and s.index not in taken]
        if len(named) == 1:
            take(name, named[0], "common name")
        elif len(named) > 1:
            pick = (clear_mic(named, get_sample().levels) if name == "mic" else named[0])
            if pick is not None:
                take(name, pick, "common name")

    # 5-7: the mic by level, speech, or your answer
    if "mic" not in chosen and info.audio:
        if len(info.audio) == 1 and not force_ask:
            take("mic", info.audio[0], "only track")
        else:
            _mic_by_sound(info, get_sample(), kinds, taken, take, tcfg, ask, folder, force_ask, known_file, log)

    # game without a name: only if exactly one unnamed track with sound is left. With several
    # (mix, voice chat, game) the sound alone cannot tell them apart; then highlights work without it.
    if ("game" not in chosen and specs["game"] and "mic" in chosen and len(info.audio) > 1
            and any(kinds[s.index] is None and s.index not in taken for s in info.audio)):
        lv = get_sample().levels
        rest = [s for s in info.audio if s.index not in taken and kinds[s.index] is None
                and lv[s.index].sound >= MIN_SOUND and lv[s.index].loud_db > -70]
        if len(rest) == 1:
            take("game", rest[0], "level")

    if log:
        log(describe(chosen, sample.levels if sample else None))
        if chosen.get("mic") and chosen["mic"].how == "guess":
            log(f"[yellow]Not sure which track is your mic. Check it once with: nightcut tracks \"{info.path}\"[/yellow]")
    return chosen


def _mic_by_sound(info, sample, kinds, taken, take, tcfg, ask, folder, force_ask, known_file, log) -> None:
    lv = sample.levels
    free = [s for s in info.audio if s.index not in taken and kinds[s.index] not in ("party",)]
    candidates = _alive([s for s in free if kinds[s.index] in (None, "mic")], lv) or _alive(free, lv) or free
    if not candidates:
        return
    if len(candidates) == 1 and not force_ask:
        take("mic", candidates[0], "level")
        return
    pick = None if force_ask else clear_mic(candidates, lv)
    if pick is not None:
        take("mic", pick, "level")
        return

    speech: dict[int, tuple[float, float | None]] = {}
    if tcfg.get("model") and len(candidates) > 1:
        if log:
            log("Not clear from the levels which track is the mic: listening for speech with Whisper ...")
        try:
            speech = speech_per_track(sample, candidates, tcfg)
        except Exception as exc:  # noqa: BLE001 - without Whisper we can still ask or guess
            if log:
                log(f"[yellow]Speech check failed ({exc}).[/yellow]")
        pick = None if force_ask else mic_by_speech(candidates, lv, speech)
        if pick is not None:
            take("mic", pick, "speech", f"{speech[pick.index][0]:.0f} s of speech in the samples")
            return

    if ask:
        own_folder = folder or Path(tempfile.mkdtemp(prefix="nightcut_listen_"))
        options = listen_files(info, sample, candidates, speech, own_folder)
        answer = ask(options, info.path.name)
        if answer is not None:
            take("mic", answer, "answer")
            remember_mic(info, answer, known_file)
            return
    # best guess: of the tracks where someone talks, the clearest voice (biggest level range);
    # without speech results, the level rule
    talkers = [s for s in candidates if speech.get(s.index, (0.0, None))[0] >= MIN_SPEECH_S]
    guess = max(talkers, key=lambda s: lv[s.index].range_db) if talkers else best_mic(candidates, lv)
    take("mic", guess or candidates[0], "guess")


def describe(chosen: dict[str, Choice], levels: dict[int, TrackLevels] | None = None) -> str:
    how_text = {"name": "by its name", "index": "by its position in the config", "common name": "by a common name",
                "answer": "your answer", "level": "by its level", "speech": "Whisper heard speech, clearest voice",
                "only track": "the only track (a mix)", "guess": "best guess, unsure"}
    parts = []
    for name in ("mic", "game", "party"):
        c = chosen.get(name)
        if c and c.stream:
            extra = f", {c.note}" if c.note else ""
            if levels and c.how == "level" and c.stream.index in levels:
                extra += f", level range {levels[c.stream.index].range_db:.0f} dB"
            parts.append(f"{name} = track {c.stream.position + 1} '{c.stream.title}' ({how_text.get(c.how, c.how)}{extra})")
    return "Audio: " + "; ".join(parts) if parts else "Audio: no usable track found"


def streams_of(chosen: dict[str, Choice]) -> dict[str, AudioStream]:
    return {name: c.stream for name, c in chosen.items() if c.stream is not None}
