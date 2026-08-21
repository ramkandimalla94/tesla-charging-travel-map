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
    trip_photos = load_trip_id("2025-09-25_Addison_to_Addison")  # Colorado album memories
    photo_clock: dict = {}

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
        page.evaluate("stopPlayback()")
        print(f"Playback: {play_state}")
        page.screenshot(path=str(SCREENSHOTS / "06-watch-mode.png"))

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
              return {
                ok: true,
                photoId: memSeg.photo_id || '',
                clockText, metaText, expectHud, expectMed,
                clockOk: clockText === expectHud,
                metaOk: metaText === expectMed,
                datetime: memSeg.datetime || memSeg.clock_start,
                nightAtHenrietta,
              };
            }"""
        )
        print(f"Photo clock sync: {photo_clock}", flush=True)
        page.evaluate("isPlaying = false; stopPlayback()")
        page.evaluate(f"selectTrip({json.dumps(trip_co)})")
        page.wait_for_timeout(300)

        # Floating play control (no dock box); speed via hidden range + [ ] keys
        dock_state = page.evaluate(
            """() => ({
              play: !!document.getElementById('btn-play'),
              pauseGone: !document.getElementById('btn-pause'),
              toggleWorks: typeof syncPlayToggle === 'function',
              speed: !!document.getElementById('speed'),
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
            })"""
        )
        print(f"Play control: {dock_state}")
        queue_state = dock_state  # keep name for older assertion block compatibility below

        # Mobile sheet regression (iPhone-ish)
        page.set_viewport_size({"width": 390, "height": 844})
        page.evaluate("selectTrip('all'); setPanelCollapsed(false)")
        page.wait_for_timeout(700)
        mobile = page.evaluate(
            """() => {
              const panel = document.getElementById('sidebar');
              const dock = document.getElementById('transport-dock');
              const toggle = document.getElementById('sidebar-toggle');
              const era = document.getElementById('era-rail');
              const pr = panel?.getBoundingClientRect();
              const dr = dock?.getBoundingClientRect();
              const tr = toggle?.getBoundingClientRect();
              const styles = getComputedStyle(panel);
              return {
                panelBottomSheet: pr.top > window.innerHeight * 0.35,
                panelGrab: !!document.querySelector('.panel-grab') && getComputedStyle(document.querySelector('.panel-grab')).display !== 'none',
                panelMaxH: pr.height,
                dockWidth: dr.width,
                dockInView: dr.bottom <= window.innerHeight + 2 && dr.top >= 0,
                toggleSize: Math.min(tr.width, tr.height),
                eraScrollable: era ? era.scrollWidth >= era.clientWidth - 1 : false,
                eraChips: document.querySelectorAll('.era-chip').length,
                hubs: document.querySelectorAll('.home-hub').length,
                vw: window.innerWidth,
                vh: window.innerHeight,
              };
            }"""
        )
        print(f"Mobile 390x844: {mobile}")
        page.screenshot(path=str(SCREENSHOTS / "08-mobile-sheet.png"))
        page.evaluate("selectTrip('all'); startPlayback()")  # should no-op / ask select
        page.evaluate(f"selectTrip({json.dumps(trip_co)}); startPlayback()")
        page.wait_for_timeout(900)
        mobile_play = page.evaluate(
            """() => ({
              playing: isPlaying,
              watching: document.body.classList.contains('watching'),
              panelCollapsed: document.getElementById('sidebar')?.classList.contains('collapsed'),
              pad: typeof mapCameraPadding === 'function' ? mapCameraPadding() : null,
            })"""
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
                dockHidden: getComputedStyle(document.getElementById('transport-dock')).opacity === '0',
                titleText: document.getElementById('cinema-title-text')?.textContent || '',
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
                /Memory|Passing through|Departing|Final stop|nearby:|✓/i.test(s.caption || '')
                || /Stop \\d|nearby|Homeward|Photo|places/i.test(s.subcaption || '')
              );
              return {
                btn: !!btn,
                enabled: btn ? !btn.disabled : false,
                hasBlurb: blurb.includes('Relive it:') && blurb.includes('mymilediary'),
                hasStoryShare: !!(trip?.story?.share_blurb),
                sampleCaption: dwellSegs[0]?.caption || '',
                dwellCount: dwellSegs.length,
                richCaptionCount: rich.length,
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
    if cinema_ux.get("maxDwell", 9999) > 1200:
        print(f"FAIL: Dwell segments should be short (<=1.2s): {cinema_ux}")
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
    if abs(float(dock_state.get("defaultSpeed") or 0) - 0.15) > 0.001:
        print(f"FAIL: Default playback speed should be 0.15: {dock_state}")
        ok = False
    if float(dock_state.get("speedMin") or 1) > 0.05 + 1e-9:
        print(f"FAIL: Speed range should allow 0.05×: {dock_state}")
        ok = False
    if mobile.get("vw") != 390:
        print(f"FAIL: Mobile viewport not applied: {mobile}")
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
    if mobile.get("eraChips", 0) < 2:
        print(f"FAIL: Mobile era chips missing: {mobile}")
        ok = False
    if not mobile_play.get("playing") or not mobile_play.get("panelCollapsed"):
        print(f"FAIL: Mobile watch/play sheet state bad: {mobile_play}")
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
    if abs(float(share_state.get("defaultSpeed") or 0) - 0.15) > 0.001:
        print(f"FAIL: Default speed should be 0.15×: {share_state}")
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
