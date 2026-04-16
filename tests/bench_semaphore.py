#!/usr/bin/env python3
"""
Benchmark: verifies the semaphore-during-sleep bug is fixed.
Simulates 20 rows with 1 retryable failure. If the semaphore is held during
the retry sleep, other rows will be blocked behind it.
"""
import asyncio, time, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Mock a caller that fails once (triggers backoff), then succeeds.
class MockCaller:
    def __init__(self, fail_on=0):
        self.calls = 0
        self.fail_on = fail_on
    async def __call__(self, session, prompt, headers, model, sys_prompt, max_tok):
        self.calls += 1
        await asyncio.sleep(0.05)  # simulate 50ms network call
        if self.calls == self.fail_on + 1:
            raise RuntimeError("[429] rate limited")
        return f"ok:{prompt[-5:]}"

from scripts.gtm_enrich import _call_with_retry

async def main():
    N = 20
    sem = asyncio.Semaphore(5)  # concurrency 5
    caller = MockCaller(fail_on=0)
    t0 = time.time()
    tasks = [_call_with_retry(caller, None, sem, i, f"prompt{i}", {}, "model", "sys", 100)
             for i in range(N)]
    results = await asyncio.gather(*tasks)
    el = time.time() - t0
    errors = sum(1 for _, r in results if str(r).startswith("[ERROR"))
    # With 20 rows @ 50ms each / 5 concurrency = 200ms minimum.
    # With the bug (sem held during sleep), the 1s backoff would block a slot
    # and total time would be > 1.2s. Without the bug, should be ~0.3-0.5s.
    print(f"  Rows: {N}  Concurrency: 5  Retries: 1  Errors: {errors}")
    print(f"  Total time: {el*1000:.0f} ms")
    print(f"  Expected < 600ms (fix applied); > 1000ms (bug present)")
    if el < 0.6:
        print(f"  ✓ Semaphore fix verified — other rows weren't blocked by retry sleep")
    else:
        print(f"  ✗ Semaphore appears to still block during sleep")

asyncio.run(main())
