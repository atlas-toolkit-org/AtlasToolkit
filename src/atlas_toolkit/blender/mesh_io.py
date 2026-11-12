"""Meshes between the toolkit and Blender: .npz files (V float64 (n, 3), F int (m, 3)) <-> Blender objects.

Objects are built from arrays (no importer operators: no console noise, no axis conversions) and read back from the
evaluated depsgraph as triangles."""
from __future__ import annotations

import numpy as np


def load_npz(path) -> tuple[np.ndarray, np.ndarray]:
    with np.load(path) as d:
        return np.asarray(d["V"], dtype=np.float64), np.asarray(d["F"], dtype=np.int64)


def save_npz(path, V, F) -> None:
    np.savez(path, V=np.asarray(V, dtype=np.float64), F=np.asarray(F, dtype=np.int32))


def build_object(name: str, V: np.ndarray, F: np.ndarray):
    """A new mesh object at the origin (identity transform), smooth-shaded, linked to the scene."""
    import bpy
    me = bpy.data.meshes.new(name)
    nv, nf = len(V), len(F)
    me.vertices.add(nv)
    me.vertices.foreach_set("co", np.ascontiguousarray(V, dtype=np.float32).ravel())
    me.loops.add(nf * 3)
    me.loops.foreach_set("vertex_index", np.ascontiguousarray(F, dtype=np.int32).ravel())
    me.polygons.add(nf)
    me.polygons.foreach_set("loop_start", np.arange(0, nf * 3, 3, dtype=np.int32))
    try:                                   # Blender < 4.0 also needs the loop counts
        me.polygons.foreach_set("loop_total", np.full(nf, 3, dtype=np.int32))
    except (AttributeError, TypeError, RuntimeError):
        pass
    me.update(calc_edges=True)
    try:
        me.shade_smooth()                  # 4.1+
    except AttributeError:                 # pragma: no cover
        me.polygons.foreach_set("use_smooth", np.ones(nf, dtype=bool))
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def evaluated_arrays(obj) -> tuple[np.ndarray, np.ndarray]:
    """World-space vertices and triangles of the object after its modifiers."""
    import bpy
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    me = ev.to_mesh()
    try:
        me.calc_loop_triangles()
    except AttributeError:                 # pragma: no cover (computed on demand in newer versions)
        pass
    co = np.empty(len(me.vertices) * 3, dtype=np.float64)
    me.vertices.foreach_get("co", co)
    tris = np.empty(len(me.loop_triangles) * 3, dtype=np.int64)
    me.loop_triangles.foreach_get("vertices", tris)
    ev.to_mesh_clear()
    m = np.array(obj.matrix_world)
    V = co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3]
    return V, tris.reshape(-1, 3)


def remove(obj) -> None:
    """Delete an object, its mesh and the node groups of its modifiers."""
    import bpy
    groups = [m.node_group for m in obj.modifiers if getattr(m, "node_group", None) is not None]
    me = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if me is not None and me.users == 0:
        bpy.data.meshes.remove(me)
    for g in groups:
        if g.users == 0:
            bpy.data.node_groups.remove(g)


def clear_modifiers(obj) -> None:
    import bpy
    groups = [m.node_group for m in obj.modifiers if getattr(m, "node_group", None) is not None]
    for m in list(obj.modifiers):
        obj.modifiers.remove(m)
    for g in groups:
        if g.users == 0:
            bpy.data.node_groups.remove(g)
