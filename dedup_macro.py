"""Deduplicate macro catalyst results by removing date-bracket and range-bin noise.

Groups questions by underlying event, keeps one representative per group,
marks the rest as BRACKET_NOISE in catalyst_results_macro.json.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
CACHE_PATH = ROOT / "data" / "markets_cache_historical.json"
MACRO_RESULTS_PATH = ROOT / "data" / "catalyst_results_macro.json"


def event_key(question: str) -> str:
    """Normalize a question to an event signature for grouping."""
    q = question.lower().strip()

    # --- Dates: drop day-of-month, keep month ---
    q = re.sub(
        r'((?:january|february|march|april|may|june|july|august|september|'
        r'october|november|december))\s+\d{1,2}(?:st|nd|rd|th)?',
        r'\1', q, flags=re.I,
    )
    q = re.sub(
        r'((?:jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec))\s+\d{1,2}(?:st|nd|rd|th)?',
        r'\1', q, flags=re.I,
    )
    q = re.sub(r'\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b', '', q)

    # --- BPS brackets (range first, then single) ---
    q = re.sub(r'\b\d+[-–]\d+\s*bps?\b', 'X bps', q)  # "1-25 bps", "26-50 bps"
    q = re.sub(r'>\s*\d+\s*bps?\b', 'X bps', q)              # ">50 bps"
    q = re.sub(r'\b\d+\s+or\s+more\s+bps?\b', 'X bps', q)    # "50 or more bps"
    q = re.sub(r'\b\d+\+?\s*bps?\b', 'X bps', q)              # "25 bps", "50+ bps"
    q = re.sub(r'\b\d+\s*basis\s+points?\b', 'X bps', q)      # "75 basis points"
    q = re.sub(r'with\s*[<>≤≥]?\s*\d+\s*dissents?', 'with X dissents', q)

    # --- Fed path combos: Cut-Cut-Cut / Cut-Pause-Cut etc. ---
    q = re.sub(
        r'((?:cut|pause)[\s––-]+(?:cut|pause)[\s––-]+(?:cut|pause))',
        'PATH', q,
    )

    # --- Rate cut counts ---
    q = re.sub(r'\b\d\+?\s+(fed\s+rate\s+cuts?)', r'X \1', q)
    q = re.sub(r'(cut\s+interest\s+rates?\s+)\d\+?\s*times?', r'\1X times', q)

    # --- Fed Chair candidates ---
    q = re.sub(r'(announce\s+).+?(\s+as\s+next\s+fed\s+chair)', r'\1X\2', q)

    # --- "less/more than or equal to" before percentages ---
    q = re.sub(r'less\s+than\s+or\s+equal\s+to\s+', '', q)
    q = re.sub(r'greater\s+than\s+or\s+equal\s+to\s+', '', q)

    # --- Percentages (handle ≤/≥ symbols and text) ---
    q = re.sub(r'[≤≥<>]\s*', '', q)  # strip comparison symbols
    q = re.sub(r'-?\d+\.?\d*\s*%', 'X%', q)
    q = re.sub(r'X%\s+or\s+(?:less|more|lower|higher)', 'X%', q)
    q = re.sub(r'(?:exactly|at\s+least|at\s+most)\s+X%', 'X%', q)

    # --- Job counts ---
    q = re.sub(r'between\s+[\d,]+k?\s+and\s+[\d,]+k?', 'between X and Y', q)
    q = re.sub(r'\d+[-–]\d+k?\s+jobs', 'X jobs', q)
    q = re.sub(r'(?:more|fewer|less|greater|over)\s+than\s+[\d,]+k?\s*(?:jobs)?', 'X jobs', q)
    q = re.sub(r'(?:at\s+least|over)\s+[\d,]+k?\s*(?:jobs)?', 'X jobs', q)
    q = re.sub(r'[\d,]+\s+or\s+more\s+(?:jobs)', 'X jobs', q)

    # --- Ship counts ---
    q = re.sub(r'\d+[-–]\d+\s+ships', 'X ships', q)
    q = re.sub(r'\d+\s+or\s+more\s+ships', 'X ships', q)

    # --- Date ranges ---
    q = re.sub(
        r'(?:january|february|march|april|may|june|july|august|september|'
        r'october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)'
        r'\s*\d*\s*[-–]\s*'
        r'(?:(?:january|february|march|april|may|june|july|august|september|'
        r'october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)\s*)?'
        r'\d*',
        'DATE_RANGE', q, flags=re.I,
    )

    # --- Transit counts ---
    q = re.sub(r'between\s+\d+\s+and\s+\d+\s+average\s+daily\s+transits', 'X transits', q)
    q = re.sub(r'\d+\s+or\s+more\s+average\s+daily\s+transits', 'X transits', q)
    q = re.sub(r'be\s+between\s+\d+\s+and\s+\d+\s+on', 'be X on', q)

    # --- Dollar amounts ---
    q = re.sub(r'\$[\d.]+[MBKmb]?', '$X', q)

    # --- Bare large numbers (250,000 jobs) ---
    q = re.sub(r'[\d,]{4,}\s*(?:jobs)', 'X jobs', q)

    # --- Years ---
    q = re.sub(r'\b20\d{2}\b', 'YEAR', q)

    # --- "in the next three decisions (X)" -> normalize the window ---
    q = re.sub(r'in\s+the\s+next\s+three\s+decisions\s*\([^)]*\)', 'in next 3 decisions', q)

    # --- Cleanup ---
    q = re.sub(r'\s+', ' ', q).strip()
    q = re.sub(r'\s*,\s*', ', ', q)

    return q


def pick_best(group: list[dict], cache_by_id: dict) -> str:
    """From a group of duplicate questions, pick the best one to keep.

    Prefer the one with the longest duration (broadest scope).
    """
    best_id = None
    best_duration = -1
    for r in group:
        m = cache_by_id.get(r["market_id"], {})
        try:
            from datetime import datetime
            created = datetime.fromisoformat(m.get("created_at", "2020-01-01"))
            end = datetime.fromisoformat(m.get("end_at", "2020-01-01"))
            duration = (end - created).days
        except Exception:
            duration = 0
        if duration > best_duration:
            best_duration = duration
            best_id = r["market_id"]
    return best_id


def main():
    cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    by_id = {m["market_id"]: m for m in cache}

    results = json.loads(MACRO_RESULTS_PATH.read_text(encoding="utf-8"))

    # Revert any previous BRACKET_NOISE back to CATALYST for a fresh run
    for r in results:
        if r["verdict"] == "BRACKET_NOISE":
            r["verdict"] = "CATALYST"

    results_by_id = {r["market_id"]: r for r in results}

    positive = [r for r in results if r["verdict"] == "CATALYST" and r["positive_sentiment"]]
    print(f"Positive catalysts before dedup: {len(positive)}")

    # Group by event key
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in positive:
        m = by_id.get(r["market_id"], {})
        q = m.get("question", "")
        key = event_key(q)
        groups[key].append(r)

    # Find groups with duplicates
    kept_ids = set()
    killed_count = 0
    single_count = 0

    for key, group in groups.items():
        if len(group) == 1:
            kept_ids.add(group[0]["market_id"])
            single_count += 1
        else:
            best = pick_best(group, by_id)
            kept_ids.add(best)
            killed_count += len(group) - 1

    # Mark killed ones
    for r in results:
        if r["market_id"] not in kept_ids and r["verdict"] == "CATALYST" and r["positive_sentiment"]:
            r["verdict"] = "BRACKET_NOISE"

    # Count final
    final_positive = [r for r in results if r["verdict"] == "CATALYST" and r["positive_sentiment"]]
    print(f"Unique events (kept): {len(final_positive)}")
    print(f"Bracket duplicates killed: {killed_count}")
    print(f"Single (no dups): {single_count}")

    # Show the biggest duplicate groups
    big_groups = sorted(groups.items(), key=lambda x: len(x[1]), reverse=True)
    print(f"\n=== Largest duplicate groups ===")
    for key, group in big_groups[:20]:
        if len(group) <= 1:
            break
        kept = pick_best(group, by_id)
        kept_q = by_id.get(kept, {}).get("question", "???")
        print(f"\n  Group ({len(group)} questions, keeping 1):")
        print(f"    Key: {key[:120]}")
        print(f"    Kept: {kept_q[:120]}")
        sample_killed = [g for g in group if g["market_id"] != kept][:3]
        for s in sample_killed:
            sq = by_id.get(s["market_id"], {}).get("question", "???")
            print(f"    Kill: {sq[:120]}")

    # Show what's left by category
    print(f"\n=== What's left ({len(final_positive)} positive catalysts) ===")
    import re as re2

    def categorize(q):
        q_lower = q.lower()
        if re2.search(r'\binflation\b|\bcpi\b', q_lower): return 'CPI/Inflation'
        if re2.search(r'\bjobs?\b|\bpayroll\b|\bnonfarm\b|\bunemployment\b', q_lower): return 'Jobs/Employment'
        if re2.search(r'\bfed\b|\bfomc\b|rate\s+(cut|hike)|interest\s+rate', q_lower): return 'Fed/Rates'
        if re2.search(r'\bgdp\b', q_lower): return 'GDP'
        if re2.search(r'\btariff\b|\btrade\s+war\b', q_lower): return 'Tariffs/Trade'
        if re2.search(r'\biran\b|\bhormuz\b', q_lower): return 'Iran/Hormuz'
        if re2.search(r'\bisrael\b|\blebanon\b|\bhezbollah\b|\bgaza\b', q_lower): return 'Israel/MidEast'
        if re2.search(r'\bipos?\b', q_lower): return 'IPO'
        if re2.search(r'\bfda\b', q_lower): return 'FDA'
        if re2.search(r'\bmerger\b|\bacquisition\b|\bantitrust\b', q_lower): return 'M&A/Antitrust'
        if re2.search(r'\bsanction', q_lower): return 'Sanctions'
        if re2.search(r'\bshutdown\b|\bdebt\s+ceiling\b|\bdeficit\b', q_lower): return 'Fiscal Policy'
        if re2.search(r'\binvade\b|\binvasion\b|\bstrike\b|\bmilitary\b|\bnuclear\b|\bwar\b', q_lower): return 'Military/War'
        if re2.search(r'\bexecutive\s+order\b|\btrump\b', q_lower): return 'Executive/Trump'
        return 'Other'

    from collections import Counter
    cats = Counter()
    for r in final_positive:
        m = by_id.get(r["market_id"], {})
        cats[categorize(m.get("question", ""))] += 1
    for cat, count in cats.most_common():
        print(f"  {cat:<25} {count:>4}")

    # Save
    MACRO_RESULTS_PATH.write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nSaved to {MACRO_RESULTS_PATH}")


if __name__ == "__main__":
    main()
