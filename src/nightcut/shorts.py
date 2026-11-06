"""Render vertical Shorts: facecam on top, gameplay below, word-by-word captions."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .media import AudioStream, MediaInfo, crop_filter, encoder_options, ffmpeg_filters, run

W, H = 1080, 1920


def pick(clips: list[dict], count: int, min_s: float) -> list[dict]:
    """Strongest clips of the edit, best first."""
    usable = [c for c in clips if c["end"] - c["start"] >= min_s]
    return sorted(usable, key=lambda c: c.get("final_score", c["score"]), reverse=True)[:count]


def word_timestamps(pcm: np.ndarray, sample_rate: int, start: float, end: float, cfg: dict, asr) -> list[dict]:
    audio = np.asarray(pcm[int(start * sample_rate):int(end * sample_rate)], dtype=np.float32) / 32768.0
    res = asr({"raw": audio, "sampling_rate": sample_rate}, return_timestamps="word",
              generate_kwargs={"language": cfg["transcribe"]["language"], "task": "transcribe"})
    words = []
    for ch in res.get("chunks", []):
        a, b = ch["timestamp"]
        text = ch["text"].strip()
        if text and a is not None:
            words.append({"start": a, "end": b if b is not None else a + 0.4, "text": text})
    return words


def _ass_time(t: float) -> str:
    t = max(t, 0.0)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def _clean(word: str) -> str:
    return word.strip(" ,.;:").upper()


def _ass_color(hex_rgb: str) -> str:
    r, g, b = hex_rgb.lstrip("#")[0:2], hex_rgb.lstrip("#")[2:4], hex_rgb.lstrip("#")[4:6]
    return f"&H00{b}{g}{r}".upper()


def write_ass(words: list[dict], path: Path, scfg: dict, caption_y: int) -> None:
    """Groups of a few words; the word being spoken is highlighted."""
    hi = _ass_color(scfg["highlight_color"])
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 2", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        f"Style: Cap,{scfg['font']},{scfg['font_size']},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,"
        f"100,100,2,0,1,{scfg['outline']},3,5,60,60,0,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    n = scfg["words_per_caption"]
    for g in range(0, len(words), n):
        group = words[g:g + n]
        for i, w in enumerate(group):
            # Whisper sometimes stretches a word back into the silence before it.
            start = max(w["start"], w["end"] - 0.8)
            end = group[i + 1]["start"] if i + 1 < len(group) else w["end"] + 0.25
            end = min(end, w["end"] + 0.6)  # do not keep text on screen through a pause
            text = " ".join(
                (f"{{\\c{hi}}}{_clean(x['text'])}{{\\c&H00FFFFFF}}" if j == i else _clean(x["text"]))
                for j, x in enumerate(group)
            )
            lines.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Cap,,0,0,0,,"
                         f"{{\\pos({W // 2},{caption_y})}}{text}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _filter_path(p: Path) -> str:
    """Escape a Windows path for use inside an ffmpeg filter argument."""
    return p.resolve().as_posix().replace(":", r"\:").replace("'", r"\'")


def _ass_seconds(value: str) -> float:
    h, m, s = value.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def _caption_backend(scfg: dict) -> str:
    backend = scfg.get("caption_backend", "auto")
    if backend == "auto":
        return "subtitles" if "subtitles" in ffmpeg_filters() else "png_video"
    if backend not in {"subtitles", "png_video"}:
        raise ValueError(f"Unknown shorts.caption_backend: {backend}")
    return backend


def _png_caption_video(ass: Path, scfg: dict, duration: float) -> Path:
    """Render captions to one small alpha MOV; avoids one FFmpeg filter graph per word."""
    events = []
    for line in ass.read_text(encoding="utf-8").splitlines():
        if not line.startswith("Dialogue:"):
            continue
        parts = line.split(",", 9)
        if len(parts) != 10:
            continue
        raw = parts[9]
        highlighted = re.search(r"\{\\c&H[0-9A-F]+\}(.+?)\{\\c&H00FFFFFF\}", raw, re.IGNORECASE)
        events.append((_ass_seconds(parts[1]), _ass_seconds(parts[2]),
                       re.sub(r"\{[^}]*\}", "", raw).strip(),
                       highlighted.group(1) if highlighted else ""))

    mov = ass.with_suffix(".captions.mov")
    font_name = str(scfg.get("font") or "Arial Bold")
    candidates = [
        Path(scfg.get("fonts_dir") or "") / f"{font_name}.ttf",
        Path(font_name),
        Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
        Path("/Library/Fonts/Arial Bold.ttf"),
        Path("C:/Windows/Fonts/arialbd.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ]
    font_path = next((path for path in candidates if path.is_file()), None)
    if font_path is None:
        raise RuntimeError(f"Caption font '{font_name}' was not found; set shorts.fonts_dir in the config")
    font = ImageFont.truetype(str(font_path), int(scfg["font_size"]))
    images = []
    for _, _, text, highlight in events:
        image = Image.new("RGBA", (W, 260), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        words = text.split()
        widths = [draw.textlength(w, font=font) for w in words]
        space = draw.textlength(" ", font=font)
        x = (W - sum(widths) - space * max(0, len(words) - 1)) / 2
        target, used = highlight.strip(), False
        for word, width in zip(words, widths):
            selected = not used and word == target
            draw.text((x, 45), word, font=font, fill=scfg["highlight_color"] if selected else "white",
                      stroke_width=int(scfg["outline"]), stroke_fill="black")
            used = used or selected
            x += width + space
        images.append(image)

    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba",
           "-s", f"{W}x260", "-r", "30", "-i", "-", "-an", "-c:v", "qtrle", "-pix_fmt", "argb", str(mov)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    assert proc.stdin is not None
    blank = Image.new("RGBA", (W, 260), (0, 0, 0, 0)).tobytes()
    index = 0
    for frame in range(max(1, round(duration * 30))):
        t = frame / 30
        while index < len(events) and t > events[index][1]:
            index += 1
        data = images[index].tobytes() if index < len(events) and events[index][0] <= t <= events[index][1] else blank
        proc.stdin.write(data)
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("Could not render PNG caption video")
    return mov


def render(info: MediaInfo, clip: dict, n: int, streams: dict[str, AudioStream], ass: Path, cfg: dict,
           out_dir: Path, gpu: int | None = None) -> Path:
    scfg = cfg["shorts"]
    out = out_dir / f"short_{n:02d}.mp4"
    start = clip["start"]
    dur = min(clip["end"] - clip["start"], scfg["max_s"])
    lay = cfg["layout"]

    if lay.get("facecam"):
        cam_h = round(W * 9 / 16 / 2) * 2                      # 16:9 camera across the full width
        game_h = H - cam_h
        graph = (f"[0:v]{crop_filter(lay['facecam'], info.width, info.height)},scale={W}:{cam_h}:force_original_aspect_ratio=increase,crop={W}:{cam_h}[cam];"
                 f"[0:v]{crop_filter(lay['gameplay'], info.width, info.height)},"
                 f"crop=ih*{W}/{game_h}:ih,scale={W}:{game_h}[game];[cam][game]vstack,setsar=1[stack];")
        src = "[stack]"
    else:
        graph = f"[0:v]{crop_filter(lay['gameplay'], info.width, info.height)},crop=ih*9/16:ih,scale={W}:{H},setsar=1[stack];"
        src = "[stack]"
    caption_video = None
    caption_backend = _caption_backend(scfg)
    if caption_backend == "png_video":
        caption_video = _png_caption_video(ass, scfg, dur)
        graph += f"{src}[1:v]overlay=0:{max(0, caption_y(cfg) - 130)}:eof_action=pass,setsar=1[v];"
    else:
        sub = f"subtitles=filename='{_filter_path(ass)}'"
        if scfg.get("fonts_dir"):
            sub += f":fontsdir='{_filter_path(Path(scfg['fonts_dir']))}'"
        graph += f"{src}{sub}[v];"
    audio_in = "".join(f"[0:{s.index}]" for s in streams.values())
    graph += f"{audio_in}amix=inputs={len(streams)}:normalize=0:dropout_transition=0[a]"

    inputs = ["-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(info.path)]
    if caption_video:
        inputs += ["-i", str(caption_video)]
    run(["ffmpeg", "-hide_banner", "-v", "error", "-y", *inputs,
         "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
         *encoder_options(cfg["render"]["codec"], 20, gpu, cfg["render"].get("video_bitrate")),
         "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)])
    return out


def caption_y(cfg: dict) -> int:
    """Captions sit on the edge between facecam and gameplay."""
    if cfg["layout"].get("facecam"):
        return round(W * 9 / 16 / 2) * 2 + 40
    return int(H * 0.7)
