"""
Smoke test for the known-job-ids skip path. Scrapes 5 jobs twice:
  Pass 1: empty known set -> full detail fetch on each
  Pass 2: pass 1's job_ids as the known set -> all detail fetches skipped

Prints elapsed time for both passes; the second should be a fraction of the
first.

Usage: python tools/smoke_skip.py [query]
"""
from __future__ import annotations

import sys
import time

from upwork_analysis.scrape_data import JobsScraper


def run(query: str, known: set[str]) -> tuple[float, list[dict]]:
    t0 = time.time()
    s = JobsScraper(
        search_query=query,
        jobs_per_page=10,
        start_page=1,
        pages_to_scrape=1,
        retries=2,
        headless=False,
        workers=1,
        fast=False,
        known_job_ids=known,
    )
    data = s.scrape_jobs()
    return time.time() - t0, data


def main() -> int:
    query = sys.argv[1] if len(sys.argv) > 1 else "python developer"

    print(f"[smoke-skip] PASS 1 (empty known set, full detail fetch)")
    t1, data1 = run(query, set())
    ids = {row["job_id"] for row in data1 if row.get("job_id")}
    has_detail = sum(1 for r in data1 if r.get("client_location") or r.get("client_total_spent"))
    print(f"[smoke-skip] PASS 1: {len(data1)} jobs in {t1:.1f}s ({has_detail} had detail fields)")
    print(f"[smoke-skip] collected {len(ids)} job_ids for the known set")

    print()
    print(f"[smoke-skip] PASS 2 (same query, all {len(ids)} ids in known set, detail skip)")
    t2, data2 = run(query, ids)
    has_detail2 = sum(1 for r in data2 if r.get("client_location") or r.get("client_total_spent"))
    print(f"[smoke-skip] PASS 2: {len(data2)} jobs in {t2:.1f}s ({has_detail2} had detail fields)")

    print()
    speedup = (t1 / t2) if t2 > 0 else float("inf")
    saved = t1 - t2
    print(f"[smoke-skip] SUMMARY: pass1={t1:.1f}s, pass2={t2:.1f}s, saved={saved:.1f}s, speedup={speedup:.1f}x")
    return 0


if __name__ == "__main__":
    sys.exit(main())
