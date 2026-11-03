from nightcut import vision


def test_generic_events_score_above_nothing():
    cands = [{"score": 1.0, "vision": {"event": ev, "intensity": 7, "summary": ""}}
             for ev in ("success", "fail", "reaction", "nothing")]
    vision.rescore(cands)
    scores = [c["final_score"] for c in cands]
    assert min(scores[:3]) > scores[3]


def test_shooter_events_only_for_shooters():
    sim = vision.offered_events({"profile": {"name": "Sushi"}})
    assert "multikill" not in sim and "death" not in sim and "success" in sim
    assert "multikill" in vision.offered_events({"profile": {"game_type": "shooter"}})
    assert "kill" in vision.offered_events({"profile": {"killfeed_region": {"x": 0}}})   # old shooter profiles


def test_profile_can_add_events():
    cfg = {"profile": {"vision_events": {"rush_hour": 0.95}}}
    assert vision.event_weights(cfg)["rush_hour"] == 0.95
    assert "unknown" not in vision.event_weights(cfg)          # not offered to the model
    cands = [{"score": 1.0, "vision": {"event": "rush_hour", "intensity": 8, "summary": ""}},
             {"score": 1.0, "vision": {"event": "reaction", "intensity": 8, "summary": ""}}]
    vision.rescore(cands, cfg)
    assert cands[0]["final_score"] > cands[1]["final_score"]
