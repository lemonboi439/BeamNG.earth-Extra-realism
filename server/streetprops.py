"""Small mapped street objects beyond the fences/furniture: manholes, gully grates, traffic-calming humps and
speed cameras (Overture infrastructure points/lines). Forest rows [group, x, y, z, yaw, scale, height, rank].

Humps are placed across the nearest road (the model's length runs along the road); a camera faces it. The models
are the mod's own (tools/build_street_props.py); no new materials. Pure given infra_fn/elev/roads.
"""
import json
import logging
import math
import os
import numpy as np

import mapcells
import mapgeom

log = logging.getLogger("earthd.streetprops")

MANHOLE, GRATE, HUMP, CAMERA = "manhole", "grate", "speedhump", "speedcamera"
HEIGHTS = {MANHOLE: 0.03, GRATE: 0.05, HUMP: 0.075, CAMERA: 3.94}
MAX_HUMPS = 160
HUMP_VALUES = {"hump", "bump"}          # (a table, chicane, island or cushion is not this model)
HUMP_W = {"speedhump": 7.0, "speedhump6": 5.8, "speedhump5": 4.9}
HUMP_MAX_OFF_M = 20.0                   # the feature must be this near a carriageway to be a hump on it
CAMERAS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "speed_cameras.json")
_cameras = None


def _camera_points():
    """The OSM speed-camera extract shipped with the mod (London; 451 nodes), or []."""
    global _cameras
    if _cameras is None:
        try:
            with open(CAMERAS_FILE, encoding="utf-8") as f:
                _cameras = json.load(f)
        except (OSError, ValueError):
            _cameras = []
    return _cameras


def _tags(feats, i):
    t = feats.attrs.get("tags")
    if t is None:
        return ""
    try:
        d = json.loads(str(t[i]))
    except (TypeError, ValueError):
        return str(t[i]).lower()
    return " ".join(f"{k}={v}" for k, v in d.items()).lower() if isinstance(d, dict) else str(d).lower()


def _road_place(p, roads):
    """(yaw radians, carriageway width, x, y of its centreline nearest p, z there, distance m) of the nearest
    carriageway, or None. The hump is laid across the centreline (a mapped point out on a lane put its old fixed
    width across the kerb); its z is the road's own height there."""
    best = None
    for _fid, spec, pts, _lon in roads:
        pts = np.asarray(pts, np.float64)
        if len(pts) < 2:
            continue
        a, d = pts[:-1, :2], np.diff(pts[:, :2], axis=0)
        t = np.clip(((p - a) * d).sum(1) / np.maximum((d * d).sum(1), 1e-9), 0.0, 1.0)
        q = a + d * t[:, None]
        ds = np.hypot(*(q - p).T)
        k = int(np.argmin(ds))
        if best is None or ds[k] < best[5]:
            z = float(pts[k, 2] + t[k] * (pts[k + 1, 2] - pts[k, 2]))
            best = (math.atan2(float(d[k, 1]), float(d[k, 0])), float(spec.width), float(q[k, 0]), float(q[k, 1]),
                    z, float(ds[k]))
    return best


def _is_hump(feats, i):
    """Is feature i a mapped hump or bump (not a table / chicane / cushion / island that shares the class)?"""
    tags = feats.attrs.get("tags")
    if tags is None:
        return True                         # (the infrastructure layer's own class, no tag detail)
    try:
        d = json.loads(str(tags[i]))
    except (TypeError, ValueError):
        return True
    if not isinstance(d, dict):
        return True
    val = None
    for k, v in d.items():
        if str(k).lower() in ("traffic_calming", "traffic_calming:type"):
            val = str(v).strip().lower()
    return val is None or val in HUMP_VALUES


def _hump_group(width):
    """The hump model whose width is nearest under the carriageway's width (the model spans the road, with the kerb's
    little gap)."""
    if width >= 6.3:
        return "speedhump"
    if width >= 5.1:
        return "speedhump6"
    return "speedhump5"


def rows(frame, infra_fn, box, elev, roads=()):
    if infra_fn is None:
        return []
    x0, y0, x1, y1 = map(float, box)
    la, lo, _ = frame.enu_to_geodetic(np.array([x0, x0, x1, x1]), np.array([y0, y1, y0, y1]), np.zeros(4))
    try:
        keys = mapcells.cells_for_points(la, lo, pad_lat=0.00005, pad_lon=0.00005, max_cells=64)
    except (ValueError, OverflowError):
        return []
    seen = set()
    pending = []        # (group, x, y, yaw, height)
    humps = 0
    for key in keys:
        try:
            feats = infra_fn(key)
        except Exception as ex:
            log.debug("street props: cell %s unavailable: %s", key, ex)
            continue
        if feats is None or not len(feats):
            continue
        cls = feats.attrs.get("class")
        for i in range(len(feats)):
            c = str(cls[i]).lower() if cls is not None else ""
            tags = _tags(feats, i)
            group = None
            if c == "manhole" or "manhole" in tags:
                group = MANHOLE
            elif c in ("drain", "gully", "grate") or "drain=" in tags:
                group = GRATE
            elif c in ("speed_camera", "speedcamera") or "speed_camera" in tags:
                group = CAMERA
            elif c == "traffic_calming" or "traffic_calming" in tags:
                if not _is_hump(feats, i):
                    continue
                group = HUMP
            if group is None:
                continue
            fid = str(feats.ids[i])
            if fid in seen:
                continue
            seen.add(fid)
            parts = feats.parts(i)
            if not parts:
                continue
            for part in parts:
                if not part or not len(part[0]):
                    continue
                ring = np.asarray(part[0], np.float64)
                if feats.kind[i] == mapgeom.POINT:
                    ring = ring[:1]
                elif group == HUMP and len(ring) > 1:
                    ring = ring[len(ring) // 2:len(ring) // 2 + 1]
                if group == HUMP and humps >= MAX_HUMPS:
                    break
                e, n, _ = frame.geodetic_to_enu(ring[:, 1], ring[:, 0], np.zeros(len(ring)))
                for px, py in zip(np.asarray(e, np.float64), np.asarray(n, np.float64)):
                    if not (x0 <= px < x1 and y0 <= py < y1):
                        continue
                    z0 = None
                    if group == HUMP:
                        place = _road_place(np.array([px, py]), roads)
                        if place is None or place[5] > HUMP_MAX_OFF_M:
                            continue
                        yaw, width, px, py, z0 = place[:5]
                        # the road's centre is crowned up to CROWN_MAX_M (roads.CROWN_FALL): the hump sits on it
                        z0 += min(0.12, 0.0125 * width) + 0.01
                        group = _hump_group(width)
                    elif group == CAMERA:
                        yaw = (_road_place(np.array([px, py]), roads) or (0.0,))[0] + math.pi / 2.0
                    else:
                        yaw = 0.0
                    pending.append((group, px, py, yaw, HEIGHTS[HUMP] if group.startswith("speedhump")
                                    else HEIGHTS[group], z0))
                    if group.startswith(HUMP):
                        humps += 1
    # actual OSM speed cameras in the London extract (the infrastructure layer has no camera class there)
    for cam in _camera_points():
        la, lo = float(cam["lat"]), float(cam["lon"])
        e, n, _ = frame.geodetic_to_enu(np.array([la]), np.array([lo]), np.zeros(1))
        px, py = float(e[0]), float(n[0])
        if not (x0 <= px < x1 and y0 <= py < y1):
            continue
        direction = cam.get("direction")
        place = _road_place(np.array([px, py]), roads)
        try:
            yaw = math.radians(90.0 - float(direction)) if direction is not None else (place[0] if place else 0.0)
        except (TypeError, ValueError):
            yaw = place[0] if place else 0.0
        if direction is None:
            yaw += math.pi / 2.0
        pending.append((CAMERA, px, py, yaw, HEIGHTS[CAMERA], None))
    if not pending:
        return []
    xy = np.array([[p[1], p[2]] for p in pending], np.float64)
    lat, lon, _ = frame.enu_to_geodetic(xy[:, 0], xy[:, 1], np.zeros(len(xy)))
    _, _, z = frame.geodetic_to_enu(lat, lon, np.asarray(elev(lat, lon), np.float64))
    out = []
    for (group, px, py, yaw, height, z0), zz in zip(pending, np.asarray(z, np.float64)):
        zz = zz if z0 is None else z0
        out.append([group, round(float(px), 2), round(float(py), 2), round(float(zz), 2),
                    round(math.degrees(yaw) % 360.0, 1), 1.0, height, 0.0])
    return out


RAIL_CLEAR_M = 3.0          # m a manhole / gully / camera keeps off a track's centre (its ballast's foot)


def clear_of_rails(rows, tracks, groups=("manhole", "grate", "speedcamera")):
    """rows without the ground props in groups within RAIL_CLEAR_M of a track (tracks: [(xy (k, 2), half width)]).
    A speed hump is kept: a level crossing's hump belongs on the road across the rails."""
    if not tracks or not rows:
        return rows
    out = []
    for r in rows:
        if r[0] not in groups:
            out.append(r)
            continue
        p = np.array([float(r[1]), float(r[2])])
        near = False
        for xy, half in tracks:
            a = np.asarray(xy, np.float64)[:, :2]
            if len(a) < 2:
                continue
            b = a[1:]
            d = b - a[:-1]
            t = np.clip(((p - a[:-1]) * d).sum(1) / np.maximum((d * d).sum(1), 1e-9), 0.0, 1.0)
            q = a[:-1] + d * t[:, None]
            if float(np.hypot(*(q - p).T).min()) <= half + RAIL_CLEAR_M:
                near = True
                break
        if not near:
            out.append(r)
    return out
