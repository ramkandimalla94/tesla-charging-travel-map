#!/usr/bin/env python3
"""Geocode unique SiteLocationName values via Nominatim with persistent cache."""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from geopy.exc import GeocoderServiceError, GeocoderTimedOut
from geopy.geocoders import Nominatim

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
MERGED = DATA_DIR / "merged_charges.csv"
CACHE_FILE = DATA_DIR / "locations_cache.json"

# Known home-base hints for ambiguous site names
HOME_BASE_HINTS = {
    "MAA Market Center": "3825 Mapleshade Lane, Plano, TX, US",
    "Addison, TX": "Addison, TX, US",
    "Mansion Grove": "Mansion Grove, Santa Clara, CA, US",
}

# Fallback coordinates when Nominatim cannot resolve a site
MANUAL_OVERRIDES = {
    "MAA Market Center": {
        "lat": 33.0033458,
        "lng": -96.7644015,
        "display_name": "3825 Mapleshade Lane, Plano, TX 75075",
        "source": "manual",
        "query": "3825 Mapleshade Lane, Plano, TX, US",
    },
    "Mansion Grove": {
        "lat": 37.3986946,
        "lng": -121.9439508,
        "display_name": "502 Mansion Park Dr, Santa Clara, CA 95054",
        "source": "manual",
        "query": "Mansion Grove, Santa Clara, CA, US",
    },
    "Tumwater, WA - Kingswood Drive Southwest": {
        "lat": 46.9932302,
        "lng": -122.9200682,
        "display_name": "Kingswood Drive, Tumwater, WA 98512",
        "source": "manual",
        "query": "Kingswood Drive, Tumwater, WA, US",
    },
}

# Pattern: "City, ST - Street details (optional floor)"
CITY_STREET = re.compile(
    r"^(?P<city>[^,]+),\s*(?P<state>[A-Z]{2})\s*-\s*(?P<street>.+)$"
)
# Pattern: "City, ST" only
CITY_STATE = re.compile(r"^(?P<city>[^,]+),\s*(?P<state>[A-Z]{2})$")


def load_cache() -> dict:
    if CACHE_FILE.exists():
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(cache: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def build_query(site_name: str) -> str:
    if site_name in HOME_BASE_HINTS:
        return HOME_BASE_HINTS[site_name]

    # Strip parenthetical suffixes: "Colorado Springs, CO (Pay to Park, Floor 1)"
    clean = re.sub(r"\s*\([^)]*\)\s*", "", site_name).strip()

    m = CITY_STREET.match(clean)
    if m:
        street = re.sub(r"\s*\([^)]*\)\s*", "", m.group("street")).strip()
        return f"{street}, {m.group('city')}, {m.group('state')}, US"

    m = CITY_STATE.match(clean)
    if m:
        return f"{m.group('city')}, {m.group('state')}, US"

    # "City, ST (extra)" pattern
    m = re.match(r"^(?P<city>[^,]+),\s*(?P<state>[A-Z]{2})\b", clean)
    if m:
        return f"{m.group('city')}, {m.group('state')}, US"

    return f"{site_name}, US"


def geocode_site(geolocator: Nominatim, site_name: str) -> dict | None:
    if site_name in MANUAL_OVERRIDES:
        override = MANUAL_OVERRIDES[site_name]
        return {"site_name": site_name, **override}

    query = build_query(site_name)
    try:
        location = geolocator.geocode(query, country_codes="us", timeout=10)
        if location:
            return {
                "site_name": site_name,
                "lat": location.latitude,
                "lng": location.longitude,
                "display_name": location.address,
                "source": "geocode",
                "query": query,
            }
    except (GeocoderTimedOut, GeocoderServiceError) as exc:
        print(f"  Geocoder error for '{site_name}': {exc}")

    if site_name in MANUAL_OVERRIDES:
        override = MANUAL_OVERRIDES[site_name]
        return {"site_name": site_name, **override}
    return None


def get_unique_sites() -> list[str]:
    df = pd.read_csv(MERGED)
    return sorted(df["SiteLocationName"].dropna().unique())


def main() -> None:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Geocode charging locations")
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Retry only locations that previously failed",
    )
    args = parser.parse_args()

    if not MERGED.exists():
        raise FileNotFoundError(f"Run merge_csvs.py first. Missing {MERGED}")

    user_agent = os.getenv("NOMINATIM_USER_AGENT", "tesla-charging-history-map")
    geolocator = Nominatim(user_agent=user_agent)

    cache = load_cache()
    sites = get_unique_sites()

    to_geocode = []
    for site in sites:
        entry = cache.get(site)
        if entry and entry.get("lat") is not None and entry.get("lng") is not None:
            continue
        if args.retry_failed:
            if entry is not None and entry.get("lat") is None:
                to_geocode.append(site)
        else:
            if entry is None:
                to_geocode.append(site)

    print(f"Total unique sites: {len(sites)}")
    print(f"Already cached: {len(sites) - len(to_geocode)}")
    print(f"To geocode: {len(to_geocode)}")

    success = 0
    failed = 0
    for i, site in enumerate(to_geocode, 1):
        print(f"[{i}/{len(to_geocode)}] Geocoding: {site}")
        result = geocode_site(geolocator, site)
        if result:
            cache[site] = result
            success += 1
            print(f"  -> {result['lat']:.5f}, {result['lng']:.5f}")
        else:
            cache[site] = {
                "site_name": site,
                "lat": None,
                "lng": None,
                "display_name": None,
                "source": "geocode",
                "query": build_query(site),
            }
            failed += 1
            print("  -> FAILED")
        save_cache(cache)
        if i < len(to_geocode):
            time.sleep(1.1)

    total_ok = sum(
        1 for s in sites if cache.get(s, {}).get("lat") is not None
    )
    total_fail = len(sites) - total_ok
    print(f"\nGeocoding complete: {total_ok} success, {total_fail} failed")
    print(f"Cache written to {CACHE_FILE}")


if __name__ == "__main__":
    main()
