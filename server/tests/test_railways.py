"""Railway tracks (railways.py): the rail lines taken, the level crossings, the bed's heights and the mesh."""
import math
import os
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import railways  # noqa: E402
import roads  # noqa: E402
from test_roads import FRAME, features  # noqa: E402


def flat(xy):
    return np.full(len(xy), 10.0)


class RailwayTest(unittest.TestCase):
    def test_a_tunnel_bore_fits_regular_trains(self):
        # no scaling the track down for the tunnel: standard gauge, a full ballast bed and a bore wider than a train
        self.assertEqual(railways.gauge("standard_gauge"), 1.435)
        self.assertGreaterEqual(2 * railways.BALLAST_TOP_M, 3.4, "the ballast top carries a full sleeper")
        import roadmesh
        self.assertGreaterEqual(railways.TUNNEL_BORE_W, 6.0)
        self.assertGreater(railways.TUNNEL_BORE_W / 2 + roadmesh.TUNNEL_SHOULDER, 3.2,
                           "a 3.2 m train fits with clearance")

    def test_a_branch_end_rounds_into_the_through_track(self):
        main = features([[(-100.0, 0.0), (100.0, 0.0)]], ["standard_gauge"], subtypes=["rail"], ids=["main"])
        branch = features([[(0.0, -8.0), (50.0, 0.0)]], ["standard_gauge"], subtypes=["rail"], ids=["branch"])
        rails = railways.parts(FRAME, [main, branch], roads.tile_bbox(0, 0), clip=False)
        got = {fid: xy for fid, _c, _k, xy in rails if fid in ("main", "branch")}
        self.assertEqual(set(got), {"main", "branch"})
        self.assertAlmostEqual(float(got["branch"][-1, 0]), 50.0, delta=0.5, msg="joined to the through track")
        self.assertAlmostEqual(float(got["branch"][-1, 1]), 0.0, delta=0.5)
        d = got["branch"][-1, :2] - got["branch"][-2, :2]
        d /= max(float(np.hypot(*d)), 1e-9)
        self.assertGreater(abs(float(d[0])), 0.995, "the switch leaves tangent to the through track")
        np.testing.assert_allclose(got["main"][[0, -1]], main.xy_in(FRAME)[[0, -1]], atol=1e-6, rtol=0,
                                   err_msg="the through track is untouched")

    def test_crossing_tunnels_pass_over_and_under(self):
        x = np.linspace(-100.0, 100.0, 101)
        a = ("a", "standard_gauge", "tunnel", np.c_[x, np.zeros(101)])
        b = ("b", "standard_gauge", "tunnel", np.c_[np.zeros(101), x])
        z = railways.tunnel_floors([a, b], FRAME, lambda xy: np.full(len(xy), 10.0))
        self.assertEqual(set(z), {0, 1})
        self.assertGreater(abs(float(z[0][50] - z[1][50])), 6.0,
                           "two bores at one depth clip; the lower now passes under the upper")

    def test_distant_bridge_of_a_nearby_feature_is_not_profiled(self):
        # One feature crosses the tile, but its last 100 m is a bridge 5 km away.
        f = features([[(-100.0, 100.0), (5000.0, 100.0), (5100.0, 100.0)]],
                     ["standard_gauge"], subtypes=["rail"],
                     flags=[(0, "is_bridge", 5100.0 / 5200.0, 1.0)])
        calls = []

        def ground(xy):
            calls.append(len(xy))
            self.assertLess(float(xy[:, 0].max()), 1000.0,
                            "a distant bridge must not trigger elevation work")
            return flat(xy)

        self.assertIsNotNone(railways.tile_mesh(FRAME, 0, 0, [f], ground))
        self.assertTrue(calls, "the nearby ground track is still built")

    def test_bridge_bbox_overlap_without_track_nearby_needs_no_work(self):
        # The bbox covers the tile, but all three sides of this U are far away.
        f = features([[(-1000.0, -1000.0), (-1000.0, 1000.0),
                       (1000.0, 1000.0), (1000.0, -1000.0)]],
                     ["standard_gauge"], subtypes=["rail"], flags=[(0, "is_bridge")])
        ground = mock.Mock(side_effect=AssertionError("no railway here to sample"))
        with mock.patch.object(railways, "_resample", wraps=railways._resample) as resample:
            self.assertIsNone(railways.tile_mesh(FRAME, 0, 0, [f], ground))
        ground.assert_not_called()
        resample.assert_not_called()

    def test_crossing_bridge_keeps_both_ends_outside_search_window(self):
        f = features([[(-500.0, 100.0), (800.0, 100.0)]],
                     ["standard_gauge"], subtypes=["rail"], flags=[(0, "is_bridge")])
        parts = railways.parts(FRAME, [f], roads.tile_bbox(0, 0), clip=True)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0][2], "bridge")
        np.testing.assert_allclose(parts[0][3][[0, -1]], f.xy_in(FRAME)[[0, -1]], atol=1e-6, rtol=0)
        self.assertIsNotNone(railways.tile_mesh(FRAME, 0, 0, [f], flat))

    def test_bridge_in_context_outside_drawn_tile_is_kept(self):
        f = features([[(300.0, 100.0), (420.0, 100.0)]],
                     ["standard_gauge"], subtypes=["rail"], flags=[(0, "is_bridge")])
        parts = railways.parts(FRAME, [f], roads.tile_bbox(0, 0), clip=True)
        self.assertEqual(len(parts), 1, "the existing reach supplies bridge and approach context")
        np.testing.assert_allclose(parts[0][3][[0, -1]], f.xy_in(FRAME)[[0, -1]], atol=1e-6, rtol=0)

    def test_an_all_bridge_network_is_a_viaduct_over_the_ground(self):
        # London Bridge station: every track bridge-tagged, no ground part to anchor the network; each track was laid
        # at its own DEM height (parallel tracks up to 1.5 m apart, clipping). The whole network is one viaduct level.
        f = features([[(-400.0, 100.0), (100.0, 100.0)], [(100.0, 100.0), (600.0, 100.0)]],
                     ["standard_gauge"] * 2, subtypes=["rail"] * 2,
                     flags=[(0, "is_bridge"), (1, "is_bridge")])
        parts = railways.parts(FRAME, [f], roads.tile_bbox(0, 0), clip=True)
        self.assertTrue(parts and all(p[2] == "bridge" for p in parts))
        z = np.concatenate([b for b, _g in railways.profile(parts, flat)])
        np.testing.assert_allclose(z, 10.0 + railways.VIADUCT_CLEAR_M, atol=1e-6)

    def test_a_short_bridge_meeting_a_ground_track_keeps_its_own_profile(self):
        f = features([[(-100.0, 100.0), (-50.0, 100.0)], [(-50.0, 100.0), (500.0, 100.0)]],
                     ["standard_gauge"] * 2, subtypes=["rail"] * 2, flags=[(0, "is_bridge")])
        parts = railways.parts(FRAME, [f], roads.tile_bbox(0, 0), clip=True)
        z = np.concatenate([b for b, _g in railways.profile(parts, flat)])
        np.testing.assert_allclose(z, 10.0, atol=1e-6, err_msg="a short bridge keeps the ground track's profile")

    def test_a_long_bridge_run_is_raised_even_at_a_ground_anchor(self):
        # Abbey Street / London Bridge: long bridge-tagged runs connected to a ground track sat at street level
        f = features([[(-500.0, 100.0), (100.0, 100.0)], [(100.0, 100.0), (400.0, 100.0)]],
                     ["standard_gauge"] * 2, subtypes=["rail"] * 2, levels=[(0, 1.0)],
                     flags=[(0, "is_bridge")])
        levels = {}
        parts = railways.parts(FRAME, [f], roads.tile_bbox(0, 0), clip=True, levels=levels)
        prof = railways.profile(parts, flat, levels=levels)
        br = [b for (fid, cls, kind, xy), (b, _g) in zip(parts, prof) if kind == "bridge"]
        gr = [b for (fid, cls, kind, xy), (b, _g) in zip(parts, prof) if kind == "ground"]
        np.testing.assert_allclose(np.concatenate(br), 10.0 + railways.VIADUCT_CLEAR_M, atol=1e-6)
        np.testing.assert_allclose(np.concatenate(gr), 10.0, atol=1e-6)

    def test_a_light_rail_viaduct_beside_a_ground_line_is_raised(self):
        # the DLR at Limehouse: bridge=viaduct, layer 1, beside c2c track on the ground; it sat on the street
        f = features([[(-500.0, 100.0), (100.0, 100.0)], [(100.0, 100.0), (400.0, 100.0)]],
                     ["light_rail", "standard_gauge"], subtypes=["rail"] * 2,
                     levels=[(0, 1.0), (1, 0.0)], flags=[(0, "is_bridge")])
        parts = railways.parts(FRAME, [f], roads.tile_bbox(0, 0), clip=False)
        prof = railways.profile(parts, flat)
        lr = [b for (fid, cls, kind, xy), (b, _g) in zip(parts, prof) if cls == "light_rail"]
        gr = [b for (fid, cls, kind, xy), (b, _g) in zip(parts, prof) if cls == "standard_gauge"]
        np.testing.assert_allclose(np.concatenate(lr), 10.0 + railways.VIADUCT_CLEAR_M, atol=1e-6)
        np.testing.assert_allclose(np.concatenate(gr), 10.0, atol=1e-6, err_msg="the ground track stays")

    def test_a_flat_city_rail_tunnel_is_laid_below_the_ground(self):
        # the Underground under a flat street: the lower hull was the street itself, so the tube stood at grade
        f = features([[(-1200.0, 100.0), (1200.0, 100.0)]], ["standard_gauge"], subtypes=["rail"],
                     levels=[(0, -4.0)], flags=[(0, "is_tunnel")])
        levels = {}
        parts = railways.parts(FRAME, [f], roads.tile_bbox(0, 0), clip=False, levels=levels)
        self.assertEqual(levels.get(parts[0][0]), -4.0)
        z = railways.tunnel_floors(parts, FRAME, flat, levels=levels)[0]
        self.assertGreater(10.0 - float(np.median(z)), 10.0)
        self.assertLessEqual(float(np.max(z)), 10.0 + 1e-6, "no track over the street")

    def test_parallel_bridge_decks_see_each_other(self):
        f = features([[(-200.0, 100.0), (200.0, 100.0)], [(-200.0, 104.0), (200.0, 104.0)]],
                     ["standard_gauge"] * 2, subtypes=["rail"] * 2,
                     flags=[(0, "is_bridge"), (1, "is_bridge")], ids=["a", "b"])
        parts = railways.parts(FRAME, [f], roads.tile_bbox(0, 0), clip=True)
        gaps = railways._neighbour_gaps(parts)
        n = next(i for i, p in enumerate(parts) if p[0] == "a")
        gl, gr = gaps[n]
        self.assertAlmostEqual(float(np.median(gl[np.isfinite(gl)])), 4.0, delta=0.02,
                               msg="the other track on the left")
        self.assertFalse(np.isfinite(gr).any(), "nothing on the right")
        m = np.isfinite(gl)
        np.testing.assert_allclose(gl[m], np.median(gl[m]), atol=0.02)

    def test_bridge_profile_agrees_across_adjacent_tiles(self):
        f = features([[(-400.0, 100.0), (100.0, 100.0)],
                      [(100.0, 100.0), (450.0, 100.0)],
                      [(450.0, 100.0), (900.0, 100.0)]],
                     ["standard_gauge"] * 3, subtypes=["rail"] * 3, flags=[(1, "is_bridge")])
        profiles = []
        for tx in (0, 1):
            parts = railways.parts(FRAME, [f], roads.tile_bbox(tx, 0), clip=True)
            bridge = next(i for i, part in enumerate(parts) if part[2] == "bridge")
            bed, _ = railways.profile(parts, flat)[bridge]
            profiles.append((parts[bridge][3], bed))
        np.testing.assert_allclose(profiles[0][0], profiles[1][0], atol=1e-6, rtol=0)
        np.testing.assert_allclose(profiles[0][1], profiles[1][1], atol=1e-6, rtol=0)

    def test_empty_tile_does_not_sample_ground(self):
        ground = mock.Mock(side_effect=AssertionError("empty tile"))
        self.assertIsNone(railways.tile_mesh(FRAME, 0, 0, [], ground))
        ground.assert_not_called()

    def test_track_mesh_along_a_line(self):
        f = features([[(-50.0, 100.0), (300.0, 100.0)]], ["standard_gauge"], subtypes=["rail"])
        mesh = railways.tile_mesh(FRAME, 0, 0, [f], flat, "fi")
        self.assertIsNotNone(mesh)
        P, T, UV, mats, M = mesh
        self.assertEqual(mats, [railways.BALLAST, railways.CONCRETE, railways.STEEL, railways.SLEEPER])
        self.assertLess(len(P), railways.VERTS_MAX)
        self.assertEqual(int(T.max()) + 1, len(P))
        steel = P[np.unique(T[M == 2])]
        self.assertAlmostEqual(float(steel[:, 2].max()),
                               10.0 + railways.BALLAST_H - railways.SLEEPER_SINK + railways.SLEEPER_H + railways.RAIL_H, 3)
        heads = np.unique(np.round(steel[:, 1] + 0.0, 2))      # (the tile's origin at y 0)
        gap = heads.max() - heads.min() - 2 * railways.RAIL_HEAD    # between the rails' inner faces
        self.assertAlmostEqual(gap, railways.FI_GAUGE_M, delta=0.011, msg="Finnish broad gauge")
        n_sleepers = (M == 3).sum() // 10
        self.assertGreater(n_sleepers, 256 / railways.SLEEPER_EVERY * 0.95)
        self.assertGreaterEqual(P[:, 0].min(), -roads.OVERLAP - railways.STEP_M - 3.0)

    def test_lean_collider_keeps_the_rails_not_the_sleepers(self):
        # (PR #41) sleepers are most of the track's triangles; the rails stay colliding (their railheads are what a
        # train's wheels ride and a car bumps over), so the collider is the bed and the rails, not the sleepers
        f = features([[(-50.0, 100.0), (300.0, 100.0)]], ["standard_gauge"], subtypes=["rail"])
        mesh = railways.tile_mesh(FRAME, 0, 0, [f], flat)
        P, T, UV, mats, M = mesh
        cP, cT, cUV, cmats, cM = railways.collision_mesh(mesh)
        got = {mats[k] for k in np.unique(cM)}
        self.assertTrue(got <= {railways.BALLAST, railways.CONCRETE, railways.STEEL})
        self.assertIn(railways.STEEL, got, "the rails collide")
        self.assertNotIn(railways.SLEEPER, got, "the sleepers stay drawn only")
        self.assertLess(len(cT), 0.5 * len(T), "still lean: no sleepers")
        bed = railways.collision_mesh(mesh, rails=False)
        self.assertEqual(len(bed[1]), int(np.isin(M, [mats.index(railways.BALLAST)]).sum()),
                         "BNGE_RAIL_COLLISION=0: the bed alone")
        np.testing.assert_array_equal(bed[1], T[M == mats.index(railways.BALLAST)])
        self.assertIs(cP, P)
        self.assertIsNone(railways.collision_mesh(None))
        rails_only = (P, T[M == 2], UV, mats, M[M == 2])
        c = railways.collision_mesh(rails_only)
        self.assertIsNotNone(c, "the rails alone are a collider now")
        self.assertEqual({mats[k] for k in np.unique(c[4])}, {railways.STEEL})
        with tempfile.TemporaryDirectory() as tmp:      # written by the road tile beside the drawn track
            path, _n = roads.write_tile(tmp, "/v", 1, 0, 0, [], rail_mesh=mesh)
            import json
            with open(os.path.join(tmp, os.path.basename(path))) as fh:
                data = json.load(fh)
            self.assertEqual(data["railcol"]["shape"], "/v/railcol_1_0_0.cdae")
            self.assertTrue(os.path.getsize(os.path.join(tmp, "railcol_1_0_0.cdae"))
                            < 0.5 * os.path.getsize(os.path.join(tmp, "rail_1_0_0.cdae")))
        self.assertEqual(roads.shape_files(1, 0, 0)["railcol"], "railcol_1_0_0.cdae")

    def test_roads_and_trams_are_not_tracks(self):
        f = features([[(0.0, 50.0), (200.0, 50.0)], [(0.0, 80.0), (200.0, 80.0)]], ["residential", "tram"],
                     subtypes=["road", "rail"])
        self.assertIsNone(railways.tile_mesh(FRAME, 0, 0, [f], flat))

    def test_level_crossing_cuts_the_track(self):
        f = features([[(0.0, 100.0), (250.0, 100.0)], [(120.0, 0.0), (120.0, 250.0)]], ["standard_gauge", "secondary"],
                     subtypes=["rail", "road"])
        P, T, UV, mats, M = railways.tile_mesh(FRAME, 0, 0, [f], flat)
        x = P[np.unique(T)][:, 0]
        hw = roads.road_specs(f)[1].width / 2
        self.assertFalse(((x > 120 - hw) & (x < 120 + hw)).any(), "nothing on the road")

    def test_short_tunnel_under_a_road_is_track_and_a_long_one_is_a_tunnel(self):
        """The line under a road bridge tagged is_tunnel for 12 m (OSM's habit) is track on the ground; a long tunnel
        is a part of its own (a bore, M5); an abandoned line beside it is drawn worn (Joensuu, 2026-10-25; the user,
        2026-10-27: abandoned lines shown, grown over)."""
        f = features([[(0.0, 100.0), (250.0, 100.0)], [(0.0, 110.0), (250.0, 110.0)], [(0.0, 150.0), (250.0, 150.0)]],
                     ["standard_gauge", "unknown", "standard_gauge"], subtypes=["rail"] * 3,
                     flags=[(0, "is_tunnel", 0.46, 0.51), (1, "is_abandoned"), (2, "is_tunnel", 0.2, 0.8)])
        worn = {}
        rails = railways.parts(FRAME, [f], roads.tile_bbox(0, 0), worn=worn)
        got = [(fid, kind, float(np.ptp(xy[:, 0]))) for fid, _c, kind, xy in rails]
        self.assertEqual(sorted(g[0] for g in got), ["id000", "id001", "id002", "id002", "id002"])
        self.assertEqual(worn, {"id001": 2}, "the abandoned line worn")
        self.assertEqual([c for fid, c, *_ in rails if fid == "id001"], ["standard_gauge"])
        self.assertAlmostEqual([g[2] for g in got if g[0] == "id000"][0], 250.0, 0, "track through the short tunnel")
        long_parts = [(k, L) for fid, k, L in got if fid == "id002"]
        self.assertEqual(sorted(k for k, _L in long_parts), ["ground", "ground", "tunnel"])
        self.assertAlmostEqual(max(L for k, L in long_parts if k == "tunnel"), 150.0, delta=1.0,
                               msg="the long tunnel kept as a track of its own")

    def test_a_long_tunnel_is_a_track_in_a_bore(self):
        """A mapped rail tunnel keeps its track: the bed is the lower hull of the hill (inside it, not over it), a
        tube is built round it, and the terrain is cut only at its mouths (not over the hill)."""
        f = features([[(-50.0, 100.0), (300.0, 100.0)]], ["standard_gauge"], subtypes=["rail"],
                     flags=[(0, "is_tunnel", 0.25, 0.75)])

        def elev(la, lo):
            e, _n, _ = FRAME.geodetic_to_enu(np.asarray(la, np.float64), np.asarray(lo, np.float64), 0.0)
            return 10.0 + 30.0 * np.exp(-((np.asarray(e) - 125.0) / 60.0) ** 2)
        ground = railways.point_ground(FRAME, elev)
        tube = []
        mesh = railways.tile_mesh(FRAME, 0, 0, [f], ground, tube_out=tube)
        self.assertIsNotNone(mesh)
        self.assertTrue(tube, "a bore is made")
        self.assertTrue(any(p[0] or p[1] for p in (c["portal"] for c in tube)), "a mouth at a portal")
        for c in tube:
            z = np.asarray(c["n"], np.float64)[:, 2]
            self.assertLess(float(z.max()), 30.0, "the tube's floor is inside the hill, not over it")
        P, T, UV, mats, M = mesh
        steel = P[np.unique(T[M == mats.index(railways.STEEL)])]
        self.assertLess(float(steel[:, 2].max()), 30.0, "the track is inside the hill")
        import roadmesh
        bore = roadmesh.tunnel(tube, (0.0, 0.0, 0.0))
        self.assertIsNotNone(bore, "the tube mesh")
        # the terrain holes: the mouths, not the ground over the vault
        n, square, cx, cy = 192, 1.0, 128.0, 128.0
        gx = cx - n * square / 2 + np.arange(n) * square
        X, Y = np.meshgrid(gx, gx)
        U = 10.0 + 30.0 * np.exp(-((X - 125.0) / 60.0) ** 2)
        keep = {"u": U}
        mask = railways.block_tunnel_holes(FRAME, [f], keep, cx, cy, square, ground)
        self.assertIsNotNone(mask)
        iy = lambda y: int(round(y - (cy - n * square / 2)))     # noqa: E731
        ix = lambda x: int(round(x - (cx - n * square / 2)))     # noqa: E731
        self.assertTrue(mask[iy(100.0), ix(37.5)], "the west mouth is cut")
        self.assertTrue(mask[iy(100.0), ix(212.5)], "the east mouth is cut")
        self.assertFalse(mask[iy(100.0), ix(125.0)], "the hill over the vault stays")
        with tempfile.TemporaryDirectory() as tmp:      # the road tile writes the tube beside the track
            import json
            path, _n = roads.write_tile(tmp, "/v", 1, 0, 0, [], rail_mesh=mesh, rail_tunnel=bore, rail_tube=tube)
            with open(os.path.join(tmp, os.path.basename(path))) as fh:
                data = json.load(fh)
            self.assertEqual(data["railtunnel"]["shape"], "/v/railtunnel_1_0_0.cdae")
            self.assertTrue(data.get("sound"), "the tube's sound space")

    def test_an_undersea_tunnel_is_laid_under_the_seabed(self):
        """The Channel: the bed is TUNNEL_DEPTH_M under the water-covered ground, faded in from the coasts; the hull of
        dry ground stays at the ground."""
        xy = np.c_[np.arange(0.0, 12001.0, 2.0), np.full(6001, 0.0)]
        seabed = np.full(len(xy), -30.0)
        dry = railways._tunnel_floor(xy, seabed)
        self.assertAlmostEqual(float(dry[3000]), -30.0, delta=0.1)
        wet = np.ones(len(xy), bool)
        deep = railways._tunnel_floor(xy, seabed, wet)
        self.assertAlmostEqual(float(deep[3000]), -30.0 - railways.TUNNEL_DEPTH_M, delta=0.5)
        self.assertGreater(float(deep[0]), float(deep[3000]), "eased in at the coast, no step")
        ramp = np.diff(deep) / np.diff(xy[:, 0])
        self.assertLessEqual(float(np.abs(ramp).max()), 0.02, "at a main line's grade")

    def test_a_resampled_tunnel_hull_takes_the_wet_fade(self):
        """roads.tunnel_sections returns the lower hull, often fewer samples than the line: a wet tunnel under
        London's docks read 3001 samples and a 700-sample hull (Roupell Street, 2026-11-06)."""
        xy = np.c_[np.linspace(0.0, 3000.0, 3001), np.zeros(3001)]
        seabed = np.full(len(xy), -20.0)
        with mock.patch.object(roads, "tunnel_sections",
                               return_value=(np.full(700, -20.0), np.ones(700, np.int64))):
            z = railways._tunnel_floor(xy, seabed, np.ones(len(xy), bool))
        self.assertEqual(len(z), 700)
        self.assertTrue(np.isfinite(z).all())
        self.assertLess(float(z[350]), -20.0)

    def test_a_station_platform_is_a_slab_on_the_nearest_track(self):
        f = features([[(0, 0), (24, 0), (24, 4), (0, 4), (0, 0)]], ["platform"])
        f.attrs["class"] = np.array(["platform"], object)
        rails = [("r", "light_rail", "ground", np.array([[0.0, 10.0], [44.0, 10.0]]))]
        prof = [(np.array([10.0, 10.0]), None)]
        mesh = railways._Mesh((0, 0, 0))
        mi = mesh.mat(railways.CONCRETE)
        railways._platforms(mesh, FRAME, (-50.0, -50.0, 60.0, 60.0), lambda key: f, rails, prof, mi)
        out = mesh.result()
        self.assertIsNotNone(out)
        P = out[0]
        self.assertGreater(len(P), 0)
        self.assertAlmostEqual(float(P[:, 2].max()), 10.0 + railways.PLATFORM_H, delta=0.05)

    def test_osm_tunnel_tag_makes_a_tunnel_without_an_overture_flag(self):
        """The rail shard's tunnel=yes (the Channel Tunnel, the Overground dives) where Overture has no range."""
        f = features([[(0.0, 100.0), (300.0, 100.0)]], ["standard_gauge"], subtypes=["rail"])
        f.attrs["osm_tunnel"] = np.array(["yes"], object)
        self.assertEqual([k for _f, _c, k, _xy in railways.parts(FRAME, [f], roads.tile_bbox(0, 0))], ["tunnel"])

    def test_a_subway_is_a_deep_bore_without_a_tunnel_flag(self):
        """railway=subway has no surface expression in Overture: it is a tunnel its whole length, SUBWAY_DEPTH_M under
        the street when the line is long (the Underground; the user's example)."""
        f = features([[(0.0, 100.0), (2000.0, 100.0)]], ["standard_gauge"], subtypes=["rail"])
        f.attrs["osm_railway"] = np.array(["subway"])
        rails = railways.parts(FRAME, [f], (0.0, 0.0, 256.0, 256.0))
        self.assertEqual([k for _f, _c, k, _xy in rails], ["tunnel"])
        self.assertEqual({c for _f, c, _k, _xy in rails}, {"subway"})
        xy = np.c_[np.arange(0.0, 2001.0, 2.0), np.full(1001, 100.0)]
        floor = railways._tunnel_floor(xy, np.full(len(xy), 10.0), depth=railways.SUBWAY_DEPTH_M,
                                      ramp=railways.SUBWAY_RAMP_M)
        self.assertAlmostEqual(float(floor[500]), 10.0 - railways.SUBWAY_DEPTH_M, delta=0.5, msg="under the street")
        self.assertGreater(float(floor[0]), float(floor[500]), "rising to its end")

    def test_a_joint_between_tunnel_parts_is_not_a_portal(self):
        """Overture splits a long tunnel at a junction: the two parts' meeting ends are a joint inside the hill, not
        two mouths with a portal face and a hole."""
        xy = np.c_[np.arange(0.0, 102.0, 2.0), np.zeros(51)]
        rails = [("a", "standard_gauge", "tunnel", xy[:26]), ("b", "standard_gauge", "tunnel", xy[25:])]
        ports = railways._tunnel_portals(rails)
        self.assertEqual(ports[0], (True, False))
        self.assertEqual(ports[1], (False, True))

    def test_level_crossing_rails_meet_the_road_on_gentle_ramps(self):
        """The rails' top is flush with the road where the track crosses it (they stood 0.6 m over it at its edge), and
        the track ramps back to its own height at 1.7 %."""
        s = np.arange(0.0, 200.0, 2.0)
        zb = np.full(len(s), 10.0)
        blk = (s >= 96) & (s <= 104)
        z = railways._flush(s, zb, blk, np.full(int(blk.sum()), 10.0))
        top = z + railways.RAIL_TOP_M
        np.testing.assert_allclose(top[blk], 10.0 + railways.FLUSH_LIFT_M)
        self.assertLessEqual(float(np.abs(np.diff(z) / np.diff(s)).max()), railways.FLUSH_GRADE + 1e-9)
        self.assertEqual(float(z[0]), 10.0, "its own height away from the road")
        P, T, UV, mats, M = railways.tile_mesh(FRAME, 0, 0, [features([[(0.0, 100.0), (250.0, 100.0)],
                                                                       [(120.0, 0.0), (120.0, 250.0)]],
                                                                      ["standard_gauge", "secondary"],
                                                                      subtypes=["rail", "road"])], flat)
        rails = P[np.unique(T[M == mats.index(railways.STEEL)])]
        edge = rails[np.abs(rails[:, 0] - 120.0) < 8.0]
        self.assertTrue(len(edge))
        self.assertLess(float(edge[:, 2].max()), flat(edge[:1, :2])[0] + railways.FLUSH_LIFT_M + 0.08, "flush")

    def test_level_crossing_kits_face_each_approach(self):
        """A secondary road's crossing gets a gate at each approach, on the kerb side of the traffic before the track,
        facing it; a residential street's a cross; an abandoned line's none."""
        f = features([[(0.0, 100.0), (250.0, 100.0)], [(120.0, 0.0), (120.0, 250.0)], [(40.0, 0.0), (40.0, 250.0)],
                      [(0.0, 180.0), (250.0, 180.0)]],
                     ["standard_gauge", "secondary", "residential", "unknown"], subtypes=["rail", "road", "road", "rail"],
                     flags=[(3, "is_abandoned")])
        rows = railways.crossing_props(FRAME, 0, 0, [f], lambda xy: np.full(len(xy), 10.0))
        gates = [r for r in rows if r[0].startswith("rrgate")]
        crosses = [r for r in rows if r[0].startswith("rrcross")]
        self.assertEqual(len(gates), 2, rows)
        self.assertEqual(len(crosses), 2, rows)
        for g, x, y, z, yaw, *_ in gates:
            self.assertGreater(abs(y - 100.0), 3.0, "before the track")
            self.assertLess(abs(y - 100.0), 8.0)
            # (the kerb side: right of the traffic heading for the track; its local +x points there)
            travel = np.array([0.0, 1.0 if y < 100.0 else -1.0])
            right = np.array([travel[1], -travel[0]])
            self.assertGreater((x - 120.0) * right[0], 0.0, "on the right of the traffic")
            ux = np.array([math.cos(math.radians(yaw)), math.sin(math.radians(yaw))])
            np.testing.assert_allclose(ux, right, atol=1e-6)
            self.assertEqual(z, 10.0)
        left = railways.crossing_props(FRAME, 0, 0, [f], lambda xy: np.full(len(xy), 10.0), left_hand=True)
        self.assertTrue(all(r[0].endswith("_l") for r in left if r[0].startswith("rrgate")))

    def test_light_rail_crossings_only_with_data(self):
        f = features([[(0.0, 100.0), (250.0, 100.0)], [(120.0, 0.0), (120.0, 250.0)]], ["light_rail", "secondary"],
                     subtypes=["rail", "road"])
        ground = lambda xy: np.zeros(len(xy))     # noqa: E731
        self.assertEqual(railways.crossing_props(FRAME, 0, 0, [f], ground), [], "a street's light rail: none")
        rows = railways.crossing_props(FRAME, 0, 0, [f], ground, kits=lambda p, c: "gate")
        self.assertEqual(len(rows), 2, "the inventory says gates")

    def test_osm_railway_tags_decide_when_known(self):
        """The weekly rail file's OSM values (railtags.py): proposed out whatever Overture's class, disused worn, a
        gauge in mm; a crossing node's devices."""
        f = features([[(0.0, 100.0), (250.0, 100.0)], [(0.0, 120.0), (250.0, 120.0)], [(0.0, 140.0), (250.0, 140.0)]],
                     ["standard_gauge", "standard_gauge", "unknown"], subtypes=["rail"] * 3)
        f.attrs["osm_railway"] = np.array(["proposed", "disused", "rail"], object)
        f.attrs["osm_gauge"] = np.array([None, None, "1520"], object)
        self.assertIsNone(railways.drawn(f, 0))
        self.assertEqual(railways.drawn(f, 1), ("standard_gauge", 1))
        self.assertEqual(railways.drawn(f, 2), ("standard_gauge", 0))
        self.assertAlmostEqual(railways.osm_gauge(f, 2), 1.52)
        self.assertEqual(railways.osm_kit_of({"railway": "level_crossing", "crossing:barrier": "half"}), "gate")
        self.assertEqual(railways.osm_kit_of({"railway": "level_crossing", "crossing:light": "yes"}), "flash")
        self.assertEqual(railways.osm_kit_of({"crossing:barrier": "no", "crossing:light": "no"}), "cross")
        self.assertIsNone(railways.osm_kit_of({"railway": "level_crossing"}), "no word: the road class's")

    def test_proposed_and_unbuilt_lines_are_not_drawn(self):
        """Overture's "unknown" rail with no abandoned or disused flag is OpenStreetMap's railway=proposed or
        construction (Link Union Station across US-101, 2026-10-27); a line under construction neither."""
        f = features([[(0.0, 100.0), (250.0, 100.0)], [(0.0, 120.0), (250.0, 120.0)], [(0.0, 140.0), (250.0, 140.0)],
                      [(0.0, 160.0), (250.0, 160.0)]],
                     ["unknown", "standard_gauge", "unknown", "standard_gauge"], subtypes=["rail"] * 4,
                     flags=[(1, "is_under_construction"), (2, "is_disused"), (3, "is_disused")])
        # Even if OSM tags the way as "rail", an under-construction line must not be drawn
        f.attrs["osm_railway"] = np.array([None, "rail", "disused", "disused"], object)
        worn = {}
        got = sorted({fid for fid, *_ in railways.parts(FRAME, [f], roads.tile_bbox(0, 0), worn=worn)})
        self.assertEqual(got, ["id002", "id003"])
        self.assertEqual(worn, {"id002": 1, "id003": 1})

    def test_abandoned_line_loses_some_sleepers_the_same_in_every_tile(self):
        xy = np.c_[np.arange(0.0, 60.0, 2.0), np.zeros(30)]
        s = np.r_[0.0, np.cumsum(np.hypot(*np.diff(xy, axis=0).T))]
        u = np.tile([0.0, 1.0], (30, 1))
        top = np.zeros(30)
        full = railways._sleepers(s, xy, u, top, 0, 29)[0]
        some = railways._sleepers(s, xy, u, top, 0, 29, railways.SLEEPERS_GONE[2])[0]
        part = railways._sleepers(s, xy, u, top, 10, 29, railways.SLEEPERS_GONE[2])[0]
        self.assertTrue(0.55 * len(full) < len(some) < 0.85 * len(full), (len(some), len(full)))
        np.testing.assert_allclose(some[some[:, 0] >= s[10]], part, err_msg="a tile's run keeps the same ones")

    def test_short_piece_on_its_own_is_left_out(self):
        f = features([[(0.0, 100.0), (120.0, 100.0)], [(120.0, 100.0), (250.0, 100.0)], [(50.0, 130.0), (62.0, 130.0)]],
                     ["standard_gauge"] * 3, subtypes=["rail"] * 3)
        got = sorted(fid for fid, *_ in railways.parts(FRAME, [f], roads.tile_bbox(0, 0)))
        self.assertEqual(got, ["id000", "id001"], "the 12 m stub goes, two short joined pieces of a line stay")

    def test_road_high_over_the_track_does_not_cut_it(self):
        f = features([[(0.0, 100.0), (250.0, 100.0)], [(120.0, 0.0), (120.0, 250.0)]], ["standard_gauge", "secondary"],
                     subtypes=["rail", "road"])

        def cutting(xy):            # the track 7 m down a cutting, the road on its own level over it
            return np.where(np.abs(xy[:, 1] - 100.0) < 12.0, 10.0, 17.0)
        P, T, UV, mats, M = railways.tile_mesh(FRAME, 0, 0, [f], cutting)
        x = P[np.unique(T[M == 2])][:, 0]
        self.assertTrue(((x > 115) & (x < 125)).any(), "the rails go on under the road")

    def test_bed_smooths_bumps_but_follows_hills(self):
        s = np.arange(0.0, 400.0, railways.STEP_M)
        z = 0.05 * s + 0.4 * np.sin(s / 3.0)
        zb = railways.bed_heights(z)
        mid = slice(20, -20)
        self.assertLess(np.abs(zb - 0.05 * s)[20:-20].max(), 0.1, "the bumps gone")
        self.assertLess(np.abs(zb - 0.05 * s).max(), 0.3, "the grade kept to the ends")

    def _bed(self, f, ground):
        rails = railways.parts(FRAME, [f], roads.tile_bbox(0, 0))
        prof = railways.profile(rails, ground)
        return rails, prof

    def _line(self, ground):
        f = features([[(-400.0, 100.0), (100.0, 100.0)], [(100.0, 100.0), (150.0, 100.0)],
                      [(150.0, 100.0), (640.0, 100.0)]], ["standard_gauge"] * 3, subtypes=["rail"] * 3,
                     flags=[(1, "is_bridge")])
        rails, prof = self._bed(f, ground)
        pts = np.concatenate([xy for *_, xy in rails])
        z = np.concatenate([zb for zb, _ in prof])
        o = np.argsort(pts[:, 0])
        return f, pts[o, 0], z[o]

    def test_bridge_over_a_dip_keeps_the_line_level(self):
        """A short bridge over a stream or an underpass road (the ground 3 m down under it): the deck straight at the
        line's height, no ramps (a 6 m clearance stood an 8 m culvert's track 6.8 m up on 4.5 m ramps, 2026-10-25)."""
        def dip(xy):
            return np.where((xy[:, 0] > 112) & (xy[:, 0] < 138), 7.0, 10.0)
        f, x, z = self._line(dip)
        self.assertLess(np.abs(z - 10.0).max(), 0.05, "on the ground's level everywhere, the bridge too")
        P, T, UV, mats, M = railways.tile_mesh(FRAME, 0, 0, [f], dip)
        piers = P[np.unique(T[M == 1])]
        self.assertTrue(((piers[:, 2] < 7.0) & (piers[:, 0] > 100) & (piers[:, 0] < 150)).any(),
                        "a pier down to the stream bed")

    def test_bridge_ends_down_a_cutting_take_the_tracks_level(self):
        """The Abbey Line over the M1 near Bricket Wood (the user's screenshot, 2026-10-31): the track level on both
        sides, the mapped bridge's ends part way down the motorway cutting's slopes. The deck at the track's level, not
        laid between those low ends (it blocked the motorway)."""
        def cutting(xy):            # the track at 10 m, the cutting 6 m deep: its floor 50 m wide, its slopes 15 m
            return 10.0 - 6.0 * np.clip((40.0 - np.abs(xy[:, 0] - 125.0)) / 15.0, 0.0, 1.0)
        f, x, z = self._line(cutting)                       # (the bridge from 100 to 150: its ends at 5 m)
        deck = (x >= 100) & (x <= 150)
        self.assertLess(cutting(np.array([[100.0, 100.0]]))[0], 5.5, "(the mapped ends down the slopes)")
        self.assertGreater(float(z[deck].min()), 10.0 - railways.GRADE * railways.APPROACH_REACH_M, "the deck up")
        self.assertLess(float(z.max()), 10.0 + 0.05, "not over the track's own level")
        dz = np.abs(np.diff(z)) / np.maximum(np.diff(x), 1e-9)
        self.assertLessEqual(dz[np.diff(x) > 0.5].max(), railways.GRADE + 1e-6, "no step at the deck's ends")

    def test_a_hillside_on_from_the_bridge_does_not_raise_it(self):
        """The ground rising on from a bridge's ends without levelling out (the line climbs a hillside there): no
        level the track holds, the deck stays at its ends' ground."""
        def valley(xy):             # 8 % slopes up from the bridge's ends, a dip under it
            x = xy[:, 0]
            return np.where(x < 100, 10.0 + 0.08 * (100 - x), np.where(x > 150, 10.0 + 0.08 * (x - 150), 7.0))
        self.assertEqual(railways.approach_levels(np.array([[100.0, 100.0], [150.0, 100.0]]), valley), (None, None))

    def test_piers_stand_beside_a_road_under_the_bridge_on_the_ground(self):
        """A rail bridge over a road in a 5 m dip: no pier in the road's lanes, every pier's foot under the ground at
        all its corners (one stood in the lanes, one hung in the air, 2026-10-25)."""
        f = features([[(-300.0, 100.0), (60.0, 100.0)], [(60.0, 100.0), (190.0, 100.0)],
                      [(190.0, 100.0), (500.0, 100.0)], [(125.0, -100.0), (125.0, 300.0)]],
                     ["standard_gauge"] * 3 + ["secondary"], subtypes=["rail"] * 3 + ["road"], flags=[(1, "is_bridge")])

        def dip(xy):                # the ground falling 5 m to the road over 40 m either side of it
            return 10.0 - 5.0 * np.clip(1.0 - np.abs(xy[:, 0] - 125.0) / 40.0, 0.0, 1.0)
        P, T, UV, mats, M = railways.tile_mesh(FRAME, 0, 0, [f], dip)
        con = P[np.unique(T[M == 1])]
        low = con[con[:, 2] < 8.5]          # the piers' feet (the deck's underside: 10 + BALLAST_H - DECK_D = 8.9)
        self.assertTrue(len(low), "piers there")
        hw = roads.road_specs(f)[3].width / 2
        self.assertFalse((np.abs(low[:, 0] - 125.0) < hw + 0.5).any(), "none in the road")
        self.assertTrue((low[:, 2] <= dip(low[:, :2]) - railways.PIER_FOOT_M + 1e-6).all(), "all in the ground")

    def test_ground_through_a_deck_raises_it_on_gentle_ramps(self):
        """Ground under a bridge's middle higher than its ends (bad data, or a ridge): the deck over it, the line
        climbing to it at GRADE at most and back down to the ground past the ramps."""
        def hump(xy):
            return np.where((xy[:, 0] > 120) & (xy[:, 0] < 130), 11.0, 10.0)
        f, x, z = self._line(hump)
        deck = (x > 120) & (x < 130)
        self.assertGreaterEqual(z[deck].min(), 11.0 - 1e-6)
        dz = np.abs(np.diff(z)) / np.maximum(np.diff(x), 1e-9)
        self.assertLessEqual(dz[np.diff(x) > 0.5].max(), railways.GRADE + 1e-6)
        self.assertAlmostEqual(float(z[x < -300].max()), 10.0, 1, "back on the ground past the ramp")

    def test_bridge_over_a_valley_runs_straight(self):
        """A bridge over a 30 m deep valley between its banks: straight between them, piers down to the floor."""
        f = features([[(-200.0, 100.0), (60.0, 100.0)], [(60.0, 100.0), (200.0, 100.0)],
                      [(200.0, 100.0), (450.0, 100.0)]], ["standard_gauge"] * 3, subtypes=["rail"] * 3,
                     flags=[(1, "is_bridge")])

        def valley(xy):
            x = xy[:, 0]
            return np.where((x > 70) & (x < 190), -20.0, 10.0 + 0.01 * x)
        rails, prof = self._bed(f, valley)
        (zb, zg), = [p for p, (_, _, k, _) in zip(prof, rails) if k == "bridge"]
        line = np.linspace(zb[0], zb[-1], len(zb))
        self.assertLess(np.abs(zb - line).max(), 0.3)
        self.assertAlmostEqual(zb[0], 10.6, 0)
        self.assertAlmostEqual(zb[-1], 12.0, 0)
        P, T, UV, mats, M = railways.tile_mesh(FRAME, 0, 0, [f], valley)
        self.assertLess(P[np.unique(T[M == 1])][:, 2].min(), -20.0)

    def test_track_follows_a_hill(self):
        f = features([[(-100.0, 100.0), (400.0, 100.0)]], ["standard_gauge"], subtypes=["rail"])

        def hill(xy):
            return 10.0 + 0.015 * xy[:, 0]
        rails, prof = self._bed(f, hill)
        zb, zg = prof[0]
        self.assertLess(np.abs(zb - zg)[10:-10].max(), 0.05)

    def test_ground_graded_to_the_bed(self):
        """A line through a 6 m ridge (a rock cutting the DEM half shows): the grader lays the ground to the track's
        bed, the walls back from it, so the track is not buried (2026-10-25: half a track under the rock)."""
        import grading
        from test_grading import FakeMD
        f = features([[(-400.0, 0.0), (400.0, 0.0)]], ["standard_gauge"], subtypes=["rail"])

        def ridge(la, lo):
            e, _n, _ = FRAME.geodetic_to_enu(np.asarray(la), np.asarray(lo), 0.0)
            return 10.0 + 6.0 * np.exp(-(np.asarray(e) / 40.0) ** 2)
        g = grading.Grader(FRAME, FakeMD(f), ridge)
        e = np.linspace(-60.0, 60.0, 25)
        for off in (0.0, 1.5, -1.5):
            la, lo, _ = FRAME.enu_to_geodetic(e, np.full(len(e), off), 0.0)
            raw = ridge(la, lo)
            got = g.apply(la, lo, raw)
            rails = railways.parts(FRAME, [f], (-60, -10, 60, 10))
            (zb, _), = railways.profile(rails, lambda xy: ridge(*FRAME.enu_to_geodetic(xy[:, 0], xy[:, 1], 0.0)[:2]))
            want = np.interp(e, rails[0][3][:, 0], zb)
            self.assertLess(np.abs(got - want).max(), 0.1, f"the ground at {off} m off the line is at its bed")
            self.assertLess(abs(float(got[12]) - float(raw[12])), 0.3, "over the ridge as it is (the real elevation)")
        self.assertLess(railways.FOOT_MAX_M, 10.0)

    def test_written_with_the_tile(self):
        f = features([[(-50.0, 100.0), (300.0, 100.0)]], ["standard_gauge"], subtypes=["rail"])
        mesh = railways.tile_mesh(FRAME, 0, 0, [f], flat)
        with tempfile.TemporaryDirectory() as d:
            roads.write_tile(d, "/vfs", 1, 0, 0, [], rail_mesh=mesh)
            self.assertTrue(os.path.exists(os.path.join(d, "rail_1_0_0.cdae")))
            self.assertIn("rail", roads.shape_files(1, 0, 0))



class RoadUnderpassTest(unittest.TestCase):
    """A tunnel flag on the lower road must also keep the railway above it open."""

    def setUp(self):
        railways._GROUND_MEMO.clear()

    def tearDown(self):
        railways._GROUND_MEMO.clear()

    def features(self, split=False, tunnel=True, parallel=False):
        track = [[(-200, 100), (120, 100)], [(120, 100), (450, 100)]] if split else [[(-200, 100), (450, 100)]]
        road = [(110, 90), (130, 90)] if parallel else [(120, 90), (120, 110)]
        return features(track + [road], ["standard_gauge"] * len(track) + ["secondary"],
                        subtypes=["rail"] * len(track) + ["road"],
                        flags=[(len(track), "is_tunnel")] if tunnel else [])

    def test_span_replaces_embankment_and_has_clearance(self):
        f = self.features()
        box = roads.tile_bbox(0, 0)
        tunnels = railways.road_underpasses(FRAME, [f], box)
        parts = railways.parts(FRAME, [f], box)
        self.assertTrue(any(p[2] == "bridge" for p in parts))
        prof = railways.profile(parts, flat, tunnels)
        for (_, _, kind, xy), (z, _) in zip(parts, prof):
            self.assertLessEqual(float((np.abs(np.diff(z)) / np.diff(railways._arc(xy))).max()),
                                 railways.GRADE + 1e-6)
            if kind == "bridge":
                self.assertGreaterEqual(float(z.min() + railways.BALLAST_H - railways.DECK_D),
                                        10 + railways.UNDERPASS_CLEAR_M - 0.5)
        P, T, _uv, mats, M = railways.tile_mesh(FRAME, 0, 0, [f], flat)
        self.assertIn(railways.BRICK, mats, "a brick arch over the road")
        # Check all vertices belonging to triangles over the actual lower lane, including ballast skirts, the
        # concrete deck and support piers; the brick arch's curved soffit is meant to come down to the springing.
        tri = P[T]
        x = float(tunnels[0][0][:, 0].mean())
        over = (tri[:, :, 0].min(1) < x + 3) & (tri[:, :, 0].max(1) > x - 3)
        if railways.BRICK in mats:
            over &= M != mats.index(railways.BRICK)
        self.assertTrue(over.any())
        self.assertGreater(float(tri[over, :, 2].min()), 10 + railways.UNDERPASS_CLEAR_M - 0.1)

    def test_no_bridge_for_level_crossing_or_parallel_track(self):
        for f in (self.features(tunnel=False), self.features(parallel=True)):
            self.assertTrue(all(p[2] == "ground" for p in railways.parts(FRAME, [f], roads.tile_bbox(0, 0))))

    def test_tunnel_ending_beside_track_does_not_create_a_bridge(self):
        f = features([[(-200, 100), (450, 100)], [(120, 80), (120, 94)]],
                     ["standard_gauge", "secondary"], subtypes=["rail", "road"], flags=[(1, "is_tunnel")])
        self.assertTrue(all(p[2] == "ground" for p in railways.parts(FRAME, [f], roads.tile_bbox(0, 0))))

    def test_feature_split_and_adjacent_tiles_keep_the_same_span(self):
        f = self.features(split=True)
        a = railways.parts(FRAME, [f], (0, 0, 256, 256))
        b = railways.parts(FRAME, [f, f], (256, 0, 512, 256))
        spans_a = [(p[0], p[3]) for p in a if p[2] == "bridge"]
        spans_b = [(p[0], p[3]) for p in b if p[2] == "bridge"]
        self.assertEqual(len(spans_a), 2)
        self.assertEqual(len(spans_a), len(spans_b))
        for (fa, xa), (fb, xb) in zip(spans_a, spans_b):
            self.assertEqual(fa, fb)
            np.testing.assert_array_equal(xa, xb)
        np.testing.assert_allclose(spans_a[0][1][-1], spans_a[1][1][0], atol=1e-8)

    def test_grading_keeps_lower_road_at_its_floor(self):
        from types import SimpleNamespace
        import grading
        f = self.features()
        md = SimpleNamespace(cell=lambda *a, **kw: SimpleNamespace(layers={"roads": f}))
        def embankment(la, lo):
            e, _, _ = FRAME.geodetic_to_enu(np.asarray(la), np.asarray(lo), 0.0)
            return np.where(np.abs(e - 120.0) < 7.0, 10.0, 15.0)
        g = grading.Grader(FRAME, md, embankment)
        ring = f.parts(len(f) - 1)[0][0]
        e, n, _ = FRAME.geodetic_to_enu(ring[:, 1], ring[:, 0], 0.0)
        xy = np.c_[e, n]
        line = railways._resample(xy, 1.0)
        la, lo, _ = FRAME.enu_to_geodetic(line[:, 0], line[:, 1], 0.0)
        z = g.apply(la, lo, np.full(len(la), 10.0), grading.ROAD_LIFT)
        self.assertLess(float(np.abs(z - (10 + grading.ROAD_LIFT)).max()), 0.2,
                        "rail approach grading must not refill the lower road")



class UnderpassNeighbourTest(unittest.TestCase):
    """Only the track actually over the road gets a span and its clearance."""

    def test_non_crossing_neighbour_stays_on_ground(self):
        for y in (114.0, 122.0):
            with self.subTest(neighbour_y=y):
                f = features([[(-200, 100), (450, 100)], [(-200, y), (450, y)],
                              [(120, 90), (120, 110)]],
                             ["standard_gauge", "standard_gauge", "secondary"],
                             subtypes=["rail", "rail", "road"], flags=[(2, "is_tunnel")])
                box = roads.tile_bbox(0, 0)
                parts = railways.parts(FRAME, [f], box)
                neighbour = str(f.ids[1])
                selected = [p for p in parts if p[0] == neighbour]
                self.assertTrue(selected)
                self.assertTrue(all(p[2] == "ground" for p in selected))
                profiles = railways.profile(parts, flat, railways.road_underpasses(FRAME, [f], box))
                for part, (bed, _) in zip(parts, profiles):
                    if part[0] == neighbour:
                        np.testing.assert_allclose(bed, 10.0, atol=1e-6)

    def test_existing_neighbour_bridge_keeps_its_height(self):
        f = features([[(-200, 100), (450, 100)], [(-200, 114), (450, 114)],
                      [(120, 90), (120, 110)]],
                     ["standard_gauge", "standard_gauge", "secondary"],
                     subtypes=["rail", "rail", "road"], flags=[(1, "is_bridge"), (2, "is_tunnel")])
        box = roads.tile_bbox(0, 0)
        parts = railways.parts(FRAME, [f], box)
        profiles = railways.profile(parts, flat, railways.road_underpasses(FRAME, [f], box))
        neighbour = [(part, bed) for part, (bed, _) in zip(parts, profiles) if part[0] == str(f.ids[1])]
        self.assertTrue(neighbour)
        for part, bed in neighbour:
            self.assertEqual(part[2], "bridge")
            np.testing.assert_allclose(bed, 10.0, atol=1e-6,
                                       err_msg="a tunnel under a different track must not raise this bridge")

    def test_track_connected_far_away_does_not_get_another_span(self):
        # Both parallel legs belong to one feature, joined 330 m from the crossing.
        f = features([[(-200, 100), (450, 100), (450, 122), (-200, 122)],
                      [(120, 90), (120, 110)]], ["standard_gauge", "secondary"],
                     subtypes=["rail", "road"], flags=[(1, "is_tunnel")])
        spans = [p[3] for p in railways.parts(FRAME, [f], roads.tile_bbox(0, 0)) if p[2] == "bridge"]
        self.assertTrue(spans)
        self.assertTrue(all(float(xy[:, 1].max()) < 101 for xy in spans),
                        "the returning leg beside the tunnel is not another crossing")

    def test_span_continues_across_a_feature_split_beside_crossing(self):
        for split in (110, 114, 120, 126, 130):
            with self.subTest(split=split):
                f = features([[(-200, 100), (split, 100)], [(split, 100), (450, 100)],
                              [(120, 90), (120, 110)]],
                             ["standard_gauge", "standard_gauge", "secondary"],
                             subtypes=["rail", "rail", "road"], flags=[(2, "is_tunnel")])
                a = [p for p in railways.parts(FRAME, [f], roads.tile_bbox(0, 0)) if p[2] == "bridge"]
                b = [p for p in railways.parts(FRAME, [f, f], roads.tile_bbox(1, 0)) if p[2] == "bridge"]
                self.assertEqual({p[0] for p in a}, {str(f.ids[0]), str(f.ids[1])})
                self.assertEqual(len(a), len(b))
                for pa, pb in zip(a, b):
                    self.assertEqual(pa[:3], pb[:3])
                    np.testing.assert_array_equal(pa[3], pb[3])
                np.testing.assert_allclose(a[0][3][-1], a[1][3][0], atol=1e-6)

    def test_two_tracks_really_crossing_the_tunnel_both_get_clearance(self):
        f = features([[(-200, 100), (450, 100)], [(-200, 108), (450, 108)],
                      [(120, 90), (120, 118)]],
                     ["standard_gauge", "standard_gauge", "secondary"],
                     subtypes=["rail", "rail", "road"], flags=[(2, "is_tunnel")])
        box = roads.tile_bbox(0, 0)
        parts = railways.parts(FRAME, [f], box)
        profiles = railways.profile(parts, flat, railways.road_underpasses(FRAME, [f], box))
        raised = {part[0] for part, (bed, _) in zip(parts, profiles)
                  if part[2] == "bridge" and float(bed.max()) > 14.0}
        self.assertEqual(raised, {str(f.ids[0]), str(f.ids[1])})


if __name__ == "__main__":
    unittest.main()


class CoarseUnderpassTest(unittest.TestCase):
    """GitHub #414: on coarse elevation a rail bridge over a ground road rises over it (railways.coarse_underpasses)."""

    def test_crossings_are_found_along_the_road(self):
        road = np.array([[0.0, 0.0], [100.0, 0.0]])
        rail = np.array([[40.0, -20.0], [40.0, 20.0]])
        self.assertEqual([round(v, 6) for v in railways._seg_hits(road, rail)], [40.0])
        self.assertEqual(railways._seg_hits(road, rail + [0.0, 50.0]), [])

    def test_only_on_coarse_elevation(self):
        import geodesy
        frame = geodesy.EnuFrame(58.1, 56.4, 0.0)
        saved = railways.COARSE_AT
        try:
            railways.COARSE_AT = None
            self.assertFalse(railways._coarse_here(frame, (0, 0, 10, 10)))
            railways.COARSE_AT = lambda la, lo: True
            self.assertTrue(railways._coarse_here(frame, (0, 0, 10, 10)))
            self.assertEqual(railways.coarse_underpasses(frame, [], (0, 0, 10, 10), []), [])   # no rail bridge
        finally:
            railways.COARSE_AT = saved
