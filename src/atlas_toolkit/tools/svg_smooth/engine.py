"""Shared Atlas preview, batch, CLI and Workflow adapter; identical draft/final geometry."""
import math
from pathlib import Path

from atlas_toolkit.core import Result, Stat
from atlas_toolkit.geom.svg import BASIC_SHAPES, SHAPES, load, read_drawing, sign_desc, write_run_metadata, write_tree
from . import TOOL
from .svg_smooth import Options, smooth_tree


def run(ctx, src: Path, dst: Path | None, p) -> Result:
    tree = load(src)
    before = read_drawing(tree, shapes=SHAPES | BASIC_SHAPES)
    options = Options(**{key: p[key] for key, f in Options.__dataclass_fields__.items() if f.init})
    st = smooth_tree(tree, options, ctx.progress)
    if not st.strokes:
        unchanged = before.stats()
        st.strokes = unchanged.strokes
        st.vertices_in = st.vertices_out = unchanged.points
        st.length_in = st.length_out = unchanged.ink
    after = read_drawing(tree, shapes=SHAPES | BASIC_SHAPES)
    average = st.blend_sum / st.smoothed if st.smoothed else 0.
    report = (f"{src.name}\n  smoothing     {p.method}\n  span          {p.span:g} mm\n"
              f"  smoothed      {st.smoothed:,} / {st.strokes:,} strokes\n"
              f"  displacement  {st.displacement:.6g} mm (cap {p.deviation:g} mm; curve sampling reserved separately)\n"
              f"  achieved blend {average:.1f}% average (requested {p.strength:g}%)\n"
              f"  limited       {st.limited:,} strokes\n"
              f"  samples       {st.vertices_in:,} → {st.vertices_out:,}\n"
              f"  ink length    {st.length_in:.3f} → {st.length_out:.3f} mm")
    for note in st.notes:
        ctx.log(note)
        report += "\n  " + note
    outputs = []
    if dst is not None:
        sign_desc(tree.getroot(), TOOL.id)
        write_run_metadata(tree.getroot(), TOOL, p)
        write_tree(tree, dst)
        outputs.append(str(dst))
        report += f"\n  saved as      {dst}"
    return Result(outputs=outputs, changed=st.changed > 0, report=report,
                  stats=[Stat("smoothed", "Smoothed", st.smoothed),
                         Stat("displacement", "Displacement", st.displacement, "mm", fmt="{:.4f}"),
                         Stat("blend", "Achieved blend", average, "%", fmt="{:.1f}"),
                         Stat("limited", "Limited", st.limited),
                         Stat("length", "Ink", st.length_out, "mm", before=st.length_in, fmt="{:.2f}")],
                  preview={"views": [{"title": "Before", "kind": "svg", "drawing": before},
                                     {"title": "After", "kind": "svg", "drawing": after}], "prefer": "After"},
                  status="Saved" if outputs else "Ready")


def make_sample(folder: Path) -> Path:
    def stroke(y, phase):
        return " ".join(f"{10 + k * .2:.6f},{y + 5 * math.sin(k / 50 + phase) + .12 * (-1) ** k:.6f}" for k in range(601))
    path = folder / "jagged_line_art.svg"
    path.write_text(f'''<svg xmlns="http://www.w3.org/2000/svg" width="150mm" height="90mm" viewBox="0 0 150 90">
<g id="ink" fill="none" stroke="#171c28" stroke-width="0.3" stroke-linecap="round" stroke-linejoin="round">
<polyline id="stripe-a" points="{stroke(25, 0)}"/><polyline id="stripe-b" points="{stroke(35, .1)}"/>
<path id="corner" d="M10 65 L30 65 L30 55 L50 55"/>
<circle id="motif" cx="70" cy="65" r="4"/><path id="curve" d="M90 65 C100 45 120 85 130 65"/>
</g></svg>''', encoding="utf-8")
    return path
