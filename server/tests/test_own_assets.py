"""The mod's own assets: every texture the world's materials use is generated into the mod (no asset from another
level), and the material names the server's chunks carry all resolve."""
import json
import os
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MOD = ROOT / "mod" / "beamng_earth" / "levels" / "beamng_earth"
SHAPES = MOD / "art" / "shapes" / "beamngEarth"

# the names the server and the mod's extensions look up (roads.py, markings.py, rails.py, sidewalks.py, roads.lua)
REQUIRED = {
    "road_asphalt_2lane", "ECA_road_edge_dirt", "italy_cobblestone", "gutter1", "sidewalk_concrete",
    "m_gm_concrete_plain", "bne_highway_concrete", "curbs", "sidewalk1", "tactile_indicator",
    "m_metal_grid_drain", "m_gm_metal_hotspotting", "road_tramline", "road_gravel", "m_dirt_road",
    "bne_dirt_gravel", "bne_dirt_variation_01", "bne_dirt_variation_02", "bne_dirt_variation_03",
    "bne_dirt_variation_04", "line_white", "line_yellow", "line_dashed_long", "line_runway_white",
    "line_runway_yellow", "crossing_white", "roadmarkings1", "bne_rail_ballast", "bne_rail_sleeper",
    "bne_rail_steel", "bne_bridge_concrete", "bne_bld_facade", "bne_bld_shop", "bne_bld_office",
    "bne_bld_roof_shingle", "bne_bld_roof_slate", "bne_bld_roof_flat", "bne_sidewalk_brick",
    "bne_fuel_white", "bne_fuel_red", "bne_fuel_metal", "bne_fuel_concrete", "bne_pumpjack_metal",
}


class OwnAssetsTest(unittest.TestCase):
    def material_files(self):
        return (list(SHAPES.glob("*.materials.json")) + list((SHAPES / "roadside").glob("*.materials.json"))
                + list((SHAPES / "fuel").glob("*.materials.json")))

    def test_the_core_material_names_exist(self):
        names = set()
        for p in self.material_files():
            with open(p, encoding="utf-8") as fh:
                names |= set(json.load(fh))
        missing = REQUIRED - names
        self.assertFalse(missing, f"material names the world uses are missing: {sorted(missing)}")

    def test_no_asset_from_another_level(self):
        for p in self.material_files():
            text = p.read_text(encoding="utf-8")
            self.assertNotIn('"/assets/', text, f"{p.name} uses stock assets")
            other = re.findall(r'"/levels/(?!beamng_earth/)[A-Za-z0-9_]+', text)
            self.assertFalse(other, f"{p.name} uses another level's paths: {sorted(set(other))[:4]}")
        roads = (ROOT / "mod" / "beamng_earth" / "lua" / "ge" / "extensions" / "beamngEarth" / "roads.lua")
        text = roads.read_text(encoding="utf-8")
        block = text.split("M.MATERIAL_FILES", 1)[1].split("}", 1)[0]
        other = re.findall(r"/levels/(?!beamng_earth/)[A-Za-z0-9_]+", block)
        self.assertFalse(other, f"roads.lua loads another level's materials: {sorted(set(other))}")

    def test_every_texture_exists(self):
        for p in self.material_files():
            for ref in re.findall(r'"(/levels/[^"]+?\.(?:png|dds))"', p.read_text(encoding="utf-8")):
                rel = ref.removeprefix("/levels/beamng_earth/")
                self.assertTrue((MOD / rel).is_file(), f"{p.name}: {ref} is missing")
        sea = (ROOT / "mod" / "beamng_earth" / "lua" / "ge" / "extensions" / "beamngEarth" / "sea.lua")
        for ref in re.findall(r'"(/levels/beamng_earth/[^"]+?\.(?:png|dds))"', sea.read_text(encoding="utf-8")):
            self.assertTrue((MOD / ref.removeprefix("/levels/beamng_earth/")).is_file(), ref)

    def test_the_facade_materials_are_window_sheets(self):
        # buildings.py samples WIN_UV of the facade texture for every window; a plain wall texture erased them
        with open(SHAPES / "buildings.materials.json", encoding="utf-8") as fh:
            mats = json.load(fh)
        for name in ("bne_bld_facade", "bne_bld_shop", "bne_bld_office", "bne_bld_brick", "bne_bld_stone",
                     "bne_bld_stucco", "bne_bld_siding", "bne_bld_panel", "bne_bld_metalwall", "bne_bld_sky"):
            self.assertIn("t_bne_sheet_", mats[name]["Stages"][0]["baseColorMap"], name)


if __name__ == "__main__":
    unittest.main()
