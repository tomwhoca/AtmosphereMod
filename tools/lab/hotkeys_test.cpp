// The add-on's hotkeys (addon/src/hotkeys.cpp): keys as text, which keys can be hotkeys, the file, learning a
// key from the keyboard, and Glass Cockpit DLSS Fix's keys from its log. Run by tools/test_hotkeys.py; exits 1
// on a failure.

#include "hotkeys.h"

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

using namespace hotkeys;

Key key(uint8_t vk, bool ctrl, bool shift, bool alt)
{
    Key k;
    k.vk = vk;
    k.ctrl = ctrl;
    k.shift = shift;
    k.alt = alt;
    return k;
}

Press press(uint8_t vk, bool ctrl, bool shift, bool alt)
{
    Press p;
    p.vk = vk;
    p.ctrl = ctrl;
    p.shift = shift;
    p.alt = alt;
    return p;
}

void text_checks()
{
    const Keys d = defaults();
    check(text(d[kToggle]) == "Ctrl+Shift+F9" && text(d[kReload]) == "Ctrl+Shift+F10" &&
              text(d[kCapture]) == "Ctrl+Shift+F11" && text(d[kGpuTimes]) == "Ctrl+Shift+F12",
          "defaults: Ctrl+Shift+F9 .. F12, as before");
    check(d[kCloudFog].none() && text(d[kCloudFog]).empty(), "defaults: the in-cloud fog has none");
    check(text(key('G', true, false, true)) == "Ctrl+Alt+G", "text: Ctrl, Shift, Alt in order");
    check(text(key('7', false, true, true)) == "Shift+Alt+7", "text: a digit");
    for (const Key &k : {d[kToggle], key('G', true, false, true), key(0x70, false, false, true), key('0', true, true, true)})
        check(parse(text(k)) == k, ("parse(text) round trip: " + text(k)).c_str());
    check(parse(" ctrl + shift + f8 ") == key(0x77, true, true, false), "parse: case and spaces don't matter");
    check(parse("Shift+Ctrl+F8") == key(0x77, true, true, false), "parse: modifiers in any order");
    check(parse("") == Key{} && parse("none") == Key{} && parse("NONE") == Key{}, "parse: none");
    check(!parse("Ctrl+Ctrl+F8") && !parse("Ctrl+") && !parse("Ctrl+F13") && !parse("Win+F8") && !parse("Ctrl+Num 5"),
          "parse: nonsense is nothing");
    check(action("cloudFog") == kCloudFog && action("gpuTimes") == kGpuTimes && action("x") == -1, "action names");
}

void usable_checks()
{
    check(usable(key(0x77, false, true, false)) && usable(key(0x77, false, false, true)), "usable: Shift+F8, Alt+F8");
    check(!usable(key(0x77, false, false, false)), "not usable: F8 alone (the sim's)");
    check(usable(key('G', true, false, false)) && usable(key('G', false, false, true)), "usable: Ctrl+G, Alt+G");
    check(!usable(key('G', false, true, false)) && !usable(key('G', false, false, false)),
          "not usable: G, Shift+G (typing)");
    check(!usable(key(0x1B, true, true, false)), "not usable: other keys (Esc)");
    check(learnable().size() == 12 + 26 + 10, "learnable: F1-F12, A-Z, 0-9");
}

void file_checks()
{
    const std::filesystem::path dir = std::filesystem::temp_directory_path() / "atmo_hotkeys_test";
    std::filesystem::remove_all(dir);
    const std::filesystem::path file = dir / "hotkeys.ini";
    check(load(file) == defaults(), "load: no file, the defaults");
    Keys keys = defaults();
    keys[kCloudFog] = key(0x77, true, true, false);
    keys[kCapture] = Key{};
    check(save(file, keys), "save: creates the folder and the file");
    check(load(file) == keys, "load: what was saved (a key, and none)");
    {
        std::ofstream o(file, std::ios::trunc);
        o << "; comment\ntoggle = Alt+F9\nreload = F10\ncapture = Ctrl+Bogus\ngpuTimes=ctrl+alt+t\nmystery = Ctrl+F1\n";
    }
    const Keys read = load(file);
    check(read[kToggle] == key(0x78, false, false, true), "load: a key");
    check(read[kReload] == defaults()[kReload], "load: an unusable key keeps the default (F10 alone)");
    check(read[kCapture] == defaults()[kCapture], "load: nonsense keeps the default");
    check(read[kGpuTimes] == key('T', true, false, true), "load: no spaces, lower case");
    check(read[kCloudFog].none(), "load: an action the file lacks keeps its default");
    check(json(defaults()) == R"({"toggle":"Ctrl+Shift+F9","reload":"Ctrl+Shift+F10","capture":"Ctrl+Shift+F11",)"
                              R"("gpuTimes":"Ctrl+Shift+F12","cloudFog":""})",
          "json: the app's state");
    std::filesystem::remove_all(dir);
}

void learner_checks()
{
    Keys keys = defaults();
    Learner l;
    check(l.action() == -1 && !l.step(keys, press(0x77, true, true, false), 0.0), "learner: idle, keys do nothing");
    l.start(kCloudFog, 0.0);
    check(l.action() == kCloudFog && l.note().empty(), "learner: learning the in-cloud fog's");
    check(!l.step(keys, Press{}, 1.0) && l.action() == kCloudFog, "learner: nothing pressed, waits");
    check(!l.step(keys, press('G', false, true, false), 2.0) && l.note() == "unusable:Shift+G" &&
              l.action() == kCloudFog,
          "learner: Shift+G refused, waits on");
    check(!l.step(keys, press(0x79, true, true, false), 3.0) && l.note() == "taken:reload" && l.action() == kCloudFog,
          "learner: Ctrl+Shift+F10 is reload's: refused, waits on");
    check(l.step(keys, press(0x77, true, true, false), 4.0) && keys[kCloudFog] == key(0x77, true, true, false) &&
              l.note() == "set:cloudFog" && l.action() == -1,
          "learner: Ctrl+Shift+F8 set, done");
    l.start(kCloudFog, 10.0);
    check(!l.step(keys, press(0x77, true, true, false), 11.0) && l.note() == "set:cloudFog",
          "learner: the same keys again: set, nothing to save");
    l.start(kToggle, 20.0);
    Press esc;
    esc.escape = true;
    check(!l.step(keys, esc, 21.0) && l.note() == "cancelled" && l.action() == -1 &&
              keys[kToggle] == defaults()[kToggle],
          "learner: Esc cancels, the key stays");
    l.start(kCapture, 30.0);
    Press back;
    back.backspace = true;
    check(l.step(keys, back, 31.0) && keys[kCapture].none() && l.note() == "cleared:capture", "learner: Backspace: none");
    l.start(kGpuTimes, 40.0);
    check(!l.step(keys, Press{}, 40.0 + Learner::kTimeoutSeconds - 0.1) && l.action() == kGpuTimes,
          "learner: waits until the timeout");
    check(!l.step(keys, Press{}, 40.0 + Learner::kTimeoutSeconds) && l.note() == "timeout" && l.action() == -1,
          "learner: then stops");
    l.start(kGpuTimes, 50.0);
    check(!l.step(keys, press('G', false, false, false), 50.0 + Learner::kTimeoutSeconds - 1.0) &&
              !l.step(keys, Press{}, 50.0 + Learner::kTimeoutSeconds + 1.0) && l.action() == kGpuTimes,
          "learner: a refused key restarts the wait");
    l.cancel();
    check(l.note() == "cancelled" && l.action() == -1, "learner: cancel()");
    l.start(-5, 0.0);
    check(l.action() == -1, "learner: no such action");
}

void glass_fix_checks()
{
    GlassFix none;
    auto o = none.others();
    check(o.size() == 2 && o[0].keys == "Ctrl+Shift+F9" && o[1].keys == "Ctrl+Shift+F11" &&
              o[0].addon == "Glass Cockpit DLSS Fix",
          "glass fix: its defaults without a log");
    GlassFix g;
    g.scan("18:57:34 Glass Cockpit DLSS Fix 1.1.0 (built Nov  5 2026 15:01:28)\r\n"
           "18:57:34 hotkeys: Ctrl+Alt+F9 overlay on/off, Ctrl+Shift+F11 green tint\r\n"
           "19:17:06 setting tint=1\r\n");
    o = g.others();
    check(o[0].keys == "Ctrl+Alt+F9" && o[1].keys == "Ctrl+Shift+F11", "glass fix: its startup line");
    g.scan("19:20:00 setting green_tint hot");  // (the rest of the line comes later)
    check(g.others()[1].keys == "Ctrl+Shift+F11", "glass fix: a part line waits");
    g.scan("key=Shift+Alt+T\r\n19:20:01 setting fix_on_off hotkey=\r\n");
    o = g.others();
    check(o[1].keys == "Shift+Alt+T" && o[0].keys.empty(), "glass fix: changes after it (and none)");
    g.scan("19:21:00 setting green_tint hotkey=Ctrl+Num 5\n");
    check(g.others()[1].keys == "Ctrl+Num 5", "glass fix: a key the mod can't name, as its log has it");
    check(json(g.others()) == R"([{"addon":"Glass Cockpit DLSS Fix","what":"its overlay on/off","keys":""},)"
                              R"({"addon":"Glass Cockpit DLSS Fix","what":"its green tint","keys":"Ctrl+Num 5"}])",
          "json: the other add-on's keys");
    std::vector<Other> quoted = {{"A \"q\"", "w\\x", "k"}};
    check(json(quoted) == R"([{"addon":"A \"q\"","what":"w\\x","keys":"k"}])", "json: quotes escaped");
}
} // namespace

int main()
{
    text_checks();
    usable_checks();
    file_checks();
    learner_checks();
    glass_fix_checks();
    std::printf(g_failures ? "%d FAILED\n" : "all passed\n", g_failures);
    return g_failures ? 1 : 0;
}
