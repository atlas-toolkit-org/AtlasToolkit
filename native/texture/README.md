# Atlas Surface Texture helper 0.2.0

MIT xatlas, pinned to `f700c7790aaa030e794b52ba7791a05c085faf0c` and vendored in `third_party/xatlas`. From the
repository root, with Atlas's Python:

```
python native/build.py --only texture
```

Requires the existing Atlas C++ toolchain (CMake/Ninja and a C++17 compiler). No Python extension ABI or Blender
dependency. The build compiles a statically linked helper and copies the executable and notices into the Python
package. The standard Atlas release builder includes it.

Protocol 2 input: little-endian uint32 version, vertex count, triangle count; float64 XYZ vertices; uint32 triangles.
Output: version, UV vertex count, triangle count; original vertex references (uint32); UVs in source model units
(float64 pairs); triangle UV indices (uint32); chart IDs (int32). Standard output contains progress events.
The parent Atlas process owns cancellation and lifetime. `--version` identifies executable/protocol compatibility.

xatlas's limits are absolute, so the helper centres every model and unwraps it at one working size and resolution,
whatever its units: a large model would otherwise ask for an enormous atlas, and a tiny one would lose small faces.
Each chart's UVs keep the model's scale; Atlas removes each chart's packing offset, so the layout has no effect.
