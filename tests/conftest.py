"""
Test fixtures: redirect LABEL_MAPPINGS_PATH so process_upwork_data() never
touches the real state/label_mappings.json during a test run.
"""
import os

import pytest


@pytest.fixture(autouse=True)
def _isolated_label_mappings(tmp_path, monkeypatch):
    mapping_file = tmp_path / "label_mappings.json"
    monkeypatch.setenv("LABEL_MAPPINGS_PATH", str(mapping_file))
    yield
