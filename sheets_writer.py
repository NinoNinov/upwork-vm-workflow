"""
SheetsWriter -- replaces MySQL writer with a Google Sheets sink.

One master tab, dedup-on-append against the `description` column. Header row
is created on first run; subsequent runs verify it matches the expected schema
and refuse to write if the user has hand-edited it incompatibly.
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

import gspread
import pandas as pd
from google.oauth2.service_account import Credentials
from gspread.exceptions import APIError, WorksheetNotFound

from utils import retry_with_backoff

logger = logging.getLogger(__name__)

# Order matters: this is the on-sheet column order. Matches the source project's
# extended schema (CLAUDE.md "End-to-end data flow") with `extraction_date`
# (no space) and includes the StableEncoder `_en` columns.
COLUMNS: list[str] = [
    "position", "title", "description", "time", "skills", "type",
    "experience_level", "time_estimate", "budget", "proposals",
    "client_location", "client_jobs_posted", "client_hire_rate",
    "client_hourly_rate", "client_total_spent", "continent", "extraction_date",
    "StartUp", "Valuation", "word_count", "description_label",
    "position_en", "type_en", "time_estimate_en", "experience_level_en",
    "client_location_en", "continent_en", "description_label_en", "proposals_en",
]

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _authorize(credentials_path: str) -> gspread.Client:
    """Build an authenticated gspread client from a service-account JSON."""
    creds = Credentials.from_service_account_file(credentials_path, scopes=_SCOPES)
    return gspread.authorize(creds)


def read_job_titles_from_sheet(
    sheet_id: str,
    tab: str,
    credentials_path: str,
) -> Dict[str, int]:
    """
    Read job titles + page counts from a Google Sheet tab.

    Expected header in row 1: ``Job Title``, ``Value`` (case sensitive).
    Returns a dict mapping each non-empty title to its integer page count.
    Rows with blank titles or non-integer values are skipped with a warning.

    Raises:
        WorksheetNotFound: if *tab* does not exist in the spreadsheet.
        APIError:          on transient Sheets API failures (retried internally).
    """
    client = _authorize(credentials_path)
    spreadsheet = client.open_by_key(sheet_id)
    try:
        ws = spreadsheet.worksheet(tab)
    except WorksheetNotFound:
        raise

    records = retry_with_backoff(
        ws.get_all_records,
        max_retries=3,
        exceptions=(APIError,),
        logger=logger,
    )

    result: Dict[str, int] = {}
    for row in records:
        title = str(row.get("Job Title", "")).strip()
        if not title:
            continue
        raw_value = row.get("Value")
        try:
            pages = int(raw_value)
        except (TypeError, ValueError):
            logger.warning("Skipping job-titles row with bad 'Value' (%r) for %r", raw_value, title)
            continue
        result[title] = pages

    logger.info("Read %d job titles from Sheets tab %r.", len(result), tab)
    return result


class SheetsWriter:
    """Append-only writer with description-based deduplication."""

    def __init__(self, sheet_id: str, tab: str, credentials_path: str) -> None:
        self.sheet_id = sheet_id
        self.tab = tab
        self._credentials_path = credentials_path
        self._client: Optional[gspread.Client] = None
        self._worksheet: Optional[gspread.Worksheet] = None

    # ------------------------------------------------------------------
    # gspread plumbing
    # ------------------------------------------------------------------

    def _connect(self) -> gspread.Worksheet:
        if self._worksheet is not None:
            return self._worksheet

        creds = Credentials.from_service_account_file(
            self._credentials_path, scopes=_SCOPES
        )
        self._client = gspread.authorize(creds)
        spreadsheet = self._client.open_by_key(self.sheet_id)

        try:
            self._worksheet = spreadsheet.worksheet(self.tab)
        except WorksheetNotFound:
            logger.info("Tab %r not found; creating it.", self.tab)
            self._worksheet = spreadsheet.add_worksheet(
                title=self.tab, rows=1000, cols=len(COLUMNS)
            )
        return self._worksheet

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ensure_header(self) -> None:
        """
        Write the header row if the sheet is empty. Otherwise verify it matches
        COLUMNS; raise if a hand-edit has diverged from the expected schema.
        """
        ws = self._connect()
        existing = retry_with_backoff(
            lambda: ws.row_values(1),
            max_retries=3,
            exceptions=(APIError,),
            logger=logger,
        )
        if not existing:
            logger.info("Sheet empty -- writing header row (%d columns).", len(COLUMNS))
            retry_with_backoff(
                lambda: ws.update("A1", [COLUMNS], value_input_option="USER_ENTERED"),
                max_retries=3,
                exceptions=(APIError,),
                logger=logger,
            )
            return

        if existing != COLUMNS:
            extra = set(existing) - set(COLUMNS)
            missing = set(COLUMNS) - set(existing)
            raise RuntimeError(
                "Sheet header does not match expected schema. "
                f"Unexpected columns: {sorted(extra)}. "
                f"Missing columns: {sorted(missing)}. "
                "Either reset the sheet or update sheets_writer.COLUMNS."
            )

    def existing_descriptions(self) -> set[str]:
        """Return all non-empty description values currently in the sheet."""
        ws = self._connect()
        try:
            col_idx = COLUMNS.index("description") + 1  # 1-based
        except ValueError:
            return set()
        values = retry_with_backoff(
            lambda: ws.col_values(col_idx),
            max_retries=3,
            exceptions=(APIError,),
            logger=logger,
        )
        # Drop header (row 1) and empties
        return {v for v in values[1:] if v}

    def append_new(self, df: pd.DataFrame) -> dict:
        """
        Dedup *df* against existing descriptions and append remaining rows.

        Returns: {"scraped": N, "added": M, "skipped_duplicate": K}
        """
        if df is None or df.empty:
            logger.warning("append_new: empty DataFrame; nothing to write.")
            return {"scraped": 0, "added": 0, "skipped_duplicate": 0}

        self.ensure_header()
        existing = self.existing_descriptions()

        # Filter: drop empty descriptions; dedup within batch and against sheet
        before = len(df)
        df = df[df["description"].notna() & (df["description"] != "")]
        df = df.drop_duplicates(subset=["description"], keep="first")
        df = df[~df["description"].isin(existing)]
        added = len(df)
        skipped = before - added

        if df.empty:
            logger.info("append_new: all %d rows already present (no-op).", before)
            return {"scraped": before, "added": 0, "skipped_duplicate": skipped}

        # Reindex to the canonical column order; fill missing columns with blank.
        # Any df columns NOT in COLUMNS are dropped (logged once).
        extra_cols = [c for c in df.columns if c not in COLUMNS]
        if extra_cols:
            logger.info(
                "append_new: dropping %d unexpected column(s) before write: %s",
                len(extra_cols), extra_cols,
            )
        rows_df = df.reindex(columns=COLUMNS).fillna("")
        # Stringify everything Sheets can't represent natively (Timestamps,
        # numpy types). gspread will pass strings through USER_ENTERED so dates
        # render as dates, numbers as numbers.
        rows = rows_df.astype(str).values.tolist()

        ws = self._connect()
        retry_with_backoff(
            lambda: ws.append_rows(rows, value_input_option="USER_ENTERED"),
            max_retries=3,
            exceptions=(APIError,),
            logger=logger,
        )
        logger.info("append_new: appended %d new row(s) (skipped %d dup).", added, skipped)
        return {"scraped": before, "added": added, "skipped_duplicate": skipped}
