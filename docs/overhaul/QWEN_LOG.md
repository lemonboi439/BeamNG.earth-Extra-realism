# QWEN_LOG — BeamNG.Earth overhaul, `qwen/ram-buildings`

Running log of the RAM + buildings/terrain/car-parks work (handoff: `QWEN_HANDOFF_PROMPT.md`).
Updated every batch. Executing agent: Qoder main agent (the user pasted the Qwen handoff to it).

## Scenario and method

- BeamNG.drive is never launched. The server is driven headlessly by `tools/ram_drive.py`:
  the real `earthd.py` (production-like flags: `--near-visual detail --cells 64 --block 1024
  --deshadow full --colormatch s2 --sea on --buildings on --trees on --airports on
  --gas-stations on`, weather/gravity/credits/update-check off) plus a protocol-4 client that
  replays the mod's rules (modsim: poses every 0.5 s sim, terrain-follow, rebases at 15 km).
- Fixed scenario: teleport 51.14, 0.91 (Folkestone — the start of the user's 2026-11-04 run),
  drive heading 150.4° ENU (NW, toward London, the user's route) at 30 m/s, speedup 4 sim-s per
  wall-s, 12 wall-minutes + 2 min parked. 12 processes spawn (4 road, 3 far, 2 block, photo,
  far-building, main) — same layout as the user's run.
- RSS is sampled externally every 5 s (ctypes psapi, working set + private bytes, whole process
  tree) — the server's own `BNGE_TRACEMALLOC` log reports `rss 0 MB` on Windows (broken there),
  traced bytes still logged every 60 s.
- Scratch cache `G:/bnge-ram-cache` (NEVER the real `G:/BeamNG.Earth Cache`), kept warm between
  baseline/after runs so both see the same data. Session folders under `G:/bnge-ram-user-*`
  (deleted after each run).
- Caveats vs the user's real run: no game process competing for cores/RAM; imagery chain is the
  source default (usgs,eox2023,eox2016), not the packaged app's longer chain.

## Batches

### P1.0 — environment + baseline

- Worktree clean on `qwen/ram-buildings` at 819d66d (docs commit over baseline c4453db).
- Tests green with the shared venv: test_budget (5), test_grading (51), test_buildings (97),
  test_parking (19), test_e2e (network OK).
- `tools/ram_drive.py` added (this branch only): headless drive + RSS sampler. Smoke run
  (3 min): 12 procs, far field 428 tiles, peak total RSS 6.58 GB on a cold cache, driver stable
  through 60 s+ server event-loop stalls (non-blocking single-I/O-thread client).
- Baseline run 1 (cold cache along the route, label `baseline` in `ram-baseline.csv`):
  drove 86.4 km sim in 12 min wall; **total tree RSS peak 11.00 GB, last-3-min median 10.30 GB,
  12 processes** (+1 transient). Server backlog severe, exactly like the user's log: only
  4 road tiles finished, "still building after 75-102 s" x3, first graded block 690 s after the
  request, heightmap waits 41-132 s, rebase anchor never ready in the window (no prune ran).
  tracemalloc: main 516 MB traced (Image 93, vegetation 67, airports 33, mapdata 29 MB),
  road workers 130-250 MB traced each — the RSS-traced gap (allocator/native) dominates.
- Baseline run 2 (warm cache, label `baseline-warm`, same route/flags) — **the canonical
  before number**: drove 86.4 km sim in 12 min wall; **total tree RSS peak 15.00 GB,
  last-3-min median 14.67 GB, 12 processes + the venv launcher stub** — an exact reproduction
  of the user's "RAM hit 15 GB". 32 terrain_ready, 27 roads tiles, 25 far-building tiles,
  15 block photos. Biggest single process 2.4-2.9 GB (private).
- Rebase behaviour: prefetches fired on the mod's 15 km rule, but the loaded server's anchor
  builds took ~200 s wall (~800 s sim at speedup 4) and the player outran the mod's
  STALE_DIST=450 m, so every prepared anchor was discarded (as the mod itself would) — prune()
  never ran in the canonical runs. `tools/ram_drive.py --force-rebase` (harness-only deviation,
  commits a ready anchor despite staleness) exists for the separate prune-focused check runs.
- The after-run uses the identical scenario (label `after`, same warm cache); a short
  force-rebase run before/after P1.4 verifies the prune path separately.

### P1.1 — proposed budget numbers (before any code change, per handoff §8.4)

Mechanism: one scale factor in `budget.apply()`, computed per process as
`per_proc_mb = clamp(total_gb * 1000 * CACHE_RAM_SHARE / proc_count, CACHE_MIN_MB, CACHE_MAX_MB)`,
`scale = per_proc_mb / DESKTOP_CACHE_MB`; every large cache constant =
`min(desktop, max(floor, round(desktop * scale)))`. Proposed constants:

- `CACHE_RAM_SHARE = 0.20` (all server caches together may hold 20 % of machine RAM)
- `DESKTOP_CACHE_MB = 1550` (sum of today's desktop per-process constants)
- `CACHE_MIN_MB = 224`, `CACHE_MAX_MB = 1600`
- `proc_count = road_processes() + far_workers (3 desktop / 2 small) + 5`
  (main, block request, block prefetch, photo, far building). This PC: 4+3+5 = 12 ✓
  (matches the log's "road-tile processes: 4 (12 threads, 34 GB)" and the 12 sampled pids).

This PC (34 GB, 12 procs): per-proc 567 MB → scale 0.366. Steam Deck (14.5 GB, 9 procs):
322 MB → scale 0.208. Resulting values (desktop floors = 50 % of the desktop constant;
small-machine floors = today's SMALL_* values, which are the measured Deck minimums):

| constant | desktop now | small now | desktop proposed (34 GB/12) | small proposed (14.5 GB/9) |
|---|---|---|---|---|
| dem.MEM_TILES (1 MB/tile) | 512 | 192 | 256 (floor) | 192 (floor) |
| mapdata.MEM_MB | 192 | 160 | 96 (floor) | 160 (floor) |
| seacarve.MEM_CELLS (~9.5 MB) | 24 | 24 | 12 (floor) | 8 (floor) |
| roadwidth.WALL_CELLS | 48 | 12 | 24 (floor) | 12 (floor) |
| roadwidth.COMBOS | 32 | 32 | 16 (floor) | 6 (floor) |
| heightmap.CHUNK_CACHE (128 KB) | 512 | 512 | 256 (floor) | 128 (floor) |
| imagery.MEM_TILES (192 KB) | 512 | 512 | 256 (floor) | 128 (floor) |
| imagery.MEM_FINISHED_MB | 160 | ≤58 (4/GB) | 80 (floor) | 33 (4/GB, floor 32) |
| imagery.MEM_DECODED | 384 | ≤96 | 192 (floor) | 96 (floor) |
| deshadow.MEM_TILES | 512 | 512 | 256 (floor) | 128 (floor) |
| roads.FACTS_MEM_KEPT (~75 KB) | 640 | 192 | 320 (floor) | 192 (floor) |
| grading PLAN_MEM_MB (new, P1.2) | unbounded | unbounded | 64 | 32 |

Worst-case all-caches-full per process ≈ 810 MB here (most processes hold only a subset),
≈ 9.7 GB × realistic fill ≈ 6–8 GB for the 12-process layout — with P1.2/P1.3 on top the
≤8 GB target is reachable. Also: cap `deshadow._params` (unbounded dict) at 64 regions LRU
(reloadable from its disk cache).

## Shared files

(none touched yet — roads/rail/tunnel files stay untouched per handoff §6)

## P2.1 — building pads (committed)

`grading._Planner` now makes a **pad per building footprint** from the `buildings` layer:
`oriented_box` of the outer ring, ground sampled at the box centre + corners from the planner's own
raw DEM (`self.elev`, PROFILE_ZOOM in every process — the same heights across block/tile workers),
pad z = the median, blend = 2 × the height spread (2–14 m, a 1:2 slope). Underground / level<0
buildings and footprints under 25 m² or over 40 000 m² are skipped. `_apply_pads` flats the ground
over the footprint and blends back to the natural ground; it runs **before `grade_points`**, so a
road keeps its own corridor and the road mesh/tyres see the same pad. `planned_only` skips pads
(the AI graph's wide window makes no blocking building fetch). Pads are made once per cell and
dropped with the cell's plan eviction.

**Why no PLANS_FORMAT bump:** pads are *not* stored in the shared plans (plancache/bake packs);
the plan output is byte-identical. The graded ground changes, and road/building tiles are keyed by
the code hash, so they rebuild on this commit. This keeps the shared plan cache format untouched
(the handoff's plan expected pads inside grading.Plan; a separate layer is cheaper and avoids
touching plancache/bakepack/goldens).

Tests: test_grading 53 (new `BuildingPadTest`: a 20 × 10 m building on a 5 % slope gets a flat pad
at the median, blends at its edge, natural ground away, none under `planned_only`); test_buildings
97, test_parking 19, test_blockworker 5, test_roads 96, test_terrainpatch 4, test_heightmap 22.

Next: P2.2 (far/skyline buildings on the same graded ground), P2.3 heights/roofs, P2.4 lots,
P2.5 collision consistency.

## P2.2 — one building truth across LODs (committed)

- Anchor far building tiles (`blockworker.far_buildings_tile`) and skyline tiles
  (`blockworker.skyline_tile`) now place their buildings on the **anchor's graded ground**
  (`g.apply(..., ROAD_LIFT)`): road grading and the P2.1 pads apply, exactly as the near road tiles.
- Geotile far/skyline content (`blockworker._far_geo_build`) uses `_frame_grader(tile.frame)` — the
  geotile's own anchor-independent grading view, the same one the near geotile content uses — so the
  cached content stays anchor-independent and the seam matches.
- `buildings.far_items` and `sky_items` now receive `roads_xy` and `tunnels_xy` like the near
  `road_tile_items`, so the over-road/over-tunnel placement rules no longer differ at 640 m.
- Revisit keys include the code hash, so old far/skyline content rebuilds on this commit.

Tests: test_buildings 97, test_farfield 39, test_geotiles 14 (1 skipped; the plan-cache test now
counts road fetches separately from the pads' buildings fetch), test_blockworker 5,
test_world_cache 11, test_farglobal 12, test_fargeo non-network classes 4. `test_fargeo.RealDataTest`
cannot run here (offline: it fetches Overture parts not in the local cache) — environment, not code.

Next: P2.3 heights/roofs, P2.4 lots, P2.5 collision consistency.

## P2.3 — honest heights and roofs (committed)

- A **guessed** pitched roof now rises at most `ROOF_RISE_MAX_SHARE` (45 %) of the mapped height: a wide
  building's 32-42° gable no longer eats its walls (a 60 × 20 m, 10 m building keeps 5.5 m of wall
  instead of 2.5 m). The drawn pitch is derived from that capped rise (`spec.pitch`), so the total
  height still matches the map.
- A **mapped `roof_height` wins exactly**: the walls take the rest (10 m building, 3 m roof → 7 m
  walls), and the drawn pitch is derived from it.
- **Roof axis**: `roof:orientation` (`along` / `across`) and `roof:direction` (the closest box axis)
  are read (they were dropped from the attribute list) and set `Spec.roof_across`; `_roof` swaps the
  ridge to the other axis. `drawn_ring` is unchanged (the box is the same rectangle).
- `pitch` stays `None` for regions without a style pitch unless the rise was capped or mapped, so
  the existing roof defaults/tests are unchanged.

Tests: test_buildings 101 (new `RoofHonestyTest`: wide house walls kept, mapped roof height exact,
across/direction ridge swap).

**Still open in P2.3:** courtyard holes and non-rectangular multi-part footprints are still drawn
from the outer ring (building *parts* with heights are drawn, as before); true holes need the
triangulator to take inner rings. Deferred (not in this batch).

Next: P2.4 lots, P2.5 collision consistency.

## P2.4 — car parks: graded pads + aisle rows (committed)

- **Lot pads**: `grading._Planner._cell_pads` now also reads the `infrastructure` layer and turns each
  mapped `parking` polygon into the same flat pad (oriented box, median ground, 2× spread blend, up to
  250 000 m² / 40 m relief), applied before `grade_points`. Lots first, building pads after (a
  building wins an overlap). `surface.paint_lots` / stalls now sit on level ground.
- **Rows from aisles**: a lot that a mapped `parking_aisle` runs through no longer gets the lot's own
  oriented-box rows (`lot_spots`) or its rect stall lines (`lot_markings`) — the existing
  `aisle_spots` / `aisle_markings` handle it. Aisle markings are no longer dropped inside such lots,
  and spots already placed there (aisle/curb) are kept. Lots without aisles are unchanged.
- Helpers: `parking.aisle_lines`, `parking.lots_with_aisles`; used by `tile_lot_spots` and
  `finish_markings` (they fetch the roads layer once per tile).

Tests: test_grading 54 (new `LotPadTest`), test_parking 21 (new `AisleLotTest`: no rect rows with an
aisle, aisle markings kept; rect lots unchanged), test_roads 96.

Next: P2.5 collision consistency, then the after measurement and final report.

## P2.5 — collision consistency (committed)

- The lean building collider's wall foot now matches the **drawn** walls (`gmin - SKIRT_M`, not
  `gmin - FOUNDATION_M`): with pads the base is the pad, and on any remaining slope the collider no
  longer stops 2.4 m above the visible skirt. No extra triangles (the wall quads just extend).
- Its lid is at the eaves (`base + min_h + spec.height`, spec.height now the P2.3 wall height), which
  is the drawn wall top; the roof stays render-only (the lean-collider design).
- Test `CollisionConsistencyTest`: lid = eaves, foot = skirt for a P2.3-capped pitched house.

Tests: test_buildings 102.

## Final step — after measurement (deferred at the user's request)

The after run is the same `tools/ram_drive.py` scenario as the 15.0 GB baseline. It spawns the
12-process server at full CPU for ~14 min; the user asked for ≤30 % CPU while the PC is in use, so it
is **not run yet**. The before number stands at **15.00 GB peak / 14.67 GB last-3-min median**; the
budgets now scale to ~567 MB/process here instead of 1550 MB, and the planner/worker/prune paths are
in. Re-run when the PC is free:

    python tools/ram_drive.py --label after --minutes 12 --out docs/overhaul/ram-after.csv

## Final status (12-step plan)

| step | status | commit |
|---|---|---|
| P1.0a environment + tests | done | (session) |
| P1.0b RAM baseline (15.00 GB peak, 14.67 GB median) | done | 0517dab |
| P1.1 cache budgets scaled by RAM share | done | 8b159ce |
| P1.2 planner byte budget + distance eviction | done | 4f95e97 |
| P1.3 Windows working-set trim | done | 8b159ce |
| P1.4 prune: cache shrink + trim + worker recycle | done | aad983f |
| P2.1 building pads | done | 207fc71 |
| P2.2 one building truth across LODs | done | cd9c89c |
| P2.3 honest heights and roofs | done | 6966069 |
| P2.4 lot pads + aisle rows | done | 8c3ec45 |
| P2.5 collision consistency | done | 4309fc4 |
| Final after-measurement + report | **measurement deferred** (CPU), report below | - |

**Tests on this branch:** test_budget 5, test_grading 54, test_buildings 102, test_parking 21,
test_roads 96, test_mapdata 63, test_imagery 43, test_seacarve 29, test_roadwidth 4, test_dem 12,
test_pools 6, test_blockworker 5, test_geotiles 14 (1 skipped), test_farfield 39, test_farglobal 12,
test_fargeo non-network 4, test_world_cache 11, test_revisit 10, test_plancache 14. The
`test_fargeo.RealDataTest` cases need Overture network/cache and are offline here.

**Not verified:** the actual RAM after number (same harness, warm cache) and all in-game visuals
(the user drives). P1/P2 code is committed locally on `qwen/ram-buildings`; nothing is pushed.


## Open risks

- Baseline run 1 was download-heavy (cold cache); run 2 (warm) is the fair reference.
- `Server.prune` recycling of road workers kills in-flight tiles (callers retry) — only safe at
  teleport/prune time, and only while the pool is idle.
- Windows working-set trim (P1.3) can add soft-fault latency after each trim; measure before
  keeping it on by default.
