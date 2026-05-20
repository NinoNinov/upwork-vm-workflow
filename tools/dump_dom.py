"""
One-off DOM dumper used to find new Upwork selectors when the scraper breaks.

Opens an Upwork search results page + the first N detail pages, saves the raw
HTML to dumps/, and prints quick text-anchored hints (lines around "ago",
"jobs posted", "Total spent") so you can spot the new attribute hooks without
loading each file by hand.

Usage:
    python tools/dump_dom.py
    python tools/dump_dom.py --query "python developer" --jobs 2

Run from the repo root. Uses the same uc=True undetected-chrome that the real
scraper uses, so the fingerprint matches production (Cloudflare-passing).
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup
from seleniumbase import Driver
from upwork_analysis.scrape_data import construct_url, _JOB_ID_RE


REPO_ROOT = Path(__file__).resolve().parent.parent
DUMPS_DIR = REPO_ROOT / "dumps"

# Text anchors we search for in each dump. Lines surrounding these hits are
# printed so the human inspector can read off the new selector attributes.
SEARCH_HINTS = ("ago", "minute", "Posted")
DETAIL_HINTS = ("jobs posted", "Total spent", "Member since", "hire rate", "/hr")


def slug(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", s).strip("-").lower()


def save(path: Path, html: str) -> None:
    path.write_text(html, encoding="utf-8")
    print(f"  -> wrote {path.relative_to(REPO_ROOT)}  ({len(html):,} bytes)")


def grep_hints(html: str, hints: tuple[str, ...], context: int = 1) -> None:
    """Print short context around each hint occurrence (case-insensitive)."""
    lines = html.splitlines()
    for hint in hints:
        needle = hint.lower()
        matches = [i for i, line in enumerate(lines) if needle in line.lower()]
        if not matches:
            print(f"  [hint MISS] {hint!r}")
            continue
        print(f"  [hint HIT ] {hint!r} -> {len(matches)} line(s); first {min(3, len(matches))}:")
        for i in matches[:3]:
            lo = max(0, i - context)
            hi = min(len(lines), i + context + 1)
            for j in range(lo, hi):
                snippet = lines[j].strip()
                if len(snippet) > 240:
                    snippet = snippet[:237] + "..."
                marker = ">>" if j == i else "  "
                print(f"    {marker} L{j}: {snippet}")


def extract_first_job_urls(search_html: str, n: int) -> list[str]:
    """Pull the first N job detail URLs from the search results page."""
    soup = BeautifulSoup(search_html, "html.parser")
    anchors = soup.select(".air3-line-clamp > h2 > a")
    out: list[str] = []
    for a in anchors:
        href = a.get("href", "")
        if not href:
            continue
        if href.startswith("/"):
            href = "https://www.upwork.com" + href
        out.append(href)
        if len(out) >= n:
            break
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--query", default="python developer",
                    help="Search query (default: 'python developer')")
    ap.add_argument("--jobs", type=int, default=2,
                    help="How many detail pages to dump (default: 2)")
    ap.add_argument("--jobs-per-page", type=int, default=10,
                    help="Search-page size (default: 10)")
    ap.add_argument("--headless", action="store_true",
                    help="Run headless. NOT recommended -- Cloudflare blocks it.")
    ap.add_argument("--detail-wait", type=int, default=12,
                    help="Seconds to wait on each detail page before saving.")
    args = ap.parse_args()

    DUMPS_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    query_slug = slug(args.query)

    print(f"[dump] query={args.query!r}  jobs={args.jobs}  headless={args.headless}")
    print(f"[dump] launching undetected Chrome (uc=True)...")
    driver = Driver("chrome", headless=args.headless, uc=True)
    try:
        search_url = construct_url(args.query, args.jobs_per_page, 1)
        print(f"[dump] GET {search_url}")
        driver.get(search_url)
        # Same anchor upstream uses to differentiate "loaded" vs "captcha".
        driver.wait_for_element("article", timeout=20)
        time.sleep(2.0)  # let lazy bits render
        search_html = driver.page_source

        search_path = DUMPS_DIR / f"search-{query_slug}-{ts}.html"
        save(search_path, search_html)
        print("[dump] search-page hint scan:")
        grep_hints(search_html, SEARCH_HINTS)

        urls = extract_first_job_urls(search_html, args.jobs)
        print(f"[dump] found {len(urls)} job URLs on the search page")
        if not urls:
            print("[dump] no anchors matched -- check search HTML manually.")
            return 1

        for idx, url in enumerate(urls, 1):
            jid_match = _JOB_ID_RE.search(url)
            jid = jid_match.group(1) if jid_match else f"job{idx}"
            print(f"\n[dump] ({idx}/{len(urls)}) GET {url}")
            try:
                driver.get(url)
                # We don't wait on the old client_location_selector here on
                # purpose -- that's one of the selectors we suspect is broken.
                # Just wait for the page shell, then a fixed delay.
                driver.wait_for_element("article", timeout=args.detail_wait)
            except Exception as exc:
                print(f"  [warn] wait_for_element failed: {exc}; saving anyway")
            time.sleep(2.5)
            detail_html = driver.page_source
            detail_path = DUMPS_DIR / f"detail-{jid}-{ts}.html"
            save(detail_path, detail_html)
            print(f"[dump] detail-page hint scan ({jid}):")
            grep_hints(detail_html, DETAIL_HINTS)
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    print(f"\n[dump] done. Inspect files in {DUMPS_DIR.relative_to(REPO_ROOT)}/")
    print("[dump] Next: open the HTML files, find stable data-test/data-qa")
    print("       attributes on the lines printed above, and patch the fork.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
