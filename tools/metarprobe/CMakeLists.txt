cmake_minimum_required(VERSION 3.20)
project(metarprobe LANGUAGES CXX)

# The MSFS 2024 SDK's SimConnect (installer sets MSFS2024_SDK).
set(SIMCONNECT_DIR "$ENV{MSFS2024_SDK}/SimConnect SDK")
if(NOT EXISTS "${SIMCONNECT_DIR}/include/SimConnect.h")
    message(FATAL_ERROR "SimConnect SDK not found at ${SIMCONNECT_DIR}; install the MSFS 2024 SDK")
endif()

add_executable(metarprobe metarprobe.cpp)
target_compile_features(metarprobe PRIVATE cxx_std_20)
target_compile_definitions(metarprobe PRIVATE NOMINMAX)
target_compile_options(metarprobe PRIVATE /W4 /permissive- /utf-8)
target_include_directories(metarprobe PRIVATE "${SIMCONNECT_DIR}/include" ${CMAKE_CURRENT_SOURCE_DIR}/../../addon/src)
target_link_libraries(metarprobe PRIVATE "${SIMCONNECT_DIR}/lib/SimConnect.lib")
set_target_properties(metarprobe PROPERTIES RUNTIME_OUTPUT_DIRECTORY_RELEASE ${CMAKE_CURRENT_SOURCE_DIR}/../../build/metarprobe)
add_custom_command(TARGET metarprobe POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different "${SIMCONNECT_DIR}/lib/SimConnect.dll" $<TARGET_FILE_DIR:metarprobe>)
