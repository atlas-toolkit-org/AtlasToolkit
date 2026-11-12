# Provenance and dependencies

## Atlas helpers

`src/flowrep3d/_atlas/` holds copies of a few Atlas Toolkit geometry helpers, so the library stays independent of
Qt and of the app:

- `camera.py`: the camera mathematics; the paper import points to the vendored module.
- `paper.py`: paper sizes and fitting.
- `raycast.py`: batched Embree/trimesh visibility, run splitting and bisection of transitions.
- `curvature.py`: triangle normals and curvature.
- `frame3d.py`: in-memory framing and axis routines, without the file and stage-set routines.
- `render.py`'s nearest-endpoint stroke ordering adapts the ordering routine in Atlas's `svgout.py`.

They are part of Atlas Toolkit and share its licence, GPL-3.0-or-later (`LICENSE`, `COPYRIGHT.md`).

## Research and libraries

- FlowRep algorithm: Gori et al., ACM TOG 2017, DOI 10.1145/3072959.3073639.
  Implemented independently from the paper. Cite the paper when publishing derived research.
- PyNanoInstantMeshes / Instant Meshes: BSD-style licence. See the installed distribution and
  upstream native/LICENSE.txt for the complete notice and enhancement clause.
  https://github.com/vork/PyNanoInstantMeshes
- NumPy and SciPy: BSD-style scientific libraries.
- NetworkX: BSD-style graph library.
- trimesh: MIT; Shapely: BSD-style.
- mapbox-earcut: ISC.
- rtree: MIT-style Python binding; its native spatial-index dependency has separate notices.
- Embree/embreex: optional accelerated ray-casting dependency, with its distribution notices.
- PySide6: optional Qt preview/UI dependency, subject to its LGPL/GPL/commercial terms.

Bundled wheels may include additional native-library notices; retain those when distributing
a frozen Atlas build. The project does not vendor the Towards3DVRSketch post-processing code.

## Reference data

The existing FlowRep model bundle and paper remain unchanged. Their model-specific source.txt
files and the paper's acknowledgements identify third-party assets and attribution requirements.
Examples and evaluations label author-reference networks separately from generated networks.

