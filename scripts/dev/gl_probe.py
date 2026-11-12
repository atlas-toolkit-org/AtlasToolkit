#!/usr/bin/env python3
"""
gl_probe.py - What OpenGL does this machine give Atlas's 3D views? Runs the real MeshView under the real platform
plugin, prints the context it got, compiles every shader program, renders a sphere and saves what came out.

    python scripts/dev/gl_probe.py                    the default context, as the app gets it
    python scripts/dev/gl_probe.py --profile core41   ask for 4.1 core (the macOS fallback)
    python scripts/dev/gl_probe.py --out DIR          default build/qa/gl_probe

Exit code 0: the mesh was drawn. 1: the view is unavailable or blank. Run it on a Mac with a real GPU to settle what
GitHub's runners (no GPU) cannot.
"""
from __future__ import annotations

import argparse
import sys
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def pump(app, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def compile_shaders(names) -> list[str]:
    """Compile each program in the current context; the names that failed."""
    from pyqtgraph.opengl import shaders
    failed = []
    for name in sorted(n for n in names if n):
        try:
            shaders.getShaderProgram(name).program()
            print(f"  shader {name:18} ok")
        except Exception as e:
            failed.append(name)
            print(f"  shader {name:18} FAILED: {str(e).splitlines()[0] if str(e) else type(e).__name__}")
    return failed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", choices=["default", "core41"], default="default")
    ap.add_argument("--out", type=Path, default=ROOT / "build" / "qa" / "gl_probe")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)

    import numpy as np
    from PySide6.QtGui import QImage, QSurfaceFormat
    from PySide6.QtWidgets import QApplication
    if a.profile == "core41":
        fmt = QSurfaceFormat()
        fmt.setVersion(4, 1)
        fmt.setProfile(QSurfaceFormat.CoreProfile)
        fmt.setDepthBufferSize(24)
        QSurfaceFormat.setDefaultFormat(fmt)
    app = QApplication(sys.argv[:1])
    print(f"platform plugin: {app.platformName()}  profile asked: {a.profile}")
    from atlas_toolkit.ui.app import _match_opengl_to_qt
    _match_opengl_to_qt(app)                          # as the app does, before anything imports PyOpenGL

    import trimesh
    from atlas_toolkit.ui.theme import Theme
    from atlas_toolkit.ui.widgets import mesh_view
    from atlas_toolkit.ui.widgets import plane_editor  # noqa: F401 - registers its shader programs too
    if mesh_view.gl is None:
        print(f"RESULT unavailable: importing the GL stack failed: {mesh_view.GL_ERROR}")
        return 1
    widget = mesh_view.MeshView(Theme("dark"))
    if widget.view is None:
        print(f"RESULT unavailable: {widget.msg.text()}")
        return 1
    sphere = trimesh.creation.icosphere(subdivisions=3, radius=20.0)
    data = SimpleNamespace(vertices=sphere.vertices, faces=sphere.faces, edge_segments=None,
                           bounds=tuple(tuple(float(v) for v in row) for row in sphere.bounds))
    widget.resize(640, 480)
    widget.show()
    widget.show_mesh(data)
    pump(app, 1.5)

    view = widget.view
    view.makeCurrent()
    context = view.context()
    if context is None or not context.isValid():
        print("RESULT unavailable: no valid OpenGL context")
        return 1
    fmt = context.format()
    print(f"context: {fmt.majorVersion()}.{fmt.minorVersion()} {fmt.profile().name}  "
          f"depth {fmt.depthBufferSize()}  samples {fmt.samples()}")
    try:
        from OpenGL import GL
        for label, key in (("vendor", GL.GL_VENDOR), ("renderer", GL.GL_RENDERER), ("version", GL.GL_VERSION),
                           ("glsl", GL.GL_SHADING_LANGUAGE_VERSION)):
            value = GL.glGetString(key)
            print(f"  GL {label:9} {value.decode(errors='replace') if value else '(none)'}")
    except Exception:
        traceback.print_exc()
    from pyqtgraph.opengl import shaders
    failed = compile_shaders(shaders.ShaderProgram.names)

    image = view.grabFramebuffer().convertToFormat(QImage.Format_RGB32)
    shot = a.out / f"{a.profile}.png"
    image.save(str(shot))
    bits = np.frombuffer(image.constBits(), np.uint8).reshape(image.height(), image.bytesPerLine())
    pixels = bits[:, :image.width() * 4].reshape(image.height(), image.width(), 4)[:, :, :3]
    drawn = np.any(pixels != pixels[0, 0], axis=2).mean()
    colours = len(np.unique(pixels.reshape(-1, 3), axis=0))
    print(f"image: {image.width()}x{image.height()}, {drawn:.1%} of pixels differ from the background, "
          f"{colours} colours  ({shot})")
    verdict = "rendered" if drawn > 0.02 and colours > 8 else "blank"
    print(f"RESULT {verdict}; shaders failed: {', '.join(failed) if failed else 'none'}")
    widget.close()
    return 0 if verdict == "rendered" else 1


if __name__ == "__main__":
    sys.exit(main())
