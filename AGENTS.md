# Agent rules — My Mile Diary

Follow these on every change. Do not leave the repo, docs, or live site stale.

## Keep the repo current (required)

After any meaningful change (map UI, trip segmentation, data, photos, deploy, features):

1. **Update committed outputs** that the change affects (`data/trips.json`, `data/photos_index.json`, `data/trip_photos.json`, `output/trips.geojson`, `output/photos/thumbs/`, `output/gpx/`, caches as needed). Rebuild with `python scripts/build_map.py` / `--public` when the map or trips change. `--public` writes `output/index.html` and embeds `MAPBOX_TOKEN` as **base64** (never a raw `pk.`/`sk.` string — GitHub push protection blocks those on `gh-pages`).
2. **Update README.md** whenever user-facing behavior, setup, live URL status, features, or commands change. Do not leave outdated screenshots/captions, wrong endpoints, or broken instructions.
3. **Refresh README screenshots** after any UI-visible change — regenerate `docs/screenshots/` and update README embeds/captions in the **same** PR. Never ship a diary UI change with stale Road Replay / Tesla screenshots.
4. **Update examples/config docs** (`data/owner_config.json.example`, photo dump docs, workflow comments, etc.) when config shape or deploy steps change.
5. **Preserve the live demo URL** — always `https://ramkandimalla94.github.io/mymilediary/` in README (after the GitHub repo is renamed to `mymilediary`). That URL is stable; the *content* behind it updates via CI. Never invent a second public URL.
6. **Protect GitHub Pages deploy** — merges to `main` must keep `.github/workflows/pages.yml` green. Keep repo secret `MAPBOX_TOKEN` set to a public `pk.` token (never `sk.`). Deploy must publish `index.html` **and** `photos/` thumbs when present. If you change `build_map.py` or deploy steps, verify the workflow can still switch to `gh-pages` after a dirty build (built site is copied aside; worktree is reset before checkout).

## Live site

- Push/merge to `main` triggers **Deploy GitHub Pages**.
- The workflow builds `output/index.html` (+ photo thumbs) and pushes to the `gh-pages` branch.
- After merge, confirm the Actions run succeeded. If deploy fails, fix it in the same effort — do not treat docs-only updates as done while the live link serves old content.

## Photos

- Dump albums at `data/photos/<album>/*` (gitignored originals).
- Local: `python scripts/ingest_photos.py` → `python scripts/enrich_trips_with_photos.py` → `python scripts/build_map.py`.
- Hover UI serves thumbs from `output/photos/thumbs/` (committed + deployed).

## Cursor Cloud specific instructions

- Install via `.cursor/install.sh`; local preview: `python -m http.server 8765` then open `/output/travel_map.html`.
- Prefer regenerating trip/map artifacts in-repo over documenting manual one-offs.
- When fixing map behavior users see on the live link, ship the data/template/workflow changes together so main + Pages stay aligned.
