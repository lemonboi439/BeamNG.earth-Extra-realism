# UK, Western Europe and US data sources (verified 2026-11-06)

This is the audit behind the "high-accuracy data" request. It maps every requested dataset to
the module that actually serves it, records the coverage and licence checks made on 2026-11-06,
and lists the sources that are deliberately **not** used (paid, scraped, or duplicating data the
world already has). The server's real provider registry is `server/imagery.py` (imagery, with
`server/imagery_regional.py` for the regional services) and `server/dem.py` (elevation, with
`server/hrdem.py` and `server/lidarbc.py`); there is no separate `elevation/providers.py` or
`osm_importer.py`.

Use `tools/data_sources.py` to see the exact chain at a coordinate:

    python tools/data_sources.py --lat 51.5074 --lon -0.1278
    python tools/data_sources.py --lat 51.5074 --lon -0.1278 --check
    python tools/data_sources.py --lat 40.7128 --lon -74.0060 --json

## Elevation

| Source | Where | What the player gets | Licence |
|---|---|---|---|
| Mapterhorn `tiles.mapterhorn.com` | global land | the base elevation; z15-z18 where a national lidar exists, Copernicus GLO-30 elsewhere (with its buildings/trees removed by `dsmground.ground` before it is used as ground) | per underlying dataset, https://mapterhorn.com/attribution |
| AWS Terrain Tiles `elevation-tiles-prod` | global | failover when Mapterhorn cannot be asked; also the polar-adjacent fallback | Mapzen / open, see CREDITS.md |
| NRCan HRDEM (Canada, 1 m DTM) `canelevation-dem.s3.ca-central-1.amazonaws.com` | southern Canada | near terrain where Mapterhorn has no lidar; opt-in `BNE_HRDEM_NEAR=1` for z15+ | OGL-Canada |
| LidarBC (BC, 1 m DEM/DSM) `nrs.objectstore.gov.bc.ca` | British Columbia | near terrain and measured canopy where flown; opt-in `BNE_LIDARBC=1` | OGL-British Columbia |
| USGS 3DEP lidar point clouds (EPT on AWS, `s3://usgs-lidar-public`) | the US | bridge-deck heights and ground stretches under real bridges (`server/roadz.py`; `BNE_LIDAR_DECKS=off` disables) | public domain (US federal) |
| Copernicus GLO-30 COGs | poles above 85.05° | polar caps (`polardem.py`) | Copernicus DEM licence |

**Key finding: the UK and US national lidar is already in Mapterhorn.** Mapterhorn's
`download_urls.json` lists the deepest tile per z6 cell. On 2026-11-06 the London z6 cell listed
z17 (a central z17 tile itself returned 404, so the effective near depth is z16, 0.74 m pixels at
51.5° N), and New York, Chicago, Denver, Seattle, Miami, Atlanta, Phoenix, Manchester, Birmingham,
Leeds, Cornwall, Edinburgh, Glasgow and Dublin listed z16; those z15+ tiles are national lidar
models, not Copernicus. A direct comparison of a London z16 tile against the Environment Agency
LIDAR Composite DTM 1 m (WCS) gave a median difference of **+0.02 m** and a height correlation of
**0.98**; EA's 1 m model carries about 10 cm of finer roughness that Mapterhorn's near tile does
not, below the scale that matters once the road grading and the 1 m lattice are applied, and not
worth a duplicate 0.65 s fetch per z16 tile.

Direct national endpoints were tested but are **not** added as parallel runtime providers:

* EA LiDAR WCS (`environment.data.gov.uk/spatialdata/lidar-composite-digital-terrain-model-dtm-1m/wcs`,
  WCS 1.0.0, coverage `Lidar_Composite_Elevation_DTM_1m`, EPSG:3857, GeoTIFF) answers a 512² z16
  London tile in ~0.65 s. It duplicates Mapterhorn's London data, so fetching it again would only
  add load-time cost.
* USGS 3DEP `ImageServer/exportImage` answers a 512² tile in ~10 s in EPSG:4326 and truncates
  EPSG:3857 responses; Mapterhorn already reads 3DEP where it exists. Adding it on the critical
  path would make a fresh US block 10-20 s slower per tile for no detail gain.

The routing rule is therefore unchanged and correct: Mapterhorn first, AWS if it cannot answer,
and the opt-in HRDEM/LidarBC readers only where Mapterhorn has no lidar at all. If Mapterhorn's
coverage ever drops or a national survey is missing from it, the provider belongs in
`dem.Dem._canada`-style code next to HRDEM, gated by `covers()` and `FINE_MIN_ZOOM`.

### Vertical datum

Mapterhorn's heights are the world's common frame (EGM2008-orthometric). National lidars are
normalized to it upstream by Mapterhorn (the London check above measured the result). The two
direct readers cover datums that are close but not identical: HRDEM is CGVD2013 and LidarBC is
CGVD2013/ellipsoidal; `dem._canada_fine` feathers the seam over 60 m (`HRDEM_FEATHER_M`) so a
decimetre difference becomes a slope, not a step. The direct providers are never blended without
that feather, and the sea (Mapterhorn's exact zeros) is never overwritten.

## Imagery

The chain is `imagery.DEFAULT_CHAIN`: every regional service whose bounding box holds the tile,
then NAIP (USDA's server, then USGS's), then USGS ImageryOnly, then the EOX Sentinel-2 cloudless
years, first with data wins. A source tile that is 404, blank, or unreachable falls through to
the next source; a host's circuit breaker is shared by all EOX years.

| Region | Sources | Resolution |
|---|---|---|
| US | MD iMAP, CT ECO, MassGIS, DC OCTO, Indiana, Ohio, NC OneMap, PEMA/PASDA, VGIN, NYS ITS, NJ OGIS, Delaware, VCGI, then NAIP + USGS | 0.15-0.6 m regional/NAIP, ~1.9 m USGS |
| UK | no open national orthophoto; EOX Sentinel-2 2025 / 2023 | ~10 m |
| France | IGN BD ORTHO | ~20 cm |
| Spain | PNOA | ~25-50 cm |
| Netherlands | Beeldmateriaal / PDOK | ~25 cm |
| Belgium | Digitaal Vlaanderen (Flanders) | ~25 cm |
| Luxembourg | geoportail.lu | ~20 cm |
| Switzerland | swisstopo SWISSIMAGE | ~10-25 cm |
| Austria | basemap.at | ~30 cm |
| Germany | Bavaria, NRW, Baden-Wurttemberg, Lower Saxony, Berlin, Hesse | ~20 cm |
| Italy | South Tyrol 2024, Geoportale Nazionale 2012 | ~20-50 cm |
| Poland | GUGiK | ~25 cm |
| Czechia | CUZK | ~20 cm |
| Estonia | Maa-amet | ~20-40 cm |

**UK imagery is a legal gap, not a code gap.** The national 25-50 cm orthophotos (APGB) are
commercial; the Environment Agency's vertical aerial photography is published as survey index
metadata, not as an open WMS/tile service; OS Aerial Maps are licensed. The new `eox2025` layer is
the best open, keyless result for the UK and is now the first global fallback. It is
CC BY-NC-SA 4.0 (the same licence as `eox2023`; `eox2016` remains CC BY 4.0 for any commercial
use). The mod must stay non-commercial for these layers, as CREDITS.md already says.

EOX 2025 was verified live on 2026-11-06: the WMTS capabilities list
`s2cloudless-2025_3857`, and a z14 London tile returned 200 image/jpeg in ~0.2 s.

## Vector and rail

There is no separate vector importer: `server/mapdata.py` reads Overture's open GeoParquet
release from S3/Azure (roads/transportation, connectors, buildings, building parts,
infrastructure, water, land use, places), and `server/railtags.py` joins OpenStreetMap railway
tags onto the rail segments (tunnel / bridge / layer / gauge signals), plus OSM lane and sign
tags. That is the UK, Western Europe and US road/building/rail source.

Building heights and floor counts come from Overture's building attributes where the source has
them; UK buildings without a mapped height still get the storey estimate. Rail geometry, tunnels,
bridges and stations are OSM/Overture geometry; `server/railways.py` builds the ballast, decks,
tunnels and lean collision mesh from it, and `server/roadz.py` measures US road bridges against
the USGS 3DEP lidar point clouds (public domain). No paid vector feed, no Google/Esri/Mapbox, no
scraping.

OS Open Buildings/OS Open Map Local would add UK height attributes, but OS Data Hub downloads and
the Features API need a registered API key; they cannot be a keyless default. US NTAD/FRA rail is
OSM-derived and no fresher than the OSM tags already joined.

## Config toggles

| Toggle | Default | Effect |
|---|---|---|
| `--imagery a,b,...` / `--imagery off` | the full chain | choose or disable imagery sources |
| `--colormatch s2` | on when USGS is in the chain | seam correction against the EOX references |
| `BNE_BCORTHO=0` | on | disable BC's lidar orthophotos |
| `BNE_HRDEM=0` | on | disable Canada's HRDEM reader |
| `BNE_HRDEM_NEAR=1` | off | read HRDEM whole for near terrain (slower, sharper) |
| `BNE_LIDARBC=1` | off | enable BC's lidar (slower first visits) |
| `BNE_DEM_ZOOM=15` | 16 | cap Mapterhorn's near zoom (A/B) |

All sources are optional in `netfetch`: one that does not answer falls through to the next, and
the status line names what it affects. Offline, the disk cache serves what it has and new ground
gets the palette / EOX fallback.

## Verification

`tools/data_sources.py` prints the active chain and, with `--check`, probes one tile of every
probeable source and Mapterhorn's coverage list. It reports the Mapterhorn depth, the regional
imagery, the EOX fallbacks and the vector/rail provenance without touching any paid service. Run
it before a release against London and New York; the expected output is in the 2026-11-06 audit
above.
