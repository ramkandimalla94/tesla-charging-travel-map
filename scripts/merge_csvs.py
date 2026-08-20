#!/usr/bin/env python3
"""Merge and deduplicate Tesla charging history CSV exports."""

from __future__ import annotations

import glob
from pathlib import Path

import pandas as pd
import re

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
        str(ROOT / "*_charging_history_*.csv"),
        str(ROOT / "*charging_history*.csv"),
        str(ROOT / "data" / "imports" / "*.csv"),
        str(ROOT / "data" / "imports" / "Tesla_Charging_History_*.csv"),
        str(ROOT / "data" / "imports" / "*charging_history*.csv"),
    ]
    files: list[str] = []
    for pattern in patterns:
        files.extend(glob.glob(pattern))
    # Never treat the merged output itself as an input source via glob
    return sorted({f for f in files if Path(f).resolve() != OUTPUT.resolve()})


def prepare_frame(df: pd.DataFrame) -> pd.DataFrame:
    required = {"ChargeStartDateTime", "SiteLocationName", "QuantityBase"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"CSV missing required columns: {sorted(missing)}")
    out = df.copy()
    out["ChargeStartDateTime"] = normalize_datetime(out["ChargeStartDateTime"])
    out["kwh"] = out["QuantityBase"].apply(parse_kwh)
    if "Vin" not in out.columns:
        out["Vin"] = ""
    if "Name" not in out.columns:
        out["Name"] = ""
    out["Vin"] = out["Vin"].fillna("").astype(str).str.strip()
    out["Name"] = out["Name"].fillna("").astype(str).str.strip()
    return out


def dedup_key(row: pd.Series) -> str:
    inv = str(row.get("InvoiceNumber", "")).strip()
    if inv and inv.lower() not in ("nan", "none"):
        return inv
    return (
        f"{row['ChargeStartDateTime']}|{row['SiteLocationName']}|"
        f"{row['kwh']}|{row.get('Vin', '')}"
    )


def load_and_merge() -> tuple[pd.DataFrame, list[str]]:
    files = discover_csv_files()
    frames: list[pd.DataFrame] = []

    # Preserve previously merged charges when original owner CSVs are absent
    # (personal exports are gitignored; friend's CSV alone must not wipe history).
    if OUTPUT.exists():
        existing = prepare_frame(pd.read_csv(OUTPUT))
        frames.append(existing)
        print(f"  Including existing {OUTPUT.name}: {len(existing)} charges")

    if not files and not frames:
        raise FileNotFoundError(
            "No Tesla charging CSV files found. Place exports in the project root "
            "as Tesla_Charging_History_*.csv / *_charging_history_*.csv "
            "or in data/imports/*.csv"
        )

    for path in files:
        frame = prepare_frame(pd.read_csv(path))
        frames.append(frame)
        print(f"  Loaded {Path(path).name}: {len(frame)} charges")

    combined = pd.concat(frames, ignore_index=True, sort=False)
    combined["_dedup_key"] = combined.apply(dedup_key, axis=1)
    combined["_file_order"] = combined.index
    combined = combined.sort_values(["_dedup_key", "_file_order", "ChargeStartDateTime"])
    deduped = combined.drop_duplicates(subset=["_dedup_key"], keep="first")
    deduped = deduped.sort_values("ChargeStartDateTime").drop(
        columns=["_file_order", "_dedup_key"]
    )
    return deduped.reset_index(drop=True), files


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df, files = load_and_merge()

    out = df.copy()
    out["ChargeStartDateTime"] = out["ChargeStartDateTime"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    out.to_csv(OUTPUT, index=False)

    owners = sorted({n for n in df["Name"].dropna().unique() if str(n).strip()})
    vins = sorted({v for v in df["Vin"].dropna().unique() if str(v).strip()})
    print(f"Merged {len(df)} unique charges from {len(files)} new CSV file(s)")
    print(f"  Owners: {', '.join(owners) or 'unknown'}")
    print(f"  Vehicles: {len(vins)}")
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
