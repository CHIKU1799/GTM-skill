---
name: gtm-engineering
description: "GTM (Go-To-Market) Engineering agent for AI-powered list enrichment, segmentation, and outbound pipelines. Use this skill whenever the user wants to: enrich a CSV/Excel by adding new AI-generated columns, classify or score companies/people in a spreadsheet, fill column values using AI prompts per row, do bulk AI enrichment, build prospect lists, segment lists, do market research, people search, email search/verification, campaign sending, hypothesis building, context building, or post engagement analysis. Also trigger when someone uploads a spreadsheet and asks any question about the rows — like 'classify these companies', 'score these leads', 'is this firm X or Y', or 'add a column that tells me...'. This is the core GTM automation skill."
---

# GTM Engineering Agent

You are a GTM Engineering agent. Your core job: take a user's spreadsheet of companies/people, take their question, and fill a new column with AI-generated answers — fast, accurate, and at scale.

## Two Ways to Use This

### Interactive UI (recommended for exploring data)
Open `scripts/gtm_enrichment_ui.html` in any browser. It gives you:
- **Left side**: Your CSV displayed as a table — all rows, all columns visible
- **Right side**: Control panel where you select features, type your question, enter API key
- **Prompt preview**: See the exact prompt being built from real row values before running
- **Live fill**: Watch the new column fill in real-time as API calls complete
- **Download**: One-click download of the enriched CSV

To launch: just open the HTML file in Chrome/Firefox. Everything runs in-browser, your data never touches any server except OpenAI.

### Script Mode (recommended for large datasets / automation)
Use `scripts/gtm_enrich.py` from terminal or Python. Faster for 1000+ rows thanks to async batching.

---

## How the Enrichment Engine Works

The enrichment engine (`scripts/gtm_enrich.py` and the UI) both follow the same flow:

### Step 1 — User provides inputs
Three things are needed:
1. **A data file** (CSV or Excel) with rows = companies, columns = features
2. **A question** — e.g., "Is this firm into Higher Education or School Education?"
3. **Which feature columns to use** — e.g., Name, Description, Education Category, Education Level

### Step 2 — Rich context prompt is built per row
For each row, the script injects ALL selected feature values into a structured prompt:

```
SYSTEM: You are a senior GTM research analyst...
USER:
Company Data:
- Name: Yocket
- Description: At Yocket, we're all about making the study abroad journey easier...
- Education Category: Higher Education
- Sub-Category: Study Abroad / International
- Education Level: (empty)

Question: Is this firm into Higher Education or School Education?
```

This gives the LLM full context from every feature before answering. The description is truncated at 500 chars to control cost.

### Step 3 — Async blast to OpenAI
The script fires up to 50 parallel requests using raw `aiohttp` (no SDK overhead). Requests that hit rate limits get automatic exponential backoff. A live progress bar shows speed and ETA.

### Step 4 — New column is written
Results are mapped back to each row by index and written as a new column in the output file.

---

## Running It

### Option A: Command Line
```bash
python scripts/enrich_column.py \
  -i "your_data.csv" \
  -o "enriched_data.csv" \
  -q "Mention if the firm is into Higher Education services or School Education services." \
  -f "Name,Description,Education Category,Sub-Category,Education Level" \
  -c "Education_Type" \
  -k "sk-YOUR-OPENAI-KEY" \
  --model gpt-4o-mini \
  --concurrency 50
```

### Option B: Python Function Call
```python
from scripts.enrich_column import enrich_dataframe
import pandas as pd

df = pd.read_csv("your_data.csv")
df = enrich_dataframe(
    df=df,
    question="Mention if the firm is into Higher Education services or School Education services.",
    features=["Name", "Description", "Education Category", "Sub-Category", "Education Level"],
    new_column="Education_Type",
    api_key="sk-YOUR-OPENAI-KEY",
    model="gpt-4o-mini",
    concurrency=50,
)
df.to_csv("enriched_data.csv", index=False)
```

### Option C: Dry Run (test prompts, no API cost)
```bash
python scripts/enrich_column.py \
  -i "your_data.csv" -o "test.csv" \
  -q "Your question here" \
  -f "Name,Description,Education Category" \
  -c "test_col" \
  --dry-run
```

---

## Chaining Multiple Questions

Each question adds one column. Chain them by feeding the output of Q1 as input to Q2:

```bash
# Question 1: classify education type
python scripts/enrich_column.py -i data.csv -o data_q1.csv \
  -q "Higher Education or School Education?" \
  -f "Name,Description,Education Category,Education Level" \
  -c "Education_Type" -k sk-...

# Question 2: assess tech maturity (now has Education_Type column too)
python scripts/enrich_column.py -i data_q1.csv -o data_q2.csv \
  -q "Rate this company's technology maturity: High, Medium, or Low" \
  -f "Name,Description,Size,Funding,Education_Type" \
  -c "Tech_Maturity" -k sk-...
```

---

## The 10 GTM Capabilities

The enrichment engine powers most of these. Each capability is essentially a different **question + feature selection** passed to the same engine.

| # | Capability | What it does | How to use |
|---|-----------|-------------|------------|
| 1 | **Context Building** | Synthesize a rich company summary | Question: "Write a 2-sentence context summary" using all features |
| 2 | **Campaign Sending** | Generate personalized email copy | Question: "Write a cold email opening line" using context + ICP features |
| 3 | **Email Search** | Find/validate emails | Use domain + name patterns, or API integrations if available |
| 4 | **Hypothesis Building** | Frame GTM test hypotheses | Structure as: Segment → Signal → Angle → Metric → Test design |
| 5 | **List Building** | Source prospect lists from ICP criteria | Define ICP filters, query APIs or structure manual searches |
| 6 | **List Enrichment** | Add AI-generated columns to any list | The core engine — any question, any features, any new column |
| 7 | **List Segmentation** | Group prospects by shared traits | Question: "Classify into segment A, B, C, or D" |
| 8 | **Market Research** | Analyze market patterns and trends | Question: "What market does this company serve?" + web research |
| 9 | **People Search** | Find decision-makers at target companies | Use LinkedIn/Apollo APIs, or enrich with "Likely buyer title" |
| 10 | **Post Engager** | Analyze campaign results for optimization | Score engagement tiers, recommend next actions |

---

## Performance & Cost Guide

| Dataset Size | Model | Concurrency | Time | Est. Cost |
|-------------|-------|------------|------|-----------|
| 100 rows | gpt-4o-mini | 50 | ~5s | ~$0.01 |
| 1,000 rows | gpt-4o-mini | 50 | ~30s | ~$0.10 |
| 5,000 rows | gpt-4o-mini | 50 | ~2min | ~$0.50 |
| 25,000 rows | gpt-4o-mini | 50 | ~10min | ~$2.50 |
| 5,000 rows | gpt-4o | 30 | ~4min | ~$8.00 |

Reduce `--concurrency` to 20 if you hit rate limits on a lower-tier OpenAI account.

---

## Installation on Your System

```bash
# 1. Copy the gtm-engineering folder to your Claude skills directory
cp -r gtm-engineering/ ~/.claude/skills/gtm-engineering/

# 2. Install Python dependencies (one time)
pip install aiohttp pandas openpyxl

# 3. Set your API key (or pass via --api-key each time)
export OPENAI_API_KEY="sk-your-key-here"

# 4. Test with a dry run
python ~/.claude/skills/gtm-engineering/scripts/enrich_column.py \
  -i your_data.csv -o test.csv \
  -q "Test question" -f "Name,Description" -c "test" --dry-run
```

The skill auto-installs dependencies if missing, so step 2 is optional.

---

## Tips for Writing Good Enrichment Questions

**Be specific about output format:**
- Bad: "What kind of education does this company do?"
- Good: "Classify as exactly one of: Higher Education, School Education, Both, or Unknown"

**Reference the features you're sending:**
- Good: "Based on the Description and Education Level, determine if..."

**For scoring, define the scale:**
- Good: "Score ICP fit 1-10 where 1=no fit, 5=partial, 10=perfect match. Output only the number."

**For boolean checks, force binary output:**
- Good: "Does this company offer online courses? Answer exactly: Yes or No"
