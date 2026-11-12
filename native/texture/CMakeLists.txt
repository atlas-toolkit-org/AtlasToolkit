cmake_minimum_required(VERSION 3.24)
cmake_policy(SET CMP0091 NEW)
project(atlas_texture VERSION 0.2.0 LANGUAGES CXX)
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
# xatlas (MIT) is vendored, pinned to f700c7790aaa030e794b52ba7791a05c085faf0c.
add_executable(atlas-texture-core main.cpp third_party/xatlas/xatlas.cpp)
target_include_directories(atlas-texture-core PRIVATE third_party/xatlas)
target_compile_features(atlas-texture-core PRIVATE cxx_std_17)
find_package(Threads REQUIRED)     # xatlas uses std::thread
target_link_libraries(atlas-texture-core PRIVATE Threads::Threads)
if(MSVC)
  target_compile_options(atlas-texture-core PRIVATE /O2 /utf-8)
endif()
install(TARGETS atlas-texture-core RUNTIME DESTINATION bin)
