#!/usr/bin/env python3
"""
Benchmark: prompt-building throughput on a 10K-row synthetic DataFrame.
Compares the old (iterrows) vs new (to_dict('records')) implementation.
"""
import sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
from scripts.gtm_enrich import build_prompt

N = 10_000
df = pd.DataFrame({
    "Name":        [f"Company {i}" for i in range(N)],
    "Description": ["We build enterprise widgets with a focus on B2B SaaS." for _ in range(N)],
    "Industry":    ["SaaS"] * N,
    "Employees":   [100 + i % 5000 for i in range(N)],
})
features = ["Name", "Description", "Industry", "Employees"]
question = "Classify segment"

# Old path: iterrows()
t0 = time.time()
old = [(idx, build_prompt(row.to_dict(), features, question)) for idx, row in df.iterrows()]
t_old = time.time() - t0

# New path: to_dict('records') + enumerate
t0 = time.time()
records = df.reset_index(drop=True).to_dict("records")
new = [(i, build_prompt(r, features, question)) for i, r in enumerate(records)]
t_new = time.time() - t0

assert len(old) == len(new) == N
# Sanity: both produce same prompt for row 0
assert old[0][1] == new[0][1], "prompts diverged"

print(f"  Rows:              {N:,}")
print(f"  OLD (iterrows):    {t_old*1000:8.1f} ms   ({N/t_old:>8.0f} rows/s)")
print(f"  NEW (to_dict):     {t_new*1000:8.1f} ms   ({N/t_new:>8.0f} rows/s)")
print(f"  Speedup:           {t_old/t_new:.1f}x")
