#!/usr/bin/env python3
"""
GTM Engineering — Bulk AI Column Enrichment Engine
====================================================
Reads a CSV/Excel, builds a rich-context prompt per row using ALL specified
feature columns, fires async parallel OpenAI calls, and writes a new column.

ARCHITECTURE (why it's fast):
─────────────────────────────
1. Raw aiohttp → no SDK overhead, direct HTTP to OpenAI
2. asyncio.Semaphore(N) → N parallel in-flight requests (default 50)
3. Pre-built prompt list → zero compute during the async blast
4. as_completed() → results stream in, progress updates live
5. Retry with exponential backoff → handles 429s without crashing
6. Description truncation at 500 chars → controls token cost

PROMPT DESIGN (why it's accurate):
───────────────────────────────────
Each row gets a two-part prompt:
  SYSTEM: Domain-expert persona + strict output format rules
  USER:   "Company Data:" block with ALL feature values injected
          + "Question:" block with the user's query

This gives the LLM full feature context to reason over before answering.

Usage (CLI):
    python enrich_column.py \\
        -i data.csv -o enriched.csv \\
        -q "Is this firm into Higher Education or School Education?" \\
        -f "Name,Description,Education Category,Sub-Category,Education Level" \\
        -c "Education_Type" \\
        -k "sk-..." \\
        --model gpt-4o-mini --concurrency 50

Usage (as Python function):
    from enrich_column import enrich_dataframe
    df = enrich_dataframe(
        df=my_df,
        question="Classify this company...",
        features=["Name", "Description", "Category"],
        new_column="Classification",
        api_key="sk-...",
    )
"""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

# ─── Auto-install deps ───────────────────────────────────────────────────────
def _ensure_dep(pkg, pip_name=None):
    try:
        __import__(pkg)
    except ImportError:
        import subprocess
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", pip_name or pkg,
             "--break-system-packages", "-q"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

_ensure_dep("aiohttp")
_ensure_dep("pandas")
_ensure_dep("openpyxl")

import aiohttp
import pandas as pd


# ═══════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════
OPENAI_URL    = "https://api.openai.com/v1/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
MODEL         = "gpt-4o-mini"
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
CONCURRENCY   = 50       # parallel in-flight requests
MAX_RETRIES   = 5
BACKOFF_BASE  = 1.0      # seconds, doubles each retry
TIMEOUT_SEC   = 60
MAX_RESP_TOK  = 200      # keep answers short & cheap
DESC_TRUNCATE = 500      # chars — saves ~40% tokens on long descriptions
TEMPERATURE   = 0.15     # near-deterministic for classification


# ═══════════════════════════════════════════════════════════════════════════════
#  PROMPT BUILDER — the heart of context injection
# ═══════════════════════════════════════════════════════════════════════════════
SYSTEM_PROMPT = """You are a senior GTM research analyst at a B2B intelligence firm.
You classify and analyze companies based on structured feature data.

RULES:
- Read ALL the company data fields carefully before answering.
- Answer the question directly and concisely (1-2 sentences max).
- If the answer is a category/label, output ONLY the label — no explanation.
- If data is genuinely insufficient, respond exactly: "Insufficient data".
- Never fabricate information not present in the provided data."""


def build_row_prompt(row: dict, features: list[str], question: str) -> str:
    """
    Inject every feature value for a single row into a structured prompt.

    The prompt format:
        Company Data:
        - Name: Yocket
        - Description: At Yocket, we're all about making...
        - Education Category: Higher Education
        ...

        Question: Is this firm into Higher Education or School Education?

    Why this format works:
    - Labeled key-value pairs let the LLM distinguish features cleanly
    - Truncating descriptions to 500 chars keeps costs ~40% lower
    - Putting the question AFTER the data forces the LLM to read data first
    """
    parts = ["Company Data:"]
    for feat in features:
        val = str(row.get(feat, "")).strip()
        # Normalize empty/null values
        if not val or val.lower() in ("nan", "none", "n/a", "null", ""):
            val = "N/A"
        # Truncate long text (descriptions) to control token costs
        if len(val) > DESC_TRUNCATE:
            val = val[:DESC_TRUNCATE] + "…"
        parts.append(f"- {feat}: {val}")

    parts.append(f"\nQuestion: {question}")
    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════════════
#  ASYNC API CALLER — one call per row, with retry
# ═══════════════════════════════════════════════════════════════════════════════
async def _call_openai(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    row_idx: int,
    prompt: str,
    headers: dict,
    model: str,
    system_prompt: str,
    max_tokens: int,
) -> tuple:
    """Fire one OpenAI completion. Returns (row_index, response_text)."""
    body = {
        "model": model,
        "temperature": TEMPERATURE,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": prompt},
        ],
    }
    # Only hold the semaphore during the actual HTTP call — backoff sleeps MUST be
    # outside the `async with sem` block or a single rate-limited row will occupy
    # a concurrency slot while sleeping, starving every other in-flight row.
    for attempt in range(MAX_RETRIES):
        wait_s = None
        try:
            async with sem:
                async with session.post(
                    OPENAI_URL, headers=headers, json=body,
                    timeout=aiohttp.ClientTimeout(total=TIMEOUT_SEC),
                ) as r:
                    if r.status == 200:
                        d = await r.json()
                        choices = d.get("choices", [])
                        if not choices:
                            return (row_idx, "[ERROR:EMPTY] No choices returned")
                        return (row_idx, choices[0]["message"]["content"].strip())
                    if r.status == 429:
                        wait_s = float(r.headers.get("Retry-After", BACKOFF_BASE * 2**attempt))
                    elif r.status >= 500:
                        wait_s = BACKOFF_BASE * 2**attempt
                    else:
                        err = await r.text()
                        return (row_idx, f"[ERROR:{r.status}] {err[:150]}")
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            if attempt >= MAX_RETRIES - 1:
                return (row_idx, f"[ERROR:TIMEOUT] {e!s:.100}")
            wait_s = BACKOFF_BASE * 2**attempt
        if wait_s is not None and attempt < MAX_RETRIES - 1:
            await asyncio.sleep(wait_s)
    return (row_idx, "[ERROR] max retries exhausted")


async def _call_anthropic(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    row_idx: int,
    prompt: str,
    headers: dict,
    model: str,
    system_prompt: str,
    max_tokens: int,
) -> tuple:
    """Fire one Anthropic Claude completion. Returns (row_index, response_text)."""
    body = {
        "model": model,
        "temperature": TEMPERATURE,
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": prompt}],
    }
    # Semaphore is only held during the HTTP call; backoff sleeps are outside it
    # so rate-limited rows don't block other concurrent rows.
    for attempt in range(MAX_RETRIES):
        wait_s = None
        try:
            async with sem:
                async with session.post(
                    ANTHROPIC_URL, headers=headers, json=body,
                    timeout=aiohttp.ClientTimeout(total=TIMEOUT_SEC),
                ) as r:
                    if r.status == 200:
                        d = await r.json()
                        content = d.get("content", [])
                        if not content:
                            return (row_idx, "[ERROR:EMPTY] No content returned")
                        return (row_idx, content[0]["text"].strip())
                    if r.status == 429 or r.status >= 500:
                        wait_s = BACKOFF_BASE * 2**attempt
                    else:
                        err = await r.text()
                        return (row_idx, f"[ERROR:{r.status}] {err[:150]}")
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            if attempt >= MAX_RETRIES - 1:
                return (row_idx, f"[ERROR:TIMEOUT] {str(e)[:100]}")
            wait_s = BACKOFF_BASE * 2**attempt
        if wait_s is not None and attempt < MAX_RETRIES - 1:
            await asyncio.sleep(wait_s)
    return (row_idx, "[ERROR] max retries exhausted")


# ═══════════════════════════════════════════════════════════════════════════════
#  PROGRESS BAR
# ═══════════════════════════════════════════════════════════════════════════════
class Progress:
    def __init__(self, total: int):
        self.total = total
        self.done = 0
        self.errs = 0
        self.t0 = time.time()

    def tick(self, is_err=False):
        self.done += 1
        self.errs += int(is_err)
        el = time.time() - self.t0
        rate = self.done / el if el > 0 else 0
        eta = (self.total - self.done) / rate if rate > 0 else 0
        pct = self.done / self.total
        bar = "█" * int(30 * pct) + "░" * (30 - int(30 * pct))
        print(f"\r  {bar} {self.done}/{self.total}  "
              f"{rate:.0f} rows/s  ETA {eta:.0f}s  err={self.errs}",
              end="", flush=True, file=sys.stderr)

    def summary(self):
        el = time.time() - self.t0
        print(f"\n  ✓ {self.done} rows in {el:.1f}s ({self.done/el:.0f}/s). "
              f"Errors: {self.errs}\n", file=sys.stderr)


# ═══════════════════════════════════════════════════════════════════════════════
#  ENRICHMENT ENGINE — the main function you call
# ═══════════════════════════════════════════════════════════════════════════════
async def _run_enrich(
    df: pd.DataFrame,
    features: list[str],
    question: str,
    new_column: str,
    api_key: str,
    api: str = "openai",
    model: str = None,
    concurrency: int = CONCURRENCY,
    system_prompt: str = SYSTEM_PROMPT,
    max_tokens: int = MAX_RESP_TOK,
) -> pd.DataFrame:
    """Async core: build prompts → blast API → collect results → return df."""
    # ── Edge case: empty dataframe ──
    if df.empty:
        print("  ⚠ Empty dataset — nothing to enrich.", file=sys.stderr)
        df[new_column] = []
        return df

    # ── Edge case: strip column whitespace ──
    df.columns = df.columns.str.strip()

    if model is None:
        model = ANTHROPIC_MODEL if api == "anthropic" else MODEL
    total = len(df)

    # ── Cost estimate ──
    avg_in = 120 + len(features) * 60   # tokens per row (rough)
    avg_out = 50
    if api == "anthropic":
        price_in, price_out = (0.25, 1.25) if "haiku" in model else (3.00, 15.00)
    else:
        price_in, price_out = (0.15, 0.60) if "mini" in model else (2.50, 10.00)
    cost = (avg_in * total * price_in + avg_out * total * price_out) / 1e6

    print(f"\n  Enriching {total} rows", file=sys.stderr)
    print(f"  API: {api}  |  Model: {model}  |  Concurrency: {concurrency}", file=sys.stderr)
    print(f"  Features: {features}", file=sys.stderr)
    print(f"  Question: {question}", file=sys.stderr)
    print(f"  Est. cost: ~${cost:.2f}\n", file=sys.stderr)

    # ── Pre-build all prompts (zero cost during async blast) ──
    # to_dict('records') is 10-30x faster than iterrows() on large DataFrames.
    records = df.reset_index(drop=True).to_dict("records")
    prompts = [(i, build_row_prompt(r, features, question)) for i, r in enumerate(records)]

    # ── Fire all requests ──
    if api == "anthropic":
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}
        caller = _call_anthropic
    else:
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        caller = _call_openai

    sem = asyncio.Semaphore(concurrency)
    prog = Progress(total)
    results = {}

    conn = aiohttp.TCPConnector(limit=concurrency + 10, ttl_dns_cache=300)
    async with aiohttp.ClientSession(connector=conn) as session:
        tasks = [
            caller(session, sem, idx, p, headers, model, system_prompt, max_tokens)
            for idx, p in prompts
        ]
        for coro in asyncio.as_completed(tasks):
            idx, resp = await coro
            results[idx] = resp
            prog.tick(str(resp).startswith("[ERROR"))

    prog.summary()

    # ── Write column ──
    # Positional list assignment — faster than df.index.map(lambda).
    df[new_column] = [results.get(i, "[ERROR:MISSING]") for i in range(len(df))]
    return df


def enrich_dataframe(
    df: pd.DataFrame,
    question: str,
    features: list[str],
    new_column: str,
    api_key: str,
    api: str = "openai",
    model: str = None,
    concurrency: int = CONCURRENCY,
    system_prompt: str = SYSTEM_PROMPT,
    max_tokens: int = MAX_RESP_TOK,
) -> pd.DataFrame:
    """
    PUBLIC FUNCTION — call this from your own Python code.

    Example:
        from enrich_column import enrich_dataframe
        import pandas as pd

        df = pd.read_csv("companies.csv")
        df = enrich_dataframe(
            df=df,
            question="Classify this company's primary market segment.",
            features=["Name", "Description", "Industry"],
            new_column="Segment",
            api_key="sk-...",
            api="openai",  # or "anthropic"
        )
        df.to_csv("enriched.csv", index=False)
    """
    return asyncio.run(
        _run_enrich(df, features, question, new_column, api_key,
                    api, model, concurrency, system_prompt, max_tokens)
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  DRY RUN MODE — test prompts without calling API
# ═══════════════════════════════════════════════════════════════════════════════
def dry_run(df: pd.DataFrame, features: list[str], question: str, n: int = 3):
    """Print sample prompts for N rows — no API call, no cost."""
    print(f"\n{'='*70}", file=sys.stderr)
    print(f"  DRY RUN — showing prompts for first {n} rows (no API calls)", file=sys.stderr)
    print(f"{'='*70}\n", file=sys.stderr)
    for i, (_, row) in enumerate(df.head(n).iterrows()):
        prompt = build_row_prompt(row.to_dict(), features, question)
        print(f"--- ROW {i} [{row.get('Name', 'unknown')}] ---", file=sys.stderr)
        print(f"SYSTEM: {SYSTEM_PROMPT[:120]}...\n", file=sys.stderr)
        print(f"USER:\n{prompt}\n", file=sys.stderr)

    # Cost estimate for full dataset
    total = len(df)
    avg_in = 120 + len(features) * 60
    avg_out = 50
    price_in, price_out = 0.15, 0.60  # gpt-4o-mini
    cost = (avg_in * total * price_in + avg_out * total * price_out) / 1e6
    print(f"{'='*70}", file=sys.stderr)
    print(f"  Full run: {total} rows × ~{avg_in} tokens/row", file=sys.stderr)
    print(f"  Est. cost (gpt-4o-mini): ~${cost:.2f}", file=sys.stderr)
    print(f"  Est. time (concurrency=50): ~{total/40:.0f}s", file=sys.stderr)
    print(f"{'='*70}\n", file=sys.stderr)


# ═══════════════════════════════════════════════════════════════════════════════
#  CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════
def main():
    p = argparse.ArgumentParser(
        description="GTM Engineering — Bulk AI Column Enrichment",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("-i", "--input",      required=True, help="Input CSV or Excel")
    p.add_argument("-o", "--output",     required=True, help="Output file (.csv or .xlsx)")
    p.add_argument("-q", "--question",   required=True, help="Question for each row")
    p.add_argument("-f", "--features",   required=True, help="Comma-sep feature columns")
    p.add_argument("-c", "--new-column", required=True, help="New column name")
    p.add_argument("-k", "--api-key",    default=None,  help="API key (or OPENAI_API_KEY / ANTHROPIC_API_KEY env)")
    p.add_argument("--api",             choices=["openai","anthropic"], default="openai", help="API provider")
    p.add_argument("-m", "--model",      default=None,  help="Model (auto-selected per provider if omitted)")
    p.add_argument("-n", "--concurrency",type=int, default=CONCURRENCY, help="Parallel calls")
    p.add_argument("-s", "--system-prompt", default=SYSTEM_PROMPT, help="Custom system prompt")
    p.add_argument("--max-tokens",       type=int, default=MAX_RESP_TOK)
    p.add_argument("--limit",           type=int, default=None, help="Process first N rows only")
    p.add_argument("--dry-run",         action="store_true", help="Show prompts, no API calls")
    p.add_argument("--retry-failed",    default=None, help="Retry [ERROR] rows from prev output")
    args = p.parse_args()

    # ── Read file ──
    path = Path(args.input)
    print(f"  Reading {path}...", file=sys.stderr)
    if path.suffix in (".xlsx", ".xls"):
        df = pd.read_excel(path)
    else:
        try:
            df = pd.read_csv(path, low_memory=False)
        except UnicodeDecodeError:
            df = pd.read_csv(path, low_memory=False, encoding="latin-1")
            print("  ⚠ Used latin-1 encoding fallback", file=sys.stderr)
    # Strip whitespace from column names
    df.columns = df.columns.str.strip()
    print(f"  Loaded: {len(df)} rows × {len(df.columns)} cols", file=sys.stderr)

    # ── Parse features ──
    features = [f.strip() for f in args.features.split(",")]
    missing = [f for f in features if f not in df.columns]
    if missing:
        print(f"  ⚠ Missing columns: {missing}", file=sys.stderr)
        print(f"  Available: {list(df.columns)}", file=sys.stderr)
        features = [f for f in features if f in df.columns]
        if not features:
            sys.exit("ERROR: No valid feature columns found.")

    # ── Limit ──
    if args.limit:
        df = df.head(args.limit).copy()

    # ── Retry mode ──
    good_df = None
    if args.retry_failed:
        prev = pd.read_csv(args.retry_failed, low_memory=False) if args.retry_failed.endswith(".csv") else pd.read_excel(args.retry_failed)
        mask = prev[args.new_column].astype(str).str.startswith("[ERROR")
        df = prev[mask].copy()
        good_df = prev[~mask].copy()
        print(f"  Retrying {len(df)} failed rows", file=sys.stderr)

    # ── Dry run ──
    if args.dry_run:
        dry_run(df, features, args.question)
        return

    # ── API key ──
    env_key = "ANTHROPIC_API_KEY" if args.api == "anthropic" else "OPENAI_API_KEY"
    api_key = args.api_key or os.environ.get(env_key)
    if not api_key:
        sys.exit(f"ERROR: Pass --api-key or set {env_key} env var")

    # ── Enrich ──
    df = enrich_dataframe(
        df, args.question, features, args.new_column,
        api_key, args.api, args.model, args.concurrency, args.system_prompt, args.max_tokens,
    )

    # ── Merge retries ──
    if good_df is not None:
        df = pd.concat([good_df, df], ignore_index=True)

    # ── Write ──
    out = Path(args.output)
    print(f"  Writing {out}...", file=sys.stderr)
    if out.suffix in (".xlsx", ".xls"):
        df.to_excel(out, index=False)
    else:
        df.to_csv(out, index=False)

    errs = df[args.new_column].astype(str).str.startswith("[ERROR").sum()
    print(f"  ✓ Done: {len(df)} rows written. Success: {len(df)-errs} | Errors: {errs}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
