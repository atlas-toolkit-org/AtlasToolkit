# Mirror

Atlas Toolkit 1.10.2, Mirror 0.1.0. Inspired by the supplied Meshmixer screenshot: an original and its reflected
copy, controlled by a movable plane and a three-axis gizmo. It runs independently of Meshmixer.

## Result and placement

**Original + mirrored copy** retains the complete original and reflection as separate shells. **Mirrored model
only** saves just the reflection; the input file remains unchanged. There is no cutting, half-model symmetry,
welding or solid union in this release.

The default plane is the YZ plane at the input's **+X bounds side**. Its reflected copy starts adjacent to the
original. **Other side** (X in the viewport) moves the current plane to the opposite position around the model's
bounds centre, preserving its orientation. **Centre** puts it through the bounds centre.

**Position relative to** selects Model +X side, Model bounds centre or World origin. Position and normal offset
are in original model units. The reference point stays fixed while the plane rotates; rotating does not silently
move its pivot. XYZ Euler angles apply X, then Y, then Z; the default (0, 90, 0) gives a YZ plane.

## Manual editor

Mirror and Plane Cut use the same `ui/widgets/plane_editor.py` controls and `geom/plane.py` math, with different
tool-owned scene styling. Their engines and settings remain separate, accessed through Atlas's Tool contract.

- **View**: left drag orbits, right/middle drag pans, wheel zooms, F fits the current scene. Camera presets and
  **Ortho** assist precise alignment. The view does not change the mirror plane.
- **Draw plane**: drag a red screen line. It defines an infinite plane extending through the view's depth.
  Line length does not limit the reflection to a local region. Very short strokes are ignored.
- **Move**: drag an X/Y/Z arrow, or N to slide perpendicular to the plane. The copy moves twice the plane's
  perpendicular displacement, as a physical reflection does.
- **Rotate**: drag a global X/Y/Z ring. Hold Shift for fine control, Ctrl for movement snapping or 15-degree
  angle snapping. Esc cancels a gesture and returns to View. Undo restores the previous editor gesture.
- **Align plane**: XY, YZ or XZ. Numeric position, rotation and normal offset remain available in the inspector.
- The original is white and the reflection cyan. In mirror-only mode, **Reference** optionally shows a ghost
  of the original for comparison; that reference is not exported. **Plane grid** hides/shows the guide.
- **Expand** opens a resizable, maximizable editor with all settings alongside the viewport. **Use settings**
  accepts placement, **Cancel** restores the settings and editor history from before expansion, and
  **Preview mirror** returns to the tool page and computes the full-resolution result without saving.

Viewport keys: V View, D Draw plane, G Move, R Rotate, X Other side, F Fit, Ctrl+Z Undo. Keys apply only when the
viewport has focus. Presets change output behavior while retaining the manually positioned plane.

## Output and integrations

`<stem>_mirror.stl` (or OBJ/PLY) is signed `atlas-toolkit:mesh-mirror 0.1.0`. Reflection preserves vertex and face
counts, distances, area and, for a closed mesh, volume magnitude and outward winding. Open meshes stay open.
It does not repair input topology or preserve materials beyond Atlas's existing mesh export capabilities.

**One combined mesh** holds the original and reflection with independent indexed vertices. Touching and
overlapping shells stay unjoined during this tool's computation and export; downstream importers may weld
coincident vertices. Use **Two separate files** when part identity must survive such importers.

In separate mode, the main `_mirror` file is the reflection and an `_original` sibling holds a signed copy of
the unchanged original geometry. Existing extra filenames are numbered rather than overwritten. The common
export transaction stages all meshes before publication and preserves an existing primary output on failure
or cancellation before the final replacement.

The same settings work in the UI, CLI, presets and history. In a workflow, the main reflection continues to
subsequent steps in separate mode, and a directly connected Save node saves both files. Combined mode passes
both shells together, following the existing workflow contract.

```powershell
.\atlas-toolkit.exe describe mesh-mirror
.\atlas-toolkit.exe run mesh-mirror model.stl
.\atlas-toolkit.exe run mesh-mirror model.stl --result-mode mirror --reference world --position 0 0 0
.\atlas-toolkit.exe run mesh-mirror model.stl --export-mode separate --rotation 15 40 0 --offset 3
```

## Verification

`tests/test_mesh_mirror.py` checks arbitrary-plane reflection, signed distances, unchanged source arrays,
double-reflection identity, area/volume, outward winding, side/centre/world references, open/disconnected meshes,
unwelded touching/overlapping copies, both output modes, all signed formats, cancellation, rollback and workflow
cache/Save/chaining. `tests/test_mirror_view.py` sends mouse gestures to the shared editor and checks drawing in
both projections, XYZ/ring/normal dragging, opposite-side placement, pivot preservation, reference visibility,
numeric synchronization and expanded-editor accept/cancel. Plane Cut's existing tests cover the shared controls
and exports against its original behavior.

`scripts/dev/validate_mirror.py --snapshots` checks real closed and open models and renders the native editor,
maximized arrows/rings, mirror-only mode, light theme, computed output and Plane Cut after control reuse.
