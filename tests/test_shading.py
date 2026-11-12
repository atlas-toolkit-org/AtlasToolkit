"""Ray-cast shading (geom/shading), curvature (geom/curvature) and Stipple: analytic checks, and parity with the
Blender pieces' own numpy functions where the project folder is present."""
import _isolate  # first: settings and caches stay in a temporary folder
import ast
import math
import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh

from atlas_toolkit.core.job import JobContext
from atlas_toolkit.core.registry import registry
from atlas_toolkit.devpaths import workspace_path
from atlas_toolkit.geom import curvature as sm
from atlas_toolkit.geom import shading
from atlas_toolkit.geom.camera import Camera, frame_for
from atlas_toolkit.geom.raycast import Caster
from atlas_toolkit.tools.stipple import TOOL as STIPPLE
from atlas_toolkit.tools.stipple import engine as stipple_engine
from atlas_toolkit.tools.stipple import stipple as st

# the original Blender pieces, in the studio workspace (dev.local.toml); the parity tests skip without them
PIECES = workspace_path("projects", "blender3d_project") or Path("no-studio-workspace")
DD_STIPPLE = PIECES / "digital_divide_1" / "plotter_lib" / "stipple.py"


def context(mode="run"):
    return JobContext(lambda *_: None, lambda: False, mode)


def tube(r, h, n=96, m=60):
    """An open cylinder with vertices on the circle at every height (a prism's flat sides have no curvature)."""
    a = np.linspace(0, 2 * np.pi, n, endpoint=False)
    z = np.linspace(-h / 2, h / 2, m)
    A, Z = np.meshgrid(a, z)
    V = np.c_[r * np.cos(A.ravel()), r * np.sin(A.ravel()), Z.ravel()]
    i, j = np.meshgrid(np.arange(m - 1), np.arange(n), indexing="ij")
    v00, v01 = (i * n + j).ravel(), (i * n + (j + 1) % n).ravel()
    v10, v11 = v00 + n, v01 + n
    F = np.r_[np.c_[v00, v01, v11], np.c_[v00, v11, v10]]
    return trimesh.Trimesh(V, F, process=False)


def ortho_view(mesh, theta=90.0, psi=180.0):
    cam = Camera(theta=theta, psi=psi, radius=10.0, fov=40.0, ortho=True)
    frame = frame_for("A5", "portrait", 10.0)
    return shading.Projector(cam, frame), Caster(mesh.vertices, mesh.faces)


class CurvatureTests(unittest.TestCase):
    def test_sphere_and_cylinder(self):
        s = trimesh.creation.icosphere(subdivisions=4, radius=2.0)
        N = sm.vertex_normals(s.vertices, s.faces)
        c = sm.curvature(s.vertices, s.faces, N)
        np.testing.assert_allclose(c["k1"], 0.5, atol=0.02)
        np.testing.assert_allclose(c["k2"], 0.5, atol=0.02)
        cyl = tube(1.0, 4.0)
        keep = np.abs(cyl.vertices[:, 2]) < 1.0
        N = sm.vertex_normals(cyl.vertices, cyl.faces)
        c = sm.curvature(cyl.vertices, cyl.faces, N)
        side = keep & (np.abs(N[:, 2]) < 0.1)
        self.assertAlmostEqual(float(np.median(np.abs(c["k1"][side]))), 1.0, delta=0.05)
        self.assertLess(float(np.median(np.abs(c["k2"][side]))), 0.05)

    def test_taubin_keeps_the_volume(self):
        s = trimesh.creation.icosphere(subdivisions=3, radius=1.0)
        E = sm.edge_topology(s.faces)[0]
        V = sm.taubin_smooth(s.vertices, E, 30)
        r = np.linalg.norm(V, axis=1)
        self.assertGreater(r.mean(), 0.97)


class ShadingTests(unittest.TestCase):
    def test_gbuffer_of_a_sphere_is_a_disc(self):
        s = trimesh.creation.icosphere(subdivisions=4, radius=1.0)
        proj, caster = ortho_view(s)
        an = shading.analyse(s.vertices, s.faces, caster, curvature=False, ao_rays=0, tone_smooth=2)
        x0, y0, x1, y1 = proj.bounds(s.vertices)
        gb = shading.gbuffer(an, caster, proj, (x0 - 2, y0 - 2, x1 + 2, y1 + 2), 4.0)
        r_mm = proj.cam.mm_per_unit(proj.frame) * 1.0
        area = gb["hit"].sum() * gb["px"] ** 2
        self.assertAlmostEqual(area / (math.pi * r_mm ** 2), 1.0, delta=0.04)
        # facing the camera at the centre: n.v = 1
        self.assertGreater(gb["ndotv"][gb["hit"]].max(), 0.99)

    def test_ambient_occlusion_and_shadows(self):
        s = trimesh.creation.icosphere(subdivisions=3, radius=1.0)
        caster = Caster(s.vertices, s.faces)
        ao = shading.vertex_ao(caster, s.vertices, sm.vertex_normals(s.vertices, s.faces), rays=16, distance=0.5)
        self.assertGreater(ao.min(), 0.97)             # nothing occludes a convex body
        # a ball over a floor plate, lit at 45 degrees from +X, seen from above: its shadow falls at x ~ -1.5
        plate = trimesh.creation.box(extents=(8, 8, 0.1))
        plate.apply_translation((0, 0, -1.5))
        scene = trimesh.util.concatenate([s, plate])
        proj, caster = ortho_view(scene, theta=1.0, psi=0.0)          # looking down
        an = shading.analyse(scene.vertices, scene.faces, caster, curvature=False, ao_rays=0, tone_smooth=2)
        gb = shading.gbuffer(an, caster, proj, proj.bounds(scene.vertices), 3.0)
        lit, _ = shading.shadows(an, caster, gb, (1.0, 0.0, 1.0), samples=2, softness_deg=1.0)
        pos = gb["pos"]
        floor = gb["hit"] & (pos[:, 2] < -1.4)
        shade = floor & (np.hypot(pos[:, 0] + 1.9, pos[:, 1]) < 0.3)
        sun = floor & (np.hypot(pos[:, 0] - 2.5, pos[:, 1]) < 0.5)
        self.assertTrue(shade.any() and sun.any())
        self.assertLess(lit[shade].mean(), 0.1)
        self.assertGreater(lit[sun].mean(), 0.9)

    def test_feature_lines_of_a_sphere_are_one_contour(self):
        s = trimesh.creation.icosphere(subdivisions=4, radius=1.0)
        proj, caster = ortho_view(s)
        an = shading.analyse(s.vertices, s.faces, caster, ao_rays=0, line_smooth=2, field_smooth=4, tone_smooth=2)
        tr = shading.trace(an, caster, proj, proj.bounds(s.vertices), (0.0, -1.0, 1.0), shadow_samples=1)
        kinds = {ln["kind"] for ln in tr["lines"]}
        self.assertIn("contour", kinds)
        self.assertNotIn("suggestive", kinds)


class StippleTests(unittest.TestCase):
    def test_density_follows_darkness_and_spacing(self):
        dark = np.tile(np.linspace(0, 1, 200), (100, 1))
        rho = st.dot_density(dark, np.ones_like(dark, bool), 0.2, min_spacing_mm=1.0, void=0.2, gamma=1.0)
        self.assertEqual(rho[:, :35].max(), 0.0)                 # the void stays empty
        pts = st.poisson_dots(rho, 0.0, 0.0, 0.2, budget=10 ** 6, seed=3)
        expected = rho.sum() * 0.04
        self.assertAlmostEqual(len(pts) / expected, 1.0, delta=0.15)
        from scipy.spatial import cKDTree
        d, _ = cKDTree(pts).query(pts, k=2)
        self.assertGreater(d[:, 1].min(), 0.5)                   # blue noise: no clumps
        self.assertEqual(len(st.poisson_dots(rho, 0, 0, 0.2, budget=50, seed=3)), 50)
        ring = st.dot_paths(pts[:3], 0.15, 8)[0]
        np.testing.assert_allclose(ring[0], ring[-1])

    @unittest.skipUnless(DD_STIPPLE.exists(), "the Digital divide project is not here")
    def test_same_dots_as_digital_divide(self):
        tree = ast.parse(DD_STIPPLE.read_text(encoding="utf-8"))
        ns = {"math": math, "np": np, "K_RSA": 0.84}
        body = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
        exec(compile(ast.Module(body=body, type_ignores=[]), str(DD_STIPPLE), "exec"), ns)
        rng = np.random.default_rng(1)
        dark = rng.random((80, 120))
        mask = dark > 0.1
        a = ns["dot_density"](dark, mask, 0.2, 0.8, 0.3, 1.4, None, 3.0, 0.3, 6.0, 4)
        b = st.dot_density(dark, mask, 0.2, 0.8, 0.3, 1.4, None, 3.0, 0.3, 6.0, 4)
        np.testing.assert_allclose(a, b)
        np.testing.assert_allclose(ns["poisson_dots"](a, 1.0, 2.0, 0.2, 500, seed=9),
                                   st.poisson_dots(b, 1.0, 2.0, 0.2, 500, seed=9))

    def test_engine_draws_dots_inside_the_margins(self):
        registry()
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "ball.stl"
            trimesh.creation.icosphere(subdivisions=4, radius=30.0).export(src)
            vals = STIPPLE.values(dict(paper="A5", budget=1500, outline="pen", theta=80.0, psi=180.0, radius=250.0))
            res = stipple_engine.run(context("analyse"), src, None, vals)
            self.assertGreater(res.data["dots"], 500)
            drawing = res.preview["views"][0]["drawing"]
            x0, y0, x1, y1 = drawing.bounds()
            self.assertTrue(x0 >= vals.margin and y0 >= vals.margin and x1 <= drawing.width - vals.margin
                            and y1 <= drawing.height - vals.margin)
            self.assertEqual(len(drawing.pens), 2)
            self.assertLess(res.data["coverage"], 0.7)


if __name__ == "__main__":
    unittest.main()
