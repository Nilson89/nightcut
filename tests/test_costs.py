from nightcut import costs


def test_compute_prices_usage_and_energy():
    pricing = {
        "usd_per_eur": 1.0,
        "electricity": {"eur_per_kwh": 0.5, "system_overhead_w": 0},
        "cloud_tiers": {"t": {"label": "T", "transcription_usd_per_min": 0.01,
                               "llm_input_usd_per_mtok": 1.0, "llm_output_usd_per_mtok": 2.0}},
    }
    stages = {
        "transcript": {"energy_wh": 500, "usage": {"transcribe_minutes": 100}},
        "edit": {"energy_wh": 500, "usage": {"input_tokens": 1_000_000, "output_tokens": 500_000}},
    }
    r = costs.compute(stages, pricing)
    assert r["energy_kwh"] == 1.0
    assert r["local_eur"] == 0.5
    assert abs(r["tiers"]["t"]["transcription_eur"] - 1.0) < 1e-9
    assert abs(r["tiers"]["t"]["llm_eur"] - 2.0) < 1e-9
    assert abs(r["tiers"]["t"]["total_eur"] - 3.0) < 1e-9


def test_default_pricing_file_loads():
    p = costs.load_pricing()
    assert {"budget", "premium"} <= set(p["cloud_tiers"])
    assert p["electricity"]["eur_per_kwh"] > 0


def test_subscriptions_per_year_and_minute_coverage():
    pricing = {"usd_per_eur": 2.0, "projection": {"sessions_per_week": 3},
               "subscriptions": {"clip": {"label": "Clip", "usd_per_month": 20.0, "minutes_per_month": 300},
                                 "chat": {"label": "Chat", "eur_per_month": 22.0, "minutes_per_month": None}}}
    s = costs.subscriptions(0.10, 4.0, pricing)       # 4 h per session, 3 per week -> 52 h per month
    assert abs(s["material_hours_per_month"] - 52.0) < 1e-9
    assert abs(s["local_eur_per_year"] - 15.6) < 1e-9
    clip, chat = s["subscriptions"]
    assert clip["eur_per_month"] == 10.0 and clip["eur_per_year"] == 120.0
    assert abs(clip["covers"] - 300 / (52 * 60)) < 1e-9
    assert chat["covers"] is None and chat["eur_per_year"] == 264.0
