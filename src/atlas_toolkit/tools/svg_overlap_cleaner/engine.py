"""Toolkit adapter for svg_overlap_cleaner: options from the parameters, previews, stats and the report."""
from __future__ import annotations

from pathlib import Path

from atlas_toolkit.core import Result, Stat
from atlas_toolkit.geom.drawing import Pen, fmt_duration, fmt_length
from atlas_toolkit.geom.svg import read_drawing, sheet_of, write_run_metadata, write_tree

from . import TOOL
from . import svg_overlap_cleaner as so

MAX_ROWS = 300
CUT_COLOR = "#E5484D"


def options(p) -> so.Options:
    return so.Options(tol=p.tol, angle=p.angle, min_overlap=p.min_overlap, min_piece=p.min_piece,
                      simplify=p.simplify, flatten=p.flatten, across_pens=p.across_pens, precision=p.precision,
                      verbose=True, collect_cuts=True, mode=p.mode, join=p.join, join_reverse=p.join_reverse,
                      join_scope=p.join_scope, shapes=p.shapes, pen_by_width=p.pen_by_width)


def run(ctx, src: Path, dst: Path | None, p) -> Result:
    tree = so.load(src)
    before = read_drawing(tree)
    ctx.progress(0.05, "analysing")
    stats = so.clean_tree(tree, options(p), progress=lambda i, n: ctx.progress(0.05 + 0.85 * i / max(n, 1)),
                          log=ctx.warn)
    after = read_drawing(tree)
    mpu = sheet_of(tree.getroot())[3]
    cuts = Pen.from_polylines("cuts", "Cut ink", ([(x * mpu, y * mpu) for x, y in c] for c in stats.cuts),
                              CUT_COLOR, 0.5)
    outputs = []
    if dst is not None and stats.changed:
        write_run_metadata(tree.getroot(), TOOL, p)
        write_tree(tree, dst)
        outputs.append(str(dst))
    ctx.progress(1.0)

    b, a = before.stats(), after.stats()
    pct = 100 * stats.retraced / stats.ink_in if stats.ink_in else 0.0
    result_stats = [
        Stat("ink_cut", "Ink cut", stats.retraced, "mm", tone="warning" if pct >= 10 else "",
             fmt="{:,.0f}" if stats.retraced else "—"),
        Stat("ink_cut_pct", "Share cut", pct, "%", fmt="{:.1f}"),
        Stat("strokes", "Strokes", stats.strokes_out, before=stats.strokes_in,
             tone="success" if stats.strokes_out < stats.strokes_in else ""),
        Stat("vertices", "Vertices", stats.vertices_out, before=stats.vertices_in),
        Stat("ink", "Ink", fmt_length(a.ink), before=fmt_length(b.ink), fmt="{}"),
        Stat("travel", "Pen-up travel", fmt_length(a.travel), before=fmt_length(b.travel), fmt="{}"),
        Stat("time", "Plot time", fmt_duration(a.seconds()), before=fmt_duration(b.seconds()), fmt="{}"),
    ]
    if pct >= 10:
        result_stats[0].tone = "warning"
    return Result(
        outputs=outputs,
        stats=result_stats,
        report=report(src, dst, stats),
        preview={"views": [{"title": "Before", "kind": "svg", "drawing": before},
                           {"title": "After", "kind": "svg", "drawing": after},
                           {"title": "Cut ink", "kind": "svg", "drawing": after,
                            "overlays": [cuts] if cuts.points else []}],
                 "prefer": "Cut ink" if stats.retraced else "After"},
        changed=bool(stats.changed),
        status=("Saved" if outputs else "Ready") if stats.changed else "Nothing to cut",
        data={"retraced": stats.retraced, "ink_in": stats.ink_in},
    )


def report(src: Path, dst: Path | None, st: so.Stats) -> str:
    lines = [st.report(src.name), f"  folder     {src.parent}"]
    if dst is not None and st.changed:
        lines.append(f"  saved as   {dst}")
    rows = sorted(st.rows, key=lambda r: -r.retraced)
    if rows:
        lines += ["", f"  CHANGED PATHS, MOST INK CUT FIRST ({len(rows)})",
                  f"  {'path':<20}{'strokes':>12}{'vertices':>18}{'ink cut':>14}"]
        for r in rows[:MAX_ROWS]:
            lines.append(f"  {r.name[:19]:<20}{f'{r.strokes_in} → {r.strokes_out}':>12}"
                         f"{f'{r.vertices_in} → {r.vertices_out}':>18}{f'{r.retraced:.1f} mm':>14}")
        if len(rows) > MAX_ROWS:
            lines.append(f"  … {len(rows) - MAX_ROWS} more")
    return "\n".join(lines)
