# GTM Engineering Pipeline Patterns Reference

## Table of Contents
1. [Enrichment Patterns](#enrichment-patterns)
2. [Segmentation Patterns](#segmentation-patterns)
3. [Campaign Patterns](#campaign-patterns)
4. [Prompt Templates](#prompt-templates)

---

## Enrichment Patterns

### Waterfall Enrichment
Try multiple data sources in priority order, stop at first hit.
```
Source A (cheapest) → Source B → Source C (most expensive)
```
Minimize cost while maximizing coverage.

### AI Classification Enrichment
Use LLM to classify/categorize rows based on existing features.

**Best practices:**
- Use `gpt-4o-mini` for binary/categorical classification (fast, cheap)
- Use `gpt-4o` for nuanced analysis requiring reasoning
- Always set `temperature: 0.1-0.3` for consistent results
- Truncate long text fields to ~500 chars to control costs
- Provide explicit answer format in the question (e.g., "Answer with exactly one of: Higher Education, School Education, Both, Unknown")

### Multi-Question Enrichment
Chain multiple enrichment passes to build layered intelligence:

```
Pass 1: "What type of education?" → education_type column
Pass 2: "What is their primary revenue model?" → revenue_model column  
Pass 3: "ICP fit score 1-10 based on: {education_type}, {revenue_model}, {size}" → icp_score column
```

Each pass can reference columns created by previous passes.

---

## Segmentation Patterns

### ICP Tiering
```
Tier 1 (Ideal): Matches ALL must-have criteria + 2+ nice-to-have
Tier 2 (Good): Matches ALL must-have criteria
Tier 3 (Maybe): Matches 80%+ must-have criteria
Tier 4 (Exclude): Fails a must-have criteria
```

### Engagement-Based Segments
```
Hot: Opened 3+ emails OR replied OR clicked
Warm: Opened 1-2 emails, no reply
Cold: No opens after 3+ sends
Dead: Bounced or unsubscribed
```

### Company Stage Segments
```
Early Stage: <50 employees, Seed/Pre-seed funding
Growth: 50-500 employees, Series A-C
Enterprise: 500+ employees, Series D+ or public
Bootstrapped: Any size, no external funding
```

---

## Campaign Patterns

### Cold Email Sequence
```
Day 0: Initial outreach (problem-aware hook)
Day 3: Follow-up 1 (social proof / case study)
Day 7: Follow-up 2 (new angle / content share)
Day 14: Follow-up 3 (breakup email)
```

### Multi-Channel Sequence
```
Day 0: LinkedIn connect request
Day 1: Email 1 (if accepted, reference LinkedIn)
Day 3: LinkedIn comment on their post
Day 5: Email 2 (reference the comment/shared interest)
Day 10: Final email (offer value, no ask)
```

---

## Prompt Templates

### Classification Prompt
```
You are a GTM research analyst. Based on the following company data, classify this company.

Company Data:
{feature_injection}

Classification Question: {question}

Rules:
- Answer with EXACTLY one of the provided categories
- If uncertain, choose the closest match and add "(uncertain)" 
- If data is truly insufficient, answer "Unknown"
```

### Scoring Prompt
```
You are a GTM scoring analyst. Score this company's fit for the following ICP.

ICP Definition:
{icp_description}

Company Data:
{feature_injection}

Score from 1-10 where:
1-3 = Poor fit (wrong industry/size/stage)
4-6 = Moderate fit (partial match)
7-9 = Strong fit (matches most criteria)
10 = Perfect fit (matches all criteria)

Output format: "SCORE: X | REASON: one sentence explanation"
```

### Context Summary Prompt
```
You are a GTM research analyst. Synthesize a brief context summary of this company.

Company Data:
{feature_injection}

Write a 2-3 sentence summary covering:
1. What the company does and who they serve
2. Their approximate size/stage
3. Any notable signals (funding, technology, growth indicators)

Be factual and concise. Do not speculate beyond the data provided.
```

### Email Personalization Prompt
```
You are a cold email copywriter. Write a personalized opening line for this prospect.

Prospect Company:
{feature_injection}

Our Product: {product_description}
Messaging Angle: {angle}

Write ONE opening line (15-25 words) that:
- References something specific about their company
- Connects it to a problem our product solves
- Feels natural, not salesy

Output only the opening line, nothing else.
```
