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
    trip_photos = load_trip_id("2025-09-25_Frisco")  # Colorado album memories (Warren Pkwy)
    photo_clock: dict = {}
    photo_pins: dict = {}

    trips_data = json.loads(TRIPS_FILE.read_text(encoding="utf-8"))
    sep_co = next(
        (
            t for t in trips_data["trips"]
            if "2025-09-25" in t.get("id", "") and t.get("has_colorado")
        ),
        None,
    )
    if not sep_co:
        print("FAIL: Sep 2025 Colorado trip missing from trips.json")
        return 1
    sep_end = str(sep_co.get("end") or "")
    sep_dest = (sep_co.get("dest_label") or "").lower()
    sep_origin = (sep_co.get("origin_label") or "").lower()
    sep_first = (sep_co.get("stops") or [{}])[0].get("location") or ""
    sep_last = (sep_co.get("stops") or [{}])[-1].get("location") or ""
    if not sep_end.startswith("2025-09-29"):
        print(f"FAIL: Sep Colorado trip should end Sep 29, got {sep_end}")
        return 1
    if "frisco" not in sep_dest and "Frisco" not in sep_last:
        print(f"FAIL: Sep Colorado dest should be Frisco, got dest={sep_co.get('dest_label')} last={sep_last}")
        return 1
    if "warren" not in sep_last.lower() or "warren" not in sep_first.lower():
        print(f"FAIL: Sep Colorado should start/end at Warren Parkway, got first={sep_first!r} last={sep_last!r}")
        return 1
    if "frisco" not in sep_origin and "frisco" not in sep_first.lower():
        print(f"FAIL: Sep Colorado origin should be Frisco, got origin={sep_co.get('origin_label')} first={sep_first}")
        return 1
    if any(str(s.get("datetime") or "").startswith("2025-10-02") for s in sep_co.get("stops") or []):
        print("FAIL: Sep Colorado trip should not include the Oct 2 home charge")
        return 1
    print(f"Sep Colorado trip: {sep_co['id']} → {sep_co.get('dest_label')} end={sep_end[:10]}")

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

        # Featured CTA + spokes (memory reel removed)
        page.evaluate("localStorage.setItem('mymilediary-onboarded','1')")
        page.wait_for_timeout(500)
        reel = page.evaluate(
            """() => {
              let spokeCount = 0;
              try {
                const raw = map.getSource('atlas-spokes')?._data;
                const feats = raw?.features || [];
                spokeCount = feats.length || map.querySourceFeatures('atlas-spokes')?.length || 0;
              } catch (e) {
                try {
                  spokeCount = map.querySourceFeatures('atlas-spokes')?.length || 0;
                } catch (e2) {}
              }
              const brand = document.querySelector('.brand')?.textContent || '';
              return {
                badge: !!document.getElementById('memory-badge'),
                cta: document.getElementById('atlas-cta')?.classList.contains('visible'),
                spokes: !!map.getLayer('atlas-spokes-line'),
                spokeBand: !!map.getLayer('atlas-spokes-band'),
                spokeCount,
                brand,
                photosStat: (DASHBOARD.total_photos || 0),
                noKwh: !document.body.innerText.includes('kWh'),
              };
            }"""
        )
        print(f"CTA/spokes/brand: {reel}")
        page.screenshot(path=str(SCREENSHOTS / "02-featured-cta.png"))
        page.screenshot(path=str(SCREENSHOTS / "07-photo-memories.png"))

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
        page.evaluate("animTimeMs = 0; startPlayback()")
        page.wait_for_timeout(400)
        play_state = page.evaluate(
            """() => {
              const st = typeof positionAtTime === 'function' ? positionAtTime(animTimeMs) : null;
              const clock = document.getElementById('trip-clock');
              return {
                playing: isPlaying,
                watching: document.body.classList.contains('watching'),
                panelCollapsed: document.getElementById('sidebar')?.classList.contains('collapsed'),
                t0: animTimeMs,
                phase: st?.phase || '',
                hasLocationHud: !!document.getElementById('location-hud'),
                clockVisible: !!clock?.classList.contains('visible'),
                clockText: (clock?.textContent || '').trim(),
                playToggle: document.getElementById('btn-play')?.classList.contains('is-playing'),
                pauseGone: !document.getElementById('btn-pause'),
              };
            }"""
        )
        page.wait_for_timeout(800)
        play_state["t1"] = page.evaluate("animTimeMs")
        cinema_ux = page.evaluate(
            """() => {
              const pb = activePlayback();
              const dwells = (pb?.segments || []).filter(s => s.type === 'dwell');
              const avgDwell = dwells.length
                ? dwells.reduce((a, s) => a + (s.duration_ms || 0), 0) / dwells.length
                : 0;
              const maxDwell = dwells.length
                ? Math.max(...dwells.map(s => s.duration_ms || 0))
                : 0;
              const mems = (pb?.segments || []).filter(s => s.type === 'memory');
              const maxMemory = mems.length
                ? Math.max(...mems.map(s => s.duration_ms || 0))
                : 0;
              // Sample dwell chrome — must not show %
              let dwellPct = false;
              const dSeg = dwells[0];
              if (dSeg) {
                let cursor = 0;
                for (const seg of pb.segments) {
                  if (seg === dSeg) break;
                  cursor += seg.duration_ms || 0;
                }
                updateTrailFromState(positionAtTime(cursor + (dSeg.duration_ms || 0) * 0.5));
                const sub = document.getElementById('prog-end')?.textContent
                  || document.querySelector('.dwell-pct')?.textContent
                  || document.getElementById('prog-text')?.textContent
                  || '';
                dwellPct = /\\d+%/.test(sub);
              }
              // Night from overnight clock (local wall time at playhead lng)
              let nightOk = null;
              let nightFactor = null;
              const nightSeg = (pb?.segments || []).find(s => {
                if (!s.clock_start) return false;
                const d = new Date(s.clock_start);
                const lng = s.lng != null ? s.lng : (s.path?.[0]?.[1]);
                const tz = (typeof tripTzFromLng === 'function')
                  ? tripTzFromLng(lng)
                  : 'America/Chicago';
                try {
                  const parts = new Intl.DateTimeFormat('en-US', {
                    timeZone: tz, hour: 'numeric', hourCycle: 'h23',
                  }).formatToParts(d);
                  const h = parseInt(parts.find(p => p.type === 'hour')?.value || '12', 10);
                  return h >= 21 || h < 5;
                } catch (_) {
                  const h = d.getUTCHours();
                  return h >= 20 || h < 5;
                }
              });
              if (nightSeg) {
                let cursor = 0;
                for (const seg of pb.segments) {
                  if (seg === nightSeg) break;
                  cursor += seg.duration_ms || 0;
                }
                updateTrailFromState(positionAtTime(cursor + 10));
                nightOk = document.body.classList.contains('night');
                nightFactor = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--night') || '0');
              }
              // Photo memory clock checked on the album trip separately
              return {
                avgDwell,
                maxDwell,
                maxMemory,
                dwellPct,
                nightOk,
                nightFactor,
                hasClockFields: !!(pb?.segments || []).some(s => s.clock_start),
                introMs: (pb?.segments || []).find(s => s.type === 'intro')?.duration_ms || 0,
              };
            }"""
        )
        print(f"Cinema UX: {cinema_ux}")
        story_state = page.evaluate(
            """() => {
              const ov = document.getElementById('story-overlay');
              return {
                visible: ov?.classList.contains('visible'),
                dwell: ov?.classList.contains('dwell'),
                travel: ov?.classList.contains('travel'),
                caption: document.getElementById('story-caption')?.textContent || '',
                modeOk: !!(ov && (ov.classList.contains('dwell') || ov.classList.contains('travel')
                  || ov.classList.contains('visible') === false || true)),
              };
            }"""
        )
        # Force a mid-travel + dwell sample via positionAtTime helpers if available
        story_pacing = page.evaluate(
            """() => {
              const pb = activePlayback();
              if (!pb?.segments?.length) return { ok: false };
              let cursor = 0;
              let travelAt = null, dwellAt = null;
              for (const seg of pb.segments) {
                if (seg.type === 'travel' && travelAt == null) {
                  travelAt = { start: cursor, dur: seg.duration_ms || 0 };
                }
                if (seg.type === 'dwell' && dwellAt == null) {
                  dwellAt = { start: cursor, dur: seg.duration_ms || 0 };
                }
                cursor += seg.duration_ms || 0;
              }
              const samples = {};
              if (travelAt && travelAt.dur > 0) {
                updateTrailFromState(positionAtTime(travelAt.start + travelAt.dur * 0.45));
                samples.midTravelVisible = document.getElementById('story-overlay')?.classList.contains('visible');
                updateTrailFromState(positionAtTime(travelAt.start + travelAt.dur * 0.04));
                samples.earlyTravelVisible = document.getElementById('story-overlay')?.classList.contains('visible');
                samples.earlyTravelMode = document.getElementById('story-overlay')?.classList.contains('travel');
              }
              if (dwellAt && dwellAt.dur > 0) {
                updateTrailFromState(positionAtTime(dwellAt.start + dwellAt.dur * 0.05));
                samples.earlyDwellPois = (document.getElementById('story-pois')?.children.length || 0);
                updateTrailFromState(positionAtTime(dwellAt.start + dwellAt.dur * 0.45));
                samples.lateDwellPois = (document.getElementById('story-pois')?.children.length || 0);
                samples.lateDwellMode = document.getElementById('story-overlay')?.classList.contains('dwell');
              }
              return { ok: true, ...samples };
            }"""
        )
        print(f"Story overlay: {story_state}")
        print(f"Caption pacing: {story_pacing}")
        print(f"Playback: {play_state}")
        page.screenshot(path=str(SCREENSHOTS / "06-watch-mode.png"))
        page.evaluate("stopPlayback()")

        # Photo trip: live clock + memory caption must share capture instant
        print(f"Checking photo clock on {trip_photos} …")
        page.evaluate(f"selectTrip({json.dumps(trip_photos)})")
        page.wait_for_timeout(400)
        page.evaluate("isPlaying = true; document.body.classList.add('watching')")
        photo_clock = page.evaluate(
            """() => {
              const pb = activePlayback();
              const memSeg = (pb?.segments || []).find(s => s.type === 'memory' && (s.datetime || s.clock_start));
              if (!memSeg || typeof formatLocalClock !== 'function') {
                return { ok: false, reason: 'no-memory' };
              }
              let cursor = 0;
              for (const seg of pb.segments) {
                if (seg === memSeg) break;
                cursor += seg.duration_ms || 0;
              }
              updateTrailFromState(positionAtTime(cursor + Math.min(200, (memSeg.duration_ms || 400) * 0.2)));
              const clockText = (document.getElementById('trip-clock')?.textContent || '').trim();
              const metaText = (document.getElementById('memory-stage-meta')?.textContent || '').trim();
              const dt = new Date(memSeg.datetime || memSeg.clock_start);
              const lng = memSeg.photo_lng ?? memSeg.lng;
              const expectHud = formatLocalClock(dt, lng, 'hud');
              const expectMed = formatLocalClock(dt, lng, 'medium');
              const hen = (pb?.segments || []).find(s =>
                s.type === 'dwell' && /Henrietta/i.test(s.label || '') && s.clock_start);
              let nightAtHenrietta = null;
              if (hen) {
                let c = 0;
                for (const seg of pb.segments) {
                  if (seg === hen) break;
                  c += seg.duration_ms || 0;
                }
                updateTrailFromState(positionAtTime(c + 20));
                nightAtHenrietta = {
                  night: document.body.classList.contains('night'),
                  factor: parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--night') || '0'),
                  clock: (document.getElementById('trip-clock')?.textContent || '').trim(),
                };
              }
              const mems = (pb?.segments || []).filter(s => s.type === 'memory');
              const maxMemory = mems.length
                ? Math.max(...mems.map(s => s.duration_ms || 0))
                : 0;
              const walks = (pb?.segments || []).filter(s => s.type === 'travel' && s.profile === 'walking');
              // Seek first walking travel and confirm hike marker swaps in
              let hikeMarker = false;
              let hikeDock = false;
              let memoryKeepsHike = false;
              if (walks.length) {
                let c = 0;
                for (const seg of (pb?.segments || [])) {
                  if (seg.type === 'travel' && seg.profile === 'walking') {
                    updateTrailFromState(positionAtTime(c + Math.min(400, (seg.duration_ms || 800) * 0.35)));
                    hikeMarker = !!document.querySelector('.vehicle-wrap.hike, .vehicle-wrap[data-mode="walking"]');
                    const dock = (document.getElementById('prog-text')?.textContent || '');
                    hikeDock = /on foot/i.test(dock);
                    // Advance into the next memory (if any) — icon must stay on foot
                    let c2 = c + (seg.duration_ms || 0);
                    for (const seg2 of (pb?.segments || []).slice((pb?.segments || []).indexOf(seg) + 1)) {
                      if (seg2.type === 'memory') {
                        updateTrailFromState(positionAtTime(c2 + Math.min(200, (seg2.duration_ms || 400) * 0.3)));
                        memoryKeepsHike = (seg2.profile === 'walking')
                          && !!document.querySelector('.vehicle-wrap.hike, .vehicle-wrap[data-mode="walking"]');
                        break;
                      }
                      if (seg2.type === 'travel' && seg2.profile !== 'walking') break;
                      c2 += seg2.duration_ms || 0;
                    }
                    break;
                  }
                  c += seg.duration_ms || 0;
                }
              }
              // Path backtrack heuristic: no travel leg should reverse >2.5mi as the crow flies
              // relative to its own start→end while covering much more along-path (spur ok).
              let badBacktrack = 0;
              for (const seg of (pb?.segments || [])) {
                if (seg.type !== 'travel' || !seg.path || seg.path.length < 2) continue;
                const a = seg.path[0], b = seg.path[seg.path.length - 1];
                const crow = havMi(a[0], a[1], b[0], b[1]);
                let along = 0;
                for (let i = 1; i < seg.path.length; i++) {
                  along += havMi(seg.path[i-1][0], seg.path[i-1][1], seg.path[i][0], seg.path[i][1]);
                }
                // Extreme hairpin spur on same corridor: along >> crow is OK for trails;
                // flag only absurd teleports where crow is tiny but along is huge AND ends near start.
                if (crow < 0.15 && along > 4.0) badBacktrack += 1;
              }
              function havMi(a, b, c, d) {
                const R = 3958.8, p = Math.PI / 180;
                const dlat = (c - a) * p, dlng = (d - b) * p;
                const x = Math.sin(dlat/2)**2 + Math.cos(a*p)*Math.cos(c*p)*Math.sin(dlng/2)**2;
                return 2 * R * Math.asin(Math.sqrt(x));
              }
              return {
                ok: true,
                photoId: memSeg.photo_id || '',
                clockText, metaText, expectHud, expectMed,
                clockOk: clockText === expectHud,
                metaOk: metaText === expectMed,
                datetime: memSeg.datetime || memSeg.clock_start,
                nightAtHenrietta,
                maxMemory,
                memoryCount: mems.length,
                walkCount: walks.length,
                hikeMarker,
                hikeDock,
                memoryKeepsHike,
                badBacktrack,
                hasHikeHtml: typeof hikeHtml === 'string' && hikeHtml.includes('hike'),
              };
            }"""
        )
        print(f"Photo clock sync: {photo_clock}", flush=True)
        # Photo pins must sit at real GPS (Colorado), not stacked off-route
        photo_pins = page.evaluate(
            """() => {
              const trip = activeTrip();
              const photos = trip?.photos || [];
              const markers = (typeof photoMarkers !== 'undefined' ? photoMarkers : []) || [];
              const lngLats = markers.map(m => {
                try { return m.getLngLat(); } catch (_) { return null; }
              }).filter(Boolean);
              const co = lngLats.filter(ll =>
                ll.lat >= 37 && ll.lat <= 41 && ll.lng >= -109 && ll.lng <= -102);
              const south = lngLats.filter(ll => ll.lat < 32);
              // Path must route TO exact EXIF pins (not snap pins onto highway)
              const path = trip?.route_path || [];
              function havMi(a, b, c, d) {
                const R = 3958.8, p = Math.PI / 180;
                const dlat = (c - a) * p, dlng = (d - b) * p;
                const x = Math.sin(dlat/2)**2 + Math.cos(a*p)*Math.cos(c*p)*Math.sin(dlng/2)**2;
                return 2 * R * Math.asin(Math.sqrt(x));
              }
              let maxOff = 0;
              const step = Math.max(1, Math.floor(path.length / 1200));
              lngLats.forEach(ll => {
                let best = 9999;
                for (let i = 0; i < path.length; i += step) {
                  const pt = path[i];
                  const d = havMi(ll.lat, ll.lng, pt[0], pt[1]);
                  if (d < best) best = d;
                }
                if (path.length) {
                  const pt = path[path.length - 1];
                  const d = havMi(ll.lat, ll.lng, pt[0], pt[1]);
                  if (d < best) best = d;
                }
                if (best > maxOff) maxOff = best;
              });
              // Marker geo must match EXIF payload (never corridor-snapped)
              let exifMismatch = 0;
              markers.forEach(m => {
                const photo = m._photo;
                if (!photo) return;
                try {
                  const ll = m.getLngLat();
                  if (Math.abs(ll.lat - Number(photo.lat)) > 1e-5 ||
                      Math.abs(ll.lng - Number(photo.lng)) > 1e-5) {
                    exifMismatch += 1;
                  }
                } catch (_) {}
              });
              // Marker root must stay position:absolute (Mapbox) — never relative
              const pin = document.querySelector('.photo-pin.mapboxgl-marker')
                || document.querySelector('.photo-pin');
              const tr = pin ? getComputedStyle(pin).transitionProperty : '';
              const pos = pin ? getComputedStyle(pin).position : '';
              const hasInner = !!document.querySelector('.photo-pin-inner');
              let maxPixelDrift = 0;
              markers.forEach(m => {
                try {
                  const ll = m.getLngLat();
                  const proj = map.project([ll.lng, ll.lat]);
                  if (!Number.isFinite(proj.x) || !Number.isFinite(proj.y)) return;
                  const rect = m.getElement().getBoundingClientRect();
                  const dx = (rect.left + rect.width / 2) - proj.x;
                  const dy = (rect.top + rect.height / 2) - proj.y;
                  if (!Number.isFinite(dx) || !Number.isFinite(dy)) return;
                  maxPixelDrift = Math.max(maxPixelDrift, Math.hypot(dx, dy));
                } catch (_) {}
              });
              return {
                photoCount: photos.length,
                markerCount: lngLats.length,
                inColorado: co.length,
                southOf32: south.length,
                maxOffRouteMi: Math.round(maxOff * 100) / 100,
                exifMismatch,
                markerPosition: pos,
                maxPixelDrift: Math.round(maxPixelDrift * 10) / 10,
                transformSafe: hasInner && !/transform/i.test(tr || '') && pos === 'absolute',
              };
            }"""
        )
        print(f"Photo pin geo: {photo_pins}", flush=True)
        page.evaluate("isPlaying = false; stopPlayback()")
        page.evaluate(f"selectTrip({json.dumps(trip_co)})")
        page.wait_for_timeout(300)

        # Floating play + continuous speed rail (bottom-right, not pills)
        dock_state = page.evaluate(
            """() => ({
              play: !!document.getElementById('btn-play'),
              pauseGone: !document.getElementById('btn-pause'),
              rewind: !!document.getElementById('btn-rewind'),
              forward: !!document.getElementById('btn-forward'),
              seekFn: typeof seekPlayback === 'function',
              rewindWorks: (() => {
                if (typeof seekPlayback !== 'function' || typeof positionAtTime !== 'function') return false;
                const pb = (typeof activePlayback === 'function') ? activePlayback() : null;
                if (!pb?.total_video_ms) return false;
                const wasPlaying = !!isPlaying;
                isPlaying = false;
                animTimeMs = Math.min(pb.total_video_ms * 0.4, 12_000);
                const before = animTimeMs;
                seekPlayback(-1);
                const after = animTimeMs;
                const dropped = after < before - 500;
                seekPlayback(1);
                const restored = animTimeMs > after + 500;
                isPlaying = wasPlaying;
                return dropped && restored;
              })(),
              toggleWorks: typeof syncPlayToggle === 'function',
              speed: !!document.getElementById('speed'),
              speedPills: document.querySelectorAll('.speed-pill').length,
              speedRail: (() => {
                const el = document.getElementById('speed-rail');
                if (!el) return false;
                const s = getComputedStyle(el);
                return s.display !== 'none' && s.visibility !== 'hidden';
              })(),
              speedControlOn: (() => {
                const el = document.getElementById('speed-rail') || document.getElementById('speed-control');
                if (!el) return false;
                const s = getComputedStyle(el);
                return s.display !== 'none' && s.visibility !== 'hidden';
              })(),
              speedRailRight: (() => {
                const el = document.getElementById('speed-rail');
                if (!el) return false;
                const r = el.getBoundingClientRect();
                return r.left > window.innerWidth * 0.55;
              })(),
              playCentered: (() => {
                const el = document.getElementById('btn-play');
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const mid = (r.left + r.right) / 2;
                return Math.abs(mid - window.innerWidth / 2) < 80;
              })(),
              equalPace: typeof clockPlayPace === 'function' && Math.abs(clockPlayPace(2) - 1) < 0.001
                && Math.abs(clockPlayPace(8) - 1) < 0.001 && Math.abs(clockPlayPace(14) - 1) < 0.001,
              scrubber: !!document.getElementById('scrubber'),
              dockBoxGone: (() => {
                const dock = document.getElementById('transport-dock');
                if (!dock) return false;
                const s = getComputedStyle(dock);
                return s.backgroundColor === 'rgba(0, 0, 0, 0)' || s.backgroundColor === 'transparent';
              })(),
              exportGone: !document.getElementById('btn-export'),
              shareGone: !document.getElementById('btn-share'),
              loopGone: !document.getElementById('btn-loop'),
              legendGone: !document.getElementById('map-legend'),
              locationHudGone: !document.getElementById('location-hud'),
              tripClock: !!document.getElementById('trip-clock'),
              memoryStage: !!document.getElementById('memory-stage'),
              defaultSpeed: parseFloat(document.getElementById('speed')?.value || '0'),
              speedMin: parseFloat(document.getElementById('speed')?.min || '0'),
              speedMax: parseFloat(document.getElementById('speed')?.max || '0'),
              mapGestures: (() => {
                if (!map) return false;
                try {
                  return !!(map.dragPan?.isEnabled?.()
                    && map.scrollZoom?.isEnabled?.()
                    && map.touchZoomRotate?.isEnabled?.());
                } catch (_) { return false; }
              })(),
              navCtrlLive: (() => {
                document.body.classList.add('watching');
                const el = document.querySelector('.mapboxgl-ctrl-group');
                if (!el) {
                  document.body.classList.remove('watching');
                  return false;
                }
                const s = getComputedStyle(el);
                const ok = s.pointerEvents !== 'none' && parseFloat(s.opacity || '0') > 0.2;
                document.body.classList.remove('watching');
                return ok;
              })(),
              userExploreOk: (() => {
                if (typeof noteUserCameraInteraction !== 'function') return false;
                if (typeof applyPlaybackCamera !== 'function') return false;
                if (typeof clearUserCameraControl !== 'function') return false;
                const wasPlaying = !!isPlaying;
                isPlaying = true;
                document.body.classList.add('watching');
                clearUserCameraControl();
                noteUserCameraInteraction();
                const armed = !!userCameraControl;
                // While exploring, non-forced chase must no-op
                const z0 = map.getZoom();
                map.jumpTo({ zoom: Math.max(2, z0 - 0.35) });
                const warm = positionAtTime(Math.max(animTimeMs, 1500)) || { phase: 'travel', lat: 39, lng: -105, bearing: 0 };
                applyPlaybackCamera(warm, false);
                const stayed = Math.abs(map.getZoom() - (z0 - 0.35)) < 0.2 || !!userCameraControl;
                clearUserCameraControl();
                isPlaying = wasPlaying;
                document.body.classList.remove('watching');
                return armed && stayed && !userCameraControl;
              })(),
              userExploreTouchOk: (() => {
                if (typeof bindMapExploreDuringPlayback !== 'function') return true;
                if (typeof clearUserCameraControl !== 'function') return false;
                const wasPlaying = !!isPlaying;
                isPlaying = true;
                document.body.classList.add('watching');
                clearUserCameraControl();
                const container = map.getCanvasContainer?.() || map.getContainer?.();
                if (!container) {
                  isPlaying = wasPlaying;
                  document.body.classList.remove('watching');
                  return false;
                }
                container.dispatchEvent(new TouchEvent('touchstart', { bubbles: true, cancelable: true, touches: [] }));
                const armed = !!userCameraControl;
                clearUserCameraControl();
                isPlaying = wasPlaying;
                document.body.classList.remove('watching');
                return armed;
              })(),
            })"""
        )
        print(f"Play control: {dock_state}")
        queue_state = dock_state  # keep name for older assertion block compatibility below

        # Mobile sheet regression (iPhone-ish) — default browse state, no forced sheet mode
        page.set_viewport_size({"width": 390, "height": 844})
        page.evaluate("selectTrip('all'); setPanelCollapsed(false); ensureMobileBrowseSheet();")
        page.wait_for_timeout(500)
        mobile_default = page.evaluate(
            """() => {
              const panel = document.getElementById('sidebar');
              const pr = panel?.getBoundingClientRect();
              const rail = document.getElementById('mobile-dest-rail');
              return {
                panelHalf: panel?.classList.contains('sheet-half'),
                panelPeek: panel?.classList.contains('sheet-peek'),
                panelMaxH: pr?.height,
                mapVisiblePx: Math.max(0, (pr?.top || window.innerHeight) - 56),
                destRailVisible: !!(rail && !rail.hidden && rail.querySelector('.mobile-dest-chip')),
                destChips: rail?.querySelectorAll('.mobile-dest-chip').length || 0,
              };
            }"""
        )
        dest_chip_pick = page.evaluate(
            """() => {
              selectTrip('all');
              setSheetState('peek');
              const chip = document.querySelector('.mobile-dest-chip');
              if (!chip) return { hasChip: false, picked: false };
              const before = selectedId;
              chip.click();
              return { hasChip: true, picked: selectedId !== before && selectedId !== 'all', id: selectedId };
            }"""
        )
        page.evaluate("selectTrip('all'); setSheetState('full');")
        page.wait_for_timeout(350)
        mobile = page.evaluate(
            """() => {
              const panel = document.getElementById('sidebar');
              const dock = document.getElementById('transport-dock');
              const toggle = document.getElementById('sidebar-toggle');
              const era = document.getElementById('era-rail');
              const timeline = document.querySelector('.timeline-bar');
              const speed = document.getElementById('speed-rail');
              const list = document.getElementById('trip-list');
              const pr = panel?.getBoundingClientRect();
              const dr = dock?.getBoundingClientRect();
              const tr = toggle?.getBoundingClientRect();
              const tlr = timeline?.getBoundingClientRect();
              const sr = speed?.getBoundingClientRect();
              const lr = list?.getBoundingClientRect();
              const overlaps = (a, b) => !!(a && b && a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top);
              const forward = document.getElementById('btn-forward');
              const fr = forward?.getBoundingClientRect();
              const items = Array.from(document.querySelectorAll('#trip-list .trip-item'));
              const visibleTrips = items.filter(el => {
                if (getComputedStyle(el).display === 'none') return false;
                const r = el.getBoundingClientRect();
                return r.height > 0 && r.bottom > pr.top + 12 && r.top < pr.bottom - 8;
              }).length;
              const togglePanelOverlap = !!(tr && pr && tr.left < pr.right && tr.right > pr.left && tr.top < pr.bottom && tr.bottom > pr.top);
              const scrollBefore = list?.scrollTop || 0;
              if (list) list.scrollTop = list.scrollHeight;
              const scrollAfter = list?.scrollTop || 0;
              const lastItem = items[items.length - 1];
              const lastR = lastItem?.getBoundingClientRect();
              return {
                panelBottomSheet: pr.bottom > window.innerHeight * 0.55 && pr.top > window.innerHeight * 0.18,
                panelGrab: !!document.getElementById('panel-grab') && getComputedStyle(document.getElementById('panel-grab')).display !== 'none',
                panelPeek: panel?.classList.contains('sheet-peek'),
                panelHalf: panel?.classList.contains('sheet-half'),
                panelFull: panel?.classList.contains('sheet-full'),
                panelMaxH: pr.height,
                panelHeightOk: pr.height >= window.innerHeight * 0.38,
                mapVisibleRatio: Math.max(0, (pr.top - 56) / window.innerHeight),
                listClientH: list?.clientHeight || 0,
                tripListVisible: visibleTrips >= 1,
                listScrollable: list ? list.scrollHeight > list.clientHeight + 4 : false,
                listScrollWorked: scrollAfter > scrollBefore + 20,
                lastTripReachable: !!(lastR && lr && lastR.top >= lr.top && lastR.bottom <= lr.bottom + 2),
                dockTimelineOverlap: overlaps(dr, tlr),
                dockTimelineGap: tlr && dr ? tlr.top - dr.bottom : null,
                speedHiddenOnAtlas: speed ? getComputedStyle(speed).display === 'none' : true,
                dockWidth: dr.width,
                dockInView: dr.bottom <= window.innerHeight + 2 && dr.top >= 0,
                toggleSize: Math.min(tr.width, tr.height),
                togglePanelOverlap,
                speedForwardOverlap: overlaps(fr, sr),
                eraScrollable: era ? era.scrollWidth >= era.clientWidth - 1 : false,
                eraChips: document.querySelectorAll('.era-chip').length,
                timelineInView: tlr ? (tlr.left >= -1 && tlr.right <= window.innerWidth + 1 && tlr.bottom <= window.innerHeight + 2) : false,
                chromeOverlap: overlaps(pr, dr) || overlaps(pr, tlr) || overlaps(pr, sr) || togglePanelOverlap,
                mobileChromeH: getComputedStyle(document.documentElement).getPropertyValue('--mobile-chrome-h').trim(),
                hubs: document.querySelectorAll('.home-hub').length,
                vw: window.innerWidth,
                vh: window.innerHeight,
              };
            }"""
        )
        print(f"Mobile default: {mobile_default}")
        print(f"Mobile dest chip: {dest_chip_pick}")
        print(f"Mobile 390x844: {mobile}")
        page.screenshot(path=str(SCREENSHOTS / "08-mobile-sheet.png"))
        page.evaluate(f"selectTrip({json.dumps(trip_co)})")
        page.wait_for_timeout(400)
        mobile_pick = page.evaluate(
            """() => ({
              panelCollapsed: document.getElementById('sidebar')?.classList.contains('collapsed'),
              tripFocus: document.body.classList.contains('trip-focus'),
              activeItems: document.querySelectorAll('#trip-list .trip-item.active').length,
            })"""
        )
        print(f"Mobile trip select: {mobile_pick}")
        page.evaluate("selectTrip('all'); startPlayback()")  # should no-op / ask select
        page.evaluate(f"selectTrip({json.dumps(trip_co)}); startPlayback()")
        page.wait_for_timeout(900)
        mobile_play = page.evaluate(
            """() => {
              const dock = document.getElementById('transport-dock');
              const speed = document.getElementById('speed-rail');
              const forward = document.getElementById('btn-forward');
              const dr = dock?.getBoundingClientRect();
              const sr = speed?.getBoundingClientRect();
              const fr = forward?.getBoundingClientRect();
              const overlaps = (a, b) => !!(a && b && a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top);
              return {
                playing: isPlaying,
                watching: document.body.classList.contains('watching'),
                panelCollapsed: document.getElementById('sidebar')?.classList.contains('collapsed'),
                panelOpenAfterSelect: !document.getElementById('sidebar')?.classList.contains('collapsed'),
                speedForwardOverlap: overlaps(fr, sr),
                speedAboveDock: sr && dr ? sr.bottom <= dr.top + 2 : false,
                pad: typeof mapCameraPadding === 'function' ? mapCameraPadding() : null,
              };
            }"""
        )
        page.evaluate("stopPlayback()")
        print(f"Mobile playback: {mobile_play}")

        # Sprint 4 — export cinema framing smoke (no MediaRecorder download)
        page.set_viewport_size({"width": 1440, "height": 900})
        page.evaluate(f"selectTrip({json.dumps(trip_co)})")
        page.wait_for_timeout(500)
        cinema = page.evaluate(
            """() => {
              const trip = activeTrip();
              document.body.classList.add('cinema-mode', 'portrait-export');
              if (typeof showExportTitleCard === 'function') showExportTitleCard(trip, 'intro');
              map.resize();
              const title = document.getElementById('cinema-title');
              const wrap = document.getElementById('map-wrap');
              const mapEl = document.getElementById('map');
              const tr = title?.getBoundingClientRect();
              const mr = mapEl?.getBoundingClientRect();
              const aspect = mr.width && mr.height ? mr.width / mr.height : 0;
              return {
                cinema: document.body.classList.contains('cinema-mode'),
                portrait: document.body.classList.contains('portrait-export'),
                titleVisible: title?.classList.contains('visible'),
                titleTop: tr?.top ?? -1,
                titleInUpper: (tr?.top ?? 9999) < window.innerHeight * 0.35,
                mapAspect: Math.round(aspect * 100) / 100,
                portraitish: aspect > 0 && aspect < 0.75,
                dockHidden: getComputedStyle(document.getElementById('transport-dock')).opacity === '0'
                  && (!document.getElementById('speed-rail')
                    || getComputedStyle(document.getElementById('speed-rail')).opacity === '0'),
                titleText: document.getElementById('cinema-title-text')?.textContent || '',
                statsText: document.getElementById('cinema-stats')?.innerText || '',
                statsHidden: !!document.getElementById('cinema-stats')?.hidden,
              };
            }"""
        )
        page.screenshot(path=str(SCREENSHOTS / "09-export-cinema.png"))
        page.evaluate(
            """() => {
              document.body.classList.remove('cinema-mode', 'portrait-export');
              if (typeof hideExportTitleCard === 'function') hideExportTitleCard();
              map.resize();
            }"""
        )
        print(f"Export cinema: {cinema}")

        # Share helpers remain available for optional use; dock no longer shows a share button
        share_state = page.evaluate(
            """() => {
              const btn = document.getElementById('btn-share');
              const trip = activeTrip();
              const blurb = typeof tripShareBlurb === 'function' ? tripShareBlurb(trip) : '';
              const dwellSegs = (trip?.playback?.segments || []).filter(s => s.type === 'dwell');
              const rich = dwellSegs.filter(s =>
                /Memory|Departing|Final stop|Stopped in/i.test(s.caption || '')
                || /Stop \\d|Homeward/i.test(s.subcaption || '')
              );
              const nearbyLeak = dwellSegs.some(s =>
                /nearby:|Passing through/i.test(s.caption || '')
                || /nearby/i.test(s.subcaption || '')
                || (s.pois || []).length > 0
              );
              return {
                btn: !!btn,
                enabled: btn ? !btn.disabled : false,
                hasBlurb: blurb.includes('Relive it:') && blurb.includes('mymilediary'),
                hasStoryShare: !!(trip?.story?.share_blurb),
                sampleCaption: dwellSegs[0]?.caption || '',
                dwellCount: dwellSegs.length,
                richCaptionCount: rich.length,
                nearbyLeak,
                hasNativeShareHelper: typeof shareTripBlurb === 'function',
                hasOfferShare: typeof offerShareAfterPlay === 'function',
                hasHideShare: typeof hideShareToast === 'function',
                toastClickable: (() => {
                  const t = document.getElementById('share-toast');
                  if (!t) return false;
                  t.classList.add('visible');
                  const pe = getComputedStyle(t).pointerEvents;
                  t.classList.remove('visible');
                  return pe === 'auto';
                })(),
                minimalDock: !document.getElementById('btn-export')
                  && !document.getElementById('btn-loop')
                  && !!document.getElementById('btn-play')
                  && !!document.getElementById('speed'),
                defaultSpeed: parseFloat(document.getElementById('speed')?.value || '0'),
              };
            }"""
        )
        print(f"Share blurb: {share_state}")

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
    if reel.get("badge"):
        print(f"FAIL: Memory reel badge should be removed: {reel}")
        ok = False
    if "Mile Diary" not in (reel.get("brand") or ""):
        print(f"FAIL: Brand should be My Mile Diary: {reel}")
        ok = False
    if not reel.get("noKwh"):
        print(f"FAIL: UI still shows kWh: {reel}")
        ok = False
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
    if play_state.get("phase") not in ("travel", "dwell", "memory"):
        print(f"FAIL: Play should skip intro and start in motion soon: {play_state}")
        ok = False
    if play_state.get("hasLocationHud"):
        print(f"FAIL: EN ROUTE location-hud should be removed: {play_state}")
        ok = False
    if not play_state.get("pauseGone") or not play_state.get("playToggle"):
        print(f"FAIL: Single play/pause toggle expected: {play_state}")
        ok = False
    if not play_state.get("clockVisible") or not play_state.get("clockText"):
        print(f"FAIL: Live trip clock should show during play: {play_state}")
        ok = False
    if cinema_ux.get("maxDwell", 9999) > 1600:
        print(f"FAIL: Dwell segments should be short (<=1.6s): {cinema_ux}")
        ok = False
    if cinema_ux.get("maxMemory", 0) > 2900:
        print(f"FAIL: Photo memory holds should stay readable (<=2.9s timeline / ~5.8s wall): {cinema_ux}")
        ok = False
    if cinema_ux.get("dwellPct"):
        print(f"FAIL: Dock must not show dwell percentage: {cinema_ux}")
        ok = False
    if cinema_ux.get("hasClockFields") is False:
        print(f"FAIL: Playback segments need clock_start for live time: {cinema_ux}")
        ok = False
    if cinema_ux.get("introMs", 9999) > 1000:
        print(f"FAIL: Intro should be short for faster start: {cinema_ux}")
        ok = False
    pcm = photo_clock if isinstance(photo_clock, dict) else {}
    if not pcm.get("ok"):
        print(f"FAIL: Photo album trip needs memory segments for clock sync: {pcm}")
        ok = False
    elif not pcm.get("clockOk") or not pcm.get("metaOk"):
        print(f"FAIL: Trip clock must match photo caption instant: {pcm}")
        ok = False
    pcm_mem = float(pcm.get("maxMemory") or 0)
    if pcm.get("ok") and pcm_mem > 2900:
        print(f"FAIL: Photo album memory holds should stay bounded: {pcm}")
        ok = False
    if pcm.get("ok") and pcm.get("memoryCount", 0) > 0 and pcm_mem < 2200:
        print(f"FAIL: Photo memory holds too brief to read: {pcm}")
        ok = False
    if pcm.get("ok") and pcm.get("memoryCount", 0) >= 5:
        if int(pcm.get("walkCount") or 0) < 1:
            print(f"FAIL: Photo album trip should include walking/hike travel segments: {pcm}")
            ok = False
        if not pcm.get("hasHikeHtml"):
            print(f"FAIL: Hike traveler HTML missing: {pcm}")
            ok = False
        if int(pcm.get("walkCount") or 0) > 0 and not pcm.get("hikeMarker"):
            print(f"FAIL: Walking segment should swap to hike marker: {pcm}")
            ok = False
        if int(pcm.get("walkCount") or 0) > 0 and pcm.get("memoryKeepsHike") is False:
            print(f"FAIL: Memory hold after a hike should keep the walker icon: {pcm}")
            ok = False
        if int(pcm.get("badBacktrack") or 0) > 2:
            print(f"FAIL: Too many absurd out-and-back teleports in travel paths: {pcm}")
            ok = False
    hen = pcm.get("nightAtHenrietta") if isinstance(pcm, dict) else None
    if hen is not None:
        if not hen.get("night") or float(hen.get("factor") or 0) < 0.9:
            print(f"FAIL: Henrietta overnight should be full night: {hen}")
            ok = False
    if cinema_ux.get("nightOk") is False and cinema_ux.get("nightFactor") is not None:
        # Only fail when a local-overnight segment was found but styling stayed day
        if float(cinema_ux.get("nightFactor") or 0) < 0.4:
            print(f"FAIL: Overnight segment should engage night styling: {cinema_ux}")
            ok = False
    if not dock_state.get("play") or not dock_state.get("pauseGone") or not dock_state.get("speed"):
        print(f"FAIL: Play control missing: {dock_state}")
        ok = False
    if not dock_state.get("rewind") or not dock_state.get("forward") or not dock_state.get("seekFn"):
        print(f"FAIL: Rewind / skip-ahead controls missing: {dock_state}")
        ok = False
    if not dock_state.get("rewindWorks"):
        print(f"FAIL: Seek should move the playhead back and forward: {dock_state}")
        ok = False
    if not dock_state.get("dockBoxGone"):
        print(f"FAIL: Transport should be play-only (no dark dock box): {dock_state}")
        ok = False
    if not dock_state.get("locationHudGone") or not dock_state.get("tripClock"):
        print(f"FAIL: Location HUD removed / trip clock required: {dock_state}")
        ok = False
    if not dock_state.get("exportGone") or not dock_state.get("shareGone") or not dock_state.get("loopGone"):
        print(f"FAIL: Extra dock controls should be removed: {dock_state}")
        ok = False
    if not dock_state.get("legendGone"):
        print(f"FAIL: Trip legend pill should be removed: {dock_state}")
        ok = False
    if not dock_state.get("memoryStage"):
        print(f"FAIL: Memory stage overlay missing: {dock_state}")
        ok = False
    if abs(float(dock_state.get("defaultSpeed") or 0) - 1.0) > 0.001:
        print(f"FAIL: Default playback speed should be 1.0: {dock_state}")
        ok = False
    if not dock_state.get("mapGestures"):
        print(f"FAIL: Map pan/zoom gestures should stay enabled during play: {dock_state}")
        ok = False
    if not dock_state.get("navCtrlLive"):
        print(f"FAIL: Navigation zoom controls should remain usable in watch mode: {dock_state}")
        ok = False
    if not dock_state.get("userExploreOk"):
        print(f"FAIL: User map explore should pause chase camera: {dock_state}")
        ok = False
    if not dock_state.get("userExploreTouchOk"):
        print(f"FAIL: Touch on map should arm explore mode before chase fights gesture: {dock_state}")
        ok = False
    if float(dock_state.get("speedMin") or 1) > 0.25 + 1e-9:
        print(f"FAIL: Speed range should allow 0.25×: {dock_state}")
        ok = False
    if float(dock_state.get("speedMax") or 0) < 4 - 1e-9:
        print(f"FAIL: Speed range should allow 4×: {dock_state}")
        ok = False
    if dock_state.get("speedPills", 0) > 0:
        print(f"FAIL: Discrete speed pills should be removed: {dock_state}")
        ok = False
    if not dock_state.get("speedRail") or not dock_state.get("speedControlOn"):
        print(f"FAIL: Continuous speed rail missing: {dock_state}")
        ok = False
    if not dock_state.get("speedRailRight"):
        print(f"FAIL: Speed rail should sit bottom-right (not bottom-middle): {dock_state}")
        ok = False
    if not dock_state.get("playCentered"):
        print(f"FAIL: Play button should stay bottom-center: {dock_state}")
        ok = False
    if not dock_state.get("equalPace"):
        print(f"FAIL: Day/night clockPlayPace must be equal (1.0): {dock_state}")
        ok = False
    if mobile.get("vw") != 390:
        print(f"FAIL: Mobile viewport not applied: {mobile}")
        ok = False
    if not mobile_default.get("panelPeek"):
        print(f"FAIL: Mobile panel should default to peek sheet for map-first browsing: {mobile_default}")
        ok = False
    if (mobile_default.get("mapVisiblePx") or 0) < 420:
        print(f"FAIL: Mobile peek sheet hides too much map: {mobile_default}")
        ok = False
    if not mobile_default.get("destRailVisible"):
        print(f"FAIL: Mobile destination quick-pick rail missing: {mobile_default}")
        ok = False
    if dest_chip_pick.get("hasChip") and not dest_chip_pick.get("picked"):
        print(f"FAIL: Mobile destination chip did not select a trip: {dest_chip_pick}")
        ok = False
    if not mobile.get("panelFull"):
        print(f"FAIL: Mobile panel should expand to full sheet for list scroll tests: {mobile}")
        ok = False
    if not mobile.get("panelHeightOk"):
        print(f"FAIL: Mobile panel too short to browse journeys: {mobile}")
        ok = False
    if not mobile.get("panelFull") and mobile.get("togglePanelOverlap"):
        print(f"FAIL: Mobile panel toggle overlaps sheet content: {mobile}")
        ok = False
    if not mobile.get("tripListVisible"):
        print(f"FAIL: Mobile trip list not visible in sheet: {mobile}")
        ok = False
    if mobile.get("dockTimelineOverlap"):
        print(f"FAIL: Mobile transport dock overlaps timeline: {mobile}")
        ok = False
    if mobile.get("listClientH", 0) < 120:
        print(f"FAIL: Mobile trip list scroll area too small: {mobile}")
        ok = False
    if mobile.get("listScrollable") and not mobile.get("listScrollWorked"):
        print(f"FAIL: Mobile trip list did not scroll: {mobile}")
        ok = False
    if mobile.get("listScrollable") and not mobile.get("lastTripReachable"):
        print(f"FAIL: Mobile trip list cannot reach last journey: {mobile}")
        ok = False
    if not mobile.get("speedHiddenOnAtlas"):
        print(f"FAIL: Speed rail should hide on atlas browse: {mobile}")
        ok = False
    if not mobile.get("panelFull") and mobile.get("chromeOverlap"):
        print(f"FAIL: Mobile chrome overlaps (panel/dock/speed/timeline): {mobile}")
        ok = False
    if not mobile.get("timelineInView"):
        print(f"FAIL: Mobile timeline clipped off-screen: {mobile}")
        ok = False
    if mobile.get("toggleSize", 0) < 40:
        print(f"FAIL: Mobile panel toggle too small: {mobile}")
        ok = False
    if not mobile.get("dockInView"):
        print(f"FAIL: Mobile dock not fully in view: {mobile}")
        ok = False
    if not mobile.get("panelGrab"):
        print(f"FAIL: Mobile panel grab handle missing: {mobile}")
        ok = False
    if mobile_pick.get("panelCollapsed") or not mobile_pick.get("tripFocus"):
        print(f"FAIL: Mobile trip select should keep browse sheet open until play: {mobile_pick}")
        ok = False
    if mobile.get("eraChips", 0) < 2:
        print(f"FAIL: Mobile era chips missing: {mobile}")
        ok = False
    if not mobile_play.get("playing") or not mobile_play.get("panelCollapsed"):
        print(f"FAIL: Mobile watch/play sheet state bad: {mobile_play}")
        ok = False
    if mobile_play.get("speedForwardOverlap"):
        print(f"FAIL: Mobile speed slider overlaps fast-forward during playback: {mobile_play}")
        ok = False
    if not mobile_play.get("speedAboveDock"):
        print(f"FAIL: Mobile speed slider should sit above transport dock while watching: {mobile_play}")
        ok = False
    if not cinema.get("cinema") or not cinema.get("portrait"):
        print(f"FAIL: Export cinema classes missing: {cinema}")
        ok = False
    if not cinema.get("titleVisible") or not cinema.get("titleInUpper"):
        print(f"FAIL: Export title card not in 9:16 safe upper zone: {cinema}")
        ok = False
    if not cinema.get("dockHidden"):
        print(f"FAIL: Dock should hide in cinema mode: {cinema}")
        ok = False
    if not cinema.get("portraitish"):
        print(f"FAIL: Map frame not portrait-ish for export: {cinema}")
        ok = False
    stats_text = (cinema.get("statsText") or "").lower()
    if cinema.get("statsHidden") or "mile" not in stats_text:
        print(f"FAIL: Intro/outro should show mapped miles: {cinema}")
        ok = False
    if not share_state.get("minimalDock"):
        print(f"FAIL: Dock should be minimal (no export/loop): {share_state}")
        ok = False
    if share_state.get("btn"):
        print(f"FAIL: Share button should be removed from dock: {share_state}")
        ok = False
    if not share_state.get("hasBlurb"):
        print(f"FAIL: Share blurb missing live URL: {share_state}")
        ok = False
    if share_state.get("dwellCount", 0) > 2 and share_state.get("richCaptionCount", 0) < 1:
        print(f"FAIL: Expected richer dwell captions: {share_state}")
        ok = False
    if abs(float(share_state.get("defaultSpeed") or 0) - 1.0) > 0.001:
        print(f"FAIL: Default speed should be 1×: {share_state}")
        ok = False
    if share_state.get("nearbyLeak"):
        print(f"FAIL: Nearby-place copy should be removed: {share_state}")
        ok = False
    pp = photo_pins if isinstance(photo_pins, dict) else {}
    if pp.get("photoCount", 0) >= 5:
        if pp.get("markerCount", 0) < 3:
            print(f"FAIL: Expected photo markers on album trip: {pp}")
            ok = False
        if pp.get("inColorado", 0) < 3:
            print(f"FAIL: Photo markers should be in Colorado GPS bounds: {pp}")
            ok = False
        if pp.get("southOf32", 0) > 0:
            print(f"FAIL: Photo markers stacked into Mexico/south: {pp}")
            ok = False
        if not pp.get("transformSafe"):
            print(f"FAIL: Photo pin root must be position:absolute without transform transition: {pp}")
            ok = False
        if float(pp.get("maxPixelDrift") or 0) > 8:
            print(f"FAIL: Photo pin DOM drifted from geo projection (stacking bug): {pp}")
            ok = False
        if int(pp.get("exifMismatch") or 0) > 0:
            print(f"FAIL: Photo pins must stay at exact EXIF GPS (not snapped to Tesla path): {pp}")
            ok = False
        # Path must visit the photo GPS — pins at EXIF, route drawn through those shots
        if float(pp.get("maxOffRouteMi") or 0) > 2.5:
            print(f"FAIL: Route path must reach exact photo GPS (<=2.5mi): {pp}")
            ok = False
    if story_pacing.get("ok"):
        if story_pacing.get("midTravelVisible"):
            print(f"FAIL: Mid-travel caption should be hidden: {story_pacing}")
            ok = False
        if story_pacing.get("earlyTravelVisible") is False:
            print(f"FAIL: Early-travel caption should show: {story_pacing}")
            ok = False
        if story_pacing.get("lateDwellMode") is False:
            print(f"FAIL: Late dwell should use dwell caption mode: {story_pacing}")
            ok = False
        if story_pacing.get("lateDwellPois", 0) > 0:
            print(f"FAIL: Nearby-place chips should be gone: {story_pacing}")
            ok = False
    else:
        print(f"WARN: Caption pacing samples skipped: {story_pacing}")
    if critical_errors:
        print("WARN: Console errors detected")

    if ok:
        print("\nVerification PASSED")
        return 0
    print("\nVerification FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
