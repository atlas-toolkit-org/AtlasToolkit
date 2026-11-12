"""
Inputs and outputs, shared by every tool: expand dropped files and folders, and decide where each output goes.
Originals are never overwritten; a template that would produce the input's own name gets a numbered suffix.
"""
from __future__ import annotations

from pathlib import Path


def expand_inputs(tool, paths, recursive: bool = False) -> list[Path]:
    """Files the tool accepts, from files and folders. A folder adds its own inputs, not earlier outputs."""
    found: list[Path] = []
    out_tail = tool.output.template.split("}")[-1].lower()   # "--nooverlap.svg", "_vox.stl" ...
    for p in map(Path, paths):
        if p.is_dir():
            it = p.rglob("*") if recursive else p.glob("*")
            for f in sorted(it):
                if f.is_file() and tool.inputs.accepts(f) and not (out_tail and f.name.lower().endswith(out_tail)):
                    found.append(f)
        elif p.is_file() and tool.inputs.accepts(p):
            found.append(p)
    seen, out = set(), []
    for f in found:
        key = str(f.resolve()).lower()
        if key not in seen:
            seen.add(key)
            out.append(f.resolve())
    return out


def output_path(tool, src: Path, values: dict | None = None, out_mode: str = "next", out_dir: str = "",
                fmt: str = "") -> Path:
    name = tool.output.name_for(src, values or {}, fmt or None)
    folder = Path(out_dir) if (out_mode == "folder" and out_dir) else src.parent
    dst = folder / name
    if dst.resolve() == src.resolve():   # never write over the input
        dst = dst.with_name(f"{dst.stem}-1{dst.suffix}")
    return dst
