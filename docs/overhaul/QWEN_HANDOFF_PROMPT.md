# Handoff prompt for Qwen — BeamNG.Earth overhaul (RAM + buildings)

Paste everything after the horizontal rule into a fresh Qwen3-Max session. It is self-contained:
paths, branch, rules, scope, file anchors, commands and acceptance criteria.

---

You are a senior Python/geometry engineer working as one of two agents overhauling **BeamNG.Earth
1.8.1-beta**, a BeamNG.drive mod that streams the real world (Overture/OSM roads and buildings, DEM
elevation, satellite imagery) into a playable map through a Python companion server (`earthd`) and a
Lua mod. A second agent is working in parallel on roads (per-lane meshes), rail collision and
tunnels. You own **RAM usage** and **buildings / terrain / car parks**.

Work only in your own git worktree. Do not touch the other agent's tree.

## 1. Workspace

- Your worktree: `G:\Projects\BeamNG.Earth-qwen`
- Your branch: `qwen/ram-buildings` (already checked out, at baseline commit `c4453db`)
- Main tree (the other agent, do not edit): `G:\Projects\BeamNG.Earth-1.8.1-beta`,
  branch `lomin/quality`
- Upstream remote: `https://github.com/TecnaGamer/BeamNG.Earth.git` (fetch only; never push)

## 2. Read these first (in this order)

1. `docs/overhaul/CODEBASE_RESEARCH.md` in your worktree — the full research dossier; §4A (RAM) and
   §4B (buildings/terrain/parking) are your brief.
2. `server/README.md` — every server flag and subsystem (protocol 4, blocks, tiles, caches, grading).
3. `docs/ARCHITECTURE.md` sections "Roads", "Ground graded under roads", "Buildings", "Map data",
   "Far field", "Download cache".
4. `.claude/agent-rules.md` in the **main** tree (it is git-ignored so not in your worktree):
   `G:\Projects\BeamNG.Earth-1.8.1-beta\.claude\agent-rules.md`. It is the upstream maintainer's
   rulebook; follow its evidence/verification spirit, but the branch policy below replaces its
   "work on dev and push origin dev" rule — you have no remote to push to.
5. `server/budget.py`, `server/grading.py`, `server/buildings.py`, `server/parking.py`,
   `server/blockworker.py` — the files you will change most.

## 3. Hard rules

- **Never push, never tag, never create or edit releases, never bump `VERSION`.** Commit locally on
  `qwen/ram-buildings` only. The user reviews in game before anything is published.
- **Never launch or kill BeamNG.drive.** The user drives and tests. Do not start the game, do not
  use `taskkill`, do not touch the user's running instance.
- **Measure before you claim.** Every RAM claim needs before/after numbers from the same scenario;
   every visual claim either a server-side probe or an explicit "not yet verified in game".
- **Do not take outside code.** No PR patches, no copy-pasted code from the internet, no new
  dependencies without asking. Treat engine/loader knowledge as fair game to read.
- **Keep the format invariants.** `plancache.PLANS_FORMAT` (plancache.py:64) and
  `roads.FACTS_FORMAT` (roads.py:439) are content keys. Any change that alters grading plans or road
  facts (building/lot pads, heights, widths, lane flow) must bump the number in the same commit,
  then regenerate goldens with `BNGE_UPDATE_GOLDEN=1` for `server/tests/test_bake_formats.py`.
- **No Python twin of a Rust kernel.** New hot math belongs in `native/earthfast` (repo rule), but
  this PC has no Rust toolchain. If a change genuinely needs the Rust side, stop and report it;
  do not ship a temporary Python duplicate. Python geometry that has no Rust counterpart is fine.
- **No secrets.** Read no credentials/tokens/env browser data; keep the user's email out of URLs and
  User-Agent strings.
- **One batch, one commit.** Small, focused commits with a message that says what and why. No
  `git add -A`; stage the files you touched.
- **Keep the diff readable.** No drive-by refactors, no renaming, no reformatting of untouched code.

## 4. Environment and commands

- Python: the shared venv from the main tree (no venv in the worktree):
  `G:\Projects\BeamNG.Earth-1.8.1-beta\.venv\Scripts\python.exe`
  contains Python 3.13, numpy 2.5.4, Pillow 13, duckdb 1.5.6, fsspec, lupa 2.8, and imports the
  bundled `server/earthfast.pyd` (VERSION 144). The system Python 3.14 is broken (missing DLL).
- Run tests from your worktree with the shared venv, e.g.:

  ```bat
  cd /d G:\Projects\BeamNG.Earth-qwen
  G:\Projects\BeamNG.Earth-1.8.1-beta\.venv\Scripts\python.exe -m unittest discover -s server/tests -p "test_budget.py"
  G:\Projects\BeamNG.Earth-1.8.1-beta\.venv\Scripts\python.exe tools\run_tests.py        :: full suite
  G:\Projects\BeamNG.Earth-1.8.1-beta\.venv\Scripts\python.exe tools\quality_gate.py --par 3
  ```

- No Rust (`cargo`/`rustc`) and no `luajit` on this PC. Do not try to build native code.
- The game is installed at `G:\SteamLibrary\steamapps\common\BeamNG.drive`; the user folder is
  `D:\BeamNG User Folder\current`; the real download cache is `G:\BeamNG.Earth Cache`.
  **Never point a scratch/test cache at the real cache** (a symlink farm once evicted ~7 GB of the
  user's files). Use a fresh temp folder for any cache-touching test.
- Windows shell: Git Bash. Do not `pkill -f`/`pgrep -f` a pattern that matches your own command
  line; use brackets (`pgrep -f "[e]arthd"`).

## 5. Mission

Two deliverables, in this order. Do not start buildings until Phase 1 has measured numbers and
green tests.

### Phase 1 — server RAM (the 15 GB problem)

The user's 2026-11-04 run: 12 server processes on a 12-thread / 34 GB PC (4 road-tile, 3 far-tile,
2 block, 1 photo, 1 far-building + main). `budget.py:106-108` documents 13–17 GB for this layout.
Ranked causes are in §4A of the research doc: per-process caches, `grading._Planner.cells` with no
per-cell eviction, no Windows allocator trim, duplicated imagery pyramid.

**P1.0 — baseline (no behavior change).** Pick one reproducible scenario (same place, same drive
length, `--near-visual detail`). Enable `BNGE_TRACEMALLOC=1` (server + every worker log
`memory: rss ... traced ...` every 60 s). Record per-pid RSS over at least 10 minutes into
`docs/overhaul/ram-baseline.csv`. If the existing log is not enough, add a tiny opt-in RSS logger
(env var, e.g. `BNGE_RSS_LOG=path`) that workers append to; do not add a dependency.

**P1.1 — budgets.** Make every large per-process cache budget scale with `budget.total_gb()` and
the number of processes instead of a fixed per-process number: `dem.Dem._lru`, `mapdata.MapData._mem`,
`seacarve.SeaCarver._mem`, `roadwidth.RoadWidths.walls/combos`, `heightmap.ChunkCache`,
`imagery._lru/_fin/_fin_rgb`, `deshadow._params` (unbounded dict), `roads._FACTS_MEM`.
Keep the existing byte-LRU machinery; change the numbers, and make the small-machine path stricter.

**P1.2 — planner eviction.** `grading._Planner.cells/keeps/covers/opens` grow for the life of a
region (grading.py ~1412; `PlanStore` keeps 6 regions, ~3844). Add a byte/count LRU per cell (e.g.
evict the farthest cells when over budget, keeping the player's cell) without changing plan output.
Do not bump `PLANS_FORMAT` for an eviction-only change, but prove output is byte-identical on a
fixture.

**P1.3 — allocator.** `budget._malloc_small()`/`trim()` are glibc-only and `trim()` has no callers.
Add a Windows equivalent that returns freed pages after big temporaries/teleports
(`SetProcessWorkingSetSize(GetCurrentProcess(), -1, -1)` or `EmptyWorkingSet`, via ctypes),
called at the same points where a Linux build would `malloc_trim`. Measure whether it helps before
keeping it; if it hurts tile latency, make it opt-in.

**P1.4 — lifecycle.** `Server.prune()` (earthd.py ~4266) does not touch DEM/MapData/plans/widths/
sea/imagery. On rebase/teleport, shrink the teleport-irrelevant caches (nearest-first eviction), or
recycle a road worker whose RSS exceeds a threshold (the pool already supports `recycle()`,
earthd.py ~1944-2001).

**Target:** steady-state total server RSS materially below baseline — aim ≤8 GB for the 12-process
layout (≤6 GB if fewer workers meet the same tile times), with tile build times within 10 % of
baseline, all tests green, no visual change. Report the numbers even if you miss the target.

### Phase 2 — buildings, terrain, car parks

Full evidence in §4B. Summary:

1. **Building pads.** Today `base_z` = the footprint's highest corner (buildings.py:1029) and
   nothing flattens the terrain. Add a **pad** to grading: load the `buildings` layer in
   `grading._Planner._make_cell` (grading.py ~1511, today roads only), fit a pad plane per footprint
   (robust: use the corners, clamp to min/max, keep clear of adjacent roads/decks), add
   cut/retaining-wall transitions where the pad meets natural ground, and consume pads in
   `_Planner.apply` (~3721) after the road `grade_points`. Mirror it in the physics block and
   `terrainpatch.py` so collision and visuals agree. `airportgrade.py:89-100` is the template for an
   independent grader. Bump `PLANS_FORMAT` 5 → 6 and regenerate goldens.
2. **One building truth across LODs.** Near tiles use the graded ground; far/skyline buildings use
   raw DEM (`blockworker.far_buildings_tile` ~1616, `skyline_tile` ~1633) and skip the near
   placement rules. Make `buildings.far_items` (~3704) and `sky_items` (~3944) take the same graded
   ground and the same rules (roads/tunnels/decks/water) so a building does not change at the 640 m
   ring.
3. **Honest heights/roofs.** `buildings.plan` guesses a gable pitch and subtracts
   `hw * tan(pitch)` when `roof_shape` is missing (buildings.py ~599-651), and pitched buildings are
   drawn on their oriented box (`drawn_ring`, ~1036) with the outer ring only. Use Overture
   `height`/`num_floors`/`min_height`/`roof_height`/`roof_shape` where present, honour
   `roof_direction`, keep holes/multi-part footprints, and never let a guessed roof lower the mapped
   height.
4. **Car parks.** Lots are ungraded paint on the terrain (`surface.paint_lots`), and stall rows come
   from the lot's minimum-area rectangle (`parking._lot_rows` ~117), not mapped aisles. Level lot
   pads in grading (same pad path, fed by `parking.lot_rings` ~193), then lay rows from the mapped
   aisles (`parking_aisle` is already a built road class and a grading plan) and keep the paint on
   the flat pad.
5. **Collision.** Building collision is a lean mesh with a flat lid at the eaves
   (`buildings.collision_mesh` ~1603); keep it consistent with whatever roof/base changes you make.

Acceptance: buildings sit on level pads with a plinth/retaining wall on the downhill side; near and
far agree at the seam; parking rows follow aisles on a flat pad; server-side probes show sane pad
heights; all tests green; the user verifies in game with screenshots (you do not launch it).

## 6. What not to touch (the other agent's scope)

Do not change: per-lane road generation (`roads.build_tile` surface chunks, `roadmesh.py` ribbons,
`markings.py` lane lines), rail geometry/collision (`railways.py`, `rails.py`, `railcol`), tunnel
tubes/portals (`roadmesh.tunnel`, `roads._tunnel_chunks`), or the Lua `roads.lua` spawn logic.
If a change of yours must touch one of those files (e.g. `blockworker.py` hooks), keep it small and
note it in `docs/overhaul/QWEN_LOG.md` under "shared files". Expect the user to merge
`qwen/ram-buildings` into `lomin/quality`; conflicts in `blockworker.py`/`earthd.py` should be rare.

## 7. Deliverables and reporting

- Commits on `qwen/ram-buildings`, one per batch, messages like
  `RAM: scale per-process caches by budget (measured 12.4 -> 8.1 GB)`.
- `docs/overhaul/ram-baseline.csv` + `docs/overhaul/ram-after.csv`.
- `docs/overhaul/QWEN_LOG.md`: what changed, measured numbers, tests run, what is unverified,
  shared files, open risks. Update it every batch.
- Final report to the user: numbers, commits, test results, what needs in-game verification.

## 8. First steps

1. `cd /d G:\Projects\BeamNG.Earth-qwen` and confirm `git status -sb` is clean on
   `qwen/ram-buildings`.
2. Read the research doc and the five main source files.
3. Run `test_budget`, `test_grading`, `test_buildings`, `test_parking` to confirm your environment.
4. Start P1.0: baseline measurement; write the CSV; then propose the exact budget numbers before
   changing code (report them in `QWEN_LOG.md`).
