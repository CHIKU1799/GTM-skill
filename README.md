# GTM Engineering Skill

An AI-powered agent skill for Go-To-Market teams to enrich, segment, and activate prospect lists at scale. Take **any CSV of companies or people**, ask **any question**, watch a new column fill with AI-generated answers — for all rows.

Works with any dataset: SaaS, e-commerce, healthcare, fintech, education, or custom schemas.

## Features

- **Interactive browser UI** (`gtm_enrichment_ui.html`) — drag-drop CSV, type question, watch live column fill
- **Python async engine** (`gtm_enrich.py`) — 50+ parallel API calls for 25K-row datasets in minutes
- **Dual API support** — OpenAI (GPT-4o family) or Anthropic Claude (Haiku/Sonnet)
- **Website crawling** — optionally fetch each company's homepage and inject into prompts
- **Auto-detection** — auto-picks URL columns, feature columns, dataset type
- **Manual prompt editing** — override the auto-generated prompt for any row
- **4 color themes** — Dark, Light, Ocean, Forest
- **Cost estimation** — know the price before you run
- **Retry + backoff** — handles rate limits gracefully
- **Local-first** — data never leaves your browser except for API calls
- **30 automated tests** — across SaaS, e-commerce, and healthcare datasets

## Quick Start

### Browser (easiest)

Open `scripts/gtm_enrichment_ui.html` in Chrome or Firefox. Drop your CSV, select features, type a question, paste your API key, hit Enrich.

### Python (for large datasets)

```bash
pip install aiohttp pandas openpyxl

# OpenAI
python3 scripts/gtm_enrich.py \
  -i companies.csv \
  -q "Classify this company's primary market segment." \
  -f "Name,Description,Industry" \
  -c "Segment" \
  -k "sk-..." \
  --api openai

# Anthropic Claude
python3 scripts/gtm_enrich.py \
  -i companies.csv \
  -q "Score ICP fit 1-10 for a B2B SaaS buyer." \
  -f "Name,Description,Industry,Employees" \
  -c "ICP_Score" \
  -k "sk-ant-..." \
  --api anthropic

# With website crawling
python3 scripts/gtm_enrich.py \
  -i companies.csv \
  -q "What does this company actually sell?" \
  -f "Name,Description" \
  -c "Product_Summary" \
  -k "sk-..." \
  --api openai --crawl
```

Or as a Python function:

```python
from scripts.gtm_enrich import enrich
import pandas as pd

df = enrich(
    df=pd.read_csv("companies.csv"),
    question="Classify as: SaaS, Services, Marketplace, Hardware, or Other",
    features=["Name", "Description", "Industry"],
    column="Company_Type",
    api_key="sk-...",
    api="openai",          # or "anthropic"
    crawl=True,            # fetch websites
)
df.to_csv("enriched.csv", index=False)
```

## The 10 GTM Capabilities

The enrichment engine powers all of these by varying the question and feature selection:

1. **List Enrichment** — add AI-generated columns (the core engine)
2. **List Segmentation** — group prospects by shared traits
3. **Context Building** — synthesize company summaries
4. **ICP Scoring** — score companies against your ideal customer profile
5. **Market Research** — analyze market patterns and trends
6. **People Search** — find decision-makers at target companies
7. **Email Generation** — generate personalized cold email copy
8. **Campaign Sending** — upload and send via email sequencing platforms
9. **Hypothesis Building** — frame and test GTM experiments
10. **Post Engagement** — analyze campaign results for optimization

## Tested Across Industries

| Dataset | Columns | Test Questions |
|---------|---------|---------------|
| **SaaS Companies** | Name, Description, Domain, Industry, Employees, Funding | Classify category, score ICP fit |
| **E-Commerce Brands** | Name, Description, Website, Category, Revenue, Target Market | DTC vs B2B channels, audience segment |
| **Healthcare Startups** | Name, Description, Domain, Specialty, Stage, Tech Stack | Buyer type, AI maturity rating |

## Installation as a Claude Skill

```bash
# Clone the repo
git clone https://github.com/CHIKU1799/GTM-skill.git

# Copy to your Claude skills directory
cp -r GTM-skill ~/.claude/skills/gtm-engineering

# Done — the skill auto-triggers on relevant prompts
```

## Running Tests

```bash
# 30 automated tests across 3 datasets
python3 tests/test_gtm_enrich.py

# With pytest
python3 -m pytest tests/test_gtm_enrich.py -v
```

Tests cover: URL normalization, prompt building, HTML extraction, file I/O, CLI dry-runs, missing column handling, multi-dataset end-to-end.

## Performance & Cost

| Rows | Model | Provider | Concurrency | Time | Cost |
|------|-------|----------|------------|------|------|
| 1,000 | gpt-4o-mini | OpenAI | 50 | ~30s | ~$0.10 |
| 5,000 | gpt-4o-mini | OpenAI | 50 | ~2min | ~$0.50 |
| 25,000 | gpt-4o-mini | OpenAI | 50 | ~10min | ~$2.50 |
| 5,000 | claude-haiku-4-5 | Anthropic | 30 | ~3min | ~$1.25 |
| 5,000 | gpt-4o | OpenAI | 30 | ~4min | ~$8.00 |

## Project Structure

```
gtm-engineering/
├── SKILL.md                            # Skill brain — orchestration & decision logic
├── README.md                           # This file
├── LICENSE                             # MIT
├── scripts/
│   ├── gtm_enrich.py                   # Python engine v2 (OpenAI + Anthropic + crawl)
│   ├── enrich_column.py                # Python engine v1 (OpenAI + Anthropic)
│   └── gtm_enrichment_ui.html          # Browser UI (drag-drop CSV, live enrichment)
├── tests/
│   ├── test_gtm_enrich.py              # 30 automated tests
│   └── fixtures/
│       ├── saas_companies.csv          # 10 SaaS companies
│       ├── ecommerce_brands.csv        # 10 DTC/e-commerce brands
│       └── healthcare_startups.csv     # 10 healthcare startups
└── references/
    └── pipeline-patterns.md            # Prompt templates & GTM patterns
```

## License

MIT

## Author

Built for human-AI collaboration research by Nishant Kumar.
