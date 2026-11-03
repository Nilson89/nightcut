"""Several NVIDIA GPUs: one Ollama server per card and a shared work queue.

The kill feed scan and the vision check are hundreds of independent model calls. With two
cards, each card runs its own Ollama server and both take the next call from the same queue,
so the faster card simply does more of them. Whisper and the Shorts run on the second card
while the first one is busy with the vision model.
"""

from __future__ import annotations

import os
import queue
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

import requests

# nvidia-smi counts GPUs in PCI order; make CUDA (PyTorch, ffmpeg, Ollama) count the same way.
os.environ.setdefault("CUDA_DEVICE_ORDER", "PCI_BUS_ID")


def list_gpus() -> list[dict]:
    """NVIDIA GPUs as nvidia-smi reports them: index, name, memory in MiB."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=index,name,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    gpus = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3 and parts[0].isdigit():
            gpus.append({"index": int(parts[0]), "name": parts[1], "memory_mib": int(float(parts[2]))})
    return gpus


def choose(available: list[dict], gcfg: dict | None, use: int | str | None = None) -> list[dict]:
    """GPUs for this run, main GPU first.

    use: "auto" (all cards if there are two or more), or a number of cards.
    gcfg["devices"]: nvidia-smi indices in order of importance; default is PCI order (first slot = main card).
    """
    gcfg = gcfg or {}
    use = gcfg.get("use", "auto") if use is None else use
    by_index = {g["index"]: g for g in available}
    order = [dict(by_index[i]) for i in (gcfg.get("devices") or []) if i in by_index] or list(available)
    if use in (None, "auto"):
        return order
    return order[:max(1, int(use))]


def _free_port(start: int) -> int:
    port = start
    while port < start + 100:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
        port += 1
    raise RuntimeError(f"No free port for Ollama between {start} and {start + 100}")


class OllamaServers:
    """Start one `ollama serve` per GPU (each sees only its own card) and stop them afterwards."""

    def __init__(self, gpus: list[dict], ocfg: dict, first_port: int, log_dir: Path):
        self.gpus, self.ocfg, self.first_port, self.log_dir = gpus, ocfg, first_port, log_dir
        self.procs: list[subprocess.Popen] = []
        self.endpoints: list[dict] = []  # ollama config per GPU (same as cfg["ollama"], other url)

    def __enter__(self) -> "OllamaServers":
        self.log_dir.mkdir(parents=True, exist_ok=True)
        port = self.first_port
        try:
            for n, g in enumerate(self.gpus):
                port = _free_port(port)
                env = {**os.environ, "CUDA_VISIBLE_DEVICES": str(g["index"]), "CUDA_DEVICE_ORDER": "PCI_BUS_ID",
                       "OLLAMA_HOST": f"127.0.0.1:{port}", "OLLAMA_NUM_PARALLEL": "1"}
                log = open(self.log_dir / f"ollama_{n + 1}_gpu{g['index']}.log", "w", encoding="utf-8")
                flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                self.procs.append(subprocess.Popen(["ollama", "serve"], env=env, stdout=log, stderr=subprocess.STDOUT,
                                                   creationflags=flags))
                url = f"http://127.0.0.1:{port}"
                self._wait(url)
                self.endpoints.append({**self.ocfg, "url": url, "gpu": g["index"], "gpu_name": g["name"]})
                port += 1
        except BaseException:
            self.__exit__()
            raise
        return self

    @staticmethod
    def _wait(url: str, timeout_s: float = 60) -> None:
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            try:
                if requests.get(f"{url}/api/version", timeout=2).ok:
                    return
            except requests.RequestException:
                pass
            time.sleep(0.5)
        raise RuntimeError(f"Ollama did not start at {url}. Is Ollama installed (ollama in PATH)?")

    def __exit__(self, *exc) -> None:
        for p in self.procs:
            if p.poll() is not None:
                continue
            if sys.platform == "win32":  # the server starts model runners as child processes: stop them too
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
            else:
                p.terminate()
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        self.procs.clear()


def parallel_map(items: list, workers: list, fn: Callable, ready: list[threading.Event | None] | None = None,
                 progress: Callable[[int, int], None] | None = None, per_worker: list[int] | None = None) -> list:
    """Run fn(item, worker) for all items, one thread per worker, all taking from one queue.

    ready[i]: worker i only starts once this event is set (e.g. the second GPU after the transcript).
    per_worker: gets how many items each worker did (shown in the report: who did how much).
    Results keep the order of items. The first error stops all workers and is raised.
    """
    if per_worker is not None:
        per_worker[:] = [0] * len(workers)
    todo: queue.Queue = queue.Queue()
    for i, item in enumerate(items):
        todo.put((i, item))
    results: list = [None] * len(items)
    errors: list[BaseException] = []
    stop = threading.Event()
    lock = threading.Lock()
    done = [0]

    def work(n: int, worker) -> None:
        event = ready[n] if ready and n < len(ready) else None
        while event is not None and not event.wait(0.5):
            if stop.is_set() or todo.empty():
                return
        while not stop.is_set():
            try:
                i, item = todo.get_nowait()
            except queue.Empty:
                return
            try:
                results[i] = fn(item, worker)
            except BaseException as exc:  # noqa: BLE001 - re-raised in the main thread
                with lock:
                    errors.append(exc)
                stop.set()
                return
            with lock:
                done[0] += 1
                if per_worker is not None:
                    per_worker[n] += 1
                if progress:
                    progress(done[0], len(items))

    threads = [threading.Thread(target=work, args=(n, w), daemon=True) for n, w in enumerate(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if errors:
        raise errors[0]
    return results
