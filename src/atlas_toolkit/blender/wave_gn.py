"""The Blender pieces' twist ("wave") as Geometry Nodes, verbatim from the skill plotter-contour-art
(head_plot_pipeline.build_wave_group). The toolkit's Twist computes the same field with numpy; this copy is only
there so a test can check the two agree (the server's "wave" operation)."""


WAVE_DEFAULTS = dict(
    sway_amp=0.07,       # lateral S-sway (units)
    sway_freq=3.6,       # cycles-ish per unit height
    sway_axis=(0, 1, 0),  # lateral direction of the sway
    depth_axis=(1, 0, 0),  # secondary, quarter-phase sway direction
    twist_amp=0.0,       # radians of undulating twist about Z
    twist_freq=2.4,
    ripple_amp=0.012,    # displacement along the normal
    ripple_freq=26.0,
    ripple_lobes=0,      # >0 spirals the ripple around the head
    ripple_axis_fade=0.0,  # >0: fade the ripple out within this radius of the vertical axis
    phase=0.6,
)


def build_wave_group(name, p):
    import bpy
    ng = bpy.data.node_groups.new(name, "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    for label, key in [("Sway Amplitude", "sway_amp"), ("Sway Frequency", "sway_freq"),
                       ("Twist Amplitude", "twist_amp"), ("Twist Frequency", "twist_freq"),
                       ("Ripple Amplitude", "ripple_amp"), ("Ripple Frequency", "ripple_freq"),
                       ("Ripple Lobes", "ripple_lobes"), ("Phase", "phase")]:
        s = ng.interface.new_socket(label, in_out="INPUT", socket_type="NodeSocketFloat")
        s.default_value = p[key]
    N, L = ng.nodes, ng.links
    gi, go = N.new("NodeGroupInput"), N.new("NodeGroupOutput")
    pos, nrm = N.new("GeometryNodeInputPosition"), N.new("GeometryNodeInputNormal")
    sep = N.new("ShaderNodeSeparateXYZ")
    L.new(pos.outputs[0], sep.inputs[0])
    x, y, z = sep.outputs

    def math_(op, a, b=None, bv=None):
        m = N.new("ShaderNodeMath")
        m.operation = op
        L.new(a, m.inputs[0])
        if b is not None:
            L.new(b, m.inputs[1])
        if bv is not None:
            m.inputs[1].default_value = bv
        return m.outputs[0]

    def vec(op, a, b=None, scale=None, const=None):
        v = N.new("ShaderNodeVectorMath")
        v.operation = op
        L.new(a, v.inputs[0])
        if b is not None:
            L.new(b, v.inputs[1])
        if const is not None:
            v.inputs[1].default_value = const
        if scale is not None:
            L.new(scale, v.inputs[3])
        return v.outputs[0]

    def axis(value):
        c = N.new("FunctionNodeInputVector")
        c.vector = value
        return c.outputs[0]

    # amplitude grows from chin (0.5) to crown (1.2)
    grow = math_("ADD", math_("MULTIPLY", z, bv=0.35), bv=0.85)
    # lateral sway + quarter-phase depth sway
    ph = math_("ADD", math_("MULTIPLY", z, gi.outputs["Sway Frequency"]), gi.outputs["Phase"])
    sway = math_("MULTIPLY", math_("MULTIPLY", math_("SINE", ph), gi.outputs["Sway Amplitude"]), grow)
    depth = math_("MULTIPLY", math_("MULTIPLY", math_("COSINE", math_("ADD", ph, bv=0.7)),
                                     gi.outputs["Sway Amplitude"]), bv=0.35)
    off = vec("ADD", vec("SCALE", axis(p["sway_axis"]), scale=sway),
              vec("SCALE", axis(p["depth_axis"]), scale=depth))
    # undulating twist about the vertical axis
    ang = math_("MULTIPLY", math_("SINE", math_("ADD", math_("MULTIPLY", z, gi.outputs["Twist Frequency"]),
                                                 gi.outputs["Phase"])), gi.outputs["Twist Amplitude"])
    rot = N.new("ShaderNodeVectorRotate")
    rot.rotation_type = "Z_AXIS"
    L.new(pos.outputs[0], rot.inputs["Vector"])
    L.new(ang, rot.inputs["Angle"])
    off = vec("ADD", off, vec("SUBTRACT", rot.outputs[0], pos.outputs[0]))
    # ripple along the normal, optionally spiralling around the head
    theta = math_("ARCTAN2", y, x)
    rph = math_("ADD", math_("MULTIPLY", z, gi.outputs["Ripple Frequency"]),
                math_("MULTIPLY", theta, gi.outputs["Ripple Lobes"]))
    ripple = math_("MULTIPLY", math_("SINE", rph), gi.outputs["Ripple Amplitude"])
    if p["ripple_axis_fade"] > 0:
        # the spiral phase (angle about Z) is undefined on the vertical axis: fade the ripple
        # out near it, or the crown top and base centre crumple
        rxy = math_("SQRT", math_("ADD", math_("MULTIPLY", x, x), math_("MULTIPLY", y, y)))
        fade = math_("MINIMUM", math_("DIVIDE", rxy, bv=p["ripple_axis_fade"]), bv=1.0)
        ripple = math_("MULTIPLY", ripple, math_("MULTIPLY", fade, fade))
    off = vec("ADD", off, vec("SCALE", nrm.outputs[0], scale=ripple))

    sp = N.new("GeometryNodeSetPosition")
    L.new(gi.outputs["Geometry"], sp.inputs["Geometry"])
    L.new(off, sp.inputs["Offset"])
    L.new(sp.outputs[0], go.inputs[0])
    return ng


def add_wave(obj, subdiv_levels=0, **params):
    p = {**WAVE_DEFAULTS, **params}
    if subdiv_levels:
        sub = obj.modifiers.new("Subdiv", "SUBSURF")
        sub.levels = sub.render_levels = subdiv_levels
    ng = build_wave_group(obj.name + "_Wave", p)
    mod = obj.modifiers.new("WaveDistortion", "NODES")
    mod.node_group = ng
    return mod
