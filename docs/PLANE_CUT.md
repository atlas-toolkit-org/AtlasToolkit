# Plane Cut

Available in Atlas Toolkit 1.10.1, tool version 0.1.0. The controls are inspired by the installed Meshmixer Plane
Cut editor and the supplied screenshots. This is an independent implementation using Atlas's bundled mesh
libraries. It does not require Meshmixer and does not copy its executable or proprietary algorithms.

In Atlas 1.10.2, Plane Cut 0.1.1 shares plane math, manual controls and staged mesh exports with Mirror. Its
cut/fill algorithms, settings, viewer entry point and output behavior are unchanged.

## Editor design and use

The **Plane** tab is an interactive placement view. **Expand** moves the same editor into a resizable,
maximizable dialog with all numeric/cut settings beside it. **Use settings** returns to the tool page;
**Preview cut** returns and computes the final geometry; **Cancel** restores the settings from before expansion.
Neither accepting editor settings nor previewing writes an output mesh. The main **Cut** button writes files.

1. Use **View** to orbit; right/middle drag pans and the wheel zooms. **F** fits the model. Camera presets and
   **Ortho** provide front, side and top alignment without changing the plane.
2. Choose **Draw plane**, then drag a red line across the view. Both endpoints and the camera's depth direction
   define an infinite plane. The line's length is not a local selection or a limit on the cut. The plane pivot
   is placed near the model centre. Very short strokes are ignored.
3. **Move** shows global X/Y/Z translation arrows and an **N** handle to slide perpendicular to the plane.
   **Rotate** shows three global rotation rings. Drag them to refine the plane. Hold **Shift** for fine movement,
   **Ctrl** for movement snapping (a model-scaled unit) or 15-degree rotation snapping. **Esc** cancels an active
   gesture and returns to View. **Undo** restores the previous editor gesture (up to 40).
4. Use **Align plane** for XY, YZ or XZ. **Centre** returns the pivot to the model bounds centre while retaining
   its orientation. Numeric **position**, **rotation** and **Slide along normal** are always available in the
   inspector, and beside the expanded viewport. XYZ Euler rotations apply X, then Y, then Z; zero is XY.
5. Choose **Kept / primary side** or click **Flip side**. The normal determines positive/negative, and the status
   line displays it. In Cut mode, the discarded part is a transparent cyan ghost; **Ghost** hides it. In Slice
   mode, both sides are opaque, in different colours. Hide **Plane grid** to inspect the surface unobstructed.
6. **Preview** computes the same clipping and caps as Run, without saving. The live placement view uses precise
   GPU surface clipping; the full plane grid is a placement guide, not a computed cap. Result tabs show Output
   and, when both exist, each side independently.

Viewport keys: **V** View, **D** Draw plane, **G** Move, **R** Rotate, **X** Flip, **F** Fit, **Ctrl+Z** Undo.
These keys act only while the viewport has focus.

## Cut and fill behavior

**Cut** retains the chosen side. **Slice** retains both non-empty sides. An outside or tangent plane may leave
only one piece; Atlas reports that. An entirely empty chosen side is an error and does not write a mesh.
There is no face-group-only mode or local face selection in this release.

| Fill | Behavior |
|---|---|
| No fill | Clip the surface and leave new cut boundaries open. |
| Minimal fill | Triangulate closed cut contours, retaining nested holes, such as the hole through a torus. |
| Remeshed fill | Refine cap interior edges toward the source mesh's median edge length. |
| Fixed fill | Refine cap interior edges toward **Cap edge size**, in model units. |

All fills are planar. Refinement is conforming, deterministic and preserves the cut boundary and the existing
surface away from the cut. Boundary edges can exceed the target size. A 200,000-cap-face budget and 12 rounds
limit very fine refinement; the report explains when a limit is reached. These are Atlas's fill policies, not
claims of identical triangulation to Meshmixer.

Closed input models must yield closed pieces when fill is enabled, or the run fails with a useful message.
Open input models can be cut; only closed section contours can be capped, and existing holes remain open.
This tool does not repair defective meshes. Move the plane slightly if a degenerate intersection cannot be capped.

## Coordinates, export and integration

Coordinates and units are retained. By default, numeric positions are relative to each input's bounds centre,
so a batch repeats a centre-relative cut. Choose **World origin** for absolute coordinates. Drawing a line
stores its plane through the same declarative parameters used by the CLI and workflows. Presets change cutting
settings while retaining the manually placed plane.

Outputs are signed `atlas-toolkit:plane-cut 0.1.0`, named `<stem>_plane-cut.stl` (or OBJ/PLY). Slice can save one
combined mesh or two separate files: the primary side uses the normal template, the other uses `_other`. If an
extra filename exists, Atlas adds a numeric suffix rather than overwriting an unconfirmed sibling. All files
are staged before publication; failure/cancellation before publication preserves the existing primary file.

At zero **Slice separation**, both pieces retain their original positions. Capped halves in a combined file
touch at the cut and may be welded together by downstream importers. Use separate files or a nonzero gap when
independent closed solids matter. Separation moves each half by half the gap in opposite normal directions.

The tool is one folder with a light spec, pure geometry, engine adapter and a tool-owned viewer. No other
tool's engine, form or viewer is changed. Atlas discovers it for menus, CLI, presets, history and workflows.
In a workflow, the primary file continues to subsequent processing steps; the other half is an extra output
that a directly connected Save node also saves, following the existing workflow contract.

```powershell
.\atlas-toolkit.exe describe plane-cut
.\atlas-toolkit.exe run plane-cut model.stl --rotation 0 90 0 --offset 5 --side negative --fill minimal
.\atlas-toolkit.exe run plane-cut model.stl --cut-type slice --export-mode separate --fill remeshed
```

## Verification

`tests/test_plane_cut.py` checks volume conservation, side classification, cap topology and annular holes,
open/disconnected meshes, tangent/empty planes, reference frames, separation, non-writing preview, signed formats,
staged export rollback, secondary-file collisions, cancellation and both outputs through workflow cache/Save.
`tests/test_plane_cut_view.py` sends mouse gestures to the actual editor and checks stroke rays in perspective
and orthographic projections, arrow/ring/normal dragging, numeric synchronisation, undo and expanded-dialog
accept/cancel. The toolkit contract suite validates discovery, schema, presets, CLI and output signatures.

`scripts/dev/validate_plane_cut.py --snapshots` also checks a real sculpture and creates source GUI screenshots,
including both gizmos, the maximized editor, light theme and the computed result.
