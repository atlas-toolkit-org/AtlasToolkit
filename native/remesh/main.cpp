// Atlas Remesh/Decimate 0.1.2. Binary array protocol 2 (protocol 1's arrays; feature noise pruned), little endian on
// supported desktop targets.
#include <pmp/surface_mesh.h>
#include <pmp/algorithms/decimation.h>
#include <pmp/algorithms/remeshing.h>
#include <pmp/algorithms/features.h>
#include <fstream>
#include <iostream>
#include <cstdint>
#include <cmath>
#include <algorithm>
#include <stdexcept>
#include <numeric>
#include <string>
#include <unordered_map>
#include <vector>
#ifdef _WIN32
#define NOMINMAX
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <psapi.h>
#else
#include <sys/resource.h>
#endif

namespace pmp { extern std::size_t atlas_face_limit, atlas_face_reached; }   // remeshing.cpp, Atlas's patched copy

template<class T> T read(std::istream& s) { T x{}; s.read(reinterpret_cast<char*>(&x), sizeof(x)); if(!s) throw std::runtime_error("Truncated input arrays"); return x; }
template<class T> void write(std::ostream& s, T x) { s.write(reinterpret_cast<char*>(&x), sizeof(x)); }

// Sharp edges on scans and decimated models are mostly noise: short, scattered chains. PMP may not collapse or flip a
// feature edge, so each one blocks the remesher around it: faces multiply (five times the target on a 25k-face scan)
// and every iteration slows. Keep feature curves of at least min_edges edges and min_length; drop the rest.
// Returns the number of feature edges dropped.

// A face with no height (a "cap": one corner on the line of the opposite edge) breaks every tool downstream. PMP's
// remove_caps skips locked edges, and Atlas locks every edge at a crease corner, so caps next to creases survive the
// remesh. Last pass: flip the long edge away where allowed (never on a boundary); otherwise move the corner off the
// line by a thousandth of the edge, in the neighbouring face's plane and away from that face (no fold), which gives
// the face real height without a visible change. Returns the number of faces fixed.
static unsigned fix_caps(pmp::SurfaceMesh& mesh) {
    unsigned fixed=0;
    for(int pass=0; pass<5; ++pass) {
        unsigned changed=0;
        for(auto f:mesh.faces()) {
            pmp::Halfedge h;                               // the longest edge, a -> c
            double best=-1;
            for(auto hh:mesh.halfedges(f)) {
                double l=pmp::distance(mesh.position(mesh.from_vertex(hh)), mesh.position(mesh.to_vertex(hh)));
                if(l>best) { best=l; h=hh; }
            }
            if(best<=0) continue;
            auto a=mesh.position(mesh.from_vertex(h)), c=mesh.position(mesh.to_vertex(h));
            auto v=mesh.to_vertex(mesh.next_halfedge(h));
            auto p=mesh.position(v);
            double height=pmp::norm(pmp::cross(c-a, p-a))/best;
            if(height>=1e-5*best) continue;
            auto e=mesh.edge(h);
            if(!mesh.is_boundary(e) && mesh.is_flip_ok(e)) { mesh.flip(e); ++changed; continue; }
            auto o=mesh.opposite_halfedge(h);
            if(mesh.is_boundary(o) || mesh.is_boundary(v)) continue;
            auto x=mesh.position(mesh.to_vertex(mesh.next_halfedge(o)));
            auto d=(c-a)/best;
            auto foot=a+d*pmp::dot(x-a,d);
            auto away=foot-x;
            double n=pmp::norm(away);
            if(n<=1e-9*best) {
                // the neighbour's corner is on the same line (several crease points in a row): go across the line,
                // within the surface around the corner
                pmp::Point normal(0,0,0);
                for(auto g:mesh.faces(v)) {
                    auto it=mesh.vertices(g);
                    auto q0=mesh.position(*it); ++it; auto q1=mesh.position(*it); ++it; auto q2=mesh.position(*it);
                    normal+=pmp::cross(q1-q0, q2-q0);
                }
                away=pmp::cross(normal, d);
                n=pmp::norm(away);
                if(n<=0) continue;
            }
            mesh.position(v)=p+away*(1e-3*best/n);
            ++changed;
        }
        fixed+=changed;
        if(!changed) break;
    }
    return fixed;
}

static void drop_features(pmp::SurfaceMesh& mesh) {
    auto ef=mesh.get_edge_property<bool>("e:feature");
    if(ef) for(auto e:mesh.edges()) ef[e]=false;
    auto vf=mesh.get_vertex_property<bool>("v:feature");
    if(vf) for(auto v:mesh.vertices()) vf[v]=false;
}


static unsigned prune_short_features(pmp::SurfaceMesh& mesh, double min_length, unsigned min_edges) {
    auto efeature = mesh.get_edge_property<bool>("e:feature");
    auto vfeature = mesh.get_vertex_property<bool>("v:feature");
    if(!efeature || !vfeature) return 0;
    std::vector<unsigned> parent(mesh.n_vertices());
    std::iota(parent.begin(), parent.end(), 0u);
    auto find = [&](unsigned x) { while(parent[x]!=x) { parent[x]=parent[parent[x]]; x=parent[x]; } return x; };
    for(auto e:mesh.edges()) if(efeature[e]) {
        unsigned a=find(mesh.vertex(e,0).idx()), b=find(mesh.vertex(e,1).idx());
        if(a!=b) parent[a]=b;
    }
    std::unordered_map<unsigned, std::pair<double,unsigned>> curve;
    for(auto e:mesh.edges()) if(efeature[e]) {
        auto& c=curve[find(mesh.vertex(e,0).idx())];
        c.first+=pmp::distance(mesh.position(mesh.vertex(e,0)), mesh.position(mesh.vertex(e,1)));
        c.second+=1;
    }
    unsigned dropped=0;
    for(auto e:mesh.edges()) if(efeature[e]) {
        const auto& c=curve[find(mesh.vertex(e,0).idx())];
        if(c.second<min_edges || c.first<min_length) { efeature[e]=false; ++dropped; }
    }
    for(auto v:mesh.vertices()) vfeature[v]=false;
    for(auto e:mesh.edges()) if(efeature[e]) { vfeature[mesh.vertex(e,0)]=true; vfeature[mesh.vertex(e,1)]=true; }
    return dropped;
}

int main(int argc, char** argv) {
    try {
        if(argc == 2 && std::string(argv[1]) == "--version") { std::cout << "atlas-remesh-core 0.1.2 protocol 2\n"; return 0; }
        if(argc != 15) throw std::runtime_error("Expected input output method target length min max error normal angle boundaries iterations density project");
        std::ifstream in(argv[1], std::ios::binary);
        if(read<uint32_t>(in) != 1) throw std::runtime_error("Unsupported array protocol");
        auto nv=read<uint32_t>(in), nf=read<uint32_t>(in);
        if(!nv || !nf || nv>50000000 || nf>50000000) throw std::runtime_error("Invalid array counts");
        pmp::SurfaceMesh mesh;
        mesh.reserve(nv, nf*3/2+nv, nf);
        for(uint32_t i=0;i<nv;++i) { auto x=read<double>(in),y=read<double>(in),z=read<double>(in); mesh.add_vertex(pmp::Point(x,y,z)); }
        for(uint32_t i=0;i<nf;++i) {
            auto a=read<uint32_t>(in),b=read<uint32_t>(in),c=read<uint32_t>(in);
            if(a>=nv || b>=nv || c>=nv) throw std::runtime_error("Invalid triangle index");
            mesh.add_triangle(pmp::Vertex(a),pmp::Vertex(b),pmp::Vertex(c));
        }
        auto weights=mesh.vertex_property<double>("v:atlas_weight");
        for(auto v:mesh.vertices()) weights[v]=read<double>(in);
        const std::string method=argv[3];
        auto target=std::stoul(argv[4]);
        double length=std::stod(argv[5]), minimum=std::stod(argv[6]), maximum=std::stod(argv[7]);
        double error=std::stod(argv[8]), normal=std::stod(argv[9]), angle=std::stod(argv[10]);
        bool boundary=std::stoi(argv[11])!=0;
        int iterations=std::stoi(argv[12]);
        double density=std::stod(argv[13]);
        bool project=std::stoi(argv[14])!=0;
        if(angle>0) pmp::detect_features(mesh, angle);
        if(angle>0 && method!="decimate") {
            unsigned dropped=prune_short_features(mesh, 3.0*length, 4);
            std::cout << "FEATURES_DROPPED " << dropped << std::endl;
        }
        auto lock=mesh.vertex_property<bool>("v:atlas_lock",false);
        auto selected=mesh.vertex_property<bool>("v:selected",true);
        auto sizing=mesh.vertex_property<pmp::Scalar>("v:atlas_density",1.0);
        unsigned int boundary_vertices=0, movable=0;
        for(auto v:mesh.vertices()) {
            if(mesh.is_boundary(v)) ++boundary_vertices;
            lock[v]=boundary && mesh.is_boundary(v);
            if(method=="decimate" && weights[v]>=1.0-1e-8) lock[v]=true;
            selected[v]=!lock[v];
            movable+=selected[v];
            sizing[v]=1.0/std::sqrt(1.0+(density-1.0)*weights[v]);
        }
        std::cout << "PROGRESS 0.1 processing surface" << std::endl;
        if(method=="decimate") {
            long chi=long(mesh.n_vertices())-long(mesh.n_edges())+long(mesh.n_faces());
            unsigned int vt=unsigned(std::max(4L,(long(target)+long(boundary_vertices)+2*chi)/2));
            if(movable && target<mesh.n_faces()) pmp::decimate(mesh,vt,0,0,0,normal,error);
        } else if(method=="adaptive" || method=="uniform") {
            auto run=[&](pmp::SurfaceMesh& m) {
                if(method=="adaptive") pmp::adaptive_remeshing(m, minimum, maximum, std::max(error,1e-12), iterations, project);
                else pmp::uniform_remeshing(m, length, iterations, project);
            };
            // Past this the result could not be used anyway (Remesh/Decimate asks for at most two million faces):
            // stop and say so, rather than grow toward PMP's own eight-million guard for many minutes. Adaptive
            // triangles on a rough scan can want their minimum edge everywhere, about eight times the faces.
            const std::size_t hard_cap=3000000;
            auto capped=[&](pmp::SurfaceMesh& m) {
                pmp::atlas_face_limit=hard_cap;
                try { run(m); pmp::atlas_face_limit=0; }
                catch(const std::runtime_error& e) {
                    pmp::atlas_face_limit=0;
                    if(std::string(e.what())!="ATLAS_FACE_LIMIT") throw;
                    throw std::runtime_error("ATLAS_TOO_MANY_FACES "+std::to_string(pmp::atlas_face_reached));
                }
            };
            bool features=false;
            if(auto ef=mesh.get_edge_property<bool>("e:feature")) for(auto e:mesh.edges()) if(ef[e]) { features=true; break; }
            if(!features) capped(mesh);
            else {
                // Sharp features that are really scan noise stop the remesher from collapsing edges around them, so
                // the faces multiply and every iteration slows. Real creases never need more than a few times the
                // faces a uniform mesh at this edge length has (1.07-1.23 on a chess pawn, a rook, a teapot): past twice
                // that, start again without the features.
                double area=0;
                for(auto f:mesh.faces()) {
                    auto it=mesh.vertices(f);
                    auto a=mesh.position(*it); ++it; auto b=mesh.position(*it); ++it; auto c=mesh.position(*it);
                    area+=0.5*pmp::norm(pmp::cross(b-a,c-a));
                }
                double unit=method=="uniform" ? length : std::sqrt(std::max(minimum,1e-12)*std::max(maximum,1e-12));
                double expected=area/(0.4330127*unit*unit);
                pmp::SurfaceMesh backup=mesh;
                pmp::atlas_face_limit=std::size_t(2.0*expected)+2000;
                try { run(mesh); pmp::atlas_face_limit=0; }
                catch(const std::runtime_error& e) {
                    pmp::atlas_face_limit=0;
                    if(std::string(e.what())!="ATLAS_FACE_LIMIT") throw;
                    std::size_t reached=pmp::atlas_face_reached ? pmp::atlas_face_reached : mesh.n_faces();
                    mesh=backup;
                    drop_features(mesh);
                    std::cout << "FEATURES_NOISY " << reached << " " << std::size_t(expected) << std::endl;
                    pmp::atlas_face_reached=0;
                    capped(mesh);
                }
            }
        } else throw std::runtime_error("Unknown method");
        if(unsigned caps=fix_caps(mesh)) std::cout << "CAPS_FIXED " << caps << std::endl;
        mesh.garbage_collection();
        std::ofstream out(argv[2], std::ios::binary);
        write<uint32_t>(out,1); write<uint32_t>(out,uint32_t(mesh.n_vertices())); write<uint32_t>(out,uint32_t(mesh.n_faces()));
        for(auto v:mesh.vertices()) for(int j=0;j<3;++j) write<double>(out,mesh.position(v)[j]);
        for(auto f:mesh.faces()) for(auto v:mesh.vertices(f)) write<uint32_t>(out,uint32_t(v.idx()));
        if(!out) throw std::runtime_error("Cannot write output arrays");
        std::cout << "PROGRESS 1 complete" << std::endl;
#ifdef _WIN32
        PROCESS_MEMORY_COUNTERS memory{};
        memory.cb = sizeof(memory);
        if(GetProcessMemoryInfo(GetCurrentProcess(), &memory, sizeof(memory)))
            std::cout << "PEAK_MB " << double(memory.PeakWorkingSetSize)/(1024*1024) << std::endl;
#else
        struct rusage usage{};
        if(getrusage(RUSAGE_SELF, &usage) == 0) {
#ifdef __APPLE__
            std::cout << "PEAK_MB " << double(usage.ru_maxrss)/(1024*1024) << std::endl;   // bytes on macOS
#else
            std::cout << "PEAK_MB " << double(usage.ru_maxrss)/1024 << std::endl;          // kilobytes on Linux
#endif
        }
#endif
        return 0;
    } catch(const std::exception& e) { std::cerr << e.what() << std::endl; return 2; }
}
