import threading
import time

import pytest

from nightcut import costs
from nightcut.gpus import choose, parallel_map

GPUS = [{"index": 0, "name": "RTX 5080", "memory_mib": 16303}, {"index": 1, "name": "RTX 5060 Ti", "memory_mib": 16311}]


def test_choose_auto_uses_all_cards_in_pci_order():
    assert [g["index"] for g in choose(GPUS, {"use": "auto"})] == [0, 1]


def test_choose_one_card_and_own_order():
    assert [g["index"] for g in choose(GPUS, {"use": "auto"}, use="1")] == [0]
    assert [g["index"] for g in choose(GPUS, {"devices": [1, 0]})] == [1, 0]
    assert [g["index"] for g in choose(GPUS, {"devices": [1]}, use=1)] == [1]


def test_choose_same_card_twice_for_testing():
    assert [g["index"] for g in choose(GPUS[:1], {"devices": [0, 0]})] == [0, 0]


def test_parallel_map_keeps_order_and_uses_all_workers():
    used = set()
    lock = threading.Lock()

    def fn(item, worker):
        with lock:
            used.add(worker)
        time.sleep(0.01)
        return item * 10

    assert parallel_map(list(range(20)), ["a", "b"], fn) == [i * 10 for i in range(20)]
    assert used == {"a", "b"}


def test_parallel_map_second_worker_joins_later():
    gate = threading.Event()
    by_worker = {"main": [], "second": []}

    def fn(item, worker):
        by_worker[worker].append(item)
        if item == 3:
            gate.set()  # e.g. the transcript is done: the second card may join
        time.sleep(0.02)
        return item

    parallel_map(list(range(12)), ["main", "second"], fn, ready=[None, gate])
    assert by_worker["main"][:4] == [0, 1, 2, 3]   # alone until the gate opened
    assert by_worker["second"] and min(by_worker["second"]) > 3


def test_parallel_map_raises_first_error():
    def fn(item, worker):
        if item == 5:
            raise RuntimeError("Ollama down")
        return item

    with pytest.raises(RuntimeError, match="Ollama down"):
        parallel_map(list(range(10)), [0, 1], fn)


def test_progress_counts_every_item():
    seen = []
    parallel_map(list(range(7)), [0, 1], lambda i, w: i, progress=lambda d, n: seen.append((d, n)))
    assert sorted(seen) == [(d, 7) for d in range(1, 8)]


def test_total_seconds_skips_stages_that_ran_at_the_same_time():
    stages = {"audio": {"seconds": 60}, "transcript": {"seconds": 300, "parallel": "killfeed"},
              "killfeed": {"seconds": 1200}, "vision": {"seconds": 400}}
    assert costs.total_seconds(stages) == 60 + 1200 + 400
