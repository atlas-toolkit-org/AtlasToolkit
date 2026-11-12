"""
Declarative tool parameters. One schema drives the inspector form, presets, saved settings, the CLI, the run record
and the <metadata> written into SVG outputs, so a tool never builds its own widgets or argument parser.

A Float or Int with `suggest` accepts the value AUTO: it is then computed per input file from that file's
InputInfo (for example a voxel edge of max_dim / 50), right before the engine runs.

Settings, workflows and presets outlive the schema that wrote them. When a parameter is renamed, list its old keys in
`former=("old_key",)`; when a choice value is renamed, map it in `former_values={"old": "new"}`. resolve() applies
both, so every saved file keeps working without a version check (see docs/PRESETS.md).
"""
from __future__ import annotations

import argparse
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

AUTO = "auto"


class ParamError(ValueError):
    pass


@dataclass
class Param:
    key: str
    label: str
    default: Any = None
    # keyword-only, so a subclass's own fields come right after the default: Choice(key, label, default, choices)
    help: str = field(default="", kw_only=True)
    advanced: bool = field(default=False, kw_only=True)            # shown under "Advanced" in the inspector
    visible_if: dict | Callable | None = field(default=None, kw_only=True)   # {"style": ("a", "b")} or fn(values)
    unit: str = field(default="", kw_only=True)
    former: tuple = field(default=(), kw_only=True)                 # keys this parameter had before a rename
    former_values: dict = field(default_factory=dict, kw_only=True)  # renamed values: {"old": "new"}
    kind = "param"

    # --------------------------------------------------------------- values
    def coerce(self, v: Any) -> Any:
        return v

    def validate(self, v: Any) -> Any:
        if v is not None and self.former_values:
            try:
                v = self.former_values.get(v, v)
            except TypeError:        # an unhashable value (a list) is never a renamed one
                pass
        return self.coerce(self.default if v is None else v)

    def lookup(self, values: dict) -> Any:
        """This parameter's raw value in `values`, under its key or a former one; None when absent."""
        v = values.get(self.key)
        if v is None:
            for old in self.former:
                if values.get(old) is not None:
                    return values[old]
        return v

    def to_json(self, v: Any) -> Any:
        return v

    def from_json(self, v: Any) -> Any:
        return self.validate(v)

    def visible(self, values: dict) -> bool:
        cond = self.visible_if
        if cond is None:
            return True
        if callable(cond):
            return bool(cond(values))
        for k, want in cond.items():
            have = values.get(k)
            if isinstance(want, (tuple, list, set)):
                if have not in want:
                    return False
            elif have != want:
                return False
        return True

    def describe(self, v: Any) -> str:
        return f"{v} {self.unit}".strip()

    # --------------------------------------------------------------- CLI
    @property
    def flag(self) -> str:
        return "--" + self.key.replace("_", "-")

    def add_cli(self, ap: argparse.ArgumentParser) -> None:
        ap.add_argument(self.flag, dest=self.key, default=None, help=self._cli_help())

    def _cli_help(self) -> str:
        d = self.default
        return f"{self.label}{' (' + self.unit + ')' if self.unit else ''}. {self.help} [default: {d}]".replace("%", "%%")


@dataclass
class Float(Param):
    min: float = -math.inf
    max: float = math.inf
    step: float = 0.1
    decimals: int = 3
    suggest: Callable | None = None   # fn(info) -> float; enables AUTO
    slider: bool = False              # show a slider next to the field
    kind = "float"

    def coerce(self, v):
        if isinstance(v, str) and v.strip().lower() == AUTO:
            if self.suggest is None:
                raise ParamError(f"{self.key}: 'auto' is not available")
            return AUTO
        try:
            f = float(v)
        except (TypeError, ValueError):
            raise ParamError(f"{self.key}: expected a number, got {v!r}") from None
        if not math.isfinite(f):
            raise ParamError(f"{self.key}: expected a finite number")
        return min(max(f, self.min), self.max)

    def describe(self, v):
        return "auto" if v == AUTO else f"{v:.{self.decimals}f} {self.unit}".strip()

    def add_cli(self, ap):
        ap.add_argument(self.flag, dest=self.key, default=None, metavar="auto|N" if self.suggest else "N",
                        help=self._cli_help())


@dataclass
class Int(Param):
    min: int = -2**31
    max: int = 2**31 - 1
    step: int = 1
    suggest: Callable | None = None
    slider: bool = False
    kind = "int"

    def coerce(self, v):
        if isinstance(v, str) and v.strip().lower() == AUTO:
            if self.suggest is None:
                raise ParamError(f"{self.key}: 'auto' is not available")
            return AUTO
        try:
            i = int(round(float(v)))
        except (TypeError, ValueError):
            raise ParamError(f"{self.key}: expected an integer, got {v!r}") from None
        return min(max(i, self.min), self.max)

    def add_cli(self, ap):
        ap.add_argument(self.flag, dest=self.key, default=None, metavar="N", help=self._cli_help())


@dataclass
class Seed(Int):
    """Random seed: the inspector adds a dice button. Every stochastic tool takes one, so runs reproduce."""
    min: int = 0
    max: int = 2**31 - 1
    kind = "seed"


@dataclass
class Bool(Param):
    kind = "bool"

    def coerce(self, v):
        if isinstance(v, str):
            s = v.strip().lower()
            if s in ("1", "true", "yes", "on"):
                return True
            if s in ("0", "false", "no", "off"):
                return False
            raise ParamError(f"{self.key}: expected true/false, got {v!r}")
        return bool(v)

    def describe(self, v):
        return "on" if v else "off"

    def add_cli(self, ap):
        ap.add_argument(self.flag, dest=self.key, default=None, action=argparse.BooleanOptionalAction,
                        help=self._cli_help())


@dataclass
class Choice(Param):
    choices: list = field(default_factory=list)       # values, or (value, label) pairs
    options_from: Callable | None = None              # fn(info) -> list of choices, from the selected input
    segmented: bool = False                           # few short options: segmented control instead of a menu
    kind = "choice"

    def pairs(self, info=None) -> list[tuple[Any, str]]:
        items = self.options_from(info) if (self.options_from and info is not None) else self.choices
        return [c if isinstance(c, tuple) else (c, str(c).replace("_", " ").capitalize()) for c in items]

    def coerce(self, v):
        if self.options_from:          # dynamic: validated against the input at run time
            return v
        values = [p[0] for p in self.pairs()]
        if v not in values:
            raise ParamError(f"{self.key}: {v!r} is not one of {values}")
        return v

    def describe(self, v):
        return next((lab for val, lab in self.pairs() if val == v), str(v))

    def add_cli(self, ap):
        ch = None if self.options_from else [p[0] for p in self.pairs()]
        ap.add_argument(self.flag, dest=self.key, default=None, choices=ch, help=self._cli_help())


@dataclass
class Text(Param):
    placeholder: str = ""
    kind = "text"

    def coerce(self, v):
        return "" if v is None else str(v)


@dataclass
class Vec3(Param):
    """Three numbers (x, y, z). `integer` makes them ints (counts); `axes` labels the fields."""
    min: float = -math.inf
    max: float = math.inf
    step: float = 0.1
    decimals: int = 3
    integer: bool = False
    axes: tuple = ("X", "Y", "Z")
    suggest: Callable | None = None
    kind = "vec3"

    def coerce(self, v):
        if isinstance(v, (list, tuple)) and len(v) == 1 and isinstance(v[0], str):
            v = v[0]                     # from the CLI: --spacing auto  or  --spacing "1,2,3"
        if isinstance(v, str):
            if v.strip().lower() == AUTO and self.suggest:
                return AUTO
            v = [x for x in v.replace(",", " ").split()]
        try:
            vals = [float(x) for x in v]
        except (TypeError, ValueError):
            raise ParamError(f"{self.key}: expected three numbers") from None
        if len(vals) == 1:
            vals = vals * 3
        if len(vals) != 3:
            raise ParamError(f"{self.key}: expected three numbers")
        vals = [min(max(x, self.min), self.max) for x in vals]
        return tuple(int(round(x)) for x in vals) if self.integer else tuple(vals)

    def to_json(self, v):
        return v if v == AUTO else list(v)

    def describe(self, v):
        if v == AUTO:
            return "auto"
        fmt = (lambda x: str(x)) if self.integer else (lambda x: f"{x:.{self.decimals}f}")
        return " × ".join(fmt(x) for x in v) + (f" {self.unit}" if self.unit else "")

    def add_cli(self, ap):
        ap.add_argument(self.flag, dest=self.key, default=None, nargs="+", metavar="N", help=self._cli_help())


@dataclass
class FileRef(Param):
    """A second input file (for example the shape SVG the Shape Cutter clips with). Empty means none."""
    exts: tuple = ()
    kind = "file"

    def coerce(self, v):
        return "" if v in (None, "") else str(v)


@dataclass
class Section:
    title: str
    params: list
    collapsed: bool = False
    help: str = ""

    def __init__(self, title: str, *params: Param, collapsed: bool = False, help: str = ""):
        self.title, self.params, self.collapsed, self.help = title, list(params), collapsed, help


def flatten(items: Iterable) -> list[Param]:
    out = []
    for it in items:
        if isinstance(it, Section):
            out.extend(it.params)
        else:
            out.append(it)
    return out


class Params(dict):
    """Validated values; also readable as attributes (p.tol)."""

    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k) from None


def resolve(params: list[Param], values: dict | None, partial: bool = False) -> Params:
    """Validate `values` against the schema, filling defaults. Unknown keys are ignored (old saved settings)."""
    values = values or {}
    out = Params()
    for p in params:
        v = p.lookup(values)
        if v is not None:
            out[p.key] = p.validate(v)
        elif not partial:
            out[p.key] = p.validate(None)
    return out


def resolve_lenient(params: list[Param], values: dict | None) -> tuple[Params, list[str]]:
    """resolve() for values that may come from an older or newer schema (a preset file): a value that no longer
    validates falls back to the default, and a key no parameter knows is dropped. Both are reported, in words."""
    values = values or {}
    out, problems = Params(), []
    known = set()
    for p in params:
        known.add(p.key)
        known.update(p.former)
        v = p.lookup(values)
        try:
            out[p.key] = p.validate(v)
        except (ParamError, TypeError, ValueError) as e:
            out[p.key] = p.validate(None)
            problems.append(f"{e}; using the default")
    unknown = sorted(k for k in values if k not in known)
    if unknown:
        problems.append(f"no longer used: {', '.join(unknown)}")
    return out, problems


def resolve_auto(params: list[Param], values: Params, info) -> Params:
    """Replace AUTO values with the parameter's suggestion for this input."""
    out = Params(values)
    for p in params:
        if out.get(p.key) == AUTO:
            sug = getattr(p, "suggest", None)
            out[p.key] = p.coerce(sug(info)) if (sug and info is not None) else p.coerce(p.default)
    return out


def paper_params(default: str = "A3", orientation: str = "portrait", margin: float = 15.0,
                 custom: bool = False) -> list[Param]:
    """Paper size, orientation and margin, shared by every tool that lays a drawing out on paper."""
    from ..geom.paper import PAPERS
    out = [
        Choice("paper", "Paper", default, [(k, k) for k in PAPERS] + ([("Custom", "Custom")] if custom else []), help="Sheet the drawing is fitted on."),
        Choice("orientation", "Orientation", orientation, [("portrait", "Portrait"), ("landscape", "Landscape")],
               segmented=True),
        Float("margin", "Margin", margin, unit="mm", min=0, max=150, step=1, decimals=1,
              help="Empty border kept on every side."),
    ]
    if custom:
        out += [Float("paper_width", "Custom width", 200., unit="mm", min=10., max=5000., visible_if={"paper": "Custom"}),
                Float("paper_height", "Custom height", 200., unit="mm", min=10., max=5000., visible_if={"paper": "Custom"})]
    return out


CAMERA_KEYS = ("theta", "psi", "radius", "lookat", "fov", "projection")


def camera_params(theta: float = 70.0, psi: float = -150.0, radius: float = 170.0, fov: float = 60.0,
                  projection: str = "perspective", lookat: tuple = (0.0, 0.0, 0.0)) -> list[Param]:
    """plotter.vision's orbit camera (geom/camera.py), shared by every tool that draws a model through a camera. The
    keys are the ones Plotter Vision's live view reads and writes, so any such tool can host that view to aim."""
    return [
        Float("theta", "Tilt (θ)", theta, unit="°", min=-180, max=180, step=1, decimals=1,
              help="Angle from straight above: 90 is on the horizon, 0 looks down, more than 90 looks up."),
        Float("psi", "Turn (ψ)", psi, unit="°", min=-180, max=180, step=1, decimals=1,
              help="Angle around the vertical (Z) axis."),
        Float("radius", "Distance", radius, min=1.0, max=5000.0, step=5, decimals=1,
              help="From the look-at point to the eye, in plotter.vision's units (the model's largest side is "
                   "100)."),
        Vec3("lookat", "Look at", tuple(lookat), help="The point the camera turns around (same units)."),
        Float("fov", "Field of view", fov, unit="°", min=1.0, max=150.0, step=1, decimals=1,
              help="Across the frame's long side. Smaller flattens the perspective, larger exaggerates it."),
        Choice("projection", "Projection", projection, [("perspective", "Perspective"),
                                                         ("orthographic", "Ortho")], segmented=True,
               help="Perspective as plotter.vision; orthographic keeps parallel lines parallel."),
    ]


AXIS_CHOICES = [("auto", "Auto"), ("+z", "+Z"), ("-z", "−Z"), ("+y", "+Y"), ("-y", "−Y"), ("+x", "+X"), ("-x", "−X")]
FRAME_KEYS = ("up_axis", "front", "rotate", "normalise", "reference_mesh")


def frame_params(stage_sets: bool = True) -> list[Param]:
    """How a model is set up before a 3D tool works on it (geom/frame3d.py): which way is up, which side is its
    front, an extra turn, and (for drawings) which models share one frame, so the stages of a sequence keep one
    scale, position and slice grid on paper."""
    mesh_exts = (".stl", ".obj", ".ply", ".glb", ".gltf", ".off")     # (core/tool.KINDS; geom.mesh needs numpy)
    out: list[Param] = [
        Choice("up_axis", "Up", "auto", AXIS_CHOICES,
               help="Which way is up in the file. Auto: Z for STL and PLY, Y for OBJ, unless another axis is "
                    "clearly the tallest (a statue stored lying down); a stage set made by a toolkit tool keeps "
                    "the frame it was made in."),
        Choice("front", "Front", "auto", AXIS_CHOICES,
               help="The side the model faces (its face, for a head). Auto: −Y for Z-up files, +Z for Y-up "
                    "files, or the stage set's own."),
        Vec3("rotate", "Extra turn", (0.0, 0.0, 0.0), unit="°", min=-180, max=180, step=5, decimals=1,
             advanced=True, help="Turns the model about X, then Y, then Z after Up and Front, e.g. to lay a "
                                 "bust on its side."),
    ]
    if stage_sets:
        out += [
            Choice("normalise", "Frame from", "set", [("set", "Its stage set"), ("model", "This model"),
                                                       ("reference", "Another model")],
                   help="Its stage set: every stage of a sequence (files named …_stage_00, _01 … beside each "
                        "other) gets the scale and position of the whole set, so the plates line up and share "
                        "their slice heights. A file that is not part of a set uses its own frame."),
            FileRef("reference_mesh", "Reference model", "", exts=mesh_exts, visible_if={"normalise": "reference"},
                    help="The model whose size and position set the frame."),
        ]
    return out


INFLUENCE_KEYS = ("influence", "band_from", "band_to", "sphere_centre", "sphere_radius", "feather", "invert_influence")


def influence_params() -> list[Param]:
    """Where a mesh tool acts (geom/influence.py): the whole model, a band of its height or a sphere, with a feathered
    edge. Lengths are % of the model's height, positions % of its size, so settings carry over between models."""
    def mode(*m):
        return lambda v: v.get("influence", "all") in m
    return [
        Choice("influence", "Acts on", "all", [("all", "Whole model"), ("band", "Height band"),
                                               ("sphere", "Sphere")], segmented=True,
               help="Whole model, or only a band of its height or a sphere; the effect fades out over the feather "
                    "and the rest stays as it was."),
        Float("band_from", "Band from", 50.0, unit="% h", min=0.0, max=100.0, step=5.0, decimals=1,
              visible_if=mode("band"), help="Lower edge of the band, % of the height from the base."),
        Float("band_to", "Band to", 100.0, unit="% h", min=0.0, max=100.0, step=5.0, decimals=1,
              visible_if=mode("band"), help="Upper edge of the band, % of the height from the base."),
        Vec3("sphere_centre", "Sphere centre", (0.0, 0.0, 25.0), unit="%", min=-100.0, max=100.0, step=5.0,
             decimals=1, visible_if=mode("sphere"),
             help="From the model's centre, % of its size along X (across), Y (depth, front is −) and Z (up)."),
        Float("sphere_radius", "Sphere radius", 30.0, unit="% h", min=1.0, max=200.0, step=5.0, decimals=1,
              visible_if=mode("sphere")),
        Float("feather", "Feather", 15.0, unit="% h", min=0.0, max=100.0, step=1.0, decimals=1,
              visible_if=mode("band", "sphere"),
              help="The effect fades from full to nothing over this distance outside the band or sphere."),
        Bool("invert_influence", "Invert", False, visible_if=mode("band", "sphere"),
             help="Act everywhere except the band or sphere."),
    ]
