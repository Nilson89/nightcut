import numpy as np

from nightcut import signals
from nightcut.signals import HOP


def test_spike_score_finds_loud_moment():
    db = np.full(int(300 / HOP), -40.0, dtype=np.float32)
    db[int(150 / HOP):int(152 / HOP)] = -10.0
    score = signals.spike_score(db)
    assert score.argmax() in range(int(150 / HOP), int(152 / HOP))
    assert score[int(50 / HOP)] == 0.0


def test_voice_regions_are_split_for_whisper():
    db = np.full(int(120 / HOP), -60.0, dtype=np.float32)
    db[int(10 / HOP):int(80 / HOP)] = -20.0
    regions = signals.voice_regions(db, margin_db=12, max_chunk_s=28)
    assert all(e - s <= 28 + 1e-6 for s, e in regions)
    assert regions[0][0] < 10 and regions[-1][1] > 79


def test_keyword_score_marks_sentence():
    segs = [{"start": 10.0, "end": 12.0, "text": "Let's go, Doppelkill!"}, {"start": 20.0, "end": 21.0, "text": "okay"}]
    score = signals.keyword_score(segs, {3.0: ["let's go"], 2.0: ["doppelkill"]}, int(60 / HOP))
    assert score[int(11 / HOP)] == 1.0
    assert score[int(20.5 / HOP)] == 0.0


def test_candidates_do_not_cut_mid_sentence():
    score = np.zeros(int(600 / HOP), dtype=np.float32)
    score[int(200 / HOP)] = 5.0
    segs = [{"start": 205.0, "end": 208.0, "text": "was war das denn"}]
    cfg = {"max_count": 10, "min_gap_s": 20, "pre_roll_s": 10, "post_roll_s": 6,
           "snap_to_speech_s": 4, "min_score_percentile": 90}
    cands = signals.pick_candidates(score, segs, cfg, 600.0)
    assert len(cands) == 1
    assert cands[0]["start"] == 190.0
    assert cands[0]["end"] >= 208.0


def test_too_few_peaks_go_below_the_percentile_but_not_below_the_median():
    # music everywhere: one clear peak, several medium ones, the rest flat
    score = np.full(int(600 / HOP), 0.2, dtype=np.float32)
    score[int(100 / HOP)] = 5.0
    for t in (200, 300, 400, 500):
        score[int(t / HOP)] = 0.8
    cfg = {"max_count": 10, "min_gap_s": 20, "pre_roll_s": 5, "post_roll_s": 5,
           "snap_to_speech_s": 4, "min_score_percentile": 99.9}
    assert len(signals.pick_candidates(score, [], cfg, 600.0)) == 1
    assert len(signals.pick_candidates(score, [], {**cfg, "min_count": 8}, 600.0)) == 5   # median 0.2 is the floor


def test_short_recording_below_baseline_window():
    """A 30 s clip against the 60 s baseline used to fail: operands could not be broadcast (119,) (240,)."""
    from nightcut.signals import moving_average, spike_score
    db = np.random.default_rng(0).uniform(-60, -20, 119).astype(np.float32)
    assert spike_score(db).shape == (119,)
    assert moving_average(db, 240).shape == (119,)
    assert moving_average(np.zeros(0, np.float32), 10).shape == (0,)
