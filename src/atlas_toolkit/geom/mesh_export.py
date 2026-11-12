"""Staged, signed publication for mesh tools with an optional extra mesh."""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from .mesh import save_mesh


def publish_meshes(meshes, dst, ctx, tool, extra_label="other", validator=None, extra_labels=None):
    """Stage every file before replacing the primary; extras never overwrite an existing sibling.

    Atlas checks the primary output's overwrite permission. Extra files use exclusive creation and numbered
    names. If publishing fails, remove only the extras created by this run and preserve the old primary.
    """
    meshes = list(meshes)
    labels = list(extra_labels) if extra_labels is not None else [extra_label] * (len(meshes) - 1)
    if len(labels) != len(meshes) - 1 or any(not label or not all(c.isalnum() or c in "_-" for c in label) for label in labels):
        raise ValueError("Each extra mesh needs one safe filename label.")
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    extras = []
    with tempfile.TemporaryDirectory(prefix=f".{tool.id}-", dir=dst.parent) as temp:
        staged = []
        for i, mesh in enumerate(meshes):
            ctx.progress(0.85 + i * 0.03, "staging output")
            path = Path(temp) / f"part-{i}{dst.suffix}"
            save_mesh(mesh, path, tool)
            if validator is not None:
                validator(path)
            staged.append(path)
        ctx.check_cancel()
        try:
            for source, label in zip(staged[1:], labels):
                n = 0
                while True:
                    tail = "" if n == 0 else f"-{n}"
                    path = dst.with_name(f"{dst.stem}_{label}{tail}{dst.suffix}")
                    try:
                        fh = path.open("xb")
                        break
                    except FileExistsError:
                        n += 1
                extras.append(path)
                with fh, source.open("rb") as src:
                    shutil.copyfileobj(src, fh)
            ctx.check_cancel()
            os.replace(staged[0], dst)
        except BaseException:
            for path in extras:
                path.unlink(missing_ok=True)
            raise
    return [str(dst)] + [str(path) for path in extras]
