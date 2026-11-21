import _isolate  # first: settings and caches stay in a temporary folder
import _presets
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from atlas_toolkit.core.job import JobContext
from atlas_toolkit.core.registry import registry, validate
from atlas_toolkit.geom.svg import read_run_metadata

from .helpers import torus
from .test_cli import write_obj


class AdapterTests(unittest.TestCase):
    """The FlowRep Sketch tool as Atlas runs it: contract, presets, draft preview and a signed SVG."""

    def test_registry_contract_presets_and_signed_output(self):
        tool = registry().get("flowrep-sketch")
        self.assertFalse(validate(tool))
        self.assertEqual(tool.runs_in, "process")
        if _presets.available():
            self.assertEqual(_presets.values(tool, "Detailed")["dmax"], 10.)
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            model = write_obj(torus(), tmp / "toy.obj")
            with patch.dict(os.environ, {"FLOWREP_CACHE": str(tmp / "cache")}):
                options = tool.defaults()
                options["regularize"] = 0
                preview = tool.engine_fn()(JobContext(lambda *a: None, lambda: False, "draft"), model, None, options)
                self.assertEqual(preview.outputs, [])
                self.assertEqual(preview.preview["views"][0]["kind"], "svg")
                target = tmp / "drawing.svg"
                result = tool.engine_fn()(JobContext(lambda *a: None, lambda: False), model, target, options)
                self.assertEqual(read_run_metadata(target)[0], "flowrep-sketch")
                self.assertIn("<desc>flowrep-sketch</desc>", target.read_text(encoding="utf-8"))
                self.assertEqual(result.outputs, [str(target)])

    def test_drawing_lands_where_the_aim_tab_places_it(self):
        """The engine places the model as the Aim tab does (geom/frame3d), so a frame stored in the file counts: a
        flat torus saved Z-up was drawn turned (flowrep3d guessed +X up for it) and away from where it was aimed."""
        import numpy as np
        from atlas_toolkit.core.params import Params
        from atlas_toolkit.geom import frame3d
        from atlas_toolkit.geom.camera import camera_from, frame_for
        tool = registry().get("flowrep-sketch")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            model = write_obj(torus(), tmp / "flat.obj")
            model.write_text('# atlas-frame: {"up": "+z", "front": "-y", "rotate": [0, 0, 0]}\n'
                             + model.read_text(encoding="utf-8"), encoding="utf-8")
            p = Params(dict(tool.defaults(), regularize=0, theta=53.1, psi=-164.8, radius=213.0, fov=41.0,
                            lookat=(-1.0, 2.0, 0.0)))
            self.assertEqual(frame3d.resolve_axes(str(model), p)[:2], ("+z", "-y"))
            with patch.dict(os.environ, {"FLOWREP_CACHE": str(tmp / "cache")}):
                res = tool.engine_fn()(JobContext(lambda *a: None, lambda: False, "draft"), str(model), None, p)
            drawn = np.concatenate([np.asarray(pl, float).reshape(-1, 2)
                                    for pen in res.preview["views"][0]["drawing"].pens for pl in pen.polylines()])
            V = np.asarray(torus().vertices, float)
            cam, paper = camera_from(p), frame_for(p.paper, p.orientation, p.margin)
            aimed = paper.to_mm(cam.project(cam.to_camera(frame3d.frame_for(str(model), p, V=V).apply(V))))
            np.testing.assert_allclose(drawn.min(0), aimed.min(0), atol=2.0)
            np.testing.assert_allclose(drawn.max(0), aimed.max(0), atol=2.0)
            # Roll: the drawing turns as the Aim tab turns its picture (the network is cached: this only redraws)
            from atlas_toolkit.geom.camera import roll_screen
            p = Params(dict(p, roll=40.0))
            with patch.dict(os.environ, {"FLOWREP_CACHE": str(tmp / "cache")}):
                res = tool.engine_fn()(JobContext(lambda *a: None, lambda: False, "draft"), str(model), None, p)
            drawn = np.concatenate([np.asarray(pl, float).reshape(-1, 2)
                                    for pen in res.preview["views"][0]["drawing"].pens for pl in pen.polylines()])
            aimed = paper.to_mm(roll_screen(cam.project(cam.to_camera(frame3d.frame_for(str(model), p, V=V).apply(V))),
                                            40.0))
            np.testing.assert_allclose(drawn.min(0), aimed.min(0), atol=2.0)
            np.testing.assert_allclose(drawn.max(0), aimed.max(0), atol=2.0)


if __name__ == "__main__":
    unittest.main()
