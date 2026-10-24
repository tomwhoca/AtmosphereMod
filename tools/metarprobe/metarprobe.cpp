// metarprobe: what weather data does MSFS 2024 give an external program?
//
//   metarprobe [ICAO]
//
// Run it while flying (start it before or after the sim; it waits and
// reconnects). It logs:
//  - every 10 s, the ambient weather SimVars at the user aircraft;
//  - the sim's own METARs, sent by the AtmosphereMod EFB app
//    (efb/PackageSources/AtmoWeather) over CommBus. The app posts one every
//    minute; the probe also requests one every 60 s, for the aircraft
//    position and, if given, for ICAO.
//
// (SimConnect's own WeatherRequestObservation* functions are declared in
// SimConnect.h but return WEATHER_UNABLE_TO_GET_OBSERVATION in MSFS 2024:
// 192 of 192 requests failed in a 32-minute live-weather flight.)
//
// Output: build/logs/metarprobe.log (summary) and build/logs/metar_json.log
// (`metarprobe --parse FILE` re-summarises a recorded metar_json.log offline)
// (each METAR message as received). Stop with Ctrl+C; it also ends when the
// sim quits.

#include <windows.h>
#pragma warning(push, 0)  // the SDK header trips /W4
#include <SimConnect.h>
#pragma warning(pop)

#include <share.h>

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdarg>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <map>
#include <regex>
#include <string>

#include "cloud_anchor.h"  // addon/src: METAR -> cloud anchor layers, shared with the add-on
#include "haze_mapping.h"  // addon/src: METAR -> haze tunables, shared with the add-on

namespace fs = std::filesystem;

namespace
{
enum Definition : DWORD { kDefWeather = 1 };
enum Request : DWORD { kReqWeather = 1 };
enum Event : DWORD { kEvtWeatherMode = 1, kEvtMetar = 10, kEvtStatus = 11 };

// CommBus events, shared with efb/PackageSources/AtmoWeather/src/AtmoWeather.tsx.
const char *const kMetarEvent = "AtmosphereMod.Metar";
const char *const kStatusEvent = "AtmosphereMod.Status";
const char *const kRequestEvent = "AtmosphereMod.Request";

// SimVars at the user aircraft, in the order they are added to kDefWeather.
struct WeatherVars
{
    double latitude, longitude, altitude_m, ground_m;
    double visibility_m, temperature_c, pressure_inhg, sea_level_mb;
    double precip_state, precip_rate_mm, in_cloud, wind_dir, wind_kt, local_time_s;
};
const struct { const char *name, *units; } kVars[] = {
    { "PLANE LATITUDE", "degrees" },        { "PLANE LONGITUDE", "degrees" },
    { "PLANE ALTITUDE", "meters" },         { "GROUND ALTITUDE", "meters" },
    { "AMBIENT VISIBILITY", "meters" },     { "AMBIENT TEMPERATURE", "celsius" },
    { "AMBIENT PRESSURE", "inHg" },         { "SEA LEVEL PRESSURE", "millibars" },
    { "AMBIENT PRECIP STATE", "mask" },     { "AMBIENT PRECIP RATE", "millimeters of water" },
    { "AMBIENT IN CLOUD", "bool" },         { "AMBIENT WIND DIRECTION", "degrees" },
    { "AMBIENT WIND VELOCITY", "knots" },  { "LOCAL TIME", "seconds" },
};
static_assert(sizeof(WeatherVars) == sizeof(kVars) / sizeof(kVars[0]) * sizeof(double));

// SIMCONNECT_EXCEPTION, in the order of SimConnect.h.
const char *const kExceptions[] = {
    "NONE", "ERROR", "SIZE_MISMATCH", "UNRECOGNIZED_ID", "UNOPENED", "VERSION_MISMATCH", "TOO_MANY_GROUPS",
    "NAME_UNRECOGNIZED", "TOO_MANY_EVENT_NAMES", "EVENT_ID_DUPLICATE", "TOO_MANY_MAPS", "TOO_MANY_OBJECTS",
    "TOO_MANY_REQUESTS", "WEATHER_INVALID_PORT", "WEATHER_INVALID_METAR", "WEATHER_UNABLE_TO_GET_OBSERVATION",
    "WEATHER_UNABLE_TO_CREATE_STATION", "WEATHER_UNABLE_TO_REMOVE_STATION", "INVALID_DATA_TYPE", "INVALID_DATA_SIZE",
    "DATA_ERROR", "INVALID_ARRAY", "CREATE_OBJECT_FAILED", "LOAD_FLIGHTPLAN_FAILED",
    "OPERATION_INVALID_FOR_OBJECT_TYPE", "ILLEGAL_OPERATION", "ALREADY_SUBSCRIBED", "INVALID_ENUM",
    "DEFINITION_ERROR", "DUPLICATE_ID", "DATUM_ID", "OUT_OF_BOUNDS", "ALREADY_CREATED",
    "OBJECT_OUTSIDE_REALITY_BUBBLE", "OBJECT_CONTAINER", "OBJECT_AI", "OBJECT_ATC", "OBJECT_SCHEDULE",
    "JETWAY_DATA", "ACTION_NOT_FOUND", "NOT_AN_ACTION", "INCORRECT_ACTION_PARAMS", "GET_INPUT_EVENT_FAILED",
    "SET_INPUT_EVENT_FAILED", "EVENT_NAME_RESERVED", "INTERNAL", "CAMERA_API",
};

// MetarPhenomenonType in the SDK's msfssdk.d.ts.
const char *const kPhenomena[] = {
    "none", "mist BR", "duststorm DS", "dust DU", "drizzle DZ", "funnel cloud FC", "fog FG", "smoke FU",
    "hail GR", "small hail GS", "haze HZ", "ice crystals IC", "ice pellets PL", "dust/sand whirls PO",
    "spray PY", "rain RA", "sand SA", "snow grains SG", "shower SH", "snow SN", "squalls SQ", "sandstorm SS",
    "unknown precip UP", "volcanic ash VA",
};

FILE *g_log = nullptr;
FILE *g_json = nullptr;
std::atomic<bool> g_stop{false};
std::map<DWORD, std::string> g_sent;      // SimConnect packet id -> what we sent
std::map<DWORD, std::string> g_partial;   // CommBus event -> chunks received so far
std::string g_icao;
haze::Inputs g_inputs;  // latest ground altitude and local time, for the haze mapping
bool g_ask_station = false;

void log(const char *fmt, ...)
{
    SYSTEMTIME t;
    GetLocalTime(&t);
    char line[2400];
    va_list args;
    va_start(args, fmt);
    vsnprintf(line, sizeof(line), fmt, args);
    va_end(args);
    for (FILE *f : { stdout, g_log })
        if (f)
        {
            fprintf(f, "%02u:%02u:%02u %s\n", t.wHour, t.wMinute, t.wSecond, line);
            fflush(f);
        }
}

// Remember what each packet was, so an exception can name the call.
void sent(HANDLE sim, const std::string &what, HRESULT hr)
{
    DWORD id = 0;
    SimConnect_GetLastSentPacketID(sim, &id);
    g_sent[id] = what;
    if (FAILED(hr))
        log("%s: call failed locally (0x%08lx)", what.c_str(), static_cast<unsigned long>(hr));
}

void request_metar(HANDLE sim)
{
    // Alternate the aircraft position and the given station.
    const std::string body = g_ask_station && !g_icao.empty() ? "{\"ident\":\"" + g_icao + "\"}" : "{}";
    g_ask_station = !g_ask_station;
    sent(sim, std::string("CallCommBusEvent(") + kRequestEvent + ", " + body + ")",
         SimConnect_CallCommBusEvent(sim, kRequestEvent, SIMCONNECT_COMM_BUS_BROADCAST_TO_JS,
                                     static_cast<DWORD>(body.size() + 1), body.c_str()));
}

std::string match(const std::string &text, const char *pattern)
{
    std::smatch m;
    return std::regex_search(text, m, std::regex(pattern)) ? m[1].str() : std::string();
}

// One line per METAR: station, the raw string, humidity and phenomena.
void summarise_metar(const std::string &json)
{
    if (g_json)
    {
        fprintf(g_json, "%s\n", json.c_str());
        fflush(g_json);
    }
    if (json.find("\"metar\":null") != std::string::npos)
    {
        log("METAR: none returned (%s)", match(json, "\"error\":\"([^\"]*)\"").c_str());
        return;
    }
    const std::string metar = match(json, "\"metarString\":\"([^\"]*)\"");
    const std::string temp = match(json, "\"temp\":(-?[0-9.]+)");
    const std::string dew = match(json, "\"dew\":(-?[0-9.]+)");
    std::string humidity = "?";
    if (!temp.empty() && !dew.empty())
    {
        // Magnus formula over water.
        const auto es = [](double c) { return std::exp(17.625 * c / (243.04 + c)); };
        char buf[16];
        snprintf(buf, sizeof(buf), "%.0f%%", 100.0 * es(std::stod(dew)) / es(std::stod(temp)));
        humidity = buf;
    }
    // Each phenomenon object holds a list of types ("phenom":[15]), an intensity
    // (-1 light, 1 heavy) and flags such as "ts" (thunderstorm), as sent by the sim.
    std::string phenomena;
    const std::regex object("\\{\"__Type\":\"Phenom\"[^}]*\\}");
    for (auto it = std::sregex_iterator(json.begin(), json.end(), object); it != std::sregex_iterator(); ++it)
    {
        const std::string obj = it->str();
        const std::string intensity = match(obj, "\"intensity\":(-?[0-9])");
        std::string item = intensity == "-1" ? "light " : intensity == "1" ? "heavy " : "";
        if (obj.find("\"ts\":true") != std::string::npos)
            item += "thunderstorm TS + ";
        const std::string list = match(obj, "\"phenom\":\\[([0-9,]*)\\]");
        for (size_t start = 0; start < list.size();)
        {
            const size_t end = std::min(list.find(',', start), list.size());
            const size_t p = std::stoul(list.substr(start, end - start));
            item += std::string(start ? "/" : "") + (p < std::size(kPhenomena) ? kPhenomena[p] : "?");
            start = end + 1;
        }
        phenomena += std::string(phenomena.empty() ? "" : ", ") + item;
    }
    log("METAR %s | RH %s | vis %s (units %s) | phenomena: %s | %s", match(json, "\"icao\":\"([^\"]*)\"").c_str(),
        humidity.c_str(), match(json, "\"vis\":([0-9.]+)").c_str(), match(json, "\"visUnits\":(-?[0-9]+)").c_str(),
        phenomena.empty() ? "none" : phenomena.c_str(), metar.c_str());

    g_inputs.metar = haze::parse_metar_json(json);
    if (g_inputs.metar.valid)
    {
        log("  haze: %s", haze::describe(g_inputs, haze::derive(g_inputs)).c_str());
        // the station's position isn't in the message: the request position, elevation 0
        const anchor::Anchor a = anchor::from_metar(g_inputs.metar, haze::detail::number(json, "\"lat\":(-?[0-9.]+)"),
                                                    haze::detail::number(json, "\"lon\":(-?[0-9.]+)"), 0.0,
                                                    g_inputs.local_hour);
        log("  %s", a.valid ? a.describe().c_str() : "cloud anchor: off (no sky condition)");
    }
}

void dispatch(SIMCONNECT_RECV *msg)
{
    switch (msg->dwID)
    {
    case SIMCONNECT_RECV_ID_OPEN:
    {
        const auto *open = static_cast<SIMCONNECT_RECV_OPEN *>(msg);
        log("connected to %s %lu.%lu (SimConnect %lu.%lu)", open->szApplicationName, open->dwApplicationVersionMajor,
            open->dwApplicationVersionMinor, open->dwSimConnectVersionMajor, open->dwSimConnectVersionMinor);
        break;
    }
    case SIMCONNECT_RECV_ID_SIMOBJECT_DATA:
    {
        const auto *data = static_cast<SIMCONNECT_RECV_SIMOBJECT_DATA *>(msg);
        if (data->dwRequestID != kReqWeather)
            break;
        WeatherVars v;
        memcpy(&v, &data->dwData, sizeof(v));
        g_inputs.ground_km = v.ground_m * 0.001;
        g_inputs.local_hour = v.local_time_s / 3600.0;
        log("aircraft %.4f %.4f alt %.0f m (ground %.0f m) | visibility %.0f m | %.1f C | %.2f inHg (QNH %.1f mb) | "
            "precip state %.0f rate %.2f mm | in cloud %.0f | wind %03.0f/%.0f kt | local %.2f h",
            v.latitude, v.longitude, v.altitude_m, v.ground_m, v.visibility_m, v.temperature_c, v.pressure_inhg,
            v.sea_level_mb, v.precip_state, v.precip_rate_mm, v.in_cloud, v.wind_dir, v.wind_kt, g_inputs.local_hour);
        break;
    }
    case SIMCONNECT_RECV_ID_COMM_BUS:
    {
        // Large messages arrive in chunks: dwEntryNumber of dwOutOf.
        const auto *bus = static_cast<SIMCONNECT_RECV_COMM_BUS *>(msg);
        std::string &text = g_partial[bus->uEventID];
        text += bus->rgData;
        if (bus->dwEntryNumber + 1 < bus->dwOutOf)
            break;
        const std::string complete = std::move(text);
        g_partial.erase(bus->uEventID);
        if (bus->uEventID == kEvtMetar)
            summarise_metar(complete);
        else
            log("EFB app status: %s", complete.c_str());
        break;
    }
    case SIMCONNECT_RECV_ID_EXCEPTION:
    {
        const auto *ex = static_cast<SIMCONNECT_RECV_EXCEPTION *>(msg);
        const char *name = ex->dwException < std::size(kExceptions) ? kExceptions[ex->dwException] : "?";
        const auto it = g_sent.find(ex->dwSendID);
        log("EXCEPTION %s (%lu) for %s, parameter %ld", name, ex->dwException,
            it != g_sent.end() ? it->second.c_str() : "unknown call", static_cast<long>(ex->dwIndex));
        break;
    }
    case SIMCONNECT_RECV_ID_EVENT_WEATHER_MODE:
    {
        const auto *ev = static_cast<SIMCONNECT_RECV_EVENT_WEATHER_MODE *>(msg);
        static const char *const modes[] = { "theme", "real-world weather", "custom", "global" };
        log("weather mode: %s", ev->dwData < 4 ? modes[ev->dwData] : "?");
        break;
    }
    case SIMCONNECT_RECV_ID_QUIT:
        log("the sim quit");
        g_stop = true;
        break;
    default:
        break;
    }
}

BOOL WINAPI on_ctrl(DWORD)
{
    g_stop = true;
    return TRUE;
}
} // namespace

int main(int argc, char **argv)
{
    if (argc > 2 && strcmp(argv[1], "--parse") == 0)
    {
        // Re-summarise recorded messages (a metar_json.log) without the sim.
        std::ifstream file(argv[2]);
        std::string line;
        while (std::getline(file, line))
            if (!line.empty())
                summarise_metar(line);
        return 0;
    }
    if (argc > 1)
        g_icao = argv[1];
    fs::path exe(argv[0]);
    fs::path logs = fs::absolute(exe).parent_path().parent_path() / "logs";  // build/metarprobe -> build/logs
    fs::create_directories(logs);
    g_log = _wfsopen((logs / "metarprobe.log").c_str(), L"a", _SH_DENYNO);  // readable while running
    g_json = _wfsopen((logs / "metar_json.log").c_str(), L"a", _SH_DENYNO);
    SetConsoleCtrlHandler(on_ctrl, TRUE);
    log("metarprobe started%s%s; log: %s", g_icao.empty() ? "" : ", station ", g_icao.c_str(),
        (logs / "metarprobe.log").string().c_str());

    bool waiting_logged = false;
    while (!g_stop)
    {
        HANDLE sim = nullptr;
        if (FAILED(SimConnect_Open(&sim, "AtmosphereMod metarprobe", nullptr, 0, nullptr, 0)))
        {
            if (!waiting_logged)
                log("waiting for the sim...");
            waiting_logged = true;
            Sleep(2000);
            continue;
        }
        waiting_logged = false;
        g_sent.clear();
        g_partial.clear();

        for (const auto &v : kVars)
            sent(sim, std::string("AddToDataDefinition(") + v.name + ", " + v.units + ")",
                 SimConnect_AddToDataDefinition(sim, kDefWeather, v.name, v.units));
        sent(sim, "RequestDataOnSimObject(weather)",
             SimConnect_RequestDataOnSimObject(sim, kReqWeather, kDefWeather, SIMCONNECT_OBJECT_ID_USER,
                                               SIMCONNECT_PERIOD_SECOND, SIMCONNECT_DATA_REQUEST_FLAG_DEFAULT,
                                               0, 10));
        sent(sim, "SubscribeToSystemEvent(WeatherModeChanged)",
             SimConnect_SubscribeToSystemEvent(sim, kEvtWeatherMode, "WeatherModeChanged"));
        sent(sim, std::string("SubscribeToCommBusEvent(") + kMetarEvent + ")",
             SimConnect_SubscribeToCommBusEvent(sim, kEvtMetar, kMetarEvent));
        sent(sim, std::string("SubscribeToCommBusEvent(") + kStatusEvent + ")",
             SimConnect_SubscribeToCommBusEvent(sim, kEvtStatus, kStatusEvent));

        ULONGLONG next_request = GetTickCount64() + 5000;
        while (!g_stop)
        {
            SIMCONNECT_RECV *msg = nullptr;
            DWORD size = 0;
            while (SUCCEEDED(SimConnect_GetNextDispatch(sim, &msg, &size)))
                dispatch(msg);
            if (GetTickCount64() >= next_request)
            {
                request_metar(sim);
                next_request = GetTickCount64() + 60000;
            }
            Sleep(100);
        }
        SimConnect_Close(sim);
    }
    log("metarprobe stopped");
    for (FILE *f : { g_log, g_json })
        if (f)
            fclose(f);
    return 0;
}
