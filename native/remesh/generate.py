"""Write Atlas's patched copies of PMP's remeshing.cpp, decimation.cpp and curvature.cpp; the PMP checkout stays verbatim.

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
        // after a whole iteration (splits swell the mesh for a moment, collapses bring it back)
        if (atlas_face_limit && mesh_.n_faces() > atlas_face_limit)
        {
            atlas_face_reached = mesh_.n_faces();
            throw std::runtime_error("ATLAS_FACE_LIMIT");
        }
        split_long_edges();''')
    # Inside each split / collapse / flip sweep too: one runaway split can multiply the faces tenfold and take
    # minutes on a million-face scan. A sweep swells the mesh for a moment (four times the expected count on a chess
    # pawn's real creases), so this bound is wider than the one between iterations.
    sweep = '        ok = true;\n'
    if text.count(sweep) != 3:
        raise RuntimeError('Unexpected PMP split/collapse/flip loops.')
    text = text.replace(sweep, '''        ok = true;
        if (atlas_face_limit && mesh_.n_faces() > 8 * atlas_face_limit / 3)
        {
            atlas_face_reached = mesh_.n_faces();
            throw std::runtime_error("ATLAS_FACE_LIMIT");
        }
''')
    marker = ('                // feature edge and feature vertex -> seems to be intended\n'
              '                if (efeature_[e] && vfeature_[v])\n'
              '                    continue;\n')
    if text.count(marker) != 1:
        raise RuntimeError('Unexpected PMP cap removal.')
    text = text.replace(marker, '''                // feature edge and feature vertex: PMP keeps such a cap as intended, but a face with no height
                // breaks every tool downstream. Atlas: merge the apex into an end of the crease edge (the three
                // points are collinear, so the crease keeps its shape).
                if (efeature_[e] && vfeature_[v])
                {
                    bool merged = false;
                    if (!vlocked_[v])
                    {
                        const Vertex va = mesh_.to_vertex(mesh_.halfedge(e, 0));
                        const Vertex vc = mesh_.to_vertex(mesh_.halfedge(e, 1));
                        for (auto hv : mesh_.halfedges(v))
                        {
                            const Vertex to = mesh_.to_vertex(hv);
                            if ((to == va || to == vc) && mesh_.is_collapse_ok(hv))
                            {
                                mesh_.collapse(hv);
                                merged = true;
                                break;
                            }
                        }
                    }
                    if (!merged)
                    {
                        // or flip the long edge away: the crease still runs a-v-c along its other two edges
                        mesh_.flip(e);
                        efeature_[e] = false;
                    }
                    continue;
                }
''')
    marker = '        curvature(mesh_, Curvature::max_abs, 0, true, false);\n'
    if text.count(marker) != 1:
        raise RuntimeError('Unexpected PMP adaptive sizing.')
    text = text.replace(marker, '''        std::cout << "PROGRESS 0.1 estimating curvature" << std::endl;
        curvature(mesh_, Curvature::max_abs, 0, true, false);
        std::cout << "PROGRESS 0.1 sizing from curvature" << std::endl;
''')
    text = nearest_triangle_tree(text)
    # Atlas: set by the helper while sharp features are kept; going past it means they block the remesher (scan noise)
    return '#include <iostream>\n#include <cstddef>\n#include <stdexcept>\nnamespace pmp { std::size_t atlas_face_limit = 0, atlas_face_reached = 0; }\n' + text


# PMP's TriangleKdTree puts a triangle in every child it straddles and stops splitting when all of a node's triangles
# straddle the cut: around a pole (a turned chess piece: hundreds of triangles sharing one vertex) leaves end up with
# hundreds of triangles, and projecting onto the source (every vertex, several times per iteration) scanned them
# all: 70 s instead of under a second on an 88 000-face pawn. This bounding-volume hierarchy keeps each triangle
# in exactly one leaf (split at the median centroid) and prunes by box distance. Same interface, same results.
BVH = r'''
class TriangleKdTree
{
public:
    TriangleKdTree(std::shared_ptr<const SurfaceMesh> mesh, unsigned int max_faces = 8, unsigned int = 0)
        : leaf_size_(max_faces ? max_faces : 4u)
    {
        auto points = mesh->get_vertex_property<Point>("v:point");
        for (const auto& f : mesh->faces())
        {
            auto v = mesh->vertices(f);
            const Point p0 = points[*v]; ++v;
            const Point p1 = points[*v]; ++v;
            const Point p2 = points[*v];
            faces_.push_back(f);
            tri_.push_back({p0, p1, p2});
        }
        order_.resize(faces_.size());
        for (size_t i = 0; i < order_.size(); ++i) order_[i] = i;
        if (!order_.empty()) build(0, order_.size());
    }

    struct NearestNeighbor
    {
        Scalar dist;
        Face face;
        Point nearest;
    };

    NearestNeighbor nearest(const Point& p) const
    {
        NearestNeighbor best;
        best.dist = std::numeric_limits<Scalar>::max();
        if (nodes_.empty()) return best;
        size_t stack[128];
        int top = 0;
        stack[top++] = 0;
        while (top)
        {
            const Node& node = nodes_[stack[--top]];
            if (box_distance(node, p) >= best.dist) continue;
            if (node.left < 0)
            {
                for (size_t i = node.start; i < node.start + node.count; ++i)
                {
                    const auto& t = tri_[order_[i]];
                    Point n;
                    const Scalar d = dist_point_triangle(p, t[0], t[1], t[2], n);
                    if (d < best.dist) { best.dist = d; best.face = faces_[order_[i]]; best.nearest = n; }
                }
                continue;
            }
            const Node& a = nodes_[node.left];
            const Node& b = nodes_[node.right];
            const Scalar da = box_distance(a, p), db = box_distance(b, p);
            // the nearer child last, so it is searched first
            if (da < db) { stack[top++] = size_t(node.right); stack[top++] = size_t(node.left); }
            else         { stack[top++] = size_t(node.left);  stack[top++] = size_t(node.right); }
        }
        return best;
    }

private:
    struct Node
    {
        Point lo, hi;
        long left{-1}, right{-1};
        size_t start{0}, count{0};
    };

    static Scalar box_distance(const Node& n, const Point& p)
    {
        Scalar s = 0;
        for (int k = 0; k < 3; ++k)
        {
            const Scalar d = std::max(Scalar(0), std::max(n.lo[k] - p[k], p[k] - n.hi[k]));
            s += d * d;
        }
        return std::sqrt(s);
    }

    size_t build(size_t start, size_t count)
    {
        const size_t id = nodes_.size();
        nodes_.push_back(Node());
        Point lo(std::numeric_limits<Scalar>::max()), hi(-std::numeric_limits<Scalar>::max());
        Point clo = lo, chi = hi;
        for (size_t i = start; i < start + count; ++i)
        {
            const auto& t = tri_[order_[i]];
            for (int k = 0; k < 3; ++k)
                for (int j = 0; j < 3; ++j) { lo[j] = std::min(lo[j], t[k][j]); hi[j] = std::max(hi[j], t[k][j]); }
            const Point c = (t[0] + t[1] + t[2]) / Scalar(3);
            for (int j = 0; j < 3; ++j) { clo[j] = std::min(clo[j], c[j]); chi[j] = std::max(chi[j], c[j]); }
        }
        nodes_[id].lo = lo;
        nodes_[id].hi = hi;
        int axis = 0;
        for (int j = 1; j < 3; ++j) if (chi[j] - clo[j] > chi[axis] - clo[axis]) axis = j;
        if (count <= leaf_size_ || chi[axis] - clo[axis] <= 0)
        {
            nodes_[id].start = start;
            nodes_[id].count = count;
            return id;
        }
        const size_t mid = start + count / 2;
        std::nth_element(order_.begin() + start, order_.begin() + mid, order_.begin() + start + count,
                         [&](size_t a, size_t b) {
                             const auto& ta = tri_[a];
                             const auto& tb = tri_[b];
                             return ta[0][axis] + ta[1][axis] + ta[2][axis] < tb[0][axis] + tb[1][axis] + tb[2][axis];
                         });
        const long left = long(build(start, mid - start));
        const long right = long(build(mid, start + count - mid));
        nodes_[id].left = left;
        nodes_[id].right = right;
        return id;
    }

    unsigned int leaf_size_;
    std::vector<Face> faces_;
    std::vector<std::array<Point, 3>> tri_;
    std::vector<size_t> order_;
    std::vector<Node> nodes_;
};

'''


def nearest_triangle_tree(text: str) -> str:
    start = text.find('class TriangleKdTree\n{')
    end = text.find('class Remeshing\n{')
    if start < 0 or end < start or text.count('class TriangleKdTree\n{') != 1:
        raise RuntimeError('Unexpected PMP TriangleKdTree implementation.')
    return text[:start] + BVH.lstrip('\n') + text[end:]


def decimation(text: str) -> str:
    text = text.replace('    // initialize normal cones\n', '''    // Atlas: smoothly bias simplification away from the feathered protection region.
    auto atlas_weights = mesh_.get_vertex_property<double>("v:atlas_weight");
    if (atlas_weights) for (auto v : mesh_.vertices()) vquadric_[v] *= (1.0+99.0*atlas_weights[v]);

    // initialize normal cones
''')
    marker = '    // check for flipping normals\n'
    if text.count(marker) != 1:
        raise RuntimeError('Unexpected PMP collapse implementation.')
    return text.replace(marker, '''    // Atlas: reject collapses that leave a face with no height (a cap: flat output faces, undefined normals).
    vpoint_[cd.v0] = p1;
    for (auto f : mesh_.faces(cd.v0)) {
        if (f == cd.fl || f == cd.fr) continue;
        auto it = mesh_.vertices(f);
        auto a = vpoint_[*it]; ++it;
        auto b = vpoint_[*it]; ++it;
        auto c = vpoint_[*it];
        auto u = b-a, v = c-a;
        auto n = cross(u,v);
        if (dot(n,n) <= 1e-10 * std::max(dot(u,u)*dot(v,v), 1e-30)) {   // sin < 1e-5: a cap, not a face
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


def curvature(text: str) -> str:
    """PMP's curvature tensor smooths its result with a Laplace matrix even for zero smoothing steps, which Atlas's
    adaptive remeshing asks for: building and scaling a million-row matrix took longer than ten minutes on a
    1 000 000-face scan (Ugolino), for nothing. Skip it when there is nothing to smooth."""
    marker = 'void CurvatureAnalyzer::smooth_curvatures(unsigned int iterations)\n{\n'
    if text.count(marker) != 1:
        raise RuntimeError('Unexpected PMP curvature smoothing.')
    return text.replace(marker, marker + '    if (iterations == 0)\n        return;   // Atlas: nothing to smooth\n')


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pmp", type=Path, required=True, help="PMP 3.0.0 checkout")
    ap.add_argument("--out", type=Path, required=True, help="folder for the generated sources")
    a = ap.parse_args()
    algorithms = a.pmp / "src/pmp/algorithms"
    a.out.mkdir(parents=True, exist_ok=True)
    write_if_changed(a.out / "remeshing.cpp", remeshing((algorithms / "remeshing.cpp").read_text(encoding="utf-8")))
    write_if_changed(a.out / "decimation.cpp", decimation((algorithms / "decimation.cpp").read_text(encoding="utf-8")))
    write_if_changed(a.out / "curvature.cpp", curvature((algorithms / "curvature.cpp").read_text(encoding="utf-8")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
