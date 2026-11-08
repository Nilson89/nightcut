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


def fake_levels(monkeypatch, by_position: list[TrackLevels]):
    calls = []

    def measure(inf, streams, *a, **k):
        calls.append(1)
        return {s.index: by_position[s.position] for s in inf.audio}
    monkeypatch.setattr(tracks, "measure", measure)
    return calls


def picked(chosen, name):
    c = chosen.get(name)
    return (c.stream.position, c.how) if c else None


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
    assert picked(ch, "mic") == (2, "content")
    assert "game" not in ch  # three other tracks with sound: no guessing


def test_mostly_silent_track_is_not_the_mic(monkeypatch):
    # like the Sushi recording without names: the game track is digital silence most of the time
    fake_levels(monkeypatch, [SPARSE, MIC, GAME, SILENT, SILENT, MIX])
    ch = tracks.pick_streams(info(*(f"Track{i}" for i in range(1, 7))), DEFAULT_AUDIO)
    assert picked(ch, "mic") == (1, "content")


def test_two_tracks_mix_and_mic(monkeypatch):
    fake_levels(monkeypatch, [MIX, MIC])
    ch = tracks.pick_streams(info("Track1", "Track2"), DEFAULT_AUDIO)
    assert picked(ch, "mic") == (1, "content")
    assert picked(ch, "game") == (0, "content")  # the only other track: game sound (or the mix)


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
    assert "mic = track 2 'Track2' (by its sound)" in text
