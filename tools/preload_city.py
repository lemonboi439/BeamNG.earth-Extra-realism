#!/usr/bin/env python3
"""Preload a city's map data, then convert it locally into baked plans/facts (fast first visits).

    python tools/preload_city.py --center 51.5074,-0.1278 --radius-km 6 --cache "G:\\BeamNG.Earth Cache" --bake
    python tools/preload_city.py --bbox 51.45,-0.20,51.56,0.02 --cache ... --layers roads,buildings,water --no-dem

Downloads the raw layers every server process would fetch on the first visit (Overture roads, buildings,
building parts, infrastructure, water, land use, places, land cover and canopy), the Mapterhorn DEM tiles
and optionally bakes the area's road plans and facts into <cache>/bake/<area>.bnge (tools/bake_area.py),
which the server reads before planning anything. Run it with the game closed: it is a bulk download, not a
load-time prefetcher. Imagery is not downloaded by default (it is many times these layers' size); add
--imagery later, or drive the area once and keep its cache.
"""
import argparse
import concurrent.futures
import json
import math
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))

DEFAULT_LAYERS = ("roads", "buildings", "building_parts", "infrastructure", "water", "land_use", "places",
                  "landcover", "canopy")


def parse_bbox(s):
    a = [float(v) for v in s.split(",")]
    if len(a) != 4:
        raise argparse.ArgumentTypeError("S,W,N,E")
    return a


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bbox", type=parse_bbox, help="S,W,N,E")
    ap.add_argument("--center", help="LAT,LON with --radius-km")
    ap.add_argument("--radius-km", type=float, default=5.0)
    ap.add_argument("--cache", default=os.path.join(os.path.expanduser("~"), ".cache", "beamng-earth"),
                    help="the server's cache (default ~/.cache/beamng-earth)")
    ap.add_argument("--layers", default=",".join(DEFAULT_LAYERS),
                    help="comma-separated map layers (default: all vector/landcover/canopy)")
    ap.add_argument("--no-dem", action="store_true", help="skip the DEM tiles")
    ap.add_argument("--imagery", action="store_true",
                    help="also fetch the satellite tiles the 1 km base photos sample (many times the map data; slow)")
    ap.add_argument("--bake", action="store_true", help="bake plans/facts after the download (bake_area.py)")
    ap.add_argument("--out", help="the .bnge pack path (default <cache>/bake/<area>.bnge)")
    ap.add_argument("--procs", type=int, default=4, help="download threads / bake processes")
    ap.add_argument("--name", default="city")
    a = ap.parse_args()
    if a.bbox:
        s, w, n, e = a.bbox
    elif a.center:
        la, lo = [float(v) for v in a.center.split(",")]
        dlat = a.radius_km / 111.32
        dlon = a.radius_km / (111.32 * max(0.05, math.cos(math.radians(la))))
        s, w, n, e = la - dlat, lo - dlon, la + dlat, lo + dlon
    else:
        ap.error("give --bbox or --center")
    layers = tuple(x.strip() for x in a.layers.split(",") if x.strip())
    cache = os.path.abspath(a.cache)
    os.makedirs(cache, exist_ok=True)
    import mapcells
    import mapdata
    import dem as demmod
    import netfetch

    cells = mapcells.cells_for_bbox(s, w, n, e)
    print(f"preloading {len(cells)} map cells in {s:.4f},{w:.4f} .. {n:.4f},{e:.4f} -> {cache}", flush=True)
    net = netfetch.Fetcher()
    md = mapdata.MapData(cache, fetcher=net)
    t0 = time.time()
    done = [0]

    def one(k):
        try:
            md.cell(k, layers=layers, kind="demand")
            done[0] += 1
            if done[0] % 10 == 0:
                print(f"  cells {done[0]}/{len(cells)} ({time.time() - t0:.0f}s)", flush=True)
        except Exception as ex:
            print(f"  cell {k}: {type(ex).__name__}: {ex}", flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, a.procs)) as pool:
        list(pool.map(one, cells))
    print(f"map data: {done[0]}/{len(cells)} cells in {time.time() - t0:.0f}s", flush=True)

    if not a.no_dem:
        try:
            dem = demmod.Dem(os.path.join(cache, "dem"), fetcher=net)
            step = max(500.0, a.radius_km * 1000.0 / 8.0) if a.center else 1000.0
            pts = []
            la = s
            while la <= n:
                lo = w
                while lo <= e:
                    pts.append((la, lo))
                    lo += step / (111320.0 * max(0.05, math.cos(math.radians(la))))
                la += step / 110540.0
            keys = []
            for la, lo in pts:
                keys += dem.keys_around(la, lo, min(1200.0, step * 0.9), 15)
            keys = list(dict.fromkeys(keys))
            got = deferred = 0
            for i in range(0, len(keys), 16):
                n_got, n_def = dem.prefetch(keys[i:i + 16])
                got += n_got
                deferred += 1 if n_def else 0
            print(f"DEM: {got} tiles downloaded ({deferred} batches deferred) in {time.time() - t0:.0f}s", flush=True)
        except Exception as ex:
            print(f"DEM: {type(ex).__name__}: {ex}", flush=True)

    if a.imagery:
        try:
            import geodesy
            import imagery as imagemod
            img = imagemod.Imagery(cache, fetch_workers=4, fetcher=net)
            step_m = 1024.0
            n_blocks = 0
            la = s + step_m / 110540.0 / 2.0
            # (block centres every 1 km, a local frame each: the base photo's ENU footprint)
            while la <= n:
                lo = w + step_m / (111320.0 * max(0.05, math.cos(math.radians(la)))) / 2.0
                while lo <= e:
                    frame = geodesy.EnuFrame(la, lo)
                    img.prefetch(frame, -step_m / 2, -step_m / 2, step_m, 4096)
                    n_blocks += 1
                    lo += step_m / (111320.0 * max(0.05, math.cos(math.radians(la))))
                la += step_m / 110540.0
            print(f"imagery: prefetch requested for {n_blocks} base-photo blocks (background)", flush=True)
            img.pool.shutdown(wait=True)
            img.urgent_pool.shutdown(wait=True)
        except Exception as ex:
            print(f"imagery: {type(ex).__name__}: {ex}", flush=True)
    if a.bake:
        out = a.out or os.path.join(cache, "bake", a.name + ".bnge")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        print(f"baking plans/facts -> {out}", flush=True)
        cmd = [sys.executable, os.path.join(ROOT, "tools", "bake_area.py"), "--bbox",
               f"{s},{w},{n},{e}", "--cache", cache, "--out", out, "--procs", str(max(1, a.procs))]
        rc = subprocess.call(cmd)
        print(f"bake exit {rc}", flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    main()
