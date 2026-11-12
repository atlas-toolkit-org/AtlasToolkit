import _isolate  # first: settings and caches stay in a temporary folder
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from .helpers import cylinder, torus


def write_obj(mesh, path: Path) -> Path:
    lines = [f"v {x:.9g} {y:.9g} {z:.9g}" for x, y, z in mesh.vertices]
    lines += ["f " + " ".join(str(i + 1) for i in face) for face in mesh.faces]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def flowrep(*args, **kw):
    return subprocess.run([sys.executable, "-m", "flowrep3d", *map(str, args)], capture_output=True, text=True, **kw)


class CLITests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.toy = write_obj(torus(), self.tmp / "toy.obj")
        self.spring = write_obj(cylinder(), self.tmp / "spring.obj")

    def tearDown(self):
        self._tmp.cleanup()

    def test_extract_render_and_no_overwrite(self):
        network, svg = self.tmp / "toy.npz", self.tmp / "toy.svg"
        extract = flowrep("extract", self.toy, "--no-cache", "--regularize", "0", "--local-passes", "0", "--quiet",
                          "-o", network)
        self.assertEqual(extract.returncode, 0, extract.stderr)
        render = flowrep("render", network, "--quiet", "-o", svg)
        self.assertEqual(render.returncode, 0, render.stderr)
        repeat = flowrep("render", network, "--quiet", "-o", svg)
        self.assertEqual(repeat.returncode, 2)
        self.assertIn("output exists", repeat.stderr)

    def test_batch_destinations_validated_before_work(self):
        run = flowrep("draw", self.toy, self.spring, "-o", self.tmp / "one.svg")
        self.assertEqual(run.returncode, 2)
        self.assertIn("batch", run.stderr)

    def test_parallel_benchmark_report(self):
        out = self.tmp / "bench"
        run = flowrep("benchmark", self.toy, "--out-dir", out, "--jobs", "2", "--no-cache", "--regularize", "0",
                      "--local-passes", "0", "--quiet", timeout=120)
        self.assertEqual(run.returncode, 0, run.stderr)
        report = json.loads((out / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["models"][0]["status"], "ok")
        self.assertTrue((out / "report.html").exists())


if __name__ == "__main__":
    unittest.main()
