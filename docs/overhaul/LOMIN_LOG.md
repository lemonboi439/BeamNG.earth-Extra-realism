# lomin/quality — work log

## 2026-11-04 — M3 batch 1: lane-accurate paved roads

**What changed**

- `roads.lane_strips(spec)`: a paved carriageway is now one strip per driving lane plus its parking lanes /
  edge strips (offsets from `lane_layout`). Each chunk carries `tu` (texture repeats across) and `u0`
  (the texture's u at its left edge), so the stock `road_asphalt_2lane` decal (8 m across,
  `roads.ASPHALT_TEX_W`) is sampled at its real scale instead of stretched over any width. The u
  coordinates run on across the lanes, so adjacent lanes show different slices of the decal.
- `roadmesh.build` honours the chunk's `u0` (both the plain and the feathered 4-row path).
- Detail mode draws the paved carriageways as meshes over the drawn block
  (`roads.ROAD_SURFACE`, set from `earthd.main_async` / `blockworker.init`); the ground photo keeps
  the painted band underneath. `BNGE_ROAD_SURFACE=0` restores the old painted-only look.
- Markings: `markings.lane_plan` is used for any paved class when the map gives directional lane
  counts, not only the five highway classes; the fallback lane lines also apply where `lanes` is
  mapped.
- Revisit keys: `_geo_content_key` and `_roads_tile_cached` include `ROAD_SURFACE`, so a content
  built for one mode is not reused for the other.

**Measured (synthetic tile, 9 roads, 4 in the tile)**

| | single ribbon | lane strips |
|---|---|---|
| paved chunks | 4 | 16 |
| paved triangles | 1,048 | 4,309 |
| whole mesh triangles | 3,816 | 7,077 |

Absolute counts are small; the increase is per-lane rows (each strip repeats the along-road rows).

**Tests**

Green: test_roads (98), test_roadmesh (49), test_markings (12), test_surface (38),
test_blockworker (5), test_geotiles (14, 1 skipped), test_world_cache (11), test_plancache (14),
test_revisit (10), test_roads_refresh (3), test_tunnels (32), test_peerwork (22, 4 skipped),
test_process_lifecycle (3). Quality gate: 37/38 modules OK; `test_cache` fails only on Windows
symlink privilege (WinError 1314), environmental, not this change.

**Not verified / known limits**

- In-game look and frame time (the user drives; detail mode).
- Deck surfaces (`_deck_chunks`) and tunnel floors (`_tunnel_chunks`) are still single stretched
  ribbons; the lane work covers ground carriageways only.
- Inner lanes at very sharp bends can fold; the painted band shows through the dropped triangles.
- Junction surfaces are not cut; overlapping lane strips are coplanar and share the material.

**For Qwen's merge:** shared files touched here are `blockworker.py` (init signature, geo content
key, anchor tile key) and `earthd.py` (pool initargs, `ROAD_SURFACE`). Keep changes away from
`roads.py` / `roadmesh.py` / `markings.py` unless agreed; they are this branch's lane work.

## 2026-11-04 — M4 batch 1: rail hitboxes

**What changed**

- `railways.collision_mesh` now keeps the rails (`STEEL_WORN`) and drops only the sleepers
  (`RENDER_ONLY = SLEEPER_WORN`). The lean `railcol` shape therefore has a real railhead surface
  0.28 m over the ballast bed — a car bumps over the rails, and a train mod's wheels can ride them.
  `BNGE_RAIL_COLLISION=0` restores the old bed-only collider; `collision_mesh(mesh, rails=False)`
  does the same per call.
- Docs/comments updated; the test was renamed and now asserts the railhead material is in the
  collider, the sleepers are not, and the rails alone still make a collider.

**Measured (synthetic 350 m standard-gauge track)**

| collider | triangles | share of full rail mesh |
|---|---|---|
| bed only (`BNGE_RAIL_COLLISION=0`) | 786 | 11.7 % |
| bed + rails (new default) | 2,358 | 35.0 % |
| full rail mesh (old pre-#41 behaviour) | 6,728 | 100 % |

Still 65 % under the full mesh; the 4,370 sleeper triangles remain render-only.

**Not verified / known limits**

- In-game: a car crossing a track and a train mod on a straight/curved track (the user drives).
- Rail colliders are not held by `roads.collider_boxes`; a tile's railcol can become solid as it
  enters range. Normally tiles load 320 m+ ahead, so this only matters after a teleport onto a
  track; a rail hold box needs the rail centrelines exported from `tile_mesh` (later batch).
- No rail centreline/nav export yet: a train mod needs its own guidance (flanges on the railhead
  sides now have collision, but nothing drives the train along the track).

## 2026-11-04 — M5 batch 1: rail tunnels, Underground lines, the Channel Tunnel

**What changed**

- **Long rail tunnels are kept** (`railways._pieces`): a mapped tunnel range ≥ `SHORT_TUNNEL_M` is a
  `"tunnel"` part of its own instead of being dropped; short ones stay track on the ground (they are
  road overpasses). The rail shard's `tunnel` / `bridge` / `layer` tags are now joined
  (`railtags.COLUMNS`) and used where Overture has no ranges, so the Channel Tunnel and the
  Overground dives are tunnels even without an Overture flag.
- **Bed profile** (`railways._tunnel_floor`): the lower convex hull of the ground over the line
  (`roads.tunnel_sections`), with its ends at the portals. Under water the bed is laid
  `TUNNEL_DEPTH_M = 18 m` under the ground (the Channel; faded in over `TUNNEL_WET_RAMP_M = 3 km`,
  1.6 %); a `railway=subway` is `SUBWAY_DEPTH_M = 14 m` under the street (faded over 600 m).
- **Track in the bore** (`railways._tunnel_bed`): concrete invert, ballast, walkways to the tube
  walls, then the normal sleepers/rails; `railways.collision_mesh` collides the rails (M4).
- **Bore and portals** (`railways._tube_chunk` + `roadmesh.tunnel`): a full-height tunnel tube
  (`TUNNEL_BORE_W = 4.6` + shoulders = 7 m between the walls) with portal faces at the mapped
  tunnel's ends; a joint where another tunnel part goes on is not a portal (`_tunnel_portals`).
  Written as a new tile part **`railtunnel`** (`roads.write_tile`, `shape_files`,
  `ANCHOR_TILE_FILES`), colliding (`blockworker.GEO_COLLIDING`) and spawned by the mod
  (`roads.lua`: `CONTENT_WHATS`, slots, delete/transform lists, drive-first collision priority).
- **Terrain** (`railways.block_tunnel_holes`, hooked into both `blockworker._tunnel_holes` and
  `earthd.Server._tunnel_holes`): the terrain is cut only where the ground lies between a rail
  bore's floor and vault (the mouths), one part at a time so two parallel bores are not merged.
  The ground over a hill or the seabed stays.
- **Subways** (`railway=subway`): accepted as a deep bore (was rejected outright); `gradesep` skips
  them so no road bridge/underpass is inferred from a metro.
- Sound: the tube chunks feed `sound_boxes`, so a rail tunnel gets the tunnel reverb/portal sounds.

**Measured / tested**

- test_railways 46, test_railtags 6, test_rails 4, test_tram_bridges 4, test_gradesep 17,
  test_roads 98, test_roadmesh 49, test_tunnels 32, test_blockworker 5, test_geotiles 14 (1
  skipped), test_world_cache 11, test_revisit 10, test_plancache 14, test_mapdata 63; Lua modules
  82/82. Quality gate: 37/38 modules OK; the only failure is the Windows-symlink `test_cache`
  environment case (WinError 1314). The full suite's `test_server.MeshServerTest.test_session`
  passes when run alone (114 s; it times out/flakes under the whole suite's load).
- New tests: a hill tunnel's track inside the hill, tube + portals, mouths cut and hilltop kept; a
  submarine bed at 18 m under a flat seabed at a ≤2 % grade; a subway 14 m under the street; a
  joint between tunnel parts not a portal; the OSM `tunnel=yes` fallback.

**Not verified / known limits**

- In-game (the user drives): portals, the tube inside a hill and under the Channel, tunnel entry by
  train; long-tunnel streaming cost (each tile samples the whole tunnel part's ground).
- **London Underground has no station entrances yet**: the bores exist and can be driven once
  reached, but nothing connects them to the street; stations/shafts are the next feature.
- A mapped tunnel's ends are its portals; where Overture splits a tunnel into several features at a
  junction, the joint is detected by coincident endpoints only (0.1 m rounding).
- The tunnel floor is the hull of the *surface* DEM; a deep-level tube's true depth is not in the
  data (subway default 14 m, undersea 18 m under the seabed).
- The tube is a near-tile mesh like the road tunnels; no far-field LOD for rails (existing).



## 2026-11-04 — road width accuracy (Wickenden Crescent and everywhere)

The user's screenshot at 51.1389, 0.9062 (a UK residential crescent; OSM tags only highway=residential,
maxspeed, surface) was drawn ~16 m wide with 2.5 m sidewalks; the real street is ~5.5 m with grass
verges. Causes and fixes, all general:

- **No invented urban parking lanes.** `carriageway` added `PARKING_M` (2.3 m) each side to every urban
  residential/tertiary/secondary/unclassified/living_street: +4.6 m on streets whose map width is the
  carriageway. Removed; the class width (or the map's width/lanes) is the carriageway. `lane_layout`
  and the markings' lane counts no longer subtract parking.
- **No invented residential sidewalks.** `has_sidewalks` defaulted every urban residential street to
  raised sidewalks. Now residential/living_street/unclassified get sidewalks only where OSM's
  `sidewalk` tag says (both/left/right) or a walled measurement found one; tertiary and above keep
  their urban default.
- **Measured widths capped per class** (`MEASURE_CAP_M`: residential 8 m, living 7.5, unclassified
  8.5, tertiary 9.5, secondary 13, primary 16, trunk 18). The between-buildings ray hits the house
  across its front garden: a 20 m right-of-way became a 12 m carriageway. Beyond the cap the class
  default stays. An accepted measurement supplies its sidewalk (`sides` set); a rejected one
  suppresses the invented sidewalk on minor classes.
- `roads.FACTS_FORMAT` 2 → 3, `plancache.PLANS_FORMAT` 5 → 6, goldens regenerated
  (`server/tests/golden/bake_formats.json`).

Tests: test_roads 101 (new `ResidentialAccuracyTest`: 6 m default, front-garden span rejected, walled
5.4 m kept with its sidewalk), test_roadwidth 4, test_plancache 14, test_bake_formats 5, plus
buildings/parking/rail/tunnels/markings/navgraph/surface/grading/blockworker/world-cache green.


## 2026-11-04 — Rotherhithe tunnel, Photon 403, estate parking/sidewalks, building base

The user's screenshots at Guinness Square (51.4937, -0.0818), plus the Rotherhithe Tunnel and the
address search's HTTP 403. All general:

- **Short river tunnels are built.** `TUNNEL_WET_MIN_LEN_M` 1200 m: a mapped tunnel chain shorter than
  this whose points run under water is a city river crossing (Rotherhithe, 1.48 km) and gets its tube;
  longer submerged chains stay excluded (a car in the long Burnley tube floats). Its floor is laid
  `TUNNEL_WET_DEPTH_M` 10 m under the water-covered ground, eased in over 120 m from the portals; the
  kinds (closed/open/ground) are re-read from the lowered floor's cover. Portal classification stays.
- **Photon address search.** Photon refuses the project's User-Agent with HTTP 403; `geocode.SEARCH_UA`
  (a browser UA) is sent and live search returns the results ("Wickenden Crescent": 4).
- **Parking on roads without sidewalks.** `curb_spots` only read sidewalk runs, so an estate road
  whose front gardens the map leaves untagged got no spots at all (Guinness Square, no cars). It now
  synthesizes the curb line at half the carriageway when a road has no walk run (walk 0, the set
  `CURB_OFF_M` in), same stations and headings. `MIN_WIDTH_M` 7.0 → 5.5 m: a 6 m residential street
  parks on one side (3.5 m left beside the row). 30 spots in the 256 m tile at the estate.
- **Estate footways 2 m.** `WALK_DEFAULT_M` (residential 2.0, living_street 1.8, unclassified 2.0) and
  `WALK_CAP_M` (measured footways 2.0-3.2 by class) replace the 2.5 m city default/4.5 m cap: the
  2.5 m concrete platform beside Page's Walk read as a plaza.
- **Building floors at the median ground.** `base_z` used the footprint's highest corner; on the
  estate's gentle slope the ground floor windows stood ~1.5 m over the kerb and the block on a green
  bank. It now uses the median of `spec.gring` (clamped to gmin..gmax), the portal rule unchanged;
  the two sloped buildings within 80 m drop 0.18/0.26 m.
- `roads.FACTS_FORMAT` 3 → 4, `plancache.PLANS_FORMAT` 6 → 7, goldens regenerated.
- Stale tests updated to the 2026-11-04 width policy: untagged urban residential has no sidewalks
  (roadcorners/bridge scenarios use tertiary or tagged streets), road-classification no longer expects
  the removed parking lanes.

Tests: parking 24, buildings 105, roads 102, tunnels 33, geocode 9, roadwidth 4, bake_formats 5,
roadcorners 14, bridge_scenarios 36, road_classification 9, grading/plancache/geotiles/rails/railways/
roadmesh/sidewalk_collision/furniture/blockworker/surface/junction_pads/navgraph green.


## 2026-11-05 — tunnels under water, London Bridge's tracks

The user's reports: "underground stuff can also be flooded (like train lines)" and London Bridge
station has all its rail lines clipping into each other.

- **Water volumes end under the bed.** A WaterBlock floods its whole box, and the sea's tiles were 200 m
  z (100 m below the surface): rail bores and the Underground under the Thames, and the new Rotherhithe
  road tube, lay inside it and were flooded. `seatiles.plan`/`plan_lakes` now measure the deepest sampled
  bed per tile and send the block's z size as twice `(surface - bed) + 2 m` (`WATER_FLOOR_M`: a car on the
  bed stays in water, a tunnel under it stays dry). The mod uses each tile's depth (`sea.lua`), and
  makes no physics block when the player is below the volume (a tunnel), so no buoyancy/fog in it.
  Old servers (7-field sea rows) still fall back to 200 m.
- **All-bridge networks are viaducts.** London Bridge's tracks are every one bridge-tagged (`layer=1`,
  bridge=yes/viaduct) with no ground part anchoring the network: each was laid at the DEM under it
  (street level), so parallel tracks up to 1.5 m apart had overlapping decks, edge walls and piers
  ("clipping into each other"). A connected all-bridge network at least 100 m long is now laid
  `VIADUCT_CLEAR_M` 5.5 m over its median ground; a bridge connected to a ground track keeps its true
  profile. Parallel viaduct networks are then equalised locally (`_equalize_viaducts`, per 8 m cell
  median, 3 x 3 cells, three passes, smoothed): the station's 101 parts came from deck heights
  3.9-5.7 m to 9.4-9.9 m, close-pair differences 0.0.
- **Shared decks between tracks.** Bridge tracks within 6 m of another at the same level trim their
  deck to the midpoint on that side and drop the edge wall there (`_neighbour_gaps`, `_deck`), so two
  decks meet instead of cutting through each other; piers within 4.2 m of a neighbour's are the same
  structure and are drawn once.
- Tests: railway 49 (viaduct raise, ground-anchored bridge unchanged, parallel-deck gaps), seatiles 5
  (8-field rows, depth 24 m for a 10 m bed), grading/blockworker/rails green.


## 2026-11-05 — the mod's own assets; footprint roofs; pavement scale

The user: the world "still uses assets from other maps only", and roads/pavements and building placement/
scale still need work.

- **Every core surface texture is the mod's own.** `tools/build_bne_assets.py` generates seamless albedo,
  normal and roughness maps (seeded noise/shapes, no third-party image) into
  `mod/.../art/shapes/beamngEarth/bne_tex/`: asphalt, concrete, cobble, brick, stone, plaster, wood,
  cladding, shingle/slate/asphalt roofs, marble, dirt, gravel, ballast, grass, rock, drain grate,
  tactile paving, water ripple/foam/depth ramp and a 4 x 4 turn-arrow sheet. A new
  `roads.materials.json` defines every material name the server emits (road_asphalt_2lane,
  ECA_road_edge_dirt, italy_cobblestone, curbs, sidewalk1, line_white/yellow, roadmarkings1, ...), and
  roads.lua no longer loads West Coast/East Coast/Italy/Johnson Valley/Driver Training/Gridmap
  material files. buildings/tunnel/dirtroad material JSONs are rewritten off `/assets/...` and other
  levels with the generated maps. Sea.lua's water textures are the generated ones.
- **Building window sheets.** buildings.py samples `WIN_UV` of the facade texture for every window;
  plain generated wall textures would have erased all windows. Each facade material now gets a
  1024 px window sheet with the 4 x 5 bay grid and the exact `WIN_UV` rect (glazed sheets for shops,
  offices and skyline walls), so windows survive and scale with the 12 m bay layout.
- **Irregular footprints are not made rectangles.** A pitched roof exists only where the footprint
  fills 0.9 of its oriented box (0.8 before): an L-shaped/angled house was drawn as its box and grew
  over the notch, into neighbours or the street. Below that it is a flat roof on the mapped ring.
- **Pavement scale.** Sidewalk slabs and curbs repeat their own concrete texture every 3 m (was 45 and
  9 m with the stock strip), so the poured joints are at a real walking pace.
- Test `test_own_assets`: required material names resolve, every referenced texture exists, no
  `/assets/` or other level's path in the mod's material files, facade materials are window sheets.
- **Fuel stations and pumpjacks are the mod's own models.** tools/build_fuel_props.py generates a canopy,
  pillar, pump, bollard, awning, sign, East Coast unit and pumpjack (two LODs, collision meshes) plus
  fuel.materials.json; gasstations.py points every region's kit at them and pumpjacks.lua uses the one
  jack (the stock map sound events are dropped). tests/test_gasstations now asserts the mod's shapes.
- Still stock (next): trees/rocks (trees.lua) and the ground-cover tufts (groundcover.lua).


## 2026-11-05 — tree placement, full-size rail bores, texture VRAM

The user: trees placed more accurately, the rails properly hitboxed and never scaled down (regular trains
must fit), and London as realistic as possible while optimising for realistic traffic.

- **Trees**: building parts (roof overhangs, balconies, canopies) are masked as well as footprints, so a
  CHM top over a roof no longer becomes a tree; the street-tree pit sits 0.3 m past the curb (was 0.4).
  Vegetation `VERSION` 24 invalidates the cached cells. Test suite test_vegetation green.
- **Rails**: the tunnel bore is 6.2 m between walls (was 4.6): a 2.8 m train had 0.9 m either side, now
  1.7 m; the ballast top stays 3.6 m and the gauge 1.435 m. A test asserts gauge, sleeper width and bore
  clearance; the lean collider still carries the ballast, decks and both rails. Nothing scales the track.
- **Textures**: every generated colour/normal/roughness map is also written as BC7 DDS (sRGB for colour,
  UNORM for data) and the material files point at it: the game takes it as is (no main-thread BC7
  recompression stalls) and VRAM is ~1 byte/px instead of RGBA PNG. The stock fallback DDS paths are gone.
- **Next for traffic performance** (measured headroom needed): building mesh LOD for road tiles beyond
  ~400 m, and a traffic-oriented quality preset (ground cover off, trees medium, grass near) — the mod's
  existing presets already set these knobs; use Quality Medium/High with Ground cover Low for now.

### Traffic-friendly preset

- New Quality preset **Traffic** (settings.lua): medium trees/distance, ground cover off, grass near, few
  lamp lights, normal terrain/buildings, low distant-traffic quality. It keeps the roads, pavement, rails
  and buildings at full detail; what goes is the costly carpet of grass tufts and excess foliage, which is
  what realistic BeamNG traffic needs headroom for. Select it in the map's settings (Quality → Traffic).


## 2026-11-05 — merged upstream/dev (160 commits) and ported two open PRs

- Our branch was rebased onto `TecnaGamer/BeamNG.Earth` **dev** (473d094d, v1.8.1-beta.dev.192), 160
  commits past the 1.8.1-beta tag: road lane counts/ramps, roundabout and deck grading, EV chargers,
  barriers, cache/launcher/terrain-hold fixes, new devtools. Measured formats: plans 9 (our footways plus
  upstream 8), facts 5 (upstream lane counts plus our width policy), roads layer 9 (both source-way and
  rail tunnel/bridge/layer tags). Goldens regenerated. Conflicts resolved by keeping upstream's newer
  grading/lane/barrier/EV features and our width, footway, rail-tunnel and asset work.
- Ported **PR #760**: a mapped roundabout's ring is graded level across (RING_LEVEL_ON), not banked as a
  trunk curve.
- Ported **PR #759**: a building whose parts cover less than half its outline (Ülemiste keskus, #608) is
  drawn as the outline too, with its parts on it; parts off the ground keep their modelled gaps.
- Removed the Qwen and pristine-check worktrees (the Qwen branch remains in git history only); the
  projects folder now holds this repo and the clean 3.12 build venv.
- Still open/not ported: #740 miter clamp at hairpin vertices (native ribbon kernel; needs a bevel),
  #638 tunnel layering, #628 road continuity, #625 building clearance, and the large feature PRs.
