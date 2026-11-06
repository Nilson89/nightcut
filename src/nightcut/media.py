"""ffprobe/ffmpeg helpers: probing, audio extraction, frame grabs."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import numpy as np


@dataclass
class AudioStream:
    index: int          # absolute stream index in the container
    position: int       # position among audio streams (0-based)
    title: str
    channels: int
    sample_rate: int


@dataclass
class MediaInfo:
    path: Path
    duration: float
    width: int
    height: int
    fps: float
    audio: list[AudioStream]

    def find_audio(self, spec: dict | None) -> AudioStream | None:
        """Match an audio stream by title first, then by audio position.

        `title` may be one name or a list of names. An exact match wins; otherwise a stream whose
        title starts with the name as a whole word counts ("MIC" finds "MIC Main Headset",
        "DISCORD" finds "DISCORD, TS und ...").
        """
        if not spec:
            return None
        titles = spec.get("title") or []
        titles = [t.lower() for t in ([titles] if isinstance(titles, str) else titles) if t]
        for t in titles:
            for stream in self.audio:
                if stream.title.lower() == t:
                    return stream
        for t in titles:
            for stream in self.audio:
                if re.match(re.escape(t) + r"\b", stream.title.lower()):
                    return stream
        pos = spec.get("index")
        if pos is not None and 0 <= pos < len(self.audio):
            return self.audio[pos]
        return None


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, **kwargs)


def probe(path: Path) -> MediaInfo:
    out = run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=index,codec_type,width,height,r_frame_rate,channels,sample_rate:stream_tags=title",
         "-of", "json", str(path)],
        capture_output=True, text=True,
    ).stdout
    data = json.loads(out)
    video = next(s for s in data["streams"] if s["codec_type"] == "video")
    num, den = (int(x) for x in video["r_frame_rate"].split("/"))
    audio = [
        AudioStream(
            index=s["index"],
            position=pos,
            title=(s.get("tags") or {}).get("title", f"audio{pos}"),
            channels=int(s.get("channels", 2)),
            sample_rate=int(s.get("sample_rate", 48000)),
        )
        for pos, s in enumerate(x for x in data["streams"] if x["codec_type"] == "audio")
    ]
    return MediaInfo(
        path=path,
        duration=float(data["format"]["duration"]),
        width=int(video["width"]),
        height=int(video["height"]),
        fps=num / den,
        audio=audio,
    )


def extract_audio_tracks(src: Path, streams: dict[str, AudioStream], out_dir: Path, sample_rate: int) -> dict[str, Path]:
    """Decode several audio streams to mono 16-bit PCM in a single read of the source.

    Large recordings are read once instead of once per track.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs = {name: out_dir / f"{name}.pcm" for name in streams}
    todo = {name: s for name, s in streams.items() if not outputs[name].exists()}
    if todo:
        cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-i", str(src)]
        for name, stream in todo.items():
            tmp = outputs[name].with_suffix(".pcm.part")
            cmd += ["-map", f"0:{stream.index}", "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", str(tmp)]
        run(cmd)
        for name, stream in todo.items():
            part = outputs[name].with_suffix(".pcm.part")
            if part.stat().st_size == 0:
                raise RuntimeError(f"Audio stream '{stream.title}' (#{stream.index}) contains no audio. "
                                   "Check the 'audio' section of your config.")
            part.replace(outputs[name])
    return outputs


def load_pcm(path: Path) -> np.ndarray:
    """Memory-map a mono s16le file."""
    return np.memmap(path, dtype=np.int16, mode="r")


def grab_frames(src: Path, times: list[float], region: dict, src_w: int, src_h: int, width: int) -> list[bytes]:
    """Return JPEG bytes for each timestamp, cropped to a layout region."""
    crop = crop_filter(region, src_w, src_h)
    frames = []
    for t in times:
        out = run(
            ["ffmpeg", "-hide_banner", "-v", "error", "-ss", f"{max(t, 0):.3f}", "-i", str(src),
             "-frames:v", "1", "-vf", f"{crop},scale={width}:-2", "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "4", "-"],
            capture_output=True,
        ).stdout
        if out:
            frames.append(out)
    return frames


def encoder_gpu(codec: str, gpu: int | None) -> list[str]:
    """ffmpeg option that puts an NVENC encoder on a given GPU (index in PCI order, like nvidia-smi)."""
    return ["-gpu", str(gpu)] if gpu is not None and "nvenc" in codec else []


@cache
def ffmpeg_encoders() -> set[str]:
    """Names of video encoders available in the active FFmpeg build."""
    try:
        out = run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    return {line.split()[1] for line in out.splitlines() if len(line.split()) >= 2 and line.lstrip().startswith("V")}


@cache
def ffmpeg_filters() -> set[str]:
    """Names of filters available in the active FFmpeg build."""
    try:
        out = run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    return {line.split()[1] for line in out.splitlines() if len(line.split()) >= 2 and line.lstrip()[:1] in ".TSC"}


@cache
def nvidia_runtime_available() -> bool:
    """True only when an NVIDIA driver and at least one usable GPU are present."""
    try:
        out = run(["nvidia-smi", "-L"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "GPU " in out


def resolve_encoder(codec: str) -> str:
    """Resolve `auto` to VideoToolbox on macOS, NVENC elsewhere, then software x264."""
    if codec != "auto":
        return codec
    available = ffmpeg_encoders()
    preferred = "h264_videotoolbox" if sys.platform == "darwin" else "h264_nvenc"
    if preferred in available and (preferred != "h264_nvenc" or nvidia_runtime_available()):
        return preferred
    if "libx264" in available:
        return "libx264"
    raise RuntimeError("No supported H.264 encoder found in FFmpeg (tried VideoToolbox, NVENC and libx264)")


def encoder_options(codec: str, quality: int, gpu: int | None = None, bitrate: str | None = None) -> list[str]:
    """Portable ffmpeg encoder options for NVIDIA, macOS VideoToolbox, or software x264."""
    codec = resolve_encoder(codec)
    common = ["-c:v", codec, *encoder_gpu(codec, gpu)]
    if "nvenc" in codec:
        return common + ["-preset", "p5", "-rc", "vbr", "-cq", str(quality), "-b:v", "0"]
    if codec == "libx264":
        return common + ["-preset", "medium", "-crf", str(quality)]
    if "videotoolbox" in codec:
        return common + ["-b:v", bitrate or "12M", "-allow_sw", "1", "-realtime", "0"]
    return common


def region_pixels(region: dict, src_w: int, src_h: int) -> tuple[int, int, int, int]:
    """Fractional region -> even pixel values (x, y, w, h)."""
    def even(v: float) -> int:
        return int(round(v / 2)) * 2
    return (even(region["x"] * src_w), even(region["y"] * src_h),
            even(region["w"] * src_w), even(region["h"] * src_h))


def crop_filter(region: dict, src_w: int, src_h: int) -> str:
    x, y, w, h = region_pixels(region, src_w, src_h)
    return f"crop={w}:{h}:{x}:{y}"


def keyframe_scan(src: Path, region: dict, src_w: int, src_h: int, mask: dict | None = None,
                  size: tuple[int, int] = (64, 36)) -> dict[str, np.ndarray]:
    """Tiny grey thumbnails of every keyframe -> time, brightness and change to the previous keyframe.

    Only keyframes are decoded, so hours of video take minutes. `mask` is a region of the
    full frame to ignore (a facecam burned into the picture would always look "moving").
    """
    w, h = size
    proc = run(["ffmpeg", "-hide_banner", "-nostats", "-v", "info", "-skip_frame", "nokey", "-i", str(src),
                "-map", "0:v:0", "-vf", f"{crop_filter(region, src_w, src_h)},scale={w}:{h},format=gray,showinfo",
                "-fps_mode", "passthrough", "-f", "rawvideo", "-"], capture_output=True)
    frames = np.frombuffer(proc.stdout, np.uint8).reshape(-1, h, w).astype(np.float32)
    times = np.array([float(t) for t in re.findall(rb"pts_time:\s*(-?[\d.]+)", proc.stderr)], dtype=np.float32)
    n = min(len(frames), len(times))
    frames, times = frames[:n], times[:n]
    keep = np.ones((h, w), dtype=bool)
    if mask:
        # mask is given in full-frame fractions; map it into the scanned region
        mx = (mask["x"] - region["x"]) / region["w"]
        my = (mask["y"] - region["y"]) / region["h"]
        x0, y0 = max(int(mx * w), 0), max(int(my * h), 0)
        x1, y1 = min(int((mx + mask["w"] / region["w"]) * w) + 1, w), min(int((my + mask["h"] / region["h"]) * h) + 1, h)
        keep[y0:y1, x0:x1] = False
    pixels = frames[:, keep]
    luma = pixels.mean(axis=1)
    motion = np.full(n, 255.0, dtype=np.float32)
    motion[1:] = np.abs(np.diff(pixels, axis=0)).mean(axis=1)
    return {"time": times, "luma": luma.astype(np.float32), "motion": motion}
