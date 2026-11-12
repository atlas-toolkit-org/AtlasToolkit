// Atlas Remesh/Decimate 0.1.1. Binary array protocol 1, little endian on supported desktop targets.
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
#include <string>
#ifdef _WIN32
#define NOMINMAX
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <psapi.h>
#else
#include <sys/resource.h>
#endif

template<class T> T read(std::istream& s) { T x{}; s.read(reinterpret_cast<char*>(&x), sizeof(x)); if(!s) throw std::runtime_error("Truncated input arrays"); return x; }
template<class T> void write(std::ostream& s, T x) { s.write(reinterpret_cast<char*>(&x), sizeof(x)); }

int main(int argc, char** argv) {
    try {
        if(argc == 2 && std::string(argv[1]) == "--version") { std::cout << "atlas-remesh-core 0.1.1 protocol 1\n"; return 0; }
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
        } else if(method=="adaptive") {
            pmp::adaptive_remeshing(mesh, minimum, maximum, std::max(error,1e-12), iterations, project);
        } else if(method=="uniform") {
            pmp::uniform_remeshing(mesh,length,iterations,project);
        } else throw std::runtime_error("Unknown method");
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
