#!/usr/bin/env python3
"""
check_frozen_gui.py - Does a built app really draw? Starts it under the Qt platform of this environment (windows, xcb,
wayland, cocoa), opens a mesh tool on a small generated model, and counts the colours in the 3D pane. A screenshot that
merely exists proves nothing: a broken OpenGL stack leaves the page looking right, with an empty pane.

    python scripts/check_frozen_gui.py --exe PATH [--out DIR] [--soft] [--software]

--soft reports a blank pane without failing: GitHub's macOS runners have no GPU, so there the check only proves that the
app starts, opens the page and takes the snapshot. --software makes Qt on Windows use its bundled renderer, as it does by
itself on a machine with no usable graphics driver, so any machine can prove that case. Standard library only: it also runs
in clean containers.
"""
from __future__ import annotations

import argparse
import math
import os
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

# The middle of the 3D view in a tool page's snapshot: x0, y0, x1, y1 of the window. Tight on purpose: on a small screen the
# page's text comes close to the pane, and antialiased text alone has hundreds of colours.
PREVIEW_PANE = (0.50, 0.45, 0.70, 0.78)
MIN_COLOURS = 64                           # an empty pane has a handful; a shaded model on its grid has hundreds


def write_sphere(path: Path, levels: int = 3) -> None:
    """A subdivided octahedron as a binary STL, so that no mesh library is needed to make it."""
    corners = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]
    faces = [(0, 2, 4), (2, 1, 4), (1, 3, 4), (3, 0, 4), (2, 0, 5), (1, 2, 5), (3, 1, 5), (0, 3, 5)]
    triangles = [tuple(corners[i] for i in face) for face in faces]

    def middle(a, b):
        m = [(x + y) / 2 for x, y in zip(a, b)]
        scale = 1 / math.sqrt(sum(v * v for v in m))
        return tuple(v * scale for v in m)

    for _ in range(levels):
        triangles = [t for a, b, c in triangles
                     for t in ((a, middle(a, b), middle(c, a)), (middle(a, b), b, middle(b, c)),
                               (middle(c, a), middle(b, c), c), (middle(a, b), middle(b, c), middle(c, a)))]
    with path.open("wb") as f:
        f.write(b"\0" * 80 + struct.pack("<I", len(triangles)))
        for triangle in triangles:
            f.write(struct.pack("<3f", 0, 0, 0))
            for vertex in triangle:
                f.write(struct.pack("<3f", *(20 * v for v in vertex)))
            f.write(b"\0\0")


def png_colours(path: Path, box: tuple = PREVIEW_PANE) -> int:
    """Distinct colours inside `box` of an 8-bit, non-interlaced RGB or RGBA PNG (what Qt writes)."""
    data = path.read_bytes()
    pos, idat, width, height, channels = 8, [], 0, 0, 0
    while pos < len(data):
        size, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + size]
        if kind == b"IHDR":
            width, height, depth, colour, _, _, interlace = struct.unpack(">IIBBBBB", body)
            if depth != 8 or colour not in (2, 6) or interlace:
                raise ValueError(f"unsupported PNG: depth {depth}, colour type {colour}, interlace {interlace}")
            channels = 3 if colour == 2 else 4
        elif kind == b"IDAT":
            idat.append(body)
        pos += 12 + size
    raw = zlib.decompress(b"".join(idat))
    stride = width * channels
    prev, seen, offset = bytearray(stride), set(), 0
    x0, y0, x1, y1 = int(box[0] * width), int(box[1] * height), int(box[2] * width), int(box[3] * height)
    for y in range(height):
        kind, line = raw[offset], bytearray(raw[offset + 1:offset + 1 + stride])
        offset += stride + 1
        if kind == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 255
        elif kind == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif kind == 3:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + (left + prev[i]) // 2) & 255
        elif kind == 4:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        if y0 <= y < y1:
            for x in range(x0, x1):
                seen.add(bytes(line[x * channels:x * channels + 3]))
        prev = line
    return len(seen)


def check(exe: Path, out: Path, soft: bool, software: bool = False) -> int:
    home = out / "home"
    home.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, ATLAS_TOOLKIT_HOME=str(home), PYINSTALLER_SUPPRESS_SPLASH_SCREEN="1")
    if sys.platform != "win32":
        env["HOME"] = str(home)
    if software:
        env["QT_OPENGL"] = "software"
    if env.get("QT_QPA_PLATFORM") == "offscreen":     # it has no OpenGL at all, so the pane would be blank whatever the build is
        del env["QT_QPA_PLATFORM"]
    sample, shot = out / "sphere.stl", out / "mesh.png"
    write_sphere(sample)
    shot.unlink(missing_ok=True)
    try:
        done = subprocess.run([str(exe), "--tool", "mesh-twist", str(sample), "--draft", "--snapshot", str(shot)],
                              env=env, timeout=240, capture_output=True, text=True, errors="replace")
    except subprocess.TimeoutExpired:
        print("FAIL frozen 3D view: the app did not finish within 240 s")
        return 1
    if done.returncode or not shot.is_file():
        print(f"FAIL frozen 3D view: exit code {done.returncode}, snapshot present: {shot.is_file()}")
        print(done.stderr[:1500])         # the start too: an uncaught NSException says why there, above its long stack
        print(done.stderr[1500:][-1500:])
        return 1
    colours = png_colours(shot)
    if colours >= MIN_COLOURS:
        print(f"ok frozen 3D view: {colours} colours in the pane ({shot})")
        return 0
    print(f"{'note' if soft else 'FAIL'} frozen 3D view: blank, {colours} colours in the pane ({shot})")
    print(done.stderr[-1500:])
    return 0 if soft else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exe", type=Path, required=True)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--soft", action="store_true", help="report a blank pane without failing")
    ap.add_argument("--software", action="store_true", help="Windows: draw with Qt's bundled software renderer")
    a = ap.parse_args()
    if a.out:
        a.out.mkdir(parents=True, exist_ok=True)
        return check(a.exe.resolve(), a.out.resolve(), a.soft, a.software)
    with tempfile.TemporaryDirectory(prefix="atlas_frozen_gl_") as folder:
        return check(a.exe.resolve(), Path(folder), a.soft, a.software)


if __name__ == "__main__":
    sys.exit(main())
