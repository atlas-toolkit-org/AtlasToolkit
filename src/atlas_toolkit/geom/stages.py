"""
Stage sets: the meshes of a progression (Disintegrate, Twist, Melt) saved as one file per stage,

    <dst.stem>_stages/<dst.stem>_stage_00.stl, _stage_01.stl, ...   (+ stages.json for Twist and Melt)

and found again by the drawing tools, which give every stage of a set the same frame (scale, position, slice grid),
so a sequence of plates reads as one motion.

Publishing is all or nothing: files are written into a scratch folder beside the output, then moved into a fresh
stages folder (an existing one is never replaced: "-1", "-2" ... are tried) and the primary output replaces dst
last. A failure or a cancel leaves the output folder exactly as it was.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

STAGE_RE = re.compile(r"^(?P<base>.+)_stage_(?P<i>\d+)$")
MANIFEST = "stages.json"


def amounts(n: int, amount: float = 1.0) -> list[float]:
    """Stage amounts: n evenly spaced values from 0 to `amount` (just [amount] for a single stage)."""
    n = max(1, int(n))
    if n == 1:
        return [float(amount)]
    return [float(amount) * k / (n - 1) for k in range(n)]


def stage_name(dst: Path, i: int) -> str:
    return f"{Path(dst).stem}_stage_{i:02d}{Path(dst).suffix}"


# ----------------------------------------------------------------------------- finding a set

@dataclass
class StageSet:
    files: list                       # [Path] in stage order
    manifest: dict | None = None      # stages.json, when a toolkit tool wrote the set
    base: str = ""

    @property
    def frame(self) -> dict | None:
        """{"up", "front", "rotate"} the set was made in (Twist, Melt), else None."""
        f = (self.manifest or {}).get("frame")
        return f if isinstance(f, dict) else None

    def index(self, path) -> int:
        p = Path(path).resolve()
        return next((i for i, f in enumerate(self.files) if Path(f).resolve() == p), -1)


def read_manifest(folder: Path) -> dict | None:
    try:
        data = json.loads((Path(folder) / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("atlas") == "stage-set" else None


def stage_set(path) -> StageSet | None:
    """The stage set `path` belongs to: from stages.json when it lists the file, else from sibling names
    (<base>_stage_NN with the same extension; at least two). None for a lone file."""
    p = Path(path)
    folder = p.parent
    manifest = read_manifest(folder)
    if manifest:
        names = [s.get("file") for s in manifest.get("stages", []) if isinstance(s, dict)]
        if p.name in names:
            files = [folder / n for n in names if (folder / n).exists()]
            if len(files) >= 2:
                m = STAGE_RE.match(p.stem)
                return StageSet(files, manifest, m.group("base") if m else p.stem)
    m = STAGE_RE.match(p.stem)
    if not m:
        return None
    base, ext = m.group("base"), p.suffix.lower()
    found = []
    try:
        entries = list(folder.iterdir())
    except OSError:
        return None
    for q in entries:
        mq = STAGE_RE.match(q.stem)
        if mq and mq.group("base") == base and q.suffix.lower() == ext and q.is_file():
            found.append((int(mq.group("i")), q))
    if len(found) < 2:
        return None
    return StageSet([q for _, q in sorted(found)], manifest, base)


# ----------------------------------------------------------------------------- publishing

@dataclass
class Publisher:
    """Collects the files of one run in a scratch folder beside `dst`, then publishes them together.

        with Publisher(ctx, dst, save, separate=True, prefix=".mesh-twist-") as pub:
            for i, mesh in enumerate(stages):
                pub.add_stage(i, mesh, {"amount": a})
            outputs = pub.commit(final_mesh)

    `save(item, path)` writes one item. `manifest` (a dict) is written as stages.json into the stages folder, with
    the stage list filled in. Nothing is published when the block exits without commit()."""
    ctx: object
    dst: Path
    save: object
    separate: bool = False
    prefix: str = ".stages-"
    manifest: dict | None = None
    scratch: Path | None = None
    stages: list = field(default_factory=list)
    _tmp: object = None

    def __enter__(self) -> "Publisher":
        self.dst = Path(self.dst)
        self._tmp = tempfile.TemporaryDirectory(prefix=self.prefix, dir=self.dst.parent)
        self.scratch = Path(self._tmp.name)
        if self.separate:
            (self.scratch / "stages").mkdir()
        return self

    def __exit__(self, *exc) -> None:
        if self._tmp is not None:
            self._tmp.cleanup()
            self._tmp = None

    def add_stage(self, i: int, item, info: dict | None = None) -> None:
        """Save stage `i` into the scratch folder (only when stage files were asked for)."""
        name = stage_name(self.dst, i)
        self.stages.append(dict({"file": name, "index": int(i)}, **(info or {})))
        if self.separate:
            self.save(item, self.scratch / "stages" / name)
            self.ctx.check_cancel()

    def commit(self, primary) -> list[str]:
        """Save the primary item, move the stage files into a fresh stages folder, then the primary onto dst.
        Returns [dst, stage files ...]."""
        dst = self.dst
        temp_primary = self.scratch / dst.name
        self.save(primary, temp_primary)
        self.ctx.check_cancel()
        temp_stages = self.scratch / "stages"
        if self.separate and self.manifest is not None:
            data = dict(self.manifest, atlas="stage-set", version=1, stages=self.stages)
            (temp_stages / MANIFEST).write_text(json.dumps(data, indent=2), encoding="utf-8")
        outputs, stage_folder = [], None
        try:
            if self.separate:
                base = dst.with_name(f"{dst.stem}_stages")
                for i in range(10000):
                    candidate = base if i == 0 else base.with_name(f"{base.name}-{i}")
                    try:
                        candidate.mkdir()   # reserve without ever replacing an existing folder
                    except FileExistsError:
                        continue
                    stage_folder = candidate
                    break
                if stage_folder is None:
                    raise ValueError("could not reserve a stages folder")
                for path in sorted(temp_stages.iterdir()):
                    self.ctx.check_cancel()
                    target = stage_folder / path.name
                    os.replace(path, target)
                    if path.name != MANIFEST:
                        outputs.append(str(target))
            self.ctx.check_cancel()
            os.replace(temp_primary, dst)
        except BaseException:
            if stage_folder is not None:
                if stage_folder.resolve().parent != dst.parent.resolve():
                    raise RuntimeError("refusing to remove a stages folder outside the output directory")
                shutil.rmtree(stage_folder)
            raise
        return [str(dst), *outputs]


def publish(ctx, dst: Path, primary, stages: list, separate: bool, save, prefix: str = ".stages-",
            manifest: dict | None = None, progress=(0.80, 0.97)) -> list[str]:
    """Publish a primary item and (optionally) every stage, all or nothing. Returns [dst, stage files ...]."""
    dst = Path(dst)
    a, b = progress
    n = 1 + (len(stages) if separate else 0)
    with Publisher(ctx, dst, _counting(ctx, save, n, a, b), separate, prefix, manifest) as pub:
        for i, stage in enumerate(stages):
            pub.add_stage(i, stage)
        return pub.commit(primary)


def _counting(ctx, save, n: int, a: float, b: float):
    count = [0]

    def wrapped(item, path):
        ctx.progress(a + (b - a) * count[0] / max(n, 1), f"saving mesh {count[0] + 1} of {n}")
        count[0] += 1
        return save(item, path)
    return wrapped


# ----------------------------------------------------------------------------- mesh tools' stage output

def progression_offsets(bounds: list, axis: str, gap: float) -> list:
    """Offsets (model units) that put stages side by side along an axis, `gap` apart (bounds: [(lo, hi)])."""
    import numpy as np
    a = "xyz".index(axis)
    out, pos = [], None
    for lo, hi in bounds:
        off = np.zeros(3)
        if pos is not None:
            off[a] = pos - lo[a]
        out.append(off)
        pos = hi[a] + off[a] + gap
    return out


class StageOutput:
    """The stages of a mesh tool (Twist, Melt) as they are made: a preview per stage, the progression, and
    publishing (dst None: preview only).

        out = StageOutput(ctx, TOOL, dst, n, p.layout, p.axis, gap, p.save_stages, manifest)
        with out:
            for i, mesh in ...:
                out.add(i, mesh, {"amount": u})
            outputs = out.finish()
        out.views, out.primary, out.prefer
    """

    def __init__(self, ctx, tool, dst, n: int, layout: str = "final", axis: str = "x", gap: float = 0.0,
                 save_stages: bool = True, manifest: dict | None = None):
        self.ctx, self.tool, self.dst, self.n = ctx, tool, dst, n
        self.progression = layout == "progression" and n > 1
        self.axis, self.gap = axis, gap
        self.views: list = []
        self.kept: list = []
        self.bounds: list = []
        self.primary = None
        self.prefer = "Output"
        separate = bool(save_stages) and n > 1 and dst is not None
        self.pub = None
        if dst is not None:
            from . import mesh as gm
            self.pub = Publisher(ctx, dst, lambda m, path: gm.save_mesh(m, path, tool), separate,
                                 f".{tool.id}-", manifest)

    def __enter__(self) -> "StageOutput":
        if self.pub is not None:
            self.pub.__enter__()
        return self

    def __exit__(self, *exc) -> None:
        if self.pub is not None:
            self.pub.__exit__(*exc)

    def add(self, i: int, mesh, info: dict | None = None) -> None:
        from . import mesh as gm
        if self.pub is not None:
            self.pub.add_stage(i, mesh, info)
        self.views.append({"title": f"Stage {i}" if self.n > 1 else "Output", "kind": "mesh",
                           "mesh": gm.preview(mesh)})
        self.bounds.append(mesh.bounds)
        if self.progression:
            self.kept.append(mesh)
        self.primary = mesh

    def finish(self) -> list:
        """Build the primary output (the last stage, or all side by side) and publish everything."""
        import trimesh
        from . import mesh as gm
        if self.progression:
            offs = progression_offsets(self.bounds, self.axis, self.gap)
            self.primary = trimesh.util.concatenate([trimesh.Trimesh(m.vertices + o, m.faces, process=False)
                                                     for m, o in zip(self.kept, offs)])
            self.views.append({"title": "Progression", "kind": "mesh", "mesh": gm.preview(self.primary)})
            self.kept = []
        self.prefer = self.views[-1]["title"]
        if self.pub is None:
            return []
        self.ctx.progress(0.9, "saving")
        return self.pub.commit(self.primary)
