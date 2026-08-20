# Agent rules — Tesla Charging Travel Map

Follow these on every change. Do not leave the repo, docs, or live site stale.

## Keep the repo current (required)

After any meaningful change (map UI, trip segmentation, data, deploy, features):

1. **Update committed outputs** that the change affects (`data/trips.json`, `output/trips.geojson`, `output/gpx/`, caches as needed). Rebuild with `python scripts/build_map.py` / `--public` when the map or trips change.
2. **Update README.md** whenever user-facing behavior, setup, live URL status, features, or commands change. Do not leave outdated screenshots/captions, wrong endpoints, or broken instructions.
3. **Update examples/config docs** (`data/owner_config.json.example`, workflow comments, etc.) when config shape or deploy steps change.
4. **Preserve the live demo URL** — always `https://ramkandimalla94.github.io/tesla-charging-travel-map/` in README. That URL is stable; the *content* behind it updates via CI. Never invent a second public URL.
5. **Protect GitHub Pages deploy** — merges to `main` must keep `.github/workflows/pages.yml` green. If you change `build_map.py` or deploy steps, verify the workflow can still switch to `gh-pages` after a dirty build (built HTML is copied aside; worktree is reset before checkout).

## Live site

- Push/merge to `main` triggers **Deploy GitHub Pages**.
- The workflow builds `output/index.html` and pushes it to the `gh-pages` branch.
- After merge, confirm the Actions run succeeded. If deploy fails, fix it in the same effort — do not treat docs-only updates as done while the live link serves old content.

## Cursor Cloud specific instructions

- Install via `.cursor/install.sh`; local preview: `python -m http.server 8765` then open `/output/travel_map.html`.
- Prefer regenerating trip/map artifacts in-repo over documenting manual one-offs.
- When fixing map behavior users see on the live link, ship the data/template/workflow changes together so main + Pages stay aligned.
