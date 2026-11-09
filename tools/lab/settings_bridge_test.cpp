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

void write(const std::filesystem::path &p, const std::string &text)
{
    std::ofstream o(p, std::ios::binary | std::ios::trunc);
    o << text;
}

// Presets (settings::presets): names, saving, which preset the settings are, loading without
// harming settings no preset has, Default kept, deleting, and the app's requests.
void presets_checks()
{
    namespace p = settings::presets;
    // names
    check(p::valid_name("  My clouds (summer) ") == "My clouds (summer)", "name: trimmed");
    check(p::valid_name("XP12-like, v2 + haze 'b'") == "XP12-like, v2 + haze 'b'", "name: - , + ' and digits");
    bool refused = true;
    for (const char *bad : {"", "   ", "..\\tunables", "a/b", "a:b", "x\"y", "con", "NUL.txt", "COM1", "lpt9 .ini",
                            ".hidden", "trailing.", "\xc3\xa4rger", "this name is far too long for a preset file!"})
        refused = refused && p::valid_name(bad).empty();
    check(refused, "name: refused: empty, paths, quotes, device names, dots at the ends, non-ASCII, too long");
    check(p::valid_name("COM10") == "COM10" && p::valid_name("Console") == "Console", "name: COM10, Console allowed");

    const auto dir = std::filesystem::temp_directory_path() / "atmo_presets_test";
    std::filesystem::remove_all(dir);
    std::filesystem::create_directories(dir);
    const auto presets = dir / "presets";
    const auto tunables = dir / "tunables.ini";
    const std::string mine = "; my settings\r\nT3 = 4\r\nC16 = 0.5   ; solid clouds\r\nR1 = 3.5\r\n; C9 = 1 (off)\r\n";
    write(tunables, mine);
    const auto known = [](char prefix, unsigned index) {
        return (prefix == 'T' && index < 16) || (prefix == 'R' && index < 14) || (prefix == 'C' && index < 36);
    };
    std::string error;
    const auto is = [&](const char *name, bool saved) {
        const p::Current c = p::current(presets, tunables, known);
        return c.name == name && c.saved == saved;
    };

    // nothing saved yet: no preset
    check(is("", false), "current: none before any preset");
    std::filesystem::create_directories(presets);
    write(presets / "Default.ini", "; defaults\r\nT3 = 1\r\nC16 = 0.5\r\nR1 = 3.5\r\nC33 = 40\r\n");
    check(is("", false), "current: none while no preset has the settings");

    // saving: tunables.ini as it is, and then the settings are that preset's
    std::string stamp = p::stamp(presets, tunables);
    check(p::save(presets, "Summer", tunables, error) && read(presets / "Summer.ini") == mine,
          "save: a copy of tunables.ini");
    check(is("Summer", true), "current: the saved preset");
    check(p::stamp(presets, tunables) != stamp, "stamp: changed by a save");
    check(!p::save(presets, "default", tunables, error) && !error.empty() &&
              read(presets / "Default.ini").find("; defaults") == 0,
          "save: Default refused (any case)");
    check(!p::save(presets, "..\\evil", tunables, error) && !std::filesystem::exists(dir / "evil.ini"),
          "save: a path refused");

    // a change: still Summer, not saved; back as it was: saved again
    stamp = p::stamp(presets, tunables);
    const std::string changed = "; my settings\r\nT3 = 5\r\nC16 = 0.5   ; solid clouds\r\nR1 = 3.5\r\n";
    write(tunables, changed);
    check(is("Summer", false), "current: changed since the save: Summer, not saved");
    check(p::stamp(presets, tunables) != stamp, "stamp: changed by tunables.ini");
    write(tunables, "\tT3\t=\t4;x\r\nR1=3.5\nC16 = 0.50 ; y\r\nT2 =\r\nC 9 = 1\r\nT5 = 1x\r\nT0003 = 2\r\n");
    check(is("Summer", true), "current: the same values (other text, tabs, no value, junk): Summer");

    // loading over unsaved settings: refused unless discarded
    write(tunables, changed);
    check(p::load(presets, "Default", tunables, known, false, settings::Fill::none, error) == 0 && !error.empty() &&
              read(tunables) == changed,
          "load: settings no preset has aren't replaced unasked");
    size_t n = p::load(presets, "Default", tunables, known, true, settings::Fill::none, error);
    check(n == 4 && value_of(read(tunables), 'C', 33) == 40.0f && value_of(read(tunables), 'T', 3) == 1.0f,
          "load: ... but are when discarded");
    check(is("Default", true), "current: Default, once loaded");

    // loading from saved settings: values written in place, the rest kept
    write(tunables, mine);
    write(presets / "Storm.ini", "; storm\r\nT3 = 6\r\nC16 = 2\r\nC35 = 1\r\nC99 = 5\r\nX1 = 2\r\nT4 = abc\r\n"
                                 "T5 = nan\r\nT6 = 1e40\r\n");
    n = p::load(presets, "Storm", tunables, known, false, settings::Fill::none, error);
    std::string text = read(tunables);
    check(n == 3, "load: T3, C16, C35 (C99, X1, a word, nan and an overflow skipped)");
    check(value_of(text, 'T', 3) == 6.0f && value_of(text, 'C', 16) == 2.0f && value_of(text, 'C', 35) == 1.0f,
          "load: values written");
    check(value_of(text, 'R', 1) == 3.5f && text.find("; my settings\r\n") == 0 &&
              text.find("C16 = 2   ; solid clouds\r\n") != std::string::npos &&
              text.find("; C9 = 1 (off)\r\n") != std::string::npos && text.find("C99") == std::string::npos &&
              text.find("T4") == std::string::npos,
          "load: comments, spacing and the keys the preset lacks kept; nothing unknown added");
    check(!std::filesystem::exists(dir / "tunables.ini.efb-tmp") && !std::filesystem::exists(presets / "last.txt.tmp"),
          "load: no temporary files left");
    // a preset without some of the settings (Storm lacks R1): its, as the load left them
    check(is("Storm", true), "current: Storm, saved: though it lacks R1, the settings are as its load left them");
    const std::string storm_loaded = text;
    write(tunables, storm_loaded + "R1 = 9\r\n");
    check(is("Storm", false), "current: ... not saved once a setting it lacks changes");
    write(tunables, storm_loaded);
    check(is("Storm", true), "current: ... and saved again when it's back");
    const std::string last_txt = read(presets / "last.txt");
    write(presets / "last.txt", "Storm");
    check(is("Storm", false), "current: a last.txt without the settings' hash (older): not saved");
    write(presets / "last.txt", last_txt);
    write(presets / "Storm.ini", text);
    check(is("Storm", true), "current: Storm once it has them all");

    // two presets with the same values: the one saved or loaded last
    write(presets / "Copy.ini", text);
    check(is("Storm", true), "current: of two with the settings, the one loaded last");
    check(p::save(presets, "Copy", tunables, error) && is("Copy", true), "current: ... or saved last");

    // a preset that's empty, missing or all junk changes nothing
    write(presets / "Empty.ini", "; nothing\r\nX1 = 2\r\n");
    const std::string before_failures = read(tunables);
    check(p::load(presets, "Empty", tunables, known, true, settings::Fill::none, error) == 0 && !error.empty(),
          "load: a preset without settings: refused");
    check(p::load(presets, "Missing", tunables, known, true, settings::Fill::none, error) == 0, "load: a missing preset: refused");
    check(p::load(presets, "..\\tunables", tunables, known, true, settings::Fill::none, error) == 0, "load: a path: refused");
    check(read(tunables) == before_failures && is("Copy", true),
          "load: after the refusals tunables.ini and the current preset are unchanged");

    // deleting: the current preset goes, then no preset has the settings
    check(!p::remove(presets, "Default", error) && std::filesystem::exists(presets / "Default.ini"),
          "delete: Default refused");
    check(p::remove(presets, "copy", error) && !std::filesystem::exists(presets / "Copy.ini"),
          "delete: a preset (any case)");
    check(!p::remove(presets, "Copy", error), "delete: an already deleted one: refused");
    check(is("Storm", true), "current: after deleting it, Storm has the settings");
    check(p::remove(presets, "Storm", error) && is("", false), "current: and after Storm, none");

    // the list: Default first, the rest by name; other files left out
    write(presets / "alpha.ini", "T3 = 1\r\n");
    write(presets / "Notes.txt", "x");
    write(presets / "con.ini", "T3 = 1\r\n");
    const auto names = p::list(presets);
    check(names == std::vector<std::string>{"Default", "alpha", "Empty", "Summer"}, "list: Default, alpha, Empty, Summer");

    // importing: a file from elsewhere, named after it, never replacing a preset
    const auto downloads = dir / "downloads";
    std::filesystem::create_directories(downloads);
    const std::string shared = "\xEF\xBB\xBF; shared\r\nT3 = 7\r\nC16 = 1\r\n";
    const std::filesystem::path fancy = downloads / u8"Tom’s clouds – v2.ini";
    write(fancy, shared);
    check(p::import(presets, fancy, known, error) == "Tom s clouds v2" &&
              read(presets / "Tom s clouds v2.ini") == shared.substr(3),
          "import: named after the file (what a name can't have as spaces), the byte-order mark dropped");
    check(p::import(presets, fancy, known, error) == "Tom s clouds v2 (2)" &&
              read(presets / "Tom s clouds v2.ini") == shared.substr(3),
          "import: the same again: (2), the first kept");
    write(downloads / "default.ini", shared);
    check(p::import(presets, downloads / "default.ini", known, error) == "default (2)" &&
              read(presets / "Default.ini").find("; defaults") == 0,
          "import: a file named Default: (2), Default kept");
    const std::filesystem::path clouds = downloads / u8"☁☁.ini";
    write(clouds, shared);
    check(p::import(presets, clouds, known, error) == "Imported", "import: nothing usable in the name: Imported");
    const std::filesystem::path wordy = downloads / "this is a rather long name for a preset file, really.ini";
    write(wordy, shared);
    check(p::import(presets, wordy, known, error) == "this is a rather long name for a preset" &&
              p::import(presets, wordy, known, error) == "this is a rather long name for a pre (2)",
          "import: a long name cut to 40 characters, with its (2)");
    write(downloads / "notes.ini", "; just text\r\nX1 = 2\r\n");
    check(p::import(presets, downloads / "notes.ini", known, error).empty() && !error.empty() &&
              !std::filesystem::exists(presets / "notes.ini"),
          "import: a file without settings: refused");
    write(downloads / "big.ini", "T3 = 1\r\n" + std::string(p::kImportMax, ';'));
    check(p::import(presets, downloads / "big.ini", known, error).empty() && !std::filesystem::exists(presets / "big.ini"),
          "import: a file over 1 MB: refused");
    check(p::import(presets, presets / "Summer.ini", known, error).empty() &&
              !std::filesystem::exists(presets / "Summer (2).ini"),
          "import: one of the folder's own presets: refused");
    check(p::import(presets, downloads / "missing.ini", known, error).empty(), "import: a missing file: refused");

    // settings a preset lacks (Default has T3, C16, R1, C33): added to it first, from Default or the
    // current settings, or left as they are
    const auto gaps = p::missing(presets, known);
    check(gaps.at("Summer") == std::vector<std::string>{"C33"} &&
              gaps.at("alpha") == std::vector<std::string>{"C16", "C33", "R1"} &&
              gaps.at("Tom s clouds v2") == std::vector<std::string>{"C33", "R1"} && !gaps.contains("Default"),
          "missing: the settings Default has and a preset doesn't");
    write(tunables, "T3 = 2\r\nC16 = 3\r\nR1 = 1\r\nC33 = 90\r\nC35 = 1\r\n");
    write(presets / "Twin.ini", "T3 = 4\r\nC16 = 0.5\r\nR1 = 3.5\r\nC33 = 40\r\nC35 = 1\r\n");  // as Summer + Default
    n = p::load(presets, "Summer", tunables, known, true, settings::Fill::defaults, error);
    text = read(tunables);
    check(n == 4 && value_of(text, 'T', 3) == 4.0f && value_of(text, 'C', 33) == 40.0f &&
              value_of(text, 'C', 35) == 1.0f,
          "load filling from Default: Summer's, C33 from Default, C35 (not in Default) kept");
    check(value_of(read(presets / "Summer.ini"), 'C', 33) == 40.0f &&
              read(presets / "Summer.ini").find(mine) == 0 && !p::missing(presets, known).contains("Summer"),
          "... C33 added to Summer (appended, the rest as it was): it lacks nothing now");
    check(is("Summer", true), "current: Summer, the one loaded, not Twin with the same values");
    n = p::load(presets, "alpha", tunables, known, false, settings::Fill::current, error);
    text = read(tunables);
    check(n == 4 && value_of(text, 'T', 3) == 1.0f && value_of(text, 'C', 16) == 0.5f &&
              value_of(text, 'C', 33) == 40.0f && value_of(read(presets / "alpha.ini"), 'C', 16) == 0.5f &&
              value_of(read(presets / "alpha.ini"), 'R', 1) == 3.5f,
          "load filling from the current settings: alpha's T3, the rest as they were, and now in alpha too");
    check(is("alpha", true), "current: alpha");
    n = p::load(presets, "Tom s clouds v2", tunables, known, false, settings::Fill::none, error);
    text = read(tunables);
    check(n == 2 && value_of(text, 'T', 3) == 7.0f && value_of(text, 'R', 1) == 3.5f &&
              value_of(text, 'C', 33) == 40.0f && p::missing(presets, known).contains("Tom s clouds v2"),
          "load leaving them: an imported preset's T3 and C16, the rest as they were, the preset as it was");
    check(is("Tom s clouds v2", true), "current: it, as the load left them");

    // the app's requests
    settings::on_message(R"({"savePreset":"My set"})");
    settings::on_message(R"({"loadPreset":"Storm"})");
    settings::on_message(R"({"deletePreset":"a\"b"})");
    settings::Requests r = settings::take();
    check(r.save_preset == std::optional<std::string>("My set") && r.load_preset == std::optional<std::string>("Storm") &&
              !r.delete_preset && !r.discard,
          "requests: save and load parsed, a name with a quote ignored");
    check(!settings::take().load_preset, "requests: taken once");
    settings::on_message(R"({"loadPreset":"Storm","discard":true})");
    r = settings::take();
    check(r.discard && r.fill == settings::Fill::none, "requests: a load that discards");
    settings::on_message(R"({"loadPreset":"Storm","discard":true,"fill":"defaults"})");
    settings::on_message(R"({"loadPreset":"Default"})");
    r = settings::take();
    check(r.load_preset == std::optional<std::string>("Default") && !r.discard && r.fill == settings::Fill::none &&
              !r.import_preset,
          "requests: the last load counts, with its own discard and fill");
    settings::on_message(R"({"loadPreset":"Storm","fill":"defaults"})");
    check(settings::take().fill == settings::Fill::defaults, "requests: a load filling from Default");
    settings::on_message(R"({"loadPreset":"Storm","fill":"current"})");
    check(settings::take().fill == settings::Fill::current, "requests: a load filling from the current settings");
    settings::on_message(R"({"loadPreset":"Storm","fill":"other"})");
    check(settings::take().fill == settings::Fill::none, "requests: an unknown fill: none");
    settings::on_message(R"({"importPreset":true})");
    check(settings::take().import_preset && !settings::take().import_preset, "requests: an import, taken once");
    settings::on_message(R"({"measure":true})");
    r = settings::take();
    check(r.measure == std::optional<bool>(true) && !settings::take().measure, "requests: a measurement, taken once");
    settings::on_message(R"({"measure":true})");
    settings::on_message(R"({"measure":false})");
    check(settings::take().measure == std::optional<bool>(false), "requests: stopping a measurement, the last one counts");
    settings::on_message(R"({"get":true})");
    check(!settings::take().measure, "requests: no measurement unless asked");
    std::filesystem::remove_all(dir);
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

    presets_checks();

    std::printf("%s\n", g_failures ? "FAILED" : "all checks passed");
    return g_failures ? 1 : 0;
}
