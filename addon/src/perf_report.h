// Performance report: what the EFB app's "Performance report" sends (the app
// shows it first, and sends it only when the user taps Send; the add-on itself
// sends nothing).
//
//   AtmosphereMod.Settings  app -> add-on  {"measure":true} starts a measurement, {"measure":false} stops it
//   AtmosphereMod.State     add-on -> app  ..., "report":{...} (state_json below), once one was asked for
//
// The measurement switches the mod on and off itself, as Ctrl+Shift+F9 does,
// and times the frames (present to present) in each state: on, off, on again,
// each after a few seconds for the picture to settle, so a drift in the scene
// shows as a difference between the two "on" parts. Afterwards the switch is
// as it was. Ctrl+Shift+F9 or the app's own switch stops it.
//
// With it, the PC (GPU, driver, CPU, memory, Windows), how ReShade is loaded,
// and the sim's own graphics settings (its UserCfg.opt: anti-aliasing and
// upscaling, frame generation, render scaling, cloud quality, ray-traced
// shadows, ...). No user names, file paths or anything else that would say who
// sent it.

#pragma once

#include <chrono>
#include <cstdint>
#include <map>
#include <optional>
#include <string>
#include <vector>

namespace perf
{
using Clock = std::chrono::steady_clock;

// The parts of a measurement, in order.
enum class Phase
{
    idle,
    settle_on,
    on,
    settle_off,
    off,
    settle_on2,
    on2,
    done,
    stopped,  // by the user (or the switch was used meanwhile)
};

struct Timing
{
    double settle_s = 4.0;   // before each part: the picture and its temporal filters settle
    double measure_s = 8.0;  // each part's frames
};

// Frame times of one part.
struct Part
{
    uint32_t frames = 0;
    double seconds = 0.0;
    double fps() const { return seconds > 0.0 ? frames / seconds : 0.0; }
    double ms_p99 = 0.0;  // the frame time 99 % of the frames are faster than: stutters
};

struct Result
{
    Part on1, off, on2;
    double fps_on() const;   // both "on" parts together
    double fps_off() const { return off.fps(); }
    // How much the two "on" parts differ (0.05 = 5 %): over ~10 % the scene changed while measuring.
    double drift() const;
};

// The measurement's clock and switching; no sim, no GPU (tested in tools/lab/perf_report_test.cpp).
class Measurement
{
public:
    explicit Measurement(Timing timing = {}) : timing_(timing) {}

    // Starts (again); `enabled` is the switch as it is, restored at the end.
    void start(Clock::time_point now, bool enabled);
    // Stops it early; returns the switch to restore, if it was running.
    std::optional<bool> stop();
    // Each frame (present): the switch the mod should have now, while measuring.
    std::optional<bool> frame(Clock::time_point now);

    Phase phase() const { return phase_; }
    bool running() const { return phase_ != Phase::idle && phase_ != Phase::done && phase_ != Phase::stopped; }
    double progress(Clock::time_point now) const;  // 0..1
    const Result &result() const { return result_; }
    // The switch to restore once it has finished (once).
    std::optional<bool> take_restore();

private:
    void enter(Phase phase, Clock::time_point now);
    Part *part();

    Timing timing_;
    Phase phase_ = Phase::idle;
    Clock::time_point phase_start_{}, last_frame_{};
    bool have_last_ = false;
    bool restore_enabled_ = true;
    bool restore_due_ = false;
    std::vector<float> frame_ms_;
    Result result_;
};

// UserCfg.opt's values: "Video.AntiAliasing" -> "DLSS",
// "Graphics.VolumetricClouds.Quality" -> "3", ... (nested { } sections joined
// with dots, values with their quotes removed).
std::map<std::string, std::string> parse_usercfg(const std::string &text);

// The Version of the <Identity> in a MicrosoftGame.Config ("1.9.24.0"), or "".
std::string game_config_version(const std::string &text);

// The sim's version as its main menu shows it ("1.9.24.0"), from inside the
// sim: the MS Store package's version, else the exe's version resource, else
// the MicrosoftGame.Config next to the exe (the GDK's; also in the Steam
// version). "" if none of them has one.
std::string sim_version();

// The PC and the sim's settings, as the "system" and "sim" objects of the
// report's JSON. Present thread (once per measurement): `native_device` is the
// sim's ID3D12Device, `reshade_module` the ReShade that loaded the add-on,
// `output_w/h` the size of the picture ReShade sees, `effects_on` how many
// ReShade effects (techniques) are on.
std::string system_json(uint64_t native_device, void *reshade_module, uint32_t output_w, uint32_t output_h,
                        int effects_on, uint32_t register_space);

// The report's JSON for the app: the measurement's phase and progress, its
// result once done, and `system` (system_json, "" before the first start).
std::string state_json(const Measurement &m, Clock::time_point now, const std::string &system);
} // namespace perf
