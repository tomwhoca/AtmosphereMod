// Settings from the EFB app (settings_bridge.h).

#include "settings_bridge.h"

#include <windows.h>

#include <algorithm>
#include <bit>
#include <cctype>
#include <charconv>
#include <cmath>
#include <fstream>
#include <iterator>
#include <map>
#include <mutex>
#include <regex>
#include <string_view>
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
    // (a name with a quote or a backslash in it isn't one a preset may have: such a request is ignored)
    static const std::regex save_re("\"savePreset\"\\s*:\\s*\"([^\"\\\\]*)\"");
    static const std::regex load_re("\"loadPreset\"\\s*:\\s*\"([^\"\\\\]*)\"");
    static const std::regex delete_re("\"deletePreset\"\\s*:\\s*\"([^\"\\\\]*)\"");
    for (const auto &[re, name] : {std::pair{&save_re, &in.save_preset}, std::pair{&load_re, &in.load_preset},
                                   std::pair{&delete_re, &in.delete_preset}})
        if (std::regex_search(json, m, *re))
            *name = m[1].str();
    static const std::regex discard_re("\"discard\"\\s*:\\s*true");
    in.discard = in.load_preset && std::regex_search(json, discard_re);
    static const std::regex fill_re("\"fill\"\\s*:\\s*\"(defaults|current)\"");
    if (in.load_preset && std::regex_search(json, m, fill_re))
        in.fill = m[1] == "defaults" ? Fill::defaults : Fill::current;
    static const std::regex import_re("\"importPreset\"\\s*:\\s*true");
    in.import_preset = std::regex_search(json, import_re);
    static const std::regex measure_re("\"measure\"\\s*:\\s*(true|false)");
    if (std::regex_search(json, m, measure_re))
        in.measure = m[1] == "true";
    static const std::regex learn_re(R"re("learnHotkey"\s*:\s*"([A-Za-z]*)")re");
    if (std::regex_search(json, m, learn_re))
        in.learn_hotkey = m[1].str();
    static const std::regex hotkeys_re(R"re("setHotkey"\s*:\s*\{([^}]*)\})re");
    static const std::regex hotkey_re(R"re("([A-Za-z]+)"\s*:\s*"([A-Za-z0-9+ ]*)")re");
    if (std::regex_search(json, m, hotkeys_re))
    {
        const std::string body = m[1];
        for (auto it = std::sregex_iterator(body.begin(), body.end(), hotkey_re); it != std::sregex_iterator(); ++it)
            in.set_hotkeys.emplace_back((*it)[1].str(), (*it)[2].str());
    }
    static const std::regex fog_re(R"re("cloudFogOff"\s*:\s*(true|false))re");
    if (std::regex_search(json, m, fog_re))
        in.cloud_fog_off = m[1] == "true";

    const std::lock_guard lock(g_mutex);
    g_requests.set.insert(g_requests.set.end(), in.set.begin(), in.set.end());
    if (in.enabled)
        g_requests.enabled = in.enabled;
    g_requests.reload = g_requests.reload || in.reload;
    for (const auto &[from, to] : {std::pair{&in.save_preset, &g_requests.save_preset},
                                   std::pair{&in.load_preset, &g_requests.load_preset},
                                   std::pair{&in.delete_preset, &g_requests.delete_preset}})
        if (*from)
            *to = *from;
    if (in.load_preset)
    {
        g_requests.discard = in.discard;
        g_requests.fill = in.fill;
    }
    g_requests.import_preset = g_requests.import_preset || in.import_preset;
    if (in.measure)
        g_requests.measure = in.measure;
    if (in.learn_hotkey)
        g_requests.learn_hotkey = in.learn_hotkey;
    g_requests.set_hotkeys.insert(g_requests.set_hotkeys.end(), in.set_hotkeys.begin(), in.set_hotkeys.end());
    if (in.cloud_fog_off)
        g_requests.cloud_fog_off = in.cloud_fog_off;
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

namespace presets
{
namespace
{
// Windows file names: case doesn't count.
bool same_name(std::string_view a, std::string_view b)
{
    return a.size() == b.size() && std::equal(a.begin(), a.end(), b.begin(), [](char x, char y) {
               return std::tolower(static_cast<unsigned char>(x)) == std::tolower(static_cast<unsigned char>(y));
           });
}

std::filesystem::path file_of(const std::filesystem::path &dir, const std::string &name)
{
    return dir / (name + ".ini");
}

bool read_file(const std::filesystem::path &file, std::string &text)
{
    std::ifstream in(file, std::ios::binary);
    if (!in)
        return false;
    text.assign(std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>());
    return !in.bad();
}

// Through a temporary file moved over the target: never half written.
bool write_file(const std::filesystem::path &file, const std::string &text)
{
    std::filesystem::path temp = file;
    temp += L".tmp";
    {
        std::ofstream o(temp, std::ios::binary | std::ios::trunc);
        o << text;
        if (!o)
            return false;
    }
    if (MoveFileExW(temp.c_str(), file.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH))
        return true;
    std::error_code ec;
    std::filesystem::remove(temp, ec);
    return false;
}

using Values = std::map<std::pair<char, unsigned>, float>;

// A tunables file's values as the add-on reads them (the last of a key counts): the keys `known`
// accepts, with finite values. Lines of "<prefix><index> = <value>", spaces or tabs around (by
// hand: std::regex is slow, and this runs on the present thread for every preset).
Values values_of(const std::string &text, const std::function<bool(char prefix, unsigned index)> &known)
{
    const auto blank = [&](size_t i) { return i < text.size() && (text[i] == ' ' || text[i] == '\t'); };
    const auto digit = [&](size_t i) { return i < text.size() && text[i] >= '0' && text[i] <= '9'; };
    Values values;
    for (size_t pos = 0; pos < text.size();)
    {
        const size_t end = std::min(text.find('\n', pos), text.size());
        size_t i = pos;
        pos = end + 1;
        while (blank(i))
            ++i;
        if (i >= end || std::string_view("TRC").find(text[i]) == std::string_view::npos)
            continue;
        const char prefix = text[i++];
        unsigned index = 0, digits = 0;
        for (; digit(i) && digits < 4; ++i, ++digits)
            index = index * 10 + (text[i] - '0');
        if (digits == 0 || digits > 3)
            continue;
        while (blank(i))
            ++i;
        if (i >= end || text[i] != '=')
            continue;
        ++i;
        while (blank(i))
            ++i;
        const size_t first = i;
        while (i < end && std::string_view(" \t\r;").find(text[i]) == std::string_view::npos)
            ++i;
        const std::string number = text.substr(first, i - first);
        char *stop = nullptr;
        const float value = std::strtof(number.c_str(), &stop);
        if (!number.empty() && stop == number.c_str() + number.size() && std::isfinite(value) && known(prefix, index))
            values[{prefix, index}] = value;
    }
    return values;
}

// The values as one number (FNV-1a over their keys and float bits).
uint64_t hash_of(const Values &values)
{
    uint64_t h = 14695981039346656037ull;
    const auto mix = [&h](uint32_t x) {
        for (int i = 0; i < 4; ++i)
            h = (h ^ ((x >> (8 * i)) & 0xff)) * 1099511628211ull;
    };
    for (const auto &[key, value] : values)
    {
        mix(static_cast<uint32_t>(key.first));
        mix(key.second);
        mix(std::bit_cast<uint32_t>(value));
    }
    return h;
}

// The preset saved or loaded last, and after a load the settings as it left them (their hash_of):
// a preset without some of the settings (one from an older version or a public install, say)
// never has exactly tunables.ini's, so it's theirs while they're as its load left them. In the
// folder's last.txt: the name, then the hash in hex (none after a save: the preset is the copy).
struct Last
{
    std::string name;
    std::optional<uint64_t> settings;
};
constexpr const wchar_t *kLastFile = L"last.txt";

void remember(const std::filesystem::path &dir, const std::string &name, std::optional<uint64_t> settings)
{
    char hex[24] = "";
    if (settings)
        std::to_chars(hex, hex + sizeof(hex) - 1, *settings, 16);
    write_file(dir / kLastFile, name + "\n" + hex + "\n");
}

Last remembered(const std::filesystem::path &dir)
{
    std::string text;
    if (!read_file(dir / kLastFile, text))
        return {};
    const size_t end = std::min(text.find('\n'), text.size());
    Last last{valid_name(text.substr(0, text.find_first_of("\r\n"))), std::nullopt};
    const std::string hex = text.substr(std::min(end + 1, text.size()));
    uint64_t settings = 0;
    if (const auto [ptr, ec] = std::from_chars(hex.data(), hex.data() + hex.size(), settings, 16);
        ec == std::errc() && ptr != hex.data())
        last.settings = settings;
    return last;
}
} // namespace

std::string valid_name(const std::string &raw)
{
    const size_t first = raw.find_first_not_of(' '), last = raw.find_last_not_of(' ');
    if (first == std::string::npos)
        return "";
    const std::string name = raw.substr(first, last - first + 1);
    if (name.size() > kNameMax || name.front() == '.' || name.back() == '.')
        return "";
    for (const char c : name)
    {
        const bool letter = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9');
        if (!letter && (c == '\0' || std::string_view(" -_().,+'").find(c) == std::string_view::npos))
            return "";
    }
    // Windows device names, alone or before an extension (CON, con.txt, COM1)
    std::string stem = name.substr(0, name.find('.'));
    while (!stem.empty() && stem.back() == ' ')
        stem.pop_back();
    for (const char *device : {"CON", "PRN", "AUX", "NUL"})
        if (same_name(stem, device))
            return "";
    if (stem.size() == 4 && (same_name(stem.substr(0, 3), "COM") || same_name(stem.substr(0, 3), "LPT")) &&
        stem[3] >= '1' && stem[3] <= '9')
        return "";
    return name;
}

std::vector<std::string> list(const std::filesystem::path &dir)
{
    std::vector<std::string> names;
    std::error_code ec;
    for (const auto &entry : std::filesystem::directory_iterator(dir, ec))
    {
        if (!entry.is_regular_file(ec) || entry.path().extension() != L".ini")
            continue;
        const std::u8string stem = entry.path().stem().u8string();
        const std::string name(stem.begin(), stem.end());
        if (valid_name(name) == name)
            names.push_back(name);
    }
    std::sort(names.begin(), names.end(), [](const std::string &a, const std::string &b) {
        if (same_name(a, kDefault) != same_name(b, kDefault))
            return same_name(a, kDefault);
        return std::lexicographical_compare(a.begin(), a.end(), b.begin(), b.end(), [](char x, char y) {
            return std::tolower(static_cast<unsigned char>(x)) < std::tolower(static_cast<unsigned char>(y));
        });
    });
    return names;
}

std::string stamp(const std::filesystem::path &dir, const std::filesystem::path &tunables)
{
    // (the folder's sizes and times as its listing has them: no call per file)
    std::string out;
    const auto add = [&out](const std::filesystem::directory_entry &entry) {
        std::error_code ec;
        const std::u8string name = entry.path().filename().u8string();
        const uintmax_t size = entry.file_size(ec);
        const auto time = entry.last_write_time(ec).time_since_epoch().count();
        out.append(name.begin(), name.end());
        out += " " + std::to_string(size) + " " + std::to_string(time) + "\n";
    };
    std::error_code ec;
    add(std::filesystem::directory_entry(tunables, ec));
    for (const auto &entry : std::filesystem::directory_iterator(dir, ec))
        add(entry);
    return out;
}

Current current(const std::filesystem::path &dir, const std::filesystem::path &tunables,
                const std::function<bool(char prefix, unsigned index)> &known)
{
    std::string text;
    if (!read_file(tunables, text))
        return {};
    const Values settings = values_of(text, known);
    std::vector<std::string> names = list(dir);
    const Last last = remembered(dir);
    const auto it = std::find_if(names.begin(), names.end(), [&](const std::string &n) { return same_name(n, last.name); });
    const std::string last_there = it != names.end() ? *it : "";
    const auto exactly = [&](const std::string &name) {
        std::string other;
        return !settings.empty() && read_file(file_of(dir, name), other) && values_of(other, known) == settings;
    };
    // (the last one first, also as its load left them: another preset with the same values, say one
    // that has what it lacked, isn't the one just loaded)
    if (!last_there.empty() && (last.settings == hash_of(settings) || exactly(last_there)))
        return {last_there, true};
    for (const std::string &name : names)
        if (name != last_there && exactly(name))
            return {name, true};
    if (last_there.empty())
        return {};
    return {last_there, false};
}

bool save(const std::filesystem::path &dir, const std::string &raw, const std::filesystem::path &tunables,
          std::string &error)
{
    const std::string name = valid_name(raw);
    if (name.empty())
    {
        error = "A name: letters, digits, spaces and - _ ( ) . , + ', up to " + std::to_string(kNameMax) +
                " characters";
        return false;
    }
    if (same_name(name, kDefault))
    {
        error = "\"Default\" can't be changed: choose another name";
        return false;
    }
    std::string text;
    if (!read_file(tunables, text) || text.empty())
    {
        error = "Couldn't read tunables.ini: nothing saved";
        return false;
    }
    std::error_code ec;
    std::filesystem::create_directories(dir, ec);
    if (!write_file(file_of(dir, name), text))
    {
        error = "Couldn't write the preset \"" + name + "\"";
        return false;
    }
    remember(dir, name, std::nullopt);
    return true;
}

std::map<std::string, std::vector<std::string>> missing(const std::filesystem::path &dir,
                                                        const std::function<bool(char prefix, unsigned index)> &known)
{
    std::map<std::string, std::vector<std::string>> result;
    std::string text;
    if (!read_file(file_of(dir, kDefault), text))
        return result;
    const Values defaults = values_of(text, known);
    for (const std::string &name : list(dir))
    {
        if (same_name(name, kDefault) || !read_file(file_of(dir, name), text))
            continue;
        const Values values = values_of(text, known);
        std::vector<std::string> keys;
        for (const auto &[key, value] : defaults)
            if (!values.contains(key))
                keys.push_back(key.first + std::to_string(key.second));
        if (!keys.empty())
            result[name] = std::move(keys);
    }
    return result;
}

size_t load(const std::filesystem::path &dir, const std::string &raw, const std::filesystem::path &tunables,
            const std::function<bool(char prefix, unsigned index)> &known, bool discard, Fill fill,
            std::string &error)
{
    const std::string name = valid_name(raw);
    std::string text;
    if (name.empty() || !read_file(file_of(dir, name), text))
    {
        error = "There's no preset \"" + raw + "\"";
        return 0;
    }
    Values values = values_of(text, known);
    if (values.empty())
    {
        error = "\"" + name + "\" has no settings in it: nothing loaded";
        return 0;
    }
    if (!discard && !current(dir, tunables, known).saved)
    {
        error = "Not loaded: your settings aren't saved";
        return 0;
    }
    // the settings it lacks (that Default has) first into the preset: appended, as tunables.ini gets new keys
    if (fill != Fill::none && !same_name(name, kDefault))
    {
        std::string default_text, current_text;
        if (!read_file(file_of(dir, kDefault), default_text) || !read_file(tunables, current_text))
        {
            error = "Couldn't read Default and tunables.ini: nothing loaded";
            return 0;
        }
        const Values defaults = values_of(default_text, known);
        const Values from = fill == Fill::defaults ? defaults : values_of(current_text, known);
        std::vector<Change> added;
        for (const auto &[key, value] : defaults)
            if (const auto it = from.find(key); !values.contains(key) && it != from.end())
                added.push_back({key.first, key.second, it->second});
        if (!added.empty() && !settings::save(file_of(dir, name), added))
        {
            error = "Couldn't add the settings it lacks to \"" + name + "\": nothing loaded";
            return 0;
        }
        for (const Change &c : added)
            values[{c.prefix, c.index}] = c.value;
    }
    // in place, through a temporary file: tunables.ini is either as it was or complete
    std::vector<Change> changes;
    for (const auto &[key, value] : values)
        changes.push_back({key.first, key.second, value});
    if (!settings::save(tunables, changes))
    {
        error = "Couldn't write tunables.ini: nothing loaded";
        return 0;
    }
    // (the settings as the load left them: the preset's while they stay so, though it lacks some)
    std::string loaded;
    remember(dir, name, read_file(tunables, loaded) ? std::optional(hash_of(values_of(loaded, known))) : std::nullopt);
    return values.size();
}

bool remove(const std::filesystem::path &dir, const std::string &raw, std::string &error)
{
    const std::string name = valid_name(raw);
    if (!name.empty() && same_name(name, kDefault))
    {
        error = "\"Default\" can't be deleted";
        return false;
    }
    std::error_code ec;
    if (name.empty() || !std::filesystem::remove(file_of(dir, name), ec))
    {
        error = "There's no preset \"" + raw + "\"";
        return false;
    }
    return true;
}

std::string import(const std::filesystem::path &dir, const std::filesystem::path &from,
                   const std::function<bool(char prefix, unsigned index)> &known, std::string &error)
{
    std::error_code ec;
    if (std::filesystem::equivalent(from.parent_path(), dir, ec))
    {
        error = "That file is a preset already";
        return "";
    }
    const uintmax_t size = std::filesystem::file_size(from, ec);
    std::string text;
    if (ec || size > kImportMax || !read_file(from, text))
    {
        error = !ec && size > kImportMax ? "That file is too large for a preset" : "Couldn't read that file";
        return "";
    }
    if (text.starts_with("\xEF\xBB\xBF"))
        text.erase(0, 3);
    if (values_of(text, known).empty())
    {
        error = "That file has no AtmosphereMod settings in it";
        return "";
    }
    // the name: the file's, with what a preset's can't have as spaces, cut to `max`
    const auto fit = [](std::string s, size_t max) {
        s.resize(std::min(s.size(), max));
        while (!s.empty() && (s.back() == ' ' || s.back() == '.'))
            s.pop_back();
        s.erase(0, std::min(s.find_first_not_of(". "), s.size()));
        return s;
    };
    std::string base;
    for (const char8_t u : from.stem().u8string())
    {
        const char c = static_cast<char>(u);
        if (u < 0x80 && ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
                         std::string_view("-_().,+'").find(c) != std::string_view::npos))
            base += c;
        else if (!base.empty() && base.back() != ' ')
            base += ' ';
    }
    base = fit(base, kNameMax);
    if (base.empty() || valid_name(base) != base)
        base = "Imported";
    std::string name = base;
    for (int n = 2; same_name(name, kDefault) || std::filesystem::exists(file_of(dir, name), ec); ++n)
    {
        if (n > 99)
        {
            error = "There are too many presets named \"" + base + "\"";
            return "";
        }
        const std::string suffix = " (" + std::to_string(n) + ")";
        name = fit(base, kNameMax - suffix.size()) + suffix;
    }
    std::filesystem::create_directories(dir, ec);
    if (!write_file(file_of(dir, name), text))
    {
        error = "Couldn't write the preset \"" + name + "\"";
        return "";
    }
    return name;
}
} // namespace presets
} // namespace settings
