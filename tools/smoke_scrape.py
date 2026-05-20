"""
Smoke test for the upwork_analysis patches. Scrapes one search page (10 jobs)
for one title, prints the fields we care about (time_raw, time, client_*),
and skips the Google Sheet entirely so we can iterate without polluting prod.

Usage: python tools/smoke_scrape.py [query]
"""
from __future__ import annotations

import sys
import time

from upwork_analysis.scrape_data import JobsScraper


def main() -> int:
    query = sys.argv[1] if len(sys.argv) > 1 else "python developer"
    print(f"[smoke] query={query!r}")
    t0 = time.time()

    scraper = JobsScraper(
        search_query=query,
        jobs_per_page=10,
        start_page=1,
        pages_to_scrape=1,
        retries=2,
        headless=False,
        workers=1,
        fast=False,  # we want the detail-page fields too
    )
    data = scraper.scrape_jobs()
    elapsed = time.time() - t0
    print(f"[smoke] scraped {len(data)} jobs in {elapsed:.1f}s")

    if not data:
        print("[smoke] no rows -- bail")
        return 1

    interesting = ("title", "time", "time_raw", "client_location",
                   "client_total_spent", "client_jobs_posted", "client_hire_rate",
                   "client_hourly_rate", "proposals")
    print()
    for i, row in enumerate(data, 1):
        print(f"--- job {i} ---")
        for k in interesting:
            v = row.get(k)
            if isinstance(v, str) and len(v) > 80:
                v = v[:77] + "..."
            print(f"  {k:22s} = {v!r}")

    # Hit-rate summary
    print()
    print("[smoke] hit rates:")
    for k in interesting:
        hits = sum(1 for r in data if r.get(k) not in (None, ""))
        print(f"  {k:22s} {hits}/{len(data)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
