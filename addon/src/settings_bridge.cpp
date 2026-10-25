// Settings from the EFB app (settings_bridge.h).

#include "settings_bridge.h"

#include <windows.h>

#include <algorithm>
#include <charconv>
#include <cmath>
#include <fstream>
#include <iterator>
#include <mutex>
#include <regex>
#include <utility>

namespace settings
{
namespace
{
std::mutex g_mutex;  // guards the fields below
Requests g_requests;
std::string g_state;
uint64_t g_state_version = 0, g_sent_version = 0;
bool g_asked = false;  // the app asked for the state
} // namespace

void on_message(const std::string &json)
{
    static const std::regex get_re("\"get\"\\s*:\\s*true");
    static const std::regex reload_re("\"reload\"\\s*:\\s*true");
    static const std::regex enabled_re("\"enabled\"\\s*:\\s*(true|false)");
    static const std::regex set_re("\"set\"\\s*:\\s*\\{([^}]*)\\}");
    static const std::regex value_re("\"([TC])([0-9]{1,2})\"\\s*:\\s*(-?[0-9]*\\.?[0-9]+(?:[eE][-+]?[0-9]+)?)");
    Requests in;
    std::smatch m;
    if (std::regex_search(json, m, enabled_re))
        in.enabled = m[1] == "true";
    if (std::regex_search(json, m, set_re))
    {
        const std::string body = m[1];
        for (auto it = std::sregex_iterator(body.begin(), body.end(), value_re); it != std::sregex_iterator(); ++it)
        {
            const float value = std::strtof((*it)[3].str().c_str(), nullptr);
            if (std::isfinite(value))
                in.set.push_back({ (*it)[1].str()[0], static_cast<unsigned>(std::stoul((*it)[2].str())), value });
        }
    }
    const bool asked = std::regex_search(json, get_re);
    in.reload = std::regex_search(json, reload_re);

    const std::lock_guard lock(g_mutex);
    g_requests.set.insert(g_requests.set.end(), in.set.begin(), in.set.end());
    if (in.enabled)
        g_requests.enabled = in.enabled;
    g_requests.reload = g_requests.reload || in.reload;
    g_asked = g_asked || asked;
}

Requests take()
{
    const std::lock_guard lock(g_mutex);
    return std::exchange(g_requests, {});
}

void publish(std::string state_json)
{
    const std::lock_guard lock(g_mutex);
    if (state_json != g_state)
    {
        g_state = std::move(state_json);
        ++g_state_version;
    }
}

std::optional<std::string> to_send()
{
    const std::lock_guard lock(g_mutex);
    if (g_state.empty() || (!g_asked && g_state_version == g_sent_version))
        return std::nullopt;
    g_asked = false;
    g_sent_version = g_state_version;
    return g_state;
}

std::string format(float value)
{
    char buf[32];
    const auto result = std::to_chars(buf, buf + sizeof(buf), value);
    return std::string(buf, result.ptr);
}

bool save(const std::filesystem::path &file, const std::vector<Change> &values)
{
    std::vector<Change> unique;  // one per key, the last value asked for
    for (const Change &v : values)
    {
        const auto same = [&](const Change &u) { return u.prefix == v.prefix && u.index == v.index; };
        if (const auto it = std::find_if(unique.begin(), unique.end(), same); it != unique.end())
            it->value = v.value;
        else
            unique.push_back(v);
    }
    std::string text;
    {
        std::ifstream in(file, std::ios::binary);
        text.assign(std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>());
    }
    const std::string newline = text.find("\r\n") != std::string::npos ? "\r\n" : "\n";
    // "<prefix><index> = <value>", as the add-on reads it (load_tunables)
    static const std::regex key_re("^[ \\t]*([TRC])([0-9]+)[ \\t]*=[ \\t]*([^ \\t\\r\\n;]*)");
    std::vector<bool> written(unique.size(), false);
    std::string out;
    out.reserve(text.size() + 64);
    for (size_t pos = 0; pos < text.size();)
    {
        const size_t end = std::min(text.find('\n', pos), text.size() - 1) + 1;
        std::string line = text.substr(pos, end - pos);
        pos = end;
        std::smatch m;
        if (std::regex_search(line, m, key_re))
        {
            const char prefix = m[1].str()[0];
            const unsigned index = static_cast<unsigned>(std::stoul(m[2].str()));
            for (size_t i = 0; i < unique.size(); ++i)
            {
                if (unique[i].prefix == prefix && unique[i].index == index)
                {
                    line.replace(m.position(3), m.length(3), format(unique[i].value));
                    written[i] = true;
                }
            }
        }
        out += line;
    }
    for (size_t i = 0; i < unique.size(); ++i)
    {
        if (written[i])
            continue;
        if (!out.empty() && out.back() != '\n')
            out += newline;
        out += unique[i].prefix + std::to_string(unique[i].index) + " = " + format(unique[i].value) + newline;
    }

    std::filesystem::path temp = file;
    temp += L".efb-tmp";
    {
        std::ofstream o(temp, std::ios::binary | std::ios::trunc);
        o << out;
        if (!o)
            return false;
    }
    return MoveFileExW(temp.c_str(), file.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH) != 0;
}
} // namespace settings
