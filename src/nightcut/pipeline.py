"""The overnight pipeline: resumable stages, each writing its result to the work folder.

If the night is interrupted, run the same command again: finished stages are skipped.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

import numpy as np
from rich.console import Console

from . import costs, editor, export_xmeml, killfeed, metadata, shorts, signals, summary, thumbnails, vision
from .media import extract_audio_tracks, load_pcm, probe
from .render import render_clip
from .transcribe import free_vram, load_asr, transcribe_regions

console = Console()


class VramMonitor:
    """Sample used GPU memory once per second.

    Windows does not report per-process VRAM, so the first sample is taken as
    baseline (desktop, browser, other apps) and only the increase is reported.
    """

    def __init__(self):
        self.baseline_mib: int | None = None
        self.peak_mib = 0
        self.energy_wh = 0.0     # GPU energy, integrated from power.draw
        self.seconds = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self):
        while not self._stop.is_set():
            try:
                out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,power.draw",
                                      "--format=csv,noheader,nounits"],
                                     capture_output=True, text=True, timeout=5).stdout
                rows = [line.split(",") for line in out.strip().splitlines()]
                used = max(int(r[0]) for r in rows)
                self.energy_wh += sum(float(r[1]) for r in rows) / 3600  # one sample per second
                if self.baseline_mib is None:
                    self.baseline_mib = used
                self.peak_mib = max(self.peak_mib, used)
            except Exception:
                pass
            self.seconds += 1.0
            self._stop.wait(1.0)

    @property
    def used_mib(self) -> int:
        return max(self.peak_mib - (self.baseline_mib or 0), 0)

    def __enter__(self):
        self._thread.start()
        time.sleep(1.2)  # take the baseline before the stage loads anything
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=3)


class Run:
    def __init__(self, source: Path, cfg: dict, force: set[str] | None = None, name: str | None = None):
        self.source = source
        self.cfg = cfg
        self.force = force or set()
        self._dirty = False  # once a stage re-runs, everything after it must re-run too
        self.work = Path(os.path.abspath(cfg["output_dir"])) / (name or source.stem)
        self.work.mkdir(parents=True, exist_ok=True)
        self.log_path = self.work / "run.json"
        self.log = json.loads(self.log_path.read_text(encoding="utf-8")) if self.log_path.exists() else {"stages": {}}
        self.pricing = costs.load_pricing(Path(cfg["pricing_file"]) if cfg.get("pricing_file") else None)

    # -- helpers -------------------------------------------------------------
    def _json(self, name: str):
        return self.work / f"{name}.json"

    def _load(self, name: str):
        return json.loads(self._json(name).read_text(encoding="utf-8"))

    def _save(self, name: str, data) -> None:
        self._json(name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _stage(self, name: str, fn, *, cached_file: str | None = None):
        """Run fn unless its result already exists; record wall time and VRAM peak."""
        target = cached_file or name
        if self._json(target).exists() and name not in self.force and not self._dirty:
            console.print(f"[dim]✓ {name} (cached)[/dim]")
            return self._load(target)
        self._dirty = True
        console.print(f"[bold cyan]▶ {name}[/bold cyan]")
        costs.usage.reset()
        t0 = time.time()
        with VramMonitor() as vram:
            result = fn()
        elapsed = time.time() - t0
        self._save(target, result)
        overhead_wh = self.pricing["electricity"]["system_overhead_w"] * elapsed / 3600
        self.log["stages"][name] = {"seconds": round(elapsed, 1), "vram_peak_mib": vram.used_mib,
                                    "vram_baseline_mib": vram.baseline_mib,
                                    "energy_wh": round(vram.energy_wh + overhead_wh, 2),
                                    "usage": costs.usage.as_dict()}
        self.log_path.write_text(json.dumps(self.log, indent=2), encoding="utf-8")
        console.print(f"[green]✓ {name}[/green] {elapsed / 60:.1f} min, VRAM used {vram.used_mib / 1024:.1f} GB")
        return result

    # -- stages ---------------------------------------------------------------
    def run(self) -> Path:
        cfg = self.cfg
        info = probe(self.source)
        streams = {name: info.find_audio(cfg["audio"].get(name)) for name in ("mic", "game", "party")}
        streams = {k: v for k, v in streams.items() if v is not None}
        if "mic" not in streams:
            raise SystemExit("No mic track found. Check the 'audio' section of your config.")
        console.print(f"[bold]{self.source.name}[/bold]: {info.duration / 3600:.2f} h, {info.width}x{info.height} "
                       f"@ {info.fps:.0f} fps, audio: {', '.join(f'{k}={v.title}' for k, v in streams.items())}")
        sr = cfg["audio"]["sample_rate"]

        def do_audio():
            paths = extract_audio_tracks(self.source, streams, self.work / "cache", sr)
            env = {name: signals.envelope_db(load_pcm(p), sr) for name, p in paths.items()}
            np.savez_compressed(self.work / "cache" / "envelopes.npz", **env)
            return {name: str(p) for name, p in paths.items()}
        pcm_paths = self._stage("audio", do_audio)
        env = dict(np.load(self.work / "cache" / "envelopes.npz"))

        def do_transcript():
            tcfg = cfg["transcribe"]
            regions = signals.voice_regions(env["mic"], tcfg["voice_margin_db"], tcfg["max_chunk_s"])
            speech_min = sum(e - s for s, e in regions) / 60
            costs.usage.transcribe_minutes += speech_min
            console.print(f"  {len(regions)} speech regions, {speech_min:.0f} min of speech")
            return transcribe_regions(load_pcm(Path(pcm_paths["mic"])), sr, regions, tcfg,
                                      progress=lambda d, n: console.print(f"  {d}/{n}", end="\r"))
        segments = self._stage("transcript", do_transcript) if cfg["transcribe"]["enabled"] else []

        kcfg, feed = cfg.get("killfeed") or {}, cfg["profile"].get("killfeed_region")
        readings = []
        if kcfg.get("enabled") and feed and cfg["vision"]["enabled"]:
            if not kcfg.get("player_names"):
                console.print("[yellow]  kill feed scan skipped: set killfeed.player_names to your in-game name[/yellow]")
            else:
                def do_killfeed():
                    region = killfeed.feed_region(cfg["layout"]["gameplay"], feed)
                    tiles = killfeed.extract_tiles(self.source, region, info.width, info.height, kcfg["every_s"],
                                                   kcfg["panels_per_call"], self.work / "cache" / "killfeed")
                    console.print(f"  {len(tiles)} kill feed images")
                    found = killfeed.read_tiles(tiles, cfg, progress=lambda d, n: console.print(f"  {d}/{n}", end="\r"),
                                                cache=self.work / "cache" / "killfeed" / "readings.jsonl")
                    return {"readings": found, "killers": killfeed.killer_stats(found)}
                readings = self._stage("killfeed", do_killfeed)["readings"]

        def do_candidates():
            sig = {name: signals.spike_score(db) for name, db in env.items()}
            sig["keywords"] = signals.keyword_score(segments, cfg["profile"].get("keywords"), len(env["mic"]))
            score = signals.combine(sig, cfg["signals"], cfg["signals"]["smooth_s"])
            np.save(self.work / "cache" / "score.npy", score)
            # the editor should be able to choose: about twice as many ~20 s moments as the target needs
            need = min(int(2 * cfg["editor"]["target_minutes"] * 60 / 20), cfg["editor"].get("max_candidates", 60))
            cands = signals.pick_candidates(score, segments, {"min_count": need, **cfg["candidates"]}, info.duration)
            if readings:
                kills = killfeed.player_kills(readings, kcfg["player_names"])
                groups = killfeed.group_kills(kills, kcfg["multikill_gap_s"])
                console.print(f"  kill feed: {len(kills)} kills in {len(groups)} moments")
                cands = killfeed.merge_into(cands, groups, segments, score, info.duration, cfg)
            return cands
        candidates = self._stage("candidates", do_candidates)
        console.print(f"  {len(candidates)} candidates")

        if cfg["vision"]["enabled"]:
            def do_vision():
                result = vision.analyse(info, [dict(c) for c in candidates], cfg,
                                        progress=lambda d, n: console.print(f"  {d}/{n}", end="\r"))
                vision.rescore(result, cfg)
                return result
            candidates = self._stage("vision", do_vision)

        fill = cfg["editor"].get("fill") is not False
        if self._json("edit").exists() and self._load("edit").get("fill", True) != fill:
            self.force.add("edit")  # the answer to "fill up?" changed: cut again

        def do_edit():
            clips, extra = editor.choose(candidates, cfg)
            return {"clips": clips, "fill": fill, **extra}
        edit = self._stage("edit", do_edit)
        total = sum(c["end"] - c["start"] for c in edit["clips"])
        console.print(f"  {len(edit['clips'])} clips, {total / 60:.1f} min")

        def do_render():
            out = self.work / "media"
            out.mkdir(exist_ok=True)
            return [render_clip(info, c, n, streams, cfg, out) for n, c in enumerate(edit["clips"], 1)]
        rendered = self._stage("render", do_render)

        xml_path = self.work / f"{self.source.stem}_{cfg['export']['sequence_name_suffix']}.xml"
        self._stage("export", lambda: str(export_xmeml.write(
            rendered, list(streams), cfg, xml_path, f"{self.source.stem} {cfg['export']['sequence_name_suffix']}")))

        if cfg["shorts"]["enabled"]:
            def do_shorts():
                out = self.work / "shorts"
                out.mkdir(exist_ok=True)
                picked = shorts.pick(edit["clips"], cfg["shorts"]["count"], cfg["shorts"]["min_s"])
                pcm = load_pcm(Path(pcm_paths["mic"]))
                asr = load_asr(cfg["transcribe"])
                words = [shorts.word_timestamps(pcm, sr, c["start"], c["start"] + min(c["end"] - c["start"],
                                                cfg["shorts"]["max_s"]), cfg, asr) for c in picked]
                del asr
                free_vram()
                result = []
                for n, (c, w) in enumerate(zip(picked, words), 1):
                    ass = out / f"short_{n:02d}.ass"
                    shorts.write_ass(w, ass, cfg["shorts"], shorts.caption_y(cfg))
                    path = shorts.render(info, c, n, streams, ass, cfg, out)
                    result.append({"file": str(path), "title": c.get("title", ""), "source_start": c["start"]})
                return result
            made_shorts = self._stage("shorts", do_shorts)
        else:
            made_shorts = []

        if cfg["metadata"]["enabled"]:
            def do_metadata():
                meta = metadata.generate(edit["clips"], edit, cfg)
                (self.work / "youtube.txt").write_text(metadata.to_text(meta), encoding="utf-8")
                return meta
            self._stage("metadata", do_metadata)

        if cfg.get("thumbnails", {}).get("enabled") and edit["clips"]:
            self._stage("thumbnails", lambda: thumbnails.make(
                info, edit["clips"], [s["file"] for s in made_shorts], cfg, self.work / "thumbnails"))

        self._write_report(info, candidates, edit)
        return xml_path

    def _write_report(self, info, candidates, edit) -> None:
        clips = edit["clips"]
        total = sum(c["end"] - c["start"] for c in clips)
        stages = self.log["stages"]
        lines = [
            f"# nightcut report: {self.source.name}",
            "",
            f"- Recording: {info.duration / 3600:.2f} h",
            f"- Candidates found: {len(candidates)}",
            *([f"- Kills read from the kill feed: {sum(c.get('kills', 0) for c in candidates)}"]
              if any("kills" in c for c in candidates) else []),
            f"- Clips in timeline: {len(clips)} ({total / 60:.1f} min)",
            f"- Total processing time: {sum(s['seconds'] for s in stages.values()) / 60:.1f} min",
            f"- Highest VRAM use: {max((s['vram_peak_mib'] for s in stages.values()), default=0) / 1024:.1f} GB",
            "",
            *[f"> ⚠️ {w}" for w in (edit.get("warning"),) if w],
            "",
            "## Title ideas",
            *[f"- {t}" for t in edit.get("video_title_ideas", [])],
            "",
            "## Clips",
            "| # | Source time | Length | Title | Why |",
            "|---|---|---|---|---|",
            *[f"| {n} | {int(c['start'] // 3600)}:{int(c['start'] % 3600 // 60):02d}:{int(c['start'] % 60):02d} "
              f"| {c['end'] - c['start']:.0f} s | {c.get('title', '')} | {c.get('reason', '')} |"
              for n, c in enumerate(clips, 1)],
            "",
            *(["## Thumbnail drafts", *[f"- `{p}`" for p in self._load("thumbnails")], ""]
              if self._json("thumbnails").exists() else []),
            "## Stages",
            "| Stage | Time | VRAM used (above baseline) |",
            "|---|---|---|",
            *[f"| {k} | {v['seconds'] / 60:.1f} min | {v['vram_peak_mib'] / 1024:.1f} GB |" for k, v in stages.items()],
            "",
            *costs.report_lines(costs.compute(stages, self.pricing), self.pricing, info.duration / 3600),
        ]
        (self.work / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

        meta = self._load("metadata") if self._json("metadata").exists() else {}
        made = self._load("shorts") if self._json("shorts").exists() else []
        s = summary.base(stages, self.pricing, self.source.name, info.duration / 3600,
                         edit.get("warning") or meta.get("warning"))
        s.update(mode="highlights", clips=len(clips), minutes=round(total / 60, 1),
                 target_minutes=self.cfg["editor"]["target_minutes"], shorts=len(made),
                 thumbnails=len(self._load("thumbnails")) if self._json("thumbnails").exists() else 0,
                 title=(meta.get("titles") or edit.get("video_title_ideas") or [""])[0],
                 xml=str(self.work / f"{self.source.stem}_{self.cfg['export']['sequence_name_suffix']}.xml"))
        summary.write(self.work, s)
