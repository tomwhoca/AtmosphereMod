cmake_minimum_required(VERSION 3.20)
project(atmolab LANGUAGES CXX)

add_executable(atmolab atmolab.cpp)
target_compile_features(atmolab PRIVATE cxx_std_20)
target_compile_definitions(atmolab PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN UNICODE _UNICODE)
target_compile_options(atmolab PRIVATE /W4 /permissive- /utf-8)
target_link_libraries(atmolab PRIVATE d3d12 dxgi)
set_target_properties(atmolab PROPERTIES RUNTIME_OUTPUT_DIRECTORY_RELEASE ${CMAKE_CURRENT_SOURCE_DIR}/../../build/lab)

# (the radar lab needs the radar replacement's private sources)
if(EXISTS ${CMAKE_CURRENT_SOURCE_DIR}/radarlab.cpp)
add_executable(radarlab radarlab.cpp)
target_compile_features(radarlab PRIVATE cxx_std_20)
target_compile_definitions(radarlab PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN UNICODE _UNICODE _CRT_SECURE_NO_WARNINGS)
target_compile_options(radarlab PRIVATE /W4 /permissive- /utf-8)
target_link_libraries(radarlab PRIVATE d3d12)
set_target_properties(radarlab PROPERTIES RUNTIME_OUTPUT_DIRECTORY_RELEASE ${CMAKE_CURRENT_SOURCE_DIR}/../../build/lab)

# radarlab's vertex shader, packed like the stock radar shaders' signatures.
file(GLOB DXC_CANDIDATES "C:/Program Files (x86)/Windows Kits/10/bin/10.*/x64/dxc.exe")
list(SORT DXC_CANDIDATES)
list(GET DXC_CANDIDATES -1 DXC)
set(RADARLAB_VS ${CMAKE_CURRENT_SOURCE_DIR}/../../build/lab/radarlab_vs.cso)
add_custom_command(OUTPUT ${RADARLAB_VS}
    COMMAND ${DXC} -nologo -T vs_6_7 -E main -O3 -pack-optimized -Fo ${RADARLAB_VS} ${CMAKE_CURRENT_SOURCE_DIR}/radarlab_vs.hlsl
    DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/radarlab_vs.hlsl)
add_custom_target(radarlab_vs ALL DEPENDS ${RADARLAB_VS})
add_dependencies(radarlab radarlab_vs)
endif()

add_executable(psotest psotest.cpp)
target_compile_features(psotest PRIVATE cxx_std_20)
target_compile_definitions(psotest PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN UNICODE _UNICODE)
target_compile_options(psotest PRIVATE /W4 /permissive- /utf-8)
target_link_libraries(psotest PRIVATE d3d12)
set_target_properties(psotest PROPERTIES RUNTIME_OUTPUT_DIRECTORY_RELEASE ${CMAKE_CURRENT_SOURCE_DIR}/../../build/lab)

# The METAR anchoring's station set (tools/test_cloud_stations.py).
add_executable(cloud_stations_test cloud_stations_test.cpp)
target_compile_features(cloud_stations_test PRIVATE cxx_std_20)
target_compile_definitions(cloud_stations_test PRIVATE NOMINMAX)
target_compile_options(cloud_stations_test PRIVATE /W4 /permissive- /utf-8)
target_include_directories(cloud_stations_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/../../addon/src)
set_target_properties(cloud_stations_test PROPERTIES RUNTIME_OUTPUT_DIRECTORY_RELEASE ${CMAKE_CURRENT_SOURCE_DIR}/../../build/lab)

# The METAR parser and anchoring, layers without a base (tools/test_metar_parse.py).
add_executable(metar_parse_test metar_parse_test.cpp)
target_compile_features(metar_parse_test PRIVATE cxx_std_20)
target_compile_definitions(metar_parse_test PRIVATE NOMINMAX)
target_compile_options(metar_parse_test PRIVATE /W4 /permissive- /utf-8)
target_include_directories(metar_parse_test PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/../../addon/src)
set_target_properties(metar_parse_test PROPERTIES RUNTIME_OUTPUT_DIRECTORY_RELEASE ${CMAKE_CURRENT_SOURCE_DIR}/../../build/lab)
