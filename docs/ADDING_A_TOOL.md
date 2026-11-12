# Adding a tool

A tool is one folder. The app finds it by itself: nothing else is edited, and the menus, form, command line,
saved settings, history and tests all come from its spec. Presets are not part of it: they are data files
([Presets](PRESETS.md)), and `tests/test_contract.py` fails if a spec defines any. Tune the parameters' defaults
instead: they are what the tool starts from.

```bat
.venv\Scripts\python scripts\new_tool.py path_simplifier --kind svg --name "Path Simplifier"
```

This writes four files:

```
src/atlas_toolkit/tools/path_simplifier/
  __init__.py          the spec: TOOL = Tool(...)          (light: no numpy, trimesh or Qt imports)
  path_simplifier.py   the algorithm, no GUI               (the Atlas code unit's main file)
  engine.py            run(ctx, src, dst, p) -> Result     (the adapter the app calls)
tests/test_path_simplifier.py
```

`--kind mesh` (mesh → mesh) and `--kind mesh2svg` (mesh → plotter SVG) start from the matching templates.

## 1. The spec (`__init__.py`)

```python
TOOL = Tool(
    id="path-simplifier",                     # kebab-case; also the <desc> signature of SVG outputs
    name="Path Simplifier", version="0.1",
    category="svg",                           # svg | mesh2svg | mesh | image | image2mesh | other
    icon="vector",                            # a studio icon name (ui/theme/icons.json, EXTRA_ICONS)
    action="Simplify",                        # the primary button's verb
    summary="One sentence for the card and the page.",
    inputs=Inputs("svg"),                     # or Inputs("mesh"); exclude=(regex,) skips earlier outputs
    output=Output("{stem}_simple.svg", "svg"),   # template; may use parameter keys: "{stem}_grid{n}.stl"
    signature="<desc>path-simplifier</desc>", # meshes: "atlas-toolkit:<id>" (STL header / OBJ, PLY comment)
    engine=".engine:run",
    runs_in="thread",                         # "process" for heavy work: own worker, Cancel always works
    live=True,                                # SVG tools: analyse every file again on each settings change
    columns=("changed",),                     # Stat keys shown as queue columns
    params=[Section("Settings",
        Float("tol", "Tolerance", 0.05, unit="mm", min=0.001, max=1, step=0.01, decimals=3,
              help="Shown as the tooltip and in `describe`."),
        Choice("mode", "Mode", "fast", [("fast", "Fast"), ("best", "Best")], segmented=True),
        Int("passes", "Passes", 2, min=1, max=10, visible_if={"mode": "best"}),
        Bool("keep", "Keep originals", True, advanced=True),
    )],
)
```

**Parameter types** (`core/params.py`):

| Type | Notes |
|---|---|
| `Float`, `Int` | `slider=True` adds a slider; `suggest=fn(info)` allows **auto**, a value computed per input file |
| `Seed` | adds a dice button |
| `Bool` | |
| `Choice` | `segmented=True` for a few short options; `options_from=fn(info)` builds them from the input, for example its layers |
| `Vec3` | x, y, z; `integer=True` for counts |
| `Text` | |
| `FileRef` | a second input file |
| `paper_params()` | size, orientation and margin |
| `camera_params()` | plotter.vision's orbit camera (θ, ψ, distance, look-at, field of view, projection) |
| `frame_params()` | how a model stands (Up, Front, Extra turn) and, for drawings, which models share one frame |
| `influence_params()` | where a mesh tool acts: whole model, a feathered height band or a sphere (`geom/influence.weights`) |

**Shared options:**
- `visible_if` is a dict, or a function of the current values.
- `advanced=True` hides a parameter behind “Show advanced settings”.

**Optional extras:**
- `inspect=".module:fn"` reads an input and returns an `InputInfo` (summary line, values for `suggest`, first
  preview). SVG and mesh inputs have defaults (`geom/svg.py:inspect`, `geom/mesh.py:inspect`).
- `panel=".panel:MyPanel"` with `panel_after="Section title"` adds your own Qt widget to the inspector. It receives
  the page. See `stl_hatch_maker/panel.py`.
- `viewer=".viewer:MyView"` adds an interactive preview tab (a QWidget built with the page, after the inspector).
  The page calls `set_input(path, info)` for the selected file and `values_changed(values)` after settings change;
  the stage calls `set_theme`, `set_art`, `fit` and shows `info_text()` (emit `info_changed` to update it). Write
  settings back with `page.form.set_values(...)` then `page._settings_changed()`. See `plotter_vision/viewer.py`.
  A debounced automatic mesh update can call `page.preview_selected(keep_view=True)` to leave the editor tab
  active; an explicit Preview opens the result tab. Settings changes cancel pending drafts immediately, and
  only the current draft job/generation may update the result. Keep exported placement in schema parameters,
  separate from the orbit camera; `ui/widgets/heightfield_placement.py` provides the shared heightfield editor; the DEM and image tools
  supply readers, preparation keys, labels and orientation cues without generic UI special cases.
  A viewer may implement `validation_error(path, mode='run') -> str`: a non-empty message blocks known
  invalid selected-input Preview/Build requests before worker submission. It must not replace engine-side
  validation, which also protects CLI, Workflows and unselected batch inputs. Refresh page controls when
  asynchronous preparation changes readiness. Heightfield editors use canonical source outlines for this gate.
- `preset_skip=("theta", ...)` lists view-state keys that presets neither set nor compare.
- `preset_inputs=("band", ...)` lists input settings a preset keeps as they are unless it sets them itself.
  Matching ignores those kept values. On the command line a preset starts from the defaults.
- When you rename a parameter, keep saved presets, settings and workflows working with `former=("old_key",)`;
  for a renamed choice value, `former_values={"old": "new"}`. A change that needs logic goes in
  `migrate=".module:fn"`, fn(values, from_version). See [Presets](PRESETS.md#when-tools-change).
- `requires=("blender",)` names external programs the tool needs (`core/runtimes.py`): the page shows a notice
  with "Locate …" until it is found, and tests and the self-test skip the tool where it is missing.
- `optional=(("blender", "Settle in Blender: a cloth simulation"),)` names programs the tool can use but runs
  without, with what each adds. The About window's Tools and Setup tabs list them; nothing blocks the tool.
  The UI asks `AppContext.lacking(tool)` / `needs_text(tool)`: a tool with a missing required runtime is faded in every list and flagged on its page and as a workflow step; never special-case one.
- `requires_if=(("blender", {"method": ("voxel", "planar")}),)` requires a runtime only for matching validated
  settings. Conditions within one rule are AND; multiple rules are OR. UI, CLI, Workflows and self-tests evaluate
  the selected values. Declare the optional capability as well so Setup explains it before it is selected.
- `Output(..., format_notes={".stl": "This format triangulates native polygons."})` shows format-specific guidance
  on the tool page and workflow inspector. Native polygon consumers can use `geom.surface.Surface`; legacy triangle
  consumers keep using `geom.mesh.load_mesh`. Native face loops, derived triangles and ownership travel together.
- **3D drawing tools** reuse the shared pieces rather than their own: `camera_params()` + `frame_params()` and
  `viewer="atlas_toolkit.tools.plotter_vision.aim:AimView"` give the Aim tab; `geom/frame3d.frame_for` the model's
  frame (a stage set shares one); `geom/raycast` embree visibility; `geom/shading.View3D` + `trace` a G-buffer with
  light, shadows and feature lines. Mesh tools that make sequences publish with `geom/stages.StageOutput` (stage
  files plus `stages.json`, all or nothing).
- `sample=".engine:make_sample"` writes a typical input for the tests and self-test.
- `input_dependencies=".engine:input_dependencies"` optionally returns additional input paths from `(src, values)`.
  Workflow cache keys include these files; use this for masks or metadata sidecars read implicitly by a library.
- `paper_params(..., custom=True)` adds Custom width/height controls. Pass `(paper_width, paper_height)` as
  `custom` to `geom.paper.sheet/fit` or `geom.camera.frame_for`; existing tools keep their current defaults.
- The `image2mesh` category is **Image → Mesh**. Numeric raster tools can use restricted `Inputs("image", exts=...)`
  with a tool-owned inspector, preserving ordinary photo inspection elsewhere.
- `geom.mesh_export.publish_meshes(..., validator=fn)` optionally checks each staged output path before publication.
  Mesh surfaces with a zero-rotation frame embed compact up/front axes in STL as well as OBJ/PLY frame metadata.
- `test_values={...}` keeps that run quick.

## 2. The algorithm (`<folder>.py`)

Plain functions, no GUI.

- **SVG tools:** `geom/svg.py` gives the shared reader from the Overlap Cleaner: `load`, `collect` (elements in
  plotting order with transform and pen), `shape_subpaths` (any element as polylines, curves flattened),
  `mm_per_unit`, `path_data`.
  `read_drawing(..., shapes=SHAPES | BASIC_SHAPES)` optionally includes retained basic shapes in previews;
  the default keeps the established path/line/polyline/polygon preview policy.
- **Tools that draw from scratch:** `geom/svgout.py` writes mm SVGs with one layer per pen, joins segments and
  orders strokes.
- **Meshes:** `geom/mesh.py` loads, cleans, decimates, saves signed, and has `result()` for the usual mesh Result.
- **Paper sizes:** `geom/paper.py`.

## 3. The engine (`engine.py`)

```python
def run(ctx, src: Path, dst: Path | None, p) -> Result:
```

- **`dst`** is the output file, or None for a dry run (live analysis or Preview: compute and preview, write
  nothing).
- **`p`** holds the validated parameters, `p.tol` or `p["tol"]`. AUTO values are already resolved.
- **`ctx.progress(fraction, message)`** often, inside long loops. It also raises when the user cancels.
- **`ctx.log(msg)` / `ctx.warn(msg)`** write to the Log tab.
- **`ctx.draft`** is True for Preview: decimate or skip slow refinements.
- **The return value** is a `Result`:
  - `outputs`: the files written;
  - `stats`: `Stat` tiles and columns, with an optional `before` value;
  - `report`: plain text;
    - `preview`: `{"views": [{"title", "kind": "svg"|"mesh", "drawing"|"mesh", "overlays"}], "prefer": title}`;
      mesh views may add a `status` string (for example `Draft · balanced` or `Final · proxy 80,000`),
      displayed beside mesh statistics. A display proxy never replaces saved final geometry.
  - `changed`: False means nothing to do for this file.

Sign every output. For SVGs, use `sign_desc` and `write_run_metadata`, or `svgout.write_svg(tool=TOOL)`. For
meshes, `geom.mesh.save_mesh` signs them. The contract test checks that the signature literal is in the tool's
folder, because that is where Atlas looks for it.

## 4. Check it

```bat
.venv\Scripts\python -m unittest discover -s tests          :: test_contract covers every tool
.venv\Scripts\python -m atlas_toolkit --tool path-simplifier some.svg
.venv\Scripts\python -m atlas_toolkit --tool path-simplifier some.svg --snapshot shot.png   :: screenshot
```

**test_contract checks:**
- the spec is valid;
- defaults are in range;
- settings round-trip through JSON and the CLI;
- the engine runs on a sample and writes the templated, signed output;
- the signature is in the tool's source.

Then bump the tool's `version`, add a line to `CHANGELOG.md` and rebuild (`scripts\build.py`).

Add the tool to the README: a card in its group in `README.md`, with 2-3 lines on what it does and what its settings
allow, and, unless it is an SVG tool, a recipe in `scripts/dev/readme_gallery.py` to make its image (`--only <id>`,
then `--publish`). `tests/test_readme.py` checks both.

## Plug-ins (no rebuild)

A tool folder placed in `%APPDATA%\AtlasToolkit\plugins\` loads at start-up, in the built exe too, as long as it only
imports what the app bundles:
- numpy, scipy, trimesh, shapely, networkx, pyqtgraph, PySide6;
- the toolkit itself.

Other plug-in folders can be listed in the `ATLAS_TOOLKIT_PLUGINS` environment variable (separated by `;`).
`new_tool.py --plugin <folder>` writes one there. A plug-in that fails to load is listed in red with its error;
the app keeps working.
