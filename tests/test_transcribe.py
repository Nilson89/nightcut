import sys
from types import SimpleNamespace

from nightcut.transcribe import free_vram, load_asr


def test_load_asr_selects_mps(monkeypatch):
    captured = {}
    fake_torch = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: False),
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: True)),
        float16="float16",
        float32="float32",
    )
    fake_transformers = SimpleNamespace(
        pipeline=lambda task, **kwargs: captured.update(task=task, **kwargs) or "pipeline")
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "transformers", fake_transformers)

    result = load_asr({"model": "whisper-test"})

    assert result == "pipeline"
    assert captured == {"task": "automatic-speech-recognition", "model": "whisper-test",
                        "torch_dtype": "float16", "device": "mps"}


def test_free_vram_releases_mps_cache(monkeypatch):
    calls = []
    fake_torch = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: False),
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: True)),
        mps=SimpleNamespace(empty_cache=lambda: calls.append("mps")),
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    free_vram()

    assert calls == ["mps"]
