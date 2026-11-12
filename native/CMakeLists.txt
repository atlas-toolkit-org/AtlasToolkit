# Atlas Toolkit native helpers: one superbuild over the four standalone helper projects.
#
#   python native/build.py                 the supported entry point (fetches pinned deps, sets up the
#                                          toolchain, builds, installs into the Python package)
#   cmake --preset <os> && cmake --build --preset <os>
#                                          the same build from an IDE or CI, once native/_deps is populated
#
# Each helper stays its own CMake project (its own dependencies, flags and static runtime), built as an
# ExternalProject so their third-party targets never meet in one tree. Executables land in
# build/<preset>/stage/bin.
cmake_minimum_required(VERSION 3.24)
project(atlas_native LANGUAGES NONE)
include(ExternalProject)

set(ATLAS_DEPS_DIR "${CMAKE_CURRENT_SOURCE_DIR}/_deps" CACHE PATH "Pinned third-party sources (native/build.py)")
set(ATLAS_STAGE_DIR "${CMAKE_BINARY_DIR}/stage" CACHE PATH "Install prefix for the helper executables")
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

set(_atlas_forward "")
foreach(var Python3_EXECUTABLE CMAKE_CXX_COMPILER CMAKE_OSX_ARCHITECTURES CMAKE_OSX_DEPLOYMENT_TARGET CMAKE_EXE_LINKER_FLAGS)
  if(DEFINED ${var} AND NOT "${${var}}" STREQUAL "")
    list(APPEND _atlas_forward "-D${var}=${${var}}")
  endif()
endforeach()

foreach(helper texture stripes remesh developable)
  string(TOUPPER "${helper}" HELPER)
  option(ATLAS_NATIVE_${HELPER} "Build the ${helper} helper" ON)
  if(ATLAS_NATIVE_${HELPER})
    ExternalProject_Add(${helper}
      SOURCE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/${helper}"
      BINARY_DIR "${CMAKE_BINARY_DIR}/${helper}"
      INSTALL_DIR "${ATLAS_STAGE_DIR}"
      CMAKE_ARGS
        -DCMAKE_BUILD_TYPE=${CMAKE_BUILD_TYPE}
        -DCMAKE_INSTALL_PREFIX=<INSTALL_DIR>
        -DATLAS_DEPS_DIR=${ATLAS_DEPS_DIR}
        ${_atlas_forward}
      BUILD_ALWAYS ON
      USES_TERMINAL_CONFIGURE ON
      USES_TERMINAL_BUILD ON)
  endif()
endforeach()
