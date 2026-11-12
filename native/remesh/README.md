# Atlas Remesh/Decimate helper 0.1.1

Headless, double-precision PMP decimation and uniform/adaptive remeshing. From the repository root, with Atlas's
Python:

```
python native/build.py --only remesh [--no-fetch] [--jobs 4]
```

PMP 3.0.0 is pinned to f2fb04f4a4188a5c1ab137e83b96e62fa99c639f. Its included Eigen 3.4.0 is used.
Generated copies (`generate.py`, run by CMake at configure time) add regional density interpolation, fixed-boundary locks, progress, an intermediate size guard
and rejection of zero-area edge collapses. Original checkout sources are not edited. The executable and notices
are installed into the Atlas package. Release builds use C++17 and a static C++ runtime on Windows.

Array protocol 1: little-endian uint32 protocol/vertex count/triangle count; float64 XYZ vertices, uint32 triangle
indices, float64 regional weights. Output is protocol/counts followed by vertices and triangles. Command arguments
specify method, target faces, sizing, approximation/normal/feature limits, boundary flag, iterations, regional density
and projection flag. Progress is emitted as `PROGRESS fraction message`. The Atlas worker owns helper lifetime.

Licenses: PMP MIT; Eigen MPL-2.0. See bundled notices. Blender is a separately installed optional runtime.
