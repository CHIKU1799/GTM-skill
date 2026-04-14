# GTM Engineering Skill

An AI-powered agent skill for Go-To-Market teams to enrich, segment, and activate prospect lists at scale. Take any CSV of companies, ask any question, watch a new column fill with AI-generated answers — for all rows.

## Features

- **Interactive browser UI** (`gtm_enrichment_ui.html`) — drag-drop CSV, type question, watch live column fill
- **Python async engine** (`gtm_enrich.py`) — 50+ parallel API calls for 25K-row datasets in minutes
- **Dual API support** — OpenAI (GPT-4o family) or Anthropic Claude (Haiku/Sonnet)
- **Website crawling** — optionally fetch each company's homepage and inject into prompts
- **Manual prompt editing** — override the auto-generated prompt for any row
- **4 color themes** — Dark, Light, Ocean, Forest
- **Smart auto-detection** — auto-picks URL columns, feature columns
- **Cost estimation** — know the price before you run
- **Retry + backoff** — handles rate limits gracefully
- **Local-first** — data never leaves your browser except for API calls

## Quick Start

### Browser (easiest)

Open `scripts/gtm_enrichment_ui.html` in Chrome or Firefox. Drop your CSV, select features, type a question, paste your API key, hit Enrich.

### Python (for large datasets)

```bash
pip install aiohttp pandas openpyxl

python scripts/gtm_enrich.py \
  -i startups.csv \
  -q "Is this firm into Higher Education or School Education?" \
  -f "Name,Description,Education Category,Education Level" \
  -c "Education_Type" \
  -k "sk-..." \
  --api openai \
  --crawl
```

Or as a Python function:

```python
from gtm_enrich import enrich
import pandas as pd

df = enrich(
    df=pd.read_csv("startups.csv"),
    question="Is this Higher Education or School Education?",
    features=["Name", "Description", "Education Category"],
    column="Education_Type",
    api_key="sk-...",
    api="openai",          # or "anthropic"
    crawl=True,            # fetch websites
)
df.to_csv("enriched.csv", index=False)
```

## The 10 GTM Capabilities

The enrichment engine powers all of these by varying the question and feature selection:

1. **Context Building** — synthesize company summaries
2. **Campaign Sending** — generate personalized cold email copy
3. **Email Search & Verification** — find and validate email addresses
4. **Hypothesis Building** — frame and test GTM experiments
5. **List Building** — source prospect lists from ICP criteria
6. **List Enrichment** — add AI-generated columns (the core engine)
7. **List Segmentation** — group prospects by shared traits
8. **Market Research** — analyze market patterns and trends
9. **People Search** — find decision-makers at target companies
10. **Post Engagement** — analyze campaign results for optimization

## Installation as a Claude Skill

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/gtm-engineering-skill.git

# Copy to your Claude skills directory
cp -r gtm-engineering-skill ~/.claude/skills/gtm-engineering

# Done — the skill auto-triggers on relevant prompts
```

## Performance & Cost

| Rows | Model | Concurrency | Time | Cost |
|------|-------|------------|------|------|
| 1,000 | gpt-4o-mini | 50 | ~30s | ~$0.10 |
| 5,000 | gpt-4o-mini | 50 | ~2min | ~$0.50 |
| 25,000 | gpt-4o-mini | 50 | ~10min | ~$2.50 |
| 5,000 | claude-haiku-4-5 | 30 | ~3min | ~$1.00 |

## Project Structure

```
gtm-engineering/
├── SKILL.md                       # Skill metadata for Claude
├── README.md                      # This file
├── scripts/
│   ├── gtm_enrich.py              # Python enrichment engine
│   ├── gtm_enrichment_ui.html     # Browser UI
│   └── enrich_column.py           # Legacy CLI (still works)
└── references/
    └── pipeline-patterns.md       # Prompt templates & patterns
```

## License

MIT

## Author

Built for human-AI collaboration research by Nishant Kumar.
