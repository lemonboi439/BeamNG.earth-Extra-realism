"""England's LiDAR surface model (Environment Agency, Open Government Licence) for measured building heights.

The national LiDAR Composite DSM 1 m is the surface (roofs, walls, trees) at about 1 m; the DTM is the bare
ground already in Mapterhorn (measured equal to 0.02 m, docs/overhaul/DATA_SOURCES.md). When Overture has no
height (or no floor count) for a building in England, this reads the DSM over its footprint and takes its
crest over the ground at the walls: a measured height, not a prediction.

    ealidar.configure(cache_dir, fetcher)      # once per server process (off with BNE_EA_LIDAR=0)
    z = ealidar.surface(lat, lon)              # m, or None

The WCS (1.0.0, GeoTIFF, EPSG:3857) answers a 1024 m block at 2 m pixels in ~0.6 s; blocks are cached on disk
and in a small LRU, so a tile's buildings cost nothing after the first. The DSM's nodata (float32 -3.4e38) is
NaN. England's envelope is the WCS's; elsewhere None (no request).
"""
import io
import logging
import math
import os
import threading
from collections import OrderedDict

import numpy as np

log = logging.getLogger("earthd.ealidar")

ENABLED = os.environ.get("BNE_EA_LIDAR", "1") != "0"
BLOCK_M = 1024.0            # a cached block's side (Web Mercator metres)
BLOCK_PX = 512              # ... at 2 m pixels
MAX_BLOCKS = 32             # in memory (float32 + validity ~4.5 MB each)
SOUTH, WEST, NORTH, EAST = 49.80, -7.25, 55.95, 2.15    # England's LiDAR envelope (with a little sea)
R = 6378137.0
HALF_WORLD = math.pi * R
URL = "https://environment.data.gov.uk/geoservices/datasets/9ba4d5ac-d596-445a-9056-dae3ddec0178/wcs"
COVERAGE = "9ba4d5ac-d596-445a-9056-dae3ddec0178:Lidar_Composite_Elevation_LZ_DSM_1m"
NODATA = -1e30

_provider = None
_lock = threading.Lock()


def covers(lat, lon):
    """Whether the EA LiDAR composite covers this point (England's envelope; the service returns nodata off it)."""
    try:
        la, lo = float(lat), float(lon)
    except (TypeError, ValueError):
        return False
    return SOUTH <= la <= NORTH and WEST <= lo <= EAST


def _merc(lat, lon):
    """Web Mercator metres (EPSG:3857)."""
    lat = max(-85.0, min(85.0, float(lat)))
    x = float(lon) * 20037508.342789244 / 180.0
    y = math.log(math.tan((90.0 + lat) * math.pi / 360.0)) * R
    return x, y


def _unmerc(x, y):
    lon = x / 20037508.342789244 * 180.0
    lat = math.degrees(2.0 * math.atan(math.exp(y / R)) - math.pi / 2.0)
    return lat, lon


class Block:
    __slots__ = ("x0", "y0", "z")

    def __init__(self, x0, y0, z):
        self.x0, self.y0, self.z = float(x0), float(y0), z


class EaLidar:
    def __init__(self, cache_dir, fetcher, block_px=BLOCK_PX, max_blocks=MAX_BLOCKS):
        self.dir = cache_dir
        self.fetcher = fetcher
        self.px = int(block_px)
        self._lock = threading.Lock()
        self._blocks = OrderedDict()
        self._miss = set()          # blocks with no data (sea, a hole, offline): not asked again this session
        self.max_blocks = int(max_blocks)
        self.stats = {"fetch": 0, "cache": 0, "miss": 0, "error": 0}
        os.makedirs(self.dir, exist_ok=True)

    def _key(self, x, y):
        bx = math.floor(x / BLOCK_M) * BLOCK_M
        by = math.floor(y / BLOCK_M) * BLOCK_M
        return bx, by

    def block(self, lat, lon):
        """The 1024 m block under (lat, lon): a Block or None (outside England, offline, or nodata)."""
        if not covers(lat, lon):
            return None
        x, y = _merc(lat, lon)
        bx, by = self._key(x, y)
        key = (bx, by)
        with self._lock:
            if key in self._miss:
                return None
            got = self._blocks.pop(key, None)
            if got is not None:
                self._blocks[key] = got
                return got
        path = os.path.join(self.dir, f"dsm_{int(bx / BLOCK_M)}_{int(by / BLOCK_M)}.npz")
        z = None
        try:
            with np.load(path, allow_pickle=False) as a:
                z = np.asarray(a["z"], np.float64)
            self.stats["cache"] += 1
        except (OSError, ValueError, KeyError):
            z = self._fetch(bx, by, path)
        if z is None:
            with self._lock:
                self._miss.add(key)
            return None
        b = Block(bx, by, z)
        with self._lock:
            self._blocks[key] = b
            while len(self._blocks) > self.max_blocks:
                self._blocks.popitem(last=False)
        return b

    def _fetch(self, bx, by, path):
        if self.fetcher is None:
            return None
        import urllib.parse
        x1, y1 = bx + BLOCK_M, by + BLOCK_M
        params = {"SERVICE": "WCS", "VERSION": "1.0.0", "REQUEST": "GetCoverage", "COVERAGE": COVERAGE,
                  "CRS": "EPSG:3857", "BBOX": f"{bx:.3f},{by:.3f},{x1:.3f},{y1:.3f}",
                  "WIDTH": str(self.px), "HEIGHT": str(self.px), "FORMAT": "GeoTIFF"}
        try:
            r = self.fetcher.get(URL + "?" + urllib.parse.urlencode(params), kind="demand")
            from PIL import Image
            with Image.open(io.BytesIO(r.body)) as im:
                a = np.asarray(im, np.float64)
            if a.shape != (self.px, self.px):
                raise ValueError(f"DSM block {a.shape}, wanted {self.px}x{self.px}")
            a[a < NODATA] = np.nan
            np.savez_compressed(path, z=a.astype(np.float32))
            self.stats["fetch"] += 1
            return a
        except Exception as ex:
            self.stats["error"] += 1
            log.info("EA LiDAR block %s,%s unavailable: %s: %s", bx, by, type(ex).__name__, ex)
            return None

    def surface(self, lat, lon):
        """The DSM height at (lat, lon), bilinear, or None (no data, sea, or not fetched)."""
        b = self.block(lat, lon)
        if b is None:
            return None
        x, y = _merc(lat, lon)
        u = (x - b.x0) / BLOCK_M * (self.px - 1)
        v = (b.y0 + BLOCK_M - y) / BLOCK_M * (self.px - 1)       # (row 0 is the block's north edge)
        i0, j0 = int(math.floor(u)), int(math.floor(v))
        i1, j1 = min(i0 + 1, self.px - 1), min(j0 + 1, self.px - 1)
        fu, fv = u - i0, v - j0
        q = np.array([[b.z[j0, i0], b.z[j0, i1]], [b.z[j1, i0], b.z[j1, i1]]], np.float64)
        if not np.isfinite(q).all():
            vals = q[np.isfinite(q)]
            return float(np.median(vals)) if len(vals) else None
        return float((q[0, 0] * (1 - fu) + q[0, 1] * fu) * (1 - fv) + (q[1, 0] * (1 - fu) + q[1, 1] * fu) * fv)


def configure(cache_dir, fetcher):
    """Make the process's EA LiDAR reader (None cache dir or BNE_EA_LIDAR=0: none)."""
    global _provider
    with _lock:
        _provider = EaLidar(cache_dir, fetcher) if ENABLED and cache_dir and fetcher is not None else None
    return _provider


def available():
    return _provider is not None


def surface(lat, lon):
    p = _provider
    return None if p is None else p.surface(lat, lon)
