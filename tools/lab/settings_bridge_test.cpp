// The EFB app's settings, add-on side (addon/src/settings_bridge.cpp): message
// parsing, when the state is sent, and saving into tunables.ini in place.
// Run by tools/test_settings_bridge.py; exits 1 on a failure.

#include "settings_bridge.h"

#include <cstdio>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <string>

namespace
{
int g_failures = 0;

void check(bool ok, const char *what)
{
    std::printf("%s %s\n", ok ? "ok  " : "FAIL", what);
    g_failures += !ok;
}

std::string read(const std::filesystem::path &p)
{
    std::ifstream in(p, std::ios::binary);
    return std::string(std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>());
}

// As the add-on reads the file (atmosphere_mod.cpp load_tunables).
float value_of(const std::string &text, char prefix, unsigned want)
{
    float found = -999.0f;
    const std::string format = std::string(" ") + prefix + "%u = %f";
    size_t pos = 0;
    while (pos < text.size())
    {
        const size_t end = std::min(text.find('\n', pos), text.size());
        unsigned index;
        float value;
        if (sscanf_s(text.substr(pos, end - pos).c_str(), format.c_str(), &index, &value) == 2 && index == want)
            found = value;
        pos = end + 1;
    }
    return found;
}
} // namespace

int main()
{
    // messages
    settings::on_message(R"({"set":{"T3":4.5,"C2":0.4,"C20":1e-1,"X9":3,"T99":1}})");
    settings::on_message(R"({"enabled":false})");
    settings::Requests r = settings::take();
    check(r.set.size() == 4, "set: T3, C2, C20 and T99 parsed (the add-on checks ranges), X9 not");
    check(r.set.size() >= 3 && r.set[0].prefix == 'T' && r.set[0].index == 3 && r.set[0].value == 4.5f &&
              r.set[1].prefix == 'C' && r.set[1].index == 2 && r.set[2].index == 20 && r.set[2].value == 0.1f,
          "set: keys and values");
    check(r.enabled.has_value() && !*r.enabled, "enabled: false");
    check(!r.reload, "no reload unless asked");
    settings::on_message(R"({"reload":true})");
    r = settings::take();
    check(r.reload && r.set.empty() && !r.enabled, "reload: asked for");
    r = settings::take();
    check(r.set.empty() && !r.enabled && !r.reload, "take empties the requests");
    settings::on_message("not json at all {{{");
    check(settings::take().set.empty(), "garbage is ignored");

    // when the state is sent
    check(!settings::to_send(), "nothing to send before a state is published");
    settings::publish("{\"a\":1}");
    auto sent = settings::to_send();
    check(sent && *sent == "{\"a\":1}", "a new state is sent");
    check(!settings::to_send(), "... once");
    settings::publish("{\"a\":1}");
    check(!settings::to_send(), "the same state again isn't sent");
    settings::on_message(R"({"get":true})");
    check(settings::to_send().has_value(), "get: the state is sent again");
    settings::publish("{\"a\":2}");
    check(settings::to_send() == std::optional<std::string>("{\"a\":2}"), "a changed state is sent");

    // value format: shortest text that reads back as the same float
    check(settings::format(0.3f) == "0.3" && settings::format(50.0f) == "50" && settings::format(0.33f) == "0.33" &&
              settings::format(4.5f) == "4.5",
          "format: 0.3, 50, 0.33, 4.5");

    // saving: CRLF file with comments, a commented-out key, odd spacing, a missing key
    const auto dir = std::filesystem::temp_directory_path() / "atmo_settings_test";
    std::filesystem::create_directories(dir);
    const auto file = dir / "tunables.ini";
    {
        std::ofstream o(file, std::ios::binary | std::ios::trunc);
        o << "; AtmosphereMod settings\r\n; T3 = 9 (a comment, not a setting)\r\nT3 = 4\r\n"
             "; glory strength\r\n  C9=0.5\r\nC12 = 0.4   \r\nR1 = 3.5\r\n";
    }
    const bool ok = settings::save(file, { { 'T', 3, 6.5f }, { 'C', 9, 1.2f }, { 'C', 21, 75.0f }, { 'T', 3, 7.0f } });
    const std::string text = read(file);
    check(ok, "save returns true");
    check(value_of(text, 'T', 3) == 7.0f, "T3: the last value asked for (7)");
    check(value_of(text, 'C', 9) == 1.2f, "C9 replaced in place despite odd spacing");
    check(value_of(text, 'C', 21) == 75.0f, "C21, missing, appended");
    check(value_of(text, 'C', 12) == 0.4f && value_of(text, 'R', 1) == 3.5f, "other settings unchanged");
    check(text.find("; T3 = 9 (a comment, not a setting)\r\n") != std::string::npos &&
              text.find("; glory strength\r\n") != std::string::npos,
          "comments kept, the commented-out key untouched");
    check(text.find("  C9=1.2\r\n") != std::string::npos && text.find("C12 = 0.4   \r\n") != std::string::npos,
          "the lines' own spacing kept");
    check(text.size() >= 2 && text.substr(text.size() - 2) == "\r\n" && text.find("C21 = 75\r\n") != std::string::npos,
          "appended with the file's CRLF line ends");
    check(!std::filesystem::exists(dir / "tunables.ini.efb-tmp"), "no temporary file left");
    std::filesystem::remove_all(dir);

    std::printf("%s\n", g_failures ? "FAILED" : "all checks passed");
    return g_failures ? 1 : 0;
}
