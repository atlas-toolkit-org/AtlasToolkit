"""The solid-mesh tools and Repair Mesh together: decimation debris is tidied by the tool itself; a real opening
stops the tool with an error that names Repair Mesh, and the repaired model then runs."""
import _isolate  # first: settings and caches stay in a temporary folder
import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh

from atlas_toolkit.core.job import Job, execute
from atlas_toolkit.core.registry import registry
from atlas_toolkit.geom import repair as R

SOLID_TOOLS = ("mesh-motion", "mesh-crumble", "mesh-developable", "pipe-fill", "mesh-stairs", "mesh-surface-eject",
               "mesh-stripes")


def blob():
    """A lumpy closed solid (not a sphere: some tools want a front and a top)."""
    m = trimesh.creation.icosphere(subdivisions=4, radius=1.0)
    V = np.asarray(m.vertices, float)
    V *= (1 + 0.12 * np.sin(3 * V[:, [0]]) * np.cos(2 * V[:, [1]]))
    V *= [30.0, 24.0, 40.0]
    return V, np.asarray(m.faces, np.int64)


def debris(V, F):
    """What the catalogue's light models had: a zero-volume sheet on an edge, and a lone crumb."""
    a, b = F[7][0], F[7][1]
    n = len(V)
    V = np.vstack([V, (V[a] + V[b]) / 2 * 1.01, [[0, 0, 80], [0.2, 0, 80], [0, 0.2, 80]]])
    return V, np.vstack([F, [[a, b, n], [b, a, n], [n + 1, n + 2, n + 3]]])


def opening(V, F):
    z = V[F].mean(1)[:, 2]
    return V, F[z > z.min() + 0.1 * np.ptp(z)]                 # the base cut away, never capped


class Guidance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp())
        cls.reg = registry()
        V, F = blob()
        cls.debris = cls.dir / "debris.ply"
        trimesh.Trimesh(*debris(V, F), process=False).export(cls.debris)
        cls.open = cls.dir / "open.ply"
        trimesh.Trimesh(*opening(V, F), process=False).export(cls.open)

    def run_tool(self, tool_id, src, mode="draft"):
        tool = self.reg.get(tool_id)
        values = dict(tool.values(dict(tool.test_values)))
        dst = self.dir / tool_id / tool.output.name_for(Path(src), values)
        box = {}
        execute(tool, Job(tool_id, mode, str(src), str(dst) if mode == "run" else None, values,
                          cache_dir=str(self.dir / "cache")),
                lambda k, p: box.__setitem__(k, p) if k in ("done", "error") else None, lambda: False)
        return box

    def test_debris_is_tidied_by_every_tool(self):
        self.assertFalse(R.diagnose(R.load_raw(self.debris)).solid)
        for tid in SOLID_TOOLS:
            with self.subTest(tid):
                box = self.run_tool(tid, self.debris)
                self.assertIn("done", box, box.get("error", ("",))[0])
                notes = box["done"][0].notes
                self.assertTrue(any("Tidied the input" in n for n in notes), notes)

    def test_an_opening_names_repair_mesh_then_the_repaired_model_runs(self):
        out = self.dir / "open_repaired.ply"
        rep = self.reg.get("mesh-repair")
        box = {}
        execute(rep, Job("mesh-repair", "run", str(self.open), str(out), dict(rep.defaults())),
                lambda k, p: box.__setitem__(k, p) if k in ("done", "error") else None, lambda: False)
        self.assertIn("done", box, box.get("error"))
        self.assertTrue(R.diagnose(R.load_raw(out)).solid)
        for tid in SOLID_TOOLS:
            with self.subTest(tid):
                if tid == "mesh-developable":            # openings are fine for a developable surface
                    continue
                box = self.run_tool(tid, self.open)
                self.assertIn("error", box, f"{tid} accepted an open model")
                text, trace, diag = box["error"]
                self.assertEqual(diag.get("suggest_tool"), "mesh-repair", text)
                self.assertIn("Repair Mesh", text)
                self.assertIn("What to do:", text)
                self.assertEqual(trace, "")
                box = self.run_tool(tid, out)
                self.assertIn("done", box, box.get("error", ("",))[0])

    def test_inspect_says_it_early(self):
        from atlas_toolkit.core.job import inspect_input
        tool = self.reg.get("mesh-crumble")
        info = inspect_input(tool, str(self.open))
        self.assertEqual(info.values["input_check"]["state"], "repair")
        self.assertIn("Repair Mesh", info.summary)
        info = inspect_input(tool, str(self.debris))
        self.assertEqual(info.values["input_check"]["state"], "tidy")

    def test_repair_tool_lists_the_tools_it_is_ready_for(self):
        from atlas_toolkit.tools.mesh_repair.engine import ready_for
        d = R.diagnose_arrays(*blob())
        ready = ready_for(d, self.reg)
        for tid in SOLID_TOOLS:
            self.assertIn(self.reg.get(tid).name, ready)

    def test_surface_eject_defaults_to_the_whole_surface(self):
        self.assertEqual(self.reg.get("mesh-surface-eject").defaults()["region"], "whole")


if __name__ == "__main__":
    unittest.main()
