"""Transport-behavior spec for CostTracker (PR 1 target behavior).

TDD red-phase: these tests encode the POST-rebuild contract. Several fail
against the current tracker by design — each failure is a live transport bug:

  1. 500 responses are silently treated as delivered (raise_for_status missing,
     transient-retry branch unreachable dead code).
  2. Timeouts (ReadTimeout et al.) are dropped, not retried — only ConnectError
     re-buffers today.
  3. The buffer is flushed as one unchunked POST; a backlog above the backend's
     batch cap would 413 and be destroyed.
  4. One schema-invalid event 422s the whole envelope and silently destroys the
     valid events batched with it.
  5. Hitting batch_size runs a synchronous POST on the caller's thread — inside
     the user's LLM call path.
  6. The buffer is unbounded — a down backend grows memory forever, with no
     drop counter.
  7. close() never joins the worker thread.

Backend contract mirrored by the fake server: envelope {"events": [...]},
batch cap 1000 (CMA_MAX_EVENTS_PER_REQUEST), 422 detail loc paths
["body", "EventsEnvelope", "events", <idx>, <field>] for min_length
violations — Pydantic v2 inserts the union member's class name, and decoy
entries from the other union branches carry no index at all (verified
against the real endpoint; a loc parser must filter, not index by position).

Retry-budget contract: delivery assertions use wait_for windows of ~5s, so
the rebuilt tracker's retry schedule must attempt its first retry within a
couple of seconds (e.g. 0.5s base with full jitter) and close() must make a
final drain attempt. A conservative first-retry backoff longer than the
wait_for window is a spec violation here, not a test bug.
"""

from __future__ import annotations

import socket
import time

from cost_monitor.tracker import CostTracker


def _make_tracker(url: str, **overrides) -> CostTracker:
    kwargs = {
        "endpoint": url,
        "api_key": "test-key",
        "flush_interval": 60.0,  # timer effectively off — tests drive delivery
        "batch_size": 100_000,  # inline/threshold flush effectively off
    }
    kwargs.update(overrides)
    return CostTracker(**kwargs)


def _log_marked(tracker: CostTracker, marker: str, count: int = 1) -> None:
    for _ in range(count):
        tracker.log_event(
            agent_name=marker,
            model="gpt-4o",
            provider="openai",
            tokens_input=10,
            tokens_output=5,
        )


def test_500_is_retried_until_delivered(fake_backend):
    """A 5xx answer must re-buffer the batch and retry — not silently drop.

    Red today: flush() never raises on HTTP status, so the 500-answered POST
    is treated as delivered and the event appears in exactly one request.
    """
    fake_backend.script[:] = [500]
    tracker = _make_tracker(fake_backend.url)
    _log_marked(tracker, "retry-500")
    tracker.close()

    fake_backend.wait_for(lambda b: len(b.requests_containing("retry-500")) >= 2)
    attempts = fake_backend.requests_containing("retry-500")
    assert len(attempts) >= 2, (
        f"expected a retry after the 500 (>=2 POSTs), got {len(attempts)} — "
        "the batch was silently dropped"
    )
    assert attempts[-1].status_sent == 200


def test_timeout_is_retried_not_dropped(fake_backend):
    """Timeouts are TransportErrors and must re-buffer like ConnectError.

    Red today: ReadTimeout falls into the generic except clause and the batch
    is dropped. The fake server hangs past the client timeout on request one.
    """
    fake_backend.script[:] = ["hang:1.0"]
    tracker = _make_tracker(fake_backend.url, timeout=0.3, connect_timeout=0.3)
    _log_marked(tracker, "retry-timeout")
    tracker.close()

    fake_backend.wait_for(lambda b: len(b.requests_containing("retry-timeout")) >= 2)
    attempts = fake_backend.requests_containing("retry-timeout")
    assert len(attempts) >= 2, (
        f"expected a retry after the timeout (>=2 POSTs), got {len(attempts)} — "
        "timed-out batch was dropped instead of re-buffered"
    )


def test_backlog_is_chunked_under_backend_cap(fake_backend):
    """A large backlog must drain in chunks <= min(batch_size, 500) per POST.

    Red today: the whole buffer goes out as one POST — 1,200 events in a
    single request, which the real backend (cap 1000) would 413 and the
    tracker would drop permanently.
    """
    tracker = _make_tracker(fake_backend.url)
    _log_marked(tracker, "backlog", count=1200)
    tracker.close()

    fake_backend.wait_for(lambda b: len(b.delivered_events()) >= 1200)
    delivered = fake_backend.delivered_events()
    assert len(delivered) == 1200, f"lost events: delivered {len(delivered)}/1200"
    sizes = [len(r.events) for r in fake_backend.requests]
    assert max(sizes) <= 500, (
        f"unchunked POST of {max(sizes)} events — exceeds the 500-event chunk "
        "contract (backend cap is 1000; one 413 would destroy the backlog)"
    )


def test_poison_event_does_not_destroy_valid_events(fake_backend):
    """One invalid event must not lose the valid events batched with it.

    The fake server mimics the backend's 422 contract (min_length=1 on
    model/provider, FastAPI loc paths). Green via either defense layer:
    pre-buffer coercion of falsy model/provider to "unknown", or parsing the
    422 loc indices and re-sending only the valid events.

    Red today: the whole envelope 422s once and is treated as delivered —
    both valid events vanish.
    """
    fake_backend.script[:] = ["validate"] * 5
    tracker = _make_tracker(fake_backend.url)
    _log_marked(tracker, "poison-good-1")
    tracker.log_event(agent_name="poison-bad", model="", provider="")
    _log_marked(tracker, "poison-good-2")
    tracker.close()

    def _both_delivered(b) -> bool:
        agents = {ev.get("agent_name") for ev in b.delivered_events()}
        return {"poison-good-1", "poison-good-2"} <= agents

    fake_backend.wait_for(_both_delivered)
    delivered_agents = {ev.get("agent_name") for ev in fake_backend.delivered_events()}
    assert "poison-good-1" in delivered_agents, "valid event lost to poison batch"
    assert "poison-good-2" in delivered_agents, "valid event lost to poison batch"


def test_log_event_never_blocks_on_network(fake_backend):
    """log_event must be signal-only — no HTTP on the caller's thread.

    Red today: reaching batch_size runs a synchronous POST inline in
    log_event, injecting network latency into the user's LLM call path
    (the fake server hangs 1s per request to make the stall measurable).
    """
    fake_backend.script[:] = ["hang:1.0"] * 10
    tracker = _make_tracker(fake_backend.url, batch_size=5)

    start = time.perf_counter()
    _log_marked(tracker, "hot-path", count=20)
    elapsed = time.perf_counter() - start

    tracker.close()
    assert elapsed < 0.5, (
        f"20 log_event calls took {elapsed:.2f}s — network I/O is running on "
        "the caller's thread"
    )


def test_buffer_is_bounded_and_drops_are_counted():
    """During an outage the buffer must cap at max_buffer_size, dropping
    oldest, with the loss visible via a stats counter.

    Red today: the ctor has no max_buffer_size parameter, the buffer is an
    unbounded list, and no stats/dropped counter exists.
    """
    # A bound-then-closed port refuses connections with ConnectError — but on
    # Windows 11 the refusal takes ~2s, not milliseconds (measured), so any
    # delivery attempt against it is slow-but-bounded, never a hang.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    dead_port = sock.getsockname()[1]
    sock.close()

    tracker = CostTracker(
        endpoint=f"http://127.0.0.1:{dead_port}",
        api_key="test-key",
        flush_interval=60.0,
        batch_size=100_000,
        max_buffer_size=1000,
    )
    try:
        for i in range(1500):
            tracker.log_event(
                agent_name=f"outage-{i:04d}",
                model="gpt-4o",
                provider="openai",
            )
        stats = tracker.stats
        assert stats["buffered"] <= 1000, f"buffer exceeded cap: {stats['buffered']}"
        assert stats["dropped"] >= 500, (
            f"expected >=500 counted drops, got {stats['dropped']} — loss is invisible"
        )
        # Drop-OLDEST semantics: during an outage the freshest cost data must
        # survive. Every surviving event must be from the newest 1000 logged.
        surviving = [ev["agent_name"] for ev in tracker._buffer]
        oldest_surviving = min(int(name.split("-")[1]) for name in surviving)
        assert oldest_surviving >= 500, (
            f"buffer kept event #{oldest_surviving} — dropped newest instead of oldest"
        )
    finally:
        tracker.close()


def test_close_joins_worker_and_drains(fake_backend):
    """close() must stop AND join the worker thread, then drain the buffer.

    Red today: close() never joins — the daemon worker is still alive
    (sleeping in its flush loop) when close() returns.
    """
    tracker = _make_tracker(fake_backend.url, flush_interval=5.0)
    _log_marked(tracker, "shutdown", count=3)
    tracker.close()

    assert not tracker._flush_thread.is_alive(), (
        "worker thread still alive after close() — close must join it"
    )
    fake_backend.wait_for(lambda b: len(b.delivered_events()) >= 3, timeout=2.0)
    assert len(fake_backend.delivered_events()) == 3, "close() lost buffered events"
