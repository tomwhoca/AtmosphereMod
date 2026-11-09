// The performance report's measurement and the sim's settings file
// (addon/src/perf_report.h), without the sim: tools/test_perf_report.py.

#include "perf_report.h"

#include <windows.h>
#include <d3d12.h>

#include <cmath>
#include <cstdio>
#include <string>
#include <vector>

namespace
{
int g_failures = 0;

void check(bool ok, const char *what)
{
    std::printf("%s %s\n", ok ? "ok  " : "FAIL", what);
    g_failures += ok ? 0 : 1;
}

bool close_to(double a, double b, double tolerance)
{
    return std::abs(a - b) <= tolerance;
}

using namespace std::chrono;

// Frames of 10 ms with the mod on and 20 ms off; in the "off" part every 10th
// frame takes 50 ms. Returns the switch after each frame, in order.
struct Run
{
    std::vector<bool> switches;
    std::vector<double> progress;
    bool restored = false, restored_to = false;
};

Run run(perf::Measurement &m, bool initial)
{
    Run out;
    bool enabled = initial;
    auto t = perf::Clock::time_point{} + hours(1);
    m.start(t, enabled);
    for (int i = 0; i < 100000 && m.running(); ++i)
    {
        const bool spike = m.phase() == perf::Phase::off && i % 10 == 0;
        t += microseconds(spike ? 50000 : enabled ? 10000 : 20000);
        if (const auto want = m.frame(t))
            enabled = *want;
        if (const auto restore = m.take_restore())
        {
            enabled = *restore;
            out.restored = true;
            out.restored_to = *restore;
        }
        out.switches.push_back(enabled);
        out.progress.push_back(m.progress(t));
    }
    return out;
}
} // namespace

int main()
{
    {
        perf::Measurement m({1.0, 2.0});
        const Run r = run(m, false);
        check(m.phase() == perf::Phase::done, "a measurement runs to its end");
        check(r.restored && !r.restored_to && !r.switches.back(), "afterwards the switch is as it was (off)");
        // on, then off, then on again
        std::vector<bool> runs;
        for (size_t i = 0; i + 1 < r.switches.size(); ++i)
            if (runs.empty() || runs.back() != r.switches[i])
                runs.push_back(r.switches[i]);
        check(runs.size() == 3 && runs[0] && !runs[1] && runs[2], "the mod is on, off, then on again");
        const perf::Result &res = m.result();
        std::printf("     on %.2f fps (%.2f, %.2f), off %.2f fps, p99 on %.1f ms, off %.1f ms, drift %.3f\n",
                    res.fps_on(), res.on1.fps(), res.on2.fps(), res.fps_off(), std::max(res.on1.ms_p99, res.on2.ms_p99),
                    res.off.ms_p99, res.drift());
        check(close_to(res.fps_on(), 100.0, 0.5), "100 fps with the mod (10 ms frames)");
        // the "off" part: 9 frames of 20 ms and one of 50 ms in every 10
        check(close_to(res.fps_off(), 10.0 / (9 * 0.020 + 0.050), 1.5), "about 43 fps without it");
        check(close_to(res.off.ms_p99, 50.0, 0.01), "the 50 ms frames show in the 99th percentile");
        check(close_to(res.on1.ms_p99, 10.0, 0.01), "steady 10 ms frames: 10 ms");
        check(res.drift() < 0.01, "no drift between the two \"on\" parts");
        check(close_to(res.on1.seconds, 2.0, 0.03) && close_to(res.off.seconds, 2.0, 0.06), "each part is measured for 2 s");
        bool rising = true;
        for (size_t i = 1; i < r.progress.size(); ++i)
            rising = rising && r.progress[i] >= r.progress[i - 1];
        check(rising && r.progress.back() == 1.0, "progress only rises, to 1");
        check(!m.take_restore(), "the switch is restored once");
    }
    {
        perf::Measurement m({1.0, 2.0});
        const Run r = run(m, true);
        check(r.restored && r.restored_to && r.switches.back(), "started with the mod on: on afterwards");
    }
    {
        perf::Measurement m({1.0, 2.0});
        auto t = perf::Clock::time_point{} + hours(1);
        m.start(t, true);
        bool enabled = true;
        for (int i = 0; i < 450; ++i)  // 4.5 s: into the "off" part
        {
            t += milliseconds(10);
            if (const auto want = m.frame(t))
                enabled = *want;
        }
        check(m.phase() == perf::Phase::off && !enabled, "partway, the mod is off");
        const auto restore = m.stop();
        check(restore && *restore && m.phase() == perf::Phase::stopped, "stopping returns the switch it had (on)");
        check(!m.frame(t + milliseconds(10)) && !m.take_restore() && !m.stop(), "a stopped measurement does nothing");
        const std::string json = perf::state_json(m, t, "");
        check(json.find("\"phase\":\"stopped\"") != std::string::npos && json.find("result") == std::string::npos,
              "a stopped measurement has no result");
    }
    {
        const auto cfg = perf::parse_usercfg(
            "Version 66\r\n{Video\r\n\tAdapter \"NVIDIA GeForce RTX 5080\"\r\n\tAntiAliasing DLSS\r\n"
            "\tFullScreenResolution 3840 2160\r\n}\r\n{Graphics\r\n\tPreset Custom\r\n\t{VolumetricClouds\r\n"
            "\t\tEnabled 1\r\n\t\tQuality 3\r\n\t}\r\n\t{RaytracedShadows\r\n\t\tEnabled 1\r\n\t}\r\n}\r\n"
            "InstalledPackagesPath \"D:\\MSFS 2024\"\r\n");
        const auto get = [&](const char *key) { const auto it = cfg.find(key); return it != cfg.end() ? it->second : "?"; };
        check(get("Version") == "66", "top-level values");
        check(get("Video.Adapter") == "NVIDIA GeForce RTX 5080", "quoted values lose their quotes");
        check(get("Video.FullScreenResolution") == "3840 2160", "values with spaces");
        check(get("Graphics.VolumetricClouds.Quality") == "3" && get("Graphics.RaytracedShadows.Enabled") == "1",
              "nested sections");
        check(get("Graphics.Preset") == "Custom", "a section's values after a nested one closed");
        check(get("InstalledPackagesPath") == "D:\\MSFS 2024", "back at the top level after the sections");
    }
    {
        // MicrosoftGame.Config's <Identity> (as in the MS Store version's, 9 November 2026)
        check(perf::game_config_version(
                  "<?xml version=\"1.0\" encoding=\"utf-8\"?>\r\n<Game configVersion=\"1\">\r\n  <Identity "
                  "Name=\"Microsoft.Limitless\" Publisher=\"CN=Microsoft Corporation, O=Microsoft Corporation\" "
                  "Version=\"1.9.24.0\" />\r\n</Game>") == "1.9.24.0",
              "the sim's version from MicrosoftGame.Config");
        check(perf::game_config_version("<Game><Identity Name=\"x\" /><Other Version=\"1.0\" /></Game>").empty(),
              "a Version outside <Identity> doesn't count");
        check(perf::game_config_version("<Identity Version=\"1.9<script>\" />").empty() &&
                  perf::game_config_version("").empty(),
              "only digits and dots");
        std::printf("     sim_version() in this test program: \"%s\"\n", perf::sim_version().c_str());
    }
    {
        // This PC, as a report would have it (a device on the default adapter, as the sim's)
        ID3D12Device *device = nullptr;
        D3D12CreateDevice(nullptr, D3D_FEATURE_LEVEL_12_0, __uuidof(ID3D12Device), reinterpret_cast<void **>(&device));
        const std::string json = perf::system_json(reinterpret_cast<uint64_t>(device), nullptr, 3840, 2160, 0, 50);
        std::printf("     %s\n", json.c_str());
        check(device == nullptr || json.find("\"gpu\":\"\"") == std::string::npos, "the GPU's name is found");
        check(json.find("\"cpu\":\"\"") == std::string::npos, "the CPU's name is found");
        if (device)
            device->Release();
    }
    std::printf("%s\n", g_failures == 0 ? "all passed" : "FAILURES");
    return g_failures == 0 ? 0 : 1;
}
