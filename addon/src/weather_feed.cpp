#include "weather_feed.h"

#include "cloud_stations.h"
#include "haze_mapping.h"

#include <windows.h>
#pragma warning(push, 0)  // the SDK header trips /W4
#include <SimConnect.h>
#pragma warning(pop)

#include <share.h>

#include <algorithm>
#include <atomic>
#include <cstddef>
#include <chrono>
#include <cctype>
#include <cmath>
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <string>
#include <thread>

namespace weather
{
namespace
{
enum : DWORD
{
    kDefAircraft = 1, kReqAircraft = 1, kDefAirport = 2, kReqAirport = 2, kDefRadar = 3, kReqRadar = 3,
    kReqAircraftPath = 4, kDefShadow = 5, kReqShadow = 5, kEvtMetar = 10, kEvtAircraftLoaded = 11,
    kReqStation0 = 100,  // ... + kStationRequests: the anchoring stations' facility data
};
constexpr DWORD kStationRequests = 16;

// The user aircraft for its shadow on clouds (C13-C15), every visual frame.
// Order of kShadowVars.
struct ShadowVars
{
    double heading_rad, bank_rad, span_ft, zulu_s, day_of_year, year, lat, lon, camera_state, view_type;
};
const struct { const char *name, *units; } kShadowVars[] = {
    { "PLANE HEADING DEGREES TRUE", "radians" },
    { "PLANE BANK DEGREES", "radians" },  // positive = left wing down (FSUIPC/SimConnect convention)
    { "WING SPAN", "feet" },
    { "ZULU TIME", "seconds" },
    { "ZULU DAY OF YEAR", "number" },
    { "ZULU YEAR", "number" },
    { "PLANE LATITUDE", "degrees" },
    { "PLANE LONGITUDE", "degrees" },
    { "CAMERA STATE", "number" },          // logged only: MSFS 2024 reported 32/35 in flight, not the SDK's 2
    { "CAMERA VIEW TYPE AND INDEX:0", "number" },  // 1 pilot, 2 instruments, 3 quickview: all in the cockpit
};
static_assert(sizeof(ShadowVars) == std::size(kShadowVars) * sizeof(double));
constexpr double kPi = 3.14159265358979323846;

// The PMDG 737's weather-radar panel knobs (tilt and gain, left and right),
// animation positions 0..100. The PMDG doesn't pass its tilt to the sim's
// radar, so the radar model reads its knob L:vars.
struct RadarKnobs
{
    double tilt_l, tilt_r, gain_l, gain_r;
};
const char *const kRadarKnobVars[] = { "L:switch_794_73X", "L:switch_795_73X", "L:switch_923_73X", "L:switch_922_73X" };
static_assert(sizeof(RadarKnobs) == std::size(kRadarKnobVars) * sizeof(double));

// Tilt knob position -> antenna tilt (deg, up positive). Provisional: linear,
// 50 = 0, the ends +-15 deg (the WXR-2100's range); calibrate against the ND's
// tilt readout (weather.log logs every knob change).
constexpr double kTiltCentre = 50.0, kTiltDegPerUnit = 15.0 / 50.0;

// CommBus events, shared with efb/PackageSources/AtmoWeather/src/AtmoWeather.tsx.
const char *const kMetarEvent = "AtmosphereMod.Metar";
const char *const kRequestEvent = "AtmosphereMod.Request";

// The METAR anchoring's survey (cloud_stations.h): besides the aircraft's
// nearest station (every minute), the stations nearest points ahead and to the
// sides of it, one every 15 s in turn, so stations are blended in before the
// aircraft gets to them. Bearings relative to the heading.
constexpr struct { double bearing_deg, km; } kSurveyPoints[] = { { 0.0, 50.0 }, { 0.0, 110.0 }, { -60.0, 60.0 },
                                                                  { 60.0, 60.0 } };
constexpr ULONGLONG kSurveyIntervalMs = 15000;

// Order of kAircraftVars.
struct AircraftVars
{
    double ground_m, local_time_s, agl_m, lat, lon;
};
const struct { const char *name, *units; } kAircraftVars[] = {
    { "GROUND ALTITUDE", "meters" },
    { "LOCAL TIME", "seconds" },
    { "PLANE ALT ABOVE GROUND", "meters" },
    { "PLANE LATITUDE", "degrees" },
    { "PLANE LONGITUDE", "degrees" },
};
static_assert(sizeof(AircraftVars) == std::size(kAircraftVars) * sizeof(double));

// Tunables the weather drives, and their place in haze::Tunables order.
constexpr size_t kDriven[] = { 1, 2, 3, 4, 5, 6, 9, 10 };
constexpr float kSmoothingSeconds = 30.0f;
constexpr double kGroundSmoothingSeconds = 300.0;  // terrain under a cruising aircraft changes fast
// Freezing level for the radar model: the METAR temperature lapsed at the
// standard 6.5 C/km from the station's elevation (SimConnect facility data).
constexpr double kLapseRatePerKm = 6.5;

std::thread g_thread;
std::atomic<bool> g_stop{false};
std::atomic<bool> g_finished{true};
FILE *g_log = nullptr;

std::mutex g_mutex;  // guards the fields below
haze::Inputs g_inputs;
bool g_have_ground = false;
double g_last_lat = 0.0, g_last_lon = 0.0;
std::array<float, std::size(kDriven)> g_target = {};
bool g_have_target = false;
std::string g_station;                   // METAR station whose elevation was requested
double g_station_elevation_m = NAN;      // NAN until the facility data arrives
double g_station_lat = NAN, g_station_lon = NAN;  // ... its position (facility data)
double g_metar_lat = NAN, g_metar_lon = NAN;      // the aircraft's when the METAR was requested
double g_heading_rad = NAN;                       // the aircraft's true heading, for the survey
struct SurveyPoint
{
    double lat = NAN, lon = NAN;
    ULONGLONG sent = 0;
};
std::array<SurveyPoint, 8> g_survey_sent = {};  // recent survey requests: their METARs aren't the aircraft's
size_t g_survey_next = 0;
std::array<std::string, kStationRequests> g_station_requests;  // facility data request -> station
DWORD g_station_request_next = 0;
std::atomic<bool> g_jumped{false};  // ask for METARs now
double g_logged_freezing_m = NAN;
bool g_pmdg737 = false;                  // from the loaded aircraft's config path
RadarKnobs g_knobs = {};
bool g_have_knobs = false;
RadarKnobs g_logged_knobs = { -1.0, -1.0, -1.0, -1.0 };
AircraftShadow g_shadow;
double g_logged_camera = -1.0;
ULONGLONG g_shadow_logged = 0;

// Present thread only.
std::array<float, std::size(kDriven)> g_smoothed = {};
bool g_smoothed_valid = false;

void log(const char *fmt, ...)
{
    if (g_log == nullptr)
        return;
    SYSTEMTIME t;
    GetLocalTime(&t);
    char line[1024];
    va_list args;
    va_start(args, fmt);
    vsnprintf(line, sizeof(line), fmt, args);
    va_end(args);
    fprintf(g_log, "%02u:%02u:%02u %s\n", t.wHour, t.wMinute, t.wSecond, line);
    fflush(g_log);
}

anchor::StationSet g_stations([](const std::string &line) { log("%s", line.c_str()); });

double now_s()
{
    return static_cast<double>(GetTickCount64()) * 0.001;
}

// The sun's azimuth (clockwise from true north) and elevation, radians, from
// the low-precision formulas of the Astronomical Almanac (~0.01 deg, 1950-2050).
void sun_position(double year, double day_of_year, double utc_s, double lat_deg, double lon_deg, double &azimuth,
                  double &elevation)
{
    const double deg = kPi / 180.0;
    const double leap_days = year > 2000.0 ? std::floor((year - 2001.0) / 4.0) + 1.0 : 0.0;
    const double n = (year - 2000.0) * 365.0 + leap_days + (day_of_year - 1.0) + utc_s / 86400.0 - 0.5;
    const double mean_longitude = std::fmod(280.460 + 0.9856474 * n, 360.0);
    const double anomaly = (357.528 + 0.9856003 * n) * deg;
    const double longitude = (mean_longitude + 1.915 * std::sin(anomaly) + 0.020 * std::sin(2.0 * anomaly)) * deg;
    const double obliquity = (23.439 - 0.0000004 * n) * deg;
    const double ra = std::atan2(std::cos(obliquity) * std::sin(longitude), std::cos(longitude));
    const double dec = std::asin(std::sin(obliquity) * std::sin(longitude));
    const double gmst_deg = std::fmod(18.697374558 + 24.06570982441908 * n, 24.0) * 15.0;
    const double hour_angle = (gmst_deg + lon_deg) * deg - ra;
    const double lat = lat_deg * deg;
    elevation = std::asin(std::sin(lat) * std::sin(dec) + std::cos(lat) * std::cos(dec) * std::cos(hour_angle));
    azimuth = std::atan2(-std::sin(hour_angle), std::tan(dec) * std::cos(lat) - std::sin(lat) * std::cos(hour_angle));
    if (azimuth < 0.0)
        azimuth += 2.0 * kPi;
}

// Caller holds g_mutex. The drawn silhouette needs the cockpit view (the
// cloud shader casts it from the camera, which is then the aircraft); the
// shadow from the sun's shadow map works in any view and only needs the span.
void update_shadow(const ShadowVars &v)
{
    double azimuth = 0.0, elevation = 0.0;
    sun_position(v.year, v.day_of_year, v.zulu_s, v.lat, v.lon, azimuth, elevation);
    const bool cockpit = v.view_type >= 1.0 && v.view_type <= 3.0;
    double relative = std::remainder(v.heading_rad - azimuth, 2.0 * kPi);
    g_shadow.valid = v.span_ft > 0.0;
    g_shadow.cockpit = cockpit;
    g_shadow.heading_from_sun = static_cast<float>(relative);
    g_shadow.bank = static_cast<float>(-v.bank_rad);  // to right wing down positive
    g_shadow.span_m = static_cast<float>(v.span_ft * 0.3048);
    const double camera = v.camera_state * 100.0 + v.view_type;
    if (camera != g_logged_camera)
    {
        g_logged_camera = camera;
        log("camera state %.0f, view type %.0f: aircraft shadow on clouds %s", v.camera_state, v.view_type,
            cockpit ? "on (cockpit: the sun's shadow map, else the drawn silhouette)"
                    : "from the sun's shadow map only (not the cockpit view)");
        g_shadow_logged = 0;
    }
    if (cockpit && GetTickCount64() - g_shadow_logged > 60000)
    {
        g_shadow_logged = GetTickCount64();
        log("shadow: heading %.1f, sun azimuth %.1f elevation %.1f (UTC day %.0f %.0f s), heading - sun %.1f, "
            "bank %.1f (right down +), span %.1f m",
            v.heading_rad / kPi * 180.0, azimuth / kPi * 180.0, elevation / kPi * 180.0, v.day_of_year, v.zulu_s,
            relative / kPi * 180.0, -v.bank_rad / kPi * 180.0, g_shadow.span_m);
    }
}

std::array<float, std::size(kDriven)> to_array(const haze::Tunables &t)
{
    return { t.bl_top_km, t.bl_edge_km, t.bl_haze, t.ft_haze, t.ft_scale_km, t.brown, t.g, t.angstrom };
}

// Caller holds g_mutex.
std::string recompute()
{
    const haze::Tunables t = haze::derive(g_inputs);
    g_target = to_array(t);
    g_have_target = true;
    return haze::describe(g_inputs, t);
}

// Caller holds g_mutex. NAN without a METAR temperature. Without the station's
// elevation (not an airport, or not answered yet) the terrain under the
// aircraft stands in.
double freezing_level_locked()
{
    if (!g_inputs.metar.valid || !std::isfinite(g_inputs.metar.temp_c))
        return NAN;
    const double elevation = std::isfinite(g_station_elevation_m) ? g_station_elevation_m : g_inputs.ground_km * 1000.0;
    return elevation + g_inputs.metar.temp_c / kLapseRatePerKm * 1000.0;
}

// Caller holds g_mutex. A METAR for the cloud anchoring's stations; a new
// station's position and elevation are asked for (facility data).
void anchor_metar(HANDLE sim, const haze::Metar &metar, double request_lat, double request_lon, bool at_aircraft)
{
    const double aircraft_lat = g_have_ground ? g_last_lat : NAN, aircraft_lon = g_have_ground ? g_last_lon : NAN;
    if (!g_stations.on_metar(metar, request_lat, request_lon, at_aircraft, g_inputs.ground_km * 1000.0,
                             g_inputs.local_hour, aircraft_lat, aircraft_lon, now_s()))
        return;
    const DWORD n = g_station_request_next++ % kStationRequests;
    g_station_requests[n] = metar.icao;
    SimConnect_RequestFacilityData(sim, kDefAirport, kReqStation0 + n, metar.icao.c_str());
}

// Caller holds g_mutex. A METAR the survey asked for (not the aircraft's).
bool surveyed(const haze::Metar &metar)
{
    for (const SurveyPoint &p : g_survey_sent)
        if (GetTickCount64() - p.sent < 180000 && std::abs(metar.request_lat - p.lat) < 1e-5 &&
            std::abs(metar.request_lon - p.lon) < 1e-5)
            return true;
    return false;
}

// Asks for the METAR nearest the next survey point.
void survey(HANDLE sim)
{
    double lat, lon, heading;
    {
        const std::lock_guard lock(g_mutex);
        if (!g_have_ground)
            return;
        lat = g_last_lat * kPi / 180.0;
        lon = g_last_lon * kPi / 180.0;
        heading = std::isfinite(g_heading_rad) ? g_heading_rad : 0.0;
    }
    const auto &point = kSurveyPoints[g_survey_next % std::size(kSurveyPoints)];
    const double bearing = heading + point.bearing_deg * kPi / 180.0, delta = point.km / 6371.0;
    const double lat2 = std::asin(std::sin(lat) * std::cos(delta) + std::cos(lat) * std::sin(delta) * std::cos(bearing));
    const double lon2 = lon + std::atan2(std::sin(bearing) * std::sin(delta) * std::cos(lat),
                                         std::cos(delta) - std::sin(lat) * std::sin(lat2));
    SurveyPoint p;
    p.lat = std::round(lat2 * 180.0 / kPi * 1e6) * 1e-6;
    p.lon = std::round(std::remainder(lon2 * 180.0 / kPi, 360.0) * 1e6) * 1e-6;
    p.sent = GetTickCount64();
    char body[96];
    snprintf(body, sizeof(body), "{\"lat\":%.6f,\"lon\":%.6f}", p.lat, p.lon);
    {
        const std::lock_guard lock(g_mutex);
        g_survey_sent[g_survey_next % g_survey_sent.size()] = p;
        ++g_survey_next;
    }
    SimConnect_CallCommBusEvent(sim, kRequestEvent, SIMCONNECT_COMM_BUS_BROADCAST_TO_JS,
                                static_cast<DWORD>(strlen(body) + 1), body);
}

// Caller holds g_mutex.
void log_freezing_level()
{
    const double level = freezing_level_locked();
    if (!std::isfinite(level) || (std::isfinite(g_logged_freezing_m) && std::abs(level - g_logged_freezing_m) < 100.0))
        return;
    g_logged_freezing_m = level;
    if (std::isfinite(g_station_elevation_m))
        log("radar freezing level %.0f m: %.0f C at %s (elevation %.0f m), %.1f C/km", level, g_inputs.metar.temp_c,
            g_inputs.metar.icao.c_str(), g_station_elevation_m, kLapseRatePerKm);
    else
        log("radar freezing level %.0f m: %.0f C at %s, from the terrain under the aircraft (%.0f m; station elevation "
            "unknown), %.1f C/km", level, g_inputs.metar.temp_c, g_inputs.metar.icao.c_str(),
            g_inputs.ground_km * 1000.0, kLapseRatePerKm);
}

double knob_tilt_deg(double position)
{
    return (position - kTiltCentre) * kTiltDegPerUnit;
}

// Caller holds g_mutex. Logs knob moves of 2 units or more (about 0.6 deg).
void log_radar_knobs()
{
    const double *now = &g_knobs.tilt_l, *logged = &g_logged_knobs.tilt_l;
    bool moved = false;
    for (size_t i = 0; i < std::size(kRadarKnobVars); ++i)
        moved |= std::abs(now[i] - logged[i]) >= 2.0;
    if (!moved)
        return;
    g_logged_knobs = g_knobs;
    log("PMDG 737 WXR knobs%s: tilt L %.1f (%+.1f deg) R %.1f (%+.1f deg), gain L %.1f R %.1f",
        g_pmdg737 ? "" : " (not a PMDG 737: unused)", g_knobs.tilt_l, knob_tilt_deg(g_knobs.tilt_l), g_knobs.tilt_r,
        knob_tilt_deg(g_knobs.tilt_r), g_knobs.gain_l, g_knobs.gain_r);
}

// Caller holds g_mutex.
void set_aircraft(const char *path)
{
    std::string lower(path);
    std::transform(lower.begin(), lower.end(), lower.begin(), [](unsigned char c) { return char(std::tolower(c)); });
    const bool pmdg737 = lower.find("pmdg") != std::string::npos && lower.find("737") != std::string::npos;
    if (pmdg737 != g_pmdg737 || !g_have_knobs)
        log("aircraft %s: %s", path, pmdg737 ? "PMDG 737, the radar model follows its tilt knob" : "not a PMDG 737");
    g_pmdg737 = pmdg737;
}

void request_metar(HANDLE sim)
{
    static const char body[] = "{}";  // the aircraft position
    SimConnect_CallCommBusEvent(sim, kRequestEvent, SIMCONNECT_COMM_BUS_BROADCAST_TO_JS, sizeof(body), body);
}

// Returns false when the sim quit.
bool dispatch(HANDLE sim, std::string &partial, std::string &last_metar, ULONGLONG &last_logged)
{
    SIMCONNECT_RECV *msg = nullptr;
    DWORD size = 0;
    while (SUCCEEDED(SimConnect_GetNextDispatch(sim, &msg, &size)))
    {
        switch (msg->dwID)
        {
        case SIMCONNECT_RECV_ID_SIMOBJECT_DATA:
        {
            const auto *data = static_cast<SIMCONNECT_RECV_SIMOBJECT_DATA *>(msg);
            if (data->dwRequestID == kReqRadar)
            {
                RadarKnobs knobs = {};
                const size_t offset = offsetof(SIMCONNECT_RECV_SIMOBJECT_DATA, dwData);
                memcpy(&knobs, &data->dwData, std::min(sizeof(knobs), size > offset ? size - offset : 0));
                const std::lock_guard lock(g_mutex);
                g_knobs = knobs;
                g_have_knobs = true;
                log_radar_knobs();
                break;
            }
            if (data->dwRequestID == kReqShadow)
            {
                ShadowVars v = {};
                const size_t offset = offsetof(SIMCONNECT_RECV_SIMOBJECT_DATA, dwData);
                const size_t got = size > offset ? size - offset : 0;
                memcpy(&v, &data->dwData, std::min(sizeof(v), got));
                const std::lock_guard lock(g_mutex);
                if (got >= sizeof(double))
                    g_heading_rad = v.heading_rad;
                if (got >= sizeof(v))  // all of it, or no shadow
                    update_shadow(v);
                else
                    g_shadow.valid = false;
                break;
            }
            if (data->dwRequestID != kReqAircraft)
                break;
            // Copy only what arrived: a SimVar the sim rejects shortens the data.
            AircraftVars v = { 0.0, 12.0 * 3600.0 };
            const size_t offset = offsetof(SIMCONNECT_RECV_SIMOBJECT_DATA, dwData);
            memcpy(&v, &data->dwData, std::min(sizeof(v), size > offset ? size - offset : 0));
            std::string line;
            {
                const std::lock_guard lock(g_mutex);
                // Terrain under the aircraft, smoothed at cruise so valleys and peaks
                // don't move the layer, but taken as is near the ground and after a
                // jump (new flight, slew, teleport): the menu sits at sea level, and
                // easing from there put Esfahan's layer 1 km underground.
                const double ground_km = v.ground_m * 0.001;
                const bool jumped = g_have_ground && (std::abs(v.lat - g_last_lat) > 0.3 || std::abs(v.lon - g_last_lon) > 0.3);
                const bool near_ground = v.agl_m < 300.0;
                if (jumped)
                {
                    log("the aircraft moved %.1f, %.1f deg at once: cloud anchor stations cleared", v.lat - g_last_lat,
                        v.lon - g_last_lon);
                    g_stations.restart();
                    g_jumped = true;
                }
                const double before = g_inputs.ground_km;
                g_inputs.ground_km = g_have_ground && !jumped && !near_ground
                    ? g_inputs.ground_km + (ground_km - g_inputs.ground_km) * (10.0 / kGroundSmoothingSeconds)
                    : ground_km;
                g_have_ground = true;
                g_last_lat = v.lat;
                g_last_lon = v.lon;
                g_inputs.local_hour = v.local_time_s / 3600.0;
                if (g_inputs.metar.valid)
                {
                    line = recompute();
                    // Log every 10 min (the day cycle moves the layer) and when the terrain moved it.
                    if (GetTickCount64() - last_logged > 600000 || std::abs(g_inputs.ground_km - before) > 0.2)
                    {
                        last_logged = GetTickCount64();
                        log("%s", line.c_str());
                    }
                }
            }
            break;
        }
        case SIMCONNECT_RECV_ID_SYSTEM_STATE:
        {
            const auto *state = static_cast<SIMCONNECT_RECV_SYSTEM_STATE *>(msg);
            if (state->dwRequestID == kReqAircraftPath)
            {
                const std::lock_guard lock(g_mutex);
                set_aircraft(state->szString);
            }
            break;
        }
        case SIMCONNECT_RECV_ID_EVENT_FILENAME:
        {
            const auto *event = static_cast<SIMCONNECT_RECV_EVENT_FILENAME *>(msg);
            if (event->uEventID == kEvtAircraftLoaded)
            {
                const std::lock_guard lock(g_mutex);
                set_aircraft(event->szFileName);
            }
            break;
        }
        case SIMCONNECT_RECV_ID_FACILITY_DATA:
        {
            const auto *data = static_cast<SIMCONNECT_RECV_FACILITY_DATA *>(msg);
            const bool anchor_station = data->UserRequestId >= kReqStation0 &&
                                        data->UserRequestId < kReqStation0 + kStationRequests;
            if ((data->UserRequestId != kReqAirport && !anchor_station) || data->Type != SIMCONNECT_FACILITY_DATA_AIRPORT)
                break;
            double fields[3];  // the definition's fields: latitude, longitude (deg), altitude (m)
            memcpy(fields, &data->Data, sizeof(fields));
            const std::lock_guard lock(g_mutex);
            if (anchor_station)
            {
                g_stations.on_position(g_station_requests[data->UserRequestId - kReqStation0], fields[0], fields[1],
                                       fields[2], now_s());
                break;
            }
            g_station_lat = fields[0];
            g_station_lon = fields[1];
            g_station_elevation_m = fields[2];
            log_freezing_level();
            g_stations.on_position(g_station, fields[0], fields[1], fields[2], now_s());
            break;
        }
        case SIMCONNECT_RECV_ID_COMM_BUS:
        {
            const auto *bus = static_cast<SIMCONNECT_RECV_COMM_BUS *>(msg);
            if (bus->uEventID != kEvtMetar)
                break;
            partial += bus->rgData;  // large messages arrive in chunks
            if (bus->dwEntryNumber + 1 < bus->dwOutOf)
                break;
            haze::Metar metar;
            try
            {
                metar = haze::parse_metar_json(partial);
            }
            catch (const std::exception &e)
            {
                log("unreadable METAR message (%s): %.200s", e.what(), partial.c_str());
            }
            partial.clear();
            if (!metar.valid)
                break;
            const std::lock_guard lock(g_mutex);
            if (surveyed(metar))  // only for the cloud anchoring
            {
                anchor_metar(sim, metar, metar.request_lat, metar.request_lon, false);
                break;
            }
            g_inputs.metar = metar;
            // where the METAR was requested (the app's message says; else the aircraft now)
            g_metar_lat = std::isfinite(metar.request_lat) ? metar.request_lat : g_last_lat;
            g_metar_lon = std::isfinite(metar.request_lon) ? metar.request_lon : g_last_lon;
            if (metar.icao != g_station)  // new station: ask for its position and elevation
            {
                g_station = metar.icao;
                g_station_elevation_m = g_station_lat = g_station_lon = NAN;
                SimConnect_RequestFacilityData(sim, kDefAirport, kReqAirport, metar.icao.c_str());
            }
            log_freezing_level();
            anchor_metar(sim, metar, g_metar_lat, g_metar_lon, true);
            const std::string line = recompute();
            if (metar.raw != last_metar)
            {
                last_metar = metar.raw;
                last_logged = GetTickCount64();
                log("%s | %s", line.c_str(), metar.raw.c_str());
            }
            break;
        }
        case SIMCONNECT_RECV_ID_EXCEPTION:
        {
            const auto *ex = static_cast<SIMCONNECT_RECV_EXCEPTION *>(msg);
            log("SimConnect exception %lu (send id %lu, parameter %ld)", ex->dwException, ex->dwSendID,
                static_cast<long>(ex->dwIndex));
            break;
        }
        case SIMCONNECT_RECV_ID_QUIT:
            return false;
        default:
            break;
        }
    }
    return true;
}

void run_loop();

// Thread entry: nothing may escape a thread inside the sim's process.
void run()
{
    try
    {
        run_loop();
    }
    catch (const std::exception &e)
    {
        log("weather feed stopped by an error: %s", e.what());
    }
    catch (...)
    {
        log("weather feed stopped by an unknown error");
    }
    g_finished = true;
}

void run_loop()
{
    bool waiting_logged = false;
    while (!g_stop)
    {
        HANDLE sim = nullptr;
        if (FAILED(SimConnect_Open(&sim, "AtmosphereMod weather feed", nullptr, 0, nullptr, 0)))
        {
            if (!waiting_logged)
                log("waiting for SimConnect");
            waiting_logged = true;
            for (int i = 0; i < 50 && !g_stop; ++i)
                Sleep(100);
            continue;
        }
        waiting_logged = false;
        log("connected; waiting for METARs from the AtmosphereMod EFB app");
        for (const auto &v : kAircraftVars)
            SimConnect_AddToDataDefinition(sim, kDefAircraft, v.name, v.units);
        SimConnect_RequestDataOnSimObject(sim, kReqAircraft, kDefAircraft, SIMCONNECT_OBJECT_ID_USER,
                                          SIMCONNECT_PERIOD_SECOND, SIMCONNECT_DATA_REQUEST_FLAG_DEFAULT, 0, 9);
        SimConnect_SubscribeToCommBusEvent(sim, kEvtMetar, kMetarEvent);
        SimConnect_AddToFacilityDefinition(sim, kDefAirport, "OPEN AIRPORT");
        SimConnect_AddToFacilityDefinition(sim, kDefAirport, "LATITUDE");
        SimConnect_AddToFacilityDefinition(sim, kDefAirport, "LONGITUDE");
        SimConnect_AddToFacilityDefinition(sim, kDefAirport, "ALTITUDE");
        SimConnect_AddToFacilityDefinition(sim, kDefAirport, "CLOSE AIRPORT");
        {
            const std::lock_guard lock(g_mutex);
            g_station.clear();  // request the elevation again on this connection
            g_have_knobs = false;
            g_stations.restart();
        }
        // The radar panel knobs (sent when they change) and which aircraft is loaded.
        for (const char *var : kRadarKnobVars)
            SimConnect_AddToDataDefinition(sim, kDefRadar, var, "number");
        SimConnect_RequestDataOnSimObject(sim, kReqRadar, kDefRadar, SIMCONNECT_OBJECT_ID_USER,
                                          SIMCONNECT_PERIOD_VISUAL_FRAME, SIMCONNECT_DATA_REQUEST_FLAG_CHANGED);
        SimConnect_SubscribeToSystemEvent(sim, kEvtAircraftLoaded, "AircraftLoaded");
        SimConnect_RequestSystemState(sim, kReqAircraftPath, "AircraftLoaded");
        // The aircraft's shadow on clouds: attitude, span, UTC and camera, every frame.
        for (const auto &v : kShadowVars)
            SimConnect_AddToDataDefinition(sim, kDefShadow, v.name, v.units);
        SimConnect_RequestDataOnSimObject(sim, kReqShadow, kDefShadow, SIMCONNECT_OBJECT_ID_USER,
                                          SIMCONNECT_PERIOD_VISUAL_FRAME, SIMCONNECT_DATA_REQUEST_FLAG_DEFAULT);
        {
            const std::lock_guard lock(g_mutex);
            g_shadow.valid = false;
            g_logged_camera = -1.0;
        }

        std::string partial, last_metar;
        ULONGLONG last_logged = 0, next_request = GetTickCount64() + 5000, next_survey = next_request + 5000;
        bool connected = true;
        while (!g_stop && connected)
        {
            connected = dispatch(sim, partial, last_metar, last_logged);
            if (g_jumped.exchange(false))
                next_request = next_survey = GetTickCount64();
            if (GetTickCount64() >= next_request)
            {
                request_metar(sim);
                next_request = GetTickCount64() + 60000;
            }
            if (GetTickCount64() >= next_survey)
            {
                survey(sim);
                next_survey = GetTickCount64() + kSurveyIntervalMs;
            }
            Sleep(200);
        }
        SimConnect_Close(sim);
        {
            const std::lock_guard lock(g_mutex);
            g_shadow.valid = false;
        }
        log(connected ? "stopped" : "the sim quit");
        if (!connected)
            break;
    }
}
} // namespace

void start(const std::filesystem::path &log_dir)
{
    if (!g_finished)
        return;
    g_log = _wfsopen((log_dir / "weather.log").c_str(), L"a", _SH_DENYNO);  // readable while running
    log("weather feed starting");
    g_stop = false;
    g_finished = false;
    g_thread = std::thread(run);
}

void stop()
{
    if (!g_thread.joinable())
        return;
    g_stop = true;
    // Joining could deadlock if this runs under the loader lock (DLL unload),
    // so wait a bounded time and leave the thread to the process otherwise.
    for (int i = 0; i < 20 && !g_finished; ++i)
        Sleep(50);
    if (g_finished)
        g_thread.join();
    else
        g_thread.detach();
}

AircraftShadow aircraft_shadow()
{
    const std::lock_guard lock(g_mutex);
    return g_shadow;
}

AircraftRadar aircraft_radar()
{
    const std::lock_guard lock(g_mutex);
    AircraftRadar r;
    r.valid = g_pmdg737 && g_have_knobs;
    r.tilt_position = g_knobs.tilt_l;
    r.tilt_deg = static_cast<float>(knob_tilt_deg(g_knobs.tilt_l));
    return r;
}

static_assert(kAnchorTableBytes == anchor::kTableBytes);

size_t cloud_anchor_table(std::array<uint8_t, kAnchorTableBytes> &out)
{
    const std::lock_guard lock(g_mutex);
    const double now = now_s();
    g_stations.tick(g_have_ground ? g_last_lat : NAN, g_have_ground ? g_last_lon : NAN, now);
    g_stations.table(now, out.data());
    return g_stations.in_use();
}

float freezing_level_m()
{
    const std::lock_guard lock(g_mutex);
    return static_cast<float>(freezing_level_locked());
}

std::array<float, kTunableCount> apply(const std::array<float, kTunableCount> &ini, float dt_seconds)
{
    std::array<float, std::size(kDriven)> target;
    {
        const std::lock_guard lock(g_mutex);
        if (!g_have_target)
            return ini;
        target = g_target;
    }
    const float k = g_smoothed_valid ? 1.0f - std::exp(-std::max(dt_seconds, 0.0f) / kSmoothingSeconds) : 1.0f;
    for (size_t i = 0; i < target.size(); ++i)
        g_smoothed[i] += (target[i] - g_smoothed[i]) * k;
    g_smoothed_valid = true;

    const float mix = std::clamp(ini[13], 0.0f, 1.0f);
    std::array<float, kTunableCount> out = ini;
    for (size_t i = 0; i < std::size(kDriven); ++i)
    {
        float weather = g_smoothed[i];
        // T9 (g, the forward glare) stays the user's: the weather adds its
        // type and humidity offsets to it instead of replacing it.
        if (kDriven[i] == 9 && ini[9] > 0.0f)
            weather = std::clamp(ini[9] + (weather - static_cast<float>(haze::kReferenceG)), 0.0f, 0.95f);
        out[kDriven[i]] = ini[kDriven[i]] + (weather - ini[kDriven[i]]) * mix;
    }
    return out;
}
} // namespace weather
