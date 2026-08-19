#!/usr/bin/env python3
"""
Segment charging history into road trips using multi-signal boundary detection.

Algorithm (in priority order):
  1. Home charge (MAA Market Center / Addison, TX) ends the current away trip.
  2. Time gap: >= TIME_GAP_DAYS between consecutive away charges → new trip.
  3. Return-to-origin: previous stop was >FAR_FROM_HOME mi from DFW and current
     stop is within HOME_RADIUS mi → trip ended (returned to Dallas area).
  4. Cross-country reset: was in Pacific NW (>1000 mi from home) and next charge
     is in TX/CO/SW desert corridor → new trip arc (likely flew/drove back).
  5. Big geographic jump (>BIG_JUMP mi) combined with moving >500 mi closer to
     home → regional trip boundary.
  6. Same-location charges within 24 h are merged into one stop.

Tuned against merged_charges.csv: ~15-22 trips for 240 charges.
"""

from __future__ import annotations

import json
import math
import re
from datetime import timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
MERGED = DATA_DIR / "merged_charges.csv"
CACHE_FILE = DATA_DIR / "locations_cache.json"
OUTPUT = DATA_DIR / "trips.json"

HOME_BASES = {"MAA Market Center", "Addison, TX"}
MERGE_WINDOW = timedelta(hours=24)

# Colorado state bounding box
CO_BBOX = {"lat_min": 37.0, "lat_max": 41.0, "lng_min": -109.1, "lng_max": -102.0}

# DFW home center (Addison / Plano)
HOME_LAT, HOME_LNG = 32.96, -96.83
HOME_RADIUS_MILES = 120
FAR_FROM_HOME_MILES = 350
TIME_GAP_DAYS = 14
BIG_JUMP_MILES = 700


def load_cache() -> dict:
    with open(CACHE_FILE, encoding="utf-8") as f:
        return json.load(f)


def haversine_miles(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 3959.0
    p = math.pi / 180
    a = (
        math.sin((lat2 - lat1) * p / 2) ** 2
        + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lng2 - lng1) * p / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(a))


def dist_from_home(lat: float | None, lng: float | None) -> float | None:
    if lat is None or lng is None:
        return None
    return haversine_miles(HOME_LAT, HOME_LNG, lat, lng)


def is_colorado(lat: float | None, lng: float | None, location: str) -> bool:
    if re.search(r",\s*CO\b", location):
        return True
    if lat is None or lng is None:
        return False
    return (
        CO_BBOX["lat_min"] <= lat <= CO_BBOX["lat_max"]
        and CO_BBOX["lng_min"] <= lng <= CO_BBOX["lng_max"]
    )


def get_region(lat: float | None, lng: float | None, location: str) -> str:
    if location in HOME_BASES:
        return "HOME"
    if is_colorado(lat, lng, location):
        return "CO"
    if lat is None or lng is None:
        return "UNK"
    if lng > -100:
        return "TX"
    if lng > -104:
        return "CO/NM"
    if lng > -112:
        return "UT/AZ/NV"
    if lng > -116:
        return "ID/UT"
    if lat > 42:
        return "PNW"
    if lat > 38:
        return "CA/NV"
    return "CA-S"


def is_home(location: str) -> bool:
    return location in HOME_BASES


def extract_city(location: str) -> str:
    m = re.match(r"^([^,]+)", location)
    return m.group(1).strip() if m else location


def extract_state_code(location: str) -> str | None:
    m = re.search(r",\s*([A-Z]{2})\b", location)
    return m.group(1) if m else None


# Map suburb/end-city labels to a recognizable destination name
DEST_ALIASES = {
    "Kirkland": "Seattle",
    "Bellevue": "Seattle",
    "Redmond": "Seattle",
    "Renton": "Seattle",
    "North Bend": "Seattle",
    "Tulalip Bay": "Seattle",
    "Auburn": "Seattle",
    "Suquamish": "Seattle",
    "Silverdale": "Seattle",
    "Puyallup": "Seattle",
    "Georgetown": "San Antonio",
    "Henrietta": "Dallas",
    "Childress": "Dallas",
    "Addison": "Dallas",
    "Plano": "Dallas",
    "Irving": "Dallas",
    "Red Oak": "Dallas",
    "Vernon": "Dallas",
}


def origin_label(stops: list[dict]) -> str:
    first = stops[0]
    if first.get("dist_home") is not None and first["dist_home"] < 200:
        return "Dallas"
    city = extract_city(first["location"])
    return DEST_ALIASES.get(city, city)


def dest_label(stops: list[dict]) -> str:
    last = stops[-1]
    if last.get("dist_home") is not None and last["dist_home"] < 200:
        return "Dallas"
    city = extract_city(last["location"])
    return DEST_ALIASES.get(city, city)


def ordered_via_states(stops: list[dict]) -> list[str]:
    """States visited in chronological order (deduped)."""
    seen: set[str] = set()
    ordered: list[str] = []
    for s in stops:
        st = extract_state_code(s["location"])
        if not st and s.get("in_colorado"):
            st = "CO"
        if st and st not in seen:
            ordered.append(st)
            seen.add(st)
    return ordered


def format_via_summary(stops: list[dict]) -> str:
    co_count = sum(1 for s in stops if s.get("in_colorado"))
    parts: list[str] = []
    for st in ordered_via_states(stops):
        if st == "CO" and co_count > 1:
            parts.append(f"CO (×{co_count} stops)")
        else:
            parts.append(st)
    return ", ".join(parts)


def merge_consecutive_stops(charges: list[dict]) -> list[dict]:
    if not charges:
        return []

    merged = [charges[0].copy()]
    for charge in charges[1:]:
        prev = merged[-1]
        same_loc = charge["location"] == prev["location"]
        within_window = (
            pd.Timestamp(charge["datetime"]) - pd.Timestamp(prev["datetime"])
        ) <= MERGE_WINDOW
        if same_loc and within_window:
            prev["kwh"] = round(prev["kwh"] + charge["kwh"], 2)
            prev["end_datetime"] = charge["datetime"]
            if charge.get("invoice_url"):
                prev["invoice_url"] = charge["invoice_url"]
        else:
            merged.append(charge.copy())
    return merged


def should_split_trip(prev: dict, curr: dict) -> tuple[bool, str]:
    gap_days = (pd.Timestamp(curr["datetime"]) - pd.Timestamp(prev["datetime"])).days

    if gap_days >= TIME_GAP_DAYS:
        return True, f"time_gap_{gap_days}d"

    prev_dh = prev.get("dist_home")
    curr_dh = curr.get("dist_home")
    if (
        prev_dh is not None
        and curr_dh is not None
        and prev_dh > FAR_FROM_HOME_MILES
        and curr_dh < HOME_RADIUS_MILES
    ):
        return True, "return_to_dfw"

    prev_reg = prev.get("region", "UNK")
    curr_reg = curr.get("region", "UNK")
    if (
        prev_dh is not None
        and prev_dh > 1000
        and prev_reg == "PNW"
        and curr_reg in ("TX", "CO/NM", "UT/AZ/NV")
    ):
        return True, "pnw_to_southwest"

    if prev.get("lat") and curr.get("lat"):
        jump = haversine_miles(prev["lat"], prev["lng"], curr["lat"], curr["lng"])
        if jump > BIG_JUMP_MILES and prev_reg != curr_reg:
            if (
                prev_dh is not None
                and curr_dh is not None
                and curr_dh < prev_dh - 500
            ):
                return True, f"geo_reset_{jump:.0f}mi"

    return False, ""


def make_trip_name(start_dt: str, stops: list[dict]) -> str:
    start = pd.Timestamp(start_dt)
    month = start.strftime("%b %Y")

    co_stops = [s for s in stops if is_colorado(s.get("lat"), s.get("lng"), s["location"])]
    if co_stops:
        first, last = stops[0], stops[-1]
        if first.get("region") == "TX" or (
            first.get("dist_home") and first["dist_home"] < 200
        ):
            origin = "Dallas"
        else:
            origin = extract_city(first["location"])
        if last.get("region") == "TX" and len(co_stops) >= 2:
            dest = "Colorado (Round Trip)"
        else:
            co_cities = list(dict.fromkeys(extract_city(s["location"]) for s in co_stops))
            dest = co_cities[0] if len(co_cities) == 1 else "Colorado"
        return f"{month} — {origin} → {dest}"

    via = ordered_via_states(stops)
    origin = origin_label(stops)
    dest = dest_label(stops)

    if len(via) >= 3:
        via_str = format_via_summary(stops)
        return f"{month} — {origin} → {dest} (via {via_str})"

    cities: list[str] = []
    seen: set[str] = set()
    for s in stops:
        city = extract_city(s["location"])
        if city not in seen:
            cities.append(city)
            seen.add(city)
    if len(cities) >= 2:
        route = f"{origin} → {dest}" if origin != dest else cities[0]
    elif cities:
        route = cities[0]
    else:
        route = "Unknown"
    return f"{month} — {route}"


def make_trip_id(index: int, start_dt: str, stops: list[dict]) -> str:
    date_part = pd.Timestamp(start_dt).strftime("%Y-%m-%d")
    first_city = extract_city(stops[0]["location"]).replace(" ", "_")[:20]
    last_city = extract_city(stops[-1]["location"]).replace(" ", "_")[:20]
    return f"trip_{index:03d}_{date_part}_{first_city}_to_{last_city}"


def charge_to_stop(row: pd.Series, cache: dict) -> dict:
    loc = row["SiteLocationName"]
    entry = cache.get(loc, {})
    lat, lng = entry.get("lat"), entry.get("lng")
    return {
        "datetime": row["ChargeStartDateTime"],
        "location": loc,
        "lat": lat,
        "lng": lng,
        "kwh": float(row.get("kwh", 0)),
        "invoice_url": row.get("Invoice", ""),
        "dist_home": dist_from_home(lat, lng),
        "region": get_region(lat, lng, loc),
        "in_colorado": is_colorado(lat, lng, loc),
    }


def finalize_trip(stops: list[dict], trip_index: int, end_dt: str | None = None) -> dict:
    merged = merge_consecutive_stops(stops)
    start = merged[0]["datetime"]
    end = end_dt or merged[-1]["datetime"]
    co_stops = [s for s in merged if s.get("in_colorado")]
    via = ordered_via_states(merged)
    return {
        "id": make_trip_id(trip_index, start, merged),
        "name": make_trip_name(start, merged),
        "start": start,
        "end": end,
        "stops": merged,
        "has_colorado": len(co_stops) > 0,
        "colorado_stops": len(co_stops),
        "via_states": via,
        "via_summary": format_via_summary(merged),
        "origin_label": origin_label(merged),
        "dest_label": dest_label(merged),
    }


def segment_trips(charges: list[dict]) -> tuple[list[dict], list[dict]]:
    trips: list[dict] = []
    local: list[dict] = []
    current_stops: list[dict] = []
    trip_index = 0

    def flush_trip(end_dt: str | None = None) -> None:
        nonlocal trip_index, current_stops
        if not current_stops:
            return
        trip_index += 1
        trips.append(finalize_trip(current_stops, trip_index, end_dt))
        current_stops = []

    for charge in charges:
        if is_home(charge["location"]):
            flush_trip(end_dt=charge["datetime"])
            local.append(charge)
            continue

        if current_stops:
            prev = current_stops[-1]
            split, reason = should_split_trip(prev, charge)
            if split:
                flush_trip(end_dt=prev["datetime"])
                charge["_split_reason"] = reason

        current_stops.append(charge)

    flush_trip()
    return trips, local


def main() -> None:
    if not MERGED.exists():
        raise FileNotFoundError(f"Run merge_csvs.py first. Missing {MERGED}")
    if not CACHE_FILE.exists():
        raise FileNotFoundError(f"Run geocode_locations.py first. Missing {CACHE_FILE}")

    df = pd.read_csv(MERGED)
    df["ChargeStartDateTime"] = pd.to_datetime(df["ChargeStartDateTime"], utc=True)
    df = df.sort_values("ChargeStartDateTime")

    cache = load_cache()
    charges = [charge_to_stop(row, cache) for _, row in df.iterrows()]

    trips, local = segment_trips(charges)

    output = {
        "home_bases": sorted(HOME_BASES),
        "trips": trips,
        "local_charges": local,
        "stats": {
            "total_charges": len(charges),
            "trip_count": len(trips),
            "local_count": len(local),
            "total_stops_in_trips": sum(len(t["stops"]) for t in trips),
        },
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)

    print(f"Segmented {len(trips)} trips from {len(charges)} charges")
    print(f"Local (home-only) charges: {len(local)}")
    for t in trips:
        print(f"  {t['id']}: {len(t['stops'])} stops — {t['name']}")
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
