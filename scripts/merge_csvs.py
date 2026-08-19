#!/usr/bin/env python3
"""Merge and deduplicate Tesla charging history CSV exports."""

from __future__ import annotations

import glob
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUTPUT = DATA_DIR / "merged_charges.csv"


def parse_kwh(value: str | float) -> float:
    if pd.isna(value):
        return 0.0
    text = str(value).strip().lower()
    match = re.search(r"([\d.]+)", text)
    return float(match.group(1)) if match else 0.0


def normalize_datetime(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True, errors="coerce")


def discover_csv_files() -> list[str]:
    """Find Tesla charging CSV exports in project root or data/imports/."""
    patterns = [
        str(ROOT / "Tesla_Charging_History_*.csv"),
        str(ROOT / "data" / "imports" / "*.csv"),
        str(ROOT / "data" / "imports" / "Tesla_Charging_History_*.csv"),
    ]
    files: list[str] = []
    for pattern in patterns:
        files.extend(glob.glob(pattern))
    return sorted(set(files))


def load_and_merge() -> pd.DataFrame:
    files = discover_csv_files()
    if not files:
        raise FileNotFoundError(
            "No Tesla charging CSV files found. Place exports in the project root "
            "as Tesla_Charging_History_*.csv or in data/imports/*.csv"
        )

    frames = [pd.read_csv(f) for f in files]
    combined = pd.concat(frames, ignore_index=True)

    combined["ChargeStartDateTime"] = normalize_datetime(combined["ChargeStartDateTime"])
    combined["kwh"] = combined["QuantityBase"].apply(parse_kwh)

    # Dedup key: use InvoiceNumber when present, else datetime+location+kwh
    # (credit/home sessions often have blank InvoiceNumber — must NOT collapse)
    combined["_dedup_key"] = combined.apply(
        lambda r: (
            str(r["InvoiceNumber"]).strip()
            if pd.notna(r["InvoiceNumber"]) and str(r["InvoiceNumber"]).strip()
            else f"{r['ChargeStartDateTime']}|{r['SiteLocationName']}|{r['kwh']}"
        ),
        axis=1,
    )
    combined["_file_order"] = combined.index
    combined = combined.sort_values(["_dedup_key", "_file_order", "ChargeStartDateTime"])
    deduped = combined.drop_duplicates(subset=["_dedup_key"], keep="first")

    deduped = deduped.sort_values("ChargeStartDateTime").drop(
        columns=["_file_order", "_dedup_key"]
    )
    return deduped.reset_index(drop=True)


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df = load_and_merge()

    out = df.copy()
    out["ChargeStartDateTime"] = out["ChargeStartDateTime"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    out.to_csv(OUTPUT, index=False)

    print(f"Merged {len(df)} unique charges from {len(files)} CSV file(s)")
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
