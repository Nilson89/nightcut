from nightcut import summary

STAGES = {"audio": {"seconds": 600, "vram_peak_mib": 2048, "energy_wh": 40,
                    "usage": {"input_tokens": 0, "output_tokens": 0, "images": 0, "transcribe_minutes": 100}},
          "vision": {"seconds": 1200, "vram_peak_mib": 9216, "energy_wh": 80,
                     "usage": {"input_tokens": 500000, "output_tokens": 5000, "images": 400, "transcribe_minutes": 0}}}
PRICING = {"usd_per_eur": 1.0, "electricity": {"eur_per_kwh": 0.5, "system_overhead_w": 0},
           "cloud_tiers": {"budget": {"label": "b", "transcription_usd_per_min": 0.01,
                                      "llm_input_usd_per_mtok": 1.0, "llm_output_usd_per_mtok": 2.0},
                           "premium": {"label": "p", "transcription_usd_per_min": 0.02,
                                       "llm_input_usd_per_mtok": 2.0, "llm_output_usd_per_mtok": 10.0}}}


def test_highlight_message():
    s = summary.base(STAGES, PRICING, "session.mkv", 3.72, None)
    assert s["cost_eur"] == {"budget": 1.51, "premium": 3.05, "local": 0.06}
    s.update(mode="highlights", clips=16, minutes=5.0, target_minutes=14, shorts=3, title="Panzer weg!", xml="x.xml")
    text = summary.text(s)
    assert text.splitlines()[0] == "🎬 nightcut: session.mkv fertig"
    assert "16 Clips, 5,0 min (Ziel 14 min) · 3 Shorts" in text
    s["thumbnails"] = 3
    assert "3 Shorts · 3 Thumbnails" in summary.text(s)
    assert "Rechenzeit 30 min · max 9,0 GB VRAM" in text
    assert "1,51 €–3,05 € gekostet, lokal 0,06 € Strom" in text


def test_letsplay_message_with_warning():
    s = summary.base(STAGES, PRICING, "awayout", 4.75, "LLM failed")
    s.update(mode="letsplay", episodes=2, episode_min=[18.0, 32.4], removed_min=1.5,
             titles=["A Way Out #1 – Start", "A Way Out #2 – Flucht"], folder="out/awayout")
    text = summary.text(s)
    assert "2 Folgen à 18–32 min · 1,5 min Leerlauf entfernt" in text
    assert 'Folge 2: "A Way Out #2 – Flucht"' in text
    assert text.endswith("⚠️ LLM failed")
