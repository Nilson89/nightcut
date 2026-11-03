"""Minimal Ollama client for JSON answers from text and vision models."""

from __future__ import annotations

import base64
import json

import requests

from .costs import usage


class OllamaError(RuntimeError):
    """Setup problem (Ollama down, model missing): stop and tell the user."""


class BadAnswer(ValueError):
    """The model answered, but not with usable JSON: skip this one call."""


def chat_json(cfg: dict, model: str, system: str, user: str, images: list[bytes] | None = None,
              temperature: float = 0.2, keep_loaded: bool = False, max_tokens: int | None = None) -> dict:
    """Send one chat turn and parse the JSON reply.

    keep_loaded: for loops of many calls. Reloading the model for every call costs
    seconds each time; the caller unloads it with unload() when the loop is done.
    max_tokens: stops a model that got stuck repeating itself.
    """
    message = {"role": "user", "content": user}
    if images:
        message["images"] = [base64.b64encode(img).decode() for img in images]
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, message],
        "format": "json",
        "stream": False,
        "think": False,
        "keep_alive": "10m" if keep_loaded else cfg["keep_alive"],
        "options": {"temperature": temperature, "num_ctx": 16384,
                    **({"num_predict": max_tokens} if max_tokens else {})},
    }
    try:
        resp = requests.post(f"{cfg['url']}/api/chat", json=payload, timeout=cfg["timeout_s"])
    except requests.ConnectionError as exc:
        raise OllamaError(f"Ollama is not reachable at {cfg['url']}. Is it running?") from exc
    if resp.status_code == 404:
        raise OllamaError(f"Model '{model}' not found. Run: ollama pull {model}")
    resp.raise_for_status()
    data = resp.json()
    usage.add_llm(data.get("prompt_eval_count", 0), data.get("eval_count", 0), len(images or []))
    content = data["message"]["content"]
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise BadAnswer(f"Model returned invalid JSON: {content[:300]}") from exc


def unload(cfg: dict, model: str) -> None:
    """Free the model's VRAM right away."""
    try:
        requests.post(f"{cfg['url']}/api/generate", json={"model": model, "keep_alive": 0}, timeout=30)
    except requests.RequestException:
        pass
