"""
Unit tests for sheets_writer.SheetsWriter. gspread is fully mocked, so no
network or Sheets credentials are required.

Run with: pytest tests/test_sheets_writer.py -v
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from sheets_writer import COLUMNS, SheetsWriter


@pytest.fixture
def fake_ws():
    """Return a MagicMock standing in for a gspread.Worksheet."""
    ws = MagicMock()
    ws.row_values.return_value = []        # empty header by default
    ws.col_values.return_value = []        # no existing descriptions
    return ws


@pytest.fixture
def writer(fake_ws, monkeypatch):
    """SheetsWriter wired to fake_ws so no real network calls happen."""
    w = SheetsWriter(sheet_id="fake", tab="fake_tab", credentials_path="unused")
    monkeypatch.setattr(w, "_connect", lambda: fake_ws)
    return w


def _row(description: str, position: str = "Python") -> dict:
    """Build a single-row dict matching COLUMNS (filling missing with empties)."""
    row = {col: "" for col in COLUMNS}
    row["position"] = position
    row["description"] = description
    return row


# ---------------------------------------------------------------------------
# Header bootstrap
# ---------------------------------------------------------------------------

def test_ensure_header_writes_when_sheet_empty(writer, fake_ws):
    fake_ws.row_values.return_value = []
    writer.ensure_header()
    fake_ws.update.assert_called_once_with("A1", [COLUMNS], value_input_option="USER_ENTERED")


def test_ensure_header_noop_when_match(writer, fake_ws):
    fake_ws.row_values.return_value = COLUMNS
    writer.ensure_header()
    fake_ws.update.assert_not_called()


def test_ensure_header_raises_on_mismatch(writer, fake_ws):
    fake_ws.row_values.return_value = ["title", "description"]  # bogus header
    with pytest.raises(RuntimeError, match="does not match expected schema"):
        writer.ensure_header()


# ---------------------------------------------------------------------------
# existing_descriptions
# ---------------------------------------------------------------------------

def test_existing_descriptions_skips_header_and_empties(writer, fake_ws):
    fake_ws.col_values.return_value = ["description", "alpha", "", "beta", "alpha"]
    result = writer.existing_descriptions()
    assert result == {"alpha", "beta"}


# ---------------------------------------------------------------------------
# append_new -- dedup + batch write
# ---------------------------------------------------------------------------

def test_append_new_empty_df_is_noop(writer, fake_ws):
    stats = writer.append_new(pd.DataFrame())
    assert stats == {"scraped": 0, "added": 0, "skipped_duplicate": 0}
    fake_ws.append_rows.assert_not_called()


def test_append_new_writes_all_when_sheet_empty(writer, fake_ws):
    fake_ws.row_values.return_value = COLUMNS          # header already there
    fake_ws.col_values.return_value = ["description"]  # only the header in description col

    df = pd.DataFrame([_row("job A"), _row("job B"), _row("job C")])
    stats = writer.append_new(df)

    assert stats == {"scraped": 3, "added": 3, "skipped_duplicate": 0}
    fake_ws.append_rows.assert_called_once()
    rows_arg = fake_ws.append_rows.call_args.args[0]
    assert len(rows_arg) == 3
    assert all(len(r) == len(COLUMNS) for r in rows_arg)


def test_append_new_dedup_against_sheet(writer, fake_ws):
    fake_ws.row_values.return_value = COLUMNS
    fake_ws.col_values.return_value = ["description", "job A", "job B"]

    df = pd.DataFrame([_row("job A"), _row("job C")])
    stats = writer.append_new(df)

    assert stats == {"scraped": 2, "added": 1, "skipped_duplicate": 1}
    rows_arg = fake_ws.append_rows.call_args.args[0]
    desc_idx = COLUMNS.index("description")
    assert [r[desc_idx] for r in rows_arg] == ["job C"]


def test_append_new_dedup_within_batch(writer, fake_ws):
    fake_ws.row_values.return_value = COLUMNS
    fake_ws.col_values.return_value = ["description"]  # empty sheet

    df = pd.DataFrame([_row("dup"), _row("dup"), _row("unique")])
    stats = writer.append_new(df)

    assert stats["added"] == 2  # one "dup", one "unique"
    assert stats["skipped_duplicate"] == 1


def test_append_new_drops_blank_descriptions(writer, fake_ws):
    fake_ws.row_values.return_value = COLUMNS
    fake_ws.col_values.return_value = ["description"]

    df = pd.DataFrame([_row(""), _row("real job")])
    stats = writer.append_new(df)

    assert stats["added"] == 1
    rows_arg = fake_ws.append_rows.call_args.args[0]
    assert len(rows_arg) == 1


def test_append_new_drops_unexpected_columns(writer, fake_ws):
    fake_ws.row_values.return_value = COLUMNS
    fake_ws.col_values.return_value = ["description"]

    row = _row("job X")
    row["secret_internal_column"] = "should not be written"
    df = pd.DataFrame([row])
    stats = writer.append_new(df)

    assert stats["added"] == 1
    rows_arg = fake_ws.append_rows.call_args.args[0]
    assert len(rows_arg[0]) == len(COLUMNS)  # extra col dropped


# ---------------------------------------------------------------------------
# Credentials loading (smoke test of the _connect path with both mocks)
# ---------------------------------------------------------------------------

def test_connect_creates_missing_tab(tmp_path):
    """Verify the WorksheetNotFound branch creates a new tab."""
    from gspread.exceptions import WorksheetNotFound

    fake_spreadsheet = MagicMock()
    fake_spreadsheet.worksheet.side_effect = WorksheetNotFound()
    fake_ws_new = MagicMock()
    fake_spreadsheet.add_worksheet.return_value = fake_ws_new

    fake_client = MagicMock()
    fake_client.open_by_key.return_value = fake_spreadsheet

    creds_file = tmp_path / "sa.json"
    creds_file.write_text("{}")  # not parsed because Credentials is patched

    with patch("sheets_writer.Credentials.from_service_account_file") as fake_creds, \
         patch("sheets_writer.gspread.authorize", return_value=fake_client):
        fake_creds.return_value = MagicMock()
        w = SheetsWriter(sheet_id="x", tab="new_tab", credentials_path=str(creds_file))
        result = w._connect()

    assert result is fake_ws_new
    fake_spreadsheet.add_worksheet.assert_called_once()
