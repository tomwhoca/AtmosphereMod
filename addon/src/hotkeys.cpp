// The add-on's hotkeys (hotkeys.h).

#include "hotkeys.h"

#include <algorithm>
#include <cctype>
#include <fstream>
#include <iterator>
#include <regex>
#include <sstream>

namespace hotkeys
{
namespace
{
constexpr uint8_t kF1 = 0x70, kF12 = 0x7B;  // VK_F1, VK_F12

bool is_f(uint8_t vk) { return vk >= kF1 && vk <= kF12; }
bool is_letter(uint8_t vk) { return vk >= 'A' && vk <= 'Z'; }
bool is_digit(uint8_t vk) { return vk >= '0' && vk <= '9'; }

std::string key_name(uint8_t vk)
{
    if (is_f(vk))
        return "F" + std::to_string(vk - kF1 + 1);
    if (is_letter(vk) || is_digit(vk))
        return std::string(1, char(vk));
    return {};
}

std::string lower(std::string s)
{
    std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return char(std::tolower(c)); });
    return s;
}

std::string trim(const std::string &s)
{
    const size_t a = s.find_first_not_of(" \t\r\n"), b = s.find_last_not_of(" \t\r\n");
    return a == std::string::npos ? std::string() : s.substr(a, b - a + 1);
}

std::string json_escaped(const std::string &s)
{
    std::string out;
    for (const char c : s)
    {
        if (c == '"' || c == '\\')
            out += '\\';
        if (static_cast<unsigned char>(c) >= 0x20)
            out += c;
    }
    return out;
}

Key make(uint8_t vk, bool ctrl, bool shift, bool alt)
{
    Key k;
    k.vk = vk;
    k.ctrl = ctrl;
    k.shift = shift;
    k.alt = alt;
    return k;
}
} // namespace

int action(const std::string &name)
{
    for (int a = 0; a < kActions; ++a)
        if (name == kNames[a])
            return a;
    return -1;
}

Keys defaults()
{
    Keys keys;
    keys[kToggle] = make(0x78, true, true, false);    // F9
    keys[kReload] = make(0x79, true, true, false);    // F10
    keys[kCapture] = make(0x7A, true, true, false);   // F11
    keys[kGpuTimes] = make(0x7B, true, true, false);  // F12
    return keys;
}

const std::vector<uint8_t> &learnable()
{
    static const std::vector<uint8_t> keys = [] {
        std::vector<uint8_t> v;
        for (uint8_t vk = kF1; vk <= kF12; ++vk)
            v.push_back(vk);
        for (uint8_t vk = 'A'; vk <= 'Z'; ++vk)
            v.push_back(vk);
        for (uint8_t vk = '0'; vk <= '9'; ++vk)
            v.push_back(vk);
        return v;
    }();
    return keys;
}

std::string text(const Key &key)
{
    if (key.none())
        return {};
    std::string out;
    if (key.ctrl)
        out += "Ctrl+";
    if (key.shift)
        out += "Shift+";
    if (key.alt)
        out += "Alt+";
    return out + key_name(key.vk);
}

std::optional<Key> parse(const std::string &raw)
{
    std::string s;
    for (const char c : raw)
        if (!std::isspace(static_cast<unsigned char>(c)))
            s += c;
    s = lower(s);
    if (s.empty() || s == "none")
        return Key{};
    Key key;
    size_t pos = 0;
    while (true)
    {
        const size_t plus = s.find('+', pos);
        const std::string part = s.substr(pos, plus == std::string::npos ? std::string::npos : plus - pos);
        if (plus == std::string::npos)
        {
            for (const uint8_t vk : learnable())
                if (lower(key_name(vk)) == part)
                    key.vk = vk;
            if (key.none())
                return std::nullopt;
            return key;
        }
        bool *mod = part == "ctrl" ? &key.ctrl : part == "shift" ? &key.shift : part == "alt" ? &key.alt : nullptr;
        if (mod == nullptr || *mod)
            return std::nullopt;  // (not a modifier, or one twice)
        *mod = true;
        pos = plus + 1;
    }
}

bool usable(const Key &key)
{
    if (is_f(key.vk))
        return key.ctrl || key.shift || key.alt;
    if (is_letter(key.vk) || is_digit(key.vk))
        return key.ctrl || key.alt;
    return false;
}

Keys load(const std::filesystem::path &file)
{
    Keys keys = defaults();
    std::ifstream in(file);
    std::string line;
    while (std::getline(in, line))
    {
        const size_t eq = line.find('=');
        if (eq == std::string::npos || trim(line).starts_with(";") || trim(line).starts_with("#"))
            continue;
        const int a = action(trim(line.substr(0, eq)));
        const std::optional<Key> key = parse(line.substr(eq + 1));
        if (a >= 0 && key && (key->none() || usable(*key)))
            keys[a] = *key;
    }
    return keys;
}

bool save(const std::filesystem::path &file, const Keys &keys)
{
    std::string out = "; AtmosphereMod's hotkeys: the EFB app's Hotkeys tab writes this file. none: no hotkey.\n"
                      "; Keys: F1-F12 with any of Ctrl, Shift, Alt; A-Z or 0-9 with Ctrl or Alt.\n";
    for (int a = 0; a < kActions; ++a)
        out += std::string(kNames[a]) + " = " + (keys[a].none() ? "none" : text(keys[a])) + "\n";
    std::error_code ec;
    std::filesystem::create_directories(file.parent_path(), ec);
    const std::filesystem::path temp = file.string() + ".tmp";
    {
        std::ofstream o(temp, std::ios::binary | std::ios::trunc);
        o << out;
        if (!o)
            return false;
    }
    std::filesystem::rename(temp, file, ec);
    return !ec;
}

std::string json(const Keys &keys)
{
    std::string out = "{";
    for (int a = 0; a < kActions; ++a)
        out += std::string(a > 0 ? "," : "") + "\"" + kNames[a] + "\":\"" + text(keys[a]) + "\"";
    return out + "}";
}

void Learner::start(int action, double now)
{
    action_ = action >= 0 && action < kActions ? action : -1;
    since_ = now;
    note_.clear();
}

void Learner::cancel()
{
    if (action_ >= 0)
        note_ = "cancelled";
    action_ = -1;
}

bool Learner::step(Keys &keys, const Press &press, double now)
{
    if (action_ < 0)
        return false;
    const std::string name = kNames[action_];
    if (press.escape)
    {
        cancel();
        return false;
    }
    if (press.backspace)
    {
        const bool changed = !keys[action_].none();
        keys[action_] = Key{};
        note_ = "cleared:" + name;
        action_ = -1;
        return changed;
    }
    if (press.vk != 0)
    {
        const Key key = make(press.vk, press.ctrl, press.shift, press.alt);
        if (!usable(key))
        {
            note_ = "unusable:" + text(key);  // (waits for another)
            since_ = now;
            return false;
        }
        for (int a = 0; a < kActions; ++a)
            if (a != action_ && keys[a] == key)
            {
                note_ = "taken:" + std::string(kNames[a]);
                since_ = now;
                return false;
            }
        const bool changed = !(keys[action_] == key);
        keys[action_] = key;
        note_ = "set:" + name;
        action_ = -1;
        return changed;
    }
    if (now - since_ >= kTimeoutSeconds)
    {
        note_ = "timeout";
        action_ = -1;
    }
    return false;
}

void GlassFix::scan(const std::string &text)
{
    static const std::regex startup(R"(hotkeys: (.*?) overlay on/off, (.*?) green tint)");
    static const std::regex change(R"(setting (fix_on_off|green_tint) hotkey=(.*))");
    part_ += text;
    size_t pos = 0, end;
    std::smatch m;
    while ((end = part_.find('\n', pos)) != std::string::npos)
    {
        std::string line = part_.substr(pos, end - pos);
        pos = end + 1;
        if (line.find("hotkey") == std::string::npos)  // (most lines: no regex)
            continue;
        if (!line.empty() && line.back() == '\r')
            line.pop_back();
        if (std::regex_search(line, m, startup))
        {
            keys_[0] = trim(m[1]);
            keys_[1] = trim(m[2]);
        }
        else if (std::regex_search(line, m, change))
            keys_[m[1] == "fix_on_off" ? 0 : 1] = trim(m[2]);
    }
    part_.erase(0, pos);
}

std::vector<Other> GlassFix::others() const
{
    const char *what[2] = {"its overlay on/off", "its green tint"};
    std::vector<Other> out;
    for (int i = 0; i < 2; ++i)
    {
        const std::optional<Key> key = parse(keys_[i]);
        out.push_back({"Glass Cockpit DLSS Fix", what[i], key ? text(*key) : keys_[i]});
    }
    return out;
}

std::string json(const std::vector<Other> &others)
{
    std::string out = "[";
    for (const Other &o : others)
        out += std::string(out.size() > 1 ? "," : "") + "{\"addon\":\"" + json_escaped(o.addon) + "\",\"what\":\"" +
               json_escaped(o.what) + "\",\"keys\":\"" + json_escaped(o.keys) + "\"}";
    return out + "]";
}
} // namespace hotkeys
