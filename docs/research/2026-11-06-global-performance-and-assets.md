# Global streaming performance and real-world assets (2026-11-06)

This release keeps the real-data path enabled for every preset and reduces cold-load
memory/concurrency spikes without lowering terrain or imagery fidelity.

## Measured changes

| Path | Before | After |
|---|---:|---:|
| DEM/imagery fetch threads per server process | 68 (DEM 16 + 16 + 16; imagery 16 + 4) | 26 (DEM 8 + 4 + 4; imagery 8 + 2) |
| Cold 1024² heightmap elevation call | up to the complete block (901,120 samples observed across the regression workload) | one 128² cache chunk (16,384 samples) |
| Plans-format regression | stale digest under format 13 | golden digest matches generated output |
| Small-profile finished imagery | overwritten to 33 MB | 58 MB measured floor |
| Windows raw sea raster cleanup | mapped file remained locked | read-only detached buffer; truncated files raise `ValueError` |

The worker reduction is a safe default, not a selectable preset: urgent DEM and imagery
requests retain dedicated lanes, while normal and probe work cannot create dozens of
queued threads in every far-field worker. `heightmap.grid_u_chunked()` now builds and
caches one chunk at a time, retaining the same elevation samples and output arrays.

## Real-world data and assets

Placement remains driven by OSM/Overture geometry, national/open DEM coverage, and the
imagery provider chain documented in `docs/overhaul/DATA_SOURCES.md`. The merged release
includes:

- mapped road and direction signs (`server/signstream.py`, `server/dirsigns.py`,
  `server/data/dirsigns_styles.json`, and `mod/.../dirsigns.lua`);
- roundabout grading and junction pads (`server/grading.py`, `server/parking.py`);
- driveway dips, curb/sidewalk development, and mapped street furniture
  (`server/development.py`, `server/furniture.py`, `server/sidewalks.py`);
- UK building-height/material rules and brick/garden-wall assets
  (`server/buildings.py`, `server/furniture.py`,
  `mod/beamng_earth/levels/beamng_earth/art/shapes/beamngEarth/bne_garden_wall.dae`);
- vegetation and pine/land-cover placement (`server/vegetation.py`,
  `mod/beamng_earth/lua/ge/extensions/beamngEarth/trees.lua`);
- free-license roadside sign and font assets, with attribution files including
  `Roadgeek-LICENSE.txt`, `Fira-OFL.txt`, and `Overpass-OFL.txt`;
- public/open source provenance for vector, DEM, imagery, rail, and mapped building data
  in `docs/overhaul/DATA_SOURCES.md` and `server/credits.py`.

No generic object is added when mapped geometry or a documented source is absent.

## Data-driven frontage and mapped hedges

OSM sidewalk side tags (`sidewalk:left`, `sidewalk:right`) and `sidewalk:width` are
joined into road specs; explicit no/separate tags remain authoritative, and mapped
widths are preserved in metres. Frontage-supported driveway chunks use the distinct
`driveway_concrete` material, which reuses the project's existing concrete maps rather
than adding an unlicensed texture.

Overture infrastructure lines classified as `hedge`, `tree_row`, `barrier=hedge`, or
`natural=tree_row` are clipped to the active tile, blocked by buildings/roads/water,
deterministically varied, and instanced through the existing `G_SHRUB` asset group.
The output is capped at 1,200 mapped hedge instances per cell to keep tile output and
RAM bounded. Geometry remains covered by the Overture/OSM attribution already listed
in `CREDITS.md` and `docs/overhaul/DATA_SOURCES.md`.
