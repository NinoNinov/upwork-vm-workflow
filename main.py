"""
Entry point for the Upwork-VM-workflow pipeline.

Phased execution:
  1. Load configuration, job titles, and configure logging.
  2. Scrape all job titles in parallel (ThreadPoolExecutor).
  3. Enrich the combined DataFrame (continent, regex flags, StableEncoder).
  4. Append new rows to Google Sheets (dedup against existing descriptions).
  5. Notify n8n via webhook with the run summary.
"""
from __future__ import annotations

import logging
import os
import sys
import uuid
from datetime import datetime, timezone

import pandas as pd

from config import (
    N8nConfig,
    ScrapingConfig,
    ScrapingResult,
    SheetsConfig,
    load_job_titles,
)
from data_processor import process_upwork_data, validate_dataframe
from job_scraper import JobScraper
from n8n_notifier import notify_n8n
from sheets_writer import SheetsWriter
from utils import configure_logging

configure_logging(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
    json_logs=os.environ.get("JSON_LOGS", "false").lower() == "true",
)
logger = logging.getLogger(__name__)


def main() -> ScrapingResult:
    run_id = uuid.uuid4().hex
    started_at = datetime.now(timezone.utc)
    logger.info("Run %s started at %s", run_id, started_at.isoformat())

    scraping_config = ScrapingConfig()
    sheets_config = SheetsConfig()
    n8n_config = N8nConfig()

    try:
        sheets_config.validate()
    except (ValueError, FileNotFoundError) as exc:
        logger.error("Sheets config invalid: %s", exc)
        _safe_notify(n8n_config, run_id, started_at, "error", 0, 0, 0, [], sheets_config, str(exc))
        return ScrapingResult.FAILURE

    job_titles = load_job_titles()
    if not job_titles:
        logger.error("No job titles loaded -- aborting.")
        _safe_notify(n8n_config, run_id, started_at, "error", 0, 0, 0, [], sheets_config,
                     "No job titles loaded.")
        return ScrapingResult.FAILURE

    scraper = JobScraper(scraping_config)

    # ------------------------------------------------------------------
    # Phase 1: Parallel scrape
    # ------------------------------------------------------------------
    logger.info("Scraping %d titles with %d workers...",
                len(job_titles), scraping_config.max_workers)
    scraped = scraper.scrape_jobs_per_title_parallel(job_titles)

    titles_failed = [t for t in job_titles if t not in scraped]
    if not scraped:
        logger.error("Scraping returned no data for any title.")
        _safe_notify(n8n_config, run_id, started_at, "error", 0, 0, 0,
                     titles_failed, sheets_config, "All titles failed to scrape.")
        return ScrapingResult.FAILURE

    # ------------------------------------------------------------------
    # Phase 2: Combine + validate + enrich
    # ------------------------------------------------------------------
    combined = pd.concat(scraped.values(), ignore_index=True)
    logger.info("Combined raw DataFrame: %d rows from %d title(s).",
                len(combined), len(scraped))

    if not validate_dataframe(combined):
        logger.error("Combined DataFrame failed validation -- aborting.")
        _safe_notify(n8n_config, run_id, started_at, "error", len(combined), 0, 0,
                     titles_failed, sheets_config, "Validation failed.")
        return ScrapingResult.FAILURE

    processed = process_upwork_data(combined)
    logger.info("Enriched DataFrame: %d rows, %d columns.",
                len(processed), len(processed.columns))

    # ------------------------------------------------------------------
    # Phase 3: Sheets append (dedup-on-write)
    # ------------------------------------------------------------------
    try:
        writer = SheetsWriter(
            sheet_id=sheets_config.sheet_id,
            tab=sheets_config.tab,
            credentials_path=sheets_config.credentials_path,
        )
        stats = writer.append_new(processed)
    except Exception as exc:
        logger.exception("Sheets write failed: %s", exc)
        _safe_notify(n8n_config, run_id, started_at, "error", len(processed), 0, 0,
                     titles_failed, sheets_config, f"Sheets write failed: {exc}")
        return ScrapingResult.FAILURE

    # ------------------------------------------------------------------
    # Phase 4: Notify n8n
    # ------------------------------------------------------------------
    status = "partial" if titles_failed else "success"
    _safe_notify(
        n8n_config, run_id, started_at, status,
        stats["scraped"], stats["added"], stats["skipped_duplicate"],
        titles_failed, sheets_config, None,
    )

    logger.info(
        "Run %s done -- scraped=%d added=%d skipped=%d titles_failed=%d",
        run_id, stats["scraped"], stats["added"], stats["skipped_duplicate"],
        len(titles_failed),
    )
    return ScrapingResult.PARTIAL if titles_failed else ScrapingResult.SUCCESS


def _safe_notify(
    cfg: N8nConfig,
    run_id: str,
    started_at: datetime,
    status: str,
    rows_scraped: int,
    rows_added: int,
    rows_skipped_duplicate: int,
    titles_failed: list[str],
    sheets_config: SheetsConfig,
    error_summary: str | None,
) -> None:
    """Build and send the n8n payload. Swallows all errors."""
    payload = {
        "run_id": run_id,
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "rows_scraped": rows_scraped,
        "rows_added": rows_added,
        "rows_skipped_duplicate": rows_skipped_duplicate,
        "titles_failed": titles_failed,
        "sheet_id": sheets_config.sheet_id,
        "tab": sheets_config.tab,
        "error_summary": error_summary,
    }
    notify_n8n(cfg, payload)


if __name__ == "__main__":
    result = main()
    logger.info("Final result: %s", result.value)
    sys.exit(0 if result in (ScrapingResult.SUCCESS, ScrapingResult.PARTIAL) else 1)
