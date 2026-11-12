"""Reproducible motion preset sweep and drawing workflow generation; writes only to build/qa."""
import json
import time
from pathlib import Path
from atlas_toolkit.devpaths import preset_names, preset_values  # noqa: E402
from atlas_toolkit.core.job import JobContext
from atlas_toolkit.core.registry import registry
from atlas_toolkit.core.workflow import load, check
from atlas_toolkit.tools.mesh_motion import TOOL
from atlas_toolkit.tools.mesh_motion.engine import run
from atlas_toolkit.devpaths import DEV_FONT, qa_dir, studio_workspace  # noqa: E402,F401

ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = studio_workspace()
OUT = qa_dir("motion_validation")


def workflows():
    reg = registry()
    template = json.loads((ROOT / "docs/workflows/bend_contours.atlasflow").read_text())
    for kind, drawing, preset in [("contours", "contour-lines", None),
                                   ("sketch", "sketch-hatching", "Classic")]:
        wf = json.loads(json.dumps(template))
        wf.update(name=f"Motion {kind.title()}", toolkit="1.16.2",
                  description="Layered motion sculpture to A3 / 0.3 mm pen drawing.")
        wf["nodes"][1].update(tool=TOOL.id, tool_version=TOOL.version,
                               values=dict(preset_values(TOOL, "Wind-carved bronze")))
        draw = reg.get(drawing)
        values = dict(preset_values(draw, preset)) if preset else dict(draw.defaults())
        values.update(paper="A3", orientation="landscape", margin=25., line_width=.3)
        wf["nodes"][2].update(tool=drawing, tool_version=draw.version, values=values)
        wf["exposed"] = [{"node": "n1", "key": k} for k in
                         ("structure", "gesture", "reach", "heading", "preserve", "taper", "curl", "layers", "seed")]
        path = ROOT / f"docs/workflows/motion_{kind}.atlasflow"
        path.write_text(json.dumps(wf, indent=2), encoding="utf-8")
        issues = check(load(path), reg)
        errors = [i for i in issues if i.level == "error"]
        if errors:
            raise RuntimeError(errors)


def main():
    workflows()
    OUT.mkdir(parents=True, exist_ok=True)
    source = WORKSPACE / "3d_models_db/head_model/v17_head_decim7.5K-v2.stl"
    rows = []
    for name in preset_names(TOOL):
        start = time.perf_counter()
        dst = OUT / (name.lower().replace(" ", "_") + ".stl")
        result = run(JobContext(lambda *_: None, lambda: False), source, dst, preset_values(TOOL, name))
        row = {"preset": name, "seconds": round(time.perf_counter()-start, 3), "output": str(dst),
               **result.data}
        rows.append(row)
        print(json.dumps(row), flush=True)
    (OUT / "presets.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
