"""Write Atlas's patched copies of PMP's remeshing.cpp and decimation.cpp; the PMP checkout stays verbatim.

    python generate.py --pmp <pmp checkout> --out <folder>

Run by remesh/CMakeLists.txt at configure time. Each anchor must occur exactly where expected, so an upstream
change fails loudly instead of compiling an unpatched algorithm.
"""
from __future__ import annotations

import argparse
from pathlib import Path


def remeshing(text: str) -> str:
    marker = '    // compute sizing field\n'
    if text.count(marker) != 1:
        raise RuntimeError('Unexpected PMP sizing implementation.')
    text = text.replace(marker, '''    // Atlas: keep protected boundaries fixed, including feature corners.
    auto atlas_lock = mesh_.get_vertex_property<bool>("v:atlas_lock");
    if (atlas_lock) for (auto v : mesh_.vertices()) vlocked_[v] = vlocked_[v] || atlas_lock[v];
    if (atlas_lock) for (auto e : mesh_.edges())
        elocked_[e] = elocked_[e] || vlocked_[mesh_.vertex(e,0)] || vlocked_[mesh_.vertex(e,1)];
''' + marker)
    marker = '    if (use_projection_)\n    {\n        // build reference mesh'
    if text.count(marker) != 1:
        raise RuntimeError('Unexpected PMP reference projection implementation.')
    text = text.replace(marker, '''    // Atlas: feathered per-vertex density, interpolated by PMP's reference projection.
    auto atlas_density = mesh_.get_vertex_property<Scalar>("v:atlas_density");
    if (atlas_density) for (auto v : mesh_.vertices()) vsizing_[v] *= atlas_density[v];
''' + marker)
    text = text.replace('        split_long_edges();', '''        std::cout << "PROGRESS " << (0.1 + 0.8 * i / iterations) << " remeshing iteration " << (i+1) << std::endl;
        if (mesh_.n_faces() > 8000000) throw std::runtime_error("Remesh exceeds eight million intermediate faces; increase edge length.");
        split_long_edges();''')
    return '#include <iostream>\n' + text


def decimation(text: str) -> str:
    text = text.replace('    // initialize normal cones\n', '''    // Atlas: smoothly bias simplification away from the feathered protection region.
    auto atlas_weights = mesh_.get_vertex_property<double>("v:atlas_weight");
    if (atlas_weights) for (auto v : mesh_.vertices()) vquadric_[v] *= (1.0+99.0*atlas_weights[v]);

    // initialize normal cones
''')
    marker = '    // check for flipping normals\n'
    if text.count(marker) != 1:
        raise RuntimeError('Unexpected PMP collapse implementation.')
    return text.replace(marker, '''    // Atlas: reject numerically collapsed faces before normal-cone tests (zero normals are undefined).
    vpoint_[cd.v0] = p1;
    for (auto f : mesh_.faces(cd.v0)) {
        if (f == cd.fl || f == cd.fr) continue;
        auto it = mesh_.vertices(f);
        auto a = vpoint_[*it]; ++it;
        auto b = vpoint_[*it]; ++it;
        auto c = vpoint_[*it];
        auto u = b-a, v = c-a;
        auto n = cross(u,v);
        if (dot(n,n) <= 1e-24 * std::max(dot(u,u)*dot(v,v), 1e-30)) {
            vpoint_[cd.v0] = p0;
            return false;
        }
    }
    vpoint_[cd.v0] = p0;
''' + marker)


def write_if_changed(path: Path, text: str) -> None:
    """Rewrite only on change, so re-running the configure step does not force a PMP rebuild."""
    if not path.is_file() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pmp", type=Path, required=True, help="PMP 3.0.0 checkout")
    ap.add_argument("--out", type=Path, required=True, help="folder for the generated sources")
    a = ap.parse_args()
    algorithms = a.pmp / "src/pmp/algorithms"
    a.out.mkdir(parents=True, exist_ok=True)
    write_if_changed(a.out / "remeshing.cpp", remeshing((algorithms / "remeshing.cpp").read_text(encoding="utf-8")))
    write_if_changed(a.out / "decimation.cpp", decimation((algorithms / "decimation.cpp").read_text(encoding="utf-8")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
