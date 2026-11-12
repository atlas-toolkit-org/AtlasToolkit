cmake_minimum_required(VERSION 3.24)
cmake_policy(SET CMP0091 NEW)
set(CMAKE_TRY_COMPILE_CONFIGURATION Release)
project(atlas_stripes VERSION 0.1.0 LANGUAGES CXX)

# Pinned sources fetched by native/build.py (see native/deps.lock.json).
set(ATLAS_DEPS_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../_deps" CACHE PATH "Pinned third-party sources")

# Standalone, portable executable: Eigen only, static C++ runtime on Windows.
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
set(SUITESPARSE OFF CACHE BOOL "" FORCE)
set(GC_EIGEN_LOCATION "${ATLAS_DEPS_DIR}/eigen" CACHE PATH "" FORCE)
add_subdirectory("${ATLAS_DEPS_DIR}/geometry-central" geometry-central EXCLUDE_FROM_ALL)
add_executable(atlas-stripes-core main.cpp)
target_compile_features(atlas-stripes-core PRIVATE cxx_std_17)
find_package(Threads REQUIRED)
target_link_libraries(atlas-stripes-core PRIVATE geometry-central Threads::Threads)
target_compile_definitions(atlas-stripes-core PRIVATE
  ATLAS_STRIPES_REVISION="019669ddabda05e0f71fa3587cfb3c1dadf19cb8")
if(MSVC)
  target_compile_options(atlas-stripes-core PRIVATE /W4 /utf-8)
endif()
install(TARGETS atlas-stripes-core RUNTIME DESTINATION bin)
