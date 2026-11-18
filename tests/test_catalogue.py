"""
The model catalogue (core/catalogue.py, geom/shapes.py, geom/thumbnail.py, catalogue/recipes.toml): its index, its
licences and credits, the shapes made in code, and getting a model both ways (unpacked from the app, downloaded from a
local stand-in server: hash, cancel and no-network cases). Everything goes to temporary folders.
"""
import _isolate  # first: settings and caches stay in a temporary folder
import gzip
import hashlib
import http.server
import json
import shutil
import socket
import tempfile
import threading
import tomllib
import unittest
from pathlib import Path

import numpy as np

from atlas_toolkit.core import catalogue as cat
from atlas_toolkit.core import paths
from atlas_toolkit.core.download import DownloadError

ROOT = Path(__file__).resolve().parents[1]
RECIPES = ROOT / "catalogue" / "recipes.toml"
ALLOWED = {"CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0", "LicenseRef-public-domain"}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class IndexTests(unittest.TestCase):
    """What ships: index.json, the thumbnails, the notices; and that they agree with the recipes."""

    @classmethod
    def setUpClass(cls):
        cls.index = json.loads((cat.root() / cat.INDEX).read_text(encoding="utf-8"))
        cls.recipes = tomllib.loads(RECIPES.read_text(encoding="utf-8"))

    def test_every_model_is_complete_and_allowed(self):
        cats = {c["id"] for c in self.index["categories"]}
        ids = [m["id"] for m in self.index["models"]]
        self.assertEqual(len(ids), len(set(ids)), "duplicate ids")
        for m in self.index["models"]:
            with self.subTest(model=m["id"]):
                self.assertIn(m["category"], cats)
                self.assertIn(m["kind"], cat.KINDS)
                self.assertIn(m["license"], ALLOWED, "only licences a GPL-3.0 app may ship (no NC, ND, BY-SA 3.0)")
                self.assertTrue((cat.root() / "thumbs" / f"{m['id']}.png").is_file(), "no thumbnail")
                self.assertGreater(m["triangles"], 5000)
                self.assertTrue(m["source_url"].startswith("https://"), "a source page")
                self.assertRegex(m["sha256"], r"^[0-9a-f]{64}$")
                self.assertLessEqual(m["bytes"], 20_000_000)
                if m.get("generator"):
                    self.assertIn(m["generator"], __import__("atlas_toolkit.geom.shapes", fromlist=["x"]).GENERATORS)
                    self.assertEqual(m["license"], "CC0-1.0")

    def test_every_category_has_models_and_its_own_icon(self):
        from atlas_toolkit.ui.theme import CATALOGUE_ICONS, icon_paths
        for c in self.index["categories"]:
            with self.subTest(category=c["id"]):
                self.assertTrue(any(m["category"] == c["id"] for m in self.index["models"]))
                self.assertIn(c["icon"], icon_paths())
                self.assertIn(c["icon"], CATALOGUE_ICONS)

    def test_the_index_is_the_recipes(self):
        """scripts/catalogue.py index was run after the last recipe change."""
        self.assertEqual([m["id"] for m in self.index["models"]], [m["id"] for m in self.recipes["model"]])
        for r, m in zip(self.recipes["model"], self.index["models"]):
            self.assertEqual((r["name"], r["category"]), (m["name"], m["category"]), r["id"])

    def test_recipes_name_their_licence_evidence(self):
        for m in self.recipes["model"]:
            with self.subTest(model=m["id"]):
                if m.get("generator"):
                    continue
                self.assertTrue(m.get("commons") or m.get("license_evidence") or m.get("from"),
                                "a Commons file (its licence is checked there), a quote of the source's terms, or a "
                                "lighter copy of a model that has one")

    def test_simple_shapes_ship_and_complex_ones_download(self):
        simple = set(self.recipes["catalogue"]["simple_shapes"])
        shapes = {m["id"]: m["kind"] for m in self.index["models"] if m["category"] == "shapes"}
        self.assertLessEqual(simple, set(shapes))
        for mid, kind in shapes.items():
            self.assertEqual(kind, "bundled" if mid in simple else "remote", mid)

    def test_light_and_quick_is_light_and_in_the_app(self):
        light = [m for m in self.index["models"] if m["category"] == "light"]
        self.assertGreaterEqual(len(light), 10)
        for m in light:
            with self.subTest(model=m["id"]):
                self.assertTrue(5_000 <= m["triangles"] <= 50_000, m["triangles"])
                self.assertEqual(m["kind"], "bundled")
        recipes = {r["id"]: r for r in self.recipes["model"]}
        for r in recipes.values():                                 # copies name a model that has its own source
            if "from" in r:
                self.assertIn(r["from"], recipes)
                self.assertNotIn("from", recipes[r["from"]])

    def test_the_starter_set_stays_small(self):
        bundled = [m for m in self.index["models"] if m["kind"] == "bundled"]
        self.assertGreaterEqual(len(bundled), 5)
        self.assertLess(sum(m["bytes"] for m in bundled), 45_000_000)

    def test_every_scanned_model_is_credited_in_the_notices(self):
        notice = (ROOT / "src" / "atlas_toolkit" / "licenses" / "catalogue-models.txt").read_text(encoding="utf-8")
        for m in self.index["models"]:
            self.assertIn(m["name"], notice)
            if not m.get("generator"):
                self.assertIn(m["source_url"], notice)
        third = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        self.assertIn("catalogue-models.txt", third)
        for lic in ("CC0-1.0.txt", "CC-BY-4.0.txt", "CC-BY-SA-4.0.txt"):
            self.assertTrue((ROOT / "src" / "atlas_toolkit" / "licenses" / lic).is_file(), lic)

    def test_no_tool_uses_a_catalogue_icon(self):
        from atlas_toolkit.core.registry import registry
        from atlas_toolkit.ui.theme import CATALOGUE_ICONS
        for t in registry().ordered():
            self.assertNotIn(t.icon, CATALOGUE_ICONS, t.id)


class ShapeTests(unittest.TestCase):
    QUICK = ("sphere", "cube", "torus", "torus_knot", "mobius_band", "twisted_torus", "klein_bottle", "boys_surface",
             "menger_sponge", "vase", "chess_pawn", "stella_octangula")

    def test_quick_shapes_are_sound_and_framed(self):
        from atlas_toolkit.geom import shapes
        for name in self.QUICK:
            with self.subTest(shape=name):
                m = shapes.build(name)
                self.assertGreaterEqual(len(m.faces), 10_000)
                self.assertAlmostEqual(float(m.extents.max()), shapes.SIZE, places=3)
                np.testing.assert_allclose(m.bounds.mean(0), 0, atol=1e-6)
                self.assertTrue(m.is_watertight, "closed (one-sided surfaces too: every edge has two faces)")
                if name not in ("klein_bottle", "boys_surface"):
                    self.assertTrue(m.is_winding_consistent)
                    self.assertGreater(m.volume, 0, "outward normals")

    def test_shapes_are_deterministic(self):
        from atlas_toolkit.geom import shapes
        a, b = shapes.build("torus_knot", p=2, q=5), shapes.build("torus_knot", p=2, q=5)
        np.testing.assert_array_equal(a.faces, b.faces)
        np.testing.assert_allclose(a.vertices, b.vertices)

    def test_every_generator_is_offered(self):
        from atlas_toolkit.geom import shapes
        recipes = tomllib.loads(RECIPES.read_text(encoding="utf-8"))
        used = {m.get("generator") for m in recipes["model"]}
        self.assertEqual(set(shapes.GENERATORS) - used, set())

    def test_thumbnail_render_is_transparent_round_the_model(self):
        from atlas_toolkit.geom import shapes, thumbnail
        m = shapes.build("sphere", subdivisions=3)
        img = np.asarray(thumbnail.render(m.vertices, m.faces, 64, ao_rays=2, shadow_rays=1))
        self.assertEqual(img.shape, (64, 64, 4))
        self.assertEqual(img[0, 0, 3], 0)
        self.assertEqual(img[32, 32, 3], 255)


def _index(files: Path, models: list, base_url: str = "http://127.0.0.1:1/") -> dict:
    index = {"schema": 1, "revision": 1, "base_url": base_url,
             "categories": [{"id": "shapes", "name": "Shapes & forms", "icon": "cat_shapes"}], "models": models}
    (files / "thumbs").mkdir(parents=True, exist_ok=True)
    (files / cat.INDEX).write_text(json.dumps(index), encoding="utf-8")
    return index


def _packed(folder: Path, name: str = "ball.ply.gz") -> Path:
    from atlas_toolkit.geom import shapes
    m = shapes.build("sphere", subdivisions=2)
    ply = folder / "ball.ply"
    cat.write_ply(ply, m.vertices, m.faces,                          # the header a real build writes
                  cat.ply_comments(cat.Model(id="ball", name="Ball", category="shapes", kind="bundled")))
    gz = folder / name
    with ply.open("rb") as src, gzip.open(gz, "wb") as out:
        shutil.copyfileobj(src, out)
    ply.unlink()
    return gz


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


class EnsureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="atlas_catalogue_"))
        self.files = self.tmp / "app"
        self.folder = self.tmp / "Catalogue"
        self.files.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_folder_stays_in_the_test_home(self):
        self.assertTrue(paths.catalogue_dir().resolve().is_relative_to(Path(_isolate.ROOT).resolve()))
        self.assertEqual(paths.catalogue_dir().name, "catalogue")

    def _bundled(self, name="Ball"):
        (self.files / "models").mkdir(exist_ok=True)
        gz = _packed(self.files / "models")
        return {"id": "ball", "name": name, "category": "shapes", "kind": "bundled", "file": gz.name,
                "sha256": sha(gz), "bytes": gz.stat().st_size, "license": "CC0-1.0", "triangles": 320}

    def test_model_is_unpacked_credited_and_framed(self):
        _index(self.files, [self._bundled()])
        c = cat.Catalogue.load(self.folder, files=self.files)
        m = c.get("ball")
        self.assertEqual(c.status(m), "bundled")
        stages = []
        p = c.ensure(m, progress=lambda s, f: stages.append(s))
        self.assertEqual(stages, ["unpacking"])
        self.assertEqual(p, self.folder / "Shapes & forms" / "Ball.ply")
        self.assertTrue(p.with_suffix(".txt").read_text(encoding="utf-8").startswith("Ball"))
        self.assertEqual(c.status(m), "local")
        from atlas_toolkit.geom.mesh import load_mesh
        from atlas_toolkit.geom.surface import read_frame
        self.assertEqual(len(load_mesh(p).faces), 320)
        self.assertEqual(read_frame(p)["up"], "+z")
        self.assertEqual(c.ensure(m), p)                          # once there, used as it is
        self.assertEqual(len(c.local_models()), 1)
        c.remove(m)
        self.assertFalse(p.exists())
        self.assertEqual(c.status(m), "bundled")

    def test_a_changed_entry_fetches_again(self):
        entry = self._bundled()
        _index(self.files, [entry])
        c = cat.Catalogue.load(self.folder, files=self.files)
        c.ensure(c.get("ball"))
        _index(self.files, [dict(entry, sha256="4" * 64)])             # a new file for the same model
        newer = cat.Catalogue.load(self.folder, files=self.files)
        self.assertNotEqual(newer.status(newer.get("ball")), "local")

    def test_damaged_bundled_file_is_refused(self):
        _index(self.files, [dict(self._bundled(), sha256="0" * 64, id="bad", name="Bad")])
        c = cat.Catalogue.load(self.folder, files=self.files)
        with self.assertRaises(ValueError):
            c.ensure(c.get("bad"))
        self.assertFalse(self.folder.joinpath("Shapes & forms", "Bad.ply").exists())

    def _serve(self, folder: Path):
        handler = lambda *a, **k: _Quiet(*a, directory=str(folder), **k)  # noqa: E731
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return f"http://127.0.0.1:{srv.server_address[1]}/"

    def test_remote_model_downloads_and_is_verified(self):
        served = self.tmp / "release"
        served.mkdir()
        gz = _packed(served)
        url = self._serve(served)
        entry = {"id": "ball", "name": "Ball", "category": "shapes", "kind": "remote", "file": gz.name,
                 "sha256": sha(gz), "bytes": gz.stat().st_size, "license": "CC0-1.0", "triangles": 320}
        _index(self.files, [entry, dict(entry, id="bad", name="Bad", sha256="1" * 64)], url)
        c = cat.Catalogue.load(self.folder, files=self.files)
        self.assertTrue(c.needs_download(c.get("ball")))
        seen = []
        p = c.ensure(c.get("ball"), progress=lambda s, f: seen.append((s, f)))
        self.assertEqual(seen[-1][0], "unpacking")
        self.assertEqual(max(f for s, f in seen if s == "downloading"), 1.0)
        self.assertTrue(p.is_file())
        self.assertEqual(c.status(c.get("ball")), "local")
        with self.assertRaises(DownloadError) as e:
            c.ensure(c.get("bad"))
        self.assertEqual(e.exception.kind, "hash")
        self.assertFalse(c.path_for(c.get("bad")).exists())
        with self.assertRaises(DownloadError) as e:                # cancelled at once
            c.remove(c.get("ball"))
            c.ensure(c.get("ball"), cancelled=lambda: True)
        self.assertEqual(e.exception.kind, "cancelled")

    def test_no_network_says_so(self):
        with socket.socket() as s:                                 # a port nothing listens on
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        entry = {"id": "ball", "name": "Ball", "category": "shapes", "kind": "remote", "file": "ball.ply.gz",
                 "sha256": "2" * 64, "bytes": 10, "license": "CC0-1.0", "triangles": 320}
        _index(self.files, [entry], f"http://127.0.0.1:{port}/")
        c = cat.Catalogue.load(self.folder, files=self.files)
        with self.assertRaises(DownloadError) as e:
            c.ensure(c.get("ball"))
        self.assertEqual(e.exception.kind, "network")
        self.assertIn("connection", str(e.exception))

    def test_missing_bundled_file_falls_back_to_download(self):
        entry = {"id": "ball", "name": "Ball", "category": "shapes", "kind": "bundled", "file": "ball.ply.gz",
                 "sha256": "3" * 64, "bytes": 10, "license": "CC0-1.0", "triangles": 320}
        _index(self.files, [entry])
        c = cat.Catalogue.load(self.folder, files=self.files)
        self.assertEqual(c.status(c.get("ball")), "remote")

    def test_folder_comes_from_the_option(self):
        from atlas_toolkit.core.settings import AppSettings
        s = AppSettings(self.tmp / "cfg")
        s.set("catalogue_dir", str(self.tmp / "mine"))
        self.assertEqual(cat.Catalogue.load(settings=s).folder, self.tmp / "mine")
        s.set("catalogue_dir", "")
        self.assertEqual(cat.Catalogue.load(settings=s).folder, paths.catalogue_dir())

    def test_names_are_safe_everywhere(self):
        self.assertEqual(cat.safe_name('Head: "A/B"?'), "Head AB")
        self.assertEqual(cat.safe_name("..."), "model")


if __name__ == "__main__":
    unittest.main()
