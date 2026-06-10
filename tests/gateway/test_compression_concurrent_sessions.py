"""Behavioral tests for concurrent compression across distinct and shared sessions.

Complements ``test_compression_concurrent_fork.py`` (which tests the
agent-level lock against a real ``SessionDB``) by focusing on gateway-level
isolation guarantees:

1. Five distinct sessions compressing in parallel must not alias each other's
   session_ids (no cross-session contamination).
2. Two agents sharing the same session_id must serialize: exactly one rotates,
   the other returns its input unchanged (the no-op / lock-loser contract).

The stub-compressor pattern mirrors ``test_compression_concurrent_fork.py``:
the compressor returns deterministic output and sleeps briefly so threads
actually overlap at the OS level, making the absence of aliasing a genuine
stress test rather than a timing accident.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hermes_state import SessionDB


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _build_agent_with_db(db: SessionDB, session_id: str):
    """Construct an AIAgent wired to *db* and pinned to *session_id*.

    Mirrors the helper in test_compression_concurrent_fork.py exactly so the
    two test modules can be read side-by-side without cognitive overhead.
    """
    with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
        from run_agent import AIAgent

        agent = AIAgent(
            api_key="test-key",
            base_url="https://openrouter.ai/api/v1",
            model="test/model",
            quiet_mode=True,
            session_db=db,
            session_id=session_id,
            skip_context_files=True,
            skip_memory=True,
        )

    # Stub the compressor: deterministic output, brief sleep to force thread overlap.
    compressor = MagicMock()

    def _compress_with_overlap(*_a, **_kw):
        time.sleep(0.25)  # match fork test sleep so threads reliably overlap
        return [
            {"role": "user", "content": "[CONTEXT COMPACTION] summary"},
            {"role": "user", "content": "tail"},
        ]

    compressor.compress.side_effect = _compress_with_overlap
    compressor.compression_count = 1
    compressor.last_prompt_tokens = 0
    compressor.last_completion_tokens = 0
    compressor._last_summary_error = None
    compressor._last_compress_aborted = False
    compressor._last_aux_model_failure_model = None
    compressor._last_aux_model_failure_error = None
    agent.context_compressor = compressor
    return agent


_MESSAGES = [{"role": "user", "content": f"m{i}"} for i in range(20)]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_concurrent_compressions_do_not_alias_sessions(tmp_path: Path) -> None:
    """Five distinct sessions compressing in parallel must each produce a unique
    post-compression session_id; no two agents must end up sharing an id.

    Without per-session locking there is no cross-session aliasing anyway (each
    agent generates its own timestamp + uuid suffix), but this test makes the
    invariant explicit and would catch any regression where session_id generation
    became shared state (e.g. a module-level counter or a shared random seed).
    """
    db = SessionDB(db_path=tmp_path / "state.db")

    n = 5
    parent_ids = [f"DISTINCT_PARENT_{i:02d}" for i in range(n)]
    for sid in parent_ids:
        db.create_session(sid, source="discord")

    agents = [_build_agent_with_db(db, sid) for sid in parent_ids]
    errors: list[Exception] = []

    def run(agent):
        try:
            agent._compress_context(_MESSAGES, "sys", approx_tokens=120_000)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(a,), name=f"session-{i}") for i, a in enumerate(agents)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert not errors, f"Compression raised exceptions: {errors}"

    # Every agent must have rotated to a new, unique session_id.
    new_ids = [a.session_id for a in agents]
    assert all(sid not in parent_ids for sid in new_ids), (
        "At least one agent did not rotate its session_id during compression. "
        f"parent_ids={parent_ids}  new_ids={new_ids}"
    )
    assert len(set(new_ids)) == n, (
        f"Post-compression session_ids are not unique: {new_ids}. "
        "Two agents aliased to the same id — cross-session contamination."
    )


def test_concurrent_compressions_same_session_serialize(tmp_path: Path) -> None:
    """Two agents sharing a session_id must not both rotate it.

    The per-session compression lock (added in #34351) serializes concurrent
    compress() calls keyed on the same session_id.  Exactly one agent must
    rotate (the lock winner); the other must return its messages unchanged (the
    lock loser, which detects ``len(returned) == len(input)`` and backs off).

    This is the gateway analogue of the fork test in
    ``test_compression_concurrent_fork.py`` but scoped to the two-agent /
    same-session shape most likely to occur in practice: the main-turn agent
    and its background-review fork both hitting the compression threshold.

    Determinism
    -----------
    A bare ``thread.start()`` + ``sleep(0.25)`` inside the stubbed compressor
    does NOT guarantee the two threads overlap at the lock.  On a loaded CI
    runner thread A can acquire the lock, compress, rotate the session_id AND
    release the lock before thread B is ever scheduled onto the acquisition
    point.  B then acquires the (now-free) lock on the original session_id and
    *also* compresses — yielding ``compressed_count == 2`` and the intermittent
    "Expected exactly one agent to compress, got 2" failure.  The lock is
    correct; the old test merely failed to force the overlap it asserts on.

    We make the overlap deterministic by synchronising on the real seam both
    threads must pass through: ``SessionDB.try_acquire_compression_lock``.  A
    ``threading.Barrier(2)`` placed immediately AFTER each thread's *real*
    acquisition attempt holds both threads until both have attempted to
    acquire.  Because SQLite serialises the two INSERT-or-IGNORE writes, exactly
    one attempt returns ``True`` (the winner, which has the lock row inserted)
    and the other returns ``False`` (the loser sees it held) — the genuine
    product outcome, unmodified.  The barrier only guarantees the loser has
    provably reached acquisition *while the winner still holds the lock*,
    closing the "B starts late" window without weakening any assertion below.
    """
    db = SessionDB(db_path=tmp_path / "state.db")
    shared_sid = "SHARED_SESSION_CONCURRENT"
    db.create_session(shared_sid, source="discord")

    agent_a = _build_agent_with_db(db, shared_sid)
    agent_b = _build_agent_with_db(db, shared_sid)

    # ── Force overlap at the lock-acquisition seam ──────────────────────────
    # Wrap the shared db's ``try_acquire_compression_lock`` so that neither
    # caller returns from its acquisition attempt until BOTH callers have made
    # their real attempt.  The real method is delegated to verbatim, so the
    # winner/loser split is decided by SQLite exactly as in production; the
    # barrier only pins the timing so the loser is guaranteed to attempt
    # acquisition while the winner is still holding the lock.
    overlap_barrier = threading.Barrier(2, timeout=10)
    _real_try_acquire = db.try_acquire_compression_lock

    def _try_acquire_with_overlap(session_id, holder, *args, **kwargs):
        acquired = _real_try_acquire(session_id, holder, *args, **kwargs)
        # Rendezvous: hold here until the other thread has also completed its
        # real acquisition attempt.  After this point exactly one thread owns
        # the lock and the other has already seen it held — so the winner can
        # safely proceed into compress()/rotate()/release() knowing the loser
        # has already lost the race (it cannot acquire the freed lock late).
        try:
            overlap_barrier.wait()
        except threading.BrokenBarrierError:
            # Defensive: if only one thread ever reaches acquisition (a real
            # regression where the second path never attempts the lock), the
            # barrier times out and breaks rather than hanging the suite.
            pass
        return acquired

    results: dict[str, list | None] = {"a": None, "b": None}
    errors: list[Exception] = []

    def run(key, agent):
        try:
            compressed, _sp = agent._compress_context(_MESSAGES, "sys", approx_tokens=120_000)
            results[key] = compressed
        except Exception as exc:
            errors.append(exc)

    t_a = threading.Thread(target=run, args=("a", agent_a), name="main_turn")
    t_b = threading.Thread(target=run, args=("b", agent_b), name="review_fork")
    with patch.object(db, "try_acquire_compression_lock", _try_acquire_with_overlap):
        t_a.start()
        t_b.start()
        t_a.join(timeout=15)
        t_b.join(timeout=15)

    assert not errors, f"Compression raised exceptions: {errors}"

    # Count which agents actually compressed (returned fewer messages than input)
    compressed_count = sum(
        1 for msgs in results.values()
        if msgs is not None and len(msgs) < len(_MESSAGES)
    )
    unchanged_count = sum(
        1 for msgs in results.values()
        if msgs is not None and len(msgs) == len(_MESSAGES)
    )

    assert compressed_count == 1, (
        f"Expected exactly one agent to compress, got {compressed_count}. "
        "If both compressed, the lock failed to serialize. "
        "If neither compressed, both lost the lock (check lock logic)."
    )
    assert unchanged_count == 1, (
        f"Expected exactly one agent to return messages unchanged (lock loser), "
        f"got {unchanged_count}."
    )

    # Exactly one session_id rotation must have occurred.
    rotated = sum(
        1 for a in (agent_a, agent_b) if a.session_id != shared_sid
    )
    assert rotated == 1, (
        f"Expected exactly one agent to rotate session_id, got {rotated}. "
        "Both agents rotating produces a session fork (Damien's incident shape)."
    )

    # The lock must be released so future compression on the NEW session_id works.
    assert db.get_compression_lock_holder(shared_sid) is None, (
        "Compression lock leaked: still held on the parent session_id after both "
        "threads joined. Future compression on the child session would deadlock."
    )
