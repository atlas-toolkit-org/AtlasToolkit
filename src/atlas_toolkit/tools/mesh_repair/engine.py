"""Toolkit adapter for Repair Mesh: load, diagnose, repair (geom/repair.py), diagnose again, save signed."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from atlas_toolkit.core import InputInfo, Result, Stat
from atlas_toolkit.geom import mesh as gm
from atlas_toolkit.geom import repair as R

from . import TOOL

LABELS = (("sheets", "Back-to-back faces"), ("duplicate", "Duplicate faces"), ("degenerate", "Degenerate faces"),
          ("crumbs", "Loose crumbs"), ("holes", "Holes"), ("boundary_edges", "Open edges"),
          ("nonmanifold_edges", "Non-manifold edges"), ("bowtie_vertices", "Bow-tie vertices"),
          ("flipped_edges", "Wrongly wound edges"), ("pieces", "Pieces"))


def settings(p) -> R.Settings:
    return R.Settings(goal=p.goal, strategy=p.strategy, weld_tol=float(p.weld), sheets=bool(p.sheets),
                      pieces=p.pieces, crumb_share=float(p.crumb_share) / 100.0, nonmanifold=bool(p.nonmanifold),
                      orient=bool(p.orient), fill="all" if p.goal == "solid" and p.fill != "none" and
                      p.strategy != "repair" else p.fill,
                      fill_max_edges=int(p.fill_max_edges), fill_shape=p.fill_shape, resolution=int(p.resolution))


def count_problems(d: R.Diagnosis) -> int:
    return (d.sheets // 2 + d.duplicate + d.degenerate + d.crumbs + d.holes + d.nonmanifold_edges
            + d.bowtie_vertices + (1 if d.flipped_edges else 0) + (1 if d.volume < 0 and not d.flipped_edges else 0))


def ready_for(d: R.Diagnosis, registry=None) -> list[str]:
    """The tools whose input needs this mesh meets."""
    if registry is None:
        from atlas_toolkit.core.registry import registry as reg
        registry = reg()
    out = []
    for t in registry.ordered():
        need = getattr(t, "strongest_need", getattr(t, "needs", None))
        if need and t.id != TOOL.id and d.ok_for(need):
            out.append(t.name if t.needs else f"{t.name} ({t.need_options(need)})")
    return out


def problems_view(mesh, title="Problems"):
    data = gm.preview(mesh)
    return {"title": title, "kind": "mesh", "mesh": replace(data, marks=R.problem_segments(mesh))}


def inspect(path: Path) -> InputInfo:
    mesh = R.load_raw(path)
    summary, values = gm.describe(mesh)
    d = R.diagnose(mesh)
    values["problems"] = count_problems(d)
    state = "a clean closed solid" if d.solid else (f"{values['problems']} problem"
                                                    f"{'s' if values['problems'] != 1 else ''} to repair")
    views = [{"title": "Input", "kind": "mesh", "mesh": gm.preview(mesh)}]
    if not d.solid:
        views.append(problems_view(mesh))
    return InputInfo(str(path), "mesh", f"{summary} · {state}", values, preview={"views": views})


def table(before: R.Diagnosis, after: R.Diagnosis) -> list[str]:
    lines = [f"  {'':<22}{'before':>10}{'after':>10}"]
    for key, label in LABELS:
        a, b = getattr(before, key), getattr(after, key)
        if a or b:
            lines.append(f"  {label:<22}{a:>10,}{b:>10,}".replace(",", " "))
    lines.append(f"  {'Faces':<22}{before.faces:>10,}{after.faces:>10,}".replace(",", " "))
    lines.append(f"  {'Closed solid':<22}{'yes' if before.solid else 'no':>10}{'yes' if after.solid else 'no':>10}")
    return lines


def run(ctx, src: Path, dst: Path | None, p) -> Result:
    if dst is not None and dst.resolve() == Path(src).resolve():
        raise ValueError("the output must not replace the input model")
    ctx.progress(0.02, "loading")
    original = R.load_raw(src)
    ctx.progress(0.1, "looking for problems")
    before = R.diagnose(original)
    ctx.progress(0.2, "repairing")
    s = settings(p)
    if len(original.faces) > 400_000 and s.strategy != "repair" and not before.solid:
        ctx.warn(f"A large model ({len(original.faces):,} faces): repairing it takes a minute or so, and if it must "
                 f"be rebuilt, resampling at {int(p.resolution)} can take a few minutes more. Cancel stops it at any "
                 "time.".replace(",", " "))
    out, notes, rebuilt = R.repair(original, s, log=ctx.log,
                                   progress=lambda t, msg="": ctx.progress(0.25 + 0.6 * float(t), "rebuilding"))
    ctx.progress(0.88, "checking")
    after = R.diagnose(out)
    if not len(out.faces):
        raise ValueError("nothing is left after the repair: every piece was debris. Choose Keep all loose pieces.")
    goal_met = after.ok_for(p.goal)
    lines = [f"Repair Mesh · {src.name}", "", f"  found      {before.summary()}"]
    lines += [f"  fixed      {n}" for n in notes] or ["  fixed      nothing: the model was already clean"]
    lines += [""] + table(before, after) + [""]
    result_notes = []
    if goal_met:
        ready = ready_for(after)
        lines.append("  Ready for: " + (", ".join(ready) if ready else "every tool"))
    else:
        left = after.summary()
        hint = ("Choose Method › Repair, rebuild only if needed (or Always rebuild) to close it by resampling."
                if p.strategy == "repair" else
                "Raise Resolution, or choose Always rebuild." if p.goal == "solid" else
                "Turn on the options above that are off.")
        lines.append(f"  Still not {'a closed solid' if p.goal == 'solid' else 'a clean surface'}: {left}.")
        lines.append(f"  {hint}")
        result_notes.append(f"the result is still not {'a closed solid' if p.goal == 'solid' else 'clean'}: "
                            f"{left}. {hint}")
        ctx.warn(result_notes[-1])
    if rebuilt:
        result_notes.append("the model was rebuilt by resampling, which softens detail finer than "
                            f"1/{int(p.resolution)} of its size")
    fixed = max(0, count_problems(before) - count_problems(after))
    stats = [Stat("problems", "Problems", count_problems(after), before=count_problems(before),
                  tone="success" if goal_met else "warning"),
             Stat("fixed", "Fixed", fixed, tone="success" if fixed else "")]
    res = gm.result(ctx, TOOL, original, out, dst, stats, lines)
    res.preview["views"].append(problems_view(out, "Problems after") if not goal_met else
                                {"title": "Input", "kind": "mesh", "mesh": gm.preview(original)})
    res.notes = result_notes
    res.status = (res.status if goal_met else "Partly repaired")
    return res


def make_sample(folder: Path) -> Path:
    """A sphere with the usual damage: a hole, a back-to-back sheet, a crumb and a flipped patch."""
    import numpy as np
    import trimesh
    m = trimesh.creation.icosphere(subdivisions=3, radius=10.0)
    V, F = np.asarray(m.vertices), np.asarray(m.faces)
    F = np.delete(F, [0, 1, 2, 3], axis=0)                       # a hole
    F[10:20] = F[10:20, ::-1]                                    # a wrongly wound patch
    F = np.vstack([F, F[40][::-1][None, :]])                     # a back-to-back face
    crumb = trimesh.Trimesh([[30, 0, 0], [30.1, 0, 0], [30, 0.1, 0]], [[0, 1, 2]], process=False)
    out = trimesh.util.concatenate([trimesh.Trimesh(V, F, process=False), crumb])
    path = Path(folder) / "damaged_sphere.stl"
    out.export(path)
    return path
