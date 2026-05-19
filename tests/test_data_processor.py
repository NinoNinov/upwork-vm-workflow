"""
Unit tests for data_processor.py -- ported from the source UpWork Container
project. Run with: pytest tests/
"""
import json
import math

import pandas as pd


def _make_valid_df(**overrides):
    base = {
        "position": ["Python"],
        "skills": ["pandas, numpy"],
        "time": [pd.Timestamp("2025-01-01")],
        "client_location": ["USA"],
        "continent": ["North America"],
        "description": ["We need a senior Python developer with 5+ years experience in data pipelines."],
    }
    base.update(overrides)
    return pd.DataFrame(base)


# --- validate_dataframe ------------------------------------------------------

def test_validate_dataframe_valid():
    from data_processor import validate_dataframe
    assert validate_dataframe(_make_valid_df()) is True


def test_validate_dataframe_empty():
    from data_processor import validate_dataframe
    assert validate_dataframe(pd.DataFrame()) is False


def test_validate_dataframe_none():
    from data_processor import validate_dataframe
    assert validate_dataframe(None) is False


def test_validate_dataframe_missing_column():
    from data_processor import validate_dataframe
    df = _make_valid_df().drop(columns=["continent"])
    assert validate_dataframe(df) is False


def test_validate_dataframe_all_null():
    from data_processor import validate_dataframe
    df = pd.DataFrame({
        "position": [None], "skills": [None], "time": [None],
        "client_location": [None], "continent": [None],
    })
    assert validate_dataframe(df) is False


# --- StableEncoder -----------------------------------------------------------

def test_stable_encoder_basic(tmp_path):
    from data_processor import StableEncoder
    enc = StableEncoder(mapping_file=str(tmp_path / "mappings.json"))
    result = enc.encode_column(pd.Series(["Python", "Java", "Python", None, "Go"]), "language")
    assert result.iloc[0] == result.iloc[2]
    assert math.isnan(result.iloc[3])
    assert len({result.iloc[0], result.iloc[1], result.iloc[4]}) == 3


def test_stable_encoder_persistence(tmp_path):
    from data_processor import StableEncoder
    mapping_file = tmp_path / "mappings.json"

    enc1 = StableEncoder(mapping_file=str(mapping_file))
    r1 = enc1.encode_column(pd.Series(["A", "B"]), "col")

    enc2 = StableEncoder(mapping_file=str(mapping_file))
    r2 = enc2.encode_column(pd.Series(["A", "B", "C"]), "col")

    assert r2.iloc[0] == r1.iloc[0]
    assert r2.iloc[1] == r1.iloc[1]
    assert r2.iloc[2] == 2.0


def test_stable_encoder_no_overwrite_on_new_values(tmp_path):
    from data_processor import StableEncoder
    mapping_file = tmp_path / "mappings.json"

    StableEncoder(mapping_file=str(mapping_file)).encode_column(pd.Series(["X", "Y"]), "col")
    StableEncoder(mapping_file=str(mapping_file)).encode_column(pd.Series(["Z"]), "col")

    with open(mapping_file) as f:
        data = json.load(f)
    assert data["col"]["X"] == 0
    assert data["col"]["Y"] == 1
    assert data["col"]["Z"] == 2


# --- process_upwork_data -----------------------------------------------------

def test_process_upwork_data_adds_encoded_columns():
    from data_processor import process_upwork_data
    df = _make_valid_df(
        type=["Fixed-price"],
        time_estimate=["Less than 1 month"],
        experience_level=["Expert"],
        proposals=["10 to 15"],
    )
    result = process_upwork_data(df.copy())
    assert "word_count" in result.columns
    assert "description_label" in result.columns
    assert "position_en" in result.columns


def test_process_upwork_data_empty():
    from data_processor import process_upwork_data
    assert process_upwork_data(pd.DataFrame()).empty


def test_process_upwork_data_startup_flag():
    from data_processor import process_upwork_data
    df = _make_valid_df(description=["We are a startup looking for a Python dev."])
    assert process_upwork_data(df.copy())["StartUp"].iloc[0] == "Yes"


def test_process_upwork_data_valuation_flag():
    from data_processor import process_upwork_data
    df = _make_valid_df(description=["Help us with company valuation analysis."])
    assert process_upwork_data(df.copy())["Valuation"].iloc[0] == "Yes"


def test_description_label_categories():
    from data_processor import process_upwork_data
    short = "hi " * 10          # Insufficient
    concise = "word " * 80      # Concise
    detailed = "word " * 200    # Well-detailed
    verbose = "word " * 400     # Overly detailed

    assert process_upwork_data(_make_valid_df(description=[short]))["description_label"].iloc[0] == "Insufficient"
    assert process_upwork_data(_make_valid_df(description=[concise]))["description_label"].iloc[0] == "Concise"
    assert process_upwork_data(_make_valid_df(description=[detailed]))["description_label"].iloc[0] == "Well-detailed"
    assert process_upwork_data(_make_valid_df(description=[verbose]))["description_label"].iloc[0] == "Overly detailed"
