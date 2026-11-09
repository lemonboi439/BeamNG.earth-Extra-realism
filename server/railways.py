"""Railway tracks: Overture's rail segments (transportation/segment subtype "rail", in the roads layer) as a ballast
bed with concrete sleepers and two steel rails, and their bridges as concrete decks on piers: one colliding mesh per
road tile (write_tile's "rail"). Mapped tram bridges use the same visual/collision
pipeline, with a narrow concrete slab and embedded rails instead of ballast and sleepers.

The heights are one profile along the connected track. On the ground the bed follows the drawn ground, smoothed
along its length (a railway does not ride every bump of the lattice). A bridge (the segment's is_bridge flag or level
above 0) is a deck straight between the bed at its ends (deck_heights), raised only where the ground under it would
come through, never steeper than GRADE. Where it is raised, its height reaches out along the track it joins, falling
GRADE per metre: the approach embankments, across segment ends and tile edges alike (the lines are taken from REACH_M
around the tile). The ballast's sides go down past the ground, a low
embankment on the flat and a tall one on a ramp. Short mapped road tunnels also create supported spans when the upper rail lacks a bridge tag.
Where a road crosses at grade, the track stops either side of the
carriageway (a level crossing: the road's own surface across it). Long tunnels are a track of their own inside a bore
(roadmesh.tunnel), a short one under a road bridge is track on the ground. Pure given its inputs."""
import math
import re

import os

import numpy as np

import footbridges
import mapgeom
import roadmesh
import roads

# the ballasted classes drawn (Overture's rail classes); ground trams stay in rails.py,
# their mapped bridges alone are meshed here. Monorails and funiculars are not; subways are deep bores.
# Nor "unknown": OpenStreetMap's railway=proposed / construction (99 of 101
# such ways not flagged abandoned or disused, in 3000 cached cells, 2026-10-27; the rest 2 trams and a miniature
# line): Los Angeles' planned Link Union Station tracks lay across US-101 on the ground and the freeway was dipped
# under a planned high-speed line's 2.6 km "viaduct", a 38 % step at Union Station
DRAWN = {"standard_gauge", "narrow_gauge", "light_rail", "subway"}
GAUGE_M = 1.435             # standard gauge between the rails' inner faces ...
FI_GAUGE_M = 1.524          # ... the Finnish (Russian) broad gauge in the Finnish fork
NARROW_GAUGE_M = 1.0
STEP_M = 2.0                # m between cross-sections, from each part's start (the same at every tile)
SMOOTH_M = 10.0             # m either side the bed's height is averaged over: the real elevation, its DEM bumps out
                            # (50 lifted the bed off the ground, the user, 2026-10-25: "follow the real elevation")
GRADE = 0.02                # the steepest a deck or an approach ramp climbs (2 %: a main line's)
REACH_M = 200.0             # m around a tile the lines are taken: the smoothing's reach and a raised deck's ramps
BALLAST_H = 0.3             # m of ballast over the bed line
BALLAST_TOP_M = 1.8         # m: half the ballast's top width
BALLAST_SLOPE = 1.5         # m out per m down on its sides
RAIL_FOOT_CLEAR_M = 1.0      # m between a fill's foot and another track's deck/ballast edge
ROAD_FOOT_CLEAR_M = 0.5      # m between a fill's foot and a road's carriageway edge
BALLAST_FOOT_M = 0.4        # m its sides reach under the ground at their foot
FOOT_MAX_M = 6.0            # m out at most (the grading fills a tall ramp's embankment; 15 fanned out, 2026-10-25)
SLEEPER_EVERY = 0.6         # m between sleepers
SLEEPER_L, SLEEPER_W, SLEEPER_H = 2.6, 0.26, 0.2
SLEEPER_SINK = 0.08         # m of sleeper in the ballast
RAIL_HEAD, RAIL_H = 0.07, 0.16
DECK_HALF_M = 2.4           # m: half a bridge deck's width
SHARED_GAP_M = 6.0          # m: a bridge track this close to another shares its bed with it (a station's parallel
SHARED_MIN_GAP_M = 1.2      # tracks): the decks are trimmed to meet midway and the edge wall between them is left out
PIER_DEDUPE_M = 4.2         # m: a pier this close to another bridge's is the same structure (parallel tracks' piers)
DECK_D = 1.4                # m of deck under the ballast's top
DECK_WALL_H = 0.7           # m its edge walls stand over the ballast
DECK_WALL_T = 0.3
TRAM_DECK_HALF_M = 1.3       # m: a single track's embedded slab (rails.TRACK's tram bed width / 2)
PIER_EVERY = 25.0           # m between piers (from the deck's start)
PIER_W, PIER_T = 3.6, 1.4   # m across and along the deck
PIER_MIN_H = 1.5            # m: a deck lower than this over the ground stands on its ends alone
PIER_FOOT_M = 2.0           # m a pier reaches into the ground
CROSSING_PAD_M = 0.5        # m past a crossing road's edge the track stops (rails.py's street track runs across the road
                            # from 0.3 m inside its edge: 1.5 left ~1.8 m of bare ground either side)
CROSSING_DZ = 1.5           # m: a road this far over or under the bed passes it by (no level crossing)
VERTS_MAX = 60000           # under the engine's 16-bit indices (sleepers are dropped first past it)
BALLAST = "bne_rail_ballast"
FOOTBRIDGES_ON = os.environ.get("BNE_FOOTBRIDGES", "on") != "off"     # (footbridges.py)
CONCRETE = "bne_bridge_concrete"    # (roadmesh.DECK_SIDE: the decks and piers)
BRICK = "bne_garden_brick"          # a rail viaduct's arches over a road (the mapped brick panel's CC0 texture)
SLEEPER = "bne_rail_sleeper"        # weathered concrete sleepers (bne_bridge_concrete stood out near-white, 2026-10-25)
STEEL = "bne_rail_steel"            # rust-brown rails
# per wear (wear()): a disused line's ballast weedy, an abandoned one's grown over, their rails and sleepers older, and
# a share of an abandoned line's sleepers gone (SLEEPERS_GONE)
BALLAST_STONE = "bne_rail_ballast_stone"    # crushed granite (BNE_RAIL_XING; tools/ingest_rail_textures.py): a track in use
BALLAST_TILE_M = 0.8        # m one tile of it covers (stones of 5-10 cm); the stock gravel's 3 m
BALLAST_WORN = (BALLAST, "bne_rail_ballast_weedy", "bne_rail_ballast_grown")
STEEL_WORN = (STEEL, "bne_rail_steel_old", "bne_rail_steel_old")
SLEEPER_WORN = (SLEEPER, "bne_rail_sleeper_old", "bne_rail_sleeper_old")
SLEEPERS_GONE = (0.0, 0.0, 0.3)


_GROUND_MEMO = {}            # anchor key -> {point (cm): z}: point_ground's heights, shared by the tiles of an anchor
GROUND_MEMO_MAX = 400000     # points per anchor kept (then forgotten, and asked again)
GROUND_MEMO_ANCHORS = 2


def point_ground(frame, elev, key=None):
    """ground(xy (n, 2)) -> z in frame at exactly those points, from elev(lat, lon) (the graded physics ground). Not
    roadmesh.drawn_ground: that fills a 2 m grid over the points' whole box, and the lines here span ~1 km (a road
    tile's workers spun on it for minutes, 2026-10-25). key (the anchor's): heights remembered per point. Every tile
    within REACH_M of a line asks for the same points (each part resampled from its start), ~6 tiles each, and the
    graded ground's lookups were most of a rail tile's time (16 tiles round Baltimore Penn Station: 28 of 68 s)."""
    memo = None
    if key is not None:
        memo = _GROUND_MEMO.get(key)
        if memo is None:
            while len(_GROUND_MEMO) >= GROUND_MEMO_ANCHORS:
                _GROUND_MEMO.pop(next(iter(_GROUND_MEMO)))
            memo = _GROUND_MEMO[key] = {}

    def look(xy):
        la, lo, _ = frame.enu_to_geodetic(xy[:, 0], xy[:, 1], 0.0)
        return np.asarray(frame.geodetic_to_enu(la, lo, np.asarray(elev(la, lo), np.float64))[2], np.float64)

    def ground(xy):
        xy = np.asarray(xy, np.float64).reshape(-1, 2)
        if not len(xy):
            return np.zeros(0)
        if memo is None:
            return look(xy)
        keys = list(map(tuple, np.round(xy * 100.0).astype(np.int64).tolist()))
        out = np.array([memo.get(k, np.nan) for k in keys], np.float64)
        miss = np.flatnonzero(np.isnan(out))
        if len(miss):
            z = look(xy[miss])
            out[miss] = z
            if len(memo) + len(miss) > GROUND_MEMO_MAX:
                memo.clear()
            for n, zz in zip(miss.tolist(), z.tolist()):
                memo[keys[n]] = zz
        return out
    return ground


GRADE_HALF_M = BALLAST_TOP_M + 1.2     # m: the ground graded flat to the bed line either side of a track (grade_plans)
# An abandoned line (wear 2) is not graded: no cutting or embankment is laid for it, its worn track lies on the ground
# as it is (tile_mesh follows the drawn ground). Its mapped remnants are often a few metres off their old bed, or the
# bed is gone: graded to a 2 % line, they cut walls into mountainsides (GitHub #706, Nikko and Mount Haruna) and
# trenches across the land (#837). A disused line (1) keeps its bed, graded as before.
UNGRADED_WEAR = 2
GRADE_REACH_M = 60.0        # m round a grading cell the lines are taken for its bed plans (the track's smoothing and the
                            # ramps near it: REACH_M's 200 m had every cell profile ~1 km of line, 8.5 of 13 s at the A11)


def cells_near(frame, box, reach=REACH_M):
    """The map cells within reach of box (x0, y0, x1, y1) in frame."""
    import mapcells
    x0, y0, x1, y1 = box[0] - reach, box[1] - reach, box[2] + reach, box[3] + reach
    lat, lon, _ = frame.enu_to_geodetic(np.array([x0, x1, x0, x1]), np.array([y0, y0, y1, y1]), 0.0)
    return mapcells.cells_for_bbox(float(np.min(lat)), float(np.min(lon)), float(np.max(lat)), float(np.max(lon)))


def _flushed(frame, cells, box, rails, prof, ground, tunnels):
    """prof (profile's [(bed, ground)] per part) with the ground parts' beds brought flush with the roads they cross at
    grade (_flush, as tile_mesh does when it builds the track), for the grader's plans: the terrain is then graded to the
    bed the mesh really has, not to the smoothed ground under a bed that tile_mesh lowers to a road's level (the ballast
    and sleepers sank under the terrain by the road, Kalynivka 2026-11-05)."""
    cr = crossings(frame, cells, box, reach=GRADE_REACH_M) + list(tunnels)
    if not cr:
        return prof
    zc = np.asarray(ground(np.concatenate([ln for ln, _ in cr])), np.float64)
    crossers, k = [], 0
    for line, hw in cr:
        crossers.append((line, hw, zc[k:k + len(line)]))
        k += len(line)
    out = []
    for (_fid, _c, kind, xy), (zb, zg) in zip(rails, prof):
        if kind == "ground":
            blk = _blocked(xy, zb, crossers)
            if blk.any():
                zb = _flush(_arc(xy), zb, blk, np.asarray(ground(xy[blk]), np.float64))
        out.append((zb, zg))
    return out


def grade_plans(frame, cells, box, elev):
    """[(fid, xy (k, 2), z (k,))]: the bed line of each on-ground rail part near box, in elev's heights (elev(lat, lon):
    the grader's DEM), for grading.Grader to lay the ground to (the cutting's walls back from the track, the ramps'
    embankments filled). tile_mesh then finds the ground at its bed. Pure given elev."""
    cells = list(cells)
    tunnels = road_underpasses(frame, cells, box, GRADE_REACH_M)
    worn = {}
    rails = parts(frame, cells, box, reach=GRADE_REACH_M, clip=True, worn=worn)
    if not rails:
        return []
    ungraded = {fid for fid, w in worn.items() if w >= UNGRADED_WEAR}
    memo_key = ("dem", round(float(getattr(frame, "lat0", 0.0)), 7), round(float(getattr(frame, "lon0", 0.0)), 7))
    memo = _GROUND_MEMO.setdefault(memo_key, {})

    def ground(xy):
        # (the DEM's heights, remembered per point: every cell's plan asks for the same lines' points)
        xy = np.asarray(xy, np.float64).reshape(-1, 2)
        keys = list(map(tuple, np.round(xy * 100.0).astype(np.int64).tolist()))
        out = np.array([memo.get(k, np.nan) for k in keys], np.float64)
        miss = np.flatnonzero(np.isnan(out))
        if len(miss):
            la, lo, _ = frame.enu_to_geodetic(xy[miss, 0], xy[miss, 1], 0.0)
            z = np.asarray(elev(la, lo), np.float64)
            out[miss] = z
            if len(memo) + len(miss) > GROUND_MEMO_MAX:
                memo.clear()
            for n, zz in zip(miss.tolist(), z.tolist()):
                memo[keys[n]] = zz
        return out
    pad = GRADE_HALF_M + 15.0
    out = []
    tunnels = tunnels + coarse_underpasses(frame, cells, box, rails, GRADE_REACH_M)
    prof = profile(rails, ground, tunnels)
    if railxing.ENABLED:        # (the bed flush with the road at a level crossing: the ground is graded to it, else the
        prof = _flushed(frame, cells, box, rails, prof, ground, tunnels)     # ballast and sleepers lay under the ground)
    for (fid, _c, kind, xy), (zb, _zg) in zip(rails, prof):
        if kind != "ground" or fid in ungraded:
            continue
        if _box_hit((xy[:, 0].min(), xy[:, 1].min(), xy[:, 0].max(), xy[:, 1].max()),
                    (box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad)):
            out.append((fid, xy, zb))
    return out


def gauge(cls, sign_style=None):
    if cls == "narrow_gauge":
        return NARROW_GAUGE_M
    return FI_GAUGE_M if sign_style == "fi" else GAUGE_M


def _box_hit(b, box):
    return not (b[2] < box[0] or b[0] > box[2] or b[3] < box[1] or b[1] > box[3])


def parts(frame, cells, box, reach=REACH_M, clip=False, worn=None, gauges=None, levels=None):
    """[(fid, class, kind, xy (k, 2))]: the rail parts ("ground" / "bridge" / "tunnel") of the lines drawn
    (drawn(); worn: a dict given here gets {fid: wear} of the worn ones, gauges {fid: m} OSM's gauge where tagged,
    levels {fid: its OSM layer/level} where tagged) within reach of box (x0, y0, x1, y1), resampled every STEP_M
    from each part's start, each feature once (by id).
    Each part must intersect that window: a feature's bbox can cover far-away bridges or a long bend around it.
    clip: the ground parts cut to their points within reach of box (a segment of line runs on for km, and every one of
    its points was profiled for each cell and tile: _clip); the bridges stay whole (a deck runs between its ends)."""
    cells = list(cells)
    near = (box[0] - reach, box[1] - reach, box[2] + reach, box[3] + reach)
    seen, out = set(), []
    wear_of = worn if worn is not None else {}
    for feats in cells:
        if feats is None or len(feats) == 0 or "subtype" not in feats.attrs:
            continue
        sub = feats.attrs["subtype"]
        fb = feats.boxes(frame)
        for i in range(len(feats)):
            if str(sub[i]) != "rail":
                continue
            fid = str(feats.ids[i])
            if fid in seen or not _box_hit(fb[i], near):
                continue
            got = drawn(feats, i, bridges=True)
            if got is None:
                continue
            c = got[0]
            seen.add(fid)
            if got[1]:
                wear_of[fid] = got[1]
            if gauges is not None:
                g = osm_gauge(feats, i)
                if g:
                    gauges[fid] = g
            if levels is not None:
                lv = None
                if getattr(feats, "rules", None):
                    try:
                        lv = float(roads._rule_value(feats.rules.get("level"), i))
                    except (TypeError, ValueError):
                        lv = None
                if lv is None:
                    ol = feats.attrs.get("osm_layer")
                    try:
                        lv = float(str(ol[i]).strip())
                    except (TypeError, ValueError, IndexError):
                        lv = None
                if lv is not None and np.isfinite(lv):
                    levels[fid] = lv
            for kind, rings in _pieces(feats, i):
                if c == "tram" and kind != "bridge":
                    continue                    # street track stays draped in rails.py, never terrain grading
                for ring in rings:
                    e, n = mapgeom.enu_xy(feats, frame, ring)
                    xy = np.c_[e, n]
                    if not _box_hit((xy[:, 0].min(), xy[:, 1].min(), xy[:, 0].max(), xy[:, 1].max()), near):
                        continue
                    if not roads.clip_polyline(xy, *near):
                        continue
                    # Test relevance before resampling, but keep the original part: a nearby bridge needs both
                    # ends, and the ground samples must keep their alignment from the part's original start.
                    xy = _resample(xy, STEP_M)
                    if len(xy) >= 2:
                        out.append((fid, c, kind, xy))
    out.sort(key=lambda p: (p[0], p[2], float(p[3][0, 0]), float(p[3][0, 1])))
    if any(kind == "ground" and wear_of.get(fid) for fid, _c, kind, _xy in out):
        out = _cut_worn_at_motorways(out, wear_of, motorway_crossings(frame, cells, box, reach))
    out = _drop_stubs(out)
    out = _span_underpasses(out, road_underpasses(frame, cells, box, reach))
    out = _switch_connectors(out, near)
    return _clip(out, near) if clip else out



# A short road tunnel may be tagged only on the lower road, with no bridge flag on
# the railway above. Its ballast and grading must not fill the lower carriageway.
UNDERPASS_CLEAR_M = 5.0     # m under a rail deck over a road (3.6 clipped a bus at Abbey Street, 2026-11-07)
UNDERPASS_SIDE_M = 1.0
UNDERPASS_MIN_SIN = 0.25    # conservatively exclude near-parallel tracks beside a tunnel


def road_underpasses(frame, cells, box, reach=REACH_M):
    """Short mapped road tunnels near box as (xy, half width), each part once."""
    near = (box[0] - reach, box[1] - reach, box[2] + reach, box[3] + reach)
    out, seen = [], set()
    for feats in cells:
        if feats is None or not len(feats):
            continue
        specs = roads.tunnel_specs(feats)
        boxes = feats.boxes(frame)
        for i, sp in enumerate(specs):
            if sp is None or not _box_hit(boxes[i], near):
                continue
            for ring in roads.tunnel_parts(feats, i):
                e, n = mapgeom.enu_xy(feats, frame, ring)
                xy = np.c_[e, n]
                if len(xy) < 2 or _arc(xy)[-1] > roads.UNDERPASS_MAX_M:
                    continue
                key = (str(feats.ids[i]), tuple(np.round(xy[[0, -1]].ravel(), 2)))
                if key not in seen:
                    seen.add(key)
                    out.append((xy, sp.width / 2 + UNDERPASS_SIDE_M))
    return out


# Where the elevation is only the coarse global model (dem.coarse_at; the server sets COARSE_AT to its Dem's), a rail
# bridge over a ground road stands on an embankment the 30 m data smeared away: the track is raised over the road (its
# piece COARSE_PIECE_M either side of the crossing, as a tunnel's mouths) and ramps at GRADE, and the road stays at
# grade (grading leaves rail spans alone there). Perm, GitHub #414: the underpass rule dug Первомайская улица 7.5 m
# under the Балмошная–Лёвшино line, a walled trench 400 m long where the street is at grade under an embankment.
COARSE_AT = None
COARSE_PIECE_M = 25.0
# ... and on lidar wherever the elevation shows no grade separation (a rail bridge built after the survey: Davis Road
# under the Armadale Line at Kelmscott, WA, where the 5 m DEM still has the old level crossing; the underpass rule dug
# the street 8 m under the rail, the user 2026-10-29). grading's UNDER_LIDAR rule (PR #504) keeps such a road at its
# own level where the deck (its ends' ground) stands less than roads.AT_GRADE_M over it; flat_rail_crossing asks the
# same of a rail bridge, so every crossing that rule leaves at grade gets its track raised over the road (as on coarse
# elevation), not a deck lying across the road. RAW_ELEV: the server sets it to the grader's own elevation
# (PROFILE_ZOOM). Off with BNE_RAIL_FLAT_SEP=0, or with the rule itself (BNE_UNDER_LIDAR=off).
FLAT_SEP_ON = os.environ.get("BNE_RAIL_FLAT_SEP", "0") == "1" and os.environ.get("BNE_UNDER_LIDAR", "on") != "off"    # (opt-in: grading.NEIGHBOUR_ON)
FLAT_SEP_M = roads.AT_GRADE_M
FLAT_SAMPLE_M = (-12.0, -6.0, 0.0, 6.0, 12.0)  # m along the road either side of the crossing (the median)
RAW_ELEV = None


def flat_rail_crossing(frame, bridge_xy, road_xy, at, elev=None):
    """Does the natural ground show no separation where road_xy (k, 2) crosses under the rail bridge bridge_xy at arc
    at (m along the road)? As grading's UNDER_LIDAR rule: the deck there (its ends' ground, interpolated along it, the
    lowest within roads.DECK_CLEAR_REACH_M of the crossing) stands less than FLAT_SEP_M (roads.AT_GRADE_M) over the
    road's ground (the median of FLAT_SAMPLE_M). elev(lat, lon): RAW_ELEV by default; False without one (as before).
    Pure given elev."""
    elev = elev if elev is not None else RAW_ELEV
    if not FLAT_SEP_ON or elev is None:
        return False
    road_xy = np.asarray(road_xy, np.float64)[:, :2]
    b = np.asarray(bridge_xy, np.float64)[:, :2]
    s = _arc(road_xy)
    a = np.clip(at + np.asarray(FLAT_SAMPLE_M), 0.0, s[-1])
    pts = np.r_[np.c_[np.interp(a, s, road_xy[:, 0]), np.interp(a, s, road_xy[:, 1])], b[[0, -1]]]
    la, lo, _ = frame.enu_to_geodetic(pts[:, 0], pts[:, 1], 0.0)
    try:
        z = np.asarray(elev(np.asarray(la, np.float64), np.asarray(lo, np.float64)), np.float64).ravel()
    except Exception:
        return False
    if not np.all(np.isfinite(z)):
        return False
    # (where along the bridge the road crosses: the deck within roads.DECK_CLEAR_REACH_M of it, as the rule takes it)
    sb = _arc(b)
    L = max(float(sb[-1]), 1e-9)
    hits = _seg_hits(b, road_xy)
    arc_b = hits[0] if hits else 0.5 * L
    corridor = np.clip([arc_b - roads.DECK_CLEAR_REACH_M, arc_b + roads.DECK_CLEAR_REACH_M], 0.0, L)
    deck = float(np.min(z[-2] + (z[-1] - z[-2]) * corridor / L))
    return bool(deck < float(np.median(z[:-2])) + FLAT_SEP_M)


def _coarse_here(frame, box):
    if COARSE_AT is None:
        return False
    try:
        la, lo, _ = frame.enu_to_geodetic(0.5 * (box[0] + box[2]), 0.5 * (box[1] + box[3]), 0.0)
        return bool(COARSE_AT(float(la), float(lo)))
    except Exception:
        return False


def _seg_hits(a, b):
    """Arc positions along polyline a where it crosses polyline b (proper segment intersections). Pure."""
    out = []
    sa = _arc(a)
    for i in range(len(a) - 1):
        p, r = a[i], a[i + 1] - a[i]
        for j in range(len(b) - 1):
            q, t = b[j], b[j + 1] - b[j]
            den = r[0] * t[1] - r[1] * t[0]
            if abs(den) < 1e-12:
                continue
            u = ((q[0] - p[0]) * t[1] - (q[1] - p[1]) * t[0]) / den
            v = ((q[0] - p[0]) * r[1] - (q[1] - p[1]) * r[0]) / den
            if 0.0 <= u <= 1.0 and 0.0 <= v <= 1.0:
                out.append(float(sa[i] + u * (sa[i + 1] - sa[i])))
    return out


def coarse_underpasses(frame, cells, box, rails, reach=REACH_M):
    """[(xy, half width)] (as road_underpasses): on coarse elevation (COARSE_AT), each ground road's piece
    COARSE_PIECE_M either side of where a rail bridge part crosses it."""
    bridges = [xy for (_fid, _c, kind, xy) in rails if kind == "bridge" and len(xy) >= 2]
    if not bridges:
        return []
    coarse = _coarse_here(frame, box)
    if not coarse and not (FLAT_SEP_ON and RAW_ELEV is not None):
        return []
    out = []
    for line, half in crossings(frame, cells, box, reach):
        if len(line) < 2:
            continue
        sl = _arc(line)
        for b in bridges:
            for sc in _seg_hits(line, b):
                if not coarse and not flat_rail_crossing(frame, b, line, sc):
                    continue
                a0, a1 = max(0.0, sc - COARSE_PIECE_M), min(float(sl[-1]), sc + COARSE_PIECE_M)
                if a1 - a0 < 1.0:
                    continue
                ss = np.linspace(a0, a1, max(3, int((a1 - a0) / STEP_M) + 1))
                piece = np.c_[np.interp(ss, sl, line[:, 0]), np.interp(ss, sl, line[:, 1])]
                out.append((piece, half + UNDERPASS_SIDE_M))
    return out


def _underpass_distance(xy, line):
    """Distance to a finite tunnel centreline, and fraction along it, per point."""
    s = _arc(line)
    dist = np.full(len(xy), np.inf)
    along = np.zeros(len(xy))
    for j, (a, b) in enumerate(zip(line[:-1], line[1:])):
        d = b - a
        length = float(np.hypot(*d))
        if length < 1e-6:
            continue
        t = np.clip((xy - a) @ d / (length * length), 0.0, 1.0)
        dd = np.linalg.norm(xy - (a + t[:, None] * d), axis=1)
        take = dd < dist
        dist[take] = dd[take]
        along[take] = (s[j] + t[take] * length) / max(s[-1], 1e-6)
    return dist, along


def _underpass_masks(rails, line, half, margin):
    """Points near a tunnel, connected locally to a track that actually crosses it.

    A crossing on one track must not qualify a neighbouring track. Connect only
    the runs inside the tunnel's padded footprint: this preserves source-feature
    splits without following a turnout or a returning track far outside it.
    """
    masks = [np.zeros(len(xy), bool) for *_, xy in rails]
    candidates, runs, owners = [], [], []
    pad = half + margin
    near = (*(line.min(0) - pad), *(line.max(0) + pad))
    direction = line[-1] - line[0]
    for n, (*_, xy) in enumerate(rails):
        covered = np.zeros(len(xy), bool)
        if _box_hit((*xy.min(0), *xy.max(0)), near):
            tangent = np.gradient(xy, axis=0)
            cross = np.abs(tangent[:, 0] * direction[1] - tangent[:, 1] * direction[0])
            cross /= np.maximum(np.linalg.norm(tangent, axis=1) * np.linalg.norm(direction), 1e-9)
            distance, _ = _underpass_distance(xy, line)
            covered = (distance <= pad) & (cross > UNDERPASS_MIN_SIN)
        candidates.append(covered)
        spans = covered[:-1] | covered[1:]
        cuts = np.flatnonzero(np.diff(np.r_[False, spans, False]))
        for a, b in zip(cuts[::2], cuts[1::2]):
            runs.append(xy[a:b + 1])
            owners.append((n, a, b))
    crossings = roads._crossings(runs, [line])
    if not crossings:
        return masks
    _nodes, edges, adj = roads._chain_graph(runs)
    connected = {node for n, _s, _xy in crossings for node in edges[n][:2]}
    pending = list(connected)
    while pending:
        for node, _length in adj[pending.pop()]:
            if node not in connected:
                connected.add(node)
                pending.append(node)
    for (n, a, b), (start, _end, _s) in zip(owners, edges):
        if start in connected:
            masks[n][a:b + 1] = candidates[n][a:b + 1]
    return masks


def _span_underpasses(rails, tunnels):
    """Replace the ground track over mapped road tunnels with supported spans.

    Keep the original sample lattice and shared endpoints, including across source
    feature and tile boundaries. The margin keeps the approach ballast's feet out
    of the carriageway as well as the bridge itself.
    """
    if not tunnels:
        return rails
    covered = [np.zeros(len(xy), bool) for *_, xy in rails]
    for line, half in tunnels:
        for keep, mask in zip(covered, _underpass_masks(rails, line, half, FOOT_MAX_M + STEP_M)):
            keep |= mask
    out = []
    for (fid, cls, kind, xy), mask in zip(rails, covered):
        if kind != "ground":
            out.append((fid, cls, kind, xy))
            continue
        spans = mask[:-1] | mask[1:]
        cuts = np.r_[0, np.flatnonzero(spans[1:] != spans[:-1]) + 1, len(spans)]
        for a, b in zip(cuts[:-1], cuts[1:]):
            out.append((fid, cls, "bridge" if spans[a] else kind, xy[a:b + 1]))
    return out


def _clip(rails, near):
    """rails with each ground part cut to its runs of points inside near (x0, y0, x1, y1), one STEP_M more each side
    (the same sample points as the whole part: they lie every STEP_M from its start). Pure."""
    out = []
    for fid, c, kind, xy in rails:
        if kind != "ground":
            out.append((fid, c, kind, xy))
            continue
        inside = (xy[:, 0] >= near[0]) & (xy[:, 0] <= near[2]) & (xy[:, 1] >= near[1]) & (xy[:, 1] <= near[3])
        if inside.all():
            out.append((fid, c, kind, xy))
            continue
        grow = inside.copy()
        grow[1:] |= inside[:-1]
        grow[:-1] |= inside[1:]
        idx = np.flatnonzero(grow)
        if not len(idx):
            continue
        for run in np.split(idx, np.flatnonzero(np.diff(idx) > 1) + 1):
            if len(run) >= 2:
                out.append((fid, c, kind, xy[run[0]:run[-1] + 1]))
    return out


SWITCH_REACH_M = 10.0       # m: a track end this near another through track is a merge (a turnout)
SWITCH_LEN_M = 16.0         # m of the branch rounded into the through track at the switch
SWITCH_ANGLE_DEG = 35.0     # a branch meeting the through track at a steeper angle is a crossing, not a turnout
SWITCH_MAX = 80             # turnouts made near one tile at most
SWITCH_END_CLEAR_M = 2.0    # the through point stays this far from its own line's ends (not a head-to-head join)


def _line_s(xy):
    return np.r_[0.0, np.cumsum(np.hypot(*np.diff(xy[:, :2], axis=0).T))]


def _at_s(xy, s):
    """(point (3,), unit tangent) at arclength s along xy (k, 2 or 3)."""
    d = np.diff(xy[:, :2], axis=0)
    L = np.hypot(*d.T)
    j = int(np.clip(np.searchsorted(np.r_[0.0, np.cumsum(L)], s) - 1, 0, len(d) - 1))
    f = (s - float(np.r_[0.0, np.cumsum(L)][j])) / max(float(L[j]), 1e-9)
    p = xy[j].astype(np.float64).copy()
    p[:2] += f * d[j]
    t = d[j] / max(float(L[j]), 1e-9)
    return p, t


def _switch_connectors(rails, near):
    """rails with every ground track end that merges into another through ground track rounded in to it (a turnout:
    tracks old and new joined at a kink derail a train, the user 2026-11-08). All the ground segments are one table:
    an end is checked against it once, not part by part (a station throat took 0.5 s a tile the naive way)."""
    gidx = [i for i, r in enumerate(rails) if r[2] == "ground" and len(r[3]) >= 3]
    if len(gidx) < 2:
        return rails
    out = list(rails)
    fid_num = {}
    aa, dd, ll, own, s0, seg_fid, plen = [], [], [], [], [], [], {}
    for i in gidx:
        xy = np.asarray(rails[i][3], np.float64)
        a, d = xy[:-1, :2], np.diff(xy[:, :2], axis=0)
        L2 = np.maximum((d * d).sum(1), 1e-12)
        cum = np.r_[0.0, np.cumsum(np.sqrt(L2))]
        aa.append(a)
        dd.append(d)
        ll.append(L2)
        own.append(np.full(len(a), i, np.int64))
        s0.append(cum[:-1])
        fn = fid_num.setdefault(rails[i][0], len(fid_num))
        seg_fid.append(np.full(len(a), fn, np.int64))
        plen[i] = float(cum[-1])
    A, D, L2 = np.concatenate(aa), np.concatenate(dd), np.concatenate(ll)
    OWN, S0, SEGFID = np.concatenate(own), np.concatenate(s0), np.concatenate(seg_fid)
    slen = np.sqrt(L2)
    plen_all = np.array([plen[o] for o in OWN], np.float64)
    through_seg = (S0 >= SWITCH_END_CLEAR_M) & (S0 + slen <= plen_all - SWITCH_END_CLEAR_M)
    made = 0
    for idx in gidx:
        fid, cls, kind, xy = out[idx]
        fn = fid_num[fid]
        for end in (-1, 0):
            if made >= SWITCH_MAX:
                return out
            xy = np.asarray(xy, np.float64)
            E = xy[-1, :2] if end == -1 else xy[0, :2]
            if not _box_hit((E[0], E[1], E[0], E[1]), near):
                continue
            inward = (xy[0, :2] - xy[1, :2]) if end == 0 else (xy[-1, :2] - xy[-2, :2])
            n = float(np.hypot(*inward))
            if n < 1e-9:
                continue
            approach = inward / n               # the direction a train takes into this end
            t = np.clip(((E - A) * D).sum(1) / L2, 0.0, 1.0)
            q = A + D * t[:, None]
            ds = np.hypot(*(q - E).T)
            bad = ~through_seg | (OWN == idx) | (SEGFID == fn) | (ds > SWITCH_REACH_M)
            ds = np.where(bad, np.inf, ds)
            cand = np.flatnonzero(np.isfinite(ds))
            if not len(cand):
                continue
            order = cand[np.argsort(ds[cand])[:5]]       # (a parallel track can be nearer than the merge)
            best = None
            for k in order:
                dist = float(ds[k])
                P, T = q[k], D[k] / slen[k]
                if dist > 0.2:
                    to_p = (P - E) / dist
                    if float(np.dot(to_p, approach)) < 0.35:
                        continue                # the other track is beside the end, not ahead of it
                if float(np.dot(T, approach)) < 0.0:
                    T = -T
                ang = math.degrees(math.acos(max(-1.0, min(1.0, float(np.dot(T, approach))))))
                if ang <= SWITCH_ANGLE_DEG:
                    best = (P, T)
                    break
            if best is None:
                continue
            P, T = best
            L = float(_line_s(xy)[-1])
            sQ = min(SWITCH_LEN_M, 0.6 * L)
            Q, Qtan = _at_s(xy, sQ if end == 0 else L - sQ)
            dQ = -Qtan if end == 0 else Qtan
            span = max(4.0, float(np.hypot(*(P - Q[:2]))))
            m = 0.55 * span
            tt = np.linspace(0.0, 1.0, 9)
            curve = ((2 * tt ** 3 - 3 * tt ** 2 + 1)[:, None] * Q[:2]
                     + (tt ** 3 - 2 * tt ** 2 + tt)[:, None] * (m * dQ)
                     + (-2 * tt ** 3 + 3 * tt ** 2)[:, None] * P
                     + (tt ** 3 - tt ** 2)[:, None] * (m * T))
            curve = _resample(curve, STEP_M)
            if len(curve) < 2:
                continue
            # the branch's last SWITCH_LEN_M is replaced by the curve, so its rail, ballast and bed bend into the
            # through track instead of ending in a kink (a train's wheels cannot follow a dead end)
            if end == -1:
                j = int(np.searchsorted(_line_s(xy), L - sQ))
                j = max(1, min(j, len(xy) - 1))
                xy = np.vstack([xy[:j, :2], curve])
            else:
                j = int(np.searchsorted(_line_s(xy), sQ))
                j = max(1, min(j, len(xy) - 1))
                xy = np.vstack([curve[::-1], xy[j:, :2]])
            out[idx] = (fid, cls, kind, xy)
            made += 1
    return out


MIN_LINE_M = 50.0           # m: connected track shorter than this is left out (a siding's piece cut off by its
                            # abandoned rest, 12 m under a bridge on its own, 2026-10-25)


def _drop_stubs(rails):
    """rails without the connected groups of parts (ends joined within 0.5 m) shorter than MIN_LINE_M in all."""
    if not rails:
        return rails
    nodes, edges, _ = roads._chain_graph([xy for *_, xy in rails])
    parent = list(range(len(nodes)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for a, b, _s in edges:
        parent[find(a)] = find(b)
    total = {}
    for a, _b, s in edges:
        total[find(a)] = total.get(find(a), 0.0) + float(s[-1])
    # A tram's approaches are drawn by rails.py, not present in this graph. Its
    # short mapped bridges are complete structures, not disconnected sidings.
    return [r for r, (a, _b, _s) in zip(rails, edges) if r[1] == "tram" or total[find(a)] >= MIN_LINE_M]


WEAR_FLAGS = (("is_abandoned", 2), ("is_disused", 1))    # a line's wear: 1 out of use, 2 abandoned (drawn worn: the
# user, 2026-10-27, "id still like abandond lines to show. perhapse with some grass on them or sumilate wear")
UNBUILT_FLAGS = ("is_under_construction",)              # not drawn (no track yet), as OSM's proposed lines
SHORT_TUNNEL_M = 60.0       # m: a "tunnel" this short is the line passing under a road bridge (OSM tags those
                            # tunnel too): track there (the track stopped either side of an overpass, 2026-10-25)
TUNNEL_BORE_W = 6.2         # m: the bore between its walls, as roadmesh.tunnel reads a "tunnel" chunk's w
                            # (half width + TUNNEL_SHOULDER either side = 4.3 m to each wall). 4.6 left 0.9 m either
                            # side of a 2.8 m train; a UK single-track bore is ~6 m, and regular rolling stock must
                            # fit without scaling the track (the user, 2026-11-05)
TUNNEL_DEPTH_M = 18.0       # m under water-covered ground an undersea tunnel's bed is laid (the Channel: the seabed
                            # is over the tube; the road tunnels' wet exclusion would leave it out)
TUNNEL_WET_RAMP_M = 3000.0  # m over which that depth fades in at a coast: 48 m under the Channel over 3 km = 1.6 %,
                            # a main line's grade (300 m was a 16 % wall at the shore)
SUBWAY_DEPTH_M = 14.0       # m under the ground a railway=subway line is laid (Overture rarely marks depth; the
                            # Underground has no surface expression to read one from)
SUBWAY_RAMP_M = 300.0       # m over which a subway's depth fades in at its ends (4.7 %; 600 m left its tube above ground)
RAIL_TUNNEL_DEPTH_M = 12.0  # m under the ground any other rail tunnel is laid (a flat city's lower hull is the
                            # street itself, so tunnels were laid AT street level: their tubes and lamps stood in
                            # the road, stations on top of them, 2026-11-07)
RAIL_TUNNEL_LEVEL_M = 4.0   # m per OSM layer below grade (-4: an Underground line ~16 m down)
RAIL_TUNNEL_MIN_M = 8.0     # m: the least depth of a mapped rail tunnel (a cut-and-cover box)
RAIL_TUNNEL_MAX_M = 40.0
RAIL_TUNNEL_RAMP_M = 300.0  # m over which that depth fades in from a portal (4 %: the Underground's worst; 600 m left a
                            # 6.8 m bore standing out of the ground for its first ~340 m, a tube lying on the surface)


def wear(feats, i):
    """Feature i's wear by its flags over most of its length: 0 in use, 1 disused, 2 abandoned; None not built yet."""
    t = feats.rules.get("flags")
    if t is None or len(t.feature) == 0:
        return 0
    rows = [(float(t.end[j]) - float(t.start[j]), str(t.value[j])) for j in t.rows(i)]
    if sum(L for L, v in rows if any(g in v for g in UNBUILT_FLAGS)) > 0.5:
        return None
    for flag, w in WEAR_FLAGS:
        if sum(L for L, v in rows if flag in v) > 0.5:
            return w
    return 0


def drawn(feats, i, bridges=False):
    """(class, wear) of a built railway track, or None. bridges also admits trams:
    only their mapped bridge pieces belong in the 3D mesh; ground trams remain
    street track in rails.py, not ballast, grading or inferred underpasses."""
    sub = feats.attrs.get("subtype")
    if sub is None or str(sub[i]) != "rail":
        return None
    w = wear(feats, i)
    if w is None:
        return None
    cls = feats.attrs.get("class")
    c = str(cls[i]) if cls is not None else "standard_gauge"
    osm = feats.attrs.get("osm_railway")
    if osm is not None and osm[i]:          # (OSM's own railway value: railtags.py, the weekly rail file)
        v = str(osm[i])
        if v == "tram":
            return ("tram", w) if bridges else None
        if v in OSM_RAILWAY_NOT:
            return None
        if v in OSM_RAILWAY_WEAR:
            w = OSM_RAILWAY_WEAR[v]
            if v == "subway":
                return ("subway", w)        # (a deep bore: _pieces, SUBWAY_DEPTH_M)
            return (c if c in DRAWN else "standard_gauge", w)
    if c == "unknown" and w:
        c = "standard_gauge"
    return (c, w) if c in DRAWN or (bridges and c == "tram") else None


# OpenStreetMap's railway values (railtags.py), when the weekly file has the line: drawn with this wear, or not at all
OSM_RAILWAY_WEAR = {"rail": 0, "light_rail": 0, "narrow_gauge": 0, "preserved": 0, "subway": 0,
                    "disused": 1, "abandoned": 2}
OSM_RAILWAY_NOT = {"proposed", "construction", "razed", "dismantled", "planned", "tram", "monorail",
                   "funicular", "miniature", "platform"}


def osm_gauge(feats, i):
    """m between the rails by OSM's gauge tag (mm, the first of several), or None."""
    g = feats.attrs.get("osm_gauge")
    if g is None or not g[i]:
        return None
    m = re.match(r"\s*(\d{3,4})", str(g[i]))
    if not m:
        return None
    mm = int(m.group(1))
    return mm / 1000.0 if 381 <= mm <= 1676 else None


def _pieces(feats, i):
    """[("ground" | "bridge" | "tunnel", [lon/lat polylines])] of feature i: its level ranges, a short tunnel
    (SHORT_TUNNEL_M: the line under a road bridge) on the ground, a longer one a tunnel of its own; a
    railway=subway with no mapped ranges is a tunnel its whole length."""
    rngs = roads.level_ranges(feats, i)
    osm = feats.attrs.get("osm_railway")
    if not rngs:
        # (OSM's own tags where Overture has no ranges: the rail shard carries tunnel / bridge / layer)
        tunnel = feats.attrs.get("osm_tunnel")
        bridge = feats.attrs.get("osm_bridge")
        if tunnel is not None and str(tunnel[i]).lower() in ("yes", "building_passage", "culvert"):
            return [("tunnel", roads.ground_parts(feats, i))]
        if bridge is not None and str(bridge[i]).lower() not in ("", "none", "no"):
            return [("bridge", roads.ground_parts(feats, i))]
        if osm is not None and str(osm[i]) == "subway":
            return [("tunnel", roads.ground_parts(feats, i))]
        return [("ground", roads.ground_parts(feats, i))]
    L = roads._feature_length_m(feats, i)
    rngs = [(a, b, "ground" if k == "tunnel" and (b - a) * L < SHORT_TUNNEL_M else k) for a, b, k in rngs]
    covered = [(a, b) for a, b, k in rngs if k != "ground"]
    ground, at = [], 0.0
    for a, b in covered:
        if a - at > roads.MIN_PART:
            ground.append((at, a))
        at = max(at, b)
    if 1.0 - at > roads.MIN_PART:
        ground.append((at, 1.0))
    ground += [(a, b) for a, b, k in rngs if k == "ground"]
    out = {"ground": [], "bridge": [], "tunnel": []}
    for part in feats.parts(i):
        for ring in part:
            if len(ring) < 2:
                continue
            for kind, spans in (("ground", ground), ("bridge", [(a, b) for a, b, k in rngs if k == "bridge"]),
                                ("tunnel", [(a, b) for a, b, k in rngs if k == "tunnel"])):
                for a, b in spans:
                    if b - a > roads.MIN_PART:
                        r = ring if (a, b) == (0.0, 1.0) else roads.sub_polyline(ring, a, b)
                        if len(r) >= 2:
                            out[kind].append(r)
    return list(out.items())


def crossings(frame, cells, box, reach=REACH_M):
    """[(xy (k, 2), half width)]: the ground roads near box (their carriageways cut the track at grade)."""
    near = (box[0] - reach, box[1] - reach, box[2] + reach, box[3] + reach)
    seen, out = set(), []
    for feats in cells:
        if feats is None or len(feats) == 0:
            continue
        specs = roads.road_specs(feats)
        fb = feats.boxes(frame)
        for i, sp in enumerate(specs):
            fid = str(feats.ids[i])
            if sp is None or fid in seen or not _box_hit(fb[i], near):
                continue
            seen.add(fid)
            for ring in roads.ground_parts(feats, i):
                e, n = mapgeom.enu_xy(feats, frame, ring)
                out.append((np.c_[e, n], sp.width / 2))
    return out


# GitHub #579: a road's sidewalks and kerbs stop where a railway crosses it at grade (the ballast runs across them)
WALK_CLEAR_M = BALLAST_TOP_M + BALLAST_H * BALLAST_SLOPE    # m from the track's centre: the ballast's foot
WALK_MIN_SIN = 0.3          # a track this square or more to a road crosses it (a parallel one beside it leaves it be)


def walk_cut_lines(frame, cells, box, reach=60.0):
    """[(xy (k, 2), half width)]: the drawn railway tracks on the ground near box (trams left out: street track, its
    sidewalks stay; Sweden's are rails.tram_lines), what a sidewalk crossing them stops at (roads.build_tile)."""
    near = (box[0] - reach, box[1] - reach, box[2] + reach, box[3] + reach)
    seen, out = set(), []
    for feats in cells:
        if feats is None or len(feats) == 0 or "subtype" not in feats.attrs:
            continue
        sub = feats.attrs["subtype"]
        fb = feats.boxes(frame)
        for i in range(len(feats)):
            if str(sub[i]) != "rail":
                continue
            fid = str(feats.ids[i])
            if fid in seen or not _box_hit(fb[i], near):
                continue
            seen.add(fid)
            if drawn(feats, i) is None:
                continue
            for kind, rings in _pieces(feats, i):
                if kind != "ground":
                    continue
                for ring in rings:
                    e, n = mapgeom.enu_xy(feats, frame, ring)
                    xy = np.c_[e, n]
                    if len(xy) >= 2 and _box_hit((xy[:, 0].min(), xy[:, 1].min(), xy[:, 0].max(), xy[:, 1].max()),
                                                 near):
                        out.append((xy, WALK_CLEAR_M))
    return out


def crosses(a, b, min_sin=WALK_MIN_SIN):
    """Does polyline b (k, 2) cross polyline a (m, 2) somewhere at |sin| >= min_sin between their segments?"""
    a = np.asarray(a, np.float64)[:, :2]
    b = np.asarray(b, np.float64)[:, :2]
    if len(a) < 2 or len(b) < 2:
        return False
    if a[:, 0].max() < b[:, 0].min() or b[:, 0].max() < a[:, 0].min() or             a[:, 1].max() < b[:, 1].min() or b[:, 1].max() < a[:, 1].min():
        return False
    p, r = a[:-1, None, :], (a[1:] - a[:-1])[:, None, :]
    q, s = b[None, :-1, :], (b[1:] - b[:-1])[None, :, :]
    rxs = r[..., 0] * s[..., 1] - r[..., 1] * s[..., 0]
    qp = q - p
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (qp[..., 0] * s[..., 1] - qp[..., 1] * s[..., 0]) / rxs
        u = (qp[..., 0] * r[..., 1] - qp[..., 1] * r[..., 0]) / rxs
        sin = np.abs(rxs) / (np.hypot(r[..., 0], r[..., 1]) * np.hypot(s[..., 0], s[..., 1]))
    hit = (t >= 0) & (t <= 1) & (u >= 0) & (u <= 1) & (sin >= min_sin)
    return bool(np.any(hit))


# A worn line (disused / abandoned) mapped across a motorway on the ground: the road was built over the old bed, and
# the rails are long gone there (a motorway has no level crossing). Drawn and graded, the track's bed line made the
# carriageway dip to it (GitHub #918: four such dips on the A20 in Mecklenburg-Vorpommern, the lines tagged historic,
# abandoned or dismantled). The track is cut across the carriageway and WORN_CUT_M past its edges. An abandoned line
# is cut at a trunk road too (rebuilt over it); a disused one only at a motorway (a trunk road's level crossing can
# still have its rails).
WORN_CUT_M = 6.0
WORN_CUT_CLASSES = {1: {"motorway"}, 2: {"motorway", "trunk"}}
WORN_CUT_MIN_SIN = 0.2      # (a crossing flatter than this cuts as if at this angle: the cut stays bounded)


def motorway_crossings(frame, cells, box, reach=REACH_M):
    """[(xy (k, 2), half width, class)]: the ground motorway and trunk carriageways (their links too) near box."""
    near = (box[0] - reach, box[1] - reach, box[2] + reach, box[3] + reach)
    seen, out = set(), []
    for feats in cells:
        if feats is None or len(feats) == 0 or "class" not in feats.attrs:
            continue
        clss = feats.attrs["class"]
        fb = None
        for i in range(len(feats)):
            c = str(clss[i])
            base = c[:-5] if c.endswith("_link") else c
            if base not in ("motorway", "trunk"):
                continue
            fid = str(feats.ids[i])
            if fb is None:
                fb = feats.boxes(frame)
            if fid in seen or not _box_hit(fb[i], near):
                continue
            seen.add(fid)
            st = roads.STYLE.get(c) or roads.STYLE.get(base)
            if st is None or roads.unbuilt(feats, i):
                continue
            for ring in roads.ground_parts(feats, i):
                e, n = mapgeom.enu_xy(feats, frame, ring)
                if len(e) >= 2:
                    out.append((np.c_[e, n], st[0] / 2.0, base))
    return out


def _segment_at(line, p):
    """The direction (b - a) of line's segment nearest point p. Pure."""
    a, b = line[:-1], line[1:]
    d = b - a
    t = np.clip(np.einsum("ij,ij->i", p - a, d) / np.maximum(np.einsum("ij,ij->i", d, d), 1e-9), 0.0, 1.0)
    k = int(np.argmin(np.linalg.norm(p - (a + t[:, None] * d), axis=1)))
    return d[k]


def _cut_worn_at_motorways(rails, wear_of, roads_xy):
    """rails with each worn ground part (wear_of {fid: 1 | 2}) cut where it crosses one of roads_xy ((xy, half width,
    class): motorway_crossings) of its wear's WORN_CUT_CLASSES: no point within the carriageway's half width (over
    the crossing's angle) and WORN_CUT_M either side of the crossing. Pure."""
    if not roads_xy:
        return rails
    out = []
    for fid, cls, kind, xy in rails:
        w = wear_of.get(fid)
        if kind != "ground" or not w:
            out.append((fid, cls, kind, xy))
            continue
        lines = [(line, half) for line, half, c in roads_xy if c in WORN_CUT_CLASSES.get(w, ())]
        if not lines:
            out.append((fid, cls, kind, xy))
            continue
        s = _arc(xy)
        cut = np.zeros(len(xy), bool)
        for (line, half), hits in zip(lines, roads._crossings_by([xy], [ln for ln, _h in lines])):
            for _pi, arc, p in hits:
                j = int(np.clip(np.searchsorted(s, arc) - 1, 0, len(xy) - 2))
                t = xy[j + 1] - xy[j]
                r = _segment_at(line, p)        # (the road's direction at the crossing)
                sin = abs(t[0] * r[1] - t[1] * r[0]) / max(np.linalg.norm(t) * np.linalg.norm(r), 1e-9)
                cut |= np.abs(s - arc) <= half / max(sin, WORN_CUT_MIN_SIN) + WORN_CUT_M
        if not cut.any():
            out.append((fid, cls, kind, xy))
            continue
        keep = ~cut
        idx = np.flatnonzero(keep)
        for run in np.split(idx, np.flatnonzero(np.diff(idx) > 1) + 1) if len(idx) else []:
            if len(run) >= 2:
                out.append((fid, cls, kind, xy[run[0]:run[-1] + 1]))
    return out


def _resample(xy, step):
    s = np.r_[0.0, np.cumsum(np.hypot(*np.diff(xy, axis=0).T))]
    if s[-1] <= 1e-6:
        return xy[:1]
    t = np.r_[np.arange(0.0, s[-1], step), s[-1]]
    if len(t) > 2 and t[-1] - t[-2] < 0.25 * step:     # (no sliver section at the end)
        t = np.r_[t[:-2], t[-1]]
    return np.c_[np.interp(t, s, xy[:, 0]), np.interp(t, s, xy[:, 1])]


def _arc(xy):
    return np.r_[0.0, np.cumsum(np.hypot(*np.diff(xy, axis=0).T))]


def _left(xy):
    d = np.gradient(xy, axis=0)
    d /= np.maximum(np.hypot(d[:, 0], d[:, 1]), 1e-9)[:, None]
    return np.c_[-d[:, 1], d[:, 0]]


def _neighbour_gaps(rails, zb=None):
    """{rail index: (left gaps (k,), right gaps (k,))} for the bridge parts: per sample, the lateral distance to the
    nearest sample of another bridge track within SHARED_GAP_M (inf where none), at the same level (zb: the parts'
    bed heights; a flyover above is no shared bed). Used to trim decks that run side by side (a station's tracks):
    each is trimmed to the midpoint, so two decks never overlap and no edge wall stands between the rails."""
    pts = {}
    for n, (_fid, cls, kind, xy) in enumerate(rails):
        if kind != "bridge" or cls == "tram":
            continue
        for k, (x, y) in enumerate(np.asarray(xy, np.float64)[:, :2]):
            pts.setdefault((int(math.floor(x / 8.0)), int(math.floor(y / 8.0))), []).append((n, k, x, y))
    out = {}
    for n, (_fid, cls, kind, xy) in enumerate(rails):
        if kind != "bridge" or cls == "tram":
            continue
        xy = np.asarray(xy, np.float64)[:, :2]
        u = _left(xy)
        gl, gr = np.full(len(xy), np.inf), np.full(len(xy), np.inf)
        near = SHARED_GAP_M
        for k, (p, uu) in enumerate(zip(xy, u)):
            cx, cy = int(math.floor(p[0] / 8.0)), int(math.floor(p[1] / 8.0))
            for gx in range(cx - 1, cx + 2):
                for gy in range(cy - 1, cy + 2):
                    for m, j, qx, qy in pts.get((gx, gy), ()):
                        if m == n:
                            continue
                        if zb is not None and abs(float(zb[m][j]) - float(zb[n][k])) > VIADUCT_MERGE_DZ_M:
                            continue
                        dx, dy = qx - p[0], qy - p[1]
                        lat = dx * uu[0] + dy * uu[1]
                        if abs(lat) < SHARED_MIN_GAP_M or abs(lat) > near:
                            continue
                        if abs(dx * uu[1] - dy * uu[0]) > 12.0:      # (a track ahead/behind, not beside)
                            continue
                        if lat > 0:
                            gl[k] = min(gl[k], lat)
                        else:
                            gr[k] = min(gr[k], -lat)
        out[n] = (gl, gr)
    return out


def _avg(v, r):
    """Running mean over 2r + 1 values, the ends extended along their grade (odd reflection: a straight grade stays
    straight to its end, and two parts meeting at a joint meet at its height)."""
    r = min(r, len(v) - 1)
    if r < 1:
        return np.asarray(v, np.float64)
    pad = np.r_[2 * v[0] - v[r:0:-1], v, 2 * v[-1] - v[-2:-r - 2:-1]]
    c = np.r_[0.0, np.cumsum(pad)]
    return (c[2 * r + 1:] - c[:-2 * r - 1]) / (2 * r + 1)


def bed_heights(z):
    """The bed line over ground heights z (every STEP_M): averaged over SMOOTH_M either side, twice (a smooth
    vertical curve), through a ridge and over a dip alike: the grader (grade_plans) cuts and fills the ground to it."""
    z = np.asarray(z, np.float64)
    if len(z) < 3:
        return z
    r = max(1, int(round(SMOOTH_M / STEP_M)))
    return _avg(_avg(z, r), r // 2)


def _envelope(s, req, grade=GRADE):
    """max over k of req[k] - grade * |s - s[k]|: the lowest line over every req that climbs at most grade."""
    out = np.array(req, np.float64)
    for k in range(1, len(s)):                  # forward then backward pass (s ascending)
        out[k] = max(out[k], out[k - 1] - grade * (s[k] - s[k - 1]))
    for k in range(len(s) - 2, -1, -1):
        out[k] = max(out[k], out[k + 1] - grade * (s[k + 1] - s[k]))
    return out


DECK_RAISE_MAX_M = 15.0   # m a bridge deck may rise over the straight chord between its ends to clear high
                            # ground under it (a bad-data ridge: over a real valley the chord already stands over the
                            # floor, so genuine gorge viaducts are unaffected)
SEED_LIFT_MAX_M = 20.0    # m a propagated deck height may lift a ground track over its own smoothed bed (one bad
                            # deck must not poison kilometres of connected line, and the graded embankment with it)


def deck_heights(s, z_ground, z_start, z_end):
    """A bridge's bed line: straight from z_start to z_end (the bed at its ends: a line keeps its grade over what it
    crosses, the stream or road under it goes down, and the bare-earth DEM has that dip), raised only where the ground
    under it stands higher than the track itself (bad data, a ridge), climbing at most GRADE. Pure. (A required 6 m
    clearance stood an 8 m culvert's track 6.8 m up on 4.5 m ramps, 2026-10-25.)"""
    s = np.asarray(s, np.float64)
    L = max(float(s[-1]), 1e-9)
    chord = z_start + (z_end - z_start) * s / L
    req = np.maximum(chord, np.asarray(z_ground, np.float64))
    # A single bad height under the deck (an unfiltered building/tree in the surface model, a DEM spike) must not
    # raise the whole deck: over a real valley the chord already stands over the floor, so only the raise over the
    # chord is capped (seen as a ~100 m deck hump with piers and graded embankment from one bad sample under it).
    req = np.minimum(req, chord + DECK_RAISE_MAX_M)
    return _envelope(s, req)


# A bridge's mapped ends often lie part way down the slopes of what it crosses (a cutting, a valley's sides): the
# bare-earth DEM there is the slope, not the track, which runs on at its embankment's level up to the deck. The Abbey
# Line's bridge over the M1 near Bricket Wood (51.7103, -0.3537): the track at 78.2 m on both sides, the mapped
# 46 m bridge's ends at 73.5 m down the cutting's sides, the motorway at 71.5 m; the deck laid between those ends
# blocked the motorway (the user's screenshot, 2026-10-31). The track keeps its level: within APPROACH_REACH_M beyond
# an end, where the ground along the track's line runs level (within its grade, APPROACH_FLAT_M long), that level less
# the grade back to the end is the deck's end at least, raised by at most APPROACH_RAISE_MAX_M. A track climbing a
# hillside on from the bridge (no level stretch) keeps the ground's height.
APPROACH_REACH_M = 40.0     # m beyond a bridge's end the track's level is looked for
APPROACH_FROM_M = 4.0       # m beyond the end the level stretch may start, at the earliest
APPROACH_FLAT_M = 16.0      # m of track over which the ground has to be level (within the grade, + APPROACH_TOL_M)
APPROACH_TOL_M = 0.4        # m: the DEM's own bumps on a level stretch
APPROACH_MIN_M = 0.5        # m: a smaller rise is left as it is
APPROACH_RAISE_MAX_M = 8.0  # m an end is raised at most (more: bad data, left as it is)
LINE_GRADE = {"light_rail": 0.05}   # a class's steepest grade where it is not GRADE's (light rail climbs to 5-6 %)
# A connected network of parts that are ALL bridge-tagged is a viaduct or a station (London Bridge: every track is
# bridge=viaduct/yes and layer=1, and no ground part anchors the network within the reach). Laid at the DEM under it
# (the street), the tracks sat at street level, each on its own end height: parallel tracks at 3.5-5 m differed by up
# to 1.5 m and their decks, edge walls and piers cut through each other (the user, 2026-11-04). Such a network is laid
# VIADUCT_CLEAR_M over the ground under it, so its tracks share the viaduct's level; a bridge that connects to a ground
# track keeps the ground track's ends (its true profile).
VIADUCT_CLEAR_M = 7.0       # m: a station/viaduct's ballast top over the ground under it (its deck underside ~5.6 m:
                            # a London bus clears; an overbridge's 4.5-5.5 m was clipping the bus at Abbey Street,
                            # and the long bridge-tagged runs were laid on the street at 0-1 m, 2026-11-07)
VIADUCT_MIN_LINE_M = 100.0  # m: a bridge run at least this long is a viaduct, not a short span


def approach_levels(xy, ground, grade=GRADE):
    """(start, end): per end of a bridge part xy (k, 2), (level m, reach m) of the level the track holds beyond it
    (APPROACH_*: the deck's end and the track up to reach m out are at it at least), or None where there is none.
    Beyond each end the line goes straight on along its last segment. ground(xy (n, 2)) -> z. Pure given ground."""
    return approach_levels_many([xy], ground, [grade])[0]


def approach_levels_many(xys, ground, grades=None):
    """approach_levels of many bridge parts with one ground call (its cost is per call). grades: per part (GRADE)."""
    d = np.arange(0.0, APPROACH_REACH_M + APPROACH_FLAT_M + 1e-9, STEP_M)
    rays = []
    for xy in xys:
        xy = np.asarray(xy, np.float64).reshape(-1, 2)
        for end, nxt in (((xy[0], xy[1]), (xy[-1], xy[-2])) if len(xy) >= 2 else ()):
            u = end - nxt
            n = float(np.hypot(*u))
            rays.append(end + (u / n)[None, :] * d[:, None] if n > 1e-6 else None)
        if len(xy) < 2:
            rays += [None, None]
    pts = [r for r in rays if r is not None]
    z_all = np.asarray(ground(np.concatenate(pts)), np.float64).ravel() if pts else np.zeros(0)
    found, k = [], 0
    w = int(round(APPROACH_FLAT_M / STEP_M))
    for j, r in enumerate(rays):
        if r is None:
            found.append(None)
            continue
        z = z_all[k:k + len(d)]
        k += len(d)
        grade = grades[j // 2] if grades is not None else GRADE
        got = None
        if np.isfinite(z).all():
            for i in range(int(round(APPROACH_FROM_M / STEP_M)), len(d) - w):
                seg = z[i:i + w + 1]
                if seg.max() - seg.min() <= grade * APPROACH_FLAT_M + APPROACH_TOL_M:
                    level = float(np.median(seg))
                    if APPROACH_MIN_M <= level - float(z[0]) <= APPROACH_RAISE_MAX_M:
                        got = (level, float(d[i]))
                    break
        found.append(got)
    return [(found[2 * j], found[2 * j + 1]) for j in range(len(xys))]


def profile(rails, ground, underpasses=(), levels=None):
    """Per part: bed and ground heights. Underpasses reserve clearance above their lower road mouths.
    ground(xy (n, 2)) -> z. The resulting deck heights propagate along connected approach ramps.
    """
    if not rails:
        return []
    allxy = np.concatenate([xy for *_, xy in rails])
    zg_all = np.asarray(ground(allxy), np.float64)
    zgs, k = [], 0
    for *_, xy in rails:
        zgs.append(zg_all[k:k + len(xy)])
        k += len(xy)
    lines = [xy for *_, xy in rails]
    nodes, edges, adj = roads._chain_graph(lines)
    level = {}                                  # node -> (level, reach): the track's level beyond a bridge's end there
    decks = [n for n, (_, _, kind, _) in enumerate(rails) if kind == "bridge"]
    got_all = approach_levels_many([rails[n][3] for n in decks], ground,
                                   [LINE_GRADE.get(rails[n][1], GRADE) for n in decks]) if decks else []
    for n, ends_got in zip(decks, got_all):
        for node, got in zip(edges[n][:2], ends_got):
            if got is not None and got[0] > level.get(node, (-np.inf, 0.0))[0]:
                level[node] = got
    zb = []
    shared = {}                                 # (a double track's beds are smoothed from one profile: railxing.py)
    if railxing.ENABLED:
        gi = [n for n, (_, _, kind, _) in enumerate(rails) if kind == "ground"]
        if len(gi) > 1:
            shared = dict(zip(gi, railxing.corridor_ground([rails[n][3] for n in gi], [zgs[n] for n in gi])))
    for n, ((_, _, kind, _), zg, (a, b, s)) in enumerate(zip(rails, zgs, edges)):
        if kind != "ground":
            zb.append(zg.copy())
            continue
        lifted = shared.get(n, zg)
        for node, dist in ((a, s), (b, s[-1] - s)):     # (the track on at that level up to the bridge, not down the
            if node in level:                           # slope under it: lifted before the bed's smoothing)
                lv, reach = level[node]
                lifted = np.where(dist <= reach + STEP_M, np.maximum(lifted, lv), lifted)
        zb.append(bed_heights(lifted))
    # Read the lower road at its mouths, not on the filled railway bed above it.
    crossings = [(line, half, _underpass_masks(rails, line, half, DECK_HALF_M + STEP_M))
                 for line, half in underpasses]
    crossings = [(line, half, masks) for line, half, masks in crossings if any(m.any() for m in masks)]
    mouths = np.concatenate([ln[[0, -1]] for ln, _hw, _masks in crossings]) if crossings else np.empty((0, 2))
    mouth_z = np.asarray(ground(mouths), np.float64).reshape(-1, 2) if len(mouths) else []
    ends = {}                                   # node -> the ground parts' bed there (a bridge's ends)
    for n, ((_, _, kind, _), (a, b, _)) in enumerate(zip(rails, edges)):
        if kind == "ground":
            ends.setdefault(a, []).append(zb[n][0])
            ends.setdefault(b, []).append(zb[n][-1])
    seeds = {}                                  # node -> the deck's height at it
    for n, ((_, cls, kind, xy), (a, b, _)) in enumerate(zip(rails, edges)):
        if kind != "bridge":
            continue
        g = zgs[n]
        za = float(np.mean(ends[a])) if a in ends else float(g[0])
        zb_ = float(np.mean(ends[b])) if b in ends else float(g[-1])
        za = max(za, level[a][0]) if a in level else za         # (the track's level beyond its ends)
        zb_ = max(zb_, level[b][0]) if b in level else zb_
        zb[n] = deck_heights(_arc(xy), g, za, zb_)
        for (line, _half, masks), (z0, z1) in zip(crossings, mouth_z):
            over = masks[n]
            if over.any():
                _distance, t = _underpass_distance(xy, line)
                req = zb[n].copy()
                req[over] = np.maximum(req[over], z0 + (z1 - z0) * t[over]
                                       + UNDERPASS_CLEAR_M + DECK_D - BALLAST_H)
                zb[n] = _envelope(_arc(xy), req)
        for node, h in ((a, zb[n][0]), (b, zb[n][-1])):
            seeds[node] = max(seeds.get(node, -np.inf), float(h))
    if seeds:                                   # the decks' heights out along the track, GRADE per metre down
        top = max(seeds.values())
        d = roads._dijkstra(adj, {n: (top - h) / GRADE for n, h in seeds.items()})
        for n, (a, b, s) in enumerate(edges):
            reach = np.maximum(top - GRADE * (d[a] + s), top - GRADE * (d[b] + s[-1] - s))
            if rails[n][2] == "bridge":         # (two decks end to end: still a straight run between their ends)
                zb[n] = _envelope(s, zb[n])
    _viaduct_levels(rails, edges, zb, zgs, levels)
    return list(zip(zb, zgs))


def _viaduct_levels(rails, edges, zb, zgs, levels=None):
    """Raise an all-bridge network (a viaduct / a station) to one level, its median ground plus VIADUCT_CLEAR_M:
    at London Bridge each parallel track otherwise sat at the DEM under it (street level), so the decks, edge walls
    and piers cut through each other. A long bridge-tagged run at OSM layer 1 or above is a viaduct even when a
    ground track anchors its component (Abbey Street's runs sat on the street, 2026-11-07); a long bridge with no
    level joined to ground keeps its own profile (a river/valley deck). The parts raised are levelled again by
    _equalize_viaducts (parallel tracks are often separate networks). In place."""
    levels = levels or {}
    if not edges:
        return
    nn = max(max(a, b) for a, b, _s in edges) + 1
    parent = list(range(nn))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for a, b, _s in edges:
        parent[find(a)] = find(b)
    comp = {}
    for n, (a, _b, _s) in enumerate(edges):
        comp.setdefault(find(a), []).append(n)
    raised = []
    for members in comp.values():
        # (a long bridge-tagged run is a viaduct even when it is one isolated OSM way, or a ground track anchors the
        # component at a station: Abbey Street's 1 km runs were each at the street's height, 2026-11-07)
        bridges = [n for n in members if rails[n][2] == "bridge"]
        if sum(float(edges[n][2][-1]) for n in bridges) < VIADUCT_MIN_LINE_M:
            continue
        if len(members) == 1 and float(levels.get(rails[bridges[0]][0], 0.0)) < 1.0:
            continue                    # (one long bridge, no layer: a valley/river span, not necessarily a viaduct)
        grounded = len(bridges) < len(members)
        if grounded and not any(float(levels.get(rails[n][0], 0.0)) >= 1.0 for n in bridges):
            continue                    # (a long bridge with no layer over a valley/river keeps its own profile)
        for n in bridges:
            if grounded and float(levels.get(rails[n][0], 0.0)) < 1.0:
                continue
            ground = np.asarray(zgs[n], np.float64)
            ground = ground[np.isfinite(ground)]
            if not len(ground):
                continue
            zb[n] = np.maximum(zb[n], float(np.median(ground)) + VIADUCT_CLEAR_M)
            raised.append(n)
    # a short bridge-tagged run at layer 1 is still above the ground (Abbey Street's 53-84 m station spans sat at
    # 0 m clearance; a valley span with no layer keeps its own deck)
    for n, (fid, _cls, kind, _xy) in enumerate(rails):
        if kind != "bridge" or float(levels.get(fid, 0.0)) < 1.0:
            continue
        if float(edges[n][2][-1]) >= VIADUCT_MIN_LINE_M or n in raised:
            continue
        ground = np.asarray(zgs[n], np.float64)
        ground = ground[np.isfinite(ground)]
        if len(ground):
            zb[n] = np.maximum(zb[n], float(np.median(ground)) + VIADUCT_CLEAR_M)
            raised.append(n)
    # Light rail's bridge parts are a viaduct even where a ground track shares their node (the DLR at Limehouse is
    # all bridge=viaduct beside c2c on the ground; the whole network sat at street level with its stations, 2026-11-07)
    light = {}
    for n, (a, _b, _s) in enumerate(edges):
        if rails[n][2] == "bridge" and rails[n][1] == "light_rail":
            light.setdefault(find(a), []).append(n)
    for members in light.values():
        if sum(float(edges[n][2][-1]) for n in members) < VIADUCT_MIN_LINE_M:
            continue
        for n in members:
            if n in raised:
                continue
            ground = np.asarray(zgs[n], np.float64)
            ground = ground[np.isfinite(ground)]
            if not len(ground):
                continue
            zb[n] = np.maximum(zb[n], float(np.median(ground)) + VIADUCT_CLEAR_M)
            raised.append(n)
    _equalize_viaducts(rails, zb, raised)


VIADUCT_MERGE_DZ_M = 3.0    # m: viaduct parts this close in height are the same structure; higher is another level


def _equalize_viaducts(rails, zb, members):
    """Put parallel viaduct tracks at one local level: a station's tracks are often separate OSM networks, raised
    to their own median ground (up to a metre apart at London Bridge). Each 8 m cell's median bed height is taken
    from the viaduct parts in it, and every sample moves to the median of its 3 x 3 cells' medians (within
    VIADUCT_MERGE_DZ_M), twice, then smoothed over 10 m. In place; a cell's median keeps the cost linear in samples
    (a per-sample neighbour search over the station's 36k points took 100 s)."""
    if len(members) < 2:
        return
    for _pass in range(3):
        cells = {}
        for n in members:
            for x, y, z in zip(*[np.asarray(a, np.float64) for a in
                                 (rails[n][3][:, 0], rails[n][3][:, 1], zb[n])]):
                cells.setdefault((int(math.floor(x / 8.0)), int(math.floor(y / 8.0))), []).append(float(z))
        med = {k: float(np.median(v)) for k, v in cells.items()}
        new = {}
        for n in members:
            xy = np.asarray(rails[n][3], np.float64)[:, :2]
            z = np.asarray(zb[n], np.float64)
            out = z.copy()
            for k, (x, y) in enumerate(xy):
                cx, cy = int(math.floor(x / 8.0)), int(math.floor(y / 8.0))
                vals = []
                for gx in range(cx - 1, cx + 2):
                    for gy in range(cy - 1, cy + 2):
                        v = med.get((gx, gy))
                        if v is not None and abs(v - z[k]) <= VIADUCT_MERGE_DZ_M:
                            vals.append(v)
                if vals:
                    out[k] = float(np.median(vals))
            new[n] = out
        for n, v in new.items():
            zb[n] = v
    for n in members:
        zb[n] = _avg(np.asarray(zb[n], np.float64), 3)


def _fit_floor(values, floor):
    """values (the tunnel's own samples) resampled to floor's length (roads.tunnel_sections returns the lower convex
    hull, often fewer samples: a wet tunnel under the Thames' docks read 3001 input samples and a 700-sample floor)."""
    values = np.asarray(values, np.float64)
    if len(values) == len(floor):
        return values
    if len(floor) == 0:
        return np.zeros(0, np.float64)
    return np.interp(np.linspace(0.0, 1.0, len(floor)), np.linspace(0.0, 1.0, len(values)), values)


TUNNEL_WET_MIN_M = 10.0     # m: the least a bed is laid under the submerged ground where water covers the line (the road
                            # code's TUNNEL_WET_DEPTH_M: vault 2.9 m under the bed, clear of the water)


def _wet_envelope(s, wet_mask, grade=0.04):
    """0..1 per point of arcs s: 1 on a point under water, falling to 0 at the dive's reach (TUNNEL_WET_MIN_M over `grade`)
    either side of the nearest one, so a bed can run from the bank down under the water at a train's grade. Pure."""
    s = np.asarray(s, np.float64)
    sw = s[np.asarray(wet_mask, bool)]
    if not len(sw):
        return np.zeros(len(s))
    k = np.clip(np.searchsorted(sw, s), 1, len(sw) - 1) if len(sw) > 1 else np.zeros(len(s), np.int64)
    d = np.abs(s - sw[k]) if len(sw) == 1 else np.minimum(np.abs(s - sw[k]), np.abs(s - sw[k - 1]))
    return np.clip(1.0 - d / (TUNNEL_WET_MIN_M / grade), 0.0, 1.0)


def _tunnel_floor(xy, g, wet_mask=None, depth=0.0, ramp=None):
    """A rail tunnel's bed line along xy (k, 2): the lower convex hull of the ground g over it (roads.tunnel_sections,
    the line under a hill with its ends at the portals), laid TUNNEL_DEPTH_M under the ground where water covers it
    (the Channel; faded in over TUNNEL_WET_RAMP_M at the coasts), or `depth` under it along the whole line (a subway,
    faded in over `ramp`). Pure."""
    s = _arc(np.asarray(xy, np.float64))
    floor, _kinds = roads.tunnel_sections(s, np.asarray(g, np.float64))
    base = np.asarray(floor, np.float64)
    if depth:
        r = TUNNEL_WET_RAMP_M if ramp is None else float(ramp)
        share = np.minimum(np.clip(s / r, 0.0, 1.0), np.clip((s[-1] - s) / r, 0.0, 1.0))
        floor = floor - float(depth) * _fit_floor(share, floor)
    if wet_mask is not None and wet_mask.any():
        r = max(1, int(round(TUNNEL_WET_RAMP_M / STEP_M)))
        k = np.ones(2 * r + 1) / (2 * r + 1)
        share = np.convolve(wet_mask.astype(np.float64), k, mode="same")
        floor = floor - TUNNEL_DEPTH_M * _fit_floor(share, floor)
        # a short river or canal crossing: the Channel-sized ramp above barely lowers it, leaving the bore in the bed
        # with the water over it (flooded tunnels, 2026-11-09): wherever water covers the line the bed is at least
        # TUNNEL_WET_MIN_M under the (submerged) ground, reached from the banks at a train's grade
        floor = np.minimum(floor, base - TUNNEL_WET_MIN_M * _fit_floor(_wet_envelope(s, wet_mask), base))
    return np.asarray(floor, np.float64)


def tunnel_floors(rails, frame, ground, wet=None, levels=None):
    """{index in rails: bed z (k,)} of the tunnel parts among rails (parts' (fid, class, kind, xy)); wet(lat, lon) ->
    water depth (m) where a sea or lake covers the line (the Channel: the bed under the seabed); a subway is
    SUBWAY_DEPTH_M under the ground, any other rail tunnel RAIL_TUNNEL_DEPTH_M (more for a deep OSM level; a flat
    city's lower hull is the street itself, so tunnels were laid at street level before, 2026-11-07). Pure given
    ground."""
    levels = levels or {}
    out = {}
    for n, (fid, cls, kind, xy) in enumerate(rails):
        if kind != "tunnel" or len(xy) < 2:
            continue
        g = np.asarray(ground(np.asarray(xy, np.float64)), np.float64)
        wet_mask = None
        if wet is not None:
            la, lo, _ = frame.enu_to_geodetic(xy[:, 0], xy[:, 1], 0.0)
            wet_mask = np.asarray(wet(la, lo), np.float64) > 0.5
        if cls == "subway":
            depth, ramp = SUBWAY_DEPTH_M, SUBWAY_RAMP_M
        else:
            lv = abs(float(levels.get(fid, 0.0)))
            depth = RAIL_TUNNEL_DEPTH_M if lv <= 0.0 else max(RAIL_TUNNEL_MIN_M,
                                                              min(RAIL_TUNNEL_MAX_M, RAIL_TUNNEL_LEVEL_M * lv))
            ramp = RAIL_TUNNEL_RAMP_M
        z = _tunnel_floor(xy, g, wet_mask, depth, ramp=ramp)
        if np.isfinite(z).all():            # (a missing DEM: no tunnel track there)
            out[n] = z
    _separate_crossing_tunnels(rails, out)
    return out


TUNNEL_STAGGER_M = 9.5      # m one bore drops under another where they cross (two at the same depth clipped through
TUNNEL_STAGGER_RAMP_M = 55.0    # m over which that drop fades in and out
TUNNEL_CROSS_PARALLEL = 0.85    # |cos| of their directions above which two tracks run beside, not across, each other
TUNNEL_CLEAR_M = 1.5        # m of rock kept between the lower bore's vault and the upper bore's floor at a crossing
TUNNEL_STAGGER_GRADE = 0.04     # the steepest the lower bore dives to pass under another (4 %: the Underground's worst)
TUNNEL_END_TAPER_M = 60.0       # m from a tunnel part's end over which a dive fades out (a portal stays at the ground)


def _min_pair(xa, xb, max_pts=70):
    """(m, index in xa, index in xb) of the closest sampled points of two polylines (at most max_pts each)."""
    ia = np.unique(np.linspace(0, len(xa) - 1, min(len(xa), max_pts)).astype(np.int64))
    ib = np.unique(np.linspace(0, len(xb) - 1, min(len(xb), max_pts)).astype(np.int64))
    a0, a1 = xa[ia][:-1, :2], xa[ia][1:, :2]
    d = a1 - a0
    L2 = np.maximum((d * d).sum(1), 1e-12)
    B = xb[ib, :2]
    rel = B[:, None, :] - a0[None, :, :]
    t = np.clip((rel * d[None]).sum(2) / L2[None], 0.0, 1.0)
    q = a0[None] + d[None] * t[:, :, None]
    dd = np.hypot(B[:, None, 0] - q[:, :, 0], B[:, None, 1] - q[:, :, 1])
    jj, kk = np.unravel_index(int(np.argmin(dd)), dd.shape)
    return float(dd[jj, kk]), int(ia[kk + 1]), int(ib[jj])


def _crossing_arcs(xa, xb, pad=1.0):
    """[(arc on a, arc on b)] of every proper crossing of two polylines (k, 2+), crossings under 10 m apart along a
    counted once. Only the segments inside the two lines' shared box are tested, in chunks (a km-long line against
    another is ~500 x 500 segments). The old nearest-sample test missed a crossing between two samples of a long way
    (its 70 samples were ~40 m apart on a 3 km line) and saw only one crossing of a pair. Pure."""
    a = np.asarray(xa, np.float64)[:, :2]
    b = np.asarray(xb, np.float64)[:, :2]
    lo = np.maximum(a.min(0), b.min(0)) - pad
    hi = np.minimum(a.max(0), b.max(0)) + pad
    if (lo > hi).any():
        return []

    def inside(p):
        s0, s1 = np.minimum(p[:-1], p[1:]), np.maximum(p[:-1], p[1:])
        return np.flatnonzero(((s1 >= lo) & (s0 <= hi)).all(1))
    ia, ib = inside(a), inside(b)
    if not len(ia) or not len(ib):
        return []
    sa, sb = _arc(a), _arc(b)
    a0, ra = a[ia], a[ia + 1] - a[ia]
    b0, rb = b[ib], b[ib + 1] - b[ib]
    out = []
    for c in range(0, len(ia), 256):
        a0c, rac = a0[c:c + 256], ra[c:c + 256]
        den = rac[:, None, 0] * rb[None, :, 1] - rac[:, None, 1] * rb[None, :, 0]
        qx = b0[None, :, 0] - a0c[:, None, 0]
        qy = b0[None, :, 1] - a0c[:, None, 1]
        with np.errstate(divide="ignore", invalid="ignore"):
            u = (qx * rb[None, :, 1] - qy * rb[None, :, 0]) / den
            v = (qx * rac[:, None, 1] - qy * rac[:, None, 0]) / den
        hit = (np.abs(den) > 1e-12) & (u >= 0.0) & (u <= 1.0) & (v >= 0.0) & (v <= 1.0)
        for i, j in zip(*np.nonzero(hit)):
            k, m = int(ia[c + i]), int(ib[j])
            out.append((float(sa[k] + u[i, j] * (sa[k + 1] - sa[k])), float(sb[m] + v[i, j] * (sb[m + 1] - sb[m]))))
    out.sort()
    merged = []
    for h in out:
        if not merged or h[0] - merged[-1][0] >= 10.0:
            merged.append(h)
    return merged


def _dive(s, z, sc, drop):
    """z (the bed at arcs s) lowered by up to drop round arc sc: a smooth cosine-squared window as wide as the dive's
    grade allows (TUNNEL_STAGGER_GRADE; never under TUNNEL_STAGGER_RAMP_M), faded out over TUNNEL_END_TAPER_M at both
    ends of the part so a portal stays at the ground. The old window was a fixed 55 m each way: a 9.5 m dive over it
    is a 27 % wall, which a train cannot follow. Pure."""
    s = np.asarray(s, np.float64)
    w = max(TUNNEL_STAGGER_RAMP_M, drop * math.pi / (2.0 * TUNNEL_STAGGER_GRADE))
    bump = np.cos(np.clip((s - sc) / w * (math.pi / 2.0), -math.pi / 2.0, math.pi / 2.0)) ** 2
    e = np.minimum(np.clip(s / TUNNEL_END_TAPER_M, 0.0, 1.0), np.clip((s[-1] - s) / TUNNEL_END_TAPER_M, 0.0, 1.0))
    return np.asarray(z, np.float64) - drop * bump * (e * e * (3.0 - 2.0 * e))


def _separate_crossing_tunnels(rails, floors):
    """Two tunnel beds at one depth crossed and their tubes clipped through each other (the user's London lines,
    2026-11-08). At every crossing of two tunnel parts (each crossing of a pair, found by segment intersection:
    _crossing_arcs) the lower bed is dived under the other until the upper bore's floor clears its vault by
    TUNNEL_CLEAR_M (roadmesh.TUNNEL_H + that, less the gap already there), over a window its grade allows (_dive: a
    9 m dive is ~370 m long, not a 55 m wall); parallel tracks (a shared bore) are left alone. A pair with no
    crossing but a close approach (a skewed end) is treated at its closest point, as before. Pairs go in the parts'
    own sorted order, so adjacent tiles agree."""
    keys = [n for n in floors if len(rails[n][3]) == len(floors[n])]
    if len(keys) < 2:
        return
    xy, arc, tan = {}, {}, {}
    for n in keys:
        xy[n] = np.asarray(rails[n][3], np.float64)
        arc[n] = _arc(xy[n])
        t = np.gradient(xy[n][:, :2], axis=0)
        tan[n] = t / np.maximum(np.hypot(t[:, 0], t[:, 1]), 1e-9)[:, None]
    need = roadmesh.TUNNEL_H + TUNNEL_CLEAR_M
    near = TUNNEL_BORE_W + 2.0 * 1.4 + 1.0
    for ai in range(len(keys)):
        na = keys[ai]
        xa = xy[na]
        for bi in range(ai + 1, len(keys)):
            nb = keys[bi]
            xb = xy[nb]
            if (xa[:, 0].max() < xb[:, 0].min() - 9.0 or xb[:, 0].max() < xa[:, 0].min() - 9.0
                    or xa[:, 1].max() < xb[:, 1].min() - 9.0 or xb[:, 1].max() < xa[:, 1].min() - 9.0):
                continue
            hits = _crossing_arcs(xa, xb)
            crossed = bool(hits)
            if not hits:
                d, ia, ib = _min_pair(xa, xb)
                if d > near:
                    continue
                hits = [(float(arc[na][min(ia, len(xa) - 1)]), float(arc[nb][min(ib, len(xb) - 1)]))]
            for sca, scb in hits:
                ka = int(np.clip(np.searchsorted(arc[na], sca), 0, len(xa) - 1))
                kb = int(np.clip(np.searchsorted(arc[nb], scb), 0, len(xb) - 1))
                if not crossed and abs(float(np.dot(tan[na][ka], tan[nb][kb]))) > TUNNEL_CROSS_PARALLEL:
                    continue                # a near approach beside each other, not a crossing
                za = float(np.interp(sca, arc[na], floors[na]))     # (an earlier crossing may have lowered them)
                zb = float(np.interp(scb, arc[nb], floors[nb]))
                gap = abs(za - zb)
                if gap >= need:
                    continue                # already one above the other
                if gap < 2.0:               # (near one depth: the bore with more run either side takes the dive, so a
                    dive_b = min(scb, float(arc[nb][-1]) - scb) >= min(sca, float(arc[na][-1]) - sca)   # short one
                else:                       # is not squeezed into a steep wall)
                    dive_b = zb <= za
                if dive_b:
                    floors[nb] = _dive(arc[nb], floors[nb], scb, need - gap)
                else:
                    floors[na] = _dive(arc[na], floors[na], sca, need - gap)


def _tunnel_portals(rails):
    """{index in rails: (its start is a mouth, its end is one)} of the tunnel parts: an end another tunnel part goes
    on from (within 0.5 m) is a joint inside the tunnel, not a portal (no face, no terrain hole there)."""
    ends = {}
    for _fid, _c, kind, xy in rails:
        if kind != "tunnel" or len(xy) < 2:
            continue
        for p in (xy[0], xy[-1]):
            k = (round(float(p[0]), 1), round(float(p[1]), 1))
            ends[k] = ends.get(k, 0) + 1
    out = {}
    for n, (_fid, _c, kind, xy) in enumerate(rails):
        if kind != "tunnel" or len(xy) < 2:
            continue
        out[n] = tuple(ends[(round(float(p[0]), 1), round(float(p[1]), 1))] < 2 for p in (xy[0], xy[-1]))
    return out


MIN_BORE_W = 4.4            # m: the narrowest a bore is squeezed to beside a neighbour (the ballast's 3.6 m and a little)
BORE_PARALLEL_DOT = 0.98    # directions within ~11 deg: beside each other (a shared bore), not crossing


def _bore_widths(rails, floors):
    """{part: bore width (m)} of the tunnel parts that run beside another at a depth their tubes share, narrowed so the
    two tubes (each its bore plus a roadmesh.TUNNEL_SHOULDER either side) just touch instead of passing through each
    other: a station throat's parallel tracks stood as overlapping tubes (the user, 2026-11-09). Beside a neighbour for
    at least ~20 m (a crossing is not: _separate_crossing_tunnels). Parts with no such neighbour are absent (they keep
    TUNNEL_BORE_W). Where tracks are closer than MIN_BORE_W allows the tubes still overlap. Pure."""
    keys = [n for n in floors if len(rails[n][3]) == len(floors[n])]
    out = {}
    if len(keys) < 2:
        return out
    sh = roadmesh.TUNNEL_SHOULDER
    reach = TUNNEL_BORE_W + 2.0 * sh
    xy = {n: np.asarray(rails[n][3], np.float64)[:, :2] for n in keys}
    arc = {n: _arc(xy[n]) for n in keys}
    dirs = {}
    for n in keys:
        d = xy[n][-1] - xy[n][0]
        dirs[n] = d / max(float(np.hypot(*d)), 1e-9)
    for n in keys:
        xa, sa = xy[n], arc[n]
        if len(xa) < 2 or sa[-1] < 20.0:
            continue
        ss = np.arange(0.0, sa[-1] + 1e-6, 10.0)
        P = np.c_[np.interp(ss, sa, xa[:, 0]), np.interp(ss, sa, xa[:, 1])]
        zmed = float(np.median(floors[n]))
        best = None
        for m in keys:
            xb = xy[m]
            if m == n or len(xb) < 2:
                continue
            if abs(zmed - float(np.median(floors[m]))) >= roadmesh.TUNNEL_H:
                continue                    # another level: a bore above or below, not beside
            if abs(float(np.dot(dirs[n], dirs[m]))) < BORE_PARALLEL_DOT:
                continue                    # crossing it, not running beside it
            if (xa[:, 0].max() < xb[:, 0].min() - reach or xb[:, 0].max() < xa[:, 0].min() - reach
                    or xa[:, 1].max() < xb[:, 1].min() - reach or xb[:, 1].max() < xa[:, 1].min() - reach):
                continue
            d, _q = roads._line_near(P, xb)
            near = d < reach
            if near.mean() >= 0.5:          # beside it for half the run at least; a crossing is only near its point
                dm = float(np.median(d[near]))
                best = dm if best is None else min(best, dm)
        if best is not None:
            w = float(np.clip(best - 2.0 * sh - 0.1, MIN_BORE_W, TUNNEL_BORE_W))
            if w < TUNNEL_BORE_W - 0.05:
                out[n] = round(w, 2)
    return out


TUNNEL_TWIN_M = 7.5         # m between parallel tunnel centre lines: one tube round both (NRails' TUNNEL_TWIN_M)


def _twin_tubes(rails, floors):
    """{part: {"skip": (k,) bool, "center": (k, 2), "width": (k,)}} of parallel tunnel parts at one depth within
    TUNNEL_TWIN_M: the first part's tube moves to the midpoint and widens to cover both, and the second's samples
    there are skipped (its own tube would pass through the wider one). One partner per leader, deterministic by the
    parts' order. Pure."""
    keys = [n for n in floors if len(rails[n][3]) == len(floors[n])]
    out = {}
    if len(keys) < 2:
        return out
    xy = {n: np.asarray(rails[n][3], np.float64)[:, :2] for n in keys}
    zmed = {n: float(np.median(floors[n])) for n in keys}
    absorbed = set()
    for ai, na in enumerate(keys):
        if na in absorbed:
            continue
        xa = xy[na]
        if len(xa) < 3:
            continue
        best = None
        for nb in keys[ai + 1:]:
            if nb in absorbed or len(xy[nb]) < 2:
                continue
            xb = xy[nb]
            if (xa[:, 0].max() < xb[:, 0].min() - TUNNEL_TWIN_M or xb[:, 0].max() < xa[:, 0].min() - TUNNEL_TWIN_M
                    or xa[:, 1].max() < xb[:, 1].min() - TUNNEL_TWIN_M or xb[:, 1].max() < xa[:, 1].min() - TUNNEL_TWIN_M):
                continue
            if abs(zmed[na] - zmed[nb]) >= roadmesh.TUNNEL_H:
                continue                    # another level: not a twin bore
            d, q = roads._line_near(xa, xb)
            twin = (d > 0.5) & (d <= TUNNEL_TWIN_M)
            if int(twin.sum()) < max(4, int(0.25 * len(xa))):
                continue                    # beside it only briefly (a crossing / a throat merge): not a twin bore
            dm = float(np.median(d[twin]))
            if best is None or dm < best[0]:
                best = (dm, nb, d, q, twin)
        if best is None:
            continue
        _dm, nb, d, q, twin = best
        xb = xy[nb]
        db, _qb = roads._line_near(xb, xa)
        center = xa.copy()
        width = np.full(len(xa), TUNNEL_BORE_W)
        center[twin] = (xa[twin] + q[twin]) / 2.0
        width[twin] = TUNNEL_BORE_W + d[twin]
        out[na] = {"skip": np.zeros(len(xa), bool), "center": center, "width": width}
        out[nb] = {"skip": db <= TUNNEL_TWIN_M, "center": xb.copy(),
                   "width": np.full(len(xb), TUNNEL_BORE_W)}
        absorbed.add(nb)
    return out


def block_tunnel_holes(frame, cells, keep, cx, cy, square, ground, wet=None):
    """Where a terrain block's rail tunnel mouths are holes (roads.tunnel_hole_mask, one part at a time so two parallel
    bores are not merged into a twin tube): the samples over a bore whose ground lies between its floor and vault.
    keep: heightmap.generate's keep (its u); ground(xy (n, 2)) -> z in frame; wet(lat, lon) as tunnel_floors. None when
    no rail tunnel is near. Pure given ground."""
    import types
    u = keep["u"]
    half = u.shape[0] * square / 2
    box = (cx - half, cy - half, cx + half, cy + half)
    levels = {}
    rails = parts(frame, list(cells), box, clip=False, levels=levels)
    parts_ = [(n, fid, np.asarray(xy, np.float64)) for n, (fid, _c, kind, xy) in enumerate(rails)
              if kind == "tunnel" and len(xy) >= 2]
    if not parts_:
        return None
    floors = tunnel_floors(rails, frame, ground, wet, levels)
    ports = _tunnel_portals(rails)
    bw = _bore_widths(rails, floors)
    mask = np.zeros(u.shape, bool)
    for n, fid, xy in parts_:
        z = floors.get(n)
        if z is None:
            continue
        mask |= roads.tunnel_hole_mask([(fid, types.SimpleNamespace(width=bw.get(n, TUNNEL_BORE_W)), xy, z, None)], u, cx, cy, square,
                                       heights={fid: roadmesh.TUNNEL_H},
                                       mouths={fid: ports.get(n, (True, True))})
    return mask if mask.any() else None


RAIL_TOP_M = BALLAST_H - SLEEPER_SINK + SLEEPER_H + RAIL_H    # the rails' top over the bed
FLUSH_LIFT_M = 0.03         # m: the road's surface over the drawn ground (grading.ROAD_LIFT, a little under)
FLUSH_GRADE = 1.0 / 60.0    # the track's ramp to and from a level crossing (a main line's 1.7 %) ...
FLUSH_REACH_M = 40.0        # ... steeper when it would run longer than this (inside the 50 m the tiles look for roads)


def _flush(s, zb, blk, zroad):
    """Bed heights zb (at arcs s) lowered or raised so the rails' top meets the road at a level crossing (blk: the
    points in a road's carriageway, zroad: the drawn ground there), on straight ramps (FLUSH_GRADE, at most
    FLUSH_REACH_M long) either side: a real crossing's rails lie in the road's surface (the user, 2026-10-27: "make
    road/rail crossings smooth like real ones"); they stood 0.6 m over the road at its edge. The same in every tile:
    every crossing within FLUSH_REACH_M of a tile's points is among its crossers."""
    sb = s[blk]
    target = zroad + FLUSH_LIFT_M - RAIL_TOP_M
    delta = target - zb[blk]
    g = np.maximum(FLUSH_GRADE, np.abs(delta) / FLUSH_REACH_M)
    d = np.abs(s[:, None] - sb[None, :]) * g[None, :]
    low = np.min(np.where(delta[None, :] < 0, target[None, :] + d, np.inf), axis=1)
    high = np.max(np.where(delta[None, :] > 0, target[None, :] - d, -np.inf), axis=1)
    return np.minimum(np.maximum(zb, high), low)


def _blocked(xy, z, crossers):
    """Per point of xy (bed heights z): inside a crossing road's carriageway (+ CROSSING_PAD_M) at grade: its nearest
    segment's start within CROSSING_DZ of the bed (earthfast.near_crossers)."""
    import fast
    xy = np.asarray(xy, np.float64).reshape(-1, 2)
    pack = _crosser_pack(crossers)
    if pack is None or not len(xy):
        return np.zeros(len(xy), bool)
    fast.require(74, "near_crossers")
    return np.asarray(fast._ef.near_crossers(np.ascontiguousarray(xy), np.ascontiguousarray(np.asarray(z, np.float64)),
                                             *pack, float(CROSSING_DZ)), bool)


_CROSSER_PACK = [(None, None)]  # [(the crossers list, its packed arrays)]: a tile's parts ask with the same list


def _crosser_pack(crossers):
    """(points (n, 3) [x, y, road z], starts (C + 1,), reach (C,)) of the crossing roads with 2 points or more."""
    if not crossers:
        return None
    last = _CROSSER_PACK[0]
    if last[0] is crossers:
        return last[1]
    keep = [(np.asarray(line, np.float64)[:, :2], float(hw), np.asarray(lz, np.float64))
            for line, hw, lz in crossers if len(line) >= 2]
    pack = None
    if keep:
        pts = np.ascontiguousarray(np.concatenate([np.c_[ln, lz[:len(ln)]] for ln, _hw, lz in keep]))
        starts = np.r_[0, np.cumsum([len(ln) for ln, _hw, _lz in keep])].astype(np.int64)
        reach = np.array([hw + CROSSING_PAD_M for _ln, hw, _lz in keep], np.float64)
        pack = (pts, starts, reach)
    _CROSSER_PACK[0] = (crossers, pack)
    return pack


def _runs(mask):
    """(i, j) inclusive ranges where mask is False, at least 2 long."""
    out, i, n = [], 0, len(mask)
    while i < n:
        if mask[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and not mask[j + 1]:
            j += 1
        if j > i:
            out.append((i, j))
        i = j + 1
    return out


class _Mesh:
    def __init__(self, origin):
        self.origin = np.asarray(origin, np.float64)
        self.P, self.T, self.UV, self.M = [], [], [], []
        self.mats = []
        self.nv = 0

    def mat(self, name):
        if name not in self.mats:
            self.mats.append(name)
        return self.mats.index(name)

    def strip(self, a, b, ua, ub, v, mi, up):
        """Quads between point rows a and b (k, 3), faces toward up ((3,) or (k, 3))."""
        k = len(a)
        P = np.concatenate([a, b]) - self.origin
        self.P.append(P)
        self.UV.append(np.c_[np.r_[np.full(k, ua), np.full(k, ub)], np.r_[v, v]])
        i = np.arange(k - 1)
        A, A1, B, B1 = i, i + 1, k + i, k + i + 1
        tri = np.concatenate([np.stack([A, B, B1], 1), np.stack([A, B1, A1], 1)])
        nrm = np.cross(P[tri[:, 1]] - P[tri[:, 0]], P[tri[:, 2]] - P[tri[:, 0]])
        want = np.concatenate([up[:-1], up[:-1]]) if np.ndim(up) == 2 else np.broadcast_to(up, nrm.shape)
        flip = (nrm * want).sum(1) < 0
        tri[flip] = tri[flip][:, ::-1]
        self.T.append(tri + self.nv)
        self.M.append(np.full(len(tri), mi))
        self.nv += 2 * k

    def boxes(self, c, ax, ay, hx, hy, z0, z1, mi):
        """Boxes at centres c (n, 2), axes ax / ay (n, 2) unit, half sizes hx / hy, from z0 to z1 (n,): top and four
        sides (no bottom), all at once."""
        n = len(c)
        if n == 0:
            return
        corners = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
        xy = np.stack([c + sx * hx * ax + sy * hy * ay for sx, sy in corners], 1)       # (n, 4, 2)
        lo = np.concatenate([xy, np.repeat(z0[:, None, None], 4, 1)], 2)
        hi = np.concatenate([xy, np.repeat(z1[:, None, None], 4, 1)], 2)
        P = np.concatenate([hi, lo], 1).reshape(-1, 3) - self.origin                  # 8 per box: top 0-3, foot 4-7
        base = (np.arange(n) * 8)[:, None]
        faces = [(0, 1, 2, 3)] + [(q, (q + 1) % 4, (q + 1) % 4 + 4, q + 4) for q in range(4)]
        tri = []
        for f in faces:
            tri.append(np.c_[base + f[0], base + f[1], base + f[2]])
            tri.append(np.c_[base + f[0], base + f[2], base + f[3]])
        tri = np.concatenate(tri)
        ctr = np.concatenate([np.c_[c, (z0 + z1) / 2] - self.origin] * 10)
        nrm = np.cross(P[tri[:, 1]] - P[tri[:, 0]], P[tri[:, 2]] - P[tri[:, 0]])
        flip = (nrm * (P[tri].mean(1) - ctr)).sum(1) < 0
        tri[flip] = tri[flip][:, ::-1]
        self.P.append(P)
        self.UV.append(np.tile(np.array([[0, 0], [1, 0], [1, 1], [0, 1]] * 2, np.float64) * 0.25, (n, 1)))
        self.T.append(tri + self.nv)
        self.M.append(np.full(len(tri), mi))
        self.nv += 8 * n

    def result(self):
        if not self.P:
            return None
        return (np.concatenate(self.P), np.concatenate(self.T), np.concatenate(self.UV), self.mats,
                np.concatenate(self.M))


def _sleepers(s, xy, u, top, i, j, gone=0.0):
    """The sleepers of a run (i..j of a part): (centres, along, across, z0, z1), every SLEEPER_EVERY from the part's
    start (the same in the tile beside); gone: the share of them left out (by a hash of their number: the same ones in
    every tile)."""
    ss = np.arange(np.ceil(s[i] / SLEEPER_EVERY) * SLEEPER_EVERY, s[j], SLEEPER_EVERY)
    if gone > 0 and len(ss):
        k = np.rint(ss / SLEEPER_EVERY).astype(np.uint64)
        h = ((k * np.uint64(2654435761)) % np.uint64(1 << 32)).astype(np.float64) / float(1 << 32)
        ss = ss[h >= gone]
    if not len(ss):
        return None
    cx, cy = np.interp(ss, s, xy[:, 0]), np.interp(ss, s, xy[:, 1])
    ux, uy = np.interp(ss, s, u[:, 0]), np.interp(ss, s, u[:, 1])
    un = np.c_[ux, uy] / np.maximum(np.hypot(ux, uy), 1e-9)[:, None]
    zt = np.interp(ss, s, top) - SLEEPER_SINK
    return np.c_[cx, cy], np.c_[un[:, 1], -un[:, 0]], un, zt, zt + SLEEPER_H


PIER_ROAD_M = 1.5           # m a pier keeps off a road's carriageway under the bridge


def _sited(pr, crossers, ground):
    """The piers pr (_piers') clear of the roads under the deck (crossers: [(centreline, half width, z)]; a pier stood
    in the lanes of a road under a rail bridge, 2026-10-25), each reaching PIER_FOOT_M under the lowest ground at its
    four corners (from the ground at the line's centre one hung in the air where the ground fell away to the road)."""
    if pr is None:
        return None
    c, along, across, z0, z1 = pr
    keep = np.ones(len(c), bool)
    for line, hw, _z in crossers:
        if len(line) < 2:
            continue
        a, b = line[:-1], line[1:]
        d = b - a
        L2 = np.maximum((d * d).sum(1), 1e-12)
        t = np.clip(((c[:, None, :] - a[None]) * d[None]).sum(2) / L2[None], 0.0, 1.0)
        dist = np.hypot(*(c[:, None, :] - (a[None] + t[..., None] * d[None])).transpose(2, 0, 1)).min(1)
        keep &= dist > hw + PIER_ROAD_M + max(PIER_W, PIER_T) / 2
    if not keep.any():
        return None
    c, along, across, z0, z1 = c[keep], along[keep], across[keep], z0[keep], z1[keep]
    corners = np.concatenate([c + sa * along * PIER_T / 2 + sc * across * PIER_W / 2
                              for sa in (-1, 1) for sc in (-1, 1)])
    zg = np.asarray(ground(corners), np.float64).reshape(4, len(c)).min(axis=0)
    return c, along, across, np.minimum(z0, zg - PIER_FOOT_M), z1


def _ballast_bed(mesh, p, uu, t, v, foot_l, foot_r, zl, zr, mat, tile=3.0):
    up = np.array([0.0, 0.0, 1.0])
    v = v * 3.0 / tile                  # (v: arc / 3 m)
    out_l = np.c_[uu, np.zeros(len(p))]
    L = np.c_[p + uu * BALLAST_TOP_M, t]
    R = np.c_[p - uu * BALLAST_TOP_M, t]
    Lf = np.c_[p + uu * foot_l[:, None], np.minimum(zl, t) - BALLAST_FOOT_M]
    Rf = np.c_[p - uu * foot_r[:, None], np.minimum(zr, t) - BALLAST_FOOT_M]
    mesh.strip(L, R, 0.0, 2 * BALLAST_TOP_M / tile, v, mat, up)
    mesh.strip(L, Lf, 0.0, 0.8 * 3.0 / tile, v, mat, out_l + up)
    mesh.strip(R, Rf, 0.0, 0.8 * 3.0 / tile, v, mat, -out_l + up)


def _deck(mesh, p, uu, t, v, ballast, concrete, wl=None, wr=None, wall_l=True, wall_r=True):
    """A bridge's deck: ballast on a concrete trough, its edge walls over the ballast, its underside DECK_D down.
    wl / wr: per-sample half widths (DECK_HALF_M where nothing runs beside it; trimmed to the midpoint towards a
    track beside it), wall_l / wall_r: its edge walls (none on a shared side: an edge wall between two tracks cut
    through the neighbour's ballast and rails)."""
    up = np.array([0.0, 0.0, 1.0])
    out_l = np.c_[uu, np.zeros(len(p))]
    n = len(p)
    wl = np.full(n, DECK_HALF_M) if wl is None else np.clip(np.asarray(wl, np.float64), 0.6, DECK_HALF_M)
    wr = np.full(n, DECK_HALF_M) if wr is None else np.clip(np.asarray(wr, np.float64), 0.6, DECK_HALF_M)
    sl = wl - (DECK_WALL_T if wall_l else 0.0)
    sr = wr - (DECK_WALL_T if wall_r else 0.0)
    mesh.strip(np.c_[p + uu * sl[:, None], t], np.c_[p - uu * sr[:, None], t],
               0.0, 2 * DECK_HALF_M / 3.0, v, ballast, up)
    for sg, w, wall in ((1, wl, wall_l), (-1, wr, wall_r)):
        o = out_l * sg
        if not wall:                       # a sealed side at the shared edge, under the neighbour's deck
            e = p + uu * (sg * w)[:, None]
            mesh.strip(np.c_[e, t], np.c_[e, t - DECK_D], 0.0, 0.6, v / 1.5, concrete, o)
            continue
        e_out, e_in = p + uu * (sg * w)[:, None], p + uu * (sg * (w - DECK_WALL_T))[:, None]
        hi = t + DECK_WALL_H
        mesh.strip(np.c_[e_in, t], np.c_[e_in, hi], 0.0, 0.2, v / 1.5, concrete, -o)
        mesh.strip(np.c_[e_in, hi], np.c_[e_out, hi], 0.0, 0.1, v / 1.5, concrete, up)
        mesh.strip(np.c_[e_out, hi], np.c_[e_out, t - DECK_D], 0.0, 0.6, v / 1.5, concrete, o)
    mesh.strip(np.c_[p + uu * wl[:, None], t - DECK_D], np.c_[p - uu * wr[:, None], t - DECK_D],
               0.0, 1.0, v / 1.5, concrete, -up)


def _tram_deck(mesh, p, uu, t, v, concrete):
    """An embedded tram track's concrete slab, not a mainline ballast trough.
    t is the common ballast-top datum; keep its underside identical to _deck
    so grading and pier placement consume the same structural depth."""
    up = np.array([0.0, 0.0, 1.0])
    out_l = np.c_[uu, np.zeros(len(p))]
    left, right = p + uu * TRAM_DECK_HALF_M, p - uu * TRAM_DECK_HALF_M
    top, bottom = t - BALLAST_H + FLUSH_LIFT_M, t - DECK_D
    mesh.strip(np.c_[left, top], np.c_[right, top], 0.0, 0.9, v, concrete, up)
    for edge, normal in ((left, out_l), (right, -out_l)):
        mesh.strip(np.c_[edge, top], np.c_[edge, bottom], 0.0, 0.5, v, concrete, normal)
    mesh.strip(np.c_[left, bottom], np.c_[right, bottom], 0.0, 0.9, v, concrete, -up)


RAIL_ARCH_LEN_M = 9.0       # m of brick arch tunnel along the road under a rail deck ...
RAIL_ARCH_T = 0.55          # ... its voussoir band's thickness, ...
RAIL_ARCH_FACE_M = 1.35     # ... and the spandrel's extra width beside the opening
RAIL_ARCH_MIN_CLEAR_M = 4.4 # under the deck: lower, no arch portal is drawn (the road would not fit)


def _rail_arch(mesh, c, u, v, z0, crown, half, length, mi):
    """A brick arch portal under a rail deck: two abutment piers and a segmental voussoir band over the road
    (c: the road point (2,), u along the road, v across, z0 the road's surface, crown the deck's underside, half
    the road's half width, length along the road)."""
    if crown - z0 < RAIL_ARCH_MIN_CLEAR_M or half < 1.2:
        return
    half = float(half)
    c = np.asarray(c, np.float64)[:2]
    u = np.asarray(u, np.float64)[:2]
    v = np.asarray(v, np.float64)[:2]
    spring = max(float(z0) + 1.8, float(crown) - half * 0.9)
    rise = max(1.0, float(crown) - spring)
    th = np.linspace(0.0, math.pi, 13)
    inner = np.c_[c[0] + v[0] * half * np.cos(th), c[1] + v[1] * half * np.cos(th),
                  spring + rise * np.sin(th)]
    outer_half = half + RAIL_ARCH_T
    outer = np.c_[c[0] + v[0] * outer_half * np.cos(th), c[1] + v[1] * outer_half * np.cos(th),
                  np.minimum(float(crown) + 0.12, spring + (rise + RAIL_ARCH_T) * np.sin(th))]
    hi = c + u * (length / 2.0)
    lo = c - u * (length / 2.0)
    vv = np.zeros(len(inner))
    for at, up in ((hi, u), (lo, -u)):
        a = inner.copy(); a[:, :2] += at - c
        b = outer.copy(); b[:, :2] += at - c
        mesh.strip(a, b, 0.0, 1.0, vv, mi, np.r_[up, 0.0])
    # the soffit seen from below
    lo_inner = inner.copy(); lo_inner[:, :2] += lo - c
    hi_inner = inner.copy(); hi_inner[:, :2] += hi - c
    mesh.strip(inner, lo_inner, 0.0, length / 2.0, vv, mi, np.array([0.0, 0.0, -1.0]))
    mesh.strip(lo_inner, hi_inner, length / 2.0, length, vv, mi, np.array([0.0, 0.0, -1.0]))
    # the piers at both sides, from the road up to the arch's springing
    pc = np.array([c + v * outer_half, c - v * outer_half], np.float64)[:, :2]
    mesh.boxes(pc, np.array([u, u]), np.array([v, v]), np.full(2, length / 2.0), np.full(2, RAIL_ARCH_T),
               np.full(2, z0), np.full(2, spring + rise * 0.15), mi)


def _rail_arches(mesh, xy, top, crossers):
    """Brick arches under a rail bridge part at every road crossing (crossers: (line, half, zroad))."""
    if not crossers:
        return
    mi = mesh.mat(BRICK)
    for line, hw, zr in crossers:
        distance, along = _underpass_distance(xy, line)
        j = int(np.argmin(distance))
        if not np.isfinite(distance[j]) or distance[j] > hw + DECK_HALF_M + 1.0:
            continue
        arc = _arc(line)
        t = float(along[j]) * arc[-1]
        p = np.array([np.interp(t, arc, line[:, 0]), np.interp(t, arc, line[:, 1])])
        z0 = float(np.interp(t, arc, zr))
        i = min(max(j, 1), len(xy) - 2)
        d = line[min(max(int(np.searchsorted(arc, t)), 1), len(line) - 1)] - \
            line[max(int(np.searchsorted(arc, t)) - 1, 0)]
        n = float(np.hypot(*d))
        if n < 1e-6:
            continue
        u = d / n
        v = np.array([-u[1], u[0]])
        crown = float(np.asarray(top)[i]) - DECK_D - 0.05
        _rail_arch(mesh, p, u, v, z0, crown, hw, RAIL_ARCH_LEN_M, mi)


def _tunnel_bed(mesh, p, uu, t, v, ballast, concrete, bore_w=None):
    """A tunnel's track bed: a concrete invert across the bore with the ballast on it, the walkways beside the ballast
    up to the tube's walls, its sides down to the invert (roadmesh.tunnel draws the tube around this floor)."""
    up = np.array([0.0, 0.0, 1.0])
    out_l = np.c_[uu, np.zeros(len(p))]
    hw = (bore_w or TUNNEL_BORE_W) / 2 + roadmesh.TUNNEL_SHOULDER
    inv = t - BALLAST_H
    mesh.strip(np.c_[p + uu * hw, inv], np.c_[p - uu * hw, inv], 0.0, 2 * hw / 3.0, v, concrete, up)
    L = np.c_[p + uu * BALLAST_TOP_M, t]
    R = np.c_[p - uu * BALLAST_TOP_M, t]
    Lf = np.c_[p + uu * hw, inv]
    Rf = np.c_[p - uu * hw, inv]
    mesh.strip(L, R, 0.0, 2 * BALLAST_TOP_M / 3.0, v, ballast, up)
    mesh.strip(L, Lf, 0.0, 0.8, v, ballast, out_l + up)
    mesh.strip(R, Rf, 0.0, 0.8, v, ballast, -out_l + up)
    # the walkways from the ballast's edges to the tube's walls, at floor level (the tube's section starts there)
    mesh.strip(np.c_[p + uu * hw, t], L, 0.0, (hw - BALLAST_TOP_M) / 1.5, v, concrete, up)
    mesh.strip(R, np.c_[p - uu * hw, t], 0.0, (hw - BALLAST_TOP_M) / 1.5, v, concrete, up)


def _tube_chunk(p, zb, portal, bore_w=None):
    """A roadmesh.tunnel chunk of a rail tunnel run: its floor at the ballast top, the full tunnel height, its portal
    ends (the ends of the mapped tunnel range, not a tile cut)."""
    n = np.c_[np.asarray(p, np.float64)[:, :2], np.asarray(zb, np.float64) + BALLAST_H]
    return {"m": "m_gm_concrete_plain", "w": bore_w or TUNNEL_BORE_W, "tl": 10.0, "p": 20,
            "n": [[round(float(q), 3) for q in pt] for pt in n],
            "fixed": True, "tunnel": True, "tube_only": True, "tunnel_h": roadmesh.TUNNEL_H,
            "portal": (bool(portal[0]), bool(portal[1]))}


def _piers(s, xy, u, t, zg, i, j):
    """The piers under a deck run: (centres, along, across, z0, z1), every PIER_EVERY from the part's start where the
    deck is PIER_MIN_H or more over the ground, not at its ends (the abutments: the embankment)."""
    ss = np.arange(PIER_EVERY, s[-1] - PIER_EVERY / 2, PIER_EVERY)
    ss = ss[(ss >= s[i]) & (ss <= s[j])]
    if not len(ss):
        return None
    zt = np.interp(ss, s, t) - DECK_D
    zgs = np.interp(ss, s, zg)
    keep = zt - zgs >= PIER_MIN_H
    ss, zt, zgs = ss[keep], zt[keep], zgs[keep]
    if not len(ss):
        return None
    cx, cy = np.interp(ss, s, xy[:, 0]), np.interp(ss, s, xy[:, 1])
    ux, uy = np.interp(ss, s, u[:, 0]), np.interp(ss, s, u[:, 1])
    un = np.c_[ux, uy] / np.maximum(np.hypot(ux, uy), 1e-9)[:, None]
    return np.c_[cx, cy], np.c_[un[:, 1], -un[:, 0]], un, zgs - PIER_FOOT_M, zt


GRADED_NEAR_M = 40.0        # m around the tile where the track's heights read the graded ground (tile_mesh far_ground)


def _mixed(ground, far_ground, box):
    """ground(xy) within GRADED_NEAR_M of box, far_ground(xy) beyond it."""
    if far_ground is None:
        return ground
    lo = np.array([box[0] - GRADED_NEAR_M, box[1] - GRADED_NEAR_M])
    hi = np.array([box[2] + GRADED_NEAR_M, box[3] + GRADED_NEAR_M])

    def mixed(xy):
        xy = np.asarray(xy, np.float64).reshape(-1, 2)
        near = ((xy >= lo) & (xy <= hi)).all(1)
        out = np.empty(len(xy))
        if near.any():
            out[near] = ground(xy[near])
        if (~near).any():
            out[~near] = far_ground(xy[~near])
        return out
    return mixed


# Level crossings' equipment (crossing_props; the models: tools/build_rail_crossing_props.py, trees.lua's rr* groups):
# per road class, a gate (its arm raised), a mast with lamps or a cross on its post, at each approach on the kerb side
# of the traffic, facing it. A disused line keeps its crosses, an abandoned one has none.
CROSSING_KIT = {"motorway": "gate", "trunk": "gate", "primary": "gate", "secondary": "gate", "tertiary": "flash"}
CROSSING_KIT_DEFAULT = "cross"
CROSSING_NO_KIT = {"footway", "cycleway", "path", "pedestrian", "steps", "track", "bridleway", "corridor"}
CROSSING_SIGN_OFF_M = 1.2       # m from the carriageway's edge to the post
CROSSING_SIGN_BEFORE_M = 2.5    # m before the track's ballast (along the road)
CROSSING_HEIGHT = {"cross": 3.263, "flash": 4.7, "gate": 8.25}
AMERICAS_LON = -30.0            # west of this the crossbuck (the Americas), else the St Andrew's cross
# light rail mostly runs in the street (a German Stadtbahn's tracks down Bielefeld's Schildescher Strasse had crosses
# at every side street, 2026-10-27): its crossings have devices only where the FRA's inventory or OSM's tags say
CROSSING_DATA_ONLY = {"light_rail"}


def crossing_props(frame, tx, ty, cells, ground, left_hand=False, kits=None, country=None):
    """Prop rows ([group, x, y, z, yaw, scale, height, 0]) of the level crossings in road tile (tx, ty): where a drawn
    line's ground part crosses a road's (cells: the road layers around the tile; ground(xy (n, 2)) -> z). kits: an
    optional fn(xy (2,), road class) -> "gate" / "flash" / "cross" / None from crossing data (the FRA's inventory in
    the US), else CROSSING_KIT by the road's class. Each crossing is the tile's that holds its point."""
    import roads as roadsmod
    box = roadsmod.tile_bbox(tx, ty)
    reach = 60.0
    worn = {}
    rails = [(fid, xy, c) for fid, c, kind, xy in parts(frame, cells, box, reach=reach, worn=worn) if kind == "ground"]
    if not rails:
        return []
    near = (box[0] - reach, box[1] - reach, box[2] + reach, box[3] + reach)
    roads, seen = [], set()
    for feats in cells:
        if feats is None or not len(feats):
            continue
        specs = roadsmod.road_specs(feats)
        fb = feats.boxes(frame)
        for i, sp in enumerate(specs):
            fid = str(feats.ids[i])
            if sp is None or fid in seen or sp.cls in CROSSING_NO_KIT or not _box_hit(fb[i], near):
                continue
            seen.add(fid)
            for ring in roadsmod.ground_parts(feats, i):
                e, n = mapgeom.enu_xy(feats, frame, ring)
                if len(e) >= 2:
                    roads.append((fid, sp, np.c_[e, n]))
    if not roads:
        return []
    americas = float(frame.lon0 if hasattr(frame, "lon0") else 0.0) < AMERICAS_LON
    if railxing.ENABLED:        # (a crossing of several tracks is one: railxing.py, BNE_RAIL_XING)
        usage = {str(f.ids[i]): str(u) for f in cells if f is not None and len(f)
                 for u, i in ((f.attrs.get("osm_usage", [None] * len(f))[i], i) for i in range(len(f))) if u}
        return railxing.crossing_rows(box, rails, roads, ground, worn=worn, usage=usage,
                                      nodes=railxing.node_lookup(frame, cells), fra=kits, country=country,
                                      lon=float(frame.lon0 if hasattr(frame, "lon0") else 0.0), left_hand=left_hand,
                                      owns=lambda x, y: roadsmod.box_owns(box, x, y))
    osm_kit = _osm_kits(frame, cells)
    rows, done = [], set()
    for (rfid, sp, line), crs in zip(roads, roadsmod._crossings_by([xy for _f, xy, _c in rails],
                                                                     [ln for *_, ln in roads])):
        for ri, _arc_r, pt in crs:
            pt = np.asarray(pt, np.float64)
            if not roadsmod.box_owns(box, pt[0], pt[1]):
                continue
            lfid, rxy, rcls = rails[ri]
            w = worn.get(lfid, 0)
            key = (rfid, lfid, round(float(pt[0]), 1), round(float(pt[1]), 1))
            if w >= 2 or key in done:
                continue
            done.add(key)
            kit = kits(pt, sp.cls) if kits is not None else None
            kit = kit or osm_kit(pt)
            if kit is None and rcls in CROSSING_DATA_ONLY:
                continue                # (a tram-like line in the street: only where the data says it has devices)
            kit = kit or CROSSING_KIT.get(sp.cls, CROSSING_KIT_DEFAULT)
            if w:
                kit = "cross"
            rows += _crossing_rows(pt, line, rxy, sp, kit, americas, left_hand, ground)
    return rows


OSM_KIT_M = 15.0        # m between a crossing and OSM's level crossing node it takes the devices of


def osm_kit_of(t):
    """"gate" / "flash" / "cross" / None of a level crossing node's OSM tags (crossing:barrier, crossing:light ...)."""
    barrier, light = str(t.get("crossing:barrier") or ""), str(t.get("crossing:light") or "")
    if barrier and barrier != "no":
        return "gate"
    if light and light != "no":
        return "flash"
    if barrier == "no" and light == "no":
        return "cross"
    return None             # (no word on its devices: the road class's kit)


def _osm_kits(frame, cells):
    """fn(xy) -> the kit of OSM's nearest level crossing node within OSM_KIT_M (railtags' nodes of the cells), or
    None."""
    pts = [(lo, la, t) for f in cells if f is not None for lo, la, t in f.__dict__.get("_rail_nodes", ())
           if t.get("railway") == "level_crossing"]
    if not pts:
        return lambda p: None
    a = np.array([[la, lo] for lo, la, _t in pts], np.float64)
    e, n, _ = frame.geodetic_to_enu(a[:, 0], a[:, 1], np.zeros(len(a)))
    xy = np.stack([np.asarray(e), np.asarray(n)], -1)
    kit = [osm_kit_of(t) for _lo, _la, t in pts]

    def look(p):
        d = np.hypot(*(xy - np.asarray(p)[:2]).T)
        j = int(np.argmin(d))
        return kit[j] if d[j] <= OSM_KIT_M else None
    return look


def _unit_at(line, pt):
    """The unit direction of polyline line at its segment nearest pt."""
    a, b = line[:-1], line[1:]
    d = b - a
    t = np.clip(((pt - a) * d).sum(1) / np.maximum((d * d).sum(1), 1e-12), 0.0, 1.0)
    j = int(np.argmin(np.hypot(*(a + t[:, None] * d - pt).T)))
    return d[j] / max(float(np.hypot(*d[j])), 1e-9)


def _crossing_rows(pt, line, rxy, sp, kit, americas, left_hand, ground):
    """The rows of one crossing: a kit at each approach (one on a one-way road), on its kerb side, facing it."""
    r = _unit_at(line, pt)
    t = _unit_at(rxy, pt)
    sin = abs(float(r[0] * t[1] - r[1] * t[0]))
    before = (BALLAST_TOP_M + BALLAST_SLOPE * BALLAST_H + CROSSING_SIGN_BEFORE_M) / max(sin, 0.35)
    off = sp.width / 2 + CROSSING_SIGN_OFF_M
    group = {"cross": "rrcross", "flash": "rrflash", "gate": "rrgate"}[kit] + ("" if americas else "_eu")
    if kit == "gate" and left_hand:
        group += "_l"
    dirs = (1.0, -1.0) if not sp.oneway else (-float(sp.dir or 1),)   # (sgn -1: travel along the geometry)
    out, pts = [], []
    for sgn in dirs:
        a = -r * sgn                                    # the travel direction towards the crossing
        side = np.array([a[1], -a[0]])                  # its right
        kerb = -side if left_hand else side
        q = pt - a * before + kerb * off
        pts.append((q, side))
    z = np.asarray(ground(np.array([q for q, _ in pts])), np.float64).ravel()
    for (q, side), zz in zip(pts, z):
        out.append([group, round(float(q[0]), 2), round(float(q[1]), 2), round(float(zz), 2),
                    round(math.degrees(math.atan2(side[1], side[0])) % 360.0, 1), 1.0, CROSSING_HEIGHT[kit], 0.0])
    return out


PLATFORM_H = 0.95           # m: a platform's top over the rail bed (DLR and National Rail: ~0.9-1.0)
PLATFORM_REACH_M = 30.0     # m: a platform polygon this far from a track is not one of its stations
PLATFORM_CLASSES = {"platform", "railway_platform", "train_station_platform"}


def _platforms(mesh, frame, box, infra_fn, rails, prof, material):
    """Station platform slabs from Overture's infrastructure `platform` polygons, on the nearest track's bed.
    A platform polygon is often at the station's higher level while the DEM under it is the street: its top is
    PLATFORM_H over the nearest rail's bed, not the ground. Nearest track within PLATFORM_REACH_M only."""
    import mapcells
    x0, y0, x1, y1 = box
    e = np.array([x0, x1, x0, x1, (x0 + x1) / 2])
    n = np.array([y0, y0, y1, y1, (y0 + y1) / 2])
    la, lo, _ = frame.enu_to_geodetic(e, n, np.zeros(len(e)))
    try:
        keys = mapcells.cells_for_points(la, lo, pad_lat=0.00005, pad_lon=0.00005, max_cells=32)
    except (ValueError, OverflowError):
        return
    seen = set()
    for key in keys:
        try:
            feats = infra_fn(key)
        except Exception:
            continue
        if feats is None or not len(feats) or feats.attrs.get("class") is None:
            continue
        classes = feats.attrs["class"]
        for i in range(len(feats)):
            if str(classes[i]).strip().lower() not in PLATFORM_CLASSES:
                continue
            fid = str(feats.ids[i])
            if fid in seen:
                continue
            seen.add(fid)
            for part in feats.parts(i):
                if not part or len(part[0]) < 3:
                    continue
                ring = np.asarray(part[0], np.float64)
                ex, ny = mapgeom.enu_xy(feats, frame, ring)
                xy = np.c_[ex, ny]
                if xy[:, 0].max() < x0 or xy[:, 0].min() > x1 or xy[:, 1].max() < y0 or xy[:, 1].min() > y1:
                    continue
                c = xy.mean(0)
                if not (x0 <= c[0] < x1 and y0 <= c[1] < y1):
                    continue                    # (its centre owns it: no slab from both tiles)
                if len(rails) == 0:
                    continue
                best = None
                for (_fid, _cls, _kind, rxy), (zb, _zg) in zip(rails, prof):
                    rxy = np.asarray(rxy, np.float64)
                    if not len(rxy):
                        continue
                    d = np.hypot(rxy[:, 0] - c[0], rxy[:, 1] - c[1])
                    j = int(np.argmin(d))
                    if best is None or d[j] < best[0]:
                        best = (float(d[j]), float(np.asarray(zb)[j]))
                if best is None or best[0] > PLATFORM_REACH_M:
                    continue
                rel = xy - c
                _u, _s, vt = np.linalg.svd(rel, full_matrices=False)
                ax, ay = vt[0], vt[1]
                hx = float(np.abs(rel @ ax).max()) + 0.02
                hy = float(np.abs(rel @ ay).max()) + 0.02
                z = best[1]
                mesh.boxes(c[None], ax[None], ay[None], np.array([hx]), np.array([hy]),
                           np.array([z - 0.3]), np.array([z + PLATFORM_H]), material)


def tile_mesh(frame, tx, ty, cells, ground, sign_style=None, far_ground=None, wet=None, tube_out=None,
              infra_fn=None):
    """The road tile (tx, ty)'s railway mesh (roadmesh.write's (positions, tris, uvs, materials, tri material)) or
    None. cells: the roads layers of the cells within REACH_M of the tile (cells_of_tile(..., margin=REACH_M));
    ground(xy (n, 2)) -> z: the drawn ground in the frame. far_ground: the natural ground, read beyond GRADED_NEAR_M
    of the tile (the lines REACH_M round it only smooth the track and carry its ramps: the graded ground there made the
    grader plan the cells round every tile, and the railways doubled a tile's time, 2026-10-25). wet(lat, lon): water
    depth (m) over an undersea tunnel (None: no sea); tube_out: a list that gets the rail tunnels' tube chunks
    (roadmesh.tunnel); infra_fn(key) -> the cell's infrastructure Features (station platforms, optional)."""
    cells = list(cells)
    box = roads.tile_bbox(tx, ty)
    tunnels = road_underpasses(frame, cells, box)
    ground = _mixed(ground, far_ground, box)
    ex = (box[0] - roads.OVERLAP, box[1] - roads.OVERLAP, box[2] + roads.OVERLAP, box[3] + roads.OVERLAP)
    worn, gauges, levels = {}, {}, {}
    rails = parts(frame, cells, box, clip=True, worn=worn, gauges=gauges, levels=levels)
    if not any(_box_hit((xy[:, 0].min(), xy[:, 1].min(), xy[:, 0].max(), xy[:, 1].max()),
                        (ex[0] - STEP_M, ex[1] - STEP_M, ex[2] + STEP_M, ex[3] + STEP_M)) for *_, xy in rails):
        return None
    cr = crossings(frame, cells, box, reach=50.0) + tunnels
    prof = profile(rails, ground, cr + coarse_underpasses(frame, cells, box, rails), levels=levels)
    gaps = _neighbour_gaps(rails, [zb for zb, _g in prof])
    tunnel_z = tunnel_floors(rails, frame, ground, wet, levels) if any(k == "tunnel" for _f, _c, k, _xy in rails) else {}
    tunnel_ports = _tunnel_portals(rails) if tunnel_z else {}
    bore_w = _bore_widths(rails, tunnel_z) if tunnel_z else {}
    twins = _twin_tubes(rails, tunnel_z) if tunnel_z else {}
    crossers = []
    if cr:                                          # (one ground call for all of them: its cost is per call)
        zc = np.asarray(ground(np.concatenate([ln for ln, _ in cr])), np.float64)
        k = 0
        for line, hw in cr:
            crossers.append((line, hw, zc[k:k + len(line)]))
            k += len(line)
    mesh = _Mesh((box[0], box[1], 0.0))
    ballast, concrete, steel = mesh.mat(BALLAST), mesh.mat(CONCRETE), mesh.mat(STEEL)
    up = np.array([0.0, 0.0, 1.0])
    small = []                                      # sleepers and piers: drawn last, as room allows
    pier_pts = []                                   # the piers drawn: a parallel track's pier beside one is the same
    for n, ((fid, cls, kind, xy), (zb, zg)) in enumerate(zip(rails, prof)):
        if kind == "tunnel":
            if n not in tunnel_z:
                continue                        # (its ground was missing: no track on the surface either)
            zb = tunnel_z[n]                    # (the bed under the hill / the sea, not the ground over it)
        inside = (xy[:, 0] >= ex[0] - STEP_M) & (xy[:, 0] <= ex[2] + STEP_M) & \
                 (xy[:, 1] >= ex[1] - STEP_M) & (xy[:, 1] <= ex[3] + STEP_M)
        if not inside.any():
            continue
        u = _left(xy)
        s = _arc(xy)
        w = worn.get(fid, 0)
        ballast_w, steel_w = mesh.mat(BALLAST_WORN[w]), mesh.mat(STEEL_WORN[w])
        half_g = gauges.get(fid, gauge(cls, sign_style)) / 2 + RAIL_HEAD / 2
        top = zb + BALLAST_H
        if kind == "ground":
            blk = _blocked(xy, zb, crossers)
            if blk.any():           # (its rails flush with the road at a level crossing)
                zb = _flush(s, zb, blk, np.asarray(ground(xy[blk]), np.float64))
                top = zb + BALLAST_H
            cut = blk | ~inside
            # the ballast's sides to the ground beside: wider where the bed stands high (a ramp's embankment)
            near = BALLAST_TOP_M + BALLAST_SLOPE * (BALLAST_H + BALLAST_FOOT_M)
            feet = []
            for sg in (1, -1):
                zs = np.asarray(ground(xy + sg * u * near), np.float64)
                foot = np.minimum(BALLAST_TOP_M + BALLAST_SLOPE * (np.maximum(top - zs, 0.0) + BALLAST_FOOT_M),
                                  FOOT_MAX_M)
                far = foot > near + 0.5
                if far.any():
                    zs = zs.copy()
                    zs[far] = np.asarray(ground(xy[far] + sg * u[far] * foot[far, None]), np.float64)
                feet.append((foot, zs))
            # a fill's foot stops at a road's edge or another track's deck: a wall there, not a slope over them
            others = [(f2, xy2) for f2, _c2, _k2, xy2 in rails if f2 != fid]
            for k, sg in enumerate((1, -1)):
                foot, zs = feet[k]
                lim = _foot_limits(xy, sg * u, foot, others, crossers)
                if np.any(lim < foot):
                    foot = lim
                    zs = np.asarray(ground(xy + sg * u * foot[:, None]), np.float64)
                    feet[k] = (foot, zs)
        else:
            cut = ~inside
        arches_done = False
        for i, j in _runs(cut):
            sl = slice(i, j + 1)
            p, uu, t, v = xy[sl], u[sl], top[sl], s[sl] / 3.0
            if kind == "ground":
                (fl, zl), (fr, zr) = feet
                _ballast_bed(mesh, p, uu, t, v, fl[sl], fr[sl], zl[sl], zr[sl], ballast_w)
            elif kind == "tunnel":
                _tunnel_bed(mesh, p, uu, t, v, ballast_w, concrete, bore_w.get(n))
                if tube_out is not None:
                    mouth = tunnel_ports.get(n, (True, True))
                    tw = twins.get(n)
                    skip = tw["skip"][sl] if tw is not None else np.zeros(len(p), bool)
                    for a, b in _runs(skip):
                        g0, g1 = i + a, i + b
                        pp = xy[g0:g1 + 1].copy()
                        ww = bore_w.get(n, TUNNEL_BORE_W)
                        if tw is not None:
                            pp[:, :2] = tw["center"][g0:g1 + 1]
                            ww = max(ww, float(np.max(tw["width"][g0:g1 + 1])))
                        tube_out.append(_tube_chunk(
                            pp, zb[g0:g1 + 1],
                            (g0 == 0 and mouth[0] and not bool(skip[0]),
                             g1 == len(xy) - 1 and mouth[1] and not bool(skip[-1])), ww))
            else:
                if cls == "tram":
                    _tram_deck(mesh, p, uu, t, v, concrete)
                else:
                    wl = wr = None
                    wall_l = wall_r = True
                    gg = gaps.get(n)
                    if gg is not None:
                        gl, gr = gg
                        if np.isfinite(gl).any():
                            wl = np.where(np.isfinite(gl), np.minimum(DECK_HALF_M, gl / 2.0), DECK_HALF_M)
                            wall_l = not np.isfinite(np.median(gl))
                        if np.isfinite(gr).any():
                            wr = np.where(np.isfinite(gr), np.minimum(DECK_HALF_M, gr / 2.0), DECK_HALF_M)
                            wall_r = not np.isfinite(np.median(gr))
                    _deck(mesh, p, uu, t, v, ballast_w, concrete,
                          None if wl is None else wl[sl], None if wr is None else wr[sl], wall_l, wall_r)
                    if not arches_done:
                        _rail_arches(mesh, xy, top, crossers)
                        arches_done = True
                pr = _sited(_piers(s, xy, u, top, zg, i, j), crossers, ground)
                if pr is not None and pier_pts:
                    c = pr[0]
                    kp = np.asarray(pier_pts, np.float64)
                    d = np.hypot(c[:, None, 0] - kp[None, :, 0], c[:, None, 1] - kp[None, :, 1]).min(1)
                    keep = d >= PIER_DEDUPE_M
                    pr = tuple(a[keep] for a in pr) if keep.any() else None
                if pr is not None and len(pr[0]):
                    pier_w = min(PIER_W, 2 * TRAM_DECK_HALF_M) if cls == "tram" else PIER_W
                    small.append((pr, PIER_T / 2, pier_w / 2, concrete))
                    pier_pts.extend(map(tuple, np.asarray(pr[0], np.float64).tolist()))
            st = t - SLEEPER_SINK + SLEEPER_H           # the sleepers' top: the rails' foot
            if cls == "tram":                       # railheads embedded flush in the slab, no exposed sleepers
                st = t - BALLAST_H + FLUSH_LIFT_M - RAIL_H + 0.005
            out_l = np.c_[uu, np.zeros(len(p))]
            for sg in (1, -1):
                c = p + uu * sg * half_g
                a, b = c + uu * RAIL_HEAD / 2, c - uu * RAIL_HEAD / 2
                mesh.strip(np.c_[a, st + RAIL_H], np.c_[b, st + RAIL_H], 0.0, 0.02, v, steel_w, up)
                mesh.strip(np.c_[a, st], np.c_[a, st + RAIL_H], 0.0, 0.05, v, steel_w, out_l)
                mesh.strip(np.c_[b, st], np.c_[b, st + RAIL_H], 0.0, 0.05, v, steel_w, -out_l)
            if cls != "tram":
                sp = _sleepers(s, xy, u, top, i, j, SLEEPERS_GONE[w])
                if sp is not None:
                    small.append((sp, SLEEPER_W / 2, SLEEPER_L / 2, mesh.mat(SLEEPER_WORN[w])))
    footbridges.add(mesh, walk, ground, crossers, ex, concrete, small)
    small.sort(key=lambda e: -e[2])                 # (the piers first: a deck needs them more than its sleepers)
    for (c, along, across, z0, z1), hx, hy, mi in small:
        room = (VERTS_MAX - mesh.nv) // 8
        if room <= 0:
            break
        n = min(room, len(c))
        mesh.boxes(c[:n], along[:n], across[:n], hx, hy, z0[:n], z1[:n], mi)
    if infra_fn is not None:
        # (a tunnel station's platform on the bore's floor, not on the street's lower hull above it: underground
        # stations stood at ground level, 2026-11-07)
        bed_prof = [(tunnel_z.get(n, zb), zg) for n, (zb, zg) in enumerate(prof)]
        _platforms(mesh, frame, box, infra_fn, rails, bed_prof, concrete)
    out = mesh.result()
    if out is not None and len(out[0]) > VERTS_MAX:     # (the ballast and rails alone past it: a rail yard)
        return (None, decks) if return_decks else None
    return (out, decks) if return_decks else out


# The materials only drawn (from mpsteppe's lean rail collider, in Madderose's PR #41): sleepers were ~40 % of a
# track's triangles (Tehachapi Loop, 16 tiles: 118.8k of 135.0k with the rails), and a collision rebuild's cost grows
# with every colliding triangle loaded. The rails stay: they are the surface a train's wheels ride on and a car bumps
# over (the bed alone left them 0.28 m of visual-only steel). BNGE_RAIL_COLLISION=0: the old bed-only collider.
RENDER_ONLY = frozenset(SLEEPER_WORN)
RAIL_COLLISION = os.environ.get("BNGE_RAIL_COLLISION", "1") != "0"


def collision_mesh(mesh, rails=None):
    """tile_mesh's mesh without the sleepers (RENDER_ONLY): the ballast, the decks, the piers and (rails, default
    RAIL_COLLISION) the rails themselves, as (positions, tris, uvs, materials, tri material) for a collision-only
    shape (the vertices are the full mesh's: cdae drops the unused ones), or None if nothing is left. Pure."""
    if mesh is None:
        return None
    P, T, UV, mats, M = mesh
    M = np.asarray(M)
    keep = ~np.isin(M, [k for k, m in enumerate(mats) if m in RENDER_ONLY])
    if not (RAIL_COLLISION if rails is None else rails):
        keep &= ~np.isin(M, [k for k, m in enumerate(mats) if m in STEEL_WORN])
    if not keep.any():
        return None
    return P, np.asarray(T)[keep], UV, mats, M[keep]
