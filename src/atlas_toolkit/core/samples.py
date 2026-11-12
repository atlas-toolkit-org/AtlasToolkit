"""Small generated sample inputs per kind, used by the self-test, the contract tests and new-tool templates."""
from __future__ import annotations

from pathlib import Path

SAMPLE_SVG = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
     width="120mm" height="90mm" viewBox="0 0 120 90">
  <g inkscape:groupmode="layer" inkscape:label="pen 1" style="fill:none;stroke:#000;stroke-width:0.3">
    <path id="base" d="M 10 80 H 110"/>
    <path id="r1" d="M 10 80 L 30 80 L 40 30 L 50 80 L 70 80"/>
    <path id="r2" d="M 10 80 L 60 80 L 75 20 L 90 80 L 110 80"/>
    <polyline id="pl" points="20,60 40,60 40,60.02 60,60"/>
    <path id="curve" d="M 15 15 C 40 0 80 30 105 15"/>
    <circle cx="95" cy="45" r="8"/>
    <rect x="12" y="35" width="18" height="12"/>
  </g>
</svg>
"""


def svg(folder: Path, name: str = "sample.svg", text: str = SAMPLE_SVG) -> Path:
    p = Path(folder) / name
    p.write_text(text, encoding="utf-8")
    return p


def mesh(folder: Path, name: str = "sample.stl") -> Path:
    import trimesh
    m = trimesh.creation.icosphere(subdivisions=3, radius=20.0)
    m = m.union(trimesh.creation.box(extents=(18, 18, 50))) if _has_booleans() else m
    p = Path(folder) / name
    m.export(p)
    return p


def _has_booleans() -> bool:
    try:
        import manifold3d  # noqa: F401
        return True
    except ImportError:
        return False


def for_kind(kind: str, folder: Path) -> Path:
    if kind == "svg":
        return svg(folder)
    if kind == "mesh":
        return mesh(folder)
    raise ValueError(f"no sample for kind {kind!r}")
