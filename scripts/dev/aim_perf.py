"""How quickly does the Aim tab answer while you aim? (Contour Lines, Sketch Hatching, Stipple, FlowRep Sketch, Plotter
Vision, Hatch Maker, and Workflows' Aim in tool…, which all share tools/plotter_vision/viewer.py.)

Opens the real tool page in a real window on generated models (a smooth sphere, then stepped spheres whose every
step is a crease, like Stair Sculpt's output: more triangles and more lines each time) or your own, and times what a
user feels:

- first: from adding the file until its hidden lines are done;
- drag: 120 orbit moves arriving every 4 ms (a 250 Hz mouse). frozen = the longest gap of a 16 ms heartbeat timer
  (how long the window stopped answering), lag = from the last move until a frame showing it is on screen,
  fps = frames drawn while dragging; settle = from letting go until the hidden lines are done, with its own frozen;
- pan: the same with Shift; wheel: 10 notches 30 ms apart, then settle;
- redrag: drag again while the lines of the last stop are still filling in;
- place: a new Up axis (tools whose Aim places the model in a frame), until the lines are done; it includes the
  page's own pause before settings apply (Setup › Preview delay);
- computes: hidden-line runs started per rest (one each is right).

A row passes when no freeze is over 100 ms, the lag is under 100 ms, and a light trace of up to 25 000 faces settles
within 2 s (bigger ones are only reported). Display scaling matters a lot (Qt draws thick lines differently above
100 %): --scale sets QT_SCALE_FACTOR so a 100 % machine can try a HiDPI one; the device pixel ratio used is reported.

    .venv\\Scripts\\python scripts\\dev\\aim_perf.py [--models sphere,s20,s60,s120,s200] [--model FILE ...]
        [--tool contour-lines|plotter-vision|stl-hatch-maker] [--exact] [--scale 1.75] [--tag NAME]
        [--compare TAG] [--limit 120]

Needs a real window (not the offscreen platform). Settings go to a temporary folder. Writes
build/qa/aim_perf/<tag>.json and .md, and the generated models to build/qa/aim_perf/models.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

GENERATED = {"sphere": None, "s20": (20, 48), "s60": (60, 96), "s120": (120, 192), "s200": (200, 320)}
FROZEN_MS = 100.0
LAG_MS = 100.0
SETTLE_S = 2.0
LIGHT_FACES = 25_000


def generated(name: str, folder: Path) -> Path:
    """A smooth sphere, or a sphere of stacked cylinders (steps x sections): nearly every edge a crease."""
    import numpy as np
    import trimesh
    path = folder / f"{'sphere' if name == 'sphere' else 'stepped_' + name}.stl"
    if path.exists():
        return path
    if name == "sphere":
        mesh = trimesh.creation.icosphere(subdivisions=5, radius=50.0)
    else:
        steps, sections = GENERATED[name]
        parts, r0, h = [], 50.0, 100.0 / steps
        for i in range(steps):
            z = -r0 + h * (i + 0.5)
            c = trimesh.creation.cylinder(radius=max(float(np.sqrt(max(r0 * r0 - z * z, 0.0))), 1.0), height=h,
                                          sections=sections)
            c.apply_translation((0.0, 0.0, z))
            parts.append(c)
        mesh = trimesh.util.concatenate(parts)
    mesh.export(path)
    return path


class Probe:
    """Heartbeat, frame and event bookkeeping around the Aim widget."""

    def __init__(self, app, viewer_cls):
        from PySide6.QtCore import QTimer
        self.app = app
        self.frames: list = []            # (time the frame was drawn, moves handled before it)
        self.moves = 0
        self.computes = 0
        self.beats: list = []
        self.heart = QTimer()
        self.heart.setInterval(16)
        self.heart.timeout.connect(lambda: self.beats.append(time.perf_counter()))
        probe = self
        paint, move, compute = viewer_cls.paintEvent, viewer_cls.mouseMoveEvent, viewer_cls._compute

        def paint_event(self, e):
            paint(self, e)
            probe.frames.append((time.perf_counter(), probe.moves))

        def mouse_move(self, e):
            probe.moves += 1
            move(self, e)

        def count_compute(self, *a, **k):
            probe.computes += 1
            return compute(self, *a, **k)

        viewer_cls.paintEvent, viewer_cls.mouseMoveEvent, viewer_cls._compute = paint_event, mouse_move, count_compute

    def pump(self, seconds: float) -> None:
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            self.app.processEvents()
            time.sleep(0.0003)

    def wait(self, cond, limit: float) -> bool:
        end = time.perf_counter() + limit
        while time.perf_counter() < end:
            self.app.processEvents()
            if cond():
                return True
            time.sleep(0.0003)
        return False

    def watch(self):
        self.beats = []
        self.heart.start()
        return time.perf_counter()

    def frozen(self, start: float, end: float) -> float:
        """The longest stretch between start and end in which the heartbeat did not tick (ms)."""
        beats = [start] + [b for b in self.beats if start <= b <= end] + [end]
        return 1000.0 * max(b - a for a, b in zip(beats, beats[1:]))


def post_mouse(app, w, kind, x, y, mods=None):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    types = {"press": QEvent.MouseButtonPress, "move": QEvent.MouseMove, "release": QEvent.MouseButtonRelease}
    local = QPointF(x, y)
    button = Qt.LeftButton if kind != "move" else Qt.NoButton
    held = Qt.LeftButton if kind != "release" else Qt.NoButton
    app.postEvent(w, QMouseEvent(types[kind], local, QPointF(w.mapToGlobal(local.toPoint())), button, held,
                                 mods if mods is not None else Qt.NoModifier))


def post_wheel(app, w, notches):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    local = QPointF(w.width() / 2, w.height() / 2)
    app.postEvent(w, QWheelEvent(local, QPointF(w.mapToGlobal(local.toPoint())), QPoint(), QPoint(0, 120 * notches),
                                 Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False))


def drag(probe, v, shift=False, moves=120, every=0.004, dx=3.0):
    """Posts a drag at mouse speed; returns (start, when the hand stopped, moves handled once the last one is) after
    posting the release. A hand does not wait for the window: when a frame takes long, the moves due meanwhile are
    posted together, as the system would queue them, and lag counts from when the last one was due."""
    from PySide6.QtCore import Qt
    app = probe.app
    x, y = v.width() / 2 - moves * dx / 2, v.height() / 2
    mods = Qt.ShiftModifier if shift else Qt.NoModifier
    post_mouse(app, v, "press", x, y, mods)
    start = time.perf_counter()
    base = probe.moves
    for i in range(1, moves + 1):
        due = start + i * every
        while time.perf_counter() < due:
            app.processEvents()
            time.sleep(0.0002)
        post_mouse(app, v, "move", x + dx * i, y + 0.6 * ((i % 9) - 4), mods)
    post_mouse(app, v, "release", x + dx * moves, y, mods)
    return start, start + moves * every, base + moves


def lag_after(probe, last_post: float, moves_needed: int, limit: float) -> tuple[float, float]:
    """(ms from the last move posted to the first frame drawn after it was handled, time of that frame)."""
    ok = probe.wait(lambda: any(n >= moves_needed for _t, n in probe.frames), limit)
    if not ok:
        return float("inf"), time.perf_counter()
    t = next(t for t, n in probe.frames if n >= moves_needed)
    return 1000.0 * max(t - last_post, 0.0), t


def settle(probe, v, limit):
    """Wait for the hidden lines; returns (seconds, frozen ms, computes started) from now."""
    c0 = probe.computes
    start = probe.watch()
    ok = probe.wait(lambda: v.state == "done" and not v.idle.isActive() and not v.commit.isActive(), limit)
    end = time.perf_counter()
    probe.heart.stop()
    return (end - start if ok else float("inf")), probe.frozen(start, end), probe.computes - c0


def interaction(probe, v, limit, shift=False):
    probe.frames.clear()
    c0 = probe.computes
    start = probe.watch()
    t0, last, needed = drag(probe, v, shift=shift)
    lag, shown = lag_after(probe, last, needed, limit)
    end = time.perf_counter()
    probe.heart.stop()
    frames = sum(1 for t, _n in probe.frames if t0 <= t <= shown)
    out = {"frozen_ms": probe.frozen(start, end), "lag_ms": lag, "fps": frames / max(shown - t0, 1e-6)}
    s, f, _c = settle(probe, v, limit)
    out.update(settle_s=s, settle_frozen_ms=f, computes=probe.computes - c0)   # from the press: one per rest
    return out


def measure(app, win, probe, tool_id, path: Path, exact: bool, limit: float) -> dict:
    page = win.open_tool(tool_id, [str(path)])
    key = str(path.resolve())                          # the page keys its rows by resolved path
    page.queue.setCurrentItem(page.rows[key].item)
    page.stage.show_aim()
    v = page.viewer
    if exact and hasattr(v, "set_exact"):
        v.set_exact(True)
    t0 = time.perf_counter()
    ok = probe.wait(lambda: v.path == key and v.model is not None and v.state == "done", limit)
    res = {"model": path.stem, "tool": tool_id, "exact": exact, "dpr": round(v.devicePixelRatioF(), 3),
           "first_s": time.perf_counter() - t0 if ok else float("inf")}
    if not ok:
        res.update(note=f"no lines within {limit:g} s", why=f"no lines within {limit:g} s")
        res["pass"] = False
        return res
    res.update(light_faces=v.model.faces, edges=v.edges)
    try:
        import trimesh
        res["faces"] = int(len(trimesh.load(str(path), process=False).faces))
    except Exception:
        res["faces"] = None
    from PySide6.QtCore import Qt
    v.setFocus()
    app.sendEvent(v, _key(Qt.Key_F))                        # frame it as a user would
    settle(probe, v, limit)
    res["edges"] = v.edges
    d = interaction(probe, v, limit)
    res.update({f"drag_{k}": x for k, x in d.items()})
    p = interaction(probe, v, limit, shift=True)
    res.update(pan_frozen_ms=p["frozen_ms"], pan_lag_ms=p["lag_ms"])
    app.sendEvent(v, _key(Qt.Key_F))
    settle(probe, v, limit)
    start = probe.watch()
    for i in range(10):
        post_wheel(app, v, 1 if i < 5 else -1)
        probe.pump(0.03)
    end = time.perf_counter()
    probe.heart.stop()
    res["wheel_frozen_ms"] = probe.frozen(start, end)
    s, f, c = settle(probe, v, limit)
    res.update(wheel_settle_s=s, wheel_settle_frozen_ms=max(f, res["wheel_frozen_ms"]), wheel_computes=c)
    # drag again while the last stop's lines are still filling in
    drag(probe, v, moves=30)
    probe.pump(0.30)
    r = interaction(probe, v, limit)
    res.update(redrag_frozen_ms=r["frozen_ms"], redrag_lag_ms=r["lag_ms"])
    if "up_axis" in getattr(v, "MODEL_KEYS", ()):
        current = page.values().get("up_axis", "auto")
        t0 = time.perf_counter()
        page.form.set_values({"up_axis": "+y" if current != "+y" else "+z"})
        page._settings_changed()
        ok = probe.wait(lambda: v.state == "loading", limit)
        start = probe.watch()
        ok = ok and probe.wait(lambda: v.model is not None and v.state == "done", limit)
        end = time.perf_counter()
        probe.heart.stop()
        res["place_s"] = end - t0 if ok else float("inf")
        res["place_frozen_ms"] = probe.frozen(start, end)
        page.form.set_values({"up_axis": current})
        page._settings_changed()
        probe.wait(lambda: v.state == "loading", 2.0)
        probe.wait(lambda: v.model is not None and v.state == "done", limit)
    res.update(verdict(res))
    return res


def _key(k):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    return QKeyEvent(QEvent.KeyPress, k, Qt.NoModifier)


def verdict(r: dict) -> dict:
    why = []
    for k in ("drag_frozen_ms", "drag_settle_frozen_ms", "pan_frozen_ms", "wheel_frozen_ms", "wheel_settle_frozen_ms",
              "redrag_frozen_ms", "place_frozen_ms"):
        if r.get(k, 0.0) > FROZEN_MS:
            why.append(f"{k} {r[k]:.0f}")
    for k in ("drag_lag_ms", "pan_lag_ms", "redrag_lag_ms"):
        if r.get(k, 0.0) > LAG_MS:
            why.append(f"{k} {r[k]:.0f}")
    if (r.get("light_faces") or 0) <= LIGHT_FACES and not r.get("exact"):
        for k in ("drag_settle_s", "wheel_settle_s"):
            if r.get(k, 0.0) > SETTLE_S:
                why.append(f"{k} {r[k]:.2f}")
    return {"pass": not why, "why": "; ".join(why)}


COLS = ["model", "faces", "light_faces", "edges", "dpr", "first_s", "drag_frozen_ms", "drag_lag_ms", "drag_fps",
        "drag_settle_s", "drag_settle_frozen_ms", "pan_frozen_ms", "wheel_frozen_ms", "wheel_settle_s",
        "redrag_frozen_ms", "redrag_lag_ms", "place_s", "drag_computes", "pass"]


def fmt(x) -> str:
    if isinstance(x, bool):
        return "PASS" if x else "FAIL"
    if isinstance(x, float):
        if x == float("inf"):
            return "timeout"
        return f"{x:.0f}" if abs(x) >= 100 else f"{x:.2f}" if abs(x) < 10 else f"{x:.1f}"
    return "" if x is None else str(x)


def report(rows, tag, out: Path) -> list:
    (out / f"{tag}.json").write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")
    lines = ["| " + " | ".join(COLS) + " |", "|" + "---|" * len(COLS)]
    for r in rows:
        lines.append("| " + " | ".join(fmt(r.get(c)) for c in COLS) + " |")
    fails = [f"- {r['model']}: {r['why']}" for r in rows if not r.get("pass") and r.get("why")]
    if fails:
        lines += ["", "Fails:"] + fails
    (out / f"{tag}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lines


def compare(rows, other: Path) -> list:
    before = {(r["model"], r["tool"], r.get("exact")): r for r in json.loads(other.read_text(encoding="utf-8"))}
    keys = ["first_s", "drag_frozen_ms", "drag_lag_ms", "drag_fps", "drag_settle_s", "drag_settle_frozen_ms",
            "wheel_settle_s", "redrag_frozen_ms", "place_s"]
    lines = ["| model | " + " | ".join(keys) + " |", "|" + "---|" * (len(keys) + 1)]
    for r in rows:
        b = before.get((r["model"], r["tool"], r.get("exact")))
        if b is None:
            continue
        cells = [f"{fmt(b.get(k))} → {fmt(r.get(k))}" if k in r or k in b else "" for k in keys]
        lines.append(f"| {r['model']} | " + " | ".join(cells) + " |")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", default="sphere,s20,s60,s120,s200", help="generated: " + ",".join(GENERATED)
                        + " (empty for none)")
    parser.add_argument("--model", action="append", default=[], help="a mesh file to measure as well")
    parser.add_argument("--tool", default="contour-lines")
    parser.add_argument("--exact", action="store_true", help="Plotter Vision's Exact lines instead of the light trace")
    parser.add_argument("--scale", type=float, help="QT_SCALE_FACTOR, on top of the display's own scaling")
    parser.add_argument("--tag", default=time.strftime("%Y%m%d-%H%M%S"))
    parser.add_argument("--compare", help="an earlier tag to print side by side")
    parser.add_argument("--limit", type=float, default=120.0, help="seconds to wait for any one step")
    args = parser.parse_args()

    home = tempfile.mkdtemp(prefix="atlas_aim_perf_")
    os.environ["ATLAS_TOOLKIT_HOME"] = home            # settings, presets and history stay out of the real profile
    os.environ["ATLAS_TOOLKIT_NO_UPDATE_CHECK"] = "1"
    os.environ.pop("ATLAS_TOOLKIT_PRESET_DIRS", None)
    if args.scale:
        os.environ["QT_SCALE_FACTOR"] = f"{args.scale:g}"
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        app = QApplication.instance() or QApplication(sys.argv[:1])
        if app.platformName() in ("offscreen", "minimal"):
            print("aim_perf needs a real window: unset QT_QPA_PLATFORM.")
            return 2
        from atlas_toolkit.core.history import History
        from atlas_toolkit.core.registry import registry
        from atlas_toolkit.core.settings import AppSettings
        from atlas_toolkit.devpaths import qa_dir
        from atlas_toolkit.tools.plotter_vision.viewer import VisionView
        from atlas_toolkit.ui.bridge import JobBridge
        from atlas_toolkit.ui.context import AppContext
        from atlas_toolkit.ui.shell import MainWindow
        from atlas_toolkit.ui.theme import load_fonts
        out = qa_dir("aim_perf")
        folder = out / "models"
        folder.mkdir(exist_ok=True)
        cases = [generated(n.strip(), folder) for n in args.models.split(",") if n.strip()]
        cases += [Path(p).resolve() for p in args.model]
        missing = [str(p) for p in cases if not p.is_file()]
        if missing:
            print("Not found:", ", ".join(missing))
            return 2
        load_fonts()
        app.setStyle("Fusion")
        ctx = AppContext(registry(), AppSettings(Path(home) / "cfg"), JobBridge(), History(Path(home) / "h.jsonl"))
        win = MainWindow(ctx)
        win.resize(1400, 900)
        win.show()
        probe = Probe(app, VisionView)
        rows = []
        for path in cases:
            print(f"{path.name} ...", flush=True)
            r = measure(app, win, probe, args.tool, path, args.exact, args.limit)
            print("  " + ", ".join(f"{k}={fmt(x)}" for k, x in r.items() if k not in ("model", "tool", "exact", "why")),
                  flush=True)
            rows.append(r)
            report(rows, args.tag, out)
        lines = report(rows, args.tag, out)
        print("\n".join(lines))
        if args.compare:
            other = out / f"{args.compare}.json"
            if other.exists():
                print(f"\nBefore ({args.compare}) → after ({args.tag}):")
                print("\n".join(compare(rows, other)))
        print("Wrote", out / f"{args.tag}.md")
        win.close()
        ctx.bridge.shutdown()
        return 0 if all(r.get("pass") for r in rows) else 1
    finally:
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
