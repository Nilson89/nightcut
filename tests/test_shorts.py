import pytest

from nightcut import shorts


def test_caption_backend_uses_libass_when_available(monkeypatch):
    monkeypatch.setattr(shorts, "ffmpeg_filters", lambda: {"scale", "subtitles"})
    assert shorts._caption_backend({"caption_backend": "auto"}) == "subtitles"


def test_caption_backend_uses_portable_fallback_without_libass(monkeypatch):
    monkeypatch.setattr(shorts, "ffmpeg_filters", lambda: {"scale"})
    assert shorts._caption_backend({"caption_backend": "auto"}) == "png_video"


def test_caption_backend_rejects_unknown_value():
    with pytest.raises(ValueError, match="caption_backend"):
        shorts._caption_backend({"caption_backend": "one_png_per_word"})