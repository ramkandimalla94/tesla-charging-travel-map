#!/usr/bin/env python3
"""
Segment travel history into road trips using multi-signal boundary detection.

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

Every trip start pin is Addison, TX or Bellevue, WA. Returns usually snap to those
hubs too, except when you came home with leftover range and Supercharged days
later — that delayed home session is local charging, and the trip can end at
the last home Supercharge (e.g. Frisco, TX on Sep 29, not Addison on Oct 2).

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
# Came home with leftover range and Supercharged days later — that later
# home session is local charging, not the trip end (e.g. Frisco Sep 29,
# Addison charge on Oct 2).
DELAYED_HOME_RETURN_HOURS = 30
HOME_APPROACH_MILES = 120


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


def endpoint_city(location: str) -> str | None:
    """City label for a trip pin — Addison/Bellevue, or another configured home base."""
    loc = (location or "").strip()
    if loc.startswith("Addison"):
        return "Addison"
    if loc.startswith("Bellevue"):
        return "Bellevue"
    if loc in HOME_BASES:
        return extract_city(loc)
    return None


def origin_label(stops: list[dict]) -> str:
    first = stops[0]
    city = endpoint_city(first.get("location", ""))
    if city:
        return city
    if first.get("is_home_anchor") or first.get("region") == "HOME":
        lbl = nearest_home_label(first.get("lat"), first.get("lng"))
        if lbl in {"Addison", "Bellevue"}:
            return lbl
    lbl = nearest_home_label(first.get("lat"), first.get("lng"))
    if lbl in {"Addison", "Bellevue"} and first.get("dist_home") is not None:
        dh = dist_to_nearest_home(first.get("lat"), first.get("lng"))
        if dh is not None and dh < HOME_RADIUS_MILES:
            return lbl
    return lbl if lbl in {"Addison", "Bellevue"} else "Addison"


def dest_label(stops: list[dict]) -> str:
    last = stops[-1]
    city = endpoint_city(last.get("location", ""))
    if city:
        return city
    if last.get("preserve_home_location"):
        return extract_city(last.get("location") or "") or origin_label(stops)
    if last.get("is_home_anchor") or last.get("region") == "HOME":
        lbl = nearest_home_label(last.get("lat"), last.get("lng"))
        if lbl in {"Addison", "Bellevue"}:
            return lbl
    lbl = nearest_home_label(last.get("lat"), last.get("lng"))
    if lbl in {"Addison", "Bellevue"} and last.get("dist_home") is not None:
        dh = dist_to_nearest_home(last.get("lat"), last.get("lng"))
        if dh is not None and dh < HOME_RADIUS_MILES:
            return lbl
    return lbl if lbl in {"Addison", "Bellevue"} else origin_label(stops)


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


def is_delayed_home_return(prev: dict, home_charge: dict) -> bool:
    """
    True when the last away stop is already approaching home and the next
    home Supercharge is days later (leftover range — not the trip arrival).
    """
    try:
        gap_h = (
            pd.Timestamp(home_charge["datetime"]) - pd.Timestamp(prev["datetime"])
        ).total_seconds() / 3600.0
    except Exception:
        return False
    if gap_h < DELAYED_HOME_RETURN_HOURS:
        return False
    dh = prev.get("dist_home")
    if dh is None:
        return False
    return float(dh) <= HOME_APPROACH_MILES


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
                "vehicle_label": "",
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
    merged = ensure_canonical_home_endpoints(merge_consecutive_stops(stops))
    start = merged[0]["datetime"]
    end = merged[-1]["datetime"]
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


def is_canonical_home_location(location: str) -> bool:
    """Trip endpoints may only be Addison, TX or Bellevue, WA."""
    loc = (location or "").strip()
    return loc.startswith("Addison") or loc.startswith("Bellevue")


def canonical_location_for_region(region: dict) -> str:
    region_label = region.get("label") or ""
    if "Addison" in region_label or region_label == "Addison":
        return "Addison, TX"
    if "Bellevue" in region_label or region_label == "Bellevue":
        return "Bellevue, WA - Northeast 8th Street"
    explicit = region.get("canonical_location")
    if explicit and is_canonical_home_location(explicit):
        return explicit
    # Fall back to whichever configured home matches the only two allowed pins
    for base in region.get("bases") or []:
        if is_canonical_home_location(base):
            return base
    return "Addison, TX" if "TX" in region_label or "Dallas" in region_label else (
        "Bellevue, WA - Northeast 8th Street"
    )


def region_for_canonical_return(stops: list[dict]) -> dict | None:
    """
    Choose Addison or Bellevue for a missing trip end pin.

    Prefer a home region the last real stop is already inside (relocation /
    true return). Otherwise round-trip back to the origin home region.
    """
    if not HOME_REGIONS:
        return None

    real_stops = [s for s in stops if not (s.get("is_home_anchor") and s.get("synthetic"))]
    probe = real_stops[-1] if real_stops else stops[-1]
    lat, lng = probe.get("lat"), probe.get("lng")
    if lat is not None and lng is not None:
        for region in HOME_REGIONS:
            radius = float(region.get("radius_miles") or HOME_RADIUS_MILES)
            if haversine_miles(lat, lng, region["lat"], region["lng"]) <= radius:
                return region

    first = stops[0]
    origin = nearest_home_region(first.get("lat"), first.get("lng"))
    return origin or HOME_REGIONS[0]


def synthesize_home_anchor(
    near_charge: dict,
    last_home: dict | None,
    *,
    region: dict | None = None,
    as_return: bool = False,
) -> dict | None:
    """
    Build a trip start/end stop at the canonical home pin (Addison / Bellevue).
    Timing comes from the last real home Supercharge when recent; otherwise
    a short synthetic departure before the first away stop (or arrival after
    the last away stop when as_return=True).
    """
    if region is None:
        region = nearest_home_region(near_charge.get("lat"), near_charge.get("lng"))
        if region is None and HOME_REGIONS:
            # Prefer the region matching the last home charge when available
            if last_home is not None:
                region = nearest_home_region(last_home.get("lat"), last_home.get("lng"))
            if region is None:
                region = HOME_REGIONS[0]
    if region is None:
        return None

    label = canonical_location_for_region(region)

    if as_return:
        dt = (pd.Timestamp(near_charge["datetime"]) + timedelta(hours=2)).isoformat()
    elif last_home is not None:
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


def ensure_canonical_home_endpoints(stops: list[dict]) -> list[dict]:
    """
    Force every trip to start and end at Addison or Bellevue only.

    Excursions that never logged a home Supercharge on return still get a
    synthetic home arrival so map pins / IDs / labels never show Leavenworth,
    Chehalis, Tumwater, etc. as endpoints.
    """
    if not stops:
        return stops

    out = [s.copy() for s in stops]

    first = out[0]
    if not (first.get("is_home_anchor") and is_canonical_home_location(first.get("location", ""))):
        start_region = nearest_home_region(first.get("lat"), first.get("lng")) or (
            HOME_REGIONS[0] if HOME_REGIONS else None
        )
        if start_region is not None:
            anchor = synthesize_home_anchor(first, None, region=start_region)
            if anchor is not None:
                if is_canonical_home_location(first.get("location", "")) and first.get("is_home_anchor"):
                    out[0] = {**first, **{k: anchor[k] for k in ("location", "lat", "lng", "region", "dist_home")}}
                else:
                    out.insert(0, anchor)

    last = out[-1]
    if last.get("preserve_home_location") and last.get("location") in HOME_BASES:
        return out
    if last.get("is_home_anchor") and is_canonical_home_location(last.get("location", "")):
        return out

    # Snap a non-canonical near-home last stop (Kirkland, Plano, …) to Addison/Bellevue
    if last.get("is_home_anchor") or last.get("region") == "HOME" or is_near_any_home(last):
        end_region = nearest_home_region(last.get("lat"), last.get("lng"))
        end_home = synthesize_home_anchor(last, last, region=end_region)
        if end_home is not None:
            end_home["datetime"] = last["datetime"]
            end_home["kwh"] = float(last.get("kwh", 0))
            end_home["invoice_url"] = last.get("invoice_url", "")
            end_home["synthetic"] = last.get("location") != end_home.get("location")
            out[-1] = end_home
        return out

    end_region = region_for_canonical_return(out)
    end_home = synthesize_home_anchor(last, None, region=end_region, as_return=True)
    if end_home is None:
        return out
    out.append(end_home)
    return out


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
                    # Long pause away, then a home charge — close the open trip
                    # with a synthetic Addison/Bellevue return (finalize_trip),
                    # and keep the later home Supercharge as local charging.
                    flush_trip(end_dt=prev["datetime"])
                    local.append(charge)
                elif is_delayed_home_return(prev, charge):
                    # Approached home with leftover range; Supercharged days later.
                    # End at the home region they were heading toward — not the
                    # later local charge. (owner_config can pin Frisco, etc.)
                    if not (
                        prev.get("is_home_anchor")
                        or prev.get("region") == "HOME"
                        or is_near_any_home(prev)
                    ):
                        approach = nearest_home_region(prev.get("lat"), prev.get("lng"))
                        end_home = synthesize_home_anchor(
                            prev, last_home, region=approach, as_return=True
                        )
                        if end_home is not None:
                            current_stops.append(end_home)
                    flush_trip(
                        end_dt=current_stops[-1]["datetime"] if current_stops else prev["datetime"]
                    )
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


def trip_matches_end_override(trip: dict, match: dict) -> bool:
    if match.get("id_contains") and match["id_contains"] not in str(trip.get("id") or ""):
        return False
    if match.get("start_prefix") and not str(trip.get("start") or "").startswith(str(match["start_prefix"])):
        return False
    if match.get("has_colorado") and not trip.get("has_colorado"):
        return False
    owner = trip.get("owner") or ""
    if match.get("owner_contains") and match["owner_contains"].lower() not in owner.lower():
        return False
    return True


def apply_configured_trip_overrides(trips: list[dict], cache: dict) -> None:
    """
    Optional owner_config.trip_overrides — pin a trip end when Supercharging
    after returning home with leftover range would otherwise extend the trip.
    """
    cfg = load_owner_config()
    rules = cfg.get("trip_overrides") or []
    if not rules:
        return
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        match = rule.get("match") or {}
        for trip in trips:
            if not trip_matches_end_override(trip, match):
                continue
            end_before = rule.get("end_before")
            if end_before:
                cut = pd.Timestamp(end_before)
                if cut.tzinfo is None:
                    cut = cut.tz_localize("UTC")
                trip["stops"] = [
                    s for s in trip["stops"]
                    if pd.Timestamp(s["datetime"]) < cut
                ]
            end_loc = rule.get("end_location")
            stops = trip.get("stops") or []
            if end_loc and stops and stops[-1].get("location") != end_loc:
                last = stops[-1]
                if last.get("is_home_anchor") and last.get("synthetic"):
                    stops.pop()
                    last = stops[-1] if stops else last
                entry = cache.get(end_loc) or {}
                dt = (pd.Timestamp(last["datetime"]) + timedelta(hours=2)).isoformat()
                lat = entry.get("lat")
                lng = entry.get("lng")
                stops.append({
                    "datetime": dt,
                    "location": end_loc,
                    "lat": lat,
                    "lng": lng,
                    "kwh": 0.0,
                    "invoice_url": "",
                    "dist_home": 0.0,
                    "region": "HOME",
                    "in_colorado": False,
                    "owner": trip.get("owner", ""),
                    "vin": trip.get("vin", ""),
                    "is_home_anchor": True,
                    "synthetic": True,
                    "preserve_home_location": True,
                })
                trip["stops"] = stops
            if not trip.get("stops"):
                continue
            trip["end"] = trip["stops"][-1]["datetime"]
            trip["dest_label"] = rule.get("dest_label") or dest_label(trip["stops"])
            trip["name"] = make_trip_name(trip["start"], trip["stops"])
            short = trip.get("owner_short") or ""
            if short and not trip["name"].endswith(f" · {short}"):
                trip["name"] = f"{trip['name']} · {short}"
            trip["via_states"] = ordered_via_states(trip["stops"])
            trip["via_summary"] = format_via_summary(trip["stops"])
            co = [s for s in trip["stops"] if s.get("in_colorado")]
            trip["has_colorado"] = bool(co)
            trip["colorado_stops"] = len(co)
            trip["origin_label"] = origin_label(trip["stops"])


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
    apply_configured_trip_overrides(all_trips, cache)
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
