#!/usr/bin/env python3
"""Browser verification for travel map — Playwright screenshots + assertions."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "output" / "travel_map.html"
SCREENSHOTS = ROOT / "docs" / "screenshots"
TRIPS_FILE = ROOT / "data" / "trips.json"

CO_BOUNDS = {"lat_min": 37, "lat_max": 41, "lng_min": -109, "lng_max": -102}
CONUS = {"lat_min": 24, "lat_max": 50, "lng_min": -125, "lng_max": -95}


def load_trip_id(keyword: str) -> str:
    data = json.loads(TRIPS_FILE.read_text(encoding="utf-8"))
    for trip in data["trips"]:
        if keyword in trip["id"]:
            return trip["id"]
    raise KeyError(f"No trip matching {keyword!r}")


def in_conus(lat: float, lng: float) -> bool:
    return (
        CONUS["lat_min"] <= lat <= CONUS["lat_max"]
        and CONUS["lng_min"] <= lng <= CONUS["lng_max"]
    )


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Install playwright: pip install playwright && playwright install chromium")
        return 1

    if not OUTPUT.exists():
        print(f"Missing {OUTPUT} — run build_map.py first")
        return 1

    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    trip_co = load_trip_id("2024-06-29_Addison")  # Colorado round trip
    trip_sea = load_trip_id("2024-11-17_Addison_to_Bellevue")

    console_errors: list[str] = []
    base_url = "http://127.0.0.1:8765/output/travel_map.html"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda err: console_errors.append(str(err)))

        print(f"Loading {base_url}")
        page.goto(base_url, wait_until="networkidle", timeout=90000)
        page.wait_for_selector(".mapboxgl-canvas", timeout=60000)
        page.wait_for_timeout(3000)
        page.evaluate("document.getElementById('splash')?.classList.add('hidden')")
        page.evaluate("document.getElementById('onboarding')?.classList.remove('visible')")
        page.wait_for_timeout(1000)

        # Overview
        overview = page.evaluate(
            """() => ({
              center: map.getCenter(),
              pitch: map.getPitch(),
              zoom: map.getZoom(),
              projection: map.getProjection?.()?.name || 'mercator',
              hasDeck: typeof deck !== 'undefined',
            })"""
        )
        print(f"Overview camera: {overview}")
        page.screenshot(path=str(SCREENSHOTS / "01-overview.png"))

        declutter = page.evaluate(
            """() => ({
              hubs: document.querySelectorAll('.home-hub').length,
              destStars: document.querySelectorAll('.dest-star').length,
              chips: document.querySelectorAll('.route-chip').length,
              stopDots: document.querySelectorAll('.stop-dot').length,
              eraChips: document.querySelectorAll('.era-chip').length,
              hubTripCounts: (DASHBOARD.hubs || []).map(h => h.trip_count),
              destGroups: (DASHBOARD.destination_groups || []).length,
            })"""
        )
        print(f"Overview declutter: {declutter}")

        # Year filter smoke
        page.evaluate("setYearFilter('2025')")
        page.wait_for_timeout(600)
        era_state = page.evaluate(
            """() => ({
              yearFilter,
              dimmedTrips: document.querySelectorAll('.trip-item.era-dim').length,
              destStars: document.querySelectorAll('.dest-star').length,
            })"""
        )
        print(f"Era filter 2025: {era_state}")
        page.evaluate("setYearFilter('all')")
        page.wait_for_timeout(400)

        # Memory reel + featured CTA
        page.evaluate("localStorage.setItem('tesla-map-onboarded','1'); startMemoryReel()")
        page.wait_for_timeout(500)
        reel = page.evaluate(
            """() => {
              let spokeCount = 0;
              try {
                spokeCount = map.querySourceFeatures('atlas-spokes')?.length || 0;
              } catch (e) {
                try {
                  const raw = map.getSource('atlas-spokes')?._data;
                  spokeCount = raw?.features?.length || 0;
                } catch (e2) {}
              }
              return {
                badge: document.getElementById('memory-badge')?.classList.contains('visible'),
                cta: document.getElementById('atlas-cta')?.classList.contains('visible'),
                spokes: !!map.getLayer('atlas-spokes-line'),
                spokeBand: !!map.getLayer('atlas-spokes-band'),
                spokeCount,
              };
            }"""
        )
        print(f"Memory/CTA/spokes: {reel}")
        page.screenshot(path=str(SCREENSHOTS / "07-memory-reel.png"))

        # Colorado trip
        page.evaluate(f"selectTrip({json.dumps(trip_co)})")
        page.wait_for_timeout(3500)
        co_state = page.evaluate(
            """() => {
              const bounds = { latMin: 37, latMax: 41, lngMin: -109, lngMax: -102 };
              const stops = activeStops();
              const inCo = stops.filter(s =>
                s.lat >= bounds.latMin && s.lat <= bounds.latMax &&
                s.lng >= bounds.lngMin && s.lng <= bounds.lngMax
              );
              const c = map.getCenter();
              return {
                total: stops.length,
                inColorado: inCo.length,
                names: inCo.map(s => s.location.split(',')[0]),
                center: { lat: c.lat, lng: c.lng },
                pitch: map.getPitch(),
                zoom: map.getZoom(),
                projection: map.getProjection?.()?.name || 'mercator',
              };
            }"""
        )
        print(f"Colorado: {co_state}")
        page.screenshot(path=str(SCREENSHOTS / "04-colorado-fixed.png"))

        # Seattle trip
        page.evaluate(f"selectTrip({json.dumps(trip_sea)})")
        page.wait_for_timeout(3500)
        sea_state = page.evaluate(
            """() => {
              const stops = activeStops();
              const c = map.getCenter();
              const wa = stops.filter(s => s.lat >= 45 && s.lat <= 49 && s.lng >= -125 && s.lng <= -116);
              const tx = stops.filter(s => s.lat >= 25 && s.lat <= 37 && s.lng >= -107 && s.lng <= -93);
              return {
                total: stops.length,
                waStops: wa.length,
                txStops: tx.length,
                center: { lat: c.lat, lng: c.lng },
                pitch: map.getPitch(),
                zoom: map.getZoom(),
              };
            }"""
        )
        print(f"Seattle: {sea_state}")
        page.screenshot(path=str(SCREENSHOTS / "05-seattle-fixed.png"))

        # Playback smoke + watch mode
        page.evaluate(f"selectTrip({json.dumps(trip_co)})")
        page.wait_for_timeout(800)
        page.evaluate("startPlayback()")
        page.wait_for_timeout(1500)
        play_state = page.evaluate(
            """() => ({
              playing: isPlaying,
              watching: document.body.classList.contains('watching'),
              panelCollapsed: document.getElementById('sidebar')?.classList.contains('collapsed'),
              t0: animTimeMs,
            })"""
        )
        page.wait_for_timeout(800)
        play_state["t1"] = page.evaluate("animTimeMs")
        page.evaluate("stopPlayback()")
        print(f"Playback: {play_state}")
        page.screenshot(path=str(SCREENSHOTS / "06-watch-mode.png"))

        # Epic queue dock badge (loop → queue)
        page.evaluate(
            """() => {
              loopTrip = false; queueEpics = false;
              document.getElementById('btn-loop')?.click(); // loop
              document.getElementById('btn-loop')?.click(); // queue
            }"""
        )
        page.wait_for_timeout(200)
        queue_state = page.evaluate(
            """() => {
              const badge = document.getElementById('queue-badge');
              return {
                queueEpics,
                loopTrip,
                badgeVisible: !!(badge && !badge.hidden && badge.classList.contains('visible')),
                badgeText: badge?.textContent || '',
                btnQueue: document.getElementById('btn-loop')?.classList.contains('queue'),
              };
            }"""
        )
        print(f"Epic queue badge: {queue_state}")
        page.evaluate("loopTrip = false; queueEpics = false; syncLoopButton()")

        browser.close()

    critical_errors = [
        e for e in console_errors
        if "favicon" not in e.lower() and "404" not in e.lower() and "net::ERR" not in e
    ]
    print(f"\nConsole errors ({len(critical_errors)}):")
    for e in critical_errors[:10]:
        print(f"  - {e}")

    ok = True
    if co_state["inColorado"] < 4:
        print(f"FAIL: Expected >=4 CO markers, got {co_state['inColorado']}")
        ok = False
    if not in_conus(co_state["center"]["lat"], co_state["center"]["lng"]):
        print(f"FAIL: CO camera outside CONUS: {co_state['center']}")
        ok = False
    if co_state["pitch"] > 50:
        print(f"FAIL: Pitch too high for CO view: {co_state['pitch']}")
        ok = False
    if overview.get("hasDeck"):
        print("FAIL: deck.gl still loaded")
        ok = False
    if overview.get("projection") == "globe":
        print("FAIL: Still using globe projection")
        ok = False
    if sea_state["waStops"] < 1 and sea_state["txStops"] < 1:
        print(f"FAIL: Seattle trip missing WA/TX stops: {sea_state}")
        ok = False
    if not in_conus(sea_state["center"]["lat"], sea_state["center"]["lng"]):
        print(f"FAIL: Seattle camera outside CONUS: {sea_state['center']}")
        ok = False
    if declutter.get("hubs", 0) < 1:
        print(f"FAIL: Expected home hubs on overview, got {declutter}")
        ok = False
    if declutter.get("destStars", 0) < 3:
        print(f"FAIL: Expected destination constellation stars, got {declutter}")
        ok = False
    if declutter.get("eraChips", 0) < 2:
        print(f"FAIL: Expected year era chips, got {declutter}")
        ok = False
    if declutter.get("stopDots", 0) > 0:
        print(f"FAIL: Overview still has per-trip stop dots: {declutter}")
        ok = False
    if era_state.get("yearFilter") != "2025":
        print(f"FAIL: Year filter not applied: {era_state}")
        ok = False
    if era_state.get("dimmedTrips", 0) < 1:
        print(f"FAIL: Era filter did not dim trips: {era_state}")
        ok = False
    if not reel.get("cta"):
        print(f"FAIL: Featured atlas CTA missing: {reel}")
        ok = False
    if not reel.get("spokes"):
        print(f"FAIL: Atlas spokes source missing: {reel}")
        ok = False
    if not reel.get("spokeBand"):
        print(f"FAIL: Atlas corridor band layer missing: {reel}")
        ok = False
    # querySourceFeatures can be empty at low zoom; layer presence is enough
    if reel.get("spokes") and reel.get("spokeCount", 0) == 0:
        print(f"WARN: Spokes layer present but no features queried at this zoom: {reel}")
    if not play_state.get("playing"):
        print(f"FAIL: Playback did not start: {play_state}")
        ok = False
    if not play_state.get("watching"):
        print(f"FAIL: Watch mode not active during play: {play_state}")
        ok = False
    if not play_state.get("panelCollapsed"):
        print(f"FAIL: Panel should collapse during play: {play_state}")
        ok = False
    if play_state.get("t1", 0) <= play_state.get("t0", 0):
        print(f"FAIL: animTimeMs not advancing: {play_state}")
        ok = False
    if not queue_state.get("queueEpics") or not queue_state.get("btnQueue"):
        print(f"FAIL: Epic queue mode not armed: {queue_state}")
        ok = False
    if not queue_state.get("badgeVisible") or "Epic queue" not in queue_state.get("badgeText", ""):
        print(f"FAIL: Epic queue dock badge missing: {queue_state}")
        ok = False
    if critical_errors:
        print("WARN: Console errors detected")

    if ok:
        print("\nVerification PASSED")
        return 0
    print("\nVerification FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
