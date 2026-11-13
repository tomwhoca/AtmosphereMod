// The add-on's hotkeys, which the EFB app's Hotkeys tab changes (settings_bridge.h: learnHotkey, setHotkey):
// each action's key with its modifiers, as text ("Ctrl+Shift+F9", "" for none), kept in <DataPath>/hotkeys.ini
// (outside tunables.ini and the presets: keys belong to the PC, not to a look; and outside ReShade.ini, which
// OptiShade rewrites). A new key is learned from the keyboard: the app asks for it, the add-on takes the next
// key pressed through ReShade, as the hotkeys themselves are read, so a key it learns is one it sees.
//
// Glass Cockpit DLSS Fix (another ReShade add-on, from OptiShade's components) has Ctrl+Shift+F9 and
// Ctrl+Shift+F11 by default too: its keys are read from its log, and the app warns where they are the mod's.

#pragma once

#include <array>
#include <cstdint>
#include <filesystem>
#include <optional>
#include <string>
#include <vector>

namespace hotkeys
{
// The actions, by index; their names in the app's messages and the file.
enum Action
{
    kToggle,    // the mod on or off (stock shaders, to compare)
    kReload,    // tunables.ini and the shaders reloaded
    kCapture,   // the constants the replacements see, and the draws (developers)
    kGpuTimes,  // the draws' GPU times (developers)
    kCloudFog,  // the in-cloud fog off for now, and on again (not saved: on at every start)
    kActions,
};
constexpr const char *kNames[kActions] = {"toggle", "reload", "capture", "gpuTimes", "cloudFog"};

// The action of that name, or -1.
int action(const std::string &name);

// A key and its modifiers; vk 0: none. Exactly these modifiers must be down (Ctrl+Shift+F9 isn't
// Ctrl+Shift+Alt+F9).
struct Key
{
    uint8_t vk = 0;
    bool ctrl = false, shift = false, alt = false;

    bool none() const { return vk == 0; }
    bool operator==(const Key &) const = default;
};
using Keys = std::array<Key, kActions>;

// Ctrl+Shift+F9 .. F12 as before; the in-cloud fog's none (so it can't take a key anything else has).
Keys defaults();

// The keys a hotkey can be: F1-F12, A-Z, 0-9 (virtual-key codes).
const std::vector<uint8_t> &learnable();

// "Ctrl+Shift+F9" (Ctrl, Shift, Alt in that order), "" for none.
std::string text(const Key &key);

// Text as text() writes it (case and spaces don't matter; "none" and "" are none). Nothing if it isn't a
// key text() could have written.
std::optional<Key> parse(const std::string &text);

// Whether a key can be a hotkey: F1-F12 with any of Ctrl, Shift, Alt (alone they're the sim's); a letter or
// digit with Ctrl or Alt (with Shift alone it's typing).
bool usable(const Key &key);

// The file's keys: lines "<name> = <text>"; an action the file lacks, or doesn't have a usable key for,
// keeps its default.
Keys load(const std::filesystem::path &file);
bool save(const std::filesystem::path &file, const Keys &keys);

// For the app's state: {"toggle":"Ctrl+Shift+F9",...}
std::string json(const Keys &keys);

// The keyboard in a frame while a key is learned: the first learnable key pressed (0 for none), the
// modifiers down, and Esc (cancel) and Backspace (no key).
struct Press
{
    uint8_t vk = 0;
    bool ctrl = false, shift = false, alt = false;
    bool escape = false, backspace = false;
};

// Learning a key for one action. The note says how the last one ended, for the app: "set:<action>",
// "cleared:<action>", "cancelled", "timeout", "unusable:<keys>" (it waits on), "taken:<action>" (the mod's
// other action has those keys; it waits on).
class Learner
{
public:
    static constexpr double kTimeoutSeconds = 15.0;

    void start(int action, double now);
    void cancel();
    int action() const { return action_; }  // -1 while none is learned
    const std::string &note() const { return note_; }
    void set_note(std::string note) { note_ = std::move(note); }
    // A frame's keys: true when `keys` changed (save them).
    bool step(Keys &keys, const Press &press, double now);

private:
    int action_ = -1;
    double since_ = 0.0;
    std::string note_;
};

// Another add-on's hotkey: the add-on, what the key does there, and the key as text() writes it (as its own
// log has it, if text() can't).
struct Other
{
    std::string addon, what, keys;
};

// Glass Cockpit DLSS Fix's hotkeys, from its log (glass-cockpit-dlss-fix.log next to it), read as it grows:
// its startup line "hotkeys: <keys> overlay on/off, <keys> green tint" and the changes after it ("setting
// fix_on_off hotkey=<keys>", "setting green_tint hotkey=<keys>"); its defaults until the log has them.
class GlassFix
{
public:
    // More of the log: whole lines (a part line waits for the rest).
    void scan(const std::string &text);
    std::vector<Other> others() const;

private:
    std::string keys_[2] = {"Ctrl+Shift+F9", "Ctrl+Shift+F11"};  // its defaults
    std::string part_;
};
constexpr const char *kGlassFixModule = "glass-cockpit-dlss-fix.addon64";
constexpr const char *kGlassFixLog = "glass-cockpit-dlss-fix.log";

// For the app's state: [{"addon":"...","what":"...","keys":"..."},...]
std::string json(const std::vector<Other> &others);
} // namespace hotkeys
