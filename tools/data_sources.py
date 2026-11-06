#!/usr/bin/env python3
"""Show which open data sources build the world at a coordinate, and their licences.

    python tools/data_sources.py --lat 51.5074 --lon -0.1278
    python tools/data_sources.py --lat 40.7128 --lon -74.0060 --check
    python tools/data_sources.py --bbox 51.45,-0.20,51.56,0.02 --json

The report follows the server's real routing:
  - elevation: Mapterhorn first, AWS Terrain Tiles as the failover; the national lidar
    (England's EA DTM, USGS 3DEP, ...) is already inside Mapterhorn's z15+ tiles where it exists;
  - imagery: the regional services whose bounding box holds the point, then NAIP / USGS, then
    the EOX Sentinel-2 years, first with data wins;
  - vector and rail: Overture's transportation/buildings/water layers, with OSM railway and lane
    tags joined in (railtags.py).

--check asks the network for one tile of every active source that can be probed without an
export request, and reports its status and size. --json prints the same data for scripts.
"""
import argparse
import json
import math
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))

import credits  # noqa: E402
import dem as demmod  # noqa: E402
import imagery  # noqa: E402

UA = "BeamNG.Earth-data-sources/1.0 (+https://github.com/TecnaGamer/BeamNG.Earth)"
MAPTERHORN_COVERAGE = "https://download.mapterhorn.com/download_urls.json"
# The US federal sources (USGS ImageryOnly, NAIP) have no bounds in Source: they answer 404 outside the US.
US_ONLY = {"usgs", "naip", "naipusda"}
US_BOUNDS = (18.0, -180.0, 72.0, -66.0)


def tile_xy(lat, lon, z):
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n) % n
    y = int((1.0 - math.asinh(math.tan(math.radians(max(-85.0, min(85.0, lat))))) / math.pi) / 2.0 * n)
    return x, max(0, min(n - 1, y))


def z6_zoom(lat, lon, doc):
    """Mapterhorn's deepest zoom under the point, from its download_urls.json, or None."""
    x, y = tile_xy(lat, lon, 6)
    for it in doc.get("items", ()):
        parts = str(it.get("name", "")).split(".")[0].split("-")
        if len(parts) == 3 and parts[0] == "6":
            try:
                if (int(parts[1]), int(parts[2])) == (x, y):
                    return int(it["max_zoom"])
            except (KeyError, TypeError, ValueError):
                continue
    return None


def inside(bounds, lat, lon):
    if not bounds:
        return True
    s, w, n, e = bounds
    return s <= lat <= n and w <= lon <= e


def serves(src, lat, lon):
    return inside(src.bounds, lat, lon) and (src.name not in US_ONLY or inside(US_BOUNDS, lat, lon))


def fetch(url, timeout=30.0):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        return r.status, len(body), r.headers.get("Content-Type", ""), time.monotonic() - t0


def probe_source(src, lat, lon):
    """One tile of an XYZ source at the point, or None for an export / out of its bounds."""
    if src.export or not serves(src, lat, lon):
        return None
    z = min(14 if src.name.startswith("eox") else 16, src.max_zoom)
    x, y = tile_xy(lat, lon, z)
    url = src.url.format(z=z, x=x, y=y, ty=2 ** z - 1 - y)
    try:
        status, size, ctype, dt = fetch(url)
        return {"source": src.name, "url": url, "status": status, "bytes": size, "content_type": ctype,
                "seconds": round(dt, 2), "ok": status == 200 and size > 0}
    except Exception as e:          # (a probe is a report, not a failure of the tool)
        return {"source": src.name, "url": url, "error": f"{type(e).__name__}: {e}", "ok": False}


def report(lat, lon, check=False):
    out = {"lat": lat, "lon": lon, "elevation": {}, "imagery": [], "vector": {}, "notes": []}

    depth = None
    if check:
        try:
            with urllib.request.urlopen(urllib.request.Request(MAPTERHORN_COVERAGE, headers={"User-Agent": UA}),
                                        timeout=90) as r:
                depth = z6_zoom(lat, lon, json.loads(r.read()))
        except Exception as e:
            out["notes"].append(f"Mapterhorn file list unavailable ({type(e).__name__}: {e})")
    out["elevation"] = {
        "provider": credits.full_label("dem:" + demmod.MAPTERHORN["name"]),
        "fallback": credits.full_label("dem:" + demmod.AWS["name"]),
        "mapterhorn_max_zoom": depth,
        "national_lidar": "inside Mapterhorn's z15+ tiles where the national survey exists",
        "vertical_frame": "Mapterhorn's heights (EGM2008-orthometric); national datums are normalized "
                          "upstream. Checked in London: +0.02 m median, r=0.98 against the EA 1 m DTM",
        "licences": [credits.ELEVATION[demmod.MAPTERHORN["name"]], credits.ELEVATION[demmod.AWS["name"]]],
    }

    active, skipped = [], []
    for name in imagery.DEFAULT_CHAIN:
        src = imagery.SOURCES[name]
        if serves(src, lat, lon):
            active.append(src)
        elif name in US_ONLY:
            skipped.append(name)
    if not any(s.bounds and s.name not in US_ONLY for s in active):
        out["notes"].append("no regional orthophoto covers this point: the EOX Sentinel-2 fallbacks draw the ground")
    if skipped:
        out["notes"].append("US-only sources skipped here: " + ", ".join(skipped))
    for src in active:
        row = {"source": src.name, "bounds": src.bounds or None, "max_zoom": src.max_zoom,
               "licence": src.attribution, "short": credits.label("img:" + src.name)}
        if src.name.startswith("eox"):
            row["resolution_m"] = 10
        elif src.name in ("naip", "naipusda"):
            row["resolution_m"] = 0.6
        elif src.bounds:
            row["resolution_m"] = "regional, 0.1-0.3"
        if check:
            p = probe_source(src, lat, lon)
            if p is not None:
                row["probe"] = p
        out["imagery"].append(row)

    out["vector"] = {
        "roads/buildings/water/land use": "Overture Maps transportation, buildings, water and land_use layers "
                                          "(Overture's own open licence; OSM-derived)",
        "rail": "Overture transportation rail segments with OpenStreetMap railway tags joined in "
                "(bridge/tunnel/layer; server/railtags.py)",
        "US bridge decks": "USGS 3DEP lidar point clouds (EPT, public domain; server/roadz.py)",
        "land cover / trees": "ESA WorldCover 10 m and Meta & WRI canopy height, where available",
        "no paid or scraped sources": "no Google, Bing, Esri, Mapbox or MapTiler data is requested",
    }
    if check:
        mx, my = tile_xy(lat, lon, 14)
        try:
            status, size, ctype, dt = fetch(demmod.MAPTERHORN["url"].format(z=14, x=mx, y=my))
            out["elevation"]["probe"] = {"source": "mapterhorn", "zoom": 14, "status": status, "bytes": size,
                                         "seconds": round(dt, 2), "ok": status == 200 and size > 0}
        except Exception as e:
            out["elevation"]["probe"] = {"source": "mapterhorn", "error": f"{type(e).__name__}: {e}", "ok": False}
    return out


def text(rep):
    lines = [f"BeamNG.Earth data sources at {rep['lat']:.5f}, {rep['lon']:.5f}", ""]
    e = rep["elevation"]
    lines.append("Elevation")
    lines.append(f"  {e['provider']}")
    lines.append(f"  fallback: {e['fallback']}")
    if e.get("mapterhorn_max_zoom") is not None:
        lines.append(f"  Mapterhorn's deepest tile here: z{e['mapterhorn_max_zoom']} "
                     f"({'national lidar' if e['mapterhorn_max_zoom'] >= 15 else 'coarse model only'})")
    lines.append(f"  {e['vertical_frame']}")
    if e.get("probe"):
        p = e["probe"]
        lines.append(f"  probe: {p.get('status', 'ERROR')} {p.get('bytes', '')} {p.get('seconds', '')}s"
                     if "error" not in p else f"  probe: {p['error']}")
    lines.append("")
    lines.append("Imagery (first with data wins)")
    for i, row in enumerate(rep["imagery"], 1):
        res = f", ~{row['resolution_m']} m" if "resolution_m" in row else ""
        lines.append(f"  {i}. {row['source']} (z<={row['max_zoom']}{res})")
        if row.get("probe"):
            p = row["probe"]
            lines.append(f"     probe: {p.get('status', 'ERROR')} {p.get('bytes', '')} {p.get('seconds', '')}s"
                         if "error" not in p else f"     probe: {p['error']}")
    lines.append("")
    lines.append("Vector / rail")
    for k, v in rep["vector"].items():
        lines.append(f"  {k}: {v}")
    if rep["notes"]:
        lines.append("")
        for n in rep["notes"]:
            lines.append("note: " + n)
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--lat", type=float)
    g.add_argument("--bbox", help="S,W,N,E (the report uses its centre)")
    ap.add_argument("--lon", type=float)
    ap.add_argument("--check", action="store_true", help="probe one tile of every active source")
    ap.add_argument("--json", action="store_true", help="JSON output")
    a = ap.parse_args()
    if a.bbox:
        s, w, n, e = [float(v) for v in a.bbox.split(",")]
        lat, lon = (s + n) / 2.0, (w + e) / 2.0
    else:
        if a.lon is None:
            ap.error("--lat needs --lon")
        lat, lon = a.lat, a.lon
    rep = report(lat, lon, check=a.check)
    print(json.dumps(rep, indent=2) if a.json else text(rep))


if __name__ == "__main__":
    main()
