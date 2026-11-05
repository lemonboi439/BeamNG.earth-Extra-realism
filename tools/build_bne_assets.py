#!/usr/bin/env python3
"""BeamNG.Earth's own tileable textures: no asset from another level.

The mod used to draw its roads with West Coast USA's asphalt, sidewalks, curbs and lines, Johnson Valley's
dirt, Italy's cobblestone, Driver Training's drain and Gridmap's concrete, and its buildings with BeamNG's
stock material library. Every one is replaced here by a texture generated from seeded noise and shapes
(no third-party image), written to
mod/beamng_earth/levels/beamng_earth/art/shapes/beamngEarth/bne_tex/ and wired into the mod's material
files and roads.materials.json. Deterministic: running it again gives the same pixels.

    python tools/build_bne_assets.py [--check]     (--check: fail if a file would change)
"""
import argparse
import hashlib
import json
import math
import os
import re
import sys
import uuid

import struct
import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))
try:                                # BC7 DDS: the game takes it as it is (no main-thread recompression, ~1 byte/px)
    import bc7 as _bc7
except Exception:                   # (the Rust kernel not built here: the PNGs are the fallback)
    _bc7 = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "mod", "beamng_earth", "levels", "beamng_earth", "art", "shapes", "beamngEarth", "bne_tex")
SHAPES = os.path.dirname(OUT)
URL = "/levels/beamng_earth/art/shapes/beamngEarth/bne_tex"
S = 512
SEED = 20261105


def _rng(tag):
    h = hashlib.blake2s(f"{SEED}:{tag}".encode(), digest_size=8).digest()
    return np.random.default_rng(int.from_bytes(h, "little"))


def noise(shape, scale, tag, octaves=4, gain=0.5):
    """Seamless fBm noise in [0, 1]: each octave a white noise filtered in the frequency domain (periodic)."""
    rng = _rng(tag)
    out = np.zeros(shape, np.float64)
    amp = 1.0
    for o in range(octaves):
        white = rng.standard_normal(shape)
        F = np.fft.fft2(white)
        fy = np.fft.fftfreq(shape[0])[:, None]
        fx = np.fft.fftfreq(shape[1])[None, :]
        rad = np.sqrt(fx * fx + fy * fy)
        F *= np.exp(-(rad * scale * (2 ** o)) ** 2)
        layer = np.fft.ifft2(F).real
        layer -= layer.mean()
        layer /= layer.std() + 1e-9
        out += amp * layer
        amp *= gain
    out -= out.min()
    out /= out.max() + 1e-9
    return out


def normal_map(h, strength=2.0):
    dzdx = (np.roll(h, -1, axis=1) - np.roll(h, 1, axis=1)) * 0.5
    dzdy = (np.roll(h, -1, axis=0) - np.roll(h, 1, axis=0)) * 0.5
    n = np.dstack([-dzdx * strength, -dzdy * strength, np.ones_like(h)])
    n /= np.sqrt((n * n).sum(2))[..., None] + 1e-9
    return np.clip(n * 0.5 + 0.5, 0.0, 1.0)


def _dds(name, rgb, srgb):
    """A BC7 DDS beside the PNG (the material files point at it); DXGI_UNORM_SRGB for colour, UNORM for data."""
    if _bc7 is None:
        return
    img = np.clip(np.asarray(rgb) * 255.0 + 0.5, 0, 255).astype(np.uint8)
    if img.ndim != 3 or img.shape[0] != img.shape[1] or (img.shape[0] & (img.shape[0] - 1)):
        return                          # (the 1-D depth ramp stays a PNG)
    # BC7 is 4x4-blocked: the engine rejects a compressed DDS whose mips are smaller than 4 pixels, so stop there
    chain = [m for m in _bc7.mips(img) if m.shape[0] >= 4]
    body = b"".join(_bc7.encode(m) for m in chain)
    w = h = img.shape[0]
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000 | 0x80000
    head = struct.pack("<4s7I", b"DDS ", 124, flags, h, w,
                                     max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * 16, 1, len(chain))
    head += bytes(44)
    head += struct.pack("<2I4s5I", 32, 0x4, b"DX10", 0, 0, 0, 0, 0)
    head += struct.pack("<4I", 0x401008, 0, 0, 0)
    head += struct.pack("<I", 0)
    head += struct.pack("<5I", 99 if srgb else 98, 3, 0, 1, 0)
    with open(os.path.join(OUT, name.rsplit(".", 1)[0] + ".dds"), "wb") as f:
        f.write(head + body)


def save(name, rgb, alpha=None, srgb=True):
    a = np.clip(np.asarray(rgb) * 255.0 + 0.5, 0, 255).astype(np.uint8)
    if alpha is not None:
        al = np.clip(np.asarray(alpha) * 255.0 + 0.5, 0, 255).astype(np.uint8)
        im = Image.fromarray(np.dstack([a, al]), "RGBA")
    else:
        im = Image.fromarray(a, "RGB")
    im.save(os.path.join(OUT, name), optimize=True)
    f = np.asarray(rgb, np.float64)
    _dds(name, np.dstack([f, np.asarray(alpha, np.float64)]) if alpha is not None else f, srgb)
    return name


def gray(x):
    return np.dstack([x, x, x])


def tex(tag, base, albedo=None, normal_h=None, roughness=None, n_strength=2.0):
    """Write <tag>_b.color.png, <tag>_nm.normal.png and <tag>_r.data.png of one material set."""
    rgb = base if albedo is None else albedo
    save(f"t_bne_{tag}_b.color.png", rgb)
    h = normal_h if normal_h is not None else noise(rgb.shape[:2], 6, tag + ":nm", 3)
    save(f"t_bne_{tag}_nm.normal.png", normal_map(h, n_strength), srgb=False)
    r = roughness if roughness is not None else 0.7 + 0.15 * noise(rgb.shape[:2], 10, tag + ":r", 3)
    r = np.asarray(r, np.float64)
    if r.ndim == 0:                     # (a flat roughness: one value per texel, not a 1-pixel image)
        r = np.full(rgb.shape[:2], float(r))
    save(f"t_bne_{tag}_r.data.png", gray(np.clip(r, 0, 1)), srgb=False)


def n_of(shape, tag, scale=6, octaves=4):
    return noise(shape, scale, tag, octaves)


def stones(size, cells, jitter, tag, palette, mortar, radius=(0.62, 0.92), sharp=0.08):
    """Seamless stone/cobble field: a jittered grid's Voronoi cells, shaded and darkened at the joints."""
    rng = _rng(tag)
    step = size / cells
    gy, gx = np.mgrid[0:size, 0:size]
    best = np.full((size, size), 1e9)
    second = np.full((size, size), 1e9)
    idx = np.zeros((size, size), np.int32)
    seeds = {}
    for j in range(cells):
        for i in range(cells):
            seeds[(j, i)] = (i * step + step / 2 + (rng.random() - 0.5) * step * jitter,
                             j * step + step / 2 + (rng.random() - 0.5) * step * jitter)
    n = 0
    for j in range(cells):
        for i in range(cells):
            sx, sy = seeds[(j, i)]
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    px, py = sx + dx * size, sy + dy * size
                    d = np.hypot(gx - px, gy - py)
                    m = d < best
                    second[m] = best[m]
                    best[m] = d[m]
                    idx[m] = n
            n += 1
    edge = (second - best)
    stone = np.clip(edge / (step * sharp), 0, 1)           # 0 at the joint, 1 in the stone
    h = (1.0 - best / step) * 0.7 + stone * 0.3
    base = np.asarray(palette, np.float64)[idx % len(palette)]
    shade = 0.75 + 0.25 * stone[..., None]
    rgb = base * shade
    joint = stone[..., None] < 0.25
    noise_mortar = 0.85 + 0.15 * _rng(tag + ":mortar").random((size, size, 1))
    rgb = np.where(joint, np.asarray(mortar) * noise_mortar, rgb)
    return np.clip(rgb, 0, 1), np.clip(h, 0, 1)


def draw_bricks(size, rows, cols, tag, palette, mortar, mortar_px=3, neutral=True):
    rng = _rng(tag)
    im = Image.new("RGB", (size, size), tuple(int(v * 255) for v in mortar))
    d = ImageDraw.Draw(im)
    rh = size // rows
    bw = size // cols
    for j in range(rows):
        off = (bw // 2) if j % 2 else 0
        for i in range(-1, cols + 1):
            x0 = i * bw + off
            if not neutral:
                c = palette[int(rng.integers(len(palette)))]
            else:                       # a grey face the material's tint colours (vertColor)
                v = 0.55 + 0.14 * rng.random()
                c = (v * 1.02, v, v * 0.96)
            var = (rng.random() - 0.5) * 0.08
            col = tuple(int(np.clip(v + var, 0, 1) * 255) for v in c)
            d.rectangle([x0 + mortar_px, j * rh + mortar_px, x0 + bw - mortar_px, (j + 1) * rh - mortar_px], fill=col)
    a = np.asarray(im, np.float64) / 255.0
    n = noise((size, size), size / max(rows, cols) * 0.9, tag + ":n", 3)
    h = n * 0.08
    # brick faces are flat, the mortar is recessed: height from the pattern's edges
    grey = a.mean(2)
    h += np.clip((grey - grey.min()) / (np.ptp(grey) + 1e-9), 0, 1) * 0.2
    return a, h


def main():
    os.makedirs(OUT, exist_ok=True)
    files = []
    BASES = {}

    def add(*names):
        files.extend(names)

    def base(cat, rgb):
        BASES[cat] = np.asarray(rgb, np.float64)

    # --- asphalt -------------------------------------------------------------------------------------------
    h = noise((S, S), 3.0, "asphalt:h", 4)
    n = noise((S, S), 2.0, "asphalt:n", 3)
    speck = (_rng("asphalt:speck").random((S, S)) > 0.997).astype(np.float64)
    base = 0.28 + 0.05 * h + 0.14 * speck
    rgb = np.dstack([base * 1.01, base, base * 0.98])
    add("t_bne_asphalt_b.color.png", "t_bne_asphalt_nm.normal.png", "t_bne_asphalt_r.data.png")
    tex("asphalt", rgb, np.clip(rgb, 0, 1), h, 0.82 + 0.10 * n, 0.9)

    # --- concrete (sidewalk, gutter, highway) --------------------------------------------------------------
    h = noise((S, S), 12, "concrete:h", 4)
    stain = noise((S, S), 60, "concrete:stain", 3)
    base = 0.66 + 0.06 * h - 0.07 * (stain > 0.72) * (stain - 0.72)
    rgb = np.dstack([base * 1.0, base * 0.99, base * 0.96])
    # a joint along the left edge: SLAB_REPEAT_M / CURB_REPEAT_M repeat it along the walk
    rgb[:, :2] *= 0.55
    rgb[:2, :] *= 0.6
    h[:, :2] -= 0.25
    h[:2, :] -= 0.2
    add("t_bne_concrete_b.color.png", "t_bne_concrete_nm.normal.png", "t_bne_concrete_r.data.png")
    tex("concrete", rgb, np.clip(rgb, 0, 1), h, 0.72 + 0.10 * h, 1.0)

    # --- precast concrete (bridge piers, decks) ------------------------------------------------------------
    h = noise((S, S), 20, "precast:h", 3)
    rgb = np.dstack([0.52 + 0.05 * h] * 3)
    for k in range(0, S, 128):
        rgb[:, k:k + 2] *= 0.72
        h[:, k:k + 2] -= 0.2
    tex("precast", np.clip(rgb, 0, 1), np.clip(rgb, 0, 1), h, 0.80 + 0.08 * h, 1.0)

    # --- cobblestone ----------------------------------------------------------------------------------------
    pal = [(0.48, 0.45, 0.40), (0.55, 0.50, 0.44), (0.40, 0.39, 0.37), (0.60, 0.54, 0.46), (0.45, 0.42, 0.38)]
    rgb, h = stones(S, 14, 0.55, "cobble", pal, (0.18, 0.17, 0.16), 0.55, 0.10)
    tex("cobble", np.clip(rgb, 0, 1), np.clip(rgb, 0, 1), h, 0.78 + 0.10 * noise((S, S), 8, "cobble:r", 3), 1.6)

    # --- brick ----------------------------------------------------------------------------------------------
    rgb, h = draw_bricks(S, 16, 8, "brick", [], (0.22, 0.21, 0.20), 3, neutral=True)
    tex("brick", np.clip(rgb, 0, 1), np.clip(rgb, 0, 1), h, 0.84 + 0.08 * noise((S, S), 12, "brick:r", 3), 1.4)

    # --- stone (irregular blocks) ---------------------------------------------------------------------------
    pal = [(0.58, 0.56, 0.52), (0.50, 0.48, 0.45), (0.64, 0.60, 0.54), (0.46, 0.46, 0.44)]
    rgb, h = stones(S, 7, 0.7, "stone", pal, (0.25, 0.24, 0.23), 0.80, 0.05)
    tex("stone", np.clip(rgb, 0, 1), np.clip(rgb, 0, 1), h, 0.86 + 0.08 * noise((S, S), 12, "stone:r", 3), 1.2)

    # --- plaster / stucco -----------------------------------------------------------------------------------
    h = noise((S, S), 30, "plaster:h", 4)
    fine = noise((S, S), 2.0, "plaster:fine", 3)
    base = 0.78 + 0.06 * h + 0.02 * fine
    tex("plaster", gray(np.clip(base, 0, 1)), gray(np.clip(base, 0, 1)), h * 0.2 + fine * 0.05, 0.92, 0.6)

    # --- wood siding ----------------------------------------------------------------------------------------
    x = np.arange(S)[None, :]
    grain = np.sin(x / 3.0) * 0.02 + noise((S, S), 4, "wood:grain", 3) * 0.06
    rgb = np.dstack([0.52 + grain, 0.42 + grain * 0.8, 0.30 + grain * 0.6])
    for k in range(0, S, 64):
        rgb[:, k:k + 2] *= 0.62
    h = noise((S, S), 5, "wood:h", 3) + np.tile(np.sin(x / 3.0) * 0.06, (S, 1))
    tex("wood", np.clip(rgb, 0, 1), np.clip(rgb, 0, 1), h, 0.82 + 0.1 * noise((S, S), 6, "wood:r", 3), 1.0)

    # --- metal / cladding -----------------------------------------------------------------------------------
    brushed = noise((S, S), 1.5, "metal:brush", 3)
    rgb = np.dstack([0.55 + 0.03 * brushed] * 3)
    tex("metal", np.clip(rgb, 0, 1), np.clip(rgb, 0, 1), brushed * 0.08, 0.45 + 0.12 * brushed, 0.6)
    ridge = np.tile(np.sin(np.arange(S)[None, :] / S * math.pi * 2 * 32) * 0.5 + 0.5, (S, 1))
    rgb = np.dstack([0.50 + 0.04 * ridge] * 3)
    h = np.tile(ridge, (S, 1))
    tex("corrugated", np.clip(rgb, 0, 1), np.clip(rgb, 0, 1), h, 0.5 + 0.2 * ridge, 2.4)

    # --- roofs ----------------------------------------------------------------------------------------------
    rng = _rng("shingle")
    rgb = np.zeros((S, S, 3))
    for j in range(16):
        y0 = j * (S // 16)
        off = (S // 32) if j % 2 else 0
        for i in range(-1, 17):
            x0 = i * (S // 16) + off
            v = 0.34 + 0.12 * rng.random()
            rgb[y0:y0 + S // 16 - 2, x0:x0 + S // 16 - 2] = (v, v * 0.97, v * 0.93)
    h = np.clip((rgb.mean(2) - 0.25) * 2.0, 0, 1)
    tex("roof_shingle", np.clip(rgb, 0, 1), np.clip(rgb, 0, 1), h, 0.86, 1.2)

    pal = [(0.32, 0.33, 0.34), (0.38, 0.39, 0.40), (0.28, 0.30, 0.32), (0.42, 0.42, 0.41)]
    rng = _rng("slate")
    rgb = np.zeros((S, S, 3))
    for j in range(20):
        y0 = j * (S // 20)
        off = (S // 40) if j % 2 else 0
        for i in range(-1, 21):
            x0 = i * (S // 20) + off
            c = pal[int(rng.integers(len(pal)))]
            rgb[y0:y0 + S // 20 - 2, x0:x0 + S // 20 - 2] = np.asarray(c) * (0.9 + 0.2 * rng.random())
    h = np.clip((rgb.mean(2) - 0.2) * 2.0, 0, 1)
    tex("roof_slate", np.clip(rgb, 0, 1), np.clip(rgb, 0, 1), h, 0.72, 1.3)

    h = noise((S, S), 10, "roof_asphalt", 4)
    rgb = gray(0.22 + 0.10 * h)
    tex("roof_asphalt", np.clip(rgb, 0, 1), np.clip(rgb, 0, 1), h, 0.9, 1.0)

    # --- marble (lobby floors, dials) -----------------------------------------------------------------------
    vein = 1.0 - np.abs(noise((S, S), 40, "marble", 4) - 0.5) * 2
    vein = np.clip(vein ** 3, 0, 1)
    rgb = np.dstack([0.82 - 0.25 * vein, 0.82 - 0.25 * vein, 0.80 - 0.22 * vein])
    tex("marble", np.clip(rgb, 0, 1), np.clip(rgb, 0, 1), 0.5 + vein * 0.2, 0.30 + 0.2 * vein, 0.5)

    # --- ground: dirt, gravel, ballast, grass, rock ---------------------------------------------------------
    h = noise((S, S), 14, "dirt", 4)
    rgb = np.dstack([0.42 + 0.10 * h, 0.34 + 0.09 * h, 0.24 + 0.07 * h])
    tex("dirt", np.clip(rgb, 0, 1), np.clip(rgb, 0, 1), h * 1.2, 0.93, 1.4)

    rng = _rng("gravel")
    rgb = np.zeros((S, S, 3))
    yy, xx = np.mgrid[0:S, 0:S]
    for k in range(900):
        cx, cy = rng.integers(0, S, 2)
        r = 2 + rng.random() * 4
        d = np.hypot(((xx - cx + S // 2) % S) - S // 2, ((yy - cy + S // 2) % S) - S // 2)
        v = np.clip(0.35 + 0.30 * (1 - d / r) + 0.1 * rng.random(), 0, 1)
        m = d <= r
        rgb[m] = np.c_[v[m], v[m] * 0.95, v[m] * 0.86]
    rgb = np.where(rgb.sum(2)[..., None] == 0, np.dstack([0.32 + 0.08 * noise((S, S), 8, "gravel:f", 3)] * 3), rgb)
    h = np.clip(rgb.mean(2) * 1.5, 0, 1)
    tex("gravel", np.clip(rgb, 0, 1), np.clip(rgb, 0, 1), h, 0.9, 1.6)

    rng = _rng("ballast")
    rgb = np.zeros((S, S, 3)) + 0.20
    for k in range(520):
        cx, cy = rng.integers(0, S, 2)
        r = 4 + rng.random() * 6
        d = np.hypot(((xx - cx + S // 2) % S) - S // 2, ((yy - cy + S // 2) % S) - S // 2)
        v = 0.30 + 0.25 * (1 - d / r) + 0.1 * rng.random()
        m = d <= r
        rgb[m] = np.c_[v[m], v[m] * 0.97, v[m] * 0.92]
    h = np.clip(rgb.mean(2) * 1.6, 0, 1)
    tex("ballast", np.clip(rgb, 0, 1), np.clip(rgb, 0, 1), h, 0.92, 1.8)

    blades = noise((S, S), 3, "grass:blades", 3)
    hue = noise((S, S), 40, "grass:hue", 3)
    rgb = np.dstack([0.20 + 0.10 * blades + 0.03 * hue,
                     0.34 + 0.16 * blades + 0.05 * hue,
                     0.12 + 0.07 * blades])
    tex("grass", np.clip(rgb, 0, 1), np.clip(rgb, 0, 1), blades, 0.94, 1.0)

    h = noise((S, S), 20, "rock", 4) * 0.7 + noise((S, S), 4, "rock:crack", 3) * 0.3
    rgb = np.dstack([0.38 + 0.15 * h, 0.36 + 0.13 * h, 0.32 + 0.11 * h])
    tex("rock", np.clip(rgb, 0, 1), np.clip(rgb, 0, 1), h, 0.9, 1.6)

    # --- drain grate ----------------------------------------------------------------------------------------
    rgb = np.zeros((S, S, 3)) + 0.10
    for k in range(16, S, 32):
        rgb[:, k:k + 18] = 0.025
    h = np.zeros((S, S))
    for k in range(16, S, 32):
        h[:, k:k + 18] = 1
    h += noise((S, S), 3, "drain:rough", 2) * 0.1
    tex("drain", np.clip(rgb, 0, 1), np.clip(rgb, 0, 1), h, 0.55, 2.0)

    # --- tactile paving -------------------------------------------------------------------------------------
    rgb = np.zeros((S, S, 3)) + (0.85, 0.66, 0.12)
    yy, xx = np.mgrid[0:S, 0:S]
    h = np.zeros((S, S))
    for cy in range(32, S, 64):
        for cx in range(32, S, 64):
            d = np.hypot(xx - cx, yy - cy)
            m = d < 20
            h[m] = np.maximum(h[m], np.clip(1 - d[m] / 20, 0, 1))
            rgb[m] = np.array([0.95, 0.78, 0.18]) * (0.75 + 0.25 * (1 - d[m, None] / 20))
    tex("tactile", np.clip(rgb, 0, 1), np.clip(rgb, 0, 1), h, 0.75, 2.0)

    # --- water ----------------------------------------------------------------------------------------------
    wave = (np.sin(xx / S * math.pi * 2 * 5) * 0.35 + np.sin(yy / S * math.pi * 2 * 7 + 1.1) * 0.35
            + noise((S, S), 6, "water", 4) * 0.5)
    save("t_bne_water_ripple_nm.normal.png", normal_map(wave, 2.5), srgb=False)
    foam = noise((S, S), 10, "foam", 4)
    a = np.clip((foam - 0.55) * 3.0, 0, 1)
    save("t_bne_water_foam_b.color.png", np.ones((S, S, 3)) * 0.95, a)
    ramp = np.zeros((1, 256, 4), np.uint8)
    for i in range(256):
        f = i / 255.0
        ramp[0, i] = (int(120 * (1 - f) + 12 * f), int(170 * (1 - f) + 40 * f),
                      int(160 * (1 - f) + 70 * f), 255)
    Image.fromarray(ramp, "RGBA").save(os.path.join(OUT, "t_bne_water_depth_ramp.png"))

    # --- turn arrows (4 x 4 cells; row 0: left, right, through) ----------------------------------------------
    sheet = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(sheet)
    cell = S // 4
    white = (245, 245, 240, 255)

    def arrow(col, kind):
        x0 = col * cell
        cx = x0 + cell // 2
        # shaft from the cell's bottom to near its top
        d.line([(cx, cell - 18), (cx, 38)], fill=white, width=26)
        if kind == "left":
            d.polygon([(cx - 2, 30), (cx - 66, 78), (cx - 18, 88)], fill=white)
        elif kind == "right":
            d.polygon([(cx + 2, 30), (cx + 66, 78), (cx + 18, 88)], fill=white)
        else:
            d.polygon([(cx, 12), (cx - 34, 62), (cx + 34, 62)], fill=white)

    arrow(0, "left")
    arrow(1, "right")
    arrow(2, "through")
    sheet.save(os.path.join(OUT, "t_bne_arrows.png"))

    # --- facade sheets --------------------------------------------------------------------------------------
    # buildings.py draws every window as one quad at WIN_UV of the facade texture (earthfast building_walls;
    # BANDS = 4 rows, TILE_PX_M = 12 m per sheet width); the stock library textures were window sheets. Plain
    # textures here would erase every window, so these keep one window at the exact rect and, for the glazed
    # looks, mullions across each band.
    WIN_RECT = (53, 557, 154, 750)          # x0, y0, x1, y1 (top-left; from buildings.WIN_UV, BANDS=4)
    for cat in ("brick", "plaster", "metal", "stone", "wood", "concrete", "corrugated"):
        BASES[cat] = np.asarray(Image.open(os.path.join(OUT, f"t_bne_{cat}_b.color.png")).convert("RGB"),
                                np.float64) / 255.0

    def facade_sheet(tag, cat, glazed=False):
        im = Image.fromarray((np.clip(BASES[cat], 0, 1) * 255).astype(np.uint8)).resize((1024, 1024), Image.NEAREST)
        im = im.convert("RGB")
        d = ImageDraw.Draw(im)
        if glazed:                           # shopfronts / curtain walls: glass panes with mullions, per band
            for b in range(4):
                y0, y1 = b * 256 + 22, (b + 1) * 256 - 22
                d.rectangle([20, y0, 1004, y1], fill=(38, 52, 62))
                for x in range(20, 1005, 164):
                    d.line([(x, y0), (x, y1)], fill=(176, 176, 170), width=5)
                d.line([(20, (y0 + y1) // 2), (1004, (y0 + y1) // 2)], fill=(176, 176, 170), width=4)
                d.rectangle([20, y1 - 26, 1004, y1], fill=(96, 96, 92))          # spandrel
        else:
            # the window sheet's 4 rows x 5 bays (BANDS = 4, TILE_PX_M = 12 m / 2.4 m bays): the wall quads of
            # each band tile it, the geometry's windows sample the one at WIN_RECT
            for b in range(4):
                for col in range(5):
                    x0, y0 = col * 204 + 53, b * 256 + 45
                    x1, y1 = x0 + 101, y0 + 193
                    d.rectangle([x0 - 7, y0 - 7, x1 + 7, y1 + 7], fill=(212, 210, 202))
                    d.rectangle([x0, y0, x1, y1], fill=(64, 86, 106))
                    d.rectangle([x0 + 3, y0 + 3, x1 - 3, y0 + 28], fill=(196, 196, 190))
                    d.line([((x0 + x1) // 2, y0), ((x0 + x1) // 2, y1)], fill=(212, 210, 202), width=4)
                    d.line([(x0, (y0 + y1) // 2), (x1, (y0 + y1) // 2)], fill=(212, 210, 202), width=3)
        rgb = np.asarray(im, np.float64) / 255.0
        save(f"t_bne_sheet_{tag}_b.color.png", rgb)
        h = noise((1024, 1024), 8, "sheet:" + tag, 3) * 0.25
        save(f"t_bne_sheet_{tag}_nm.normal.png", normal_map(h, 0.8), srgb=False)
        rough = np.full((1024, 1024), 0.85)
        for b in range(4):
            for col in range(5):
                x0, y0 = col * 204 + 53, b * 256 + 45
                rough[y0 + 8:y0 + 185, x0 + 8:x0 + 93] = 0.22
        if glazed:
            for b in range(4):
                rough[b * 256 + 30:(b + 1) * 256 - 30, 28:996] = 0.25
        save(f"t_bne_sheet_{tag}_r.data.png", gray(rough), srgb=False)

    for tag, cat, glazed in (("facade", "plaster", False), ("shop", "metal", True), ("office", "metal", True),
                             ("stucco", "plaster", False), ("brick", "brick", False), ("stone", "stone", False),
                             ("siding", "wood", False), ("panel", "concrete", False),
                             ("metalwall", "corrugated", False)):
        facade_sheet(tag, cat, glazed)

    # --- materials ------------------------------------------------------------------------------------------
    def p(tag, kind):
        ext = {"b": "b.color.dds", "n": "nm.normal.dds", "r": "r.data.dds"}[kind]
        return f"{URL}/t_bne_{tag}_{ext}"

    def material(name, tag=None, factor=(1, 1, 1, 1), translucent=False, cast=False, maps=True, extra=None):
        stage = {"baseColorFactor": list(factor)}
        if maps and tag:
            stage["baseColorMap"] = p(tag, "b")
            stage["normalMap"] = p(tag, "n")
            stage["roughnessMap"] = p(tag, "r")
        if extra:
            stage.update(extra)
        return {name: {"name": name, "mapTo": name, "class": "Material", "version": 1.5,
                       "persistentId": str(uuid.uuid5(uuid.NAMESPACE_URL, "beamng-earth/" + name)),
                       "Stages": [stage, {}, {}, {}], "translucent": bool(translucent), "castShadows": bool(cast),
                       "annotation": "BNE"}}

    roads = {}
    roads.update(material("road_asphalt_2lane", "asphalt"))
    roads.update(material("ECA_road_edge_dirt", "dirt", translucent=True))
    roads.update(material("italy_cobblestone", "cobble"))
    roads.update(material("gutter1", "concrete"))
    roads.update(material("sidewalk_concrete", "concrete"))
    roads.update(material("m_gm_concrete_plain", "concrete"))
    roads.update(material("bne_highway_concrete", "concrete"))
    roads.update(material("curbs", "concrete"))
    roads.update(material("sidewalk1", "concrete"))
    roads.update(material("tactile_indicator", "tactile"))
    roads.update(material("m_metal_grid_drain", "drain"))
    roads.update(material("m_gm_metal_hotspotting", "metal", factor=(0.45, 0.45, 0.45, 1)))
    roads.update(material("road_tramline", "asphalt"))
    roads.update(material("road_gravel", "gravel"))
    roads.update(material("m_dirt_road", "dirt"))
    roads.update(material("bne_dirt_gravel", "gravel"))
    for k in range(1, 5):
        roads.update(material(f"bne_dirt_variation_0{k}", "dirt",
                              factor=[(1.0, 0.98, 0.95, 1), (0.9, 0.86, 0.8, 1), (0.82, 0.80, 0.76, 1),
                                      (0.95, 0.9, 0.82, 1)][k - 1]))
    for name, col in (("line_white", (0.94, 0.94, 0.92, 1)), ("crossing_white", (0.94, 0.94, 0.92, 1)),
                      ("line_dashed_long", (0.94, 0.94, 0.92, 1)), ("line_runway_white", (0.94, 0.94, 0.92, 1)),
                      ("line_yellow", (0.92, 0.72, 0.06, 1)), ("line_runway_yellow", (0.92, 0.72, 0.06, 1))):
        roads.update(material(name, None, factor=col, maps=False, translucent=True))
    roads["roadmarkings1"] = {
        "name": "roadmarkings1", "mapTo": "roadmarkings1", "class": "Material", "version": 1.5,
        "persistentId": str(uuid.uuid5(uuid.NAMESPACE_URL, "beamng-earth/roadmarkings1")),
        "Stages": [{"baseColorMap": f"{URL}/t_bne_arrows.png", "baseColorFactor": [1, 1, 1, 1],
                    "alphaRef": 80}, {}, {}, {}],
        "translucent": True, "translucentZWrite": True, "castShadows": False, "annotation": "BNE"}
    with open(os.path.join(SHAPES, "roads.materials.json"), "w", encoding="utf-8") as f:
        json.dump(roads, f, indent=2)
        f.write("\n")

    # --- rewrite the mod's own material files off the stock library ----------------------------------------
    def category(name, path):
        """The texture set of a material, by its name first (after one run every path is already ours: the original
        stock path no longer tells what the material is), the stock path after."""
        n = name.lower()
        for keys, cat in (
                (("rail_ballast",), "ballast"), (("sleeper",), "concrete"),
                (("rail_steel", "truss_steel", "lm_iron", "wire", "lm_lamp", "lm_dial"), "metal"),
                (("bne_steel",), "metal"), (("bld_office", "bld_shop"), "metal"),
                (("roof_metal", "cladding", "corrugat"), "corrugated"),
                (("roof_slate",), "roof_slate"), (("roof_shingle",), "roof_shingle"),
                (("roof_flat", "roof_asphalt"), "roof_asphalt"),
                (("bridge_asphalt",), "asphalt"),
                (("stone",), "stone"), (("brick",), "brick"),
                (("marble",), "marble"), (("stucco", "plaster", "facade"), "plaster"),
                (("siding", "wood"), "wood"), (("panel", "concrete", "precast", "bridge_concrete",
                                                "highway_concrete", "tunnel_inner"), "concrete"),
                (("grass",), "grass"), (("rock", "scree"), "rock"), (("gravel",), "gravel"),
                (("dirt", "soil"), "dirt"), (("metal", "zinc"), "metal"), (("asphalt",), "asphalt")):
            if any(k in n for k in keys):
                return cat
        low = (name + " " + path).lower()
        for keys, cat in ((("rail_ballast",), "ballast"), (("sleeper",), "concrete"),
                          (("rail_steel", "truss_steel", "steel_", "lm_iron", "wire", "metal_paint"), "metal"),
                          (("corrugat", "cladding", "roof_metal"), "corrugated"),
                          (("bridge_asphalt", "terrain/asphalt", "asphalt_roof"), "roof_asphalt"),
                          (("brick",), "brick"), (("stone",), "stone"), (("slate",), "roof_slate"),
                          (("shingle",), "roof_shingle"), (("marble",), "marble"),
                          (("stucco", "plaster", "facade"), "plaster"), (("weatherboard", "siding", "wood"), "wood"),
                          (("grass",), "grass"), (("rock", "scree"), "rock"), (("gravel",), "gravel"),
                          (("dirt", "soil"), "dirt"), (("concrete", "precast"), "concrete"),
                          (("metal", "zinc"), "metal"), (("asphalt",), "asphalt")):
            if any(k in low for k in keys):
                return cat
        return "concrete"

    SHEETS = {"bne_bld_facade": "facade", "bne_bld_shop": "shop", "bne_bld_office": "office",
              "bne_bld_stucco": "stucco", "bne_bld_brick": "brick", "bne_bld_stone": "stone",
              "bne_bld_siding": "siding", "bne_bld_panel": "panel", "bne_bld_metalwall": "metalwall",
              "bne_bld_sky": "office"}

    def rewrite(path, only=None):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for name, m in data.items():
            if only is not None and name not in only:
                continue
            stages = m.get("Stages") or []
            if not stages:
                continue
            st = stages[0]
            if name in SHEETS:               # a window sheet: buildings.py's window quads sample WIN_UV of it
                tag = SHEETS[name]
                st["baseColorMap"] = f"{URL}/t_bne_sheet_{tag}_b.color.png"
                st["normalMap"] = f"{URL}/t_bne_sheet_{tag}_nm.normal.png"
                st["roughnessMap"] = f"{URL}/t_bne_sheet_{tag}_r.data.png"
            else:
                cat = category(name, str(st))
                if st.get("baseColorMap") or st.get("normalMap") or st.get("roughnessMap"):
                    st["baseColorMap"] = p(cat, "b")
                    st["normalMap"] = p(cat, "n")
                    st["roughnessMap"] = p(cat, "r")
            for k in ("ambientOcclusionMap", "metallicMap", "detailNormalMap", "detailScale", "opacityMap"):
                v = st.get(k)
                if k == "opacityMap" and v and "/levels/beamng_earth/" in str(v):
                    continue                        # (the dirt strips' own fade: their sides into the ground)
                st.pop(k, None)
            em = st.get("emissiveMap")
            if em and "/levels/beamng_earth/" not in str(em):
                st.pop("emissiveMap", None)
            for s2 in stages[1:]:
                for k in list(s2):
                    if k != "baseColorFactor" and k != "roughnessFactor":
                        s2.pop(k, None)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")

    rewrite(os.path.join(SHAPES, "buildings.materials.json"))
    rewrite(os.path.join(SHAPES, "tunnel.materials.json"))
    rewrite(os.path.join(SHAPES, "dirtroad.materials.json"))
    rewrite(os.path.join(SHAPES, "roadside", "median.materials.json"))

    # --- the water textures the mod's sea.lua reads ---------------------------------------------------------
    sea = os.path.join(ROOT, "mod", "beamng_earth", "lua", "ge", "extensions", "beamngEarth", "sea.lua")
    text = open(sea, encoding="utf-8").read()
    text = text.replace("/levels/east_coast_usa/art/water/ripple3.dds", p("water_ripple", "n"))
    text = text.replace("/levels/east_coast_usa/art/water/depthcolor_ramp.png", f"{URL}/t_bne_water_depth_ramp.png")
    text = text.replace("/levels/east_coast_usa/art/water/foam.dds", p("water_foam", "b"))
    text = text.replace("/assets/materials/tileable/water/water_effects/ripple_nm.normal.dds", p("water_ripple", "n"))
    text = text.replace("/assets/materials/tileable/water/depthcolor_ramp/river_depthcolor_ramp_b.png",
                        f"{URL}/t_bne_water_depth_ramp.png")
    text = text.replace("/assets/materials/tileable/water/water_effects/foam_b.color.dds", p("water_foam", "b"))
    with open(sea, "w", encoding="utf-8") as f:
        f.write(text)

    for f in os.listdir(OUT):       # the PNGs were previews the DDS replace: keep the two materials still in PNG
        if f.endswith(".png") and f not in ("t_bne_arrows.png", "t_bne_water_depth_ramp.png"):
            os.remove(os.path.join(OUT, f))
    print(f"wrote texture files and 1 roads material file to {OUT}")
    print("rewrote buildings/tunnel/dirtroad materials and sea.lua's water paths")


if __name__ == "__main__":
    main()
