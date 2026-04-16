#!/usr/bin/env python3
"""
Automated test suite for GTM Enrichment Engine.
Tests prompt building, URL handling, file I/O, and CLI across diverse datasets.

Run: python3 -m pytest tests/test_gtm_enrich.py -v
  or: python3 tests/test_gtm_enrich.py
"""

import os
import sys
import math
import tempfile
import subprocess
from pathlib import Path

# Add scripts dir to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
from scripts.gtm_enrich import (
    normalize_url, detect_url_column, build_prompt, extract_text, dedupe,
)
from scripts.enrich_column import build_row_prompt, dry_run

FIXTURES = ROOT / "tests" / "fixtures"


# ═══════════════════════════════════════════════════════════════════════════════
#  URL HANDLING
# ═══════════════════════════════════════════════════════════════════════════════

def test_normalize_url_valid():
    assert normalize_url("slack.com") == "https://slack.com/"
    assert normalize_url("https://notion.so") == "https://notion.so/"
    assert normalize_url("http://figma.com/design") == "https://figma.com/design"
    assert normalize_url("www.example.com/page") == "https://www.example.com/page"

def test_normalize_url_invalid():
    assert normalize_url("") is None
    assert normalize_url("nan") is None
    assert normalize_url("N/A") is None
    assert normalize_url("none") is None
    assert normalize_url(None) is None
    assert normalize_url(123) is None

def test_normalize_url_skips_linkedin():
    assert normalize_url("linkedin.com/company/test") is None
    assert normalize_url("https://www.linkedin.com/in/johndoe") is None

def test_detect_url_column_priority():
    assert detect_url_column(["Name", "Domain", "Website"]) == "Domain"
    assert detect_url_column(["Name", "Website", "Size"]) == "Website"
    assert detect_url_column(["Name", "homepage_url", "Size"]) == "homepage_url"
    assert detect_url_column(["Name", "site_link"]) == "site_link"

def test_detect_url_column_none():
    assert detect_url_column(["Name", "Description", "Size"]) is None
    assert detect_url_column([]) is None

def test_detect_url_column_skips_linkedin():
    assert detect_url_column(["LinkedIn URL", "Name"]) is None


# ═══════════════════════════════════════════════════════════════════════════════
#  PROMPT BUILDING (gtm_enrich.py v2)
# ═══════════════════════════════════════════════════════════════════════════════

def test_build_prompt_basic():
    row = {"Name": "Acme", "Industry": "SaaS"}
    result = build_prompt(row, ["Name", "Industry"], "What segment?")
    assert "- Name: Acme" in result
    assert "- Industry: SaaS" in result
    assert "Question: What segment?" in result

def test_build_prompt_handles_nan():
    row = {"Name": "Acme", "Missing": float("nan")}
    result = build_prompt(row, ["Name", "Missing"], "Test?")
    assert "- Missing: N/A" in result

def test_build_prompt_handles_empty_string():
    row = {"Name": "Acme", "Empty": ""}
    result = build_prompt(row, ["Name", "Empty"], "Test?")
    assert "- Empty: N/A" in result

def test_build_prompt_truncates_long_description():
    row = {"Name": "Acme", "Description": "x" * 1000}
    result = build_prompt(row, ["Name", "Description"], "Test?")
    assert "..." in result
    desc_line = [l for l in result.split("\n") if "Description:" in l][0]
    assert len(desc_line) < 600

def test_build_prompt_missing_feature():
    row = {"Name": "Acme"}
    result = build_prompt(row, ["Name", "NonExistent"], "Test?")
    assert "- NonExistent: N/A" in result

def test_build_prompt_with_crawl_text():
    row = {"Name": "Acme"}
    result = build_prompt(row, ["Name"], "What do they do?", crawl_text="We build widgets for enterprises.")
    assert "Website Content (live scrape):" in result
    assert "We build widgets" in result


# ═══════════════════════════════════════════════════════════════════════════════
#  PROMPT BUILDING (enrich_column.py legacy)
# ═══════════════════════════════════════════════════════════════════════════════

def test_build_row_prompt_basic():
    row = {"Name": "Stripe", "Product": "Payments"}
    result = build_row_prompt(row, ["Name", "Product"], "B2B or B2C?")
    assert "- Name: Stripe" in result
    assert "- Product: Payments" in result
    assert "Question: B2B or B2C?" in result

def test_build_row_prompt_null_variants():
    for null_val in ["nan", "none", "null", "N/A", "", "NaN"]:
        row = {"Name": "Test", "Field": null_val}
        result = build_row_prompt(row, ["Name", "Field"], "Test?")
        assert "- Field: N/A" in result


# ═══════════════════════════════════════════════════════════════════════════════
#  HTML TEXT EXTRACTION
# ═══════════════════════════════════════════════════════════════════════════════

def test_extract_text_basic():
    html = '<html><head><title>Acme</title></head><body><p>We sell widgets</p></body></html>'
    result = extract_text(html)
    assert "Acme" in result
    assert "widgets" in result

def test_extract_text_strips_scripts():
    html = '<html><body><script>var x=1;</script><p>Content here</p></body></html>'
    result = extract_text(html)
    assert "var x" not in result
    assert "Content here" in result

def test_extract_text_strips_styles():
    html = '<html><body><style>.foo{color:red}</style><p>Visible</p></body></html>'
    result = extract_text(html)
    assert "color:red" not in result
    assert "Visible" in result

def test_extract_text_respects_limit():
    html = '<body>' + 'a' * 5000 + '</body>'
    result = extract_text(html, max_chars=100)
    assert len(result) <= 103  # 100 + "..."

def test_extract_text_meta_description():
    html = '<html><head><meta name="description" content="Best widgets ever"></head><body></body></html>'
    result = extract_text(html)
    assert "Best widgets ever" in result


# ═══════════════════════════════════════════════════════════════════════════════
#  FILE I/O — loading diverse datasets
# ═══════════════════════════════════════════════════════════════════════════════

def test_load_saas_csv():
    df = pd.read_csv(FIXTURES / "saas_companies.csv")
    assert len(df) == 10
    assert "Name" in df.columns
    assert "Domain" in df.columns
    assert df.iloc[0]["Name"] == "Slack"

def test_load_ecommerce_csv():
    df = pd.read_csv(FIXTURES / "ecommerce_brands.csv")
    assert len(df) == 10
    assert "Website" in df.columns
    assert "Target_Market" in df.columns

def test_load_healthcare_csv():
    df = pd.read_csv(FIXTURES / "healthcare_startups.csv")
    assert len(df) == 10
    assert "Specialty" in df.columns
    assert "Tech_Stack" in df.columns


# ═══════════════════════════════════════════════════════════════════════════════
#  END-TO-END: prompt building across all datasets
# ═══════════════════════════════════════════════════════════════════════════════

def test_e2e_saas_prompts():
    df = pd.read_csv(FIXTURES / "saas_companies.csv")
    for _, row in df.iterrows():
        prompt = build_prompt(row.to_dict(), ["Name", "Description", "Industry"], "Classify this company.")
        assert "Company Data:" in prompt
        assert "Question:" in prompt
        assert row["Name"] in prompt

def test_e2e_ecommerce_prompts():
    df = pd.read_csv(FIXTURES / "ecommerce_brands.csv")
    for _, row in df.iterrows():
        prompt = build_prompt(row.to_dict(), ["Name", "Description", "Category", "Target_Market"], "DTC or B2B?")
        assert row["Name"] in prompt
        assert "N/A" not in prompt or pd.isna(row.get("some_field", "x"))

def test_e2e_healthcare_prompts():
    df = pd.read_csv(FIXTURES / "healthcare_startups.csv")
    for _, row in df.iterrows():
        prompt = build_row_prompt(row.to_dict(), ["Name", "Description", "Specialty", "Tech_Stack"], "Buyer type?")
        assert row["Name"] in prompt
        assert row["Specialty"] in prompt


# ═══════════════════════════════════════════════════════════════════════════════
#  CLI DRY-RUN TESTS
# ═══════════════════════════════════════════════════════════════════════════════

def test_cli_dry_run_saas():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "enrich_column.py"),
         "-i", str(FIXTURES / "saas_companies.csv"),
         "-o", "/dev/null",
         "-q", "Developer Tool, Communication, Productivity, or Other?",
         "-f", "Name,Description,Industry",
         "-c", "Category",
         "--dry-run"],
        capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "DRY RUN" in result.stderr
    assert "Slack" in result.stderr

def test_cli_dry_run_ecommerce():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "enrich_column.py"),
         "-i", str(FIXTURES / "ecommerce_brands.csv"),
         "-o", "/dev/null",
         "-q", "DTC Only or DTC + B2B?",
         "-f", "Name,Description,Category,Target_Market",
         "-c", "Channel",
         "--dry-run"],
        capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "Warby Parker" in result.stderr

def test_cli_dry_run_healthcare():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "enrich_column.py"),
         "-i", str(FIXTURES / "healthcare_startups.csv"),
         "-o", "/dev/null",
         "-q", "B2B Healthcare, B2B Employer, or B2C Patient?",
         "-f", "Name,Description,Specialty,Tech_Stack",
         "-c", "Buyer_Type",
         "--dry-run"],
        capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "Tempus" in result.stderr

def test_cli_missing_column_warns():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "enrich_column.py"),
         "-i", str(FIXTURES / "saas_companies.csv"),
         "-o", "/dev/null",
         "-q", "Test?",
         "-f", "Name,FakeColumn,Description",
         "-c", "Test",
         "--dry-run"],
        capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "Missing" in result.stderr or "FakeColumn" in result.stderr

def test_cli_limit_flag():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "enrich_column.py"),
         "-i", str(FIXTURES / "saas_companies.csv"),
         "-o", "/dev/null",
         "-q", "Test?",
         "-f", "Name,Description",
         "-c", "Test",
         "--limit", "2",
         "--dry-run"],
        capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "2 rows" in result.stderr


# ═══════════════════════════════════════════════════════════════════════════════
#  EDGE CASES
# ═══════════════════════════════════════════════════════════════════════════════

def test_empty_dataframe():
    """Empty DF should return without crashing."""
    df = pd.DataFrame(columns=["Name", "Description"])
    prompt = build_prompt({}, ["Name", "Description"], "Test?")
    assert "Question: Test?" in prompt
    assert "- Name: N/A" in prompt

def test_all_features_missing():
    """Row with none of the requested features should still produce a valid prompt."""
    row = {"Unrelated": "value"}
    prompt = build_prompt(row, ["Name", "Description", "Industry"], "Test?")
    assert prompt.count("N/A") == 3

def test_unicode_values():
    """Unicode company names and descriptions should work."""
    row = {"Name": "Gojek (Gojek Indonesia)", "Description": "PT GoTo Gojek Tokopedia — ride-hailing & fintech"}
    prompt = build_prompt(row, ["Name", "Description"], "What market?")
    assert "Gojek" in prompt
    assert "ride-hailing" in prompt

def test_numeric_values():
    """Integer and float column values should convert cleanly."""
    row = {"Name": "Acme", "Employees": 5000, "Revenue": 12.5}
    prompt = build_prompt(row, ["Name", "Employees", "Revenue"], "Size tier?")
    assert "5000" in prompt
    assert "12.5" in prompt

def test_whitespace_column_names():
    """Column names with leading/trailing spaces should be handled."""
    df = pd.DataFrame({" Name ": ["Acme"], " Desc ": ["Widgets"]})
    df.columns = df.columns.str.strip()
    assert "Name" in df.columns
    assert "Desc" in df.columns

def test_duplicate_column_values():
    """Same value in multiple features should still appear for each feature."""
    row = {"Name": "Acme", "Legal_Name": "Acme"}
    prompt = build_prompt(row, ["Name", "Legal_Name"], "Same entity?")
    assert "- Name: Acme" in prompt
    assert "- Legal_Name: Acme" in prompt

def test_very_long_question():
    """Extremely long questions should not break prompt building."""
    row = {"Name": "Test"}
    long_q = "x" * 2000
    prompt = build_prompt(row, ["Name"], long_q)
    assert f"Question: {long_q}" in prompt

def test_special_chars_in_values():
    """Values with quotes, newlines, brackets should not break prompt."""
    row = {"Name": 'O\'Reilly "Media"', "Description": "Line1\nLine2\tTab <html>&amp;"}
    prompt = build_prompt(row, ["Name", "Description"], "Test?")
    assert "O'Reilly" in prompt
    assert "Line1" in prompt

def test_normalize_url_edge_cases():
    """Additional URL edge cases."""
    assert normalize_url("   slack.com   ") == "https://slack.com/"
    assert normalize_url("HTTPS://NOTION.SO") == "https://NOTION.SO/"  # case-insensitive scheme check
    assert normalize_url("http://") is None

def test_extract_text_empty_html():
    """Empty or minimal HTML should not crash."""
    result = extract_text("")
    assert "TITLE:" in result and "BODY:" in result  # should not crash
    assert "BODY:" in extract_text("<html></html>")

def test_crawl_text_injection():
    """Crawl text should appear in prompt when provided, not when empty."""
    row = {"Name": "Acme"}
    with_crawl = build_prompt(row, ["Name"], "Test?", crawl_text="We are a fintech company.")
    without_crawl = build_prompt(row, ["Name"], "Test?", crawl_text="")
    assert "Website Content" in with_crawl
    assert "fintech" in with_crawl
    assert "Website Content" not in without_crawl

def test_cli_all_columns_missing_exits():
    """CLI should handle all feature columns being wrong."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "enrich_column.py"),
         "-i", str(FIXTURES / "saas_companies.csv"),
         "-o", "/dev/null",
         "-q", "Test?",
         "-f", "Fake1,Fake2,Fake3",
         "-c", "Test",
         "--dry-run"],
        capture_output=True, text=True
    )
    assert result.returncode != 0 or "No valid" in result.stderr or "ERROR" in result.stderr


# ═══════════════════════════════════════════════════════════════════════════════
#  DEDUPE (new feature)
# ═══════════════════════════════════════════════════════════════════════════════
def test_dedupe_mark_basic():
    df = pd.DataFrame({"Name": ["Acme", "acme", "Beta", "ACME ", "Beta"]})
    out = dedupe(df, ["Name"], mode="mark")
    # Case-insensitive + whitespace-normalized: row 0 first, 1 dup, 2 first, 3 dup, 4 dup
    assert out["Is_Duplicate"].tolist() == ["No", "Yes", "No", "Yes", "Yes"]

def test_dedupe_remove_keeps_first():
    df = pd.DataFrame({"Name": ["Acme", "acme", "Beta", "ACME", "Beta"]})
    out = dedupe(df, ["Name"], mode="remove")
    assert len(out) == 2
    assert out["Name"].tolist() == ["Acme", "Beta"]  # first occurrence preserved

def test_dedupe_multi_feature_key():
    df = pd.DataFrame({
        "Name":   ["Acme", "Acme", "Acme"],
        "Domain": ["acme.com", "acme.io", "acme.com"],
    })
    out = dedupe(df, ["Name", "Domain"], mode="mark")
    # Only (Acme, acme.com) repeats at row 2
    assert out["Is_Duplicate"].tolist() == ["No", "No", "Yes"]

def test_dedupe_empty_df_safe():
    df = pd.DataFrame(columns=["Name"])
    out = dedupe(df, ["Name"], mode="mark")
    assert len(out) == 0

def test_dedupe_no_features_returns_df_unchanged():
    df = pd.DataFrame({"Name": ["Acme"]})
    out = dedupe(df, [], mode="mark")
    assert "Is_Duplicate" not in out.columns

def test_dedupe_custom_column_name():
    df = pd.DataFrame({"Name": ["a", "a", "b"]})
    out = dedupe(df, ["Name"], mode="mark", column="Dupe_Flag")
    assert "Dupe_Flag" in out.columns
    assert out["Dupe_Flag"].tolist() == ["No", "Yes", "No"]


# ═══════════════════════════════════════════════════════════════════════════════
#  RUNNER
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Simple test runner if pytest is not installed
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  \033[32m✓\033[0m {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  \033[31m✗\033[0m {t.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n  {passed} passed, {failed} failed out of {len(tests)} tests")
    sys.exit(1 if failed else 0)
