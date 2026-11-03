"""A short, machine-readable summary of a run and the morning message built from it.

Hermes (or any other agent) can run `nightcut summary <folder>` and forward the text
instead of reading report.md and picking numbers itself.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import costs


def _num(v: float, digits: int = 1) -> str:
    return f"{v:.{digits}f}".replace(".", ",")


def _eur(v: float) -> str:
    return _num(v, 2) + " €"


def _clock(minutes: float) -> str:
    return f"{int(minutes // 60)} h {int(minutes % 60):02d} min" if minutes >= 60 else f"{minutes:.0f} min"


def base(stages: dict, pricing: dict, name: str, hours: float, warning: str | None) -> dict:
    result = costs.compute(stages, pricing)
    return {
        "name": name,
        "recording_hours": round(hours, 2),
        "processing_min": round(sum(s["seconds"] for s in stages.values()) / 60, 1),
        "vram_gb": round(max((s["vram_peak_mib"] for s in stages.values()), default=0) / 1024, 1),
        "cost_eur": {"budget": round(result["tiers"]["budget"]["total_eur"], 2),
                     "premium": round(result["tiers"]["premium"]["total_eur"], 2),
                     "local": round(result["local_eur"], 2)},
        **({"warning": warning} if warning else {}),
    }


def write(work: Path, data: dict) -> None:
    (work / "summary.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def text(s: dict) -> str:
    """German morning message: first lines fit a phone lock screen."""
    c = s["cost_eur"]
    if s["mode"] == "letsplay":
        lines = [
            f"🎬 nightcut: {s['name']}, {s['episodes']} Folgen fertig",
            f"{_num(s['recording_hours'])} h Aufnahme → {s['episodes']} Folgen à {s['episode_min'][0]:.0f}–"
            f"{s['episode_min'][1]:.0f} min · {_num(s['removed_min'])} min Leerlauf entfernt",
            *[f"Folge {n}: \"{t}\"" for n, t in enumerate(s["titles"], 1) if t],
        ]
        where = f"Ordner: {s['folder']}"
    else:
        lines = [
            f"🎬 nightcut: {s['name']} fertig",
            f"{_num(s['recording_hours'])} h Aufnahme → {s['clips']} Clips, {_num(s['minutes'])} min "
            f"(Ziel {s['target_minutes']} min) · {s['shorts']} Shorts"
            + (f" · {s['thumbnails']} Thumbnails" if s.get("thumbnails") else ""),
        ]
        if s.get("title"):
            lines.append(f"Bester Titel: \"{s['title']}\"")
        where = f"Timeline: {s['xml']}"
    lines += [
        f"Rechenzeit {_clock(s['processing_min'])} · max {_num(s['vram_gb'])} GB VRAM",
        f"Cloud hätte {_eur(c['budget'])}–{_eur(c['premium'])} gekostet, lokal {_eur(c['local'])} Strom",
        where,
    ]
    if s.get("warning"):
        lines.append(f"⚠️ {s['warning']}")
    return "\n".join(lines)
