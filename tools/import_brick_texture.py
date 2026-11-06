#!/usr/bin/env python3
"""Import the CC0 brick texture for the mapped garden walls.

    python tools/import_brick_texture.py

Source: ambientCG "Bricks075A" (1K JPG), CC0 (public domain), https://ambientcg.com/view?id=Bricks075A.
Writes t_bne_garden_brick_b.color.dds / _nm.normal.dds / _r.data.dds beside the mod's other generated
textures. The JPG set is 1024x512 and seamless; each map is tiled vertically to the square DDS grid.
"""
import io
import os
import sys
import urllib.request
import zipfile

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from build_bne_assets import _dds, OUT  # noqa: E402

URL = "https://ambientcg.com/get?file=Bricks075A_1K-JPG.zip"
NAMES = {"color": "Bricks075A_1K-JPG_Color.jpg", "normal": "Bricks075A_1K-JPG_NormalGL.jpg",
         "rough": "Bricks075A_1K-JPG_Roughness.jpg"}


def _square(im):
    """The seamless 1024 x 512 map tiled vertically to 1024 x 1024 (the DDS writer is square)."""
    im = im.convert("RGB") if im.mode not in ("RGB", "L") else im
    a = np.asarray(im)
    a = np.concatenate([a, a], axis=0)
    return a.astype(np.float64) / 255.0


def main():
    data = urllib.request.urlopen(urllib.request.Request(URL, headers={"User-Agent": "BeamNG.Earth-build/1.0"}),
                                  timeout=120).read()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        maps = {k: _square(Image.open(io.BytesIO(z.read(v)))) for k, v in NAMES.items()}
    _dds("t_bne_garden_brick_b.color.dds", maps["color"], srgb=True)
    _dds("t_bne_garden_brick_nm.normal.dds", maps["normal"], srgb=False)
    r = maps["rough"].mean(2) if maps["rough"].ndim == 3 else maps["rough"]
    _dds("t_bne_garden_brick_r.data.dds", np.dstack([r, r, r]), srgb=False)
    print("wrote", OUT, sorted(os.path.basename(p) for p in
                               (f"{OUT}/t_bne_garden_brick_b.color.dds",
                                f"{OUT}/t_bne_garden_brick_nm.normal.dds",
                                f"{OUT}/t_bne_garden_brick_r.data.dds")))


if __name__ == "__main__":
    main()
