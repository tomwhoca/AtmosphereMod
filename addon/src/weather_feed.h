// Weather feed: haze tunables from the sim's own METARs.
//
// A background thread is an in-process SimConnect client. It receives the
// METARs the AtmosphereMod EFB app sends over CommBus, plus ground altitude
// and local time, and maps them to tunables with haze_mapping.h. The add-on
// blends the result into T1..T6, T9 and T10 by T13 (0 = tunables.ini only,
// 1 = weather-driven), smoothed over about 30 s so station changes don't jump.

#pragma once

#include <array>
#include <cstdint>
#include <filesystem>

namespace weather
{
constexpr size_t kTunableCount = 14;  // T0..T13, as in the add-on

// Starts the SimConnect thread; it retries until the sim's SimConnect server
// is up. Log: <log_dir>/weather.log.
void start(const std::filesystem::path &log_dir);

// Signals the thread and waits briefly; never blocks indefinitely (it may be
// called while the process is exiting).
void stop();

// The aircraft's own radar controls, for the radar model: the PMDG 737's
// captain-side tilt knob (L:switch_794_73X). valid only when a PMDG 737 is
// loaded and the knob has been read. Any thread.
struct AircraftRadar
{
    bool valid = false;
    double tilt_position = 0.0;  // knob animation position, 0..100
    float tilt_deg = 0.0f;       // antenna tilt, up positive (provisional mapping, weather_feed.cpp)
};
AircraftRadar aircraft_radar();

// The user aircraft for its shadow on clouds (cloud tunables C13-C15). The
// shader orients the aircraft by the sun: heading_from_sun is the true heading
// minus the sun's true azimuth (computed from the sim's UTC and position).
// valid only in the cockpit view, where the camera is the aircraft. Any thread.
struct AircraftShadow
{
    bool valid = false;    // the feed has the aircraft (span > 0)
    bool cockpit = false;  // in the cockpit view: the camera is the aircraft
    float heading_from_sun = 0.0f;  // rad, clockwise seen from above
    float bank = 0.0f;              // rad, right wing down positive
    float span_m = 0.0f;
};
AircraftShadow aircraft_shadow();

// Freezing level (m above sea level) for the radar model: the latest METAR's
// temperature lapsed at 6.5 C/km from the station's elevation. NAN until a
// METAR with a temperature arrives. Any thread.
float freezing_level_m();

// METAR anchoring of the clouds (cloud_stations.h): the station table the
// patched ch_cloudDensity reads from the debug buffer (tools/patch_dxil.py
// ANCHOR_TABLE) as it is now: fades and crossfades advance with time. Returns
// the stations in use. Any thread (the add-on: once per present).
constexpr size_t kAnchorTableBytes = 16 + 16 * 8 * 33;
size_t cloud_anchor_table(std::array<uint8_t, kAnchorTableBytes> &out);

// Present thread: advances the smoothing by dt_seconds and returns `ini` with
// the weather-driven tunables blended in by ini[13]. Returns `ini` unchanged
// until the first METAR arrives.
std::array<float, kTunableCount> apply(const std::array<float, kTunableCount> &ini, float dt_seconds);
} // namespace weather
