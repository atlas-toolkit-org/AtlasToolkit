"""Melt distortion as a Geometry Nodes modifier, run by Blender's own Python for the Atlas Toolkit's Melt tool
(src/atlas_toolkit/tools/mesh_melt). Ported from the Blender pieces (projects/blender3d_project, skill
plotter-contour-art: melt_distortion.py, with Augustus' drip_floor) and generalised:

- `head` False: no face terms (the toolkit zeroes them) and rivulets may run anywhere, not only off the face;
- `zone_front`, `zone_side`: the face zones scale with the head's own depth and width (1 = the v17 head);
- `head_centre`, `head_gain`, `head_x`, `head_y`: where the head is, when it is only part of the model (a bust);
- the Mesh to Volume resolution mode is set through a property (Blender 4.x) or its menu socket (5.x).
Everything else - the node graph, its constants, its defaults - is the pieces' own, so the v17 values give the
v17 melt.

    add_melt(obj, **params) -> NodesModifier

The model is normalised as in the pieces: centred on its bounding box, 2 units tall, Z up, its front at -Y.

Adds a non-destructive Geometry Nodes modifier that turns a normalised head
(2 units tall, centred, see import_and_normalize) into melting ice cream /
molten metal that is still readable as a face.

1. soften   Laplacian smoothing: heat takes the crisp sculpt detail first.
2. sag      a smooth *space warp*: the displacement depends only on position,
            with gentle gradients, so the surface can never tear or fold.
            The face slides down off the skull (more the lower it is, more on
            one side than the other); what sticks out (nose, lips, chin,
            earlobes) hangs lowest; melted material gathers in a heavy mass
            under the jaw; the crown slumps and spreads like a melting scoop.
3. pool     the head sinks a little into the floor; material below the pool
            surface is lifted back into a sloped, lobed, smoothed pool so
            horizontal slices draw it as nested rings.
4. drips    seeds on overhangs (brow, nose, lips, chin, jaw, earlobes) and on
            steep sides of the skull. Each drip starts as a straight fall and
            is snapped onto any surface it touches or passes just inside, so
            liquid runs over the lips and chin before dropping off; it ends
            where it would sink into the material below, and stubs are
            removed. Each drip is one tube: flared root, slightly thinning
            stream, swollen round bulb. Tubes are joined, not booleaned:
            ray-cast hidden-line removal already draws the union correctly,
            and the exporter chains each contour into the tube arc it meets,
            so a drip running over the surface costs no extra pen lifts.
"""
import math

MELT_DEFAULTS = dict(
    face_dir=(0.0, -1.0, 0.0),
    subdiv=1,              # Catmull-Clark levels first: smoother contours on paper
    base_flatten=0.04,     # flatten this sliver above the floor (ragged scan cuts)
    # 1. soften (iteration counts are per mesh edge: scale them with mesh density)
    soften=16, soften_weight=0.7,
    dissolve=0.0,          # 0..1: blend toward a feature-free proxy (1 = featureless blob)
    dissolve_voxel=0.06,   # proxy resolution: features smaller than this go first
    dissolve_smooth=20,    # blur iterations on the proxy
    dissolve_blur=40,      # blur iterations of the pull on the fine mesh
    # 2. sag (units of the normalised head)
    slump=0.05,            # uniform sink into the pool
    face_slide=0.24,       # face sliding off the skull, full at the chin
    face_zone=(0.30, 0.75),    # forward range where the face slide ramps in
    slide_z=(0.45, -0.60),     # none at the forehead, full from the mouth down
    asymmetry=0.50,        # one side of the face melts this much more
    protrude=0.10,         # extra droop of the most forward parts (nose tip)
    protrude_zone=(0.78, 0.92),
    forward_sag=0.15,      # sagging tissue also bulges forward (fraction of slide)
    jowl=0.05,             # cheeks bulge outward as they sag
    jaw_mass=0.11,         # melted material gathering under the jaw
    base_mound=0.05,       # material piling up around the base before it pools
    ear_droop=0.16,
    ear_zone=(0.55, 0.72),     # lateral range of the ears
    crown_sag=0.11,        # top of the head slumps
    crown_bulge=0.035,     # ... and spreads a little, like a melting scoop
    variation=0.40,        # low-frequency melt irregularity (0..1)
    variation_scale=1.4,
    flow_ripples=0.007,    # faint vertical flow streaks on the melting surface
    # 3. pool
    puddle_height=0.10,
    puddle_softness=0.02,
    puddle_spread=1.5,
    puddle_flatness=4.0,
    puddle_lobes=0.35,
    puddle_lobe_scale=0.8,
    puddle_smooth=40,
    # 4. drips
    hang_overhang=0.45,        # how downward-facing an overhang seed surface must be
    hang_spacing=0.085,        # Poisson minimum distance between overhang drips
    hang_len=(0.15, 0.70),
    hang_radius=(0.020, 0.045),
    run_spacing=0.09,          # rivulets on the steep sides of the skull
    run_density=45.0,
    run_min_height=0.95,       # above the floor
    run_len=(0.40, 1.00),
    run_radius=(0.018, 0.030),
    drip_clearance=0.25,       # no seeds this close above the floor
    hang_drips=True,           # drips hanging from overhangs
    run_drips=True,            # rivulets running down the skull
    seed_mode="final",         # "final": seed on the melted head; "rest": on the un-melted shape
                               # and carried by the melt (stable drips across a sequence)
    drip_amount=None,          # None: every seed drips at full length; 0..1+: progressive drips
    drip_grow=0.5,             # onset range over which a drip grows to full size
    snap_dist=0.09,            # an overhang drip within this of a surface runs on it
    run_snap_dist=0.30,        # rivulets cling harder: they follow the curve of the skull
    snap_deep=0.12,            # a drip ends once it would sink deeper than this
    drip_floor=None,           # a falling drip also ends this far above the floor (None = off):
                               # drips over open ground stop on it instead of hanging below it
    drip_min_len=0.10,         # drips shorter than this after trimming are removed (stubs)
    snap_lift=-0.3,            # attached drips: tube centre height above the surface / radius
    drip_bulb=1.35,            # bulb radius / stream radius
    drip_flare=0.3,            # extra root radius (fraction)
    drip_wiggle=0.010,
    drip_resolution=24,
    seed=0,
    head=True,                 # face terms and the face's no-rivulet zone (the toolkit passes False for objects)
    zone_front=1.0,            # head depth / the v17 head's (0.911): scales the forward zones
    zone_side=1.0,             # head half-width / the v17 head's (0.726): scales the lateral zones
    head_centre=0.0,           # the head's height band, as (z - head_centre) * head_gain = -1 .. 1
    head_gain=1.0,
    head_x=0.0,                # the head's horizontal centre (a bust's head is not at the box centre)
    head_y=0.0,
)


class _Tree:
    """Tiny helper to build node trees from Python without the boilerplate."""

    def __init__(self, ng):
        self.N, self.L = ng.nodes, ng.links

    def new(self, type_, **props):
        n = self.N.new(type_)
        for k, v in props.items():
            setattr(n, k, v)
        return n

    def set(self, sock, v):
        import bpy
        if isinstance(v, bpy.types.NodeSocket):
            self.L.new(v, sock)
        else:
            sock.default_value = v

    def enum(self, node, prop: str, prop_value: str, socket: str, socket_value: str):
        """An option that is a node property in Blender 4.x and a menu socket in 5.x."""
        if hasattr(node, prop):
            setattr(node, prop, prop_value)
        else:
            self.set(node.inputs[socket], socket_value)

    @staticmethod
    def sock(sockets, name):
        return next(s for s in sockets if s.name == name and s.enabled)

    def math(self, op, a, b=None, c=None):
        n = self.new("ShaderNodeMath", operation=op)
        for i, v in enumerate((a, b, c)):
            if v is not None:
                self.set(n.inputs[i], v)
        return n.outputs[0]

    def vmath(self, op, a, b=None, scale=None):
        n = self.new("ShaderNodeVectorMath", operation=op)
        self.set(n.inputs[0], a)
        if b is not None:
            self.set(n.inputs[1], b)
        if scale is not None:
            self.set(n.inputs[3], scale)
        return n.outputs[1] if op in ("LENGTH", "DOT_PRODUCT", "DISTANCE") else n.outputs[0]

    def xyz(self, v):
        n = self.new("ShaderNodeSeparateXYZ")
        self.set(n.inputs[0], v)
        return n.outputs

    def vec(self, x, y, z):
        n = self.new("ShaderNodeCombineXYZ")
        for i, v in enumerate((x, y, z)):
            self.set(n.inputs[i], v)
        return n.outputs[0]

    def ramp(self, v, a, b, tmin=0.0, tmax=1.0):
        """Smoothstep from tmin at a to tmax at b (a > b allowed)."""
        n = self.new("ShaderNodeMapRange", data_type="FLOAT", interpolation_type="SMOOTHSTEP", clamp=True)
        for name, val in (("Value", v), ("From Min", a), ("From Max", b), ("To Min", tmin), ("To Max", tmax)):
            self.set(self.sock(n.inputs, name), val)
        return n.outputs["Result"]

    def noise(self, vector, scale, detail=2.0, roughness=0.5, output="Factor"):
        n = self.new("ShaderNodeTexNoise", noise_dimensions="3D")
        self.set(n.inputs["Vector"], vector)
        self.set(n.inputs["Scale"], scale)
        self.set(n.inputs["Detail"], detail)
        self.set(n.inputs["Roughness"], roughness)
        return n.outputs[output]

    def blur(self, value, data_type, iterations, weight=1.0):
        n = self.new("GeometryNodeBlurAttribute", data_type=data_type)
        self.set(self.sock(n.inputs, "Value"), value)
        self.set(n.inputs["Iterations"], iterations)
        self.set(n.inputs["Weight"], weight)
        return self.sock(n.outputs, "Value")

    def store(self, geo, name, value, data_type, domain="POINT"):
        n = self.new("GeometryNodeStoreNamedAttribute", data_type=data_type, domain=domain)
        self.set(n.inputs["Geometry"], geo)
        self.set(n.inputs["Name"], name)
        self.set(self.sock(n.inputs, "Value"), value)
        return n.outputs[0]

    def named(self, name, data_type="FLOAT"):
        n = self.new("GeometryNodeInputNamedAttribute", data_type=data_type)
        self.set(n.inputs["Name"], name)
        return self.sock(n.outputs, "Attribute")

    def set_position(self, geo, offset=None, position=None, selection=None):
        n = self.new("GeometryNodeSetPosition")
        self.set(n.inputs["Geometry"], geo)
        if selection is not None:
            self.set(n.inputs["Selection"], selection)
        if offset is not None:
            self.set(n.inputs["Offset"], offset)
        if position is not None:
            self.set(n.inputs["Position"], position)
        return n.outputs[0]

    def random(self, lo, hi, seed):
        n = self.new("FunctionNodeRandomValue", data_type="FLOAT")
        self.set(self.sock(n.inputs, "Min"), lo)
        self.set(self.sock(n.inputs, "Max"), hi)
        self.set(n.inputs["Seed"], seed)
        return self.sock(n.outputs, "Value")

    def lerp(self, a, b, w):
        return self.vmath("ADD", a, self.vmath("SCALE", self.vmath("SUBTRACT", b, a), scale=w))

    def softplus(self, x):
        """ln(1 + e^x): a smooth max(0, x)."""
        return self.math("LOGARITHM", self.math("ADD", self.math("EXPONENT", x), 1.0), math.e)

    def join(self, *geos):
        n = self.new("GeometryNodeJoinGeometry")
        for g in reversed(geos):
            self.set(n.inputs[0], g)
        return n.outputs[0]


def _drip_radius(T, t, r, length, p):
    """Drip profile: rounded start -> slightly wider root -> thinning stream -> bulb -> round tip.
    t is the 0..1 position along the drip, r the stream radius, length its length."""
    tc = T.math("MAXIMUM", T.math("SUBTRACT", 1.0, T.math("DIVIDE", T.math("MULTIPLY", r, p["drip_bulb"]), length)), 0.5)
    body = T.math("SUBTRACT", T.math("ADD", 1.0, T.math("MULTIPLY", T.ramp(t, 0.25, 0.0), p["drip_flare"])),
                  T.math("MULTIPLY", T.ramp(t, 0.3, 0.8), 0.15))
    w = T.ramp(t, T.math("SUBTRACT", tc, 0.22), tc)
    shape = T.math("ADD", T.math("MULTIPLY", body, T.math("SUBTRACT", 1.0, w)), T.math("MULTIPLY", w, p["drip_bulb"]))
    # hemispherical end: tip of the bulb
    s = T.math("DIVIDE", T.math("SUBTRACT", T.math("MAXIMUM", t, tc), tc), T.math("SUBTRACT", 1.0, tc))
    cap = T.math("SQRT", T.math("MAXIMUM", T.math("SUBTRACT", 1.0, T.math("MULTIPLY", s, s)), 0.0))
    # hemispherical start: no flat disk where a drip leaves the surface
    s0 = T.math("SUBTRACT", 1.0, T.math("MINIMUM", T.math("DIVIDE", T.math("MULTIPLY", t, length), r), 1.0))
    cap0 = T.math("SQRT", T.math("MAXIMUM", T.math("SUBTRACT", 1.0, T.math("MULTIPLY", s0, s0)), 0.0))
    return T.math("MULTIPLY", T.math("MULTIPLY", r, shape), T.math("MULTIPLY", cap, cap0))


def _seeds(T, head, spacing, density_max, factor, seed, radius, length, snap, kind):
    dist = T.new("GeometryNodeDistributePointsOnFaces", distribute_method="POISSON")
    T.set(dist.inputs["Mesh"], head)
    T.set(dist.inputs["Distance Min"], spacing)
    T.set(dist.inputs["Density Max"], density_max)
    T.set(dist.inputs["Density Factor"], factor)
    T.set(dist.inputs["Seed"], seed)
    pts = T.store(dist.outputs["Points"], "drip_rad", T.random(radius[0], radius[1], seed + 101), "FLOAT")
    pts = T.store(pts, "drip_kind", kind, "FLOAT")            # 0 hanging, 1 rivulet
    pts = T.store(pts, "drip_snap", snap, "FLOAT")
    return T.store(pts, "drip_len", T.random(length[0], length[1], seed + 202), "FLOAT")


def build_melt_group(name, p):
    import bpy
    ng = bpy.data.node_groups.new(name, "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    T = _Tree(ng)
    gi, go = T.new("NodeGroupInput"), T.new("NodeGroupOutput")
    pos = T.new("GeometryNodeInputPosition").outputs[0]
    nrm = T.new("GeometryNodeInputNormal").outputs[0]
    s = int(p["seed"]) % 2_000_000_000          # seed + 202 must fit Blender's int32 sockets
    o = s % 9973                                 # noise offsets stay where float32 still has detail
    seed_off = (o * 17.13 + 3.1, o * 5.71 + 7.7, o * 11.37 + 1.3)
    fdir = p["face_dir"]
    side = (-fdir[1], fdir[0], 0.0)                     # horizontal, perpendicular to the face

    geo = gi.outputs[0]
    if p["subdiv"]:
        sub = T.new("GeometryNodeSubdivisionSurface")
        T.set(sub.inputs["Mesh"], geo)
        T.set(sub.inputs["Level"], p["subdiv"])
        geo = sub.outputs[0]
    bbox = T.new("GeometryNodeBoundBox")
    T.set(bbox.inputs[0], geo)
    floor = T.xyz(bbox.outputs["Min"])[2]
    # flatten the lowest sliver: scanned busts often have a ragged cut at the base
    bx, by, bz = T.xyz(pos)
    geo = T.set_position(geo, position=T.vec(bx, by, T.math("MAXIMUM", floor, T.math(
        "SUBTRACT", bz, T.ramp(bz, T.math("ADD", floor, p["base_flatten"]), floor, 0.0, p["base_flatten"])))))
    geo = T.store(geo, "rest_position", pos, "FLOAT_VECTOR")
    rest_geo = geo

    # --- 1. soften ----------------------------------------------------------
    if p["soften"]:
        geo = T.set_position(geo, position=T.blur(pos, "FLOAT_VECTOR", p["soften"], p["soften_weight"]))

    # --- 1b. dissolve: pull the surface toward a feature-free proxy -------------
    # (a coarse voxel rebuild of the head, smoothed): eyes and lips melt away first,
    # the massing of brow, nose and skull survives longest
    if p["dissolve"] > 0:
        m2v = T.new("GeometryNodeMeshToVolume")
        T.set(m2v.inputs["Mesh"], geo)
        T.enum(m2v, "resolution_mode", "VOXEL_SIZE", "Resolution Mode", "Size")
        T.set(m2v.inputs["Voxel Size"], p["dissolve_voxel"])
        v2m = T.new("GeometryNodeVolumeToMesh")
        T.set(v2m.inputs["Volume"], m2v.outputs[0])
        T.set(v2m.inputs["Threshold"], 0.5)
        proxy = T.set_position(v2m.outputs[0], position=T.blur(pos, "FLOAT_VECTOR", p["dissolve_smooth"]))
        dprox = T.new("GeometryNodeProximity", target_element="FACES")
        T.set(dprox.inputs["Geometry"], proxy)
        T.set(dprox.inputs["Sample Position"], pos)
        # the pull itself is smoothed over the fine mesh so no voxel facet survives
        geo = T.store(geo, "dissolve_pull", T.vmath("SUBTRACT", dprox.outputs["Position"], pos), "FLOAT_VECTOR")
        pull = T.blur(T.named("dissolve_pull", "FLOAT_VECTOR"), "FLOAT_VECTOR", p["dissolve_blur"])
        geo = T.set_position(geo, offset=T.vmath("SCALE", pull, scale=p["dissolve"]))

    # --- 2. sag: smooth space warp driven by the rest position --------------
    rest = T.named("rest_position", "FLOAT_VECTOR")
    rx, ry, rz = T.xyz(rest)
    hxy = (p["head_x"], p["head_y"], 0.0)
    rest_h = T.vmath("SUBTRACT", rest, hxy)                 # relative to the head's own axis
    fwd = T.math("DIVIDE", T.vmath("DOT_PRODUCT", rest_h, fdir), p["zone_front"])
    lat_s = T.math("DIVIDE", T.vmath("DOT_PRODUCT", rest_h, side), p["zone_side"])
    rzh = T.math("MULTIPLY", T.math("SUBTRACT", rz, p["head_centre"]), p["head_gain"])   # height within the head
    lat = T.math("ABSOLUTE", lat_s)
    var = T.math("ADD", 1.0 - p["variation"], T.math(
        "MULTIPLY", T.noise(T.vmath("ADD", rest, seed_off), p["variation_scale"], 2.0), 2 * p["variation"]))
    asym = T.math("ADD", 1.0, T.math("MULTIPLY", T.math("DIVIDE", lat_s, 0.7), p["asymmetry"]))
    slide = T.math("MULTIPLY", T.math("MULTIPLY", T.ramp(fwd, *p["face_zone"]), T.ramp(rzh, *p["slide_z"])),
                   T.math("MULTIPLY", asym, p["face_slide"]))
    prot = T.math("MULTIPLY", T.ramp(fwd, *p["protrude_zone"]), T.math("MULTIPLY", asym, p["protrude"]))
    ear = T.math("MULTIPLY", T.math("MULTIPLY", T.ramp(lat, *p["ear_zone"]), T.ramp(rzh, 0.45, -0.30)),
                 p["ear_droop"])
    droop = T.math("MULTIPLY", T.math("ADD", T.math("ADD", slide, prot), ear), var)
    crown = T.math("MULTIPLY", T.ramp(rz, -0.2, 1.0), p["crown_sag"])
    dz = T.math("MULTIPLY", T.math("ADD", T.math("ADD", droop, crown), p["slump"]), -1.0)
    hdir = T.vmath("NORMALIZE", T.vec(rx, ry, 0.0))
    jowl = T.math("MULTIPLY", T.math("MULTIPLY", T.ramp(fwd, 0.2, 0.6), T.ramp(lat, 0.15, 0.45)),
                  T.math("MULTIPLY", T.ramp(rzh, -0.05, -0.40), T.ramp(rzh, -0.85, -0.55)))
    jaw = T.math("MULTIPLY", T.ramp(fwd, -0.15, 0.30),
                 T.math("MULTIPLY", T.ramp(rzh, -0.98, -0.86), T.ramp(rzh, -0.50, -0.70)))
    rim = T.math("MULTIPLY", T.ramp(rz, 0.05, 0.35), T.ramp(rz, 0.75, 0.45))
    mound = T.math("MULTIPLY", T.ramp(rz, -1.0, -0.9), T.ramp(rz, -0.55, -0.82))
    spread = T.math("ADD", T.math("ADD", T.math("MULTIPLY", jowl, p["jowl"]),
                                  T.math("MULTIPLY", jaw, T.math("MULTIPLY", asym, p["jaw_mass"]))),
                    T.math("ADD", T.math("MULTIPLY", rim, p["crown_bulge"]), T.math("MULTIPLY", mound, p["base_mound"])))
    spread = T.math("MULTIPLY", spread, var)
    offset = T.vmath("ADD", T.vec(0.0, 0.0, dz), T.vmath("SCALE", hdir, scale=spread))
    offset = T.vmath("ADD", offset, T.vmath("SCALE", fdir, scale=T.math("MULTIPLY", slide, p["forward_sag"])))
    geo = T.set_position(geo, offset=offset)
    if p["flow_ripples"]:
        # vertically stretched noise along the normal, where material is moving
        streak = T.math("SUBTRACT", T.noise(T.vmath("MULTIPLY", T.vmath("ADD", rest, seed_off), (1.0, 1.0, 0.18)),
                                            11.0, 1.0, 0.4), 0.5)
        # (faded out near the floor: streaks there would turn into radial fins in the pool)
        moving = T.math("MULTIPLY", T.math("MINIMUM", T.math("ADD", T.math("MULTIPLY", droop, 6.0), 0.25), 1.0),
                        T.ramp(rz, T.math("ADD", floor, 0.12), T.math("ADD", floor, 0.40)))
        geo = T.set_position(geo, offset=T.vmath("SCALE", nrm, scale=T.math(
            "MULTIPLY", T.math("MULTIPLY", streak, 2 * p["flow_ripples"]), moving)))

    # --- 3. pool ----------------------------------------------------------------
    stat = T.new("GeometryNodeAttributeStatistic", data_type="FLOAT_VECTOR", domain="POINT")
    T.set(stat.inputs["Geometry"], geo)
    T.set(stat.inputs["Selection"], T.math("LESS_THAN", rz, T.math("ADD", floor, 0.1)))
    T.set(T.sock(stat.inputs, "Attribute"), rest)
    cx, cy, _ = T.xyz(T.sock(stat.outputs, "Mean"))
    px, py, pz = T.xyz(pos)
    radial = T.vec(T.math("SUBTRACT", px, cx), T.math("SUBTRACT", py, cy), 0.0)
    rdir = T.vmath("NORMALIZE", radial)
    h, soft = p["puddle_height"], p["puddle_softness"]
    # the pool is shaped from a smoothed height field (scanned bases are ragged), and
    # inside the pool the height is replaced outright, so its rings come out clean
    u = T.math("SUBTRACT", T.blur(pz, "FLOAT", p["puddle_smooth"]), floor)
    lift = T.math("MULTIPLY", T.softplus(T.math("DIVIDE", T.math("SUBTRACT", h, u), soft)), soft)
    full = p["slump"] + h                      # lift of the original base ring -> the rim
    rimf = T.math("MAXIMUM", T.math("SUBTRACT", 1.0, T.math(
        "POWER", T.math("MINIMUM", T.math("DIVIDE", lift, full), 1.0), p["puddle_flatness"])), 0.0)
    new_u = T.math("MULTIPLY", T.math("ADD", u, lift), rimf)
    in_pool = T.ramp(lift, 0.005, 0.04)
    z_new = T.math("ADD", T.math("MULTIPLY", T.math("ADD", pz, T.math("SUBTRACT", new_u, u)),
                                 T.math("SUBTRACT", 1.0, in_pool)),
                   T.math("MULTIPLY", T.math("ADD", floor, new_u), in_pool))
    lobes = T.ramp(T.noise(T.vmath("ADD", rdir, seed_off), p["puddle_lobe_scale"], 1.0),
                   0.3, 0.7, 1 - p["puddle_lobes"], 1 + p["puddle_lobes"])
    no_tear = T.ramp(T.vmath("LENGTH", radial), 0.0, 0.15)
    pspread = T.math("MULTIPLY", T.math("MULTIPLY", lift, p["puddle_spread"]), T.math("MULTIPLY", lobes, no_tear))
    geo = T.store(geo, "pool", T.ramp(lift, 0.003, 0.03), "FLOAT")
    geo = T.set_position(geo, offset=T.vmath("ADD", T.vmath("SCALE", rdir, scale=pspread),
                                             T.vec(0.0, 0.0, T.math("SUBTRACT", z_new, pz))))
    if p["puddle_smooth"]:
        smooth = T.blur(pos, "FLOAT_VECTOR", p["puddle_smooth"])
        geo = T.set_position(geo, position=T.lerp(pos, smooth, T.named("pool")))
    head = geo

    # --- 4. drips -------------------------------------------------------------
    nz = T.xyz(nrm)[2]
    above_floor = T.ramp(pz, T.math("ADD", floor, p["drip_clearance"] * 0.6),
                         T.math("ADD", floor, p["drip_clearance"]))
    hang_f = T.math("MULTIPLY", T.ramp(T.math("MULTIPLY", nz, -1.0), p["hang_overhang"], p["hang_overhang"] + 0.25),
                    above_floor)
    # seeds on the melted head ("final"), or on the un-melted rest shape ("rest"): rest seeds
    # are identical for every melt strength and are carried along by the melt, so across a
    # sequence the same drips grow in the same places
    seed_geo = rest_geo if p["seed_mode"] == "rest" else head
    hang = _seeds(T, seed_geo, p["hang_spacing"], 120.0, hang_f, s, p["hang_radius"], p["hang_len"],
                  p["snap_dist"], 0.0)
    fwd_now = T.math("DIVIDE", T.vmath("DOT_PRODUCT", T.vmath("SUBTRACT", pos, hxy), fdir), p["zone_front"])
    off_face = T.ramp(fwd_now, 0.55, 0.35) if p["head"] else 1.0          # rivulets stay off a face
    run_f = T.math("MULTIPLY", T.math("MULTIPLY", T.ramp(T.math("ABSOLUTE", nz), 0.50, 0.25),
                                      T.ramp(pz, T.math("ADD", floor, p["run_min_height"]),
                                             T.math("ADD", floor, p["run_min_height"] + 0.15))),
                   T.math("MULTIPLY", off_face,
                          T.ramp(T.noise(T.vmath("ADD", pos, seed_off), 2.5, 1.0), 0.42, 0.58)))
    run = _seeds(T, seed_geo, p["run_spacing"], p["run_density"], run_f, s + 7, p["run_radius"], p["run_len"],
                 p["run_snap_dist"], 1.0)
    pts = T.join(*([hang] if p["hang_drips"] else []) + ([run] if p["run_drips"] else []))
    if p["drip_amount"] is not None:
        # each drip has an onset in 0..1: it appears once drip_amount passes it and grows
        # to full length / radius over drip_grow; early drips are the longest
        mat = T.math("MINIMUM", T.math("MAXIMUM", T.math("DIVIDE", T.math(
            "SUBTRACT", p["drip_amount"], T.random(0.0, 1.0, s + 303)), p["drip_grow"]), 0.0), 1.0)
        pts = T.store(pts, "drip_mat", mat, "FLOAT")
        unborn = T.new("GeometryNodeDeleteGeometry", domain="POINT")
        T.set(unborn.inputs["Geometry"], pts)
        T.set(unborn.inputs["Selection"], T.math("LESS_THAN", T.named("drip_mat"), 0.001))
        pts = unborn.outputs[0]
        m_ = T.named("drip_mat")
        pts = T.store(pts, "drip_len", T.math("MULTIPLY", T.named("drip_len"),
                                              T.math("ADD", 0.25, T.math("MULTIPLY", m_, 0.75))), "FLOAT")
        pts = T.store(pts, "drip_rad", T.math("MULTIPLY", T.named("drip_rad"),
                                              T.math("ADD", 0.7, T.math("MULTIPLY", m_, 0.3))), "FLOAT")
    if p["seed_mode"] == "rest":
        # carry each seed onto the melted surface: the head with its rest positions restored
        # carries the melted position as an attribute, sampled at the seed's rest position
        carrier = T.store(T.store(head, "final_pos", pos, "FLOAT_VECTOR"), "final_nrm", nrm, "FLOAT_VECTOR")
        carrier = T.set_position(carrier, position=T.named("rest_position", "FLOAT_VECTOR"))
        csns = T.new("GeometryNodeSampleNearestSurface", data_type="FLOAT_VECTOR")
        T.set(csns.inputs["Mesh"], carrier)
        T.set(T.sock(csns.inputs, "Value"), T.named("final_pos", "FLOAT_VECTOR"))
        T.set(csns.inputs["Sample Position"], pos)
        nsns = T.new("GeometryNodeSampleNearestSurface", data_type="FLOAT_VECTOR")
        T.set(nsns.inputs["Mesh"], carrier)
        T.set(T.sock(nsns.inputs, "Value"), T.named("final_nrm", "FLOAT_VECTOR"))
        T.set(nsns.inputs["Sample Position"], pos)
        pts = T.store(pts, "seed_nz", T.xyz(T.sock(nsns.outputs, "Value"))[2], "FLOAT")
        pts = T.set_position(pts, position=T.sock(csns.outputs, "Value"))
        # seeds that melted down into the pool are gone, and a hanging drip needs its
        # surface to still face down after the melt (else it would stand up like a peg)
        gone = T.math("MAXIMUM", T.math("LESS_THAN", pz, T.math("ADD", floor, p["drip_clearance"])),
                      T.math("MULTIPLY", T.math("LESS_THAN", T.named("drip_kind"), 0.5),
                             T.math("GREATER_THAN", T.named("seed_nz"), -0.25)))
        sunk = T.new("GeometryNodeDeleteGeometry", domain="POINT")
        T.set(sunk.inputs["Geometry"], pts)
        T.set(sunk.inputs["Selection"], gone)
        pts = sunk.outputs[0]
    r0 = T.named("drip_rad")
    pts = T.store(pts, "drip_total", T.math("ADD", T.named("drip_len"), T.math("MULTIPLY", r0, 0.5)), "FLOAT")
    pts = T.set_position(pts, offset=T.vec(0.0, 0.0, T.math("MULTIPLY", r0, 0.5)))  # root just inside
    line = T.new("GeometryNodeCurvePrimitiveLine")
    T.set(line.inputs["Start"], (0.0, 0.0, 0.0))
    T.set(line.inputs["End"], (0.0, 0.0, -1.0))
    inst = T.new("GeometryNodeInstanceOnPoints")
    T.set(inst.inputs["Points"], pts)
    T.set(inst.inputs["Instance"], line.outputs[0])
    T.set(inst.inputs["Scale"], T.vec(1.0, 1.0, T.named("drip_total")))
    real = T.new("GeometryNodeRealizeInstances")
    T.set(real.inputs["Geometry"], inst.outputs[0])
    res = T.new("GeometryNodeResampleCurve")
    T.set(res.inputs["Curve"], real.outputs[0])
    T.set(res.inputs["Count"], 96)
    curves = res.outputs[0]
    t = T.new("GeometryNodeSplineParameter").outputs["Factor"]
    # lateral wander of the free fall
    wob = T.vmath("SUBTRACT", T.noise(T.vmath("ADD", pos, seed_off), 5.0, 1.0, output="Color"), (0.5, 0.5, 0.5))
    curves = T.set_position(curves, offset=T.vmath("SCALE", T.vmath(
        "MULTIPLY", wob, (2 * p["drip_wiggle"], 2 * p["drip_wiggle"], 0.0)), scale=T.ramp(t, 0.05, 0.5)))
    # snap onto surfaces the fall touches or passes just inside
    prox = T.new("GeometryNodeProximity", target_element="FACES")
    T.set(prox.inputs["Geometry"], head)
    T.set(prox.inputs["Sample Position"], pos)
    q, d = prox.outputs["Position"], prox.outputs["Distance"]
    sns = T.new("GeometryNodeSampleNearestSurface", data_type="FLOAT_VECTOR")
    T.set(sns.inputs["Mesh"], head)
    T.set(T.sock(sns.inputs, "Value"), nrm)
    T.set(sns.inputs["Sample Position"], pos)
    qn = T.vmath("NORMALIZE", T.sock(sns.outputs, "Value"))
    inside = T.math("LESS_THAN", T.vmath("DOT_PRODUCT", T.vmath("SUBTRACT", pos, q), qn), 0.0)
    r_est = _drip_radius(T, t, T.named("drip_rad"), T.named("drip_total"), p)
    deep = T.math("MULTIPLY", inside, T.math("GREATER_THAN", d, p["snap_deep"]))
    snap = T.named("drip_snap")
    w_out = T.math("MULTIPLY", T.math("SUBTRACT", 1.0, inside), T.ramp(d, snap, T.math("MULTIPLY", snap, 0.85)))
    attach = T.math("MAXIMUM", T.math("SUBTRACT", inside, deep), w_out)
    curves = T.store(curves, "attached", attach, "FLOAT")
    curves = T.store(curves, "deep", deep, "FLOAT")
    # a drip ends where it would sink into the material below (not at its root)
    cop = T.new("GeometryNodeCurveOfPoint")
    acc = T.new("GeometryNodeAccumulateField", data_type="FLOAT", domain="POINT")
    ended = T.named("deep")
    if p["drip_floor"] is not None:
        below = T.math("LESS_THAN", T.xyz(pos)[2], T.math("ADD", floor, p["drip_floor"]))
        ended = T.math("MAXIMUM", ended, below)
    T.set(T.sock(acc.inputs, "Value"), T.math("MULTIPLY", ended, T.math("GREATER_THAN", t, 0.08)))
    T.set(acc.inputs["Group ID"], cop.outputs["Curve Index"])
    trim = T.new("GeometryNodeDeleteGeometry", domain="POINT")
    T.set(trim.inputs["Geometry"], curves)
    T.set(trim.inputs["Selection"], T.math("GREATER_THAN", T.sock(acc.outputs, "Leading"), 0.5))
    stubs = T.new("GeometryNodeDeleteGeometry", domain="CURVE")
    T.set(stubs.inputs["Geometry"], trim.outputs[0])
    T.set(stubs.inputs["Selection"], T.math("LESS_THAN", T.new("GeometryNodeSplineLength").outputs["Length"],
                                            p["drip_min_len"]))
    curves = stubs.outputs[0]
    # where attached, the drip sits on the surface (partly embedded): a rounded ridge
    target = T.vmath("ADD", q, T.vmath("SCALE", qn, scale=T.math("MULTIPLY", r_est, p["snap_lift"])))
    curves = T.set_position(curves, position=T.lerp(pos, target, T.named("attached")))
    # once a drip lets go of the surface it falls straight down from where it let go,
    # not from its original fall line: take x, y from the last attached point of the drip
    held = T.math("GREATER_THAN", T.named("attached"), 0.5)
    cop2 = T.new("GeometryNodeCurveOfPoint")
    acc2 = T.new("GeometryNodeAccumulateField", data_type="FLOAT", domain="POINT")
    T.set(T.sock(acc2.inputs, "Value"), held)
    T.set(acc2.inputs["Group ID"], cop2.outputs["Curve Index"])
    n_held = T.sock(acc2.outputs, "Leading")                      # attached points so far
    back = T.math("SUBTRACT", T.math("SUBTRACT", n_held, 1.0), cop2.outputs["Index in Curve"])
    opc = T.new("GeometryNodeOffsetPointInCurve")
    T.set(opc.inputs["Point Index"], T.new("GeometryNodeInputIndex").outputs[0])
    T.set(opc.inputs["Offset"], back)
    lastp = T.new("GeometryNodeSampleIndex", data_type="FLOAT_VECTOR", domain="POINT")
    T.set(lastp.inputs["Geometry"], curves)
    T.set(T.sock(lastp.inputs, "Value"), pos)
    T.set(lastp.inputs["Index"], opc.outputs["Point Index"])
    lx, ly, _ = T.xyz(T.sock(lastp.outputs, "Value"))
    _, _, cz = T.xyz(pos)
    falling = T.math("MULTIPLY", T.math("SUBTRACT", 1.0, held), T.math("GREATER_THAN", n_held, 0.5))
    curves = T.set_position(curves, selection=falling, position=T.vec(lx, ly, cz))
    curves = T.set_position(curves, position=T.lerp(pos, T.blur(pos, "FLOAT_VECTOR", 4), T.ramp(t, 0.0, 0.04)))
    # final profile from the real (snapped) length. Every drip is one tube: running on the
    # surface, necking off an edge, hanging, ending in a bulb. Hidden-line removal draws the
    # union with the head, and the exporter chains each contour into the tube arc it meets.
    slen = T.new("GeometryNodeSplineLength").outputs["Length"]
    r_prof = _drip_radius(T, t, T.named("drip_rad"), slen, p)
    circle = T.new("GeometryNodeCurvePrimitiveCircle", mode="RADIUS")
    T.set(circle.inputs["Resolution"], p["drip_resolution"])
    T.set(circle.inputs["Radius"], 1.0)
    tube = T.new("GeometryNodeCurveToMesh")
    T.set(tube.inputs["Curve"], curves)
    T.set(tube.inputs["Profile Curve"], circle.outputs[0])
    T.set(tube.inputs["Scale"], r_prof)
    T.set(tube.inputs["Fill Caps"], True)

    T.set(go.inputs[0], T.join(head, tube.outputs[0]))
    return ng


def add_melt(obj, **params):
    p = {**MELT_DEFAULTS, **params}
    mod = obj.modifiers.new("MeltDistortion", "NODES")
    mod.node_group = build_melt_group(obj.name + "_Melt", p)
    return mod
