// Settings from the EFB app: the AtmosphereMod EFB app shows the main tunables
// as switches and sliders (efb/PackageSources/AtmoWeather/src/Settings.tsx) and
// talks to the add-on over CommBus, through the weather feed's SimConnect
// connection (weather_feed.cpp):
//
//   AtmosphereMod.Settings  app -> add-on  {"get":true}, {"set":{"T3":4.5,"C2":0.4}}, {"enabled":false},
//                                          {"reload":true} (as Ctrl+Shift+F10), {"savePreset":"name"},
//                                          {"loadPreset":"name"} (+ "discard":true, "fill":"defaults"|"current"),
//                                          {"deletePreset":"name"}, {"importPreset":true} (a file dialog:
//                                          file_dialog.h)
//   AtmosphereMod.State     add-on -> app  {"enabled":true,"T":[...],"C":[...],"TE":[...],"weather":"...",
//                                          "version":"0.5.1","presets":["Default",...],"preset":"name",
//                                          "presetSaved":true,"presetsMissing":{"name":["C34",...]},
//                                          "presetNote":"..."}
//
// T and C are tunables.ini's values, TE the atmosphere tunables the shaders
// get (with the METAR's haze blended in by T13), enabled the Ctrl+Shift+F9
// switch, version the add-on's (addon/CMakeLists.txt), presets the saved sets
// of tunables (namespace presets below), preset and presetSaved the one the
// settings are (presets::current), presetsMissing the settings presets lack
// (presets::missing: the app asks what to do about them before loading one)
// and presetNote why the last preset request
// failed, or what an import did ("" if there's nothing to say). The present
// thread applies the changes
// (atmosphere_mod.cpp) and saves them into tunables.ini once they have rested
// for a second.

#pragma once

#include <filesystem>
#include <functional>
#include <map>
#include <optional>
#include <string>
#include <vector>

namespace settings
{
constexpr const char *kSettingsEvent = "AtmosphereMod.Settings";
constexpr const char *kStateEvent = "AtmosphereMod.State";

// One tunable: T3, C20, ...
struct Change
{
    char prefix = 'T';
    unsigned index = 0;
    float value = 0.0f;
};

// What a load does about the settings the preset lacks (those Default has):
// leaves them as they are, or first fills them into the preset, with Default's
// values or the current ones (tunables.ini's).
enum class Fill
{
    none,
    defaults,
    current,
};

struct Requests
{
    std::vector<Change> set;      // in the order asked for
    std::optional<bool> enabled;  // the Ctrl+Shift+F9 switch
    bool reload = false;          // as Ctrl+Shift+F10
    // a preset's name as the app sent it (presets:: checks it); the last one of each
    std::optional<std::string> save_preset, load_preset, delete_preset;
    bool discard = false;     // load_preset may replace settings no preset has (the user said so)
    Fill fill = Fill::none;  // load_preset: the settings it lacks
    bool import_preset = false;  // a file to choose and add as a preset
};

// A message from the app (the weather feed's thread). Unreadable parts are
// ignored. Any thread.
void on_message(const std::string &json);

// Present thread: what the app asked for since the last call.
Requests take();

// Present thread: the state the app shows. It is sent when it changes, or when
// the app asks for it (it starts, or its view opens).
void publish(std::string state_json);

// Weather feed's thread: the state to send now, if any.
std::optional<std::string> to_send();

// Writes the values into the tunables file in place: the value on each of
// their lines is replaced, comments and order kept; keys the file lacks are
// appended. Written to a temporary file first, then moved over the original.
bool save(const std::filesystem::path &file, const std::vector<Change> &values);

// A value as the file and the app get it: the shortest text that reads back
// as the same float.
std::string format(float value);

// Presets: saved sets of tunables, <name>.ini in a folder (the add-on's
// <data>/presets), each a copy of tunables.ini. "Default" holds the shipping
// defaults (the installer writes it at every install): the app can load it,
// never overwrite or delete it. The settings are a preset's while tunables.ini
// has exactly its values (current); a load replaces settings that no preset has
// only when the app says to discard them. The preset saved or loaded last is
// kept in the folder's last.txt (with, after a load, a hash of the settings it
// left). Names: ASCII letters, digits, space and
// - _ ( ) . , + ', 1 to kNameMax characters, not a Windows device name: the
// file can't leave the folder.
namespace presets
{
constexpr const char *kDefault = "Default";
constexpr size_t kNameMax = 40;

// The name trimmed, or "" if it isn't one a preset may have.
std::string valid_name(const std::string &name);

// The presets in the folder: Default first, then by name.
std::vector<std::string> list(const std::filesystem::path &dir);

// What list and current depend on: the folder's files and tunables.ini, with
// their sizes and write times (cheap: nothing is read).
std::string stamp(const std::filesystem::path &dir, const std::filesystem::path &tunables);

struct Current
{
    std::string name;    // "" while no preset has the settings and none was saved or loaded
    bool saved = false;  // tunables.ini has exactly the preset's values
};

// The preset the settings are. First the one saved or loaded last (while it's
// there), if it has exactly tunables.ini's values (the keys `known` accepts,
// finite) or the settings are as its load left them (a preset without some
// settings, from an older version or a public install, never has exactly
// tunables.ini's). Else another with exactly tunables.ini's values; else the
// last one, not saved (the settings changed since).
Current current(const std::filesystem::path &dir, const std::filesystem::path &tunables,
                const std::function<bool(char prefix, unsigned index)> &known);

// tunables.ini (as it is on disk) saved as preset `name` (replacing one of that
// name), not Default. False with `error` on failure.
bool save(const std::filesystem::path &dir, const std::string &name, const std::filesystem::path &tunables,
          std::string &error);

// For each preset but Default, the settings Default has (`known` keys) and it
// doesn't: "C34", ... (it's from an older version, or they were left out).
// Presets that lack none aren't in it.
std::map<std::string, std::vector<std::string>> missing(const std::filesystem::path &dir,
                                                        const std::function<bool(char prefix, unsigned index)> &known);

// Preset `name`'s values written into tunables.ini, in place (comments and
// order kept): only keys `known` accepts, with finite values, and only if the
// preset has at least one. The settings it lacks (that Default has) stay as
// they are, or are first added to the preset (`fill`: Default's values or the
// current ones), so it has them from then on. Unless `discard`, only while a
// preset has the settings (current is saved): settings that no preset has are
// never replaced unasked. Returns how many values the preset has, 0 with
// `error` when tunables.ini wasn't changed.
size_t load(const std::filesystem::path &dir, const std::string &name, const std::filesystem::path &tunables,
            const std::function<bool(char prefix, unsigned index)> &known, bool discard, Fill fill,
            std::string &error);

// Preset `name` deleted (not Default). False with `error` on failure.
bool remove(const std::filesystem::path &dir, const std::string &name, std::string &error);

// The file `from` (one somebody shared, say) added as a new preset, named after
// the file: what a preset's name can't have becomes spaces ("Imported" if
// nothing is left), and " (2)", " (3)" ... are added while a preset has that
// name (or it's Default): no preset is replaced. Only a file with settings in
// it (`known` keys), up to kImportMax bytes, from outside the folder; a UTF-8
// byte-order mark is dropped. Returns the preset's name, "" with `error` on
// failure.
constexpr size_t kImportMax = 1 << 20;
std::string import(const std::filesystem::path &dir, const std::filesystem::path &from,
                   const std::function<bool(char prefix, unsigned index)> &known, std::string &error);
} // namespace presets
} // namespace settings
