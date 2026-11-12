// Headless adapter for Knoppel et al. (2015), via David Jourdan's Geometry Central implementation.
#include "geometrycentral/surface/meshio.h"
#include "geometrycentral/surface/direction_fields.h"
#include "geometrycentral/surface/stripe_patterns.h"
#include "geometrycentral/utilities/utilities.h"
#include <cmath>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <stdexcept>
#include <string>
#ifdef _WIN32
#include <windows.h>
#endif

using namespace geometrycentral;
using namespace geometrycentral::surface;
constexpr const char* VERSION = "0.1.0";

int execute(const std::vector<std::string>& args) {
  try {
    if (args.size() == 2 && args[1] == "--version") {
      std::cout << "atlas-stripes-core " << VERSION << " gc=" << ATLAS_STRIPES_REVISION << std::endl;
      return 0;
    }
    std::map<std::string, std::string> options{{"--field", "curvature"}, {"--rotation", "0"},
      {"--families", "single"}, {"--seed", "0"}};
    for (size_t i=1; i<args.size(); i+=2) {
      if (i+1 >= args.size()) throw std::invalid_argument("every option requires a value");
      const auto& key = args[i];
      if (key != "--input" && key != "--output" && key != "--spacing" && !options.count(key))
        throw std::invalid_argument("unknown option " + key);
      options[key] = args[i+1];
    }
    if (!options.count("--input") || !options.count("--output") || !options.count("--spacing"))
      throw std::invalid_argument("usage: atlas-stripes-core --input triangles.obj --output curves.json --spacing model_units [--field curvature|smoothest] [--rotation degrees] [--families single|orthogonal] [--seed integer]");
    if (options["--field"] != "curvature" && options["--field"] != "smoothest")
      throw std::invalid_argument("field must be curvature or smoothest");
    if (options["--families"] != "single" && options["--families"] != "orthogonal")
      throw std::invalid_argument("families must be single or orthogonal");
    size_t consumed;
    double spacing = std::stod(options["--spacing"], &consumed);
    if (consumed != options["--spacing"].size() || !std::isfinite(spacing) || spacing <= 0)
      throw std::invalid_argument("spacing must be finite and positive");
    double rotation = std::stod(options["--rotation"], &consumed);
    if (consumed != options["--rotation"].size() || !std::isfinite(rotation))
      throw std::invalid_argument("rotation must be finite");
    unsigned seed = static_cast<unsigned>(std::stoul(options["--seed"], &consumed));
    if (consumed != options["--seed"].size()) throw std::invalid_argument("seed must be an integer");
    std::srand(seed);
    util_mersenne_twister.seed(seed);
    std::ifstream input(std::filesystem::u8path(options["--input"]));
    if (!input) throw std::invalid_argument("cannot open input OBJ");
    std::cerr << "PROGRESS 0.05 Loading mesh" << std::endl;
    auto loaded = readManifoldSurfaceMesh(input, "obj");
    auto& mesh = *std::get<0>(loaded);
    auto& geometry = *std::get<1>(loaded);
    if (!mesh.isTriangular() || mesh.nVertices() < 3)
      throw std::invalid_argument("input must contain nondegenerate triangles");
    geometry.requireVertexPositions();
    geometry.requireFaceAreas();
    for (Face f : mesh.faces()) {
      if (!std::isfinite(geometry.faceAreas[f]) || geometry.faceAreas[f] <= 1e-16)
        throw std::invalid_argument("input contains degenerate triangles");
    }
    std::cerr << "PROGRESS 0.15 Computing direction field" << std::endl;
    VertexData<Vector2> guide = options["--field"] == "smoothest"
      ? computeSmoothestVertexDirectionField(geometry, 2)
      : computeCurvatureAlignedVertexDirectionField(geometry, 2);
    bool fallback = false;
    for (Vertex v : mesh.vertices()) {
      if (!std::isfinite(guide[v].x) || !std::isfinite(guide[v].y) || guide[v].norm() < 1e-12)
        fallback = true;
    }
    if (fallback) {
      std::srand(seed);
      guide = computeSmoothestVertexDirectionField(geometry, 2);
    }
    // In the 2-RoSy power representation the visible stripe tangent is 90deg from the phase gradient.
    Vector2 turn = Vector2::fromAngle(2 * (rotation * PI / 180 + PI/2));
    for (Vertex v : mesh.vertices()) {
      guide[v] *= turn;
      if (!std::isfinite(guide[v].x) || !std::isfinite(guide[v].y))
        throw std::runtime_error("direction field computation returned non-finite values");
    }
    VertexData<double> frequencies(mesh, 1.0 / spacing); // GC expects cycles per model unit, not radians.
    int families = options["--families"] == "orthogonal" ? 2 : 1;
    std::ofstream out(std::filesystem::u8path(options["--output"]));
    if (!out) throw std::invalid_argument("cannot open output JSON");
    out << std::setprecision(17) << "{\"schema\":2,\"backend\":\"" << VERSION
        << "\",\"revision\":\"" << ATLAS_STRIPES_REVISION << "\",\"curvature_fallback\":"
        << (fallback ? "true" : "false") << ",\"mesh_vertices\":[";
    geometry.requireVertexIndices();
    bool comma = false;
    for (Vertex v : mesh.vertices()) {
      auto p = geometry.vertexPositions[v];
      if (comma) out << ',';
      comma = true;
      out << '[' << p.x << ',' << p.y << ',' << p.z << ']';
    }
    out << "],\"mesh_faces\":[";
    comma = false;
    for (Face f : mesh.faces()) {
      if (comma) out << ',';
      comma = true;
      out << '[';
      bool inner = false;
      for (Vertex v : f.adjacentVertices()) {
        if (inner) out << ',';
        inner = true;
        out << geometry.vertexIndices[v];
      }
      out << ']';
    }
    out << "],\"families\":[";
    size_t totalEdges = 0;
    for (int family=0; family<families; ++family) {
      std::cerr << "PROGRESS " << (0.25 + family * 0.35) << " Solving stripe family " << family+1 << std::endl;
      CornerData<double> values;
      FaceData<int> zeros, branches;
      std::tie(values, zeros, branches) = computeStripePattern(geometry, frequencies, guide);
      for (Corner c : mesh.corners())
        if (!std::isfinite(values[c])) throw std::runtime_error("stripe solver returned non-finite phase values");
      size_t singularities=0;
      for (Face f : mesh.faces()) if (zeros[f] || branches[f]) ++singularities;
      std::vector<Vector3> points;
      std::vector<std::array<size_t,2>> edges;
      std::tie(points, edges) = extractPolylinesFromStripePattern(geometry, values, zeros, branches, guide, true);
      if (points.size() > 2000000 || edges.size() > 2000000)
        throw std::runtime_error("stripe curve budget exceeded; increase spacing");
      totalEdges += edges.size();
      if (family) out << ',';
      out << "{\"id\":" << family << ",\"singular_faces\":" << singularities << ",\"vertices\":[";
      for (size_t i=0; i<points.size(); ++i) {
        auto p = points[i];
        if (!std::isfinite(p.x) || !std::isfinite(p.y) || !std::isfinite(p.z))
          throw std::runtime_error("stripe extraction returned non-finite coordinates");
        if (i) out << ',';
        out << '[' << p.x << ',' << p.y << ',' << p.z << ']';
      }
      out << "],\"edges\":[";
      for (size_t i=0; i<edges.size(); ++i) {
        if (i) out << ',';
        out << '[' << edges[i][0] << ',' << edges[i][1] << ']';
      }
      out << "],\"corner_phase\":[";
      comma = false;
      for (Face f : mesh.faces()) {
        if (comma) out << ',';
        comma = true;
        out << '[';
        bool inner = false;
        for (Corner c : f.adjacentCorners()) {
          if (inner) out << ',';
          inner = true;
          out << values[c];
        }
        out << ']';
      }
      out << "]}";
      for (Vertex v : mesh.vertices()) guide[v] *= -1; // second visible family is orthogonal.
    }
    out << "]}\n";
    out.close();
    if (!out) throw std::runtime_error("failed writing curve result");
    if (!totalEdges) throw std::runtime_error("no stripes were extracted; decrease spacing");
    std::cerr << "PROGRESS 1 Stripe curves ready" << std::endl;
    return 0;
  } catch (const std::invalid_argument& e) {
    std::cerr << "ERROR " << e.what() << std::endl;
    return 2;
  } catch (const std::exception& e) {
    std::cerr << "ERROR " << e.what() << std::endl;
    return 3;
  }
}

#ifdef _WIN32
int wmain(int argc, wchar_t** argv) {
  std::vector<std::string> args;
  for (int i=0; i<argc; ++i) {
    int size = WideCharToMultiByte(CP_UTF8, 0, argv[i], -1, nullptr, 0, nullptr, nullptr);
    std::string arg(static_cast<size_t>(size), '\0');
    WideCharToMultiByte(CP_UTF8, 0, argv[i], -1, arg.data(), size, nullptr, nullptr);
    arg.pop_back();
    args.push_back(arg);
  }
  return execute(args);
}
#else
int main(int argc, char** argv) { return execute(std::vector<std::string>(argv, argv+argc)); }
#endif
