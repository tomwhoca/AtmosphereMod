#include "activesky.h"

#include <windows.h>

#include <winhttp.h>

#include <algorithm>
#include <cctype>
#include <cstdarg>
#include <cstdio>

namespace activesky
{
namespace
{
constexpr INTERNET_PORT kPort = 19285;
constexpr uint64_t kPollIntervalMs = 60000;

struct Reply
{
    int status = 0;  // the HTTP status; 0 when Active Sky didn't answer
    std::string body;
};

struct Handle
{
    HINTERNET h = nullptr;
    ~Handle()
    {
        if (h)
            WinHttpCloseHandle(h);
    }
};

// Runs on the weather thread, so the timeouts are short: it answers in a few ms.
Reply get(const std::wstring &path)
{
    Reply reply;
    Handle session{ WinHttpOpen(L"AtmosphereMod", WINHTTP_ACCESS_TYPE_NO_PROXY, WINHTTP_NO_PROXY_NAME,
                                WINHTTP_NO_PROXY_BYPASS, 0) };
    if (!session.h)
        return reply;
    WinHttpSetTimeouts(session.h, 500, 500, 500, 1000);
    Handle connection{ WinHttpConnect(session.h, L"127.0.0.1", kPort, 0) };
    if (!connection.h)
        return reply;
    const std::wstring object = L"/ActiveSky/API/" + path;
    Handle request{ WinHttpOpenRequest(connection.h, L"GET", object.c_str(), nullptr, WINHTTP_NO_REFERER,
                                       WINHTTP_DEFAULT_ACCEPT_TYPES, 0) };
    if (!request.h || !WinHttpSendRequest(request.h, WINHTTP_NO_ADDITIONAL_HEADERS, 0, WINHTTP_NO_REQUEST_DATA, 0, 0, 0) ||
        !WinHttpReceiveResponse(request.h, nullptr))
        return reply;
    DWORD status = 0, size = sizeof(status);
    if (!WinHttpQueryHeaders(request.h, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER, WINHTTP_HEADER_NAME_BY_INDEX,
                             &status, &size, WINHTTP_NO_HEADER_INDEX))
        return reply;
    std::string body;
    for (;;)
    {
        DWORD available = 0;
        if (!WinHttpQueryDataAvailable(request.h, &available))
            return reply;  // cut off: not the whole report
        if (available == 0)
            break;
        const size_t at = body.size();
        body.resize(at + available);
        DWORD read = 0;
        if (!WinHttpReadData(request.h, body.data() + at, available, &read) || body.size() > 1 << 20)
            return reply;
        body.resize(at + read);
    }
    reply.status = static_cast<int>(status);
    reply.body = std::move(body);
    return reply;
}

std::string format(const char *fmt, ...)
{
    char text[512];
    va_list args;
    va_start(args, fmt);
    vsnprintf(text, sizeof(text), fmt, args);
    va_end(args);
    return text;
}
} // namespace

// It answers "No sim connection" (503) for the aircraft's conditions while it
// runs without feeding the sim.
bool ActiveSky::poll()
{
    const uint64_t now = GetTickCount64();
    if (now < next_poll_ms_)
        return false;
    next_poll_ms_ = now + kPollIntervalMs;
    const Reply r = get(L"GetCurrentConditions");
    const State state = r.status == 200 ? State::injecting : r.status ? State::idle : State::absent;
    if (state == state_)
        return false;
    state_ = state;
    if (state == State::injecting)
        log_(format("Active Sky feeds the sim (%.100s): its depicted METARs replace the sim's",
                    get(L"GetMode").body.c_str()));
    else if (state == State::idle)
        log_(format("Active Sky runs but doesn't feed the sim (%d %.100s): the sim's METARs", r.status, r.body.c_str()));
    else
        log_("no Active Sky: the sim's METARs");
    return true;
}

std::optional<haze::Metar> ActiveSky::depict(const haze::Metar &metar)
{
    Reply r;
    if (metar.icao.size() == 4 &&
        std::all_of(metar.icao.begin(), metar.icao.end(), [](unsigned char c) { return std::isalnum(c); }))
        r = get(L"GetMetarInfoAt?ICAO=" + std::wstring(metar.icao.begin(), metar.icao.end()));
    if (r.status == 0)
        next_poll_ms_ = 0;  // gone? the next poll checks
    haze::Metar m;
    try
    {
        if (r.status == 200)
            m = haze::parse_metar_raw(r.body);
    }
    catch (const std::exception &e)
    {
        log_(std::string("unreadable Active Sky METAR (") + e.what() + "): " + r.body.substr(0, 200));
    }
    if (!m.valid || m.icao != metar.icao)
    {
        const std::string key = metar.icao + " " + r.body;
        if (key != undepicted_logged_)
            log_(format("no depicted METAR from Active Sky for %s (%d %.100s): ignored", metar.icao.c_str(), r.status,
                        r.body.c_str()));
        undepicted_logged_ = key;
        return std::nullopt;
    }
    m.depicted = true;
    m.request_lat = metar.request_lat;
    m.request_lon = metar.request_lon;
    return m;
}
} // namespace activesky
