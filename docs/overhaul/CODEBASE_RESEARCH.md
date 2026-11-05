# BeamNG.Earth 1.8.1-beta — codebase research and overhaul plan

Research snapshot: 2026-11-04. No gameplay code has been changed yet. This document is the shared
map for the overhaul: what the code does today, why the five reported problems happen, where to
change each one, and how the work is split between two parallel branches.

Working copies:

| Path | Branch | Purpose |
|---|---|---|
| `G:\Projects\BeamNG.Earth-1.8.1-beta` | `lomin/quality` | main working tree (roads / rail / tunnels) |
| `G:\Projects\BeamNG.Earth-qwen` | `qwen/ram-buildings` | worktree for the parallel agent (RAM / buildings / terrain / lots) |

`upstream` = `https://github.com/TecnaGamer/BeamNG.Earth.git` (fetch only; nothing is pushed).
Baseline commit: `c4453db Import BeamNG.Earth 1.8.1-beta`.
`main` is untouched at the same commit.

---

## 0. The five asks, one line each

| Ask | Root cause (detail in §4) | Fix shape |
|---|---|---|
| RAM reached ~15 GB (server alone) | 11–12 processes, each with its own multi-hundred-MB caches; one unbounded planner cache; no allocator trim on Windows | per-process budgets, per-cell eviction, teleport-scoped purge, Windows working-set trim |
| Buildings placed weird | No building pads anywhere: base = highest footprint corner; near vs far tiles use different ground and rules; heights guessed down | building pads in grading, one ground for all LODs, honest heights/roofs |
| Height maps / car parks | Same pad gap; lots are ungraded paint on sloped terrain; stall rows from a min-area rectangle | lot pads + aisle-following rows |
| Roads aren't really roads | Default `ROADS_IN_GROUND=1`: no carriageway mesh at all — one flat texture stretched across any width; lanes exist only as 1 cm paint ribbons | per-lane mesh strips with correct UVs + lane-aware paint |
| Train tracks need hitboxes | Rails and sleepers are render-only; collider is the ballast bed 0.28 m below the railheads; no rail nav/centreline data | railhead collision (or guided bed) + rail centreline/nav export |
| Underground / Channel Tunnel | Rail tunnels ≥ 60 m are dropped outright; `railway=subway` is rejected; no rail tunnel tube, hole or grading; Channel Tunnel absent entirely | rail tunnel profiling + tube + carves + portal ends; undersea policy |

---

## 1. What the mod is

BeamNG.Earth renders the real Earth inside BeamNG.drive. The game mod (Lua) talks over TCP to a
companion server (`earthd`, Python) that downloads public geodata (Overture/OSM roads and
buildings, DEM elevation, satellite imagery), builds meshes, heightmaps and textures on demand
around the player, and streams them into the level. The world is a flat ENU "bubble" around an
**anchor**; the mod moves the bubble as the player drives (rebases).

Repo shape (git-ignored `.claude/` holds the upstream author's dev notes and hook-injected rules):

```
mod/beamng_earth/            Lua mod: level, extensions (core, roads, geotile, rebase, ...), Vue UI
server/                      Python world server (~440 files, ~200k LOC): earthd entry point
server/tests/                unittest suites
native/earthfast/            Rust kernels shipped as server/earthfast.pyd/.so (abi3, VERSION 144)
tools/                       build/benchmark/probe tools (quality_gate, build_native, build_app, ...)
docs/                        ARCHITECTURE.md (1700+ lines), specs, research, this folder
launcher.py start.py install.py bmplaunch.py    app entry points
probes/                      in-game probe suite from early development
```

---

## 2. How it runs on this PC

- The run that produced the 2026-11-04 log is the **packaged app**: `G:\BeamNG.Earth-1.8.1-beta.exe`
  (PyInstaller one-file, ~57 MB). Its logs are in `%LOCALAPPDATA%\beamng-earth\logs\server.log`,
  its cache config in `%LOCALAPPDATA%\beamng-earth\app.json` (`G:\BeamNG.Earth Cache`).
- Source runs go through `install.py` (copies the mod into the user folder, makes `.venv`) then
  `start.py` (recommended flags: deshadow full, near-visual detail, airports/sea/trees/buildings/
  gas-stations on). A source run and the packaged exe are different code paths for the mod copy,
  but the same `server/` code.
- Game: `G:\SteamLibrary\steamapps\common\BeamNG.drive`. User folder: `D:\BeamNG User Folder\current`.
- **Python status (2026-11-04):** the system Python 3.14 at
  `%LOCALAPPDATA%\Programs\Python\Python314` is broken (`python314.dll` missing, `py` launcher
  points at it). Miniconda 3.13 works. A repo `.venv` was created from miniconda with
  `server/requirements.txt` installed, so tests run as:
  `G:\Projects\BeamNG.Earth-1.8.1-beta\.venv\Scripts\python.exe ...`
- No Rust toolchain (`cargo`/`rustc`) and no `luajit` on this PC. The bundled `earthfast.pyd` is
  what tests import. **Do not change `native/earthfast` from this machine** unless Rust is
  installed; keep server changes in Python or add a Python path guarded by `fast.require(...)` only
  if a compatible kernel already exists.
- The 2026-11-04 16:44–17:57 run (12 threads, 34 GB): 4 road-tile processes, 12 server processes
  total; repeated "road geotile still building after 75 s" and 90 s stall warnings; the far field
  ended with 1448 tiles queued; frame hitches 100–400 ms with one 3.5 s. This is the live
  evidence behind the RAM/throughput complaints — see §4A.

---

## 3. Architecture and data flow

### 3.1 Processes

`earthd` starts these (earthd.py:6890-6948):

| Pool | Count | Notes |
|---|---|---|
| block request (`LANE_REQUEST`) | 1 | physics blocks the mod asks for |
| block prefetch (`LANE_PREFETCH`) | 1 | predicted block ahead |
| photo (detail mode, ≥12 CPUs) | 1 | requested blocks' photos/stand-ins |
| far tile (`--workers`) | 3 | default, earthd.py:353 |
| road tile (`budget.road_processes()`) | 4 here | ≥12 threads and ≥24 GB → 4 (budget.py:190-197) |
| far building | 1 | far building tiles |
| **total** | **12** | each `blockworker.init` builds its **own** `Dem`, `MapData`, `SeaCarver`, `PlanStore`, `Surface`, `VehicleWidths`; only read-only files are shared via mmap |

The process count is the single biggest memory multiplier: every cache below exists once *per
process*.

### 3.2 Coordinate model

- A frame is an ENU plane at the anchor (`server/mapgeom.py`, `Frame.geodetic_to_enu`).
- **Tiles**: 256 m squares in the anchor frame (`roads.TILE`, roads.py:43), sent when their square
  comes within 640 m (`SEND_M`, roads.py:44), dropped at 960 m.
- **Physics block**: a square heightmap on the world-locked 2 m lattice (`heightmap.SQUARE`),
  1024² samples by default in terrain mode, 512² in mesh mode; it follows the player.
- **Geotiles**: anchor-independent content on the global Web-Mercator zoom-17 grid (~240 m at 39°),
  key `g17_x_y`. The server builds the content once in the tile's own frame; the mod places it into
  the current anchor's world with a rigid transform + height shear (`mod/.../geotile.lua`). This is
  what survives rebases.
- Far field: a dynamic-LOD quadtree of render-only `.cdae` tiles out to the horizon; horizon
  selection uses per-anchor coarse DEM block maxima.

### 3.3 Streaming pipeline (near tile)

1. The mod sends `pose`; the server works out anchor, block, tiles.
2. **Physics block** (block processes): DEM sample → `ground()` (sea carve, runway grading, …) →
   `grading.Grader.apply` under roads (adds `ROAD_LIFT`) → `roads.block_arch_raise` (soil over low
   arches) → `roads.block_tunnel_holes` (portal openings in the `.ter`) → `heightmap.generate`
   (quantised heights + material masks) → `terrain` message.
3. **Road tile** (`blockworker._tile_parts`, blockworker.py ~715-950): warm map-data cells, build
   bridges/tunnels profiles, gas stations, buildings, parking, props, rails, guardrails, sidewalks;
   `roads.build_tile` makes the chunks/meshes; `roads.write_tile` (roads.py:9215) writes
   `<kind>_<aid>_<tx>_<ty>.cdae` + `roads_<aid>_<tx>_<ty>.json` atomically.
4. The mod spawns the tile (`mod/.../roads.lua`), creating `DecalRoad` chunks only when no road mesh
   exists, plus the named shapes (`walk`, `road`, `deck`, `tunnel`, `rail`, `railcol`, `bld*`,
   `park`, `fuel`, ...). Colliders are spawned only near the player (`COLLIDE_M = {320, 2000}`,
   roads.lua:59-61) and held while the player is inside (`playerIn`, roads.lua ~1859).
5. Far tiles are independently built LOD meshes with their own texture; far buildings/skyline are
   separate streams; horizon nodes come from the DEM.
6. Rebases: anchor changes; geotiles are re-placed, anchor tiles rebuilt; session folders are
   adopted/pruned.

### 3.4 Protocol 4 (server/README.md "Protocol 4")

Length-prefixed JSON over TCP (127.0.0.1:47613 by default). Mod → server: `hello`, `pose`,
`prepare_terrain`, `options`, `session`, `cache`, `traffic_visuals`, ... Server → mod: `hello`,
`terrain`, `roads_tile`/`roads_skin`/`roads_exact`, `roads_drop`, `far_tile`/`far_drop`,
`far_bld`, `rail` data is inside the tile JSON, `status`, `weather`, `gravity`, `credits`, ...
Protocol changes must stay backward compatible or bump `hello.protocol` (see `test_protocol_agreement`).

### 3.5 Caches (RAM)

All are per process unless stated. Full audit in §4A.

| Store | Location | Budget today |
|---|---|---|
| DEM decoded tiles | dem.py `_lru` (~50, 228) | 512 × 512² float32 ≈ 512 MB |
| MapData cell layers | mapdata.py `_mem` (~183, 274) | 192 MB byte LRU |
| Sea carver rasters | seacarve.py `_mem` (~111) | 24 entries ≈ 228 MB |
| Road widths | roadwidth.py `walls`/`combos` (~50) | 48/32 entries, 100–160 MB |
| Height chunks | heightmap.py `ChunkCache` (~45) | 2 anchors × 64 MB |
| Imagery decoded / finished | imagery.py `_lru`/`_fin`/`_fin_rgb` | ~100 MB / 160 MB / ~74 MB |
| Deshadow / colour match | deshadow.py, colormatch.py | LRU; one unbounded dict |
| Grading plans | grading.py `_Planner.cells` (~1412), `PlanStore` (~3844) | **no per-cell eviction**; 6 region planners |
| Road facts | roads.py `_FACTS_MEM` (~403) | 640 entries ≈ 48 MB |
| Server tiles | earthd.py `tiles` | LRU 2500 (64 cells) / 6000 |
| Tile bytes / parts | earthd.py `piece_parts` | 24 entries ≈ 0.5–1 MB each |

Disk caches (plans, road facts, geotiles, bake packs) are separate; see `plancache.py`,
`roads.py` `FACTS_FORMAT`, `revisit.py`, `cache.py`.

### 3.6 Native Rust kernels

`native/earthfast` (abi3, `server/earthfast.pyd` VERSION 144) does the hot numeric work: road
grading (`grade_points`), tight bends, bridge slabs, geotiles, traffic routing, ... Python calls it
through `server/fast.py` (`fast.require(n, ...)`, `fast.MIN_VERSION`). New server logic is meant to
go here first (repo rule), but this PC cannot rebuild it; keep the Python fallbacks/ports intact.

### 3.7 Mod Lua

`mod/beamng_earth/lua/ge/extensions/beamngEarth/`: `core.lua` (connection, rebase, level hooks),
`roads.lua` (tile spawn, DecalRoads, collision near/far, `CONTENT_WHATS` at ~551),
`geotile.lua` (anchor-independent placement), `navgraph.lua` (AI routes), `parking.lua`,
`trees.lua`, `parking.lua`, `farfield.lua`, `rebase.lua`, plus the Vue settings UI
(`ui/ui-vue/mods/beamngEarth/*.vue`; check edits with
`node .claude/devtools/vuecheck/check.js ...` — devtools are not in this repo's tracked `.claude/`,
so ask before relying on them).

### 3.8 Tests and tools

- Python: `unittest`; full suite `tools/run_tests.py`; bounded gate `tools/quality_gate.py`
  (needs `lupa==2.8` for the Lua bridge tests and the bundled native kernel; CI pins Python 3.12).
- Lua: `lupa` bridge tests; Node for the Vue compiler check.
- In-game/dev tools: `tools/autodrive_perf.py`, `tools/mcp.py`, `tools/compat_check.py`,
  `.claude/devtools/*` (upstream author's, git-ignored).
- **Do not launch BeamNG.drive without an explicit request.** The user drives and tests.

---

## 4. Findings per ask

### 4A. RAM: why it reaches ~15 GB

Evidence: `budget.py:106-108` documents *"a desktop's server held 13-17 GB in its eleven
processes (1-1.5 GB a worker, mostly freed memory its 40-65 threads' arenas kept)"*. The user's log
confirms 12 processes on 12 threads / 34 GB.

Ranked causes (file:line):

1. **Per-process duplication (dominant).** DEM ≤512 MB × 12, MapData 192 MB × 12, RoadWidths
   100–160 MB × 12, SeaCarver ≤228 MB × 12, imagery ~160 MB in the server plus each far worker.
   Nothing is shared except read-only mmap files.
2. **`grading._Planner.cells` has no per-cell eviction** (grading.py ~1412; PlanStore ~3844,
   LRU of 6 planners). A planner keeps every cell it ever planned (0.1–5 MB each, up to 256 cells
   per region) until its whole region falls out. Every process has its own `PlanStore`.
3. **No allocator release on Windows.** `budget._malloc_small()`/`trim()` are glibc-only
   (budget.py ~125-155); `trim()` has no callers. Freed numpy arenas stay in RSS.
4. **Imagery pyramid duplication**: `imagery._fin`/`_fin_rgb` in the server and in each far worker;
   decoded LRU in the server.
5. Secondary: `roads._FACTS_MEM` ~48 MB/proc, `heightmap.ChunkCache` 128 MB/worker,
   `Deshadow._params` unbounded dict of ready regions.

Teleport behaviour: `Server.prune(keep_from)` (earthd.py ~4266-4311) drops anchors, tiles, height
maps, horizon grids; it does **not** touch DEM/MapData/plans/widths/sea/imagery, and nothing trims
the allocator afterwards.

Measurement: `BNGE_TRACEMALLOC=1` starts `tracemalloc` (earthd.py ~7045-7059, ~7524) and logs
`memory: rss <MB>, <MB> traced; top: file:line ...` every `TRACE_S` = 60 s (`TRACE_TOP` = 15) in
the server and in every worker (`memory pid ...`). The RSS−traced gap is native/allocator share.

Fix plan (ordered, measurable):

- **P0 — baseline.** Run a fixed scenario (same place, same drive, detail mode) with
  `BNGE_TRACEMALLOC=1`; log per-pid RSS every 30–60 s to a CSV; record before numbers. No code
  changes in this phase except the CSV logger if needed.
- **P1 — budgets.** Scale every per-process cache by `available GB / process count` instead of a
  fixed per-process number; cap `_Planner.cells` by bytes or count with LRU; trim the imagery
  finished pyramid per worker; add a Windows working-set trim (`SetProcessWorkingSetSize(-1,-1)`
  or `EmptyWorkingSet`) after a tile build and after a teleport — the Windows analogue of
  `malloc_trim`, only if measurement shows it helps.
- **P2 — lifecycle.** On rebase/prune, drop teleport-scoped caches (DEM, MapData, plans, widths,
  sea, imagery nearest-first) or at least shrink them; consider recycling a road worker after N
  tiles when its RSS is above a threshold (the pool already supports `recycle()` after a hung
  tile, earthd.py ~1944-2001).
- **P3 — process count.** Re-evaluate the defaults (`budget.road_processes`, `WORKERS`) against
  measured tile times; 12 processes is right for throughput on this 12-thread PC only if memory
  permits. Never trade away correctness or tile latency silently; measure.

Acceptance: measured total server RSS at steady state on the user's PC is materially lower than
the 13–17 GB baseline (target ≈ ≤8 GB for the 12-process layout, or ≤6 GB with fewer workers),
with tile build times within 10 % of baseline, no test regressions, no visual regressions.

### 4B. Buildings, heightmaps, car parks

Evidence: buildings audit (2026-11-04, whole-file read).

1. **No building pads.** `buildings.tile_buildings` (buildings.py ~2345, ground sampled ~2554-2593)
   reads the graded ground at the footprint corners; `base_z` returns `gmax` — the **highest**
   corner (buildings.py:1029; portal buildings use `gmin`). Walls are extruded from
   `gmin - SKIRT_M` (3 m) to `base + min_h + height` (~1539-1541). Nothing flattens the terrain
   under a footprint. On slopes the downhill facade floats and door/porch geometry bridges the gap;
   curved ground can poke through the floor.
2. **Near and far LOD disagree.** Near tiles use the graded ground (`g.apply(..., ROAD_LIFT)`);
   `blockworker.far_buildings_tile`/`skyline_tile` (~1616-1650) pass **raw DEM** into
   `buildings.far_items` (~3704) / `sky_items` (~3944). Far tiles also skip the near placement
   rules (`roads_xy`, `tunnels_xy`, `decks`, `water` are not passed), so the same building is a
   ground-level block far away and a lifted slab up close.
3. **Height/roof guesses.** Without `roof_shape` the roof is guessed gabled and the mapped height is
   reduced by `hw * tan(pitch)` (buildings.py ~599-651), so wide buildings lose metres and floor
   counts shrink. `roof_direction`/`roof_orientation` are dropped; pitched buildings are drawn on
   their oriented bounding box (`drawn_ring`, buildings.py:1036), outer ring only — courtyards and
   L-shapes are lost.
4. **Buildings a road crosses are lifted 5.5 m** into slabs (`OVER_ROAD_CLEAR_M`, ~2481-2496) rather
   than removed or split — reads as "building on the road".
5. **Grading contains no building or lot pads.** `Plan` is a road polyline + half width
   (grading.py:396); `_make_cell` reads only the `roads` layer (~1511-1523); `_Planner.apply`
   (~3721-3763) calls the Rust `grade_points` for road segments. Junction pads (`junction_pads`,
   ~1183) are the only non-road flattening. `airportgrade.py:89-100` is the template for an
   independent pad grader.
6. **Parking lots are ungraded paint.** `surface.paint_lots` (surface.py ~872) repaints the terrain
   material, `photo_lots` repaints the photo, stalls are draped `line_white` chunks
   (`parking.lot_markings` ~431). `_lot_rows` (~117-133) lays rows out from the lot's minimum-area
   rectangle, not the mapped aisles, so angled/L-shaped lots misalign. `lot_spots` (~136-181)
   samples the graded ground per stall centre.
7. **Collision** is a lean mesh (`buildings.collision_mesh` ~1603-1636) whose lid is flat at the
   eaves; pitched roofs are non-collidable; `collider_boxes` (~2713) uses an axis-aligned box to
   switch it off when "inside".

Fix plan:

- **Pads.** Add a pad concept to `grading`: load the `buildings` (and lot) layers in
  `_Planner._make_cell`, build a pad plane per footprint/lot (robust plane: fit corners, clamp to
  min/max, keep below adjacent road heights where relevant), add retaining/cut walls where the pad
  meets natural ground, consume pads in `_Planner.apply` after `grade_points`, and mirror in the
  physics block and `terrainpatch`. Bump `plancache.PLANS_FORMAT` (5 → 6) in the same commit and
  regenerate goldens (`BNGE_UPDATE_GOLDEN=1` for `tests/test_bake_formats.py`).
- **One building truth.** Make `tile_buildings`, `far_items` and `sky_items` take the same graded
  ground and the same placement rules (roads/tunnels/decks/water); remove the near/far visual jump.
- **Honest heights.** Use Overture `height`/`num_floors`/`min_height`/`roof_height`/`roof_shape`
  as given; only guess a roof when missing, and never below the mapped height; honour
  `roof_direction`; keep holes and multi-part footprints.
- **Lots.** Level lot pads in grading; lay rows from mapped aisles (`parking_aisle` is already a
  built road class and a grading plan); keep paint/markings on the flat pad.
- Acceptance: buildings sit on level pads with a plinth/retaining wall downhill; near/far match at
  the 640 m ring; parking rows align with aisles; screenshots vs real aerial/Mapillary; tests.

### 4C. Roads: "not really roads", rebuild by lane

Evidence: road audit (2026-11-04).

Current model:

- Default `ROADS_IN_GROUND=1` (roads.py ~209-212): `build_tile` **skips the carriageway mesh**
  (roads.py ~8362) and the road is painted into the ground: one asphalt terrain material
  (`surface.paint_roads`), flat photo tint (`photo_roads`), a second asphalt layer near the player.
- Markings are separate ribbons 1 cm above the surface (`roadmesh.LIFT["mark"]`), dashes cut
  geometrically (`markings.dashes`); lane lines only for five highway classes (roads.py ~198-200,
  ~4629-4641). Residential/service streets get none even when `lanes` is mapped.
- Width is heuristic (`carriageway` roads.py:1654: rule width → OSM width → `lanes × LANE_W` →
  class default, plus parking/edge strips), not a lane sum.
- When a mesh *is* built, each road is one ribbon of one material: `_chunk` (roads.py:5126) stores a
  scalar width and material; `roadmesh` maps u 0→1 across it; `tu` cross-repeat is only used for
  deck concrete. `road_asphalt_2lane` is an 8 m / 2-lane decal, so a 22 m six-lane motorway gets
  the same two-lane image stretched across it. The code comments acknowledge this.
- Per-lane tags available via `railtags.LANE_COLUMNS` and `roads.lane_tags` (~5113):
  `turn:lanes*`, `change:lanes*`, `overtaking*`, `lanes:forward/backward/both_ways` are consumed;
  `placement*`, `divider`, `lane_markings`, `bus:lanes`, `busway`, `hov:lanes` are not.
- Junctions/taper: `junctions.find`/`apply_cuts`, `roadcorners`, `_going_on`, `islands`,
  `taper_widths`, `shift_at`, `_strip_open_ends` already solve the hard geometry; a lane mesh must
  go through the same cuts.

Fix plan:

1. `lane_edges(spec, osm)` reusing `lane_layout` (roads.py:5102) + `markings.lane_plan`
   (markings.py:107) → integer lane boundaries and roles.
2. Emit one chunk per lane (or one ribbon with N+1 rows) using `offset_polyline` (roads.py ~4350,
   mitred) and `roadmesh.build`'s multi-row support + per-polygon `uvq` (~646) and `tu` (~606).
   Use a **one-lane-wide** asphalt texture (new material or a u-range slice of the 2-lane decal) so
   each lane repeats instead of stretching.
3. Feed the same lane geometry to `surface.paint_roads`/`_road_segments`, so ground paint and mesh
   agree.
4. Draw lane lines from the same lane edges for every class when lanes are mapped; keep the
   existing dash/solid logic.
5. Reconcile with junction cuts, tapers, decks, tunnels and geotile skins; add tests
   (`test_roads.py::InGroundTest`, `test_motorway_lanes_rural_two_urban_three`,
   `test_cobblestone_strips_no_paint`, `test_roadmesh.py`, `test_markings.py`).
6. Keep `ROADS_IN_GROUND` as an option; the lane mesh is the new default only after it beats the
   paint in-game.

Risks: triangle count and spawn cost (more chunks), z-fighting at lane seams, junction double
cover, and material authoring (UV/repeat). Measure on the Steam Deck-class target (30 fps) if
available; otherwise on the user's PC.

### 4D. Rail collision and train support

Evidence: rail audit (2026-11-04).

- Tracks are built per road tile (`railways.tile_mesh`, railways.py ~1225) as ballast, deck slabs,
  piers, sleepers and rails. `roads.write_tile` (roads.py:9215) writes `rail_*.cdae` **and** a lean
  `railcol_*.cdae` (`railways.collision_mesh`, railways.py:1334) with `collision_only=True`.
- `RENDER_ONLY = STEEL_WORN + SLEEPER_WORN` (railways.py:1331): rails and sleepers never collide;
  the collider is the ballast top, 0.28 m below the railheads. Comment: "A car on the track now
  drives on the ballast's top".
- Mod side: `rail` is marked render-only when `railcol` exists and `railcol` gets
  `collisionType 'Collision Mesh'` (roads.lua ~1779-1780); both spawn only within `collideNear`
  (320–2000 m, roads.lua:59-61, ~1128-1139); `collider_boxes` (roads.py ~9342) has no rail hold
  box, so a bed can pop in under a vehicle.
- Geometry: gauge from OSM (`railways.gauge` ~169), rail strips ±(gauge/2 + 0.035), railhead at
  bed+0.58 m; sleepers 0.6 m spacing; bed height = ground smoothed over 10 m; decks and approaches
  handled (`deck_heights`, `approach_levels`).
- No rail nav data: `navgraph.py` is Overture-road only; track geometry exists only inside the tile
  mesh; no centreline/waypoint export, no `train`/rolling-stock code.

Fix plan:

- **Collision to the railhead.** Add narrow collision strips at the railheads (or a low
  railhead-top surface) to `collision_mesh` while keeping the ballast deck; keep the triangle
  budget bounded (rails were ~88 % of a track's triangles — use long, lean boxes/strips, not the
  render geometry; the collider can be 2–4 quads per metre pair). Add rail hold boxes to
  `roads.collider_boxes` and honour `playerIn` for rail.
- **Guidance/export.** Emit a rail centreline + gauge + direction (a `rail_path` in the tile JSON or
  a sidecar) and route data so a train mod can follow the track; add rail edges to the nav data or
  a separate rail graph. Keep it deterministic and cached like road facts.
- **Trains.** The mod's stock vehicles can drive the bed today; a rail vehicle needs guidance
  (physics or Lua constraint). Decide with the user whether the target is "drive a car on the
  track" (collision only) or "run a train mod" (guidance + couplers). The Channel/London work in
  4E assumes the latter.
- Acceptance: a car cannot fall through rails/sleepers; a train mod can follow a straight test
  track and a curve at speed without derailing; no measurable frame-time regression when rail
  collision is active; tests for the collider triangle count and hold boxes.

### 4E. Tunnels: road tunnels, underground rail, Channel Tunnel

Road tunnels already exist:

- Overture `flags`/`level` → `roads.level_ranges` (~343) → `tunnel_specs` (~2720) →
  `TunnelProfiles` (~6127) → `tunnel_sections` (~6090) splits each tunnel into open road / low arch
  / full tunnel from ground cover → `_tunnel_chunks` (~7638) emits `tunnel` chunks with `portal`,
  `tunnel_h`, `mouth`; `twin_tunnels` (~7596) merges side-by-side bores.
- `roadmesh.tunnel` (~1651) wraps the chunk in an inward-facing concrete tube + portal frames; the
  floor comes from `deck`; `blockworker._tunnel_holes` (~1579) → `roads.block_tunnel_holes`
  (~6962) / `tunnel_hole_mask` (~6830) cut terrain only where ground lies between floor and vault;
  `block_arch_raise` lifts soil over low arches. The tunnel mesh is colliding and spawned by the
  mod (`CONTENT_WHATS`, roads.lua ~551).

Rail tunnels are missing:

- `railways._pieces` (railways.py:572) drops every tunnel range **≥ 60 m**
  (`SHORT_TUNNEL_M`, railways.py:509); shorter ones are drawn on the ground (they are usually road
  overpasses). `railways.parts` documents "tunnels left out". `grade_plans` only takes `ground`
  parts.
- The rail tag shard contains `bridge/tunnel/layer`, but `server/railtags.py` joins only
  `railway, gauge, electrified, service, usage` (railtags.py ~30) — tunnel/bridge for rails comes
  from Overture flags instead. `railway=subway` is rejected (`railways.DRAWN`, ~31; `drawn`, ~527).
- Consequences: London Underground (subway) is never drawn; London Overground has 60 m+ gaps at
  every dive; the Channel Tunnel has **nothing** at all — no track, tube, collider or terrain
  change; the seabed is plain DEM.
- There is no rail tunnel hole carving (`_tunnel_holes` is road-only) and no undersea guard on rail
  grading (`TunnelProfiles.wet` is road-only), so a mis-tagged undersea line could be graded into
  the seabed.

Fix plan:

1. **Rail tunnel profiling** — a rail analogue of `TunnelProfiles`/`tunnel_sections`: read
   tunnel/bridge/layer from the rail shard (extend `railtags.py` and the weekly shard builder), keep
   long tunnel ranges instead of dropping them, and build a bore (tube mesh + portal) either
   above ground (mountain) or below it (underground).
2. **Terrain** — extend `_tunnel_holes`/`block_tunnel_holes` to rail bores; grade rail tunnel
   floors only inside the bore; never grade the open seabed; add a wet/undersea policy like
   `TunnelProfiles.wet`.
3. **Underground stations** — London Underground needs station boxes/portals only if the user wants
   them; start with continuous bore + trains, no stations.
4. **Channel Tunnel** — the 50 km undersea case needs a different scale of solution: do not build
   200 × 256 m tiles per bore per direction, 4 bores; instead a special long-tunnel mode that emits
   long procedural tube sections with coarse LOD and a single collider, entered at the Folkestone
   and Calais portals. The mod keeps the tube loaded like a level, not like streamed map tiles.
   Rail height must come from the tunnel profile, never from the seabed DEM.
5. **Mod** — spawn rail bores/tubes with collision, add a portal transition (like `core.lua`'s
   floor check ~843), and make sure `collideNear`/`DRIVE_FIRST_M` do not unload the bore around the
   player inside a long tunnel.
6. **Acceptance** — London Overground continuous through dives; a continuous Underground line;
   drive a train mod through the Channel Tunnel from Folkestone to Calais without gaps or falling;
   no frame-time blow-up; tests for profile/hole/portal data.

---

## 5. Invariants, formats and traps

- **Plan/facts formats are content keys, not code hashes.**
  `plancache.PLANS_FORMAT` (plancache.py:64) and `roads.FACTS_FORMAT` (roads.py:439) must be bumped
  in the same commit as any change that alters grading plans or road facts (pads, lane edges,
  heights, widths, flows, separations). Then run the bake tests with `BNGE_UPDATE_GOLDEN=1` and
  commit the new goldens. Pure speed changes keep the format only if byte-identical output is
  proven.
- **Never leave two implementations.** New server logic in Rust (`native/earthfast`) per the repo
  rules; but this PC has no Rust toolchain and the shipped abi3 module is version 144. If a change
  needs a new kernel, install Rust or hand the native part back to the user; do not add a Python
  twin "for now" without deleting the other later.
- **Version/ABI:** `fast.require(n, ...)`, `fast.MIN_VERSION`; bump `VERSION` in the Rust module and
  `fast.MIN_VERSION` together if a signature changes. `earthd` refuses to start below the minimum.
- **Determinism:** tile/plan output must be deterministic across processes and runs; all caches use
  content hashes. Do not introduce per-run ordering.
- **Cache sharing:** never point a test/benchmark scratch cache at the real
  `G:\BeamNG.Earth Cache` through links or copies; use a fresh folder (a symlink farm once evicted
  ~7 GB of the user's cache).
- **Security:** treat every PR/patch from outside as untrusted: read the whole diff for network
  calls, downloads, exec/eval/subprocess, base64 blobs, file deletion, credential reads, CI/
  installer changes, binaries, lockfiles. Do not run it; report to the user first.
- **Publishing:** never push `main`, never bump `VERSION`, tag or create releases without the user's
  explicit go. No push at all before the user has tested the change in game.
- **Do not launch or kill BeamNG.drive** unless the user explicitly asks; the user drives. The Deck
  (if used) is a shared Steam account; only one Steam-started game at a time.
- **Windows:** no `pkill -f`/`pgrep -f` with a pattern that matches your own command line; use
  brackets (`pgrep -f "[e]arthd"`). `malloc_trim` is glibc-only.
- **Units:** report speeds/distances to the user in mph and miles.

---

## 6. Roadmap and work split

Phase order is chosen so RAM is fixed before the quality work makes tiles heavier and the two
branches never block each other.

| Milestone | Branch | Deliverable | Acceptance |
|---|---|---|---|
| M0 | both | this doc + handoff prompt + branches + working `.venv` | docs committed; tests import |
| M1 | `qwen/ram-buildings` | RAM baseline CSV + budget/lifecycle fixes | measured RSS reduction, tile times within 10 %, tests green |
| M2 | `qwen/ram-buildings` | building pads, one near/far ground + rules, honest heights, lot pads/rows | `PLANS_FORMAT` 6 + goldens; visual QA screenshots; tests |
| M3 | `lomin/quality` | per-lane road mesh + lane-aware paint + markings | new lane tests; in-game comparison; no junction/taper regressions |
| M4 | `lomin/quality` | railhead collision + rail centreline/nav export + hold boxes | car-on-track and train-follow tests; no fps loss |
| M5 | `lomin/quality` | rail tunnels/underground + Channel Tunnel long-tunnel mode | London and Channel acceptance; tests |

Merge order: M1/M2 into `lomin/quality`, then M3–M5. Expect conflicts in `blockworker.py` and
`earthd.py`; keep each batch small and commit per batch. A `docs/overhaul/HANDOFF.md` per branch
records touched files, open risks and test commands.

---

## 7. Open questions (decide with the user)

1. Train target: drive a car on the bed (collision only), or run a train mod with guidance and
   couplers? M4/M5 scope depends on this.
2. Visual target for roads: lane mesh as the new default, or an option beside the painted ground?
3. Building interiors/stations: needed for the London/Channel experience, or exterior only?
4. Which real places are the acceptance benchmarks? Suggested: a UK motorway junction (lane mesh),
   London Overground dive, a Channel Tunnel portal, a US hillside neighbourhood (pads), a big box
   store car park (lots).
5. Can the user rebuild the native Rust module, or should all work stay in Python until they can?

---

## Appendix A — key files

| Area | Files |
|---|---|
| server entry / pools / protocol | `server/earthd.py`, `server/pools.py`, `server/xproc.py`, `server/peerwork.py` |
| blocks / tiles | `server/blockworker.py`, `server/heightmap.py`, `server/terrainpatch.py`, `server/geotiles.py` |
| grading | `server/grading.py`, `server/plancache.py`, `server/roadwidth.py`, `server/roadcorners.py`, `server/junctions.py` |
| roads | `server/roads.py`, `server/roadmesh.py`, `server/markings.py`, `server/carriageways.py`, `server/medians.py`, `server/surface.py` |
| buildings / lots | `server/buildings.py`, `server/parking.py`, `server/gasstations.py` |
| rail | `server/railways.py`, `server/rails.py`, `server/railtags.py`, `server/crossings.py`, `server/fra.py` |
| tunnels | `server/roads.py` (tunnel profiles/holes), `server/roadmesh.py` (`tunnel`), `server/blockworker.py` (`_tunnel_holes`) |
| memory | `server/budget.py`, `server/mapdata.py`, `server/dem.py`, `server/seacarve.py`, `server/imagery.py`, `server/deshadow.py`, `server/colormatch.py`, `server/farfield.py` |
| mod | `mod/beamng_earth/lua/ge/extensions/beamngEarth/*.lua`, `mod/beamng_earth/ui/ui-vue/mods/beamngEarth/*.vue` |
| tests | `server/tests/*.py`, `mod/tests`, `tools/run_tests.py`, `tools/quality_gate.py` |

## Appendix B — commands

```bash
# source run (needs .venv; do not overwrite the user's installed mod without asking)
cd G:/Projects/BeamNG.Earth-1.8.1-beta
.venv/Scripts/python.exe install.py --dry-run
.venv/Scripts/python.exe start.py

# tests
.venv/Scripts/python.exe tools/run_tests.py            # full suite
.venv/Scripts/python.exe tools/quality_gate.py --par 3 # bounded gate
.venv/Scripts/python.exe -m unittest discover -s server/tests -p "test_roads.py"

# memory tracing (server and every worker)
set BNGE_TRACEMALLOC=1
.venv/Scripts/python.exe start.py -- --verbose

# git
git -C G:/Projects/BeamNG.Earth-1.8.1-beta status -sb
git -C G:/Projects/BeamNG.Earth-1.8.1-beta worktree list
```

## Appendix C — evidence trail

- User report and session log: `%LOCALAPPDATA%\beamng-earth\logs\server.log` (2026-11-04
  16:44–17:57).
- Four subsystem audits (RAM, buildings/terrain, rail/tunnels, roads/lanes) were run on
  2026-11-04; their findings are condensed in §4 with the file:line anchors above. Verified
  anchors: `roads.TILE` (43), `roads.carriageway` (1654), `roads._chunk` (5126), `roads.build_tile`
  (8260), `roads.write_tile` (9215), `grading.Plan` (396), `grading._Planner._make_cell` (1511),
  `budget._malloc_small` (~125), `earthd` pools (6890-6948), `railways.RENDER_ONLY` (1331),
  `railways.collision_mesh` (1334), `railways.SHORT_TUNNEL_M` (509), `railways._pieces` (572),
  `buildings.base_z` (1029), `buildings.drawn_ring` (1036), `buildings.collision_mesh` (1603),
  `plancache.PLANS_FORMAT` (64), `roads.FACTS_FORMAT` (439).
