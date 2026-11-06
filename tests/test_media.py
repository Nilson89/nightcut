from pathlib import Path

from nightcut import media
from nightcut.media import AudioStream, MediaInfo, encoder_options, resolve_encoder

OBS = ["GAME", "MIC Main Headset", "MIC Tonor", "DESKTOP", "DISCORD, TS und TIPEESTREAM", "STREAM"]
INFO = MediaInfo(Path("x.mkv"), 60.0, 2560, 1440, 60.0,
                 [AudioStream(i + 1, i, t, 2, 48000) for i, t in enumerate(OBS)])


def test_exact_title_wins():
    assert INFO.find_audio({"title": "GAME", "index": 2}).title == "GAME"


def test_title_matches_start_of_longer_name():
    assert INFO.find_audio({"title": "MIC", "index": 5}).title == "MIC Main Headset"
    assert INFO.find_audio({"title": "MIC Tonor", "index": 0}).title == "MIC Tonor"


def test_title_list_and_whole_words_only():
    spec = {"title": ["PARTY", "DISCORD", "TEAMSPEAK"], "index": 3}
    assert INFO.find_audio(spec).title.startswith("DISCORD")
    # "STREAM" must not be found by a name that is only part of a word
    assert INFO.find_audio({"title": "STREA", "index": None}) is None


def test_falls_back_to_position():
    assert INFO.find_audio({"title": "PARTY", "index": 3}).title == "DESKTOP"
    assert INFO.find_audio(None) is None


def test_encoder_options_are_codec_specific():
    assert encoder_options("h264_nvenc", 18, 1) == [
        "-c:v", "h264_nvenc", "-gpu", "1", "-preset", "p5", "-rc", "vbr", "-cq", "18", "-b:v", "0"]
    assert encoder_options("libx264", 20) == ["-c:v", "libx264", "-preset", "medium", "-crf", "20"]
    assert encoder_options("h264_videotoolbox", 20, bitrate="8M") == [
        "-c:v", "h264_videotoolbox", "-b:v", "8M", "-allow_sw", "1", "-realtime", "0"]


def test_auto_encoder_prefers_videotoolbox_on_macos(monkeypatch):
    monkeypatch.setattr(media.sys, "platform", "darwin")
    monkeypatch.setattr(media, "ffmpeg_encoders", lambda: {"h264_videotoolbox", "libx264"})
    assert resolve_encoder("auto") == "h264_videotoolbox"


def test_auto_encoder_falls_back_to_libx264(monkeypatch):
    monkeypatch.setattr(media.sys, "platform", "linux")
    monkeypatch.setattr(media, "ffmpeg_encoders", lambda: {"h264_nvenc", "libx264"})
    monkeypatch.setattr(media, "nvidia_runtime_available", lambda: False)
    assert resolve_encoder("auto") == "libx264"


def test_auto_encoder_uses_nvenc_with_nvidia_runtime(monkeypatch):
    monkeypatch.setattr(media.sys, "platform", "linux")
    monkeypatch.setattr(media, "ffmpeg_encoders", lambda: {"h264_nvenc", "libx264"})
    monkeypatch.setattr(media, "nvidia_runtime_available", lambda: True)
    assert resolve_encoder("auto") == "h264_nvenc"
