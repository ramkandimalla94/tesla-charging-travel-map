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

from home_config import extract_city, haversine_miles, load_owner_config, resolve_home_config

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
MERGED = DATA_DIR / "merged_charges.csv"
CACHE_FILE = DATA_DIR / "locations_cache.json"
OUTPUT = DATA_DIR / "trips.json"

MERGE_WINDOW = timedelta(hours=24)
HOME_BASES: set[str] = set()
HOME_REGIONS: list[dict] = []
HOME_LAT, HOME_LNG = 39.0, -98.0
HOME_LABEL = "Home"
HOME_RADIUS_MILES = 120
FAR_FROM_HOME_MILES = 350

# Trip quality — filter local metro charging mistaken as road trips
MIN_TRIP_SPAN_MILES = 80
MIN_TRIP_STATES = 2
LOCAL_METRO_SPAN_MILES = 45
LOCAL_HOME_DISTANCE_MILES = 40
# Metro bubble for "routine local Supercharging" — keep tight so Olympic /
# Leavenworth / Cascades day trips still qualify as real trips.
EXTENDED_HOME_RADIUS_MILES = 45
MIN_STOPS_PER_WEEK = 0.35
# Look back this far when anchoring a trip's departure to the last home charge.
HOME_ANCHOR_LOOKBACK_DAYS = 14

# Colorado state bounding box
CO_BBOX = {"lat_min": 37.0, "lat_max": 41.0, "lng_min": -109.1, "lng_max": -102.0}

TIME_GAP_DAYS = 14
BIG_JUMP_MILES = 700


def load_cache() -> dict:
    with open(CACHE_FILE, encoding="utf-8") as f:
        return json.load(f)


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


def dist_to_nearest_home(lat: float | None, lng: float | None) -> float | None:
    if lat is None or lng is None or not HOME_REGIONS:
        return dist_from_home(lat, lng)
    return min(haversine_miles(lat, lng, r["lat"], r["lng"]) for r in HOME_REGIONS)


def is_near_any_home(charge: dict) -> bool:
    if charge["location"] in HOME_BASES:
        return True
    lat, lng = charge.get("lat"), charge.get("lng")
    if lat is None or lng is None:
        return False
    for region in HOME_REGIONS:
        if haversine_miles(lat, lng, region["lat"], region["lng"]) <= region.get("radius_miles", 55):
            return True
    return False


def nearest_home_label(lat: float | None, lng: float | None) -> str:
    if lat is None or lng is None or not HOME_REGIONS:
        return HOME_LABEL
    best = min(HOME_REGIONS, key=lambda r: haversine_miles(lat, lng, r["lat"], r["lng"]))
    if haversine_miles(lat, lng, best["lat"], best["lng"]) <= best.get("radius_miles", 55) + 20:
        return best["label"]
    return "Away"


def trip_span_miles(stops: list[dict]) -> float:
    coords = [(s["lat"], s["lng"]) for s in stops if s.get("lat") is not None and s.get("lng") is not None]
    if len(coords) < 2:
        return 0.0
    return max(
        haversine_miles(a[0], a[1], b[0], b[1])
        for i, a in enumerate(coords)
        for b in coords[i + 1 :]
    )


def trip_duration_days(stops: list[dict], end_dt: str | None = None) -> int:
    if not stops:
        return 0
    start = pd.Timestamp(stops[0]["datetime"])
    if end_dt:
        end = pd.Timestamp(end_dt)
    else:
        end = pd.Timestamp(stops[-1].get("end_datetime") or stops[-1]["datetime"])
    return max(1, (end - start).days + 1)


def all_stops_within_extended_home(stops: list[dict], radius_miles: float = EXTENDED_HOME_RADIUS_MILES) -> bool:
    """True when every stop stays inside a home metro bubble (e.g. Seattle suburbs)."""
    if not HOME_REGIONS:
        return False
    for stop in stops:
        lat, lng = stop.get("lat"), stop.get("lng")
        if lat is None or lng is None:
            continue
        if not any(
            haversine_miles(lat, lng, region["lat"], region["lng"]) <= radius_miles
            for region in HOME_REGIONS
        ):
            return False
    return True


def charging_pace(stops: list[dict], end_dt: str | None = None) -> float:
    """Average merged stops per week over the trip timeline."""
    merged = merge_consecutive_stops(stops)
    weeks = max(1.0, trip_duration_days(merged, end_dt) / 7.0)
    return len(merged) / weeks


def is_destination_excursion(stops: list[dict]) -> bool:
    """True for notable getaways from a home metro (Olympic peninsula, Cascades, etc.)."""
    destinations = {
        "sequim", "forks", "aberdeen", "leavenworth", "cle elum", "yakima",
        "ellensburg", "richland", "kelso", "ridgefield", "port angeles",
        "ocean shores", "westport", "astoria", "cannon beach", "portland",
        "chehalis", "centralia", "olympia", "tumwater", "rochester",
        "silverdale", "suquamish", "port townsend",
    }
    cities = {extract_city(s["location"]).lower() for s in stops}
    return bool(cities & destinations)


def is_real_trip(stops: list[dict], end_dt: str | None = None) -> bool:
    """
    Return True only for genuine road trips — not local metro Supercharging patterns.
    """
    if len(stops) < 2:
        return False

    merged = merge_consecutive_stops(stops)
    states = ordered_via_states(merged)
    span = trip_span_miles(merged)
    duration = trip_duration_days(merged, end_dt)
    home_dists = [dist_to_nearest_home(s.get("lat"), s.get("lng")) for s in merged]
    home_dists = [d for d in home_dists if d is not None]
    max_home_dist = max(home_dists) if home_dists else 0.0
    min_home_dist = min(home_dists) if home_dists else 0.0

    pace = charging_pace(stops, end_dt)
    excursion = is_destination_excursion(merged)

    # Every stop inside a tight home metro bubble = routine local Supercharging
    if all_stops_within_extended_home(merged) and not excursion:
        return False

    # Destination getaways (Olympic, Leavenworth, etc.) with a real outbound leg
    if excursion and span >= 35 and max_home_dist >= LOCAL_HOME_DISTANCE_MILES:
        return True

    # Too few stops over a small area — not a road trip
    if len(merged) < 3 and span < MIN_TRIP_SPAN_MILES:
        return False

    # Must leave the local home bubble meaningfully
    if max_home_dist < LOCAL_HOME_DISTANCE_MILES:
        return False

    # Single-state blob that never spans far = routine local charging
    if span < LOCAL_METRO_SPAN_MILES and len(states) <= 1:
        return False

    # Long timeline, tiny geography = sporadic local Supercharging
    if duration >= 10 and span < MIN_TRIP_SPAN_MILES and len(states) <= 1:
        return False

    # Sparse charging over weeks still inside the metro footprint
    if duration >= 7 and pace < MIN_STOPS_PER_WEEK and max_home_dist < EXTENDED_HOME_RADIUS_MILES + 15:
        return False

    if duration >= 7 and len(merged) <= 3 and span < 120 and max_home_dist < EXTENDED_HOME_RADIUS_MILES + 15:
        return False

    # Single-state regional hops that barely leave home
    if len(states) == 1 and span < MIN_TRIP_SPAN_MILES and min_home_dist < 20 and not excursion:
        return False

    # Multi-state journeys with sustained travel pace are real road trips
    if len(states) >= MIN_TRIP_STATES and pace >= MIN_STOPS_PER_WEEK:
        return True

    # Multi-state but sparse + still near home = errand charging split by time gap
    if len(states) >= MIN_TRIP_STATES and max_home_dist < EXTENDED_HOME_RADIUS_MILES + 15:
        return False

    # Clear road trip: large geographic span in one state (e.g. cross-Texas / Olympic loop)
    if span >= MIN_TRIP_SPAN_MILES:
        return True

    # Left home region by a lot even if one state
    if max_home_dist >= FAR_FROM_HOME_MILES:
        return True

    return False


def extract_state_code(location: str) -> str | None:
    m = re.search(r",\s*([A-Z]{2})\b", location)
    return m.group(1) if m else None


def origin_label(stops: list[dict]) -> str:
    first = stops[0]
    if first.get("is_home_anchor") or first.get("region") == "HOME":
        lbl = nearest_home_label(first.get("lat"), first.get("lng"))
        if lbl != "Away":
            return lbl
    lbl = nearest_home_label(first.get("lat"), first.get("lng"))
    if lbl != "Away" and first.get("dist_home") is not None:
        dh = dist_to_nearest_home(first.get("lat"), first.get("lng"))
        if dh is not None and dh < HOME_RADIUS_MILES:
            return lbl
    return extract_city(first["location"])


def dest_label(stops: list[dict]) -> str:
    last = stops[-1]
    if last.get("is_home_anchor") or last.get("region") == "HOME":
        lbl = nearest_home_label(last.get("lat"), last.get("lng"))
        if lbl != "Away":
            return lbl
    lbl = nearest_home_label(last.get("lat"), last.get("lng"))
    if lbl != "Away" and last.get("dist_home") is not None:
        dh = dist_to_nearest_home(last.get("lat"), last.get("lng"))
        if dh is not None and dh < HOME_RADIUS_MILES:
            return lbl
    return extract_city(last["location"])


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
        return True, "return_to_home"

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


def trip_highlight_destination(stops: list[dict]) -> str | None:
    """Human label for notable getaway destinations."""
    cities = {extract_city(s["location"]).lower() for s in stops}
    states = ordered_via_states(stops)

    def furthest_city() -> str | None:
        ranked = []
        for s in stops:
            if s.get("is_home_anchor"):
                continue
            dh = s.get("dist_home")
            if dh is None:
                continue
            ranked.append((dh, extract_city(s["location"])))
        if not ranked:
            return None
        ranked.sort(reverse=True)
        if ranked[0][0] >= 60:
            return ranked[0][1]
        return None

    # Multi-state epics: name after the farthest stop (Las Vegas, Page, Oceanside…)
    if len(states) >= 3:
        return furthest_city()

    if {"forks", "sequim"} & cities or {"forks", "aberdeen"} & cities:
        return "Olympic Peninsula"
    if "leavenworth" in cities:
        return "Leavenworth"
    if "cle elum" in cities and "leavenworth" not in cities:
        return "Cascades"
    if "portland" in cities:
        return "Portland"
    return furthest_city()


def make_trip_name(start_dt: str, stops: list[dict]) -> str:
    start = pd.Timestamp(start_dt)
    month = start.strftime("%b %Y")

    co_stops = [s for s in stops if is_colorado(s.get("lat"), s.get("lng"), s["location"])]
    if co_stops:
        first, last = stops[0], stops[-1]
        if first.get("is_home_anchor") or (first.get("dist_home") is not None and first["dist_home"] < HOME_RADIUS_MILES):
            origin = origin_label(stops)
        else:
            origin = extract_city(first["location"])
        if last.get("dist_home") is not None and last["dist_home"] < HOME_RADIUS_MILES and len(co_stops) >= 2:
            dest = "Colorado (Round Trip)"
        else:
            co_cities = list(dict.fromkeys(extract_city(s["location"]) for s in co_stops))
            dest = co_cities[0] if len(co_cities) == 1 else "Colorado"
        return f"{month} — {origin} → {dest}"

    via = ordered_via_states(stops)
    origin = origin_label(stops)
    dest = dest_label(stops)
    highlight = trip_highlight_destination(stops)
    home_labels = {r.get("label") for r in HOME_REGIONS} | {HOME_LABEL}

    if len(via) >= 3:
        via_str = format_via_summary(stops)
        # Prefer home arrival label for cross-country relocates (Addison → Bellevue)
        if dest in home_labels and dest != origin:
            end = dest
        else:
            end = highlight or dest
        return f"{month} — {origin} → {end} (via {via_str})"

    if highlight:
        return f"{month} — {origin} → {highlight}"

    cities: list[str] = []
    seen: set[str] = set()
    for s in stops:
        city = extract_city(s["location"])
        if city not in seen:
            cities.append(city)
            seen.add(city)
    if origin == dest and len(cities) >= 3:
        mid = cities[len(cities) // 2]
        route = f"{origin} → {mid}"
    elif len(cities) >= 2:
        route = f"{origin} → {dest}" if origin != dest else f"{origin} → {cities[1]}"
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
        "dist_home": dist_to_nearest_home(lat, lng),
        "region": get_region(lat, lng, loc),
        "in_colorado": is_colorado(lat, lng, loc),
        "owner": str(row.get("Name", "") or "").strip(),
        "vin": str(row.get("Vin", "") or "").strip(),
    }


def owner_short_name(name: str) -> str:
    """First name (or first token) for trip labels."""
    if not name:
        return ""
    return name.strip().split()[0]


def trip_base_name(name: str, owner_short: str = "") -> str:
    """Strip trailing driver suffix added during finalize_trip."""
    if owner_short and name.endswith(f" · {owner_short}"):
        return name[: -len(f" · {owner_short}")]
    return name


def trip_matches_shared_rule(trip: dict, rule: dict) -> bool:
    match = rule.get("match", {})
    owner = trip.get("owner", "")
    if match.get("owner") and owner != match["owner"]:
        return False
    if match.get("owner_contains") and match["owner_contains"].lower() not in owner.lower():
        return False
    if match.get("vin") and trip.get("vin") != match["vin"]:
        return False
    if match.get("has_colorado") and not trip.get("has_colorado"):
        return False
    return True


def default_shared_trip_rules(profile_name: str) -> list[dict]:
    """Built-in rules when owner_config.json has no shared_trips section."""
    return [
        {
            "match": {"owner_contains": "Akash", "has_colorado": True},
            "travelers": [profile_name, "Akash"],
            "driver": "Akash",
            "vehicle_label": "Akash's car",
        }
    ]


def apply_trip_crew_labels(trips: list[dict]) -> None:
    """
    Label who was on each trip. Charging CSV shows the car account holder (driver);
    shared_trips config marks when you rode in someone else's car.
    """
    cfg = load_owner_config()
    profile = cfg.get("profile_name", "Rama")
    rules = cfg.get("shared_trips") or default_shared_trip_rules(profile)

    for trip in trips:
        matched = False
        for rule in rules:
            if not trip_matches_shared_rule(trip, rule):
                continue
            travelers = rule.get("travelers") or [profile, trip.get("owner_short", "")]
            driver = rule.get("driver") or trip.get("owner_short", "")
            vehicle = rule.get("vehicle_label") or f"{driver}'s car"
            crew = " + ".join(travelers)
            base = trip_base_name(trip["name"], trip.get("owner_short", ""))
            trip.update({
                "name": f"{base} · {crew} ({vehicle})",
                "travelers": travelers,
                "trip_crew": crew,
                "driver": driver,
                "driver_short": driver,
                "vehicle_label": vehicle,
                "is_shared": True,
            })
            matched = True
            break

        if matched:
            continue

        driver = trip.get("owner_short") or profile
        base = trip_base_name(trip["name"], trip.get("owner_short", ""))
        if driver.lower() == profile.lower():
            trip.update({
                "name": base,
                "travelers": [profile],
                "trip_crew": profile,
                "driver": profile,
                "driver_short": profile,
                "vehicle_label": "your Tesla",
                "is_shared": False,
            })
        else:
            trip.update({
                "travelers": [driver],
                "trip_crew": driver,
                "driver": driver,
                "driver_short": driver,
                "vehicle_label": f"{driver}'s car",
                "is_shared": False,
            })


def finalize_trip(
    stops: list[dict],
    trip_index: int,
    end_dt: str | None = None,
    *,
    owner: str = "",
    vin: str = "",
) -> dict:
    merged = merge_consecutive_stops(stops)
    start = merged[0]["datetime"]
    end = end_dt or merged[-1]["datetime"]
    co_stops = [s for s in merged if s.get("in_colorado")]
    via = ordered_via_states(merged)
    name = make_trip_name(start, merged)
    short = owner_short_name(owner)
    if short:
        name = f"{name} · {short}"
    return {
        "id": make_trip_id(trip_index, start, merged),
        "name": name,
        "start": start,
        "end": end,
        "stops": merged,
        "has_colorado": len(co_stops) > 0,
        "colorado_stops": len(co_stops),
        "via_states": via,
        "via_summary": format_via_summary(merged),
        "origin_label": origin_label(merged),
        "dest_label": dest_label(merged),
        "owner": owner,
        "vin": vin,
        "owner_short": short,
    }


def nearest_home_region(lat: float | None, lng: float | None) -> dict | None:
    if lat is None or lng is None or not HOME_REGIONS:
        return None
    return min(HOME_REGIONS, key=lambda r: haversine_miles(lat, lng, r["lat"], r["lng"]))


def synthesize_home_anchor(near_charge: dict, last_home: dict | None) -> dict | None:
    """
    Build a trip start stop at the canonical home pin (Addison / Bellevue).
    Timing comes from the last real home Supercharge when recent; otherwise
    a short synthetic departure before the first away stop.
    """
    region = nearest_home_region(near_charge.get("lat"), near_charge.get("lng"))
    if region is None and HOME_REGIONS:
        # Prefer the region matching the last home charge when available
        if last_home is not None:
            region = nearest_home_region(last_home.get("lat"), last_home.get("lng"))
        if region is None:
            region = HOME_REGIONS[0]
    if region is None:
        return None

    bases = set(region.get("bases") or [])
    region_label = region.get("label") or ""
    label = region.get("canonical_location") or next(iter(bases), region_label)
    preferred_candidates = [
        "Addison, TX" if "Addison" in region_label else "",
        "Bellevue, WA - Northeast 8th Street" if "Bellevue" in region_label else "",
        f"{region_label}, TX",
        f"{region_label}, WA",
    ]
    for preferred in preferred_candidates:
        if preferred and (preferred in bases or preferred.startswith(region_label)):
            label = preferred
            break
    if "Addison" in region_label:
        label = "Addison, TX"
    elif "Bellevue" in region_label:
        label = "Bellevue, WA - Northeast 8th Street"

    if last_home is not None:
        gap = pd.Timestamp(near_charge["datetime"]) - pd.Timestamp(last_home["datetime"])
        if gap <= timedelta(days=HOME_ANCHOR_LOOKBACK_DAYS):
            dt = last_home["datetime"]
        else:
            dt = (pd.Timestamp(near_charge["datetime"]) - timedelta(hours=2)).isoformat()
    else:
        dt = (pd.Timestamp(near_charge["datetime"]) - timedelta(hours=2)).isoformat()

    return {
        "datetime": dt,
        "location": label,
        "lat": region["lat"],
        "lng": region["lng"],
        "kwh": float(last_home.get("kwh", 0)) if last_home else 0.0,
        "invoice_url": (last_home or {}).get("invoice_url", ""),
        "dist_home": 0.0,
        "region": "HOME",
        "in_colorado": False,
        "owner": near_charge.get("owner", ""),
        "vin": near_charge.get("vin", ""),
        "is_home_anchor": True,
        "synthetic": True,
    }


def segment_trips(
    charges: list[dict],
    *,
    owner: str = "",
    vin: str = "",
    trip_index_start: int = 0,
) -> tuple[list[dict], list[dict], int]:
    trips: list[dict] = []
    local: list[dict] = []
    current_stops: list[dict] = []
    trip_index = trip_index_start
    last_home: dict | None = None

    def flush_trip(end_dt: str | None = None) -> None:
        nonlocal trip_index, current_stops
        if not current_stops:
            return
        if is_real_trip(current_stops, end_dt):
            trip_index += 1
            trips.append(
                finalize_trip(
                    current_stops, trip_index, end_dt, owner=owner, vin=vin
                )
            )
        else:
            local.extend(
                s for s in current_stops if not s.get("is_home_anchor") or not s.get("synthetic")
            )
        current_stops = []

    def begin_away_trip(charge: dict) -> None:
        """Start a new away segment, anchored at the nearest home departure."""
        nonlocal current_stops
        anchor = synthesize_home_anchor(charge, last_home)
        if anchor is not None:
            # Avoid duplicating if the away charge somehow is already home-labelled
            if anchor.get("location") != charge.get("location"):
                current_stops = [anchor, charge]
                return
        current_stops = [charge]

    for charge in charges:
        if is_near_any_home(charge):
            if current_stops:
                prev = current_stops[-1]
                gap_days = (
                    pd.Timestamp(charge["datetime"]) - pd.Timestamp(prev["datetime"])
                ).days
                if gap_days >= TIME_GAP_DAYS:
                    # Long pause away, then a home charge — end the trip at the
                    # last away stop rather than stretching to a distant home pin.
                    flush_trip(end_dt=prev["datetime"])
                    local.append(charge)
                else:
                    # Snap the return pin to the canonical home for that region
                    end_home = synthesize_home_anchor(charge, charge)
                    if end_home is not None:
                        end_home["datetime"] = charge["datetime"]
                        end_home["kwh"] = float(charge.get("kwh", 0))
                        end_home["invoice_url"] = charge.get("invoice_url", "")
                        end_home["synthetic"] = charge.get("location") != end_home.get("location")
                        current_stops.append(end_home)
                    else:
                        current_stops.append({**charge, "is_home_anchor": True})
                    flush_trip(end_dt=charge["datetime"])
            else:
                local.append(charge)
            last_home = charge
            continue

        if current_stops:
            prev = current_stops[-1]
            split, reason = should_split_trip(prev, charge)
            if split:
                flush_trip(end_dt=prev["datetime"])
                charge["_split_reason"] = reason
                begin_away_trip(charge)
            else:
                current_stops.append(charge)
        else:
            begin_away_trip(charge)

    flush_trip()
    return trips, local, trip_index


def init_home_config(locations: list[str], cache: dict) -> None:
    global HOME_BASES, HOME_REGIONS, HOME_LAT, HOME_LNG, HOME_LABEL
    global HOME_RADIUS_MILES, FAR_FROM_HOME_MILES
    cfg = resolve_home_config(locations, cache)
    HOME_BASES = cfg["home_bases"]
    HOME_REGIONS = cfg.get("home_regions", [])
    HOME_LAT = cfg["home_lat"]
    HOME_LNG = cfg["home_lng"]
    HOME_LABEL = cfg["home_label"]
    HOME_RADIUS_MILES = cfg["home_radius_miles"]
    FAR_FROM_HOME_MILES = cfg["far_from_home_miles"]


def main() -> None:
    if not MERGED.exists():
        raise FileNotFoundError(f"Run merge_csvs.py first. Missing {MERGED}")
    if not CACHE_FILE.exists():
        raise FileNotFoundError(f"Run geocode_locations.py first. Missing {CACHE_FILE}")

    df = pd.read_csv(MERGED)
    df["ChargeStartDateTime"] = pd.to_datetime(df["ChargeStartDateTime"], utc=True)
    df = df.sort_values("ChargeStartDateTime")
    if "Vin" not in df.columns:
        df["Vin"] = ""
    if "Name" not in df.columns:
        df["Name"] = ""
    df["Vin"] = df["Vin"].fillna("").astype(str).str.strip()
    df["Name"] = df["Name"].fillna("").astype(str).str.strip()

    cache = load_cache()

    # Detect home from ALL charges so multi-owner DFW homes cluster together,
    # then segment each vehicle independently so routes never cross cars.
    init_home_config(df["SiteLocationName"].tolist(), cache)
    detection_source = "config" if (DATA_DIR / "owner_config.json").exists() else "auto"

    all_trips: list[dict] = []
    all_local: list[dict] = []
    trip_index = 0
    vehicle_groups = df.groupby(["Vin", "Name"], dropna=False, sort=False)

    for (vin, owner), group in vehicle_groups:
        owner_s = str(owner or "").strip()
        vin_s = str(vin or "").strip() or "unknown"
        # Re-init home using this vehicle's charge locations so friend's Frisco
        # home and owner's home are both respected when flushing "near home".
        init_home_config(group["SiteLocationName"].tolist(), cache)
        charges = [charge_to_stop(row, cache) for _, row in group.iterrows()]
        trips, local, trip_index = segment_trips(
            charges, owner=owner_s, vin=vin_s, trip_index_start=trip_index
        )
        all_trips.extend(trips)
        all_local.extend(local)
        label = owner_s or vin_s[-6:]
        print(f"  Vehicle {label}: {len(trips)} trips · {len(local)} local from {len(charges)} charges")

    all_trips.sort(key=lambda t: str(t["start"]))
    apply_trip_crew_labels(all_trips)
    # Re-number trip ids in chronological order after multi-vehicle merge
    for i, trip in enumerate(all_trips, start=1):
        date_part = pd.Timestamp(trip["start"]).strftime("%Y-%m-%d")
        first_city = extract_city(trip["stops"][0]["location"]).replace(" ", "_")[:20]
        last_city = extract_city(trip["stops"][-1]["location"]).replace(" ", "_")[:20]
        trip["id"] = f"trip_{i:03d}_{date_part}_{first_city}_to_{last_city}"

    # Restore global home summary from all locations for metadata
    init_home_config(df["SiteLocationName"].tolist(), cache)

    owners = sorted({t.get("owner", "") for t in all_trips if t.get("owner")})
    output = {
        "home_bases": sorted(HOME_BASES),
        "home_label": HOME_LABEL,
        "home_lat": HOME_LAT,
        "home_lng": HOME_LNG,
        "home_regions": [
            {
                "label": r["label"],
                "lat": r["lat"],
                "lng": r["lng"],
                "radius_miles": r.get("radius_miles", 55),
                "charge_count": r.get("charge_count", 0),
                "bases": sorted(r.get("bases", [])),
                "canonical_location": r.get("canonical_location"),
            }
            for r in HOME_REGIONS
        ],
        "owners": owners,
        "trips": all_trips,
        "local_charges": all_local,
        "stats": {
            "total_charges": len(df),
            "trip_count": len(all_trips),
            "local_count": len(all_local),
            "total_stops_in_trips": sum(len(t["stops"]) for t in all_trips),
            "home_detection": detection_source,
            "vehicle_count": len(vehicle_groups),
            "owner_count": len(owners),
        },
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)

    print(f"Segmented {len(all_trips)} trips from {len(df)} charges across {len(vehicle_groups)} vehicle(s)")
    print(f"Local (home-only) charges: {len(all_local)}")
    for t in all_trips:
        co = " 🏔" if t.get("has_colorado") else ""
        print(f"  {t['id']}: {len(t['stops'])} stops — {t['name']}{co}")
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
