import json
import logging
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pycountry
import pycountry_convert as pc

from config import ScrapingConfig

logger = logging.getLogger(__name__)


def _default_mapping_path() -> str:
    return os.environ.get("LABEL_MAPPINGS_PATH", "state/label_mappings.json")


class StableEncoder:
    """
    Encodes categorical string values to stable integers persisted in a JSON
    file. Existing codes never change; new values are appended.
    """

    def __init__(self, mapping_file: str | None = None):
        self.mapping_file = Path(mapping_file or _default_mapping_path())
        self._mappings = self._load()

    def _load(self):
        if self.mapping_file.exists():
            try:
                with open(self.mapping_file, encoding="utf-8") as fh:
                    return json.load(fh)
            except Exception as exc:
                logger.warning("Could not load label mappings from %s: %s", self.mapping_file, exc)
        return {}

    def _save(self):
        try:
            self.mapping_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.mapping_file, "w", encoding="utf-8") as fh:
                json.dump(self._mappings, fh, indent=2, ensure_ascii=False)
        except Exception as exc:
            logger.warning("Could not save label mappings to %s: %s", self.mapping_file, exc)

    def encode_column(self, series, column_name):
        if column_name not in self._mappings:
            self._mappings[column_name] = {}
        mapping = self._mappings[column_name]
        changed = False

        def _encode(val):
            nonlocal changed
            if pd.isna(val):
                return np.nan
            key = str(val)
            if key not in mapping:
                mapping[key] = len(mapping)
                changed = True
            return float(mapping[key])

        result = series.apply(_encode)
        if changed:
            self._save()
        return result


_ALPHA3_TO_CONTINENT: dict[str, str] = {}


def _build_continent_map():
    code_to_name = {
        "AF": "Africa", "AS": "Asia", "EU": "Europe",
        "NA": "North America", "SA": "South America",
        "OC": "Oceania", "AN": "Antarctica",
    }
    result = {}
    for country in pycountry.countries:
        try:
            continent_code = pc.country_alpha2_to_continent_code(country.alpha_2)
            result[country.alpha_3] = code_to_name.get(continent_code, "Unknown")
        except Exception:
            pass
    return result


def _get_alpha3_to_continent():
    global _ALPHA3_TO_CONTINENT
    if not _ALPHA3_TO_CONTINENT:
        _ALPHA3_TO_CONTINENT = _build_continent_map()
    return _ALPHA3_TO_CONTINENT


def _alpha3_to_country_name(code):
    country = pycountry.countries.get(alpha_3=code)
    return country.name if country else code


def _country_name_to_continent(country_name):
    if not country_name:
        return "Unknown"
    country = pycountry.countries.get(name=country_name)
    if not country:
        return "Unknown"
    return _get_alpha3_to_continent().get(country.alpha_3, "Unknown")


class JobDataProcessor:
    def __init__(self, config: ScrapingConfig):
        self.config = config
        self.continent_dict = self._load_continent_data()

    def _load_continent_data(self):
        try:
            continent_file = Path(self.config.continent_file)
            if not continent_file.exists():
                logger.warning("Continent file %s not found; using pycountry fallback.", continent_file)
                return {}
            df = pd.read_csv(continent_file)
            return dict(zip(df["Country"], df["Continent"]))
        except Exception as exc:
            logger.error("Error loading continent data: %s", exc)
            return {}

    def process_scraped_data(self, data, position):
        if not data:
            return None
        try:
            df = pd.DataFrame([{"position": position, **entry} for entry in data])
            df["skills"] = df["skills"].apply(
                lambda x: ", ".join(map(str, x)) if isinstance(x, list) else str(x)
            )
            df["time"] = (
                pd.to_datetime(df["time"], unit="s", errors="coerce")
                + pd.Timedelta(hours=self.config.timezone_offset)
            )
            df["continent"] = df["client_location"].apply(
                lambda x: self.continent_dict.get(x, _country_name_to_continent(x))
                if pd.notna(x) else "Unknown"
            )
            df["extraction_date"] = pd.Timestamp.now().normalize()
            return df
        except Exception as exc:
            logger.error("Error processing scraped data for %s: %s", position, exc)
            return None


def process_upwork_data(df):
    if df is None or df.empty:
        logger.warning("process_upwork_data received an empty DataFrame.")
        return df

    if "client_location" in df.columns:
        df["client_location"] = df["client_location"].apply(
            lambda c: _alpha3_to_country_name(c) if pd.notna(c) else np.nan
        )
        df["continent"] = df["client_location"].apply(
            lambda c: _country_name_to_continent(c) if pd.notna(c) else np.nan
        )
    else:
        logger.warning("client_location column not found; skipping country/continent mapping.")

    if "description" in df.columns:
        desc = df["description"].fillna("")
        df["StartUp"] = desc.str.contains(
            r"\b(?:StartUp|Start Up|startup|start up)\b", case=False, regex=True
        ).map({True: "Yes", False: "No"})
        df["Valuation"] = desc.str.contains(
            r"\bValuation\b", case=False, regex=True
        ).map({True: "Yes", False: "No"})
        df["word_count"] = desc.apply(lambda d: len(d.split()))
    else:
        logger.warning("description column not found; StartUp, Valuation, word_count set to null.")
        df["StartUp"] = np.nan
        df["Valuation"] = np.nan
        df["word_count"] = np.nan

    def _cat(count):
        if pd.isna(count):
            return np.nan
        if count < 50:
            return "Insufficient"
        if count <= 150:
            return "Concise"
        if count <= 300:
            return "Well-detailed"
        return "Overly detailed"

    df["description_label"] = df["word_count"].apply(_cat)

    encoder = StableEncoder()
    for col in ["position", "type", "time_estimate", "experience_level",
                "client_location", "continent", "description_label", "proposals"]:
        if col in df.columns:
            df[f"{col}_en"] = encoder.encode_column(df[col], col)
        else:
            logger.warning("Column %s not found; skipping encoding.", col)

    logger.info("Feature engineering complete.")
    return df


def validate_dataframe(df):
    required = ["position", "skills", "time", "client_location", "continent"]
    if df is None or df.empty:
        logger.warning("validate_dataframe: DataFrame is None or empty.")
        return False
    missing = [c for c in required if c not in df.columns]
    if missing:
        logger.error("validate_dataframe: missing required columns: %s", missing)
        return False
    if df.isnull().values.all():
        logger.warning("validate_dataframe: entire DataFrame is null.")
        return False
    return True
