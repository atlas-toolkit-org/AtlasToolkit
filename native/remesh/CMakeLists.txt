cmake_minimum_required(VERSION 3.24)
cmake_policy(SET CMP0091 NEW)
set(CMAKE_TRY_COMPILE_CONFIGURATION Release)
project(atlas_remesh VERSION 0.1.1 LANGUAGES CXX)

# Pinned sources fetched by native/build.py (see native/deps.lock.json).
set(ATLAS_DEPS_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../_deps" CACHE PATH "Pinned third-party sources")
set(PMP_DIR "${ATLAS_DEPS_DIR}/pmp")

set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
set(PMP_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(PMP_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(PMP_BUILD_DOCS OFF CACHE BOOL "" FORCE)
set(PMP_BUILD_VIS OFF CACHE BOOL "" FORCE)
set(PMP_INSTALL OFF CACHE BOOL "" FORCE)
set(PMP_SCALAR_TYPE 64 CACHE STRING "" FORCE)
# Warnings in third-party code must not fail the build on a newer compiler.
set(PMP_STRICT_COMPILATION OFF CACHE BOOL "" FORCE)
# PMP has no OpenMP code of its own, but links it when found: the executable would then need vcomp140.dll or libgomp.
set(CMAKE_DISABLE_FIND_PACKAGE_OpenMP ON)
set(EIGEN3_INCLUDE_DIR "${PMP_DIR}/external/eigen-3.4.0")

# Atlas's extensions go into generated copies of two PMP sources; the checkout stays verbatim.
find_package(Python3 REQUIRED COMPONENTS Interpreter)
set(ATLAS_GENERATED "${CMAKE_CURRENT_BINARY_DIR}/generated")
execute_process(
  COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/generate.py" --pmp "${PMP_DIR}" --out "${ATLAS_GENERATED}"
  RESULT_VARIABLE ATLAS_GENERATE_RESULT)
if(NOT ATLAS_GENERATE_RESULT EQUAL 0)
  message(FATAL_ERROR "remesh/generate.py failed (${ATLAS_GENERATE_RESULT})")
endif()
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
  "${CMAKE_CURRENT_SOURCE_DIR}/generate.py"
  "${PMP_DIR}/src/pmp/algorithms/remeshing.cpp"
  "${PMP_DIR}/src/pmp/algorithms/decimation.cpp")

add_subdirectory("${PMP_DIR}" pmp EXCLUDE_FROM_ALL)
get_target_property(PMP_SOURCES pmp SOURCES)
list(FILTER PMP_SOURCES EXCLUDE REGEX "[/\\](remeshing|decimation).cpp$")
set_property(TARGET pmp PROPERTY SOURCES ${PMP_SOURCES})
target_sources(pmp PRIVATE "${ATLAS_GENERATED}/remeshing.cpp")
target_sources(pmp PRIVATE "${ATLAS_GENERATED}/decimation.cpp")
target_include_directories(pmp PUBLIC "${PMP_DIR}/src")
add_executable(atlas-remesh-core main.cpp)
target_compile_features(atlas-remesh-core PRIVATE cxx_std_17)
target_compile_definitions(atlas-remesh-core PRIVATE PMP_SCALAR_TYPE_64)
find_package(Threads REQUIRED)
target_link_libraries(atlas-remesh-core PRIVATE pmp Threads::Threads)
if(WIN32)
  target_link_libraries(atlas-remesh-core PRIVATE psapi)
endif()
if(MSVC)
  target_compile_options(atlas-remesh-core PRIVATE /W4 /utf-8)
endif()
install(TARGETS atlas-remesh-core RUNTIME DESTINATION bin)
