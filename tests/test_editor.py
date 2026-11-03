from nightcut import editor, llm

CFG = {"editor": {"enabled": True, "model": "m", "target_minutes": 1, "max_clip_s": 60, "max_candidates": 2},
       "ollama": {}, "profile": {"name": "x"}}
CANDS = [{"id": n, "peak": n * 100 + 10, "start": n * 100.0, "end": n * 100 + 20.0, "score": float(n),
          "final_score": n / 10, "transcript": "x" * 500, "vision": {"event": "kill", "summary": "s"}}
         for n in range(1, 6)]


def test_only_best_candidates_go_to_the_model(monkeypatch):
    seen = {}

    def fake(cfg, model, system, user, **kw):
        seen.setdefault("user", user)   # the edit call; a later call only names top-ups
        return {"clips": [{"id": 5, "title": "Top", "reason": "r"}]}
    monkeypatch.setattr(llm, "chat_json", fake)
    monkeypatch.setattr(llm, "unload", lambda *a: None)
    clips, extra = editor.choose(CANDS, CFG)
    assert '"id": 5' in seen["user"] and '"id": 4' in seen["user"] and '"id": 1,' not in seen["user"]
    assert "x" * 151 not in seen["user"]          # transcripts are shortened
    assert clips[0]["title"] == "Top"


def test_top_up_uses_quiet_moments_last_and_never_deaths(monkeypatch):
    # calm game: the vision model sees "nothing" everywhere, the talk is the content
    cands = [{"id": n, "peak": n * 100 + 10, "start": n * 100.0, "end": n * 100 + 20.0, "score": float(n),
              "final_score": 0.1, "transcript": "t", "vision": {"event": ev, "summary": "s"}}
             for n, ev in enumerate(["nothing", "nothing", "death", "nothing", "funny", "nothing"], 1)]
    cfg = {**CFG, "editor": {**CFG["editor"], "target_minutes": 1.5, "max_candidates": 10}}
    monkeypatch.setattr(llm, "chat_json", lambda *a, **kw: {"clips": [{"id": 1, "title": "Hook", "reason": "r"}]})
    monkeypatch.setattr(llm, "unload", lambda *a: None)
    clips, _ = editor.choose(cands, cfg)
    ids = [c["id"] for c in clips]
    assert ids[0] == 1 and 3 not in ids            # hook stays first, no plain death
    assert 5 in ids and {6, 4} <= set(ids)         # rated moment first, then loudest quiet ones
    assert sum(c["end"] - c["start"] for c in clips) >= 0.9 * 90


def test_no_fill_leaves_quiet_moments_out(monkeypatch):
    cands = [{"id": n, "peak": n * 100 + 10, "start": n * 100.0, "end": n * 100 + 20.0, "score": float(n),
              "final_score": 0.1, "transcript": "t", "vision": {"event": ev, "summary": "s"}}
             for n, ev in enumerate(["nothing", "nothing", "funny", "nothing"], 1)]
    cfg = {**CFG, "editor": {**CFG["editor"], "target_minutes": 1.5, "max_candidates": 10, "fill": False}}
    monkeypatch.setattr(llm, "chat_json", lambda *a, **kw: {"clips": [{"id": 1, "title": "Hook", "reason": "r"}]})
    monkeypatch.setattr(llm, "unload", lambda *a: None)
    clips, _ = editor.choose(cands, cfg)
    assert [c["id"] for c in clips] == [1, 3]      # shorter video instead of quiet filler


def test_top_ups_get_titles_from_a_second_call(monkeypatch):
    calls = []

    def fake(cfg, model, system, user, **kw):
        calls.append(system)
        if "name clips" in system:
            return {"titles": [{"id": 4, "title": "Lager voll"}]}
        return {"clips": [{"id": 5, "title": "Top", "reason": "r"}]}
    monkeypatch.setattr(llm, "chat_json", fake)
    monkeypatch.setattr(llm, "unload", lambda *a: None)
    cfg = {**CFG, "editor": {**CFG["editor"], "target_minutes": 1.0, "max_candidates": 10}}
    clips, _ = editor.choose(CANDS, cfg)
    by_id = {c["id"]: c["title"] for c in clips}
    assert len(calls) == 2 and by_id[5] == "Top" and by_id[4] == "Lager voll"


def test_unusable_answer_warns(monkeypatch):
    monkeypatch.setattr(llm, "chat_json", lambda *a, **kw: {"clips": [{"id": 999}]})
    monkeypatch.setattr(llm, "unload", lambda *a: None)
    clips, extra = editor.choose(CANDS, CFG)
    assert clips and "warning" in extra
