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


if __name__ == "__main__":
    unittest.main()
