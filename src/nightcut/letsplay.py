"""Let's Play mode: one or more long recordings -> cleaned-up episodes.

Nothing is picked as a highlight. The pipeline keeps everything, removes only
dead air (menus, loading screens, pauses, AFK; more with stronger settings),
and splits the result into episodes that end at natural breaks.

The timelines reference the original recordings directly. A cut is only an
in/out point, so in the editor every removed part can be pulled back.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from . import costs, episodes, export_xmeml, metadata, segments, signals, summary, tracks
from .media import extract_audio_tracks, keyframe_scan, load_pcm, probe
from .pipeline import Run, console
from .render import render_clip
from .transcribe import transcribe_regions

FULL_FRAME = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}


class LetsPlayRun(Run):
    def __init__(self, sources: list[Path], cfg: dict, force: set[str] | None = None, name: str | None = None):
        super().__init__(sources[0], cfg, force, name=name or f"{sources[0].stem}_letsplay")
        self.sources = sources

    def run(self) -> list[Path]:
        infos = [probe(s) for s in self.sources]
        self._resolve_layout(self.sources[0])  # one setup for the whole series: checked on the first recording
        cfg, lcfg = self.cfg, self.cfg["letsplay"]
        strength = lcfg["strengths"][lcfg["strength"]]
        offsets = list(np.cumsum([0.0] + [i.duration for i in infos[:-1]]))
        total_src = sum(i.duration for i in infos)
        streams, voice_streams, timeline_audio = [], [], []
        previous = None  # (track signature, positions): a series recorded with the same OBS setup is checked once
        for n, info in enumerate(infos):
            sig = tracks.signature(info)
            if previous and previous[0] == sig:
                found = {k: info.audio[p] for k, p in previous[1].items()}
                mix = info.audio[previous[2]] if previous[2] is not None else None
            else:
                console.print(f"  {info.path.name}:")
                found, mix, _ = self._pick_tracks(info, self.work / "cache" / f"{n:02d}")
                previous = (sig, {k: s.position for k, s in found.items()}, mix.position if mix else None)
            if "mic" not in found:
                raise SystemExit(f"{info.path.name} has no audio track. nightcut needs at least the mic.")
            streams.append(found)
            timeline_audio.append(tracks.timeline_tracks(info, found, mix))  # every track, for the edit
            # other voices (voice chat, an AI assistant only heard in the mix): no cuts while they talk
            voices = {name: found.get(name) if name in found else info.find_audio(cfg["audio"].get(name))
                      for name in lcfg.get("protect_voices") or []}
            voice_streams.append({k: v for k, v in voices.items() if v is not None and v.index != found["mic"].index})
        console.print(f"[bold]{len(infos)} recording(s)[/bold], {total_src / 3600:.2f} h in total, "
                      f"strength: {lcfg['strength']}, " + ("one episode per recording" if lcfg.get("episodes") == "per_recording"
                                                            else f"episodes of ~{lcfg['episode_minutes']} min"))
        sr = cfg["audio"]["sample_rate"]
        cache = self.work / "cache"
        layout = cfg["layout"]
        mask = layout.get("facecam") if layout.get("facecam_burned_in") else None

        def do_audio():
            paths = []
            for n, (info, st) in enumerate(zip(infos, streams)):
                console.print(f"  audio {n + 1}/{len(infos)}: {info.path.name}")
                p = extract_audio_tracks(info.path, {"mic": st["mic"]}, cache / f"{n:02d}", sr)["mic"]
                np.save(cache / f"{n:02d}" / "mic_env.npy", signals.envelope_db(load_pcm(p), sr))
                paths.append(str(p))
            return paths
        pcm_paths = self._stage("audio", do_audio)

        def do_scan():
            out = []
            for n, info in enumerate(infos):
                f = cache / f"{n:02d}" / "scan.npz"
                if not f.exists():
                    console.print(f"  picture {n + 1}/{len(infos)}: {info.path.name}")
                    np.savez(f, **keyframe_scan(info.path, layout["gameplay"], info.width, info.height, mask))
                out.append(str(f))
            return out
        scan_paths = self._stage("scan", do_scan)
        scans = [dict(np.load(p)) for p in scan_paths]

        def do_transcript():
            tcfg = cfg["transcribe"]
            result = []
            for n in range(len(infos)):
                env = np.load(cache / f"{n:02d}" / "mic_env.npy")
                regions = signals.voice_regions(env, tcfg["voice_margin_db"], tcfg["max_chunk_s"])
                costs.usage.transcribe_minutes += sum(e - s for s, e in regions) / 60
                console.print(f"  speech {n + 1}/{len(infos)}: {len(regions)} regions")
                segs = transcribe_regions(load_pcm(Path(pcm_paths[n])), sr, regions, tcfg)
                result.append(segs)
            return result
        transcripts = self._stage("transcript", do_transcript) if cfg["transcribe"]["enabled"] else [[] for _ in infos]

        def do_voices():
            # Only where the mic is quiet long enough to be cut does it matter whether someone else talks.
            tcfg, result = cfg["transcribe"], []
            min_gap = min(strength["min_gap_s"], lcfg["afk_s"])
            for n, info in enumerate(infos):
                found = []
                regions = segments.chunk_regions(segments.speech_gaps(transcripts[n], info.duration, min_gap),
                                                 tcfg["max_chunk_s"])
                for name, stream in voice_streams[n].items():
                    console.print(f"  {name} {n + 1}/{len(infos)}: {sum(b - a for a, b in regions) / 60:.0f} min to check")
                    pcm = extract_audio_tracks(info.path, {f"voice_{name}": stream}, cache / f"{n:02d}", sr)[f"voice_{name}"]
                    costs.usage.transcribe_minutes += sum(b - a for a, b in regions) / 60
                    segs = segments.real_speech(transcribe_regions(load_pcm(Path(pcm)), sr, regions, tcfg))
                    found += [{**s, "voice": name} for s in segs]
                result.append(sorted(found, key=lambda s: s["start"]))
            return result
        has_voices = cfg["transcribe"]["enabled"] and any(voice_streams)
        voices = self._stage("voices", do_voices) if has_voices else [[] for _ in infos]
        if has_voices:
            console.print(f"  other voices: {sum(len(v) for v in voices)} sentences protected from cuts")

        def do_cuts():
            files = []
            for n, info in enumerate(infos):
                stretches = segments.still_stretches(scans[n], lcfg, min(strength["min_gap_s"], lcfg["afk_s"]))
                console.print(f"  {info.path.name}: {len(stretches)} still stretches")
                segments.label_stretches(info.path, stretches, layout["gameplay"], info.width, info.height, cfg,
                                         progress=lambda d, k: console.print(f"  {d}/{k}", end="\r"))
                removals = segments.plan_removals(stretches, transcripts[n] + voices[n], info.duration, cfg)
                files.append({"stretches": stretches, "removals": removals,
                              "kept": segments.kept_pieces(info.duration, removals)})
            return files
        cuts = self._stage("cuts", do_cuts)
        removed = sum(r["end"] - r["start"] for f in cuts for r in f["removals"])
        console.print(f"  removed {removed / 60:.1f} min of {total_src / 60:.0f} min")

        def do_episodes():
            pieces = episodes.build_pieces([[tuple(k) for k in f["kept"]] for f in cuts], offsets)
            g = lambda n, items: [{**x, "start": x["start"] + offsets[n], "end": x["end"] + offsets[n]} for x in items]
            removals = [r for n, f in enumerate(cuts) for r in g(n, f["removals"])]
            segs = [s for n, t in enumerate(transcripts) for s in g(n, t)]
            blacks = [float(t) + offsets[n] for n, s in enumerate(scans)
                      for t, l in zip(s["time"], s["luma"]) if l < lcfg["black_luma"]]
            points = episodes.break_points(pieces, removals, segs, blacks, offsets, total_src, cfg)
            return episodes.split(pieces, points, cfg, offsets)
        eps = self._stage("episodes", do_episodes)
        console.print(f"  {len(eps)} episodes: " + ", ".join(f"{e['length'] / 60:.0f} min" for e in eps))

        first = int(lcfg.get("first_episode") or 1)  # number of the first episode in this run

        def do_export():
            out = []
            series = lcfg.get("series_name") or cfg["profile"].get("name", "Let's Play")
            separate_cam = layout.get("facecam") and not layout.get("facecam_burned_in")
            for ep in eps:
                # rendered files and originals both carry every track of the recording
                audio = timeline_audio[ep["pieces"][0]["file"]]
                if separate_cam or not lcfg["reference_source"]:
                    # stacked layouts (gameplay + cam in one canvas) need clean files per piece
                    media = self.work / "media" / f"ep{ep['n']:02d}"
                    media.mkdir(parents=True, exist_ok=True)
                    clips = [render_clip(infos[p["file"]], p, k, timeline_audio[p["file"]], cfg, media)
                             for k, p in enumerate(ep["pieces"], 1)]
                else:
                    clips = [{**p, "n": k, "gameplay_file": str(infos[p["file"]].path), "facecam_file": None,
                              "trim_in": p["start"]} for k, p in enumerate(ep["pieces"], 1)]
                for c in clips:
                    c["title"] = f"Teil {c['n']}"
                path = self.work / f"ep{ep['n']:02d}_{self.work.name}.xml"
                export_xmeml.write(clips, audio, cfg, path, f"{series} #{ep['n'] + first - 1:02d}")
                out.append(str(path))
            return out
        xmls = self._stage("export", do_export)

        meta = []
        if cfg["metadata"]["enabled"]:
            def do_metadata():
                series = lcfg.get("series_name") or cfg["profile"].get("name", "Let's Play")
                result = []
                for ep in eps:
                    lines, pos = [], 0.0
                    for p in ep["pieces"]:
                        for s in transcripts[p["file"]]:
                            if p["start"] <= s["start"] < p["end"]:
                                lines.append((pos + s["start"] - p["start"], s["text"]))
                        pos += p["end"] - p["start"]
                    m = metadata.generate_episode(lines, series, ep["n"] + first - 1, ep["length"], cfg)
                    (self.work / f"ep{ep['n']:02d}_youtube.txt").write_text(metadata.to_text(m), encoding="utf-8")
                    result.append(m)
                return result
            meta = self._stage("metadata", do_metadata)

        self._write_lp_report(infos, cuts, eps, meta)
        return [Path(x) for x in xmls]

    def _write_lp_report(self, infos, cuts, eps, meta) -> None:
        stages = self.log["stages"]
        total = sum(i.duration for i in infos)
        by_label: dict[str, float] = {}
        for f in cuts:
            for r in f["removals"]:
                by_label[r["label"]] = by_label.get(r["label"], 0.0) + r["end"] - r["start"]
        clock = metadata.fmt
        lines = [
            f"# nightcut Let's Play report: {self.work.name}",
            "",
            f"- Recordings: {len(infos)} ({total / 3600:.2f} h)",
            f"- Strength: {self.cfg['letsplay']['strength']}",
            f"- Removed: {sum(by_label.values()) / 60:.1f} min "
            f"({', '.join(f'{k} {v / 60:.1f} min' for k, v in sorted(by_label.items())) or 'nothing'})",
            f"- Episodes: {len(eps)}",
            f"- Total processing time: {costs.total_seconds(stages) / 60:.1f} min",
            f"- Highest VRAM use: {max((s['vram_peak_mib'] for s in stages.values()), default=0) / 1024:.1f} GB",
            "",
            "## Episodes",
            "| # | Length | From | To | Ends at | Title idea |",
            "|---|---|---|---|---|---|",
        ]
        for n, ep in enumerate(eps):
            a, b = ep["pieces"][0], ep["pieces"][-1]
            title = meta[n]["titles"][0] if n < len(meta) and meta[n]["titles"] else ""
            lines.append(f"| {ep['n']} | {clock(ep['length'])} | rec {a['file'] + 1} {clock(a['start'])} "
                         f"| rec {b['file'] + 1} {clock(b['end'])} | {ep['ends_at']} | {title} |")
        lines += ["", "## Cuts", "| Recording | From | To | Length | What |", "|---|---|---|---|---|"]
        for n, f in enumerate(cuts):
            for r in f["removals"]:
                lines.append(f"| {n + 1} | {clock(r['start'])} | {clock(r['end'])} | {r['end'] - r['start']:.0f} s "
                             f"| {r['label']}: {r.get('summary', '')} |")
        lines += ["", "## Stages", "| Stage | Time | VRAM used (above baseline) |", "|---|---|---|",
                  *[f"| {k} | {v['seconds'] / 60:.1f} min | {v['vram_peak_mib'] / 1024:.1f} GB |"
                    for k, v in stages.items()],
                  "", *costs.report_lines(costs.compute(stages, self.pricing), self.pricing, total / 3600)]
        (self.work / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        warning = next((m["warning"] for m in meta if m.get("warning")), None)
        lengths = [e["length"] / 60 for e in eps]
        s = summary.base(stages, self.pricing, self.work.name, total / 3600, warning)
        s.update(mode="letsplay", episodes=len(eps), episode_min=[round(min(lengths), 1), round(max(lengths), 1)],
                 removed_min=round(sum(by_label.values()) / 60, 1),
                 titles=[m["titles"][0] if m.get("titles") else "" for m in meta], folder=str(self.work))
        summary.write(self.work, s)
