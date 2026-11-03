"""Cut the chosen clips out of the recording as edit-friendly files.

Each clip is written once as gameplay (with all audio tracks) and once as
facecam, both from a single read of the source. Handles give the editor room
to trim. The timeline then references these clean files, so no NLE has to
deal with the stacked layout, MKV or 4:4:4 video.
"""

from __future__ import annotations

from pathlib import Path

from .media import AudioStream, MediaInfo, crop_filter, run


def render_clip(info: MediaInfo, clip: dict, n: int, streams: dict[str, AudioStream], cfg: dict, out_dir: Path) -> dict:
    rcfg = cfg["render"]
    handle = rcfg["handles_s"]
    src_in = max(clip["start"] - handle, 0.0)
    src_out = min(clip["end"] + handle, info.duration)
    # The source time is part of the name: a changed edit never reuses a stale file.
    stem = f"clip_{n:02d}_{int(src_in * 1000):09d}"
    gameplay = out_dir / f"{stem}_gameplay.mp4"
    separate_cam = cfg["layout"].get("facecam") and not cfg["layout"].get("facecam_burned_in")
    facecam = out_dir / f"{stem}_facecam.mp4" if separate_cam else None

    if not gameplay.exists() or (facecam and not facecam.exists()):
        video_opts = ["-c:v", rcfg["codec"], "-preset", "p5", "-rc", "vbr", "-cq", str(rcfg["cq"]), "-b:v", "0",
                      "-pix_fmt", "yuv420p", "-g", "60"]
        graph = f"[0:v]{crop_filter(cfg['layout']['gameplay'], info.width, info.height)}[g]"
        if facecam:
            graph += f";[0:v]{crop_filter(cfg['layout']['facecam'], info.width, info.height)}[f]"
        cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-ss", f"{src_in:.3f}", "-t", f"{src_out - src_in:.3f}",
               "-i", str(info.path), "-filter_complex", graph, "-map", "[g]"]
        for pos, (name, stream) in enumerate(streams.items()):
            cmd += ["-map", f"0:{stream.index}", f"-metadata:s:a:{pos}", f"title={name.upper()}",
                    f"-metadata:s:a:{pos}", f"handler_name={name.upper()}"]
        parts = {gameplay: gameplay.with_name(gameplay.name + ".part")}
        cmd += video_opts + ["-c:a", "aac", "-b:a", rcfg["audio_bitrate"], "-movflags", "+faststart",
                             "-f", "mp4", str(parts[gameplay])]
        if facecam:
            parts[facecam] = facecam.with_name(facecam.name + ".part")
            cmd += ["-map", "[f]", "-an"] + video_opts + ["-movflags", "+faststart", "-f", "mp4", str(parts[facecam])]
        run(cmd)
        for final, part in parts.items():
            part.replace(final)

    return {
        **clip,
        "n": n,
        "gameplay_file": str(gameplay),
        "facecam_file": str(facecam) if facecam else None,
        "file_in": src_in,                     # source time where the rendered file starts
        "trim_in": clip["start"] - src_in,     # offset of the cut inside the rendered file
        "file_duration": src_out - src_in,
    }
