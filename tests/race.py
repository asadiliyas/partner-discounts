"""Force concurrent requests to interleave at a chosen call.

``asyncio.gather`` on its own does not reliably produce a race here: with the
in-memory Mongo the first request tends to finish before the second reads,
so a test with the concurrency guard removed still passed. Holding the
first ``parties`` calls to the write step at a barrier means every request
has read the same state before any of them writes, which is exactly the
interleaving the conditional updates must survive.
"""

import asyncio


def hold_at_barrier(monkeypatch, module, name: str, parties: int = 2, timeout: float = 2.0) -> None:
    real = getattr(module, name)
    barrier = asyncio.Barrier(parties)
    calls = 0

    async def held(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls <= parties:
            await asyncio.wait_for(barrier.wait(), timeout)
        return await real(*args, **kwargs)

    monkeypatch.setattr(module, name, held)
