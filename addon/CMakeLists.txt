cmake_minimum_required(VERSION 3.20)
# The version ReShade shows, and the release's (tools/make_release.py).
project(AtmosphereMod VERSION 0.5.0 LANGUAGES CXX RC)

configure_file(src/version.rc.in ${CMAKE_CURRENT_BINARY_DIR}/version.rc @ONLY)
add_library(AtmosphereMod SHARED src/atmosphere_mod.cpp src/weather_feed.cpp src/settings_bridge.cpp src/activesky.cpp
            ${CMAKE_CURRENT_BINARY_DIR}/version.rc)

# The weather feed is an in-process SimConnect client; the SDK's static library
# keeps SimConnect.dll out of the sim's folder.
set(SIMCONNECT_DIR "$ENV{MSFS2024_SDK}/SimConnect SDK")
if(NOT EXISTS "${SIMCONNECT_DIR}/include/SimConnect.h")
    message(FATAL_ERROR "SimConnect SDK not found at ${SIMCONNECT_DIR}; install the MSFS 2024 SDK")
endif()
target_include_directories(AtmosphereMod PRIVATE "${SIMCONNECT_DIR}/include")
target_link_libraries(AtmosphereMod PRIVATE "${SIMCONNECT_DIR}/lib/static/SimConnect.lib" ws2_32 shlwapi winhttp)
target_include_directories(AtmosphereMod PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/../third_party/reshade/include)
target_compile_features(AtmosphereMod PRIVATE cxx_std_20)
target_compile_definitions(AtmosphereMod PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN UNICODE _UNICODE)
target_compile_options(AtmosphereMod PRIVATE $<$<COMPILE_LANGUAGE:CXX>:/W4 /permissive- /utf-8>)

# ReShade loads add-ons by the .addon64 extension. DLL CRT: the static SimConnect
# library is built against it, and the sim already loads it (its package
# depends on the VC++ runtime), so it adds no dependency.
set_target_properties(AtmosphereMod PROPERTIES
    PREFIX ""
    SUFFIX ".addon64"
    MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>DLL")
