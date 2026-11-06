"""Street-name plates on UK/Irish road tiles (signstream's "names" field).

OpenStreetMap names every street; `signroads.Context.pavements` already gives the tile's road rows with
their names (the roads layer's `name`, joined by osmtags). A black plate with white lettering - the
London nameplate - stands on the footway near a junction, facing the carriageway. One plate per street
name and tile, so a long street gets another at its next tile; render-only, built with the guide signs'
glyph atlas and materials (exitsigns.Mesh and GUIDE_MATERIALS).

The plate is skipped outside the UK/Ireland and on motorways, trunks, service roads, links and very
short pieces. Placement is deterministic: one plate per name, at the middle of its longest piece in
the tile, on the left footway (a sign on the other side is the next street's own).
"""
import numpy as np

import exitsigns
import roads
import signroads

COUNTRIES = {"GB", "IE", "IM", "JE", "GG"}
SKIP_CLASSES = {"motorway", "trunk", "service", "track", "path", "pedestrian", "footway", "steps"}
MATERIALS = exitsigns.GUIDE_MATERIALS
PLATE_H = 0.24
PLATE_BOTTOM = 2.15
PLATE_DEPTH = 0.062
POST_D = 0.035
MAX_W = 2.0
MIN_PIECE_M = 12.0
INSET_M = 0.85              # m from the kerb onto the footway (clear of the gutter and the kerb line)


def _at(piece, s):
    """(point (3,), unit tangent (2,)) at arc length s along piece."""
    d = np.diff(piece[:, :2], axis=0)
    seg = np.hypot(d[:, 0], d[:, 1])
    arc = np.concatenate(([0.0], np.cumsum(seg)))
    s = float(np.clip(s, 0.0, arc[-1]))
    k = min(max(int(np.searchsorted(arc, s, side="right") - 1), 0), len(piece) - 2)
    span = max(float(arc[k + 1] - arc[k]), 1e-9)
    t = (s - float(arc[k])) / span
    p = piece[k] + t * (piece[k + 1] - piece[k])
    tan = d[k] / span
    n = np.hypot(*tan)
    return p, tan / max(n, 1e-9)


def _plate(mesh, name, at, face, right):
    mesh.centre = np.asarray(at, np.float64)
    mesh.face = np.r_[np.asarray(face, np.float64), 0.0]
    mesh.right = np.r_[np.asarray(right, np.float64), 0.0]
    w = min(MAX_W, max(0.72, exitsigns.guide_width(name.upper(), PLATE_H * 0.68) + 0.26))
    mesh.rectangle(w + 0.05, PLATE_BOTTOM - 0.025, PLATE_H + 0.05, PLATE_DEPTH - 0.007, 1)
    mesh.rectangle(w, PLATE_BOTTOM, PLATE_H, PLATE_DEPTH, 6)
    mesh.guide_text(name.upper(), 0.0, PLATE_BOTTOM + 0.052, PLATE_H * 0.62, w - 0.14)
    y = -w / 2 + 0.07
    mesh.box((-POST_D, y, 0.0), (POST_D, y + 2 * POST_D, PLATE_BOTTOM), 2)


def build_tile(frame, tx, ty, elev, context, left_hand=False, country=None):
    """The tile's nameplate mesh, or None (outside the UK/Ireland, no named road, no context)."""
    if context is None or str(country or "").upper() not in COUNTRIES:
        return None
    box = roads.tile_bbox(tx, ty)
    e = np.array([box[0], box[2], box[0], box[2], (box[0] + box[2]) / 2])
    n = np.array([box[1], box[1], box[3], box[3], (box[1] + box[3]) / 2])
    lat, lon, _ = frame.enu_to_geodetic(e, n, np.zeros(len(e)))
    try:
        rows = context.pavements(frame, np.c_[lon, lat], left_hand)
    except Exception:               # (optional scenery: a missing cell must not cost the tile)
        return None
    chosen = {}                     # name -> (length, piece)
    for fid, sp, line, width in rows:
        name = str(getattr(sp, "name", "") or "").strip()
        if not name or sp.cls in SKIP_CLASSES or getattr(sp, "link", False):
            continue
        if not (0.0 < float(width) < 30.0):
            continue
        pieces = roads.clip_polyline(np.asarray(line, np.float64), *box)
        if not pieces:
            continue
        piece = max(pieces, key=lambda p: float(np.hypot(*np.diff(p[:, :2], axis=0).T).sum()) if len(p) > 1 else 0.0)
        if len(piece) < 2:
            continue
        length = float(np.hypot(*np.diff(piece[:, :2], axis=0).T).sum())
        if length < MIN_PIECE_M:
            continue
        if name not in chosen or length > chosen[name][0]:
            chosen[name] = (length, piece, float(width))
    if not chosen:
        return None
    mesh = exitsigns.Mesh((box[0], box[1], 0.0))
    made = 0
    for name in sorted(chosen):
        length, piece, width = chosen[name]
        p, tan = _at(piece, length * 0.5)
        nrm = np.array([-tan[1], tan[0]])           # left of the line
        side = 1
        q = p[:2] + nrm * side * (width / 2 + INSET_M)
        qa, qo, _ = frame.enu_to_geodetic(np.array([q[0]]), np.array([q[1]]), np.zeros(1))
        z = float(np.asarray(elev(qa, qo)).reshape(-1)[0])
        face = -nrm * side
        right = -tan * side                          # the viewer's right, reading the plate
        _plate(mesh, name, (q[0], q[1], z), face, right)
        made += 1
    if not made or not mesh.t:
        return None
    return (np.asarray(mesh.p), np.asarray(mesh.t), np.asarray(mesh.uv), MATERIALS, np.asarray(mesh.mi))
