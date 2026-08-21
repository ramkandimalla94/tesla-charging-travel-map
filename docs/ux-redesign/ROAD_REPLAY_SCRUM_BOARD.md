# Road Replay UX Redesign — Scrum Board

**Product:** Tesla charging → cinematic trip map (“Road Replay”)  
**Session style:** Cloud-agent parallel roles (no shared standup; artifact-driven handoffs)  
**Critical complaint:** Home page locations overlapping; UI feels poor  
**This doc owns:** Process, backlog, DoD, handoffs — **not** implementation  

**Team session status (2026-08-21):** PM + Design Lead + Eng Lead + Frontend Dev + QA ran in parallel. Artifacts filled: `01-audit-notes.md`, `02-design-vision.md`, `03-eng-plan.md`. Live QA confirmed overview label soup + cinema chrome competition. **Do not implement UI until user green-lights Sprint 1.**

---

## 1. Team operating model (cloud-agent session)

Each role is a **separate agent run** (or sequential persona). Ownership is exclusive; handoffs are files, not chat lore.

| Role | Owns | Does | Does not |
|------|------|------|----------|
| **PM** | Problem framing, ranked backlog, acceptance criteria, sprint goal | Writes problem statements; fills Impact/Confidence; gates Ready vs Idea; updates this board | Design mockups, code, visual polish |
| **Design Lead** | Visual/UX vision, information hierarchy, motion rules | Audit screenshots + design vision note; label collision rules; panel/dock IA; mobile breakpoints | Shipping code, performance profiling |
| **Eng Lead** | Feasibility, Effort scores, tech approach, risk flags | Eng plan; maps items to `travel_map.html` / `build_map.py` surfaces; Blocked reasons; spike notes | Pixel-perfect CSS without Design Lead vision |
| **Frontend Dev** | Implementation of Ready items | Code + rebuild map artifacts per `AGENTS.md`; keep live URL/docs aligned | Re-prioritizing backlog; inventing IA without design note |
| **QA** | Verification against DoD | Playwright/manual matrix; screenshot before/after; a11y spot checks; “looks broken” checklist | Scope changes mid-sprint |

### Session rules

1. **One owner per backlog item** (role, not person name).
2. **Status ≠ done until QA marks DoD pass** (even if Frontend Dev pushed).
3. **Must-not-embarrass** items (Sprint 1) block polish items unless Eng Lead re-ranks.
4. **Parallel work is allowed** only when artifacts don’t conflict (e.g. Design Lead vision + Eng Lead spikes on different themes).
5. **No implementation in discovery personas** — PM/Design/Eng Lead produce docs only.

---

## 2. Stats board concept

Every backlog item carries:

| Field | Meaning | Scale |
|-------|---------|-------|
| **Impact** | How much it reduces “UI feels poor” / overlap / share embarrassment | 1–5 (5 = critical complaint / share-blocker) |
| **Effort** | Relative agent+review cost | 1–5 (1 = half-day agent, 5 = multi-surface rewrite) |
| **Confidence** | How sure we are Impact/Effort are right *before* build | 0.1–1.0 |
| **Priority Score** | `(Impact × Confidence) / Effort` | Higher = do sooner |
| **Status** | Workflow state | `Idea` · `Ready` · `Blocked` |
| **Owner** | Accountable role | PM / Design Lead / Eng Lead / Frontend Dev / QA |

### Status definitions

- **Idea** — Problem known; acceptance incomplete or unvalidated.
- **Ready** — AC written, design constraint present (or “N/A — bugfix”), Effort set, no blocker.
- **Blocked** — Waiting on artifact, Mapbox/token/data, or prior story.

### Score interpretation (rule of thumb)

| Score | Guidance |
|-------|----------|
| ≥ 2.0 | Sprint 1 candidates |
| 1.0–1.9 | Sprint 1 if capacity; else Sprint 2 |
| < 1.0 | Later / polish unless Compliance/a11y legal risk |

---

## 3. Sprint 0 — Discovery / audit

**Goal:** Prove *where* home overlaps and *why* the chrome feels poor; freeze a ranked Ready list for Sprint 1.  
**Exit:** Handoff pack complete (see §5); Sprint 1 items all `Ready` or explicitly `Blocked` with owner.

| ID | Item | Impact | Effort | Conf. | Score | Status | Owner |
|----|------|-------:|-------:|------:|------:|--------|--------|
| S0-01 | **Home overlap audit** — Screenshot overview + trip home base; catalog colliding labels/markers (density, z-index, declutter rules missing). Capture repro zoom/bearing. | 5 | 1 | 0.9 | **4.50** | Ready | Design Lead |
| S0-02 | **Chrome clutter inventory** — Top bar, trip panel, transport dock, timeline, cinema HUD: list competing focal points on first paint. | 4 | 1 | 0.85 | **3.40** | Ready | Design Lead |
| S0-03 | **Playback cinema gap list** — Play vs Export vs Director: pacing, camera, caption, chrome hide/show inconsistencies. | 4 | 2 | 0.75 | **1.50** | Ready | Design Lead |
| S0-04 | **Panel/dock IA map** — Current hierarchy vs intended (trip select → play → export). Note duplicate location strings (map label + dock + HUD). | 4 | 1 | 0.8 | **3.20** | Ready | Design Lead |
| S0-05 | **Responsive/mobile audit** — Narrow widths: panel overlap dock, tap targets, map usable area %. Breakpoints used today. | 4 | 2 | 0.7 | **1.40** | Ready | QA |
| S0-06 | **A11y & click-target audit** — Focus order, contrast on overlays, min 44×44 controls, keyboard play/scrub. | 3 | 2 | 0.7 | **1.05** | Ready | QA |
| S0-07 | **Performance overview layers** — Profile initial load: GeoJSON size, label layer count, terrain; note jank when all trips visible. | 3 | 2 | 0.65 | **0.98** | Ready | Eng Lead |
| S0-08 | **Export cinema quality checklist** — 9:16 framing, chrome bleed, label readability in recording, WebM length/size. | 4 | 2 | 0.75 | **1.50** | Ready | QA |
| S0-09 | **Eng feasibility + Effort calibration** — Map S0 findings → files (`output/travel_map.html`, build script, label layers); set Effort for Sprint 1. | 5 | 1 | 0.85 | **4.25** | Ready | Eng Lead |
| S0-10 | **PM freeze: Sprint 1 Ready list** — Apply scores; write AC; demote Ideas; publish board update. | 5 | 1 | 0.9 | **4.50** | Ready | PM |

**Sprint 0 ordered by Priority Score:** S0-01 → S0-10 → S0-09 → S0-02 → S0-04 → S0-03 / S0-08 → S0-05 → S0-06 → S0-07

---

## 4. Sprint 1 — Must-not-embarrass fixes

**Goal:** First paint and home overview no longer look broken; share/demo path is credible.  
**Sprint goal statement:** *Visitor opens live map → home/overview is readable at a glance; no stacked location soup; primary controls are findable on desktop.*

| ID | Theme | Item | Impact | Effort | Conf. | Score | Status | Owner |
|----|-------|------|-------:|-------:|------:|------:|--------|--------|
| S1-01 | Home overlap / declutter | **Stop label collision on overview** — Declutter/hide/cluster or show only home + trip anchors at overview zoom; no overlapping city/stop names on first load. | 5 | 2 | 0.85 | **2.13** | Idea → Ready after S0-01 | Frontend Dev |
| S1-02 | Home overlap / declutter | **Home base marker hierarchy** — Single clear home treatment; suppress duplicate “home” text layers. | 5 | 1 | 0.9 | **4.50** | Idea → Ready after S0-01 | Frontend Dev |
| S1-03 | Panel/dock IA | **First-viewport declutter** — Collapse or de-emphasize secondary chrome so map is the composition; one primary CTA (pick trip / play). | 4 | 2 | 0.8 | **1.60** | Idea → Ready after S0-02/04 | Frontend Dev |
| S1-04 | Panel/dock IA | **Single source of location truth** — Dock shows current stop; map labels don’t repeat the same string at the same time unless cinema mode. | 4 | 2 | 0.75 | **1.50** | Idea → Ready after S0-04 | Frontend Dev |
| S1-05 | Trip playback cinema | **Playback chrome consistency** — Play preview hides same chrome as cinema where intended; no half-hidden panels. | 4 | 2 | 0.7 | **1.40** | Idea → Ready after S0-03 | Frontend Dev |
| S1-06 | Trip playback cinema | **Camera/pacing polish pass** — Director framing doesn’t clip labels; halt pacing doesn’t feel “stuck.” | 3 | 3 | 0.6 | **0.60** | Idea | Frontend Dev |
| S1-07 | Responsive/mobile | **Narrow layout: no panel-over-dock crush** — Usable map ≥50% height; panel overlays or drawer pattern. | 4 | 3 | 0.7 | **0.93** | Idea → Ready after S0-05 | Frontend Dev |
| S1-08 | A11y & click targets | **Transport controls ≥44px; focus visible** — Play/Export/Director meet target size; keyboard operable scrubber. | 3 | 2 | 0.75 | **1.13** | Idea → Ready after S0-06 | Frontend Dev |
| S1-09 | Performance overview | **Overview layer budget** — Cap visible labels/features until trip selected; defer heavy layers. | 3 | 3 | 0.65 | **0.65** | Idea → Ready after S0-07 | Frontend Dev |
| S1-10 | Export cinema quality | **Export frame is clean** — No UI chrome in WebM; labels legible; 9:16 safe margins. | 4 | 2 | 0.8 | **1.60** | Idea → Ready after S0-08 | Frontend Dev |
| S1-11 | QA gate | **“Home not broken” regression pack** — Automate/screenshot assert overview labels don’t overlap bounding boxes beyond threshold. | 5 | 2 | 0.85 | **2.13** | Ready | QA |

### Sprint 1 recommended commit order (by score, then dependency)

1. **S1-02** (4.50) — home marker hierarchy  
2. **S1-01** (2.13) + **S1-11** (2.13) — declutter + QA harness in parallel after S1-02  
3. **S1-03** (1.60) / **S1-10** (1.60) — chrome declutter + export clean frame  
4. **S1-04** (1.50) → **S1-05** (1.40) → **S1-08** (1.13)  
5. Stretch: **S1-07**, **S1-09**, **S1-06** (scores &lt; 1.0 unless S0 raises Confidence)

**Explicitly out of Sprint 1:** full brand redesign, new fonts/motion system beyond fixing broken overlap, multi-theme redesign (preserve existing Road Replay visual language per product rules).

---

## 5. Definition of Done — “Home page no longer looks broken”

All must be true on **desktop** (1440×900) and **spot-check mobile** (390×844) against live-built `output/travel_map.html` / Pages build:

### Visual / map

- [ ] At default overview camera, **no two location labels overlap** (bounding boxes may touch ≤2px; no stacked text).  
- [ ] **Home** is identifiable in &lt;2 seconds (unique marker or single label — not 3 competing “home” strings).  
- [ ] Overview does not look like a **label soup**; trip density is controlled (cluster, fade, or trip-selected-only detail).  
- [ ] Map remains the **dominant composition**; chrome does not cover &gt;35% of the map on desktop default.

### Interaction

- [ ] User can select a trip and start **Play** without hunting; primary controls not obscured by labels.  
- [ ] Click/tap targets for Play / Export / panel toggle meet **≥44×44** CSS px (or equivalent padding).  
- [ ] No dead clicks on overlapping hit areas (label vs marker vs chrome).

### Cinema / share path (must-not-embarrass)

- [ ] **Export** recording contains **no** trip panel, dock, or top bar.  
- [ ] Exported frame labels remain readable (no overlap in cinema mode for the active trip).

### Process

- [ ] Before/after screenshots in `docs/screenshots/` (or UX redesign folder) updated.  
- [ ] QA checklist S1-11 signed off.  
- [ ] README caption/screenshot not contradicting fixed UI (if user-facing visuals changed).  
- [ ] Rebuild artifacts committed per `AGENTS.md` when map template/data behavior changed.

**Fail = still “broken”:** Any overlapping home/stop names on first load, or export that films the app chrome.

---

## 6. Parallel-agent handoff protocol

### Artifact types (required filenames)

| Artifact | Path (canonical) | Producer | Consumer |
|----------|------------------|----------|----------|
| **Audit notes** | `docs/ux-redesign/01-audit-notes.md` | Design Lead + QA (+ Eng Lead perf section) | PM, Eng Lead |
| **Design vision** | `docs/ux-redesign/02-design-vision.md` | Design Lead | Frontend Dev, PM |
| **Eng plan** | `docs/ux-redesign/03-eng-plan.md` | Eng Lead | Frontend Dev, QA |
| **Ranked backlog** | `docs/ux-redesign/ROAD_REPLAY_SCRUM_BOARD.md` (this file) | PM (updates Status/Score) | Everyone |

Optional evidence: `docs/ux-redesign/evidence/` (screenshots, Lighthouse/perf notes).

### Handoff gates

```
S0 audits ──► 01-audit-notes.md
                 │
                 ▼
            02-design-vision.md  (collision rules + IA + mobile)
                 │
                 ▼
            03-eng-plan.md  (Effort locked, file touch list, risks)
                 │
                 ▼
            PM sets Status=Ready on Sprint 1 rows
                 │
                 ▼
            Frontend Dev implements Ready only
                 │
                 ▼
            QA runs DoD; fails → Status=Blocked + note in eng plan
```

### Rules for agents

1. **Write the artifact before coding** (except pure QA repro).  
2. **Never edit another role’s artifact section** without appending a dated `## Amendment` block.  
3. **Stats changes** only by PM (Impact/Confidence) or Eng Lead (Effort); both may update Score.  
4. **Branch naming** for implementers: `cursor/<theme>-c6cb` (e.g. `cursor/home-label-declutter-c6cb`).  
5. **One theme per PR/agent** when possible (home declutter ≠ export cinema).  
6. **Blocked** items must name the missing artifact ID (e.g. `Blocked: waiting 02-design-vision collision rules`).

### Minimal template stubs (create when Sprint 0 starts)

**01-audit-notes.md** — repro steps, zoom levels, collision inventory, chrome heatmap, mobile notes, perf numbers.  
**02-design-vision.md** — declutter rules, IA diagram (text), cinema vs edit chrome, motion constraints, what *not* to redesign.  
**03-eng-plan.md** — file list, layer IDs, proposed Mapbox declutter/clustering approach, test plan link to DoD.

---

## 7. Board legend (quick copy)

```
Priority Score = (Impact × Confidence) / Effort
Status: Idea | Ready | Blocked
Owners: PM | Design Lead | Eng Lead | Frontend Dev | QA
```

**Now:** Execute Sprint 0 in parallel (Design Lead S0-01/02/04, QA S0-05/06/08, Eng Lead S0-07/09), then PM S0-10 freezes Sprint 1 Ready set.

---

*Scrum Master deliverable only. Implementation agents start after S0-10 and design/eng artifacts exist.*
