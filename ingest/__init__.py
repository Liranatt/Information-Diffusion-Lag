"""Consolidated data ingestion: scan -> dedup -> regex prefilter -> Gemini gate
-> asset mapping -> probability/price download -> artifact build.

Single source for the cleaning chain shared by historical backfill and live
discovery. Populated during the ingest-consolidation step.
"""
