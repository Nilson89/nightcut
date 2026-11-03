import numpy as np

from nightcut import episodes, segments

CFG = {"letsplay": {
    "strength": "gentle",
    "strengths": {"gentle": {"remove": ["menu", "loading", "pause", "black", "afk"], "min_gap_s": 8}},
    "still_motion": 2.5, "black_luma": 12, "afk_s": 30, "keep_pad_s": 1.0,
    "file_break_score": 1.0, "break_phrases": ["bis zum nächsten mal"],
    "episode_minutes": 10, "tolerance": 0.2,
}}


def test_still_stretch_found_between_moving_keyframes():
    t = np.arange(0, 100, 4.0, dtype=np.float32)
    motion = np.full(len(t), 20.0, dtype=np.float32)
    motion[10:15] = 0.5                   # keyframes 9..14 look the same
    luma = np.full(len(t), 80.0, dtype=np.float32)
    found = segments.still_stretches({"time": t, "motion": motion, "luma": luma}, CFG["letsplay"], 8)
    assert found == [{"start": 36.0, "end": 56.0, "black": 0.0}]


def test_menu_is_cut_but_not_while_talking():
    stretches = [{"start": 100.0, "end": 140.0, "label": "menu", "black": 0.0}]
    talk = [{"start": 120.0, "end": 125.0, "text": "welche Option?"}]
    cuts = segments.plan_removals(stretches, talk, 600, CFG)
    assert cuts == [{"start": 101.0, "end": 119.0, "label": "menu", "summary": ""}]


def test_quiet_still_gameplay_is_afk_and_cutscene_stays():
    stretches = [{"start": 0.0, "end": 60.0, "label": "gameplay", "black": 0.0},
                 {"start": 100.0, "end": 160.0, "label": "cutscene", "black": 0.0}]
    cuts = segments.plan_removals(stretches, [], 600, CFG)
    assert [c["label"] for c in cuts] == ["afk"]


def test_kept_pieces_are_the_complement():
    assert segments.kept_pieces(100, [{"start": 10, "end": 20}, {"start": 50, "end": 60}]) == \
        [(0.0, 10), (20, 50), (60, 100)]


def test_episode_ends_at_said_goodbye_near_target_length():
    # two recordings of 10 min each, nothing cut
    pieces = episodes.build_pieces([[(0.0, 600.0)], [(0.0, 600.0)]], [0.0, 600.0])
    segs = [{"start": 545.0, "end": 548.0, "text": "Ciao, bis zum nächsten Mal!"}]
    cfg = {"letsplay": {**CFG["letsplay"], "file_break_score": 0.0}}
    points = episodes.break_points(pieces, [], segs, [], [0.0, 600.0], 1200.0, cfg)
    eps = episodes.split(pieces, points, cfg)
    assert len(eps) == 2
    assert abs(eps[0]["length"] - 548.5) < 0.01
    assert eps[1]["pieces"][0] == {"file": 0, "start": 548.5, "end": 600.0}


def test_episode_spans_two_recordings():
    pieces = episodes.build_pieces([[(0.0, 300.0)], [(0.0, 900.0)]], [0.0, 300.0])
    eps = episodes.slice_episodes(pieces, [{"edit": 600.0, "reason": "x"}])
    assert eps[0]["pieces"] == [{"file": 0, "start": 0.0, "end": 300.0}, {"file": 1, "start": 0.0, "end": 300.0}]


def test_music_hallucinations_are_noise():
    from nightcut.transcribe import is_noise
    assert is_noise("Musik")
    assert is_noise("E-E-E-E-E-E-E-E-E-E")
    assert is_noise("DIN DIN DIN DIN DIN DIN DIN")
    assert not is_noise("Oh, oh, das war knapp!")
    assert not is_noise("Musik ist hier echt gut")


def test_series_name_is_not_repeated_in_the_title():
    from nightcut.metadata import strip_series
    assert strip_series("Car Dealer Simulator: Goldbarren-Suche", "Car Dealer Simulator") == "Goldbarren-Suche"
    assert strip_series("Car Dealer Simulator #227 – Toni", "Car Dealer Simulator") == "Toni"
    assert strip_series("Auto-Kauf-Abenteuer", "Car Dealer Simulator") == "Auto-Kauf-Abenteuer"


def test_known_whisper_inventions_are_noise_but_real_goodbyes_stay():
    from nightcut.transcribe import is_noise
    assert is_noise("Vertraue und glaube, es hilft, es heilt die göttliche Kraft!")
    assert is_noise("Untertitelung des ZDF, 2020")
    assert is_noise("Vielen Dank fürs Zuschauen!")
    assert not is_noise("Vielen Dank fürs Zuschauen, bis zum nächsten Mal, ciao!")


def test_repeated_goodbye_is_not_an_episode_end():
    pieces = episodes.build_pieces([[(0.0, 1200.0)]], [0.0])
    segs = [{"start": t, "end": t + 1, "text": "Bis zum nächsten Mal."} for t in (100.0, 300.0, 500.0)]
    points = episodes.break_points(pieces, [], segs, [], [0.0], 1200.0, CFG)
    assert not [p for p in points if p["reason"].startswith("said")]


def test_per_recording_keeps_every_recording_whole():
    # a 65 min recording with a cut in it and a goodbye in the middle stays one episode
    offsets = [0.0, 3900.0, 4320.0]
    pieces = episodes.build_pieces([[(0.0, 1200.0), (1230.0, 3900.0)], [(0.0, 420.0)], [(0.0, 600.0)]], offsets)
    segs = [{"start": 1500.0, "end": 1503.0, "text": "Bis zum nächsten Mal!"}]
    cfg = {"letsplay": {**CFG["letsplay"], "episodes": "per_recording"}}
    points = episodes.break_points(pieces, [], segs, [], offsets, 4920.0, cfg)
    eps = episodes.split(pieces, points, cfg, offsets)
    assert [e["length"] for e in eps] == [3870.0, 420.0, 600.0]
    assert eps[0]["pieces"] == [{"file": 0, "start": 0.0, "end": 1200.0}, {"file": 0, "start": 1230.0, "end": 3900.0}]
    assert [e["ends_at"] for e in eps] == ["next recording", "next recording", "end of material"]


def test_short_recording_still_ends_its_episode():
    # recording 1 is only 7 min (outside 10 min +- 20 %), but a session change is the natural end
    pieces = episodes.build_pieces([[(0.0, 420.0)], [(0.0, 600.0)], [(0.0, 600.0)]], [0.0, 420.0, 1020.0])
    points = episodes.break_points(pieces, [], [], [], [0.0, 420.0, 1020.0], 1620.0, CFG)
    eps = episodes.split(pieces, points, CFG)
    assert [e["length"] for e in eps] == [420.0, 600.0, 600.0]
    assert [e["ends_at"] for e in eps][:2] == ["next recording", "next recording"]


def test_other_voices_protect_quiet_mic_from_cuts():
    # mic is quiet from 10 to 100 s, but the AI assistant (heard only in the mix) talks from 35 to 55 s
    cfg = {"letsplay": {**CFG["letsplay"], "strength": "normal",
                        "strengths": {"normal": {"remove": ["silence"], "min_gap_s": 5, "silence_s": 30}}}}
    mic = [{"start": 0.0, "end": 10.0, "text": "so jetzt"}, {"start": 100.0, "end": 110.0, "text": "weiter"}]
    ai = segments.real_speech([{"start": 35.0, "end": 55.0, "text": "Stell sie nah ans Lagerhaus, kurze Wege."},
                               {"start": 70.0, "end": 71.0, "text": "Vielen Dank."}])   # invented on music
    assert len(ai) == 1
    alone = segments.plan_removals([], mic, 110.0, cfg)
    assert [(r["start"], r["end"]) for r in alone] == [(11.0, 99.0)]
    protected = segments.plan_removals([], mic + ai, 110.0, cfg)
    assert [(r["start"], r["end"]) for r in protected] == [(56.0, 99.0)]   # 10-35 s is shorter than 30 s


def test_long_gaps_are_chunked_for_whisper():
    assert segments.chunk_regions([(0.0, 60.0), (100.0, 100.3)], 28) == [(0.0, 28.0), (28.0, 56.0), (56.0, 60.0)]
