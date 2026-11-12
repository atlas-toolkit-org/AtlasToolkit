// Atlas Surface Texture: cancellable xatlas transport, protocol 2.
// xatlas's limits are absolute: faces under a fixed area are ignored, and texelsPerUnit sets the atlas in model units, so a
// large model asks for an atlas of hundreds of millions of texels. Every model is therefore unwrapped at one working size
// and resolution, and its UVs come back in the source model's units.
#include "xatlas.h"
#include <algorithm>
#include <fstream>
#include <iostream>
#include <vector>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <cmath>

constexpr double kWorkingSize=256.0;   // the model's longest side while it is unwrapped
constexpr float kTexelsPerUnit=8.0f;   // about 2048 texels along that side: only chart shapes are used, not their layout

template<class T> T read(std::istream& s) { T v{}; s.read(reinterpret_cast<char*>(&v), sizeof(v)); if(!s) throw std::runtime_error("Truncated arrays"); return v; }
template<class T> void write(std::ostream& s, T v) { s.write(reinterpret_cast<char*>(&v), sizeof(v)); }
bool progress(xatlas::ProgressCategory category, int percent, void*) {
    std::cout << "PROGRESS " << int(category) << " " << percent << std::endl;
    return true; // Atlas cancels the containing process/job, including native worker threads.
}
int main(int argc, char** argv) {
    try {
        if(argc==2 && std::string(argv[1])=="--version") { std::cout << "atlas-texture-core 0.2.0 protocol 2\n"; return 0; }
        if(argc!=3) throw std::runtime_error("Expected input and output array files");
        std::ifstream in(argv[1],std::ios::binary);
        if(read<uint32_t>(in)!=2) throw std::runtime_error("Unsupported protocol");
        auto nv=read<uint32_t>(in), nf=read<uint32_t>(in);
        if(!nv || !nf || nv>2000000 || nf>2000000) throw std::runtime_error("Unwrap exceeds two million input vertices/faces");
        std::vector<double> source(size_t(nv)*3);
        double low[3]={HUGE_VAL,HUGE_VAL,HUGE_VAL}, high[3]={-HUGE_VAL,-HUGE_VAL,-HUGE_VAL};
        for(size_t i=0;i<source.size();++i) {
            auto d=read<double>(in);
            if(!std::isfinite(d)) throw std::runtime_error("Nonfinite vertex");
            source[i]=d; low[i%3]=std::min(low[i%3],d); high[i%3]=std::max(high[i%3],d);
        }
        const double longest=std::max({high[0]-low[0],high[1]-low[1],high[2]-low[2]});
        if(!(longest>0.0)) throw std::runtime_error("The model has no size");
        const double scale=kWorkingSize/longest;
        std::vector<float> vertices(source.size());
        for(size_t i=0;i<source.size();++i) vertices[i]=float((source[i]-(low[i%3]+high[i%3])/2.0)*scale);
        std::vector<uint32_t> indices(size_t(nf)*3);
        for(auto& i:indices) { i=read<uint32_t>(in); if(i>=nv) throw std::runtime_error("Invalid index"); }
        auto* atlas=xatlas::Create();
        xatlas::SetProgressCallback(atlas,progress,nullptr);
        xatlas::MeshDecl decl;
        decl.vertexCount=nv; decl.vertexPositionData=vertices.data(); decl.vertexPositionStride=3*sizeof(float);
        decl.indexCount=nf*3; decl.indexData=indices.data(); decl.indexFormat=xatlas::IndexFormat::UInt32;
        auto error=xatlas::AddMesh(atlas,decl);
        if(error!=xatlas::AddMeshError::Success) { xatlas::Destroy(atlas); throw std::runtime_error("xatlas rejected the mesh"); }
        xatlas::ChartOptions charts;
        xatlas::PackOptions pack;
        pack.texelsPerUnit=kTexelsPerUnit; pack.rotateCharts=false; pack.rotateChartsToAxis=false; pack.padding=2;
        xatlas::Generate(atlas,charts,pack);
        if(!atlas->meshCount || atlas->texelsPerUnit<=0) { xatlas::Destroy(atlas); throw std::runtime_error("xatlas generated no charts"); }
        const auto& mesh=atlas->meshes[0];
        std::ofstream out(argv[2],std::ios::binary);
        write<uint32_t>(out,2); write<uint32_t>(out,mesh.vertexCount); write<uint32_t>(out,mesh.indexCount/3);
        for(uint32_t i=0;i<mesh.vertexCount;++i) write<uint32_t>(out,mesh.vertexArray[i].xref);
        for(uint32_t i=0;i<mesh.vertexCount;++i) for(int j=0;j<2;++j) write<double>(out,mesh.vertexArray[i].uv[j]/(double(atlas->texelsPerUnit)*scale));
        for(uint32_t i=0;i<mesh.indexCount;++i) write<uint32_t>(out,mesh.indexArray[i]);
        for(uint32_t i=0;i<mesh.vertexCount;++i) write<int32_t>(out,mesh.vertexArray[i].chartIndex);
        xatlas::Destroy(atlas);
        if(!out) throw std::runtime_error("Cannot write UV arrays");
        return 0;
    } catch(const std::exception& e) { std::cerr << e.what() << std::endl; return 2; }
}
