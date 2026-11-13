"""A stand-in for ml/depth_server.py that speaks its protocol without torch: a first-use "download" (a file that
grows in FAKE_DEPTH_FOLDER, reported by the real server's DownloadReport), then a flat depth map.

    FAKE_DEPTH=download   not cached: the download grows to 1 of 2 MB, finishes, then the depth is written
    FAKE_DEPTH=cached     already downloaded: no download messages
    FAKE_DEPTH=fail       the download fails half-way, as Hugging Face does offline
    FAKE_DEPTH=hang       the download stops growing (and never ends)
"""
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

spec = importlib.util.spec_from_file_location(
    "depth_server", Path(__file__).resolve().parents[2] / "src" / "atlas_toolkit" / "ml" / "depth_server.py")
ds = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ds)

MODE = os.environ.get("FAKE_DEPTH", "download")
FOLDER = Path(os.environ["FAKE_DEPTH_FOLDER"])

ds.send({"ready": True, "device": "fake"})
for line in sys.stdin:
    req = json.loads(line)
    if req.get("quit"):
        break
    rid = req["id"]
    if req.get("op") == "cached":
        ds.send({"id": rid, "ok": True, "cached": MODE == "cached", "model": "Fake Model", "total_mb": 2.0,
                 "folder": str(FOLDER)})
        continue
    try:
        if MODE != "cached":
            with ds.DownloadReport(lambda d, rid=rid: ds.send({"id": rid, "progress": 0.0, "download": d}),
                                   "Fake Model", FOLDER, 2.0, every=0.05):
                (FOLDER / "blobs").mkdir(parents=True, exist_ok=True)
                with open(FOLDER / "blobs" / f"blob.{os.getpid()}.incomplete", "ab") as f:
                    for i in range(10):
                        f.write(b"x" * 100_000)
                        f.flush()
                        time.sleep(0.04)
                        if MODE == "fail" and i == 4:
                            raise OSError("We couldn't connect to 'https://huggingface.co' to load the files")
                if MODE == "hang":
                    time.sleep(60)
        h, w = req["size"]
        Path(req["out"]).parent.mkdir(parents=True, exist_ok=True)
        np.save(req["out"], np.zeros((h, w), np.float32))
        ds.send({"id": rid, "ok": True, "out": req["out"], "device": "fake", "model": "Fake Model", "runs": [],
                 "notes": [], "dtype": "fp32"})
    except Exception as e:
        ds.send({"id": rid, "ok": False, "error": f"{type(e).__name__}: {e}"})
