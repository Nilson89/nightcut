"""Write a Final Cut Pro 7 XML (xmeml) timeline.

xmeml is the one interchange format both DaVinci Resolve and Premiere Pro
import natively (Resolve: File > Import > Timeline, Premiere: File > Import).
"""

from __future__ import annotations

import os
import re
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

from .media import probe


def _rate(parent: ET.Element, fps: float) -> None:
    rate = ET.SubElement(parent, "rate")
    ET.SubElement(rate, "timebase").text = str(round(fps))
    ET.SubElement(rate, "ntsc").text = "TRUE" if abs(fps - round(fps)) > 0.01 else "FALSE"


def _sub(parent: ET.Element, tag: str, text: str | int | None = None, **attrs) -> ET.Element:
    el = ET.SubElement(parent, tag, attrs)
    if text is not None:
        el.text = str(text)
    return el


def _pathurl(path: str) -> str:
    # abspath, not resolve(): resolve() turns a mapped drive (Z:) into a UNC path editors cannot open.
    # Keep Windows drive paths intact even when an XML is generated or tested on another OS.
    p = path.replace("\\", "/") if re.match(r"^[A-Za-z]:[\\/]", path) else Path(os.path.abspath(path)).as_posix()
    if p.startswith("//"):  # UNC share: file://server/share/...
        return "file:" + quote(p, safe="/:")
    return "file://localhost/" + quote(p, safe="/:")


def _timecode(parent: ET.Element, fps: float) -> None:
    tc = _sub(parent, "timecode")
    _rate(tc, fps)
    _sub(tc, "string", "00:00:00:00")
    _sub(tc, "frame", 0)
    _sub(tc, "displayformat", "NDF")


def _video_sc(parent: ET.Element, fps: float, width: int, height: int) -> None:
    sc = _sub(parent, "samplecharacteristics")
    _rate(sc, fps)
    _sub(sc, "width", width)
    _sub(sc, "height", height)
    _sub(sc, "anamorphic", "FALSE")
    _sub(sc, "pixelaspectratio", "square")
    _sub(sc, "fielddominance", "none")


class _Files:
    """First use of a file writes the full <file> element, later uses only reference it."""

    def __init__(self, fps: float):
        self.fps = fps
        self.seen: set[str] = set()
        self._durations: dict[str, int] = {}

    def duration(self, path: str) -> int:
        if path not in self._durations:
            self._durations[path] = round(probe(Path(path)).duration * self.fps)
        return self._durations[path]

    def add(self, parent: ET.Element, path: str, n_audio: int) -> None:
        fid = "file-" + Path(path).stem
        if fid in self.seen:
            _sub(parent, "file", id=fid)
            return
        self.seen.add(fid)
        info = probe(Path(path))
        f = _sub(parent, "file", id=fid)
        _sub(f, "name", Path(path).name)
        _sub(f, "pathurl", _pathurl(path))
        _rate(f, self.fps)
        _sub(f, "duration", round(info.duration * self.fps))
        _timecode(f, self.fps)
        media = _sub(f, "media")
        _video_sc(_sub(media, "video"), self.fps, info.width, info.height)
        for _ in range(n_audio):
            audio = _sub(media, "audio")
            asc = _sub(audio, "samplecharacteristics")
            _sub(asc, "depth", 16)
            _sub(asc, "samplerate", 48000)
            _sub(audio, "channelcount", 2)


def _clipitem(track: ET.Element, cid: str, name: str, fps: float, start: int, end: int, src_in: int,
              file_path: str, files: _Files, n_audio: int, mediatype: str, trackindex: int) -> ET.Element:
    ci = _sub(track, "clipitem", id=cid)
    _sub(ci, "masterclipid", "master-" + Path(file_path).stem)
    _sub(ci, "name", name)
    _sub(ci, "enabled", "TRUE")
    _sub(ci, "duration", files.duration(file_path))
    _rate(ci, fps)
    _sub(ci, "start", start)
    _sub(ci, "end", end)
    _sub(ci, "in", src_in)
    _sub(ci, "out", src_in + (end - start))
    files.add(ci, file_path, n_audio)
    st = _sub(ci, "sourcetrack")
    _sub(st, "mediatype", mediatype)
    _sub(st, "trackindex", trackindex)
    return ci


def _pip_filter(ci: ET.Element, pip: dict, seq_w: int, seq_h: int, cam_w: int, cam_h: int) -> None:
    """Basic Motion: scale and move the facecam into a corner.

    xmeml 'center' is relative to the frame centre, in units of half the frame size.
    """
    scale = pip["scale"] / 100
    half_w = cam_w * scale / seq_w   # half width of the PiP in half-frame units
    half_h = cam_h * scale / seq_h
    margin = pip["margin"] * 2
    horiz = 1 - margin - half_w
    vert = 1 - margin * seq_w / seq_h - half_h
    if "left" in pip["corner"]:
        horiz = -horiz
    if "top" in pip["corner"]:
        vert = -vert
    effect = _sub(_sub(ci, "filter"), "effect")
    _sub(effect, "name", "Basic Motion")
    _sub(effect, "effectid", "basic")
    _sub(effect, "effectcategory", "motion")
    _sub(effect, "effecttype", "motion")
    _sub(effect, "mediatype", "video")
    p = _sub(effect, "parameter")
    _sub(p, "parameterid", "scale")
    _sub(p, "name", "Scale")
    _sub(p, "value", round(pip["scale"], 2))
    p = _sub(effect, "parameter")
    _sub(p, "parameterid", "center")
    _sub(p, "name", "Center")
    v = _sub(p, "value")
    _sub(v, "horiz", round(horiz / 2, 4))
    _sub(v, "vert", round(vert / 2, 4))


def write(clips: list[dict], audio_names: list[str], cfg: dict, out_path: Path, sequence_name: str) -> Path:
    first = probe(Path(clips[0]["gameplay_file"]))
    fps = first.fps
    seq_w, seq_h = first.width, first.height
    n_audio = len(audio_names)
    files = _Files(fps)

    root = ET.Element("xmeml", version="4")
    seq = _sub(root, "sequence", id="sequence-1")
    _sub(seq, "uuid", str(uuid.uuid4()))
    seq_duration = _sub(seq, "duration")
    _rate(seq, fps)
    _sub(seq, "name", sequence_name)
    media = _sub(seq, "media")

    video = _sub(media, "video")
    _video_sc(_sub(video, "format"), fps, seq_w, seq_h)
    v1 = _sub(video, "track")
    v2 = _sub(video, "track") if clips[0].get("facecam_file") else None

    audio = _sub(media, "audio")
    asc = _sub(_sub(audio, "format"), "samplecharacteristics")
    _sub(asc, "depth", 16)
    _sub(asc, "samplerate", 48000)
    a_tracks = [_sub(audio, "track") for _ in audio_names]

    pip = cfg["export"]["facecam_pip"]
    cam = probe(Path(clips[0]["facecam_file"])) if v2 is not None else None
    t = 0
    for c in clips:
        length = round((c["end"] - c["start"]) * fps)
        src_in = round(c["trim_in"] * fps)
        name = f"{c['n']:02d} {c.get('title') or 'Highlight'}"
        vi = _clipitem(v1, f"v1-{c['n']}", name, fps, t, t + length, src_in, c["gameplay_file"], files, n_audio, "video", 1)
        if "score" in c:  # highlight clips carry a marker with the reason; Let's Play pieces do not
            marker = _sub(vi, "marker")
            _sub(marker, "name", c.get("title") or "Highlight")
            _sub(marker, "comment", f"score {c.get('final_score', c['score'])} | {c.get('reason', '')}")
            _sub(marker, "in", src_in)
            _sub(marker, "out", -1)
        if v2 is not None:
            ci = _clipitem(v2, f"v2-{c['n']}", f"{name} (facecam)", fps, t, t + length, src_in,
                           c["facecam_file"], files, 0, "video", 1)
            if pip.get("enabled", True):
                _pip_filter(ci, pip, seq_w, seq_h, cam.width, cam.height)
        for idx, track in enumerate(a_tracks, 1):
            _clipitem(track, f"a{idx}-{c['n']}", f"{name} {audio_names[idx - 1].upper()}", fps, t, t + length,
                      src_in, c["gameplay_file"], files, n_audio, "audio", idx)
        t += length
    seq_duration.text = str(t)
    _timecode(seq, fps)

    ET.indent(root)
    out_path.write_bytes(b'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + ET.tostring(root, encoding="utf-8"))
    return out_path
