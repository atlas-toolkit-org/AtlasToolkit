"""Atlas adapter: identical draft/final geometry, before/after preview, signed SVG and run metadata."""
from pathlib import Path
import math

from atlas_toolkit.core import Result, Stat
from atlas_toolkit.geom.svg import load, read_drawing, sign_desc, write_run_metadata, write_tree
from . import TOOL
from .svg_simplify import Options, simplify_tree


def run(ctx, src: Path, dst: Path | None, p) -> Result:
    tree = load(src)
    before = read_drawing(tree)
    st = simplify_tree(tree, Options(**{k: p[k] for k in Options.__dataclass_fields__}), ctx.progress)
    after = read_drawing(tree)
    reduction = 100 * (1 - st.vertices_out / st.vertices_in) if st.vertices_in else 0.
    report = (f"{src.name}\n  algorithm    {p.method}\n  deviation cap {p.tolerance:g} mm\n"
              f"  vertices     {st.vertices_in:,} → {st.vertices_out:,} ({reduction:.1f}% removed)\n"
              f"  strokes      {st.strokes:,}\n  changed      {st.changed:,} elements\n"
              f"  span error   {st.deviation:.6g} mm (against sampled source)\n"
              f"  ink length   {st.length_in:.3f} → {st.length_out:.3f} mm\n"
              f"  guarded      {st.blocked:,} proposed shortcuts subdivided")
    for note in st.notes:
        ctx.log(note)
        report += "\n  " + note
    outputs = []
    if dst is not None:
        sign_desc(tree.getroot(), TOOL.id)
        write_run_metadata(tree.getroot(), TOOL, p)
        write_tree(tree, dst)
        outputs.append(str(dst))
        report += f"\n  saved as     {dst}"
    return Result(outputs=outputs, changed=st.changed > 0, report=report,
                  stats=[Stat("vertices", "Vertices", st.vertices_out, before=st.vertices_in),
                         Stat("reduction", "Removed", reduction, "%", fmt="{:.1f}"),
                         Stat("deviation", "Span error", st.deviation, "mm", fmt="{:.4f}"),
                         Stat("strokes", "Strokes", st.strokes),
                         Stat("length", "Ink", st.length_out, "mm", before=st.length_in, fmt="{:.2f}")],
                  preview={"views": [{"title": "Before", "kind": "svg", "drawing": before},
                                     {"title": "After", "kind": "svg", "drawing": after}], "prefer": "After"},
                  status="Saved" if outputs else "Ready")


def make_sample(folder: Path) -> Path:
    # A flowing pen stroke, a sharp accent, a closed motif and a crossing stroke.
    pts = " ".join(f"{10 + k * .35:.6f},{35 + 12 * math.sin(k / 24):.6f}" for k in range(360))
    path = folder / "line_art.svg"
    path.write_text(f'''<svg xmlns="http://www.w3.org/2000/svg" width="150mm" height="90mm" viewBox="0 0 150 90">
<g id="ink" fill="none" stroke="#171c28" stroke-width="0.3" stroke-linecap="round" stroke-linejoin="round">
<polyline id="gesture" points="{pts}"/><path id="accent" d="M 15 65 L 25 65 L 30 55 L 35 65 L 45 65"/>
<path id="motif" d="M70 65 L71 65.01 L72 65 L80 65 L80 75 L70 75 Z"/>
<path d="M100 15 L100 60"/><path d="M15 15 C25 0 45 30 60 15"/>
</g></svg>''', encoding="utf-8")
    return path
