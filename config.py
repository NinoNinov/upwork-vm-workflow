"""
Central configuration for the Upwork-VM-workflow pipeline.

Three dataclasses are loaded from environment variables (with sane defaults
suitable for `python main.py` in development). Job titles are loaded lazily
via load_job_titles() -- no CSV I/O happens on import.
"""
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, Optional

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class ScrapingResult(Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"


@dataclass
class ScrapingConfig:
    """Configuration for the Upwork scraper."""
    max_workers: int = int(os.environ.get("SCRAPING_MAX_WORKERS", 3))
    timeout: int = int(os.environ.get("SCRAPING_TIMEOUT", 300))
    max_retries: int = int(os.environ.get("SCRAPING_MAX_RETRIES", 3))
    jobs_per_page: int = int(os.environ.get("SCRAPING_JOBS_PER_PAGE", 50))
    start_page: int = int(os.environ.get("SCRAPING_START_PAGE", 1))
    headless: bool = os.environ.get("SCRAPING_HEADLESS", "true").lower() == "true"
    scraper_workers: int = int(os.environ.get("SCRAPING_SCRAPER_WORKERS", 2))
    fast: bool = os.environ.get("SCRAPING_FAST", "false").lower() == "true"
    continent_file: str = os.environ.get("CONTINENT_FILE", "countries_continents.csv")
    timezone_offset: int = int(os.environ.get("SCRAPING_TIMEZONE_OFFSET", 3))


@dataclass
class SheetsConfig:
    """Configuration for the Google Sheets sink."""
    sheet_id: str = field(
        default_factory=lambda: os.environ.get("GOOGLE_SHEET_ID", "")
    )
    tab: str = field(
        default_factory=lambda: os.environ.get("GOOGLE_SHEET_TAB", "upwork_master")
    )
    credentials_path: str = field(
        default_factory=lambda: os.environ.get(
            "GOOGLE_APPLICATION_CREDENTIALS", "secrets/sa.json"
        )
    )

    def validate(self) -> None:
        if not self.sheet_id:
            raise ValueError("GOOGLE_SHEET_ID is not set (see .env.example).")
        if not Path(self.credentials_path).exists():
            raise FileNotFoundError(
                f"Service-account key not found at {self.credentials_path}. "
                "Set GOOGLE_APPLICATION_CREDENTIALS or mount the JSON file."
            )


@dataclass
class N8nConfig:
    """Configuration for the n8n webhook notifier."""
    webhook_url: str = field(
        default_factory=lambda: os.environ.get("N8N_WEBHOOK_URL", "")
    )
    webhook_token: str = field(
        default_factory=lambda: os.environ.get("N8N_WEBHOOK_TOKEN", "")
    )
    timeout: int = int(os.environ.get("N8N_TIMEOUT", 10))

    @property
    def enabled(self) -> bool:
        return bool(self.webhook_url)


def load_job_titles(csv_path: Optional[str] = None) -> Dict[str, int]:
    """
    Load job titles + page-count values.

    Source is controlled by ``JOB_TITLES_SOURCE`` (``sheet`` or ``csv``).
    Default is ``sheet`` -- reads from the ``Job Titles`` tab of the same
    spreadsheet as the writer. Falls back to CSV if the sheet read fails so
    a transient API hiccup doesn't abort the run.

    Both sources expect columns ``Job Title`` and ``Value``.
    """
    source = os.environ.get("JOB_TITLES_SOURCE", "sheet").lower()
    if source == "sheet":
        result = _load_job_titles_from_sheet()
        if result:
            return result
        logger.warning("Sheet-based job titles unavailable; falling back to CSV.")

    return _load_job_titles_from_csv(csv_path)


def _load_job_titles_from_sheet() -> Dict[str, int]:
    """Read the job-titles tab from the Google Sheet. Returns {} on failure."""
    sheet_id = os.environ.get("GOOGLE_SHEET_ID", "")
    tab = os.environ.get("GOOGLE_SHEET_JOB_TITLES_TAB", "Job Titles")
    creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "secrets/sa.json")

    if not sheet_id:
        logger.warning("GOOGLE_SHEET_ID not set; cannot load job titles from Sheets.")
        return {}
    if not Path(creds).exists():
        logger.warning("Service-account key %s missing; cannot load job titles from Sheets.", creds)
        return {}

    try:
        # Imported lazily so the CSV path stays usable in tests that mock Sheets out.
        from sheets_writer import read_job_titles_from_sheet
        return read_job_titles_from_sheet(sheet_id, tab, creds)
    except Exception as exc:
        logger.error("Failed to load job titles from Sheets tab %r: %s", tab, exc)
        return {}


def _load_job_titles_from_csv(csv_path: Optional[str] = None) -> Dict[str, int]:
    """Read job titles from a CSV (the legacy / fallback source)."""
    candidates = [p for p in (csv_path, "/app/data/job_titles.csv", "job_titles.csv") if p]

    for path_str in candidates:
        path = Path(path_str)
        if not path.exists():
            continue
        try:
            df = pd.read_csv(path)
            result: Dict[str, int] = dict(zip(df["Job Title"], df["Value"].astype(int)))
            logger.info("Loaded %d job titles from %s", len(result), path)
            return result
        except Exception as exc:
            logger.error("Failed to load job titles from %s: %s", path, exc)

    logger.error(
        "job_titles.csv not found in any of: %s -- running with empty title list.",
        candidates,
    )
    return {}
