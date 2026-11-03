import tempfile
from pathlib import Path

import numpy as np

from nightcut import killfeed, vision

CFG = {"candidates": {"pre_roll_s": 12, "post_roll_s": 6, "snap_to_speech_s": 4},
       "killfeed": {"every_s": 4}}


def test_fuzzy_player_name():
    assert killfeed.is_player("Nils0n1489", ["Nilson1489"])
    assert killfeed.is_player("nilson1489", ["Nilson1489"])
    assert not killfeed.is_player("looney1942", ["Nilson1489"])
    assert not killfeed.is_player("", ["Nilson1489"])


def test_same_line_seen_twice_counts_once():
    readings = [
        {"time": 100.0, "kills": [{"killer": "Nilson1489", "victim": "XLK04"}]},
        {"time": 104.0, "kills": [{"killer": "Nilson1489", "victim": "XLK04"},
                                  {"killer": "Nilson1489", "victim": "GreyVulcan"}]},
        {"time": 108.0, "kills": [{"killer": "Lord_Sensenmann", "victim": "seansstream"}]},
        {"time": 200.0, "kills": [{"killer": "Nilson1489", "victim": "XLK04"}]},  # killed again later
    ]
    kills = killfeed.player_kills(readings, ["Nilson1489"])
    assert [(k["time"], k["victim"]) for k in kills] == [(100.0, "XLK04"), (104.0, "GreyVulcan"), (200.0, "XLK04")]
    groups = killfeed.group_kills(kills, gap_s=15)
    assert [g["kills"] for g in groups] == [2, 1]


def test_quiet_kill_becomes_candidate_and_loud_one_is_marked():
    existing = [{"id": 1, "peak": 50.0, "start": 40.0, "end": 60.0, "score": 2.0, "transcript": "boah"}]
    groups = [{"start": 52.0, "end": 52.0, "victims": ["a"], "kills": 1},
              {"start": 300.0, "end": 310.0, "victims": ["b", "c"], "kills": 2}]
    score = np.zeros(4000, dtype=np.float32)
    out = killfeed.merge_into(existing, groups, [], score, 1000.0, CFG)
    assert len(out) == 2
    assert out[0]["kills"] == 1 and "vision" not in out[0]
    new = out[1]
    assert new["start"] == 300.0 - 12 - 4 and new["end"] == 316.0
    assert new["vision"]["event"] == "multikill" and new["vision"]["source"] == "killfeed"


def test_kill_feed_upgrades_a_missed_kill():
    cands = [{"score": 1.0, "kills": 2, "vision": {"event": "nothing", "intensity": 2, "summary": ""}},
             {"score": 1.0, "vision": {"event": "nothing", "intensity": 2, "summary": ""}}]
    vision.rescore(cands)
    assert cands[0]["vision"]["event"] == "multikill"
    assert cands[0]["final_score"] > cands[1]["final_score"]


def test_feed_region_is_inside_gameplay():
    region = killfeed.feed_region({"x": 0.0, "y": 0.0, "w": 1.0, "h": 0.5}, {"x": 0.0, "y": 0.5, "w": 0.3, "h": 0.2})
    assert region == {"x": 0.0, "y": 0.25, "w": 0.3, "h": 0.1}


def test_answer_parsing_skips_placeholders_and_bad_panels():
    answer = {"panels": [{"panel": 1, "kills": [{"killer": "name", "victim": "name"}]},
                         {"panel": 2, "kills": [{"killer": "Nilson1489", "victim": "XLK04"}]},
                         {"panel": 9, "kills": [{"killer": "Nilson1489", "victim": "ghost"}]},
                         "garbage"]}
    out = killfeed.parse_answer(answer, [10.0, 14.0, 18.0])
    assert out == [{"time": 14.0, "kills": [{"killer": "Nilson1489", "victim": "XLK04"}]}]


def test_reading_resumes_from_cache():
    with tempfile.TemporaryDirectory() as tmp:
        _resume(Path(tmp) / "readings.jsonl")


def _resume(cache):
    cache.write_text('{"file": "a.jpg", "readings": [{"time": 4.0, "kills": []}]}\n', encoding="utf-8")
    cfg = {"killfeed": {"model": "m"}, "vision": {"model": "m"}, "ollama": {}}
    # every tile is in the cache: no model call may happen (it would fail without Ollama)
    out = killfeed.read_tiles([{"file": "a.jpg", "times": [4.0]}], cfg, cache=cache)
    assert out == [{"time": 4.0, "kills": []}]
