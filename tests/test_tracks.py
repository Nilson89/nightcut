from pathlib import Path

import numpy as np
import pytest

from nightcut import tracks
from nightcut.media import AudioStream, MediaInfo
from nightcut.tracks import TrackLevels, kind_by_name

DEFAULT_AUDIO = {
    "mic": {"title": "MIC", "index": "auto"},
    "game": {"title": "GAME", "index": "auto"},
    "party": {"title": ["PARTY", "DISCORD", "TEAMSPEAK"], "index": "auto"},
}

MIC = TrackLevels(sound=0.97, range_db=45.0, loud_db=-30.0)       # quiet between the words
MIX = TrackLevels(sound=1.0, range_db=20.0, loud_db=-25.0)        # everything together
GAME = TrackLevels(sound=1.0, range_db=8.0, loud_db=-50.0)
CHAT = TrackLevels(sound=0.9, range_db=21.0, loud_db=-37.0)
SPARSE = TrackLevels(sound=0.17, range_db=70.0, loud_db=-48.0)    # digital silence most of the time
SILENT = TrackLevels(sound=0.0, range_db=0.0, loud_db=-120.0)


def info(*titles: str) -> MediaInfo:
    return MediaInfo(Path("x.mkv"), 3600.0, 1920, 1080, 60.0,
                     [AudioStream(i + 1, i, t, 2, 48000) for i, t in enumerate(titles)])


@pytest.fixture(autouse=True)
def own_memory(tmp_path, monkeypatch):
    """Never read or write the real config/known_tracks.yaml in tests."""
    monkeypatch.setattr(tracks, "KNOWN_TRACKS", tmp_path / "known_tracks.yaml")
    return tmp_path / "known_tracks.yaml"


def fake_levels(monkeypatch, by_position: list[TrackLevels], speech: dict[int, float] | None = None):
    """Fake measuring (levels by track position) and, optionally, the Whisper check (seconds of speech)."""
    calls = []

    def measure(inf, streams, *a, **k):
        calls.append(1)
        return tracks.Sample({s.index: by_position[s.position] for s in inf.audio})
    monkeypatch.setattr(tracks, "measure", measure)
    if speech is not None:
        def speech_per_track(sample, cands, tcfg, **k):
            calls.append("whisper")
            return {s.index: (speech.get(s.position, 0.0), 5.0) for s in cands}
        monkeypatch.setattr(tracks, "speech_per_track", speech_per_track)
        monkeypatch.setattr(tracks, "listen_files", lambda inf, sample, cands, sp, folder: [(s, folder / f"track{s.position + 1}.m4a") for s in cands])
    return calls


def picked(chosen, name):
    c = chosen.get(name)
    return (c.stream.position, c.how) if c else None


WHISPER = {"audio": DEFAULT_AUDIO, "transcribe": {"model": "whisper-test"}}


@pytest.mark.parametrize("title, kind", [
    ("MIC Main Headset", "mic"), ("Mikrofon", "mic"), ("Voice", "mic"), ("Stimme", "mic"), ("Headset Mic", "mic"),
    ("Voice Chat", "party"), ("Discord", "party"), ("DISCORD, TS und TIPEESTREAM", "party"), ("TeamSpeak 3", "party"),
    ("Spiel", "game"), ("Game Audio", "game"), ("DESKTOP", "game"),
    ("STREAM", "mix"), ("Mix", "mix"), ("simple_aac_recording0", "mix"),
    ("Track1", None), ("Audio 2", None), ("audio0", None), ("", None), ("Spur 3", None),
])
def test_kind_by_name(title, kind):
    assert kind_by_name(title) == kind


def test_named_obs_tracks_need_no_measuring(monkeypatch):
    calls = fake_levels(monkeypatch, [])
    ch = tracks.pick_streams(info("GAME", "MIC Main Headset", "MIC Tonor", "DESKTOP", "DISCORD, TS", "STREAM"),
                             DEFAULT_AUDIO)
    assert picked(ch, "mic") == (1, "name") and picked(ch, "game") == (0, "name") and picked(ch, "party") == (4, "name")
    assert not calls


def test_other_names_are_found(monkeypatch):
    fake_levels(monkeypatch, [MIX, GAME, MIC, CHAT])
    ch = tracks.pick_streams(info("Alles", "Spiel", "Mikrofon", "Voice Chat"), DEFAULT_AUDIO)
    assert picked(ch, "mic") == (2, "common name")
    assert picked(ch, "game") == (1, "common name")
    assert picked(ch, "party") == (3, "common name")


def test_unnamed_tracks_mic_by_sound(monkeypatch):
    # like the Minecraft recording: mix, voice chat, mic, game
    fake_levels(monkeypatch, [MIX, CHAT, MIC, GAME])
    ch = tracks.pick_streams(info("Track1", "Track2", "Track3", "Track4"), DEFAULT_AUDIO)
    assert picked(ch, "mic") == (2, "level")
    assert "game" not in ch  # three other tracks with sound: no guessing


def test_mostly_silent_track_is_not_the_mic(monkeypatch):
    # like the Sushi recording without names: the game track is digital silence most of the time
    fake_levels(monkeypatch, [SPARSE, MIC, GAME, SILENT, SILENT, MIX])
    ch = tracks.pick_streams(info(*(f"Track{i}" for i in range(1, 7))), DEFAULT_AUDIO, ask=None)
    assert picked(ch, "mic") == (1, "guess")  # not clear (the sparse track has a bigger range), no Whisper, no terminal


def test_two_tracks_mix_and_mic(monkeypatch):
    fake_levels(monkeypatch, [MIX, MIC])
    ch = tracks.pick_streams(info("Track1", "Track2"), DEFAULT_AUDIO)
    assert picked(ch, "mic") == (1, "level")
    assert picked(ch, "game") == (0, "level")  # the only other track: game sound (or the mix)


def test_single_track_is_used_as_mix(monkeypatch):
    calls = fake_levels(monkeypatch, [MIX])
    ch = tracks.pick_streams(info("simple_aac_recording0"), DEFAULT_AUDIO)
    assert picked(ch, "mic") == (0, "only track")
    assert not calls


def test_fixed_index_from_config_wins(monkeypatch):
    calls = fake_levels(monkeypatch, [MIX, CHAT, MIC, GAME])
    audio = {"mic": {"title": None, "index": 1}, "game": {"title": None, "index": 3}, "party": None}
    ch = tracks.pick_streams(info("Track1", "Track2", "Track3", "Track4"), audio)
    assert picked(ch, "mic") == (1, "index") and picked(ch, "game") == (3, "index") and "party" not in ch
    assert not calls


def test_several_mic_names_take_the_one_in_use(monkeypatch):
    fake_levels(monkeypatch, [GAME, TrackLevels(1.0, 8.6, -45.0), MIC])
    ch = tracks.pick_streams(info("Game", "Mic Tonor", "Mic Headset"), {**DEFAULT_AUDIO, "mic": {"title": None, "index": "auto"}})
    assert picked(ch, "mic") == (2, "common name")


def test_levels_from_db():
    db = np.concatenate([np.full(500, -120.0), np.full(400, -60.0), np.full(100, -20.0)])
    lv = tracks.levels_from_db(db)
    assert lv.sound == 0.5 and lv.range_db == pytest.approx(40.0)


def test_describe_names_track_and_reason(monkeypatch):
    fake_levels(monkeypatch, [MIX, MIC])
    text = tracks.describe(tracks.pick_streams(info("Track1", "Track2"), DEFAULT_AUDIO))
    assert "mic = track 2 'Track2' (by its level" in text


QUIET_GATED_MIC = TrackLevels(sound=0.2, range_db=50.0, loud_db=-35.0)  # noise gate, little talking


def test_quiet_gated_mic_found_by_speech(monkeypatch):
    """Game sound is there all the time; a gated mic of someone who talks little is mostly silent.
    The levels are not clear, Whisper finds speech on mic and mix, the mic has the bigger range."""
    calls = fake_levels(monkeypatch, [MIX, GAME, QUIET_GATED_MIC], speech={0: 25.0, 2: 20.0})
    ch = tracks.pick_streams(info("Track1", "Track2", "Track3"), WHISPER, ask=None)
    assert picked(ch, "mic") == (2, "speech")
    assert "whisper" in calls


def test_only_one_track_with_speech(monkeypatch):
    fake_levels(monkeypatch, [MIX, CHAT, GAME], speech={1: 30.0, 0: 1.0})
    ch = tracks.pick_streams(info("Track1", "Track2", "Track3"), WHISPER, ask=None)
    assert picked(ch, "mic") == (1, "speech")


def test_still_unsure_asks_and_remembers(monkeypatch, tmp_path, own_memory):
    # mic and voice chat both talk and look alike: ask
    fake_levels(monkeypatch, [MIX, CHAT, TrackLevels(0.9, 24.0, -36.0)], speech={1: 30.0, 2: 28.0})
    asked = []

    def ask(options, name):
        asked.append([p.name for _, p in options])
        return options[-1][0]
    rec = info("Track1", "Track2", "Track3")
    ch = tracks.pick_streams(rec, WHISPER, ask=ask, folder=tmp_path / "listen")
    assert picked(ch, "mic") == (2, "answer")
    assert asked and "track3.m4a" in asked[0]
    import yaml
    assert yaml.safe_load(own_memory.read_text(encoding="utf-8")) == {"3 tracks: Track1 | Track2 | Track3": 3}

    # next recording with the same tracks: no measuring, no question
    calls = fake_levels(monkeypatch, [MIX, CHAT, MIC])
    ch = tracks.pick_streams(info("Track1", "Track2", "Track3"), WHISPER, ask=lambda *a: pytest.fail("asked again"))
    assert picked(ch, "mic") == (2, "answer") and "whisper" not in calls


def test_no_terminal_takes_best_guess(monkeypatch):
    fake_levels(monkeypatch, [MIX, CHAT, TrackLevels(0.9, 24.0, -36.0)], speech={1: 30.0, 2: 28.0})
    lines = []
    ch = tracks.pick_streams(info("Track1", "Track2", "Track3"), WHISPER, ask=None, log=lines.append)
    assert picked(ch, "mic") == (2, "guess")
    assert any("nightcut tracks" in line for line in lines)


def test_enter_means_guess(monkeypatch, tmp_path, own_memory):
    fake_levels(monkeypatch, [MIX, CHAT, TrackLevels(0.9, 24.0, -36.0)], speech={1: 30.0, 2: 28.0})
    ch = tracks.pick_streams(info("Track1", "Track2", "Track3"), WHISPER, ask=lambda *a: None, folder=tmp_path)
    assert picked(ch, "mic")[1] == "guess" and not own_memory.exists()


def test_force_ask_ignores_names(monkeypatch, tmp_path):
    fake_levels(monkeypatch, [GAME, MIC], speech={1: 30.0})
    ch = tracks.pick_streams(info("GAME", "MIC"), WHISPER, ask=lambda options, name: options[0][0],
                             folder=tmp_path, force_ask=True)
    assert picked(ch, "mic")[1] == "answer"


def test_sample_time_back_to_recording():
    s = tracks.Sample({}, {}, [(100.0, 30.0), (500.0, 30.0)])
    assert s.to_recording(10.0) == 110.0 and s.to_recording(35.0) == 505.0


def test_guess_prefers_the_clearest_voice_over_steady_sound(monkeypatch, tmp_path):
    """Enter pressed (or no terminal): a gated quiet mic with speech beats the steady mix."""
    fake_levels(monkeypatch, [MIX, GAME, QUIET_GATED_MIC, CHAT], speech={0: 80.0, 2: 10.0, 3: 30.0})
    ch = tracks.pick_streams(info("Track1", "Track2", "Track3", "Track4"), WHISPER, ask=lambda *a: None,
                             folder=tmp_path, force_ask=True)
    assert picked(ch, "mic") == (2, "guess")
