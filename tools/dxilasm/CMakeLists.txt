cmake_minimum_required(VERSION 3.20)
project(dxilasm LANGUAGES CXX)

add_executable(dxilasm dxilasm.cpp)
target_compile_features(dxilasm PRIVATE cxx_std_20)
target_compile_definitions(dxilasm PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN UNICODE _UNICODE)
target_compile_options(dxilasm PRIVATE /W4 /permissive- /utf-8)
set_target_properties(dxilasm PROPERTIES RUNTIME_OUTPUT_DIRECTORY_RELEASE ${CMAKE_CURRENT_SOURCE_DIR}/../../build/dxilasm)
