"""How fast is the face-paint dialog (Surface Eject, Motion Sculpture, Crumble, Stairs) on big meshes?

Opens the real dialog in a real window on generated spheres (or your own models) and times what a user feels:

- open: building the dialog until its first frame is on screen;
- dab: one click of the brush in the middle, until the new colours are on screen (the first one after the dialog
  opens, and one after turning the model);
- stroke: 30 mouse moves arriving every 8 ms, until the last of them is painted and drawn;
- frozen: the longest gap of a 16 ms heartbeat timer during the stroke, i.e. how long the window stopped answering.

    .venv\\Scripts\\python scripts\\dev\\paint_perf.py [--sizes 20k,80k,330k,1.3m] [--model FILE ...] [--tag NAME]
    [--slow 20]   skip the stroke when one dab takes longer than this many seconds (the old dialog took minutes)

Needs a working OpenGL window (not the offscreen platform). Writes build/qa/paint_perf/<tag>.json and .md.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from atlas_toolkit.devpaths import qa_dir  # noqa: E402

SUBDIVISIONS = {"5k": 4, "20k": 5, "80k": 6, "330k": 7, "1.3m": 8, "5m": 9}


def sphere(size):
    import trimesh
    return f"sphere {size}", trimesh.creation.icosphere(subdivisions=SUBDIVISIONS[size])


def model(path):
    import trimesh
    from atlas_toolkit.geom import frame3d, mesh as gm
    original = gm.load_mesh(path)
    frame = frame3d.fit_frame(original.vertices, "+z", "-y", (0., 0., 0.), mode="height", size=2.)
    return Path(path).name, trimesh.Trimesh(frame.apply(original.vertices), original.faces, process=False)


class Probe:
    """Heartbeat, frame and mouse-event bookkeeping around one dialog."""

    def __init__(self, app, canvas):
        from PySide6.QtCore import QTimer
        self.app, self.canvas = app, canvas
        self.frames = 0
        canvas.frameSwapped.connect(self._frame)
        self.beats = []
        self.heart = QTimer()
        self.heart.setInterval(16)
        self.heart.timeout.connect(lambda: self.beats.append(time.perf_counter()))

    def _frame(self):
        self.frames += 1

    def idle(self, dialog):
        busy = getattr(dialog, "busy", None)
        return not (busy and busy())

    def settle(self, dialog, frames_from, limit=600.):
        """Run the event loop until the dialog has nothing pending, then until one more frame is on screen (so any
        colours still waiting to be drawn are in it)."""
        start = time.perf_counter()
        target = None
        while time.perf_counter() - start < limit:
            self.app.processEvents()
            if target is None and self.idle(dialog):
                target = max(self.frames, frames_from)
                self.canvas.update()
            if target is not None and self.frames > target:
                return time.perf_counter()
            time.sleep(.0005)
        raise TimeoutError("the dialog did not settle")

    def mouse(self, kind, x, y, pressed=True):
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        types = {"press": QEvent.MouseButtonPress, "move": QEvent.MouseMove, "release": QEvent.MouseButtonRelease}
        button = Qt.LeftButton if kind != "move" else Qt.NoButton
        buttons = Qt.LeftButton if pressed and kind != "release" else Qt.NoButton
        local = QPointF(x, y)
        glob = QPointF(self.canvas.mapToGlobal(local.toPoint()))
        self.app.postEvent(self.canvas, QMouseEvent(types[kind], local, glob, button, buttons, Qt.NoModifier))


def dab(probe, dialog, x, y):
    before = probe.frames
    start = time.perf_counter()
    probe.mouse("press", x, y)
    probe.mouse("release", x, y, pressed=False)
    return probe.settle(dialog, before) - start


def stroke(probe, dialog, x, y, length, moves=30, every=.008):
    from PySide6.QtCore import QTimer
    points = [(x - length / 2 + length * i / (moves - 1), y + 0.2 * length * ((i % 6) - 3) / 3) for i in range(moves)]
    probe.mouse("press", *points[0])
    state = {"i": 1}

    def next_move():
        i = state["i"]
        if i < len(points):
            probe.mouse("move", *points[i])
            state["i"] += 1
        else:
            timer.stop()
            probe.mouse("release", *points[-1], pressed=False)
            state["done"] = True

    timer = QTimer()
    timer.setInterval(int(every * 1000))
    timer.timeout.connect(next_move)
    probe.beats.clear()
    start = time.perf_counter()
    probe.heart.start()
    timer.start()
    while not state.get("done"):
        probe.app.processEvents()
        time.sleep(.0005)
    end = probe.settle(dialog, probe.frames)
    probe.heart.stop()
    beats = [start] + probe.beats + [end]
    gaps = [b - a for a, b in zip(beats, beats[1:])]
    return end - start, max(gaps)


def measure(app, theme, name, mesh, slow):
    import numpy as np
    from atlas_toolkit.tools.mesh_surface_eject.viewer import SelectionDialog
    row = {"model": name, "faces": int(len(mesh.faces))}
    mesh.triangles_center   # the caller's mesh comes cached as from load_mesh in the app
    start = time.perf_counter()
    dialog = SelectionDialog(mesh, mesh, np.zeros(len(mesh.faces), bool), theme)
    dialog.resize(1050, 760)
    probe = Probe(app, dialog.canvas)
    dialog.show()
    row["open_s"] = probe.settle(dialog, 0) - start
    w, h = dialog.canvas.width(), dialog.canvas.height()
    row["dab_s"] = dab(probe, dialog, w / 2, h / 2)
    row["selected_after_dab"] = int(dialog.selected.sum())
    if row["dab_s"] > slow:
        row["note"] = f"one dab took over {slow:g} s: stroke skipped"
    else:
        row["stroke_s"], row["frozen_s"] = stroke(probe, dialog, w / 2, h / 2 + 60, w / 3)
        dialog.canvas.setCameraPosition(azimuth=dialog.canvas.opts["azimuth"] + 40)
        row["turned_dab_s"] = dab(probe, dialog, w / 2 - 40, h / 2 - 40)
    row["selected"] = int(dialog.selected.sum())
    dialog.close()
    dialog.deleteLater()
    app.processEvents()
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sizes", default="20k,80k,330k,1.3m", help="comma-separated: " + ",".join(SUBDIVISIONS))
    parser.add_argument("--model", action="append", default=[], help="a mesh file to measure as well")
    parser.add_argument("--tag", default=time.strftime("%Y%m%d-%H%M%S"))
    parser.add_argument("--slow", type=float, default=20.)
    args = parser.parse_args()
    from PySide6.QtWidgets import QApplication
    from atlas_toolkit.ui.theme import Theme
    app = QApplication.instance() or QApplication(sys.argv)
    theme = Theme("dark")
    cases = [sphere(s.strip().lower()) for s in args.sizes.split(",") if s.strip()] + [model(p) for p in args.model]
    rows = []
    for name, mesh in cases:
        print(f"{name}: {len(mesh.faces):,} faces ...", flush=True)
        row = measure(app, theme, name, mesh, args.slow)
        print("  " + ", ".join(f"{k}={v:.3f}" if isinstance(v, float) else f"{k}={v}" for k, v in row.items()
                               if k not in ("model",)), flush=True)
        rows.append(row)
        lines = report(rows, args.tag)
    print("\n".join(lines))
    print("Wrote", qa_dir("paint_perf") / f"{args.tag}.md")


def report(rows, tag):
    out = qa_dir("paint_perf")
    (out / f"{tag}.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    cols = ["model", "faces", "open_s", "dab_s", "stroke_s", "frozen_s", "turned_dab_s", "selected", "note"]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for row in rows:
        lines.append("| " + " | ".join(f"{row[c]:.3f}" if isinstance(row.get(c), float) else str(row.get(c, ""))
                                       for c in cols) + " |")
    (out / f"{tag}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lines


if __name__ == "__main__":
    main()
