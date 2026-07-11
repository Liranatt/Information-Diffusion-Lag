"""Bracket-ladder de-duplication.

Polymarket publishes macro events as ladders of near-identical questions that
differ only in a date, a bps bracket, or a range bin ("Fed cuts 25bps" /
"50bps" / "75+bps"; "CPI 2.4%" / "2.5%" / "2.6%"). They map to overlapping
assets, so tracking every rung wastes Gemini spend and, live, crowds out
`max_concurrent` with correlated bets on one event.

`event_key` normalizes a question to an event signature; `dedup_markets` keeps a
single representative per signature (the longest-duration / broadest-scope one).
Formerly `data_pipeline/dedup_macro.py`.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime


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


def _duration_days(market: dict) -> float:
    """Days from created_at to end_at; -inf if unparseable (so it never wins)."""
    try:
        created = datetime.fromisoformat(str(market["created_at"]).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(market["end_at"]).replace("Z", "+00:00"))
        return (end - created).total_seconds() / 86400.0
    except Exception:
        return float("-inf")


def dedup_markets(markets: list[dict]) -> list[dict]:
    """Keep one market per event signature — the longest-duration representative.

    Input/output are market dicts with at least `question`, `created_at`,
    `end_at`. Order of the kept markets follows first appearance.
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for m in markets:
        groups[event_key(m.get("question", ""))].append(m)

    kept: list[dict] = []
    for group in groups.values():
        best = max(group, key=_duration_days)
        kept.append(best)
    return kept
