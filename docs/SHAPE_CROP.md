# Shape Crop

Available in Atlas Toolkit 2.5.0, tool version 0.1.0 (Mesh · model prep, README family *Prepare and cut*).

Shape Crop keeps the part of a model that lies inside a box, sphere, cylinder or pyramid you place on it. It is a
preparation tool: choose the region of a scan or sculpture to draw, before Contour Lines, Sketch Hatching, Plotter
Vision or any sculpting tool. The same machinery also cuts the region away, adds the shape to the model, or keeps
both remainders.

## Operations

| Operation | Result | Input |
|---|---|---|
| **Keep inside · intersection** (default) | the model within the shape | any mesh |
| **Cut away · difference** | the model without the shape's region | any mesh |
| **Add the shape · union** | model and shape as one solid (a plinth, a block, a drum) | a closed solid |
| **Both remainders · symmetric difference** | the model without the shape, plus the shape without the model | a closed solid |

**Cut faces** (Keep inside and Cut away only):

- **Close the cut** (default). A closed model is cropped with a solid boolean (manifold3d): the shape's own surface
  fills the cut, so the result is closed and every solid tool takes it. Spheres and cylinders are faceted for this:
  **Curve segments** (advanced) is 0 = automatic, from the model's own edge length (48 to 720 around).
- **Leave open**. Only the model's own surface is kept, clipped exactly along the shape: each new vertex lies on the
  box's face, the sphere or the cylinder (to 1e-9 of its size), and the faces stay joined (no cracks). Quicker, and
  what the drawing tools need when the cut face should not be drawn.

An open model (a scan with a hole, a relief, a sheet) cannot be closed by a boolean, so Keep inside and Cut away clip
it as **Leave open** does and say so in the report. **Add the shape** and **Both remainders** need a closed solid:
as soon as such a file is added the file list marks it *Needs repair* for those operations (and only while one of
them is chosen) and offers **Open in Repair Mesh**. Small defects (decimation crumbs, a pin-hole) are tidied
automatically without moving the surface; a model with a real opening is not tidied for nothing, the run goes
straight to the clip.

**Remainders** (Both remainders only): one combined mesh (the two parts touch where the model met the shape) or two
files, `<name>_crop.stl` with the model's remainder and `<name>_crop_shape.stl` with the shape's.

## Placing the shape

The **Shape** tab draws the model with the shape's wireframe over it, and crops it live on the graphics card as you
work, whatever the model's size (it draws the input's light preview copy). **Ghost** shows what is removed;
**Add the shape** and **Both remainders** show the shape as a solid. **Expand** opens the same editor in a large window
with every setting beside it; **Preview crop** there computes the exact result.

- **Draw** (D): drag a rectangle over the part to keep. The shape turns to face the view, fills the rectangle and
  spans the model's whole depth seen from there. Then refine it.
- **Move** (G): drag the X, Y or Z arrow. **Rotate** (R): drag a ring. **Scale** (S): drag a square handle to size
  the shape along its own width (W), depth (D) or height (H); the white handle sizes it evenly. One handle length
  outward doubles the size, inward halves it.
- Shift: fine. Ctrl: snap (moves to a model-scaled step, turns to 15°, sizes to 5%). Esc cancels a gesture; **Undo**
  and Ctrl+Z step back (40 steps). F fits the view; **Ortho** and the camera menu align it.
- **Centre** moves the shape to the middle of the model; **Fit to model** turns it square and sizes it to the model's
  bounds; **Reset turn** squares it; **Inside ⇄ outside** (X) swaps Keep inside and Cut away.

The numbers are always in the inspector:

- **Position relative to**: the model's bounds centre (the default: the same settings place the shape alike on a batch
  of models) or the world origin.
- **Shape centre** in model units, **Shape rotation** as XYZ Euler angles (X, then Y, then Z, as Plane Cut).
- **Size in** % of the model's largest dimension (default; 60 × 60 × 60 is a centred cube) or model units, and
  **Shape size**: the box's width, depth and height; a sphere's three diameters (an ellipsoid when they differ); a
  cylinder's two diameters and its height along its own Z; a pyramid's base and height, apex towards its own +Z.

Each file keeps its own placement (the file list's menu can give it to the others). The placement is not part of a
preset, and the official preset packs have none for this tool: crops belong to each model.

## Speed

Large meshes are the point of the tool. Measured on the development machine with
`scripts/dev/validate_mesh_crop.py` (every shape and operation, a closed model and an open scan of each size; the
times include reading the file, saving the result and making its preview):

| Faces | Adding the file | Crop, closed cut | Crop, open scan |
|---:|---:|---:|---:|
| 80 000 | 0.2–0.3 s | 0.2–0.3 s | 0.2–0.3 s |
| 330 000 | 1.0–1.4 s | 0.9–1.5 s | 0.5–1.1 s |
| 1.3 million | 4–5 s | 3.0–4.4 s | 2.0–4.3 s |
| 5.2 million | 10–14 s | 12–15 s | 8–13 s |

The boolean or clip itself is the smaller part (0.35–1.2 s at 1.3 million faces, 1–3.3 s at 5 million); reading and
cleaning the file is the larger, and 2.5.0 made it about twice as quick for every mesh tool. The shape editor never
waits on the model: it works on a light copy of at most 80 000 faces.

## Limits

- The open clip keeps faces by their corners. A curved shape that only grazes a large face is found by splitting long
  faces near the shape first (to 1/32 of its size, up to 4 million extra faces; the report says when it stops).
- A shape that lies wholly inside a model's surface has nothing of that surface inside it: **Leave open** then reports
  that nothing is left, while **Close the cut** returns the shape (it is inside the solid).
- Both remainders, combined, touch along the cut; other software may weld them. Use separate files to keep them apart.

## For developers

`geom/region.py` holds the shapes (frame, constraints with exact crossings, outlines, manifold3d solids, volumes);
`tools/mesh_crop/mesh_crop.py` the clip and booleans; `ui/widgets/shape_editor.py` the editor, on the gizmo base it
shares with Plane Cut and Mirror (`ui/widgets/gizmo.py`). Tests: `tests/test_mesh_crop.py` (exact crossings, volumes
by inclusion and exclusion for every shape, a model a million units from the origin, a 1.3-million-face model through
the whole tool), `tests/test_crop_view.py`, `tests/test_mesh_fast_paths.py`.
