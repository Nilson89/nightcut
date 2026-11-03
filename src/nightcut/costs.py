"""Count what a run consumes and price it: local electricity vs. cloud APIs."""

from __future__ import annotations

import threading
from pathlib import Path

import yaml

from .config import ROOT

PRICING_FILE = ROOT / "config" / "pricing.yaml"
_lock = threading.Lock()


def total_seconds(stages: dict) -> float:
    """Processing time of a run. Stages that ran at the same time as another one (2 GPUs) are
    marked `parallel`; their time is already inside that stage."""
    return sum(s["seconds"] for s in stages.values() if not s.get("parallel"))


class Usage:
    """Collects billable units while a stage runs (tokens, images, audio minutes)."""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.images = 0
        self.transcribe_minutes = 0.0

    def add_llm(self, input_tokens: int, output_tokens: int, images: int = 0) -> None:
        with _lock:  # with several GPUs, model answers arrive at the same time
            self.input_tokens += int(input_tokens or 0)
            self.output_tokens += int(output_tokens or 0)
            self.images += images

    def as_dict(self) -> dict:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "images": self.images, "transcribe_minutes": round(self.transcribe_minutes, 2)}


usage = Usage()


def load_pricing(path: Path | None = None) -> dict:
    with open(path or PRICING_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)


def compute(stages: dict, pricing: dict) -> dict:
    """Sum usage and energy over all stages and price them."""
    totals = Usage()
    energy_wh = 0.0
    for s in stages.values():
        u = s.get("usage") or {}
        totals.add_llm(u.get("input_tokens", 0), u.get("output_tokens", 0), u.get("images", 0))
        totals.transcribe_minutes += u.get("transcribe_minutes", 0.0)
        energy_wh += s.get("energy_wh", 0.0)

    rate = pricing["usd_per_eur"]
    local_eur = energy_wh / 1000 * pricing["electricity"]["eur_per_kwh"]
    tiers = {}
    for key, t in pricing["cloud_tiers"].items():
        transcription = totals.transcribe_minutes * t["transcription_usd_per_min"] / rate
        llm = (totals.input_tokens * t["llm_input_usd_per_mtok"]
               + totals.output_tokens * t["llm_output_usd_per_mtok"]) / 1e6 / rate
        tiers[key] = {"label": t["label"], "transcription_eur": transcription, "llm_eur": llm,
                      "total_eur": transcription + llm}
    return {"usage": totals.as_dict(), "energy_kwh": energy_wh / 1000, "local_eur": local_eur, "tiers": tiers}


def subscriptions(local_eur: float, recording_hours: float, pricing: dict) -> dict:
    """Per-year comparison with monthly subscriptions at the projected number of sessions."""
    per_week = (pricing.get("projection") or {}).get("sessions_per_week", 0)
    material_min = recording_hours * 60 * per_week * 52 / 12  # recorded minutes per month
    subs = []
    for s in (pricing.get("subscriptions") or {}).values():
        month = s["eur_per_month"] if "eur_per_month" in s else s["usd_per_month"] / pricing["usd_per_eur"]
        included = s.get("minutes_per_month")
        subs.append({"label": s["label"], "eur_per_month": month, "eur_per_year": month * 12,
                     "covers": included / material_min if included and material_min else None,
                     "note": s.get("note", "")})
    return {"material_hours_per_month": material_min / 60, "local_eur_per_year": local_eur * per_week * 52,
            "subscriptions": subs}


def report_lines(result: dict, pricing: dict, recording_hours: float | None = None) -> list[str]:
    """Markdown section for report.md."""
    def eur(v: float) -> str:
        return f"{v:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")

    u = result["usage"]
    proj = pricing.get("projection") or {}
    per_year = proj.get("sessions_per_week", 0) * 52
    lines = [
        "## Cloud vs. local",
        "",
        f"Billable units of this run: {u['transcribe_minutes']:.0f} min speech, {u['images']} images, "
        f"{u['input_tokens']:,} input tokens, {u['output_tokens']:,} output tokens.",
        "",
        "| | Transcription | Vision + editing | Total | Saved locally |",
        "|---|---|---|---|---|",
    ]
    for t in result["tiers"].values():
        lines.append(f"| {t['label']} | {eur(t['transcription_eur'])} | {eur(t['llm_eur'])} | "
                     f"**{eur(t['total_eur'])}** | {eur(t['total_eur'] - result['local_eur'])} |")
    lines += [
        f"| Local (electricity, {result['energy_kwh']:.2f} kWh at {pricing['electricity']['eur_per_kwh']:.2f} €/kWh) "
        f"| | | **{eur(result['local_eur'])}** | |",
        "",
    ]
    if per_year:
        lines.append(f"Projection at {proj['sessions_per_week']} sessions per week ({per_year} per year):")
        for t in result["tiers"].values():
            saved = (t["total_eur"] - result["local_eur"]) * per_year
            line = f"- {t['label']}: {eur(saved)} saved per year"
            if proj.get("gpu_price_eur") and t["total_eur"] > result["local_eur"]:
                nights = proj["gpu_price_eur"] / (t["total_eur"] - result["local_eur"])
                line += f", GPU paid off after {nights:,.0f} sessions".replace(",", ".")
            lines.append(line)
        lines.append("")
    if recording_hours and pricing.get("subscriptions") and per_year:
        s = subscriptions(result["local_eur"], recording_hours, pricing)
        lines += [
            f"Subscriptions at {proj['sessions_per_week']} sessions per week like this one "
            f"({s['material_hours_per_month']:.0f} h of recordings per month):",
            "",
            "| Subscription | Per month | Per year | Included minutes cover | Note |",
            "|---|---|---|---|---|",
            *[f"| {x['label']} | {eur(x['eur_per_month'])} | {eur(x['eur_per_year'])} | "
              f"{'–' if x['covers'] is None else format(x['covers'], '.0%')} | {x['note']} |"
              for x in s["subscriptions"]],
            f"| **Local (electricity)** | | **{eur(s['local_eur_per_year'])}** | 100 % | measured in this run |",
            "",
        ]
    lines.append(f"_Prices checked {pricing['checked']}, see config/pricing.yaml for sources. "
                 "Not included in the cloud price: uploading the recording, rendering, storage._")
    return lines
