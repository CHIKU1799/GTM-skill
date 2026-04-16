#!/usr/bin/env python3
"""
GTM ENRICHMENT ENGINE v2
=========================
Reads a CSV/Excel, builds a rich-context prompt per row (optionally crawling
the company's website), calls OpenAI OR Anthropic Claude, and fills a new column.

NEW in v2:
  - Website crawling: fetches & extracts text from each row's Domain URL
  - Anthropic Claude support: use --api anthropic
  - Auto-detect URL columns (Domain, Website, URL, etc.)
  - Smart caching: won't re-crawl the same domain twice

USAGE:
    python gtm_enrich.py \\
        -i startups.csv \\
        -q "Is this firm into Higher Education or School Education?" \\
        -f "Name,Description,Education Category,Education Level" \\
        -c "Education_Type" \\
        -k "sk-..." \\
        --api openai            # or: anthropic
        --crawl                 # enable website crawling
"""

from __future__ import annotations

import argparse, asyncio, os, sys, time, re, html
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

def _install(pkg):
    try: __import__(pkg)
    except ImportError:
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg,
            "--break-system-packages", "-q"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

_install("aiohttp"); _install("pandas"); _install("openpyxl")
import aiohttp, pandas as pd


# ═══════════════════════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════════════════════
OPENAI_URL    = "https://api.openai.com/v1/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

MODELS = {
    "openai":    "gpt-4o-mini",
    "anthropic": "claude-haiku-4-5-20251001",
}

CONCURRENCY   = 30
MAX_RETRIES   = 5
BACKOFF       = 1.0
TIMEOUT       = 60
MAX_TOKENS    = 200
TEMPERATURE   = 0.1
DESC_LIMIT    = 500
CRAWL_LIMIT   = 1500   # max chars of website content to inject
CRAWL_TIMEOUT = 10

SYSTEM = """You are a senior GTM research analyst. You classify and analyze companies based on structured feature data.

RULES:
- Read ALL company data fields carefully before answering.
- Answer the question directly. 1-2 sentences MAX.
- If the answer is a category/label, output ONLY the label.
- If data is insufficient, say "Insufficient data".
- NEVER fabricate. Only use what's in the provided data."""


# ═══════════════════════════════════════════════════════════════════════════════
#  WEBSITE CRAWLER — fetch & extract text from a URL
# ═══════════════════════════════════════════════════════════════════════════════
_crawl_cache = {}

def normalize_url(url: str) -> str | None:
    """Normalize a domain/URL string into a fetchable HTTPS URL."""
    if not url or not isinstance(url, str): return None
    url = url.strip()
    if not url or url.lower() in ("nan", "none", "n/a"): return None
    # Skip LinkedIn explicitly
    if "linkedin.com" in url.lower(): return None
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url.lstrip("/")
    try:
        p = urlparse(url)
        if not p.netloc: return None
        return f"https://{p.netloc}{p.path or '/'}"
    except Exception:
        return None


def extract_text(html_content: str, max_chars: int = CRAWL_LIMIT) -> str:
    """Crude HTML → text extraction. Keeps text, strips tags/scripts/styles."""
    # Remove scripts and styles
    html_content = re.sub(r'<script[^>]*>.*?</script>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
    html_content = re.sub(r'<style[^>]*>.*?</style>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
    # Try to grab meta description first (high signal)
    desc_match = re.search(r'<meta\s+name=["\']description["\']\s+content=["\']([^"\']+)["\']', html_content, re.IGNORECASE)
    desc = desc_match.group(1) if desc_match else ""
    # Extract title
    title_match = re.search(r'<title[^>]*>([^<]+)</title>', html_content, re.IGNORECASE)
    title = title_match.group(1) if title_match else ""
    # Strip all tags
    text = re.sub(r'<[^>]+>', ' ', html_content)
    text = html.unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()
    combined = f"TITLE: {title} | DESC: {desc} | BODY: {text}".strip()
    return combined[:max_chars] + ("..." if len(combined) > max_chars else "")


async def crawl_url(session, url: str, sem: asyncio.Semaphore) -> str:
    """Fetch a URL and return extracted text. Cached."""
    url = normalize_url(url)
    if not url: return ""
    if url in _crawl_cache: return _crawl_cache[url]

    async with sem:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=CRAWL_TIMEOUT),
                                    allow_redirects=True,
                                    headers={"User-Agent": "Mozilla/5.0 (compatible; GTM-Enrich/1.0)"}) as r:
                if r.status == 200:
                    content = await r.text(errors="ignore")
                    result = extract_text(content)
                    _crawl_cache[url] = result
                    return result
                _crawl_cache[url] = ""
                return ""
        except Exception:
            _crawl_cache[url] = ""
            return ""


def detect_url_column(columns: list[str]) -> str | None:
    """Auto-detect which column contains website URLs."""
    priority = ["domain", "website", "url", "homepage", "site"]
    for p in priority:
        for c in columns:
            if p == c.lower() or (p in c.lower() and "linkedin" not in c.lower()):
                return c
    return None


# ═══════════════════════════════════════════════════════════════════════════════
#  NLP AUTO-ROUTING — decide if a question is per-row or list-level
# ═══════════════════════════════════════════════════════════════════════════════
async def _decide_mode_async(question: str, api_key: str, api: str = "anthropic") -> str:
    """Single small API call → returns 'per_row' or 'list'. ~1s latency."""
    sys_prompt = (
        "Return exactly one word: PER_ROW if the instruction should be applied to "
        "EACH row (classify, score, enrich, look up, summarize each), or LIST if it "
        "should be applied ONCE over the whole dataset (hypothesis, overall insight, "
        "schema, strategy)."
    )
    user = f"Instruction: {question}\n\nOne word answer: PER_ROW or LIST"
    model = MODELS[api]
    async with aiohttp.ClientSession() as session:
        if api == "openai":
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            text = await call_openai(session, user, headers, model, sys_prompt, 15)
        else:
            headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}
            text = await call_anthropic(session, user, headers, model, sys_prompt, 15)
    return "list" if "LIST" in text.upper() else "per_row"


def decide_mode(question: str, api_key: str, api: str = "anthropic") -> str:
    """Sync wrapper — returns 'per_row' or 'list'."""
    return asyncio.run(_decide_mode_async(question, api_key, api))


# ═══════════════════════════════════════════════════════════════════════════════
#  DEDUPE — pure-pandas, no API cost
# ═══════════════════════════════════════════════════════════════════════════════
def dedupe(df: pd.DataFrame, features: list, mode: str = "mark",
           column: str = "Is_Duplicate") -> pd.DataFrame:
    """
    Find/remove duplicates using case-insensitive, whitespace-normalized match
    on the concatenation of `features`.

    mode='mark'   → adds column with "Yes"/"No" (first occurrence = "No")
    mode='remove' → returns DataFrame with duplicates dropped (keeps first)
    """
    if df.empty or not features:
        return df
    norm = lambda v: str(v if v is not None else "").lower().strip()
    key = df[features].astype(str).apply(lambda r: "||".join(norm(v) for v in r), axis=1)
    if mode == "remove":
        return df.loc[~key.duplicated(keep="first")].reset_index(drop=True)
    out = df.copy()
    out[column] = ["Yes" if d else "No" for d in key.duplicated(keep="first")]
    return out


# ═══════════════════════════════════════════════════════════════════════════════
#  PROMPT BUILDER
# ═══════════════════════════════════════════════════════════════════════════════
def build_prompt(row: dict, features: list[str], question: str, crawl_text: str = "") -> str:
    lines = ["Company Data:"]
    for f in features:
        val = str(row.get(f, "")).strip()
        if not val or val.lower() in ("nan", "none", "n/a", "null", ""):
            val = "N/A"
        if len(val) > DESC_LIMIT:
            val = val[:DESC_LIMIT] + "..."
        lines.append(f"- {f}: {val}")

    if crawl_text:
        lines.append(f"\nWebsite Content (live scrape):\n{crawl_text}")

    lines.append(f"\nQuestion: {question}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
#  API CALLERS — OpenAI OR Anthropic
# ═══════════════════════════════════════════════════════════════════════════════
async def call_openai(session, prompt, headers, model, sys_prompt, max_tok):
    body = {
        "model": model, "temperature": TEMPERATURE, "max_tokens": max_tok,
        "messages": [{"role":"system","content":sys_prompt}, {"role":"user","content":prompt}],
    }
    async with session.post(OPENAI_URL, headers=headers, json=body,
        timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as r:
        if r.status == 200:
            d = await r.json()
            choices = d.get("choices", [])
            if not choices:
                raise RuntimeError("[EMPTY] OpenAI returned no choices")
            return choices[0]["message"]["content"].strip()
        raise RuntimeError(f"[{r.status}] {(await r.text())[:120]}")


async def call_anthropic(session, prompt, headers, model, sys_prompt, max_tok):
    body = {
        "model": model, "temperature": TEMPERATURE, "max_tokens": max_tok,
        "system": sys_prompt,
        "messages": [{"role":"user","content":prompt}],
    }
    async with session.post(ANTHROPIC_URL, headers=headers, json=body,
        timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as r:
        if r.status == 200:
            d = await r.json()
            content = d.get("content", [])
            if not content:
                raise RuntimeError("[EMPTY] Anthropic returned no content")
            return content[0]["text"].strip()
        raise RuntimeError(f"[{r.status}] {(await r.text())[:120]}")


async def _call_with_retry(caller, session, sem, idx, prompt, headers, model, sys_prompt, max_tok):
    for attempt in range(MAX_RETRIES):
        # Only hold the semaphore during the actual HTTP call, NOT during backoff sleep.
        # Otherwise one rate-limited row blocks a concurrency slot while sleeping.
        try:
            async with sem:
                result = await caller(session, prompt, headers, model, sys_prompt, max_tok)
            return (idx, result)
        except RuntimeError as e:
            msg = str(e)
            if ("429" in msg or "529" in msg or "503" in msg) and attempt < MAX_RETRIES - 1:
                await asyncio.sleep(BACKOFF * 2**attempt)
                continue
            return (idx, f"[ERROR] {msg[:100]}")
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(BACKOFF * 2**attempt); continue
            return (idx, f"[ERROR] {str(e)[:100]}")
    return (idx, "[ERROR] max retries")


# ═══════════════════════════════════════════════════════════════════════════════
#  PROGRESS BAR
# ═══════════════════════════════════════════════════════════════════════════════
class Bar:
    def __init__(self, total, label="Enriching"):
        self.total, self.done, self.errs, self.t0 = total, 0, 0, time.time()
        self.label = label
    def tick(self, err=False):
        self.done += 1; self.errs += int(err)
        el = time.time() - self.t0
        rate = self.done/el if el else 0
        eta = (self.total-self.done)/rate if rate else 0
        pct = self.done/self.total
        filled = int(30*pct)
        print(f"\r  {self.label}: {'█'*filled}{'░'*(30-filled)} {self.done}/{self.total}  "
              f"{rate:.0f}/s  ETA:{eta:.0f}s  err:{self.errs}",
              end="", flush=True, file=sys.stderr)
    def done_msg(self):
        el = time.time()-self.t0
        print(f"\n  ✓ {self.label} done: {self.done} in {el:.1f}s ({self.done/el:.0f}/s). Errors: {self.errs}\n",
              file=sys.stderr)


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN FUNCTION
# ═══════════════════════════════════════════════════════════════════════════════
def enrich(
    df: pd.DataFrame,
    question: str,
    features: list[str],
    column: str,
    api_key: str,
    api: str = "openai",
    model: str = None,
    concurrency: int = CONCURRENCY,
    system_prompt: str = SYSTEM,
    max_tokens: int = MAX_TOKENS,
    crawl: bool = False,
    url_column: str = None,
) -> pd.DataFrame:
    """
    Enrich a DataFrame with AI-generated answers.

    Args:
        api:        "openai" or "anthropic"
        crawl:      If True, fetches each row's website and injects into prompt
        url_column: Column name containing URLs. Auto-detected if None.
    """
    model = model or MODELS[api]

    async def _go():
        # ── Edge case: empty dataframe ──
        if df.empty:
            print("  ⚠ Empty dataset — nothing to enrich.", file=sys.stderr)
            df[column] = []
            return df

        # ── Edge case: strip column whitespace ──
        df.columns = df.columns.str.strip()

        total = len(df)

        print(f"\n  ╔══════════════════════════════════════════════════╗", file=sys.stderr)
        print(f"  ║  GTM ENRICHMENT ENGINE v2                       ║", file=sys.stderr)
        print(f"  ╠══════════════════════════════════════════════════╣", file=sys.stderr)
        print(f"  ║  Rows:        {total:<35}║", file=sys.stderr)
        print(f"  ║  API:         {api:<35}║", file=sys.stderr)
        print(f"  ║  Model:       {model:<35}║", file=sys.stderr)
        print(f"  ║  Concurrency: {concurrency:<35}║", file=sys.stderr)
        print(f"  ║  New column:  {column:<35}║", file=sys.stderr)
        print(f"  ║  Crawl web:   {'YES' if crawl else 'no':<35}║", file=sys.stderr)
        print(f"  ╚══════════════════════════════════════════════════╝\n", file=sys.stderr)
        print(f"  Question: {question}\n", file=sys.stderr)

        # ── STEP 0: Crawl websites if enabled ──
        crawl_texts = {}
        if crawl:
            url_col = url_column or detect_url_column(list(df.columns))
            if not url_col:
                print(f"  ⚠ No URL column found. Skipping crawl.\n", file=sys.stderr)
            else:
                print(f"  Crawling {url_col} column...", file=sys.stderr)
                crawl_sem = asyncio.Semaphore(20)  # lower concurrency for crawling
                bar = Bar(total, "Crawling")
                crawl_conn = aiohttp.TCPConnector(limit=30, ttl_dns_cache=300, ssl=False)
                # Reset index to ensure iloc alignment
                df_reset = df.reset_index(drop=True)
                async with aiohttp.ClientSession(connector=crawl_conn) as session:
                    results = await asyncio.gather(*[
                        crawl_url(session, str(df_reset.iloc[i].get(url_col, "")), crawl_sem)
                        for i in range(total)
                    ], return_exceptions=True)
                    for i, r in enumerate(results):
                        crawl_texts[i] = r if isinstance(r, str) else ""
                        bar.tick(err=not bool(r))
                bar.done_msg()

        # ── STEP 1: Build all prompts ──
        # Use to_dict('records') — 10-30x faster than iterrows() on large DataFrames.
        # Reset index ensures crawl_texts positional alignment with records.
        records = df.reset_index(drop=True).to_dict("records")
        prompts = [(i, build_prompt(r, features, question, crawl_texts.get(i, "")))
                   for i, r in enumerate(records)]

        # ── STEP 2: API calls ──
        if api == "openai":
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            caller = call_openai
        else:
            headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}
            caller = call_anthropic

        sem = asyncio.Semaphore(concurrency)
        bar = Bar(total, "Enriching")
        results = {}

        conn = aiohttp.TCPConnector(limit=concurrency+10, ttl_dns_cache=300)
        async with aiohttp.ClientSession(connector=conn) as session:
            tasks = [_call_with_retry(caller, session, sem, idx, p, headers, model, system_prompt, max_tokens)
                     for idx, p in prompts]
            for coro in asyncio.as_completed(tasks):
                idx, resp = await coro
                results[idx] = resp
                bar.tick(str(resp).startswith("[ERROR"))

        bar.done_msg()

        # Positional result assignment — faster than df.index.map(lambda).
        df[column] = [results.get(i, "[ERROR:MISSING]") for i in range(len(df))]
        return df

    return asyncio.run(_go())


# ═══════════════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════════════
def main():
    p = argparse.ArgumentParser(description="GTM Enrichment Engine v2 — OpenAI/Anthropic + Website Crawling")
    p.add_argument("-i", "--input", required=True)
    p.add_argument("-q", "--question", required=True)
    p.add_argument("-f", "--features", required=True, help="Comma-separated feature columns")
    p.add_argument("-c", "--column", required=True, help="New column name")
    p.add_argument("-k", "--api-key", default=None)
    p.add_argument("-o", "--output", default=None)
    p.add_argument("--api", choices=["openai","anthropic"], default="openai")
    p.add_argument("-m", "--model", default=None)
    p.add_argument("-n", "--concurrency", type=int, default=CONCURRENCY)
    p.add_argument("--crawl", action="store_true", help="Crawl company websites and inject into prompts")
    p.add_argument("--url-column", default=None, help="URL column name (auto-detected if not provided)")
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args()

    # API key
    key = args.api_key or os.environ.get("OPENAI_API_KEY" if args.api=="openai" else "ANTHROPIC_API_KEY")
    if not key:
        sys.exit(f"ERROR: Pass --api-key or set env var for {args.api}")

    # Read
    path = Path(args.input)
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
    print(f"  Loaded: {len(df)} rows × {len(df.columns)} cols from {path.name}", file=sys.stderr)

    if df.empty:
        sys.exit("ERROR: File is empty — no rows to enrich.")

    features = [f.strip() for f in args.features.split(",")]
    bad = [f for f in features if f not in df.columns]
    if bad:
        print(f"  ⚠ Missing: {bad}  |  Available: {list(df.columns)}", file=sys.stderr)
        features = [f for f in features if f in df.columns]
    if not features:
        sys.exit("ERROR: No valid feature columns found. Check column names.")

    if args.limit: df = df.head(args.limit).copy()

    df = enrich(df, args.question, features, args.column, key,
                api=args.api, model=args.model, concurrency=args.concurrency,
                crawl=args.crawl, url_column=args.url_column)

    out = Path(args.output) if args.output else path.with_name(path.stem + "_enriched.csv")
    if out.suffix in (".xlsx",".xls"): df.to_excel(out, index=False)
    else: df.to_csv(out, index=False)

    errs = df[args.column].astype(str).str.startswith("[ERROR").sum()
    print(f"  ✓ Saved to {out} ({len(df)} rows, {errs} errors)", file=sys.stderr)


if __name__ == "__main__":
    main()
