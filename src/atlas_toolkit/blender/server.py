"""
The Atlas Toolkit's Blender worker: started by geom/blender.py as

    blender -b --factory-startup -noaudio --python-exit-code 1 --python server.py -- --protocol 1

it answers JSON requests, one per line on stdin, until "quit" or until stdin closes (the toolkit's worker is gone:
the server exits at once, even mid-request). Blender's own C code prints to stdout, so the protocol keeps a private
copy of stdout and every other print (Python or C) goes to stderr; protocol lines also carry a tag.

    ready     {"ready": true, "protocol": 1, "server": .., "blender": "5.2.2 LTS", "version": [5, 2, 2],
               "numpy": .., "pid": .., "startup_s": .., "missing_nodes": [...]}
    request   {"id": 3, "op": "melt", "mesh": "in.npz", "stages": [{"out": "s0.npz", "intact": true, "subdiv": 1},
               {"out": "s1.npz", "subdiv": 1, "params": {...}}]}
              ops: ping, check, melt, wave, solidify, cloth
    progress  {"id": 3, "progress": 0.5, "msg": "stage 2 of 4: evaluating"}
    reply     {"id": 3, "ok": true, "stages": [{"out": .., "faces": .., "vertices": .., "seconds": ..}]}
              {"id": 3, "ok": false, "error": "...", "trace": "..."}    {"id": 3, "ok": false, "cancelled": true}
    cancel    {"cancel": 3}        quit {"quit": true}

Meshes travel as .npz files (blender/mesh_io.py). Python 3.11+ syntax only: Blender brings its own Python.
"""
import json
import os
import queue
import sys
import threading
import time
import traceback

PROTOCOL = 1
SERVER_VERSION = "1.1"
TAG = "\x1eATLAS1 "
NODE_TYPES = [
    "GeometryNodeSubdivisionSurface", "GeometryNodeBoundBox", "GeometryNodeSetPosition",
    "GeometryNodeStoreNamedAttribute", "GeometryNodeInputNamedAttribute", "GeometryNodeBlurAttribute",
    "GeometryNodeMeshToVolume", "GeometryNodeVolumeToMesh", "GeometryNodeProximity", "ShaderNodeTexNoise",
    "ShaderNodeMapRange", "ShaderNodeMath", "ShaderNodeVectorMath", "ShaderNodeSeparateXYZ", "ShaderNodeCombineXYZ",
    "GeometryNodeAttributeStatistic", "GeometryNodeDistributePointsOnFaces", "FunctionNodeRandomValue",
    "GeometryNodeDeleteGeometry", "GeometryNodeSampleNearestSurface", "GeometryNodeCurvePrimitiveLine",
    "GeometryNodeInstanceOnPoints", "GeometryNodeRealizeInstances", "GeometryNodeResampleCurve",
    "GeometryNodeSplineParameter", "GeometryNodeCurveOfPoint", "GeometryNodeAccumulateField",
    "GeometryNodeSplineLength", "GeometryNodeOffsetPointInCurve", "GeometryNodeSampleIndex",
    "GeometryNodeInputIndex", "GeometryNodeCurvePrimitiveCircle", "GeometryNodeCurveToMesh",
    "GeometryNodeJoinGeometry", "GeometryNodeInputPosition", "GeometryNodeInputNormal",
]


class Cancelled(Exception):
    pass


def _here() -> str:
    argv = sys.argv
    if "--python" in argv:
        i = argv.index("--python")
        if i + 1 < len(argv):
            return os.path.dirname(os.path.abspath(argv[i + 1]))
    return os.path.dirname(os.path.abspath(globals().get("__file__", ".")))


def missing_nodes() -> list:
    import bpy
    ng = bpy.data.node_groups.new("atlas_check", "GeometryNodeTree")
    missing = []
    for t in NODE_TYPES:
        try:
            ng.nodes.new(t)
        except Exception:
            missing.append(t)
    bpy.data.node_groups.remove(ng)
    return missing


def reset_scene() -> None:
    import bpy
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    for me in list(bpy.data.meshes):
        if me.users == 0:
            bpy.data.meshes.remove(me)


# ----------------------------------------------------------------------------- operations

def op_ping(msg, progress):
    return {"pong": True}


def op_check(msg, progress):
    """Build and evaluate a full melt (drips included) on a small sphere."""
    import numpy as np
    import melt_gn
    import mesh_io
    import bpy
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3, radius=1.0)
    ob = bpy.context.active_object
    try:
        melt_gn.add_melt(ob, subdiv=0, dissolve=0.3, drip_amount=0.9, seed_mode="rest", head=False)
        V, F = mesh_io.evaluated_arrays(ob)
    finally:
        mesh_io.remove(ob)
    return {"faces": int(len(F)), "finite": bool(np.isfinite(V).all())}


def op_melt(msg, progress):
    import melt_gn
    import mesh_io
    V, F = mesh_io.load_npz(msg["mesh"])
    base = mesh_io.build_object("atlas_melt", V, F)
    stages = msg["stages"]
    n = len(stages)
    out = []
    try:
        for i, st in enumerate(stages):
            progress(i / n, f"stage {i + 1} of {n}: building the melt")
            t0 = time.perf_counter()
            levels = int(st.get("subdiv", 0))
            if st.get("intact"):
                if levels:
                    sub = base.modifiers.new("Subdivision", "SUBSURF")
                    sub.levels = sub.render_levels = levels
            else:
                params = dict(st.get("params", {}))
                params["subdiv"] = levels
                if isinstance(params.get("face_dir"), list):
                    params["face_dir"] = tuple(params["face_dir"])
                melt_gn.add_melt(base, **params)
            progress((i + 0.25) / n, f"stage {i + 1} of {n}: evaluating")
            Vo, Fo = mesh_io.evaluated_arrays(base)
            mesh_io.save_npz(st["out"], Vo, Fo)
            mesh_io.clear_modifiers(base)
            out.append({"out": st["out"], "faces": int(len(Fo)), "vertices": int(len(Vo)),
                        "seconds": round(time.perf_counter() - t0, 3)})
    finally:
        mesh_io.remove(base)
    progress(1.0, "done")
    return {"stages": out}


def op_wave(msg, progress):
    """The skill's twist node group on a mesh (for parity tests with the toolkit's numpy Twist)."""
    import mesh_io
    import wave_gn
    V, F = mesh_io.load_npz(msg["mesh"])
    ob = mesh_io.build_object("atlas_wave", V, F)
    try:
        params = dict(msg.get("params", {}))
        for k in ("sway_axis", "depth_axis"):
            if isinstance(params.get(k), list):
                params[k] = tuple(params[k])
        wave_gn.add_wave(ob, subdiv_levels=int(msg.get("subdiv", 0)), **params)
        Vo, Fo = mesh_io.evaluated_arrays(ob)
        mesh_io.save_npz(msg["out"], Vo, Fo)
    finally:
        mesh_io.remove(ob)
    return {"out": msg["out"], "faces": int(len(Fo))}


def op_solidify(msg, progress):
    """Complex Solidify (Surface Peel's panels): thickness grown against the normals, rims on open edges."""
    import mesh_io
    V, F = mesh_io.load_npz(msg["mesh"])
    ob = mesh_io.build_object("atlas_solidify", V, F)
    try:
        progress(0.1, "solidifying")
        mod = ob.modifiers.new("Solidify", "SOLIDIFY")
        mod.solidify_mode = "NON_MANIFOLD"
        mod.nonmanifold_thickness_mode = "CONSTRAINTS"
        mod.thickness = float(msg["thickness"])
        mod.offset = -1.0
        mod.use_even_offset = True
        mod.use_rim = True
        Vo, Fo = mesh_io.evaluated_arrays(ob)
        mesh_io.save_npz(msg["out"], Vo, Fo)
    finally:
        mesh_io.remove(ob)
    progress(1.0, "done")
    return {"out": msg["out"], "faces": int(len(Fo))}


def op_cloth(msg, progress):
    """Cloth settle (Ribbon Wrap): the ribbon sheet ("ribbon": V, F, pin weights W) falls for `frames` frames
    under gravity onto the body ("body") and, when given, a floor at z = `floor`; returns its points in the same
    order. Pinned points (W, scaled by pin_stiffness) hold their place, free ones fall, fold and drape."""
    import bpy
    import numpy as np
    import mesh_io
    Vb, Fb = mesh_io.load_npz(msg["body"])
    with np.load(msg["ribbon"]) as d:
        Vr, Fr, W = np.asarray(d["V"], np.float64), np.asarray(d["F"], np.int64), np.asarray(d["W"], np.float64)
    made = []
    try:
        body = mesh_io.build_object("atlas_cloth_body", Vb, Fb)
        made.append(body)
        body.modifiers.new("Collision", "COLLISION")
        body.collision.thickness_outer = 0.002
        body.collision.cloth_friction = 20.0
        if msg.get("floor") is not None:
            z = float(msg["floor"])
            lo, hi = Vb.min(0), Vb.max(0)
            c, r = (lo + hi) / 2.0, float((hi - lo).max()) * 3.0
            Vf = np.array([[c[0] - r, c[1] - r, z], [c[0] + r, c[1] - r, z], [c[0] + r, c[1] + r, z],
                           [c[0] - r, c[1] + r, z]])
            floor = mesh_io.build_object("atlas_cloth_floor", Vf, np.array([[0, 1, 2], [0, 2, 3]]))
            made.append(floor)
            floor.modifiers.new("Collision", "COLLISION")
            floor.collision.thickness_outer = 0.002
            floor.collision.cloth_friction = 40.0
        rib = mesh_io.build_object("atlas_cloth_ribbon", Vr, Fr)
        made.append(rib)
        vg = rib.vertex_groups.new(name="pin")
        q = np.round(np.clip(W, 0.0, 1.0) * 20.0) / 20.0
        for w in np.unique(q):
            if w > 0:
                vg.add(np.nonzero(q == w)[0].tolist(), float(w), "REPLACE")
        mod = rib.modifiers.new("Cloth", "CLOTH")
        st = mod.settings
        st.quality = int(msg.get("quality", 6))
        st.mass = float(msg.get("mass", 0.15))
        st.air_damping = 1.0
        st.tension_stiffness = st.compression_stiffness = 40.0
        st.shear_stiffness = 10.0
        st.bending_stiffness = float(msg.get("bending", 0.5))
        st.vertex_group_mass = "pin"
        st.pin_stiffness = float(msg.get("pin_stiffness", 8.0))
        cs = mod.collision_settings
        cs.distance_min = float(msg.get("distance", 0.005))
        cs.collision_quality = 4
        cs.use_self_collision = bool(msg.get("self_collision", False))
        cs.self_distance_min = max(0.5 * cs.distance_min, 0.002)
        frames = max(1, int(msg.get("frames", 40)))
        sc = bpy.context.scene
        sc.frame_start, sc.frame_end = 1, frames
        mod.point_cache.frame_start, mod.point_cache.frame_end = 1, frames
        for f in range(1, frames + 1):
            sc.frame_set(f)
            progress(f / frames, f"cloth frame {f} of {frames}")
        Vo, _ = mesh_io.evaluated_arrays(rib)
        mesh_io.save_npz(msg["out"], Vo, Fr)
        sc.frame_set(1)
        return {"out": msg["out"], "vertices": int(len(Vo)), "frames": frames}
    finally:
        for ob in made:
            mesh_io.remove(ob)


OPS = {"ping": op_ping, "check": op_check, "melt": op_melt, "wave": op_wave, "solidify": op_solidify,
       "cloth": op_cloth}

def op_remesh(msg, progress):
    import remesh
    return remesh.run(msg, progress)

OPS['remesh'] = op_remesh


# ----------------------------------------------------------------------------- loop

def main() -> int:
    t0 = time.perf_counter()
    proto = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
    os.dup2(2, 1)                       # C-level prints (Blender's own) now go to stderr
    sys.stdout = sys.stderr
    lock = threading.Lock()

    def send(msg: dict) -> None:
        with lock:
            proto.write(TAG + json.dumps(msg) + "\n")
            proto.flush()

    sys.path.insert(0, _here())
    try:
        # everything is imported before the stdin reader starts: on Windows, loading a DLL while another thread
        # blocks reading a pipe can stall
        import bpy
        import numpy
        import melt_gn  # noqa: F401
        import mesh_io  # noqa: F401
        import wave_gn  # noqa: F401
        reset_scene()
        missing = missing_nodes()
    except Exception as e:
        send({"ready": False, "fatal": True, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()})
        return 1
    inbox: queue.Queue = queue.Queue()
    cancelled: set = set()

    def reader() -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "cancel" in msg:
                cancelled.add(msg["cancel"])
                continue
            inbox.put(msg)
        os._exit(0)                     # the toolkit is gone: never linger

    threading.Thread(target=reader, daemon=True).start()
    send({"ready": True, "protocol": PROTOCOL, "server": SERVER_VERSION, "blender": bpy.app.version_string,
          "version": list(bpy.app.version), "numpy": numpy.__version__, "pid": os.getpid(),
          "startup_s": round(time.perf_counter() - t0, 3), "missing_nodes": missing})
    while True:
        msg = inbox.get()
        if msg.get("quit"):
            break
        rid = msg.get("id")

        def progress(f, text, rid=rid):
            if rid in cancelled:
                raise Cancelled()
            send({"id": rid, "progress": float(f), "msg": text})

        try:
            op = OPS.get(msg.get("op"))
            if op is None:
                raise ValueError(f"unknown operation {msg.get('op')!r}")
            reply = op(msg, progress)
            send(dict({"id": rid, "ok": True}, **reply))
        except Cancelled:
            send({"id": rid, "ok": False, "cancelled": True})
        except Exception as e:
            send({"id": rid, "ok": False, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()})
        finally:
            cancelled.discard(rid)
    return 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    if code:
        sys.exit(code)
