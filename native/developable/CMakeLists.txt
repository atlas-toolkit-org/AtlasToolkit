cmake_minimum_required(VERSION 3.18)
project(atlas_developable LANGUAGES CXX)

# Pinned sources: native/_deps in the repo (native/build.py fetches them), or ./deps inside the
# corresponding-source archive (developable-source.zip), which builds offline on its own.
if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/deps")
  set(_atlas_deps "${CMAKE_CURRENT_SOURCE_DIR}/deps")
else()
  set(_atlas_deps "${CMAKE_CURRENT_SOURCE_DIR}/../_deps")
endif()
set(ATLAS_DEPS_DIR "${_atlas_deps}" CACHE PATH "Pinned third-party sources")

add_executable(atlas-developable-core main.cpp)
target_compile_features(atlas-developable-core PRIVATE cxx_std_14)
target_include_directories(atlas-developable-core PRIVATE vendor vendor/include
  "${ATLAS_DEPS_DIR}/libigl/include" "${ATLAS_DEPS_DIR}/eigen" "${ATLAS_DEPS_DIR}/json/single_include")
target_compile_definitions(atlas-developable-core PRIVATE IGNORE_VALENCE_3 NOMINMAX _USE_MATH_DEFINES)
find_package(Threads REQUIRED)     # libigl uses std::thread
target_link_libraries(atlas-developable-core PRIVATE Threads::Threads)
if(MSVC)
  set_property(TARGET atlas-developable-core PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
  target_compile_options(atlas-developable-core PRIVATE /bigobj /O2 /wd4244 /wd4267)
else()
  target_compile_options(atlas-developable-core PRIVATE -O3)
endif()
install(TARGETS atlas-developable-core RUNTIME DESTINATION bin)
