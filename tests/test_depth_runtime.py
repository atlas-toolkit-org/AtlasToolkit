"""The depth environment's installer without downloading anything: GPU detection, the build each card gets, the plan's
checks, and the whole install against a stand-in uv (tests/fixtures/fake_uv.py). The real download is tested only for
releases, by scripts/dev/depth_install_test.py."""
import _isolate  # noqa: F401

import hashlib
import io
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from atlas_toolkit.core import gpu
from atlas_toolkit.core import runtimes
from atlas_toolkit.core.paths import venv_python
from atlas_toolkit.geom import depth_runtime as dr

FAKE_UV = Path(__file__).parent / "fixtures" / "fake_uv.py"


def nvidia(name="RTX", driver="595.71", compute=(12, 0), memory=8151):
    return gpu.GpuReport("nvidia", [gpu.Gpu(name, driver, memory, compute)], "nvidia-smi")


class Settings:
    def __init__(self, **values):
        self.values, self.saved = dict(values), 0

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value

    def save(self):
        self.saved += 1


class Recorder(dr.Observer):
    def __init__(self, cancel_at=None, cancelled=None):
        self.events, self.lines, self.progress_values = [], [], []
        self.cancel_at, self.cancelled = cancel_at, cancelled

    def stage(self, index, state, note=""):
        self.events.append((index, state))
        if self.cancel_at == (index, state):
            self.cancelled.set()

    def progress(self, done, total, rate, eta):
        self.progress_values.append((done, total))

    def log(self, line):
        self.lines.append(line)


class Detection(unittest.TestCase):
    def test_parse_nvidia_smi(self):
        out = "NVIDIA GeForce RTX 5070 Laptop GPU, 595.71, 8151, 12.0\nTesla, Inc. card, 535.10, 16000, 7.0\n"
        cards = gpu.parse_smi(out)
        self.assertEqual(cards[0], gpu.Gpu("NVIDIA GeForce RTX 5070 Laptop GPU", "595.71", 8151, (12, 0)))
        self.assertEqual(cards[1].name, "Tesla, Inc. card")       # a comma in the name
        self.assertEqual(cards[0].sm, "sm_120")
        self.assertEqual(gpu.parse_smi("GTX 680, 470.82, 2048", with_compute=False)[0].compute, None)
        self.assertEqual(gpu.parse_smi("garbage\n\nNVIDIA-SMI has failed"), [])

    def test_old_driver_without_compute_field(self):
        calls = []

        def run(exe, fields):
            calls.append(fields)
            if "compute_cap" in fields:
                return 6, 'Field "compute_cap" is not a valid field to query.'
            return 0, "GeForce GTX 680, 470.82, 2048"

        with mock.patch.object(gpu, "smi_candidates", return_value=[Path(sys.executable)]):
            cards, exe, problem = gpu.from_smi(run)
        self.assertEqual(len(calls), 2)
        self.assertEqual((cards[0].driver, cards[0].compute), ("470.82", None))

    def test_driver_that_does_not_answer(self):
        with mock.patch.object(gpu, "smi_candidates", return_value=[Path(sys.executable)]):
            cards, exe, problem = gpu.from_smi(lambda e, f: (9, "NVIDIA-SMI has failed because it couldn't communicate "
                                                                "with the NVIDIA driver."))
        self.assertEqual(cards, [])
        self.assertIn("couldn't communicate", problem)

    def test_windows_driver_version(self):
        self.assertEqual(gpu.windows_driver("32.0.15.9571"), "595.71")
        self.assertEqual(gpu.windows_driver("31.0.15.2849"), "528.49")
        self.assertEqual(gpu.windows_driver("1.2"), "")

    def test_card_without_a_working_driver(self):
        smi = lambda: ([], "", "not found")  # noqa: E731
        hardware = lambda: ([gpu.Gpu("GeForce GTX 1080", source="registry")], ["Intel UHD"], "no driver")  # noqa: E731
        with mock.patch.object(gpu.sys, "platform", "win32"):
            r = gpu.detect(smi, hardware)
        self.assertEqual(r.status, "no-driver")
        self.assertIn("GTX 1080", r.summary())
        with mock.patch.object(gpu.sys, "platform", "linux"):
            r = gpu.detect(smi, lambda: ([], ["AMD graphics"], ""))
        self.assertEqual(r.status, "none")
        self.assertIn("AMD", r.summary())
        with mock.patch.object(gpu.sys, "platform", "darwin"):
            self.assertEqual(gpu.detect(smi, hardware).status, "apple")

    def test_linux_pci(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for name, vendor, cls in (("0000_01_00.0", "0x10de", "0x030000"), ("0000_00_02.0", "0x8086", "0x030000"),
                                      ("0000_00_1f.0", "0x8086", "0x060100")):     # real names have colons: not on Windows
                dev = root / name
                dev.mkdir()
                (dev / "vendor").write_text(vendor, encoding="ascii")
                (dev / "device").write_text("0x2d18", encoding="ascii")
                (dev / "class").write_text(cls, encoding="ascii")
            found = gpu.linux_adapters(root)
        self.assertEqual([a["vendor"] for a in found], ["0x8086", "0x10de"])     # the bridge is not a display

    def test_the_best_card_decides(self):
        r = gpu.GpuReport("nvidia", [gpu.Gpu("old", "595.0", 4000, (6, 1)), gpu.Gpu("new", "595.0", 8000, (8, 6))])
        self.assertEqual(r.best().name, "new")


class Choosing(unittest.TestCase):
    def choose(self, report, os_key="win32"):
        with mock.patch.object(dr, "os_key", return_value=os_key):
            return dr.choose(report)

    def test_the_table(self):
        cases = [(nvidia(), "cu130"),                                           # Blackwell, new driver
                 (nvidia(compute=(7, 5), driver="560.94"), "cu126"),            # Turing, driver too old for 13.0
                 (nvidia(compute=(6, 1)), "cu126"),                             # Pascal: 13.0 dropped it
                 (nvidia(compute=(12, 0), driver="575.0"), "cpu"),              # Blackwell, 12.6 has no sm_120
                 (nvidia(compute=(3, 5)), "cpu"),                               # Kepler: nothing supports it
                 (nvidia(compute=None, driver="470.82"), "cpu"),                # too old to say its compute capability
                 (gpu.GpuReport("no-driver", [gpu.Gpu("GTX")]), "cpu"),
                 (gpu.GpuReport("none"), "cpu")]
        for report, build in cases:
            with self.subTest(report=report.gpus):
                self.assertEqual(self.choose(report).build, build)
        self.assertEqual(self.choose(gpu.GpuReport("apple"), "darwin").build, "mac")

    def test_reasons_and_advice(self):
        c = self.choose(nvidia(compute=(12, 0), driver="575.0"))
        self.assertIn("580", c.advice)
        self.assertEqual(c.level, "warn")
        c = self.choose(nvidia(compute=(7, 5), driver="560.94"))
        self.assertIn("580", c.reason + c.advice)
        self.assertIn("optional", self.choose(gpu.GpuReport("none")).reason)
        self.assertIn("driver", self.choose(gpu.GpuReport("no-driver", [gpu.Gpu("GTX")])).advice)

    def test_fallbacks(self):
        with mock.patch.object(dr, "os_key", return_value="win32"):
            self.assertEqual(dr.fallbacks("cu130", nvidia(compute=(7, 5))), ["cu126", "cpu"])
            self.assertEqual(dr.fallbacks("cu130", nvidia()), ["cpu"])
            self.assertEqual(dr.fallbacks("cpu", nvidia()), [])


class LockFile(unittest.TestCase):
    def test_every_pin_is_exact_and_every_platform_has_uv(self):
        L = dr.lock()
        for key in ("win32-x86_64", "linux-x86_64", "darwin-arm64"):
            f = L["uv"]["files"][key]
            self.assertRegex(f["sha256"], r"^[0-9a-f]{64}$")
            self.assertIn(L["uv"]["version"], f["url"])
            self.assertGreater(f["bytes"], 1_000_000)
        for p in L["packages"]:
            self.assertRegex(p, r"^[A-Za-z0-9_.-]+==[0-9][0-9.]*$")
        self.assertRegex(L["torch"], r"^\d+\.\d+\.\d+$")
        self.assertRegex(L["python"], r"^3\.\d+$")
        for os_key in ("win32", "linux", "darwin"):
            keys = [k for k, b in L["builds"].items() if os_key in b["platforms"]]
            self.assertTrue(keys, os_key)
            for k in keys:
                b = L["builds"][k]
                self.assertIn(os_key, b["download"])
                self.assertIn(os_key, b["installed"])
                if b["gpu"]:
                    self.assertIn(os_key, b["min_driver"])
                    self.assertTrue(b["index"].startswith("https://download.pytorch.org/whl/"))

    def test_the_server_knows_the_default_model(self):
        self.assertIn(dr.lock()["model"]["key"], dr.server_models())

    def test_runtime_contract(self):
        rt = runtimes.get("depth")
        mod = rt.installer_module()
        for name in runtimes.INSTALLER_API:
            self.assertTrue(callable(getattr(mod, name, None)), name)
        self.assertEqual(rt.commands, "atlas-toolkit components install depth")


class Planning(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="atlas_depth_plan_"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def plan(self, **kw):
        return dr.plan(kw.pop("build", "auto"), report=kw.pop("report", gpu.GpuReport("none")), online=False,
                       target=self.dir / "ml-runtime", **kw)

    def test_summary_says_what_where_how_long(self):
        p = self.plan()
        rows = dict(p.summary())
        for head in ("Build", "Installs", "Download", "Disk", "Time", "Folder", "Models"):
            self.assertIn(head, rows)
        lo, hi = p.minutes()
        self.assertLessEqual(lo, hi)
        self.assertTrue(p.ok())
        self.assertEqual([k for k, _ in p.stages()], ["uv", "python", "venv", "torch", "packages", "check"])
        self.assertEqual(self.plan(with_model=True).stages()[-1][0], "model")
        self.assertGreater(self.plan(with_model=True).sizes()["download"], p.sizes()["download"] + 1000)

    def test_a_full_disk_refuses(self):
        with mock.patch.object(dr.shutil, "disk_usage", return_value=shutil._ntuple_diskusage(10**12, 10**12 - 10**8, 10**8)):
            p = self.plan()
        self.assertFalse(p.ok())
        self.assertIn("Free some space", next(c.text for c in p.checks if c.title == "Disk space"))

    def test_another_environment_is_named(self):
        with mock.patch.dict(os.environ, {dr.ENV: "/somewhere/python"}):
            p = self.plan(settings=Settings(ml_python=str(self.dir / "mine" / "python")))
        texts = " ".join(c.text for c in p.checks if c.title == "Another environment")
        self.assertIn(dr.ENV, texts)
        self.assertIn("located", texts)
        self.assertTrue(p.clear_setting)

    def test_a_build_by_hand(self):
        with mock.patch.object(dr, "os_key", return_value="linux"):
            p = self.plan(build="cpu", report=nvidia())
        self.assertEqual(p.build, "cpu")
        self.assertIn("by hand", p.choice.reason)
        with self.assertRaises(ValueError):
            self.plan(build="nonsense")

    def test_hosts_follow_the_build(self):
        with mock.patch.object(dr, "os_key", return_value="darwin"):
            self.assertNotIn("download.pytorch.org", dr.hosts(self.plan(report=gpu.GpuReport("apple"))))
        self.assertIn("huggingface.co", dr.hosts(self.plan(with_model=True)))


class Installing(unittest.TestCase):
    """The whole install, against the stand-in uv: a real venv, stub packages, the real probe and swap."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="atlas_depth_install_"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        patcher = mock.patch.object(dr, "UV_COMMAND", [sys.executable, str(FAKE_UV)])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.env = mock.patch.dict(os.environ, {"FAKE_UV_FAIL": "", "FAKE_UV_SLOW": "", "FAKE_TORCH_CUDA": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.target = self.dir / "ml-runtime"

    def plan(self, report=None, build="auto", settings=None):
        with mock.patch.object(dr, "os_key", return_value="linux" if sys.platform != "darwin" else "darwin"):
            return dr.plan(build, report=report or gpu.GpuReport("none"), online=False, target=self.target,
                           settings=settings)

    def install(self, plan, **kw):
        rec = kw.pop("observer", None) or Recorder()
        with mock.patch.object(dr, "os_key", return_value="linux" if sys.platform != "darwin" else "darwin"):
            return dr.install(plan, rec, **kw), rec

    def test_installs_checks_and_puts_in_place(self):
        settings = Settings(ml_python=str(self.dir / "elsewhere" / "python"))
        released = []
        result, rec = self.install(self.plan(settings=settings), release=lambda: released.append(1), settings=settings)
        self.assertTrue(result.ok, result.lines())
        self.assertTrue(venv_python(self.target).exists())
        self.assertFalse((self.dir / "ml-runtime.partial").exists())
        self.assertFalse((self.dir / "ml-runtime-base" / "cache").exists(), "the download cache is cleaned up")
        stamp = json.loads((self.target / dr.STAMP).read_text(encoding="utf-8"))
        self.assertEqual((stamp["build"], stamp["revision"]), (result.build, dr.lock()["revision"]))
        self.assertTrue(stamp["torch"].startswith(dr.lock()["torch"]))
        self.assertEqual(released, [1])
        self.assertEqual(settings.get("ml_python"), "")
        self.assertEqual([s for i, s in rec.events if s == "done"], ["done"] * 6)
        done = [d for d, _ in rec.progress_values]
        self.assertEqual(done, sorted(done), "progress never goes back")
        self.assertTrue(result.log_path.is_file())
        self.assertIn("Installed.", " ".join(result.lines()))
        self.assertTrue(dr.stamp(self.target))

        again, _ = self.install(self.plan())                      # over itself: the old one is swapped out
        self.assertTrue(again.ok, again.lines())
        self.assertTrue(again.replaced)
        self.assertFalse((self.dir / "ml-runtime.old").exists())

    def test_a_failed_download_keeps_the_old_environment(self):
        first, _ = self.install(self.plan())
        self.assertTrue(first.ok)
        os.environ["FAKE_UV_FAIL"] = "torch:error: Failed to fetch: `https://download.pytorch.org/whl/cpu/torch/` dns error"
        result, rec = self.install(self.plan())
        self.assertFalse(result.ok)
        self.assertEqual(result.error.kind, "network")
        self.assertTrue(result.previous_kept)
        self.assertIn("still in place", " ".join(result.lines()))
        self.assertTrue(venv_python(self.target).exists())
        self.assertIn((3, "failed"), rec.events)

    def test_a_download_that_breaks_off_is_tried_again(self):
        os.environ["FAKE_UV_FAIL_ONCE"] = ("torch:error: Failed to download `nvidia-cufft==12.0.0.61`\n  cause: Failed to "
                                           "download distribution due to network timeout.")
        self.addCleanup(os.environ.pop, "FAKE_UV_FAIL_ONCE", None)
        result, rec = self.install(self.plan())
        self.assertTrue(result.ok, result.lines())
        self.assertTrue(any("trying again" in line for line in rec.lines))

    def test_failure_kinds(self):
        for output, kind in (("error: No space left on device (os error 28)", "disk"),
                             ("error: invalid peer certificate: UnknownIssuer", "tls"),
                             ("The process cannot access the file because it is being used by another process. (os error 32)", "locked"),
                             ("something new", "other")):
            with self.subTest(kind=kind):
                os.environ["FAKE_UV_FAIL"] = f"python install:{output}"
                result, _ = self.install(self.plan())
                self.assertEqual(result.error.kind, kind)
                self.assertTrue(result.error.hint)
                self.assertFalse(venv_python(self.target).exists())

    def test_a_gpu_build_that_cannot_see_the_card_is_not_put_in_place(self):
        os.environ["FAKE_TORCH_CUDA"] = "broken"
        result, _ = self.install(self.plan(report=nvidia(), build="cu130" if sys.platform != "darwin" else "mac"))
        if sys.platform == "darwin":
            self.skipTest("macOS has no CUDA builds")
        self.assertFalse(result.ok)
        self.assertEqual(result.error.kind, "gpu")
        self.assertIn("cpu", result.fallbacks)
        self.assertFalse(venv_python(self.target).exists())
        self.assertFalse((self.dir / "ml-runtime.partial").exists())
        os.environ["FAKE_TORCH_CUDA"] = "1"
        result, _ = self.install(self.plan(report=nvidia(), build="cu130"))
        self.assertTrue(result.ok, result.lines())
        self.assertEqual(result.probe["sm"], "sm_120")

    def test_cancel_removes_what_was_begun(self):
        os.environ["FAKE_UV_SLOW"] = "torch"
        cancelled = threading.Event()
        rec = Recorder(cancel_at=(3, "running"), cancelled=cancelled)
        result, _ = self.install(self.plan(), observer=rec, cancelled=cancelled)
        self.assertTrue(result.cancelled)
        self.assertFalse((self.dir / "ml-runtime.partial").exists())
        self.assertIn("Cancelled", result.lines()[0])

    def test_leftovers_are_tidied_and_remove_deletes_everything(self):
        (self.dir / "ml-runtime.partial").mkdir()
        (self.dir / "ml-runtime.old").mkdir()
        result, _ = self.install(self.plan())
        self.assertTrue(result.ok)
        self.assertFalse((self.dir / "ml-runtime.old").exists())
        with mock.patch.object(dr, "ml_runtime_dir", return_value=self.target):
            freed, gone = dr.remove()
        self.assertEqual(len(gone), 2)
        self.assertFalse(self.target.exists())
        self.assertFalse((self.dir / "ml-runtime-base").exists())


class DownloadingUv(unittest.TestCase):
    """The pinned uv: streamed, its SHA-256 checked, the program taken out of the archive."""

    def archive(self, name):
        buf = io.BytesIO()
        if name.endswith(".zip"):
            with zipfile.ZipFile(buf, "w") as z:
                z.writestr("uv.exe", b"MZfake")
        else:
            with tarfile.open(fileobj=buf, mode="w:gz") as t:
                data = b"#!fake"
                info = tarfile.TarInfo("uv-x86_64-unknown-linux-gnu/uv")
                info.size = len(data)
                t.addfile(info, io.BytesIO(data))
        return buf.getvalue()

    def run_download(self, data, sha):
        info = {"url": "https://example.invalid/uv" + (".zip" if sys.platform == "win32" else ".tar.gz"),
                "sha256": sha, "bytes": len(data)}
        lock = dict(dr.lock(), uv=dict(dr.lock()["uv"], files={dr.platform_key(): info}))
        with tempfile.TemporaryDirectory() as d, mock.patch.object(dr, "lock", return_value=lock), \
                mock.patch.object(dr, "open_url", return_value=io.BytesIO(data)):
            f = dr.folders(Path(d) / "ml-runtime")
            meter = dr.Meter(dr.Observer(), [1.0])
            run = dr.Runner(dr.Observer(), threading.Event(), None)
            dr._download_uv(f, run, meter, 1.0)
            return f.uv.read_bytes(), (f.uv_dir / ".verified").read_text(encoding="ascii")

    def test_good_and_bad_downloads(self):
        data = self.archive("x.zip" if sys.platform == "win32" else "x.tar.gz")
        sha = hashlib.sha256(data).hexdigest()
        program, marker = self.run_download(data, sha)
        self.assertTrue(program.startswith(b"MZ" if sys.platform == "win32" else b"#!"))
        self.assertEqual(marker, sha)
        with self.assertRaises(dr.InstallError) as e:
            self.run_download(data, "0" * 64)
        self.assertEqual(e.exception.kind, "hash")


class Words(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(dr.classify("error: Failed to download `torch`: error sending request")[0], "network")
        self.assertEqual(dr.classify("[SSL: CERTIFICATE_VERIFY_FAILED]")[0], "tls")
        self.assertEqual(dr.classify("OSError: [Errno 28] No space left on device")[0], "disk")

    def test_uv_error_names_the_cause_not_the_hint(self):
        out = ("Downloading nvidia-cufft (204.2MiB)\nerror: Failed to download `nvidia-cufft==12.0.0.61`\n"
               "  cause: Failed to extract archive: x.whl\n  cause: Failed to download distribution due to network "
               "timeout.\n\nhint: `nvidia-cufft` was included because `torch` depends on it")
        msg = dr.uv_error(out, 1)
        self.assertTrue(msg.startswith("Failed to download `nvidia-cufft"), msg)
        self.assertIn("network timeout", msg)
        self.assertNotIn("hint", msg)
        self.assertEqual(dr.uv_error("", 3), "exit code 3")

    def test_gb(self):
        self.assertEqual(dr.gb(2150), "2.1 GB")
        self.assertEqual(dr.gb(300), "300 MB")
        self.assertTrue(re.match(r"\d", dr.gb(1024)))


if __name__ == "__main__":
    unittest.main()
