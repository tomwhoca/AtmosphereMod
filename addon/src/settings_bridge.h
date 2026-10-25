// Settings from the EFB app: the AtmosphereMod EFB app shows the main tunables
// as switches and sliders (efb/PackageSources/AtmoWeather/src/Settings.tsx) and
// talks to the add-on over CommBus, through the weather feed's SimConnect
// connection (weather_feed.cpp):
//
//   AtmosphereMod.Settings  app -> add-on  {"get":true}, {"set":{"T3":4.5,"C2":0.4}}, {"enabled":false},
//                                          {"reload":true} (as Ctrl+Shift+F10)
//   AtmosphereMod.State     add-on -> app  {"enabled":true,"T":[...],"C":[...],"TE":[...],"weather":"..."}
//
// T and C are tunables.ini's values, TE the atmosphere tunables the shaders
// get (with the METAR's haze blended in by T13), enabled the Ctrl+Shift+F9
// switch. The present thread applies the changes (atmosphere_mod.cpp) and
// saves them into tunables.ini once they have rested for a second.

#pragma once

#include <filesystem>
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

struct Requests
{
    std::vector<Change> set;      // in the order asked for
    std::optional<bool> enabled;  // the Ctrl+Shift+F9 switch
    bool reload = false;          // as Ctrl+Shift+F10
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
} // namespace settings
