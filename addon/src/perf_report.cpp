// Performance report (perf_report.h).

#include "perf_report.h"

#include <windows.h>
#include <d3d12.h>
#include <dxgi1_6.h>
#include <psapi.h>

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <sstream>

namespace perf
{
namespace
{
namespace fs = std::filesystem;

constexpr bool phase_on(Phase p)
{
    return p == Phase::settle_on || p == Phase::on || p == Phase::settle_on2 || p == Phase::on2;
}

constexpr bool phase_measures(Phase p)
{
    return p == Phase::on || p == Phase::off || p == Phase::on2;
}

const char *phase_name(Phase p)
{
    switch (p)
    {
    case Phase::idle: return "idle";
    case Phase::settle_on: return "settleOn";
    case Phase::on: return "on";
    case Phase::settle_off: return "settleOff";
    case Phase::off: return "off";
    case Phase::settle_on2: return "settleOn2";
    case Phase::on2: return "on2";
    case Phase::done: return "done";
    case Phase::stopped: return "stopped";
    }
    return "idle";
}

double seconds(Clock::duration d)
{
    return std::chrono::duration<double>(d).count();
}

// A JSON string, quotes included.
std::string json_quote(const std::string &text)
{
    std::string out = "\"";
    for (const unsigned char c : text)
    {
        if (c == '"' || c == '\\')
            out += std::string("\\") + char(c);
        else if (c < 0x20)
        {
            char buf[8];
            snprintf(buf, sizeof(buf), "\\u%04x", c);
            out += buf;
        }
        else
            out += char(c);
    }
    return out + "\"";
}

std::string number(double v, int decimals = 2)
{
    if (!std::isfinite(v))
        return "null";
    char buf[48];
    snprintf(buf, sizeof(buf), "%.*f", decimals, v);
    return buf;
}

std::string utf8(const std::wstring &w)
{
    if (w.empty())
        return {};
    const int n = WideCharToMultiByte(CP_UTF8, 0, w.c_str(), int(w.size()), nullptr, 0, nullptr, nullptr);
    std::string out(size_t(std::max(n, 0)), '\0');
    WideCharToMultiByte(CP_UTF8, 0, w.c_str(), int(w.size()), out.data(), n, nullptr, nullptr);
    return out;
}

std::wstring widen(const std::string &s)
{
    if (s.empty())
        return {};
    const int n = MultiByteToWideChar(CP_UTF8, 0, s.c_str(), int(s.size()), nullptr, 0);
    std::wstring out(size_t(std::max(n, 0)), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.c_str(), int(s.size()), out.data(), n);
    return out;
}

std::string trim(std::string s)
{
    const auto space = [](unsigned char c) { return std::isspace(c) != 0; };
    s.erase(s.begin(), std::find_if_not(s.begin(), s.end(), space));
    s.erase(std::find_if_not(s.rbegin(), s.rend(), space).base(), s.end());
    return s;
}

std::string lower(std::string s)
{
    std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return char(std::tolower(c)); });
    return s;
}

std::wstring env(const wchar_t *name)
{
    wchar_t buf[MAX_PATH];
    const DWORD n = GetEnvironmentVariableW(name, buf, MAX_PATH);
    return n > 0 && n < MAX_PATH ? std::wstring(buf, n) : std::wstring();
}

std::string registry_string(HKEY root, const wchar_t *key, const wchar_t *value)
{
    wchar_t buf[512];
    DWORD size = sizeof(buf);
    if (RegGetValueW(root, key, value, RRF_RT_REG_SZ, nullptr, buf, &size) != ERROR_SUCCESS)
        return {};
    return trim(utf8(buf));
}

struct Gpu
{
    std::string name, vendor, driver;
    double vram_gb = 0.0;
};

// The driver version as the vendor numbers it: NVIDIA 32.0.15.6094 -> 560.94; others as Windows has it.
std::string vendor_driver(UINT vendor_id, unsigned a, unsigned b, unsigned c, unsigned d)
{
    char buf[64];
    if (vendor_id == 0x10DE)
    {
        const unsigned v = (c % 10) * 10000 + d;
        snprintf(buf, sizeof(buf), "%u.%02u", v / 100, v % 100);
    }
    else
        snprintf(buf, sizeof(buf), "%u.%u.%u.%u", a, b, c, d);
    return buf;
}

// The driver's version from the display adapters' registry keys, for the adapter of that name.
std::string registry_driver(const std::wstring &description, UINT vendor_id)
{
    const wchar_t *kClass = L"SYSTEM\\CurrentControlSet\\Control\\Class\\{4d36e968-e325-11ce-bfc1-08002be10318}";
    HKEY cls;
    if (RegOpenKeyExW(HKEY_LOCAL_MACHINE, kClass, 0, KEY_READ, &cls) != ERROR_SUCCESS)
        return {};
    std::string found;
    wchar_t sub[64];
    for (DWORD i = 0; found.empty(); ++i)
    {
        DWORD len = 64;
        if (RegEnumKeyExW(cls, i, sub, &len, nullptr, nullptr, nullptr, nullptr) != ERROR_SUCCESS)
            break;
        wchar_t desc[256], version[64];
        DWORD dsize = sizeof(desc), vsize = sizeof(version);
        if (RegGetValueW(cls, sub, L"DriverDesc", RRF_RT_REG_SZ, nullptr, desc, &dsize) == ERROR_SUCCESS &&
            description == desc &&
            RegGetValueW(cls, sub, L"DriverVersion", RRF_RT_REG_SZ, nullptr, version, &vsize) == ERROR_SUCCESS)
        {
            unsigned a = 0, b = 0, c = 0, d = 0;
            if (swscanf_s(version, L"%u.%u.%u.%u", &a, &b, &c, &d) == 4)
                found = vendor_driver(vendor_id, a, b, c, d);
        }
    }
    RegCloseKey(cls);
    return found;
}

Gpu gpu(uint64_t native_device)
{
    Gpu out;
    auto *const device = reinterpret_cast<ID3D12Device *>(native_device);
    if (device == nullptr)
        return out;
    const LUID luid = device->GetAdapterLuid();
    IDXGIFactory4 *factory = nullptr;
    if (FAILED(CreateDXGIFactory1(__uuidof(IDXGIFactory4), reinterpret_cast<void **>(&factory))))
        return out;
    IDXGIAdapter1 *adapter = nullptr;
    if (SUCCEEDED(factory->EnumAdapterByLuid(luid, __uuidof(IDXGIAdapter1), reinterpret_cast<void **>(&adapter))))
    {
        DXGI_ADAPTER_DESC1 desc = {};
        if (SUCCEEDED(adapter->GetDesc1(&desc)))
        {
            out.name = trim(utf8(desc.Description));
            out.vram_gb = double(desc.DedicatedVideoMemory) / (1024.0 * 1024.0 * 1024.0);
            out.vendor = desc.VendorId == 0x10DE ? "NVIDIA" : desc.VendorId == 0x1002 ? "AMD"
                         : desc.VendorId == 0x8086 ? "Intel" : "other";
            LARGE_INTEGER v = {};
            if (SUCCEEDED(adapter->CheckInterfaceSupport(__uuidof(IDXGIDevice), &v)))
                out.driver = vendor_driver(desc.VendorId, HIWORD(v.HighPart), LOWORD(v.HighPart), HIWORD(v.LowPart),
                                           LOWORD(v.LowPart));
            else
                out.driver = registry_driver(desc.Description, desc.VendorId);
        }
        adapter->Release();
    }
    factory->Release();
    return out;
}

std::string windows_version()
{
    using RtlGetVersionFn = LONG(WINAPI *)(OSVERSIONINFOW *);
    OSVERSIONINFOW info = {sizeof(info)};
    const HMODULE ntdll = GetModuleHandleW(L"ntdll.dll");
    const auto get = ntdll ? reinterpret_cast<RtlGetVersionFn>(GetProcAddress(ntdll, "RtlGetVersion")) : nullptr;
    if (get == nullptr || get(&info) != 0)
        return "";
    const bool eleven = info.dwMajorVersion == 10 && info.dwBuildNumber >= 22000;
    return std::string(eleven ? "Windows 11" : info.dwMajorVersion == 10 ? "Windows 10" : "Windows") + " (build " +
           std::to_string(info.dwBuildNumber) + ")";
}

std::wstring module_path(HMODULE module)
{
    wchar_t buf[MAX_PATH];
    const DWORD n = GetModuleFileNameW(module, buf, MAX_PATH);
    return n > 0 && n < MAX_PATH ? std::wstring(buf, n) : std::wstring();
}

std::string file_version(const std::wstring &path)
{
    DWORD handle = 0;
    const DWORD size = GetFileVersionInfoSizeW(path.c_str(), &handle);
    if (size == 0)
        return {};
    std::vector<uint8_t> data(size);
    VS_FIXEDFILEINFO *info = nullptr;
    UINT len = 0;
    if (!GetFileVersionInfoW(path.c_str(), 0, size, data.data()) ||
        !VerQueryValueW(data.data(), L"\\", reinterpret_cast<void **>(&info), &len) || info == nullptr)
        return {};
    return std::to_string(HIWORD(info->dwFileVersionMS)) + "." + std::to_string(LOWORD(info->dwFileVersionMS)) + "." +
           std::to_string(HIWORD(info->dwFileVersionLS)) + "." + std::to_string(LOWORD(info->dwFileVersionLS));
}

// The ReShade add-ons loaded besides this one (file names only).
std::vector<std::string> other_addons()
{
    std::vector<std::string> out;
    HMODULE modules[1024];
    DWORD needed = 0;
    if (!EnumProcessModules(GetCurrentProcess(), modules, sizeof(modules), &needed))
        return out;
    HMODULE self = nullptr;
    GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                       reinterpret_cast<LPCWSTR>(&other_addons), &self);
    for (size_t i = 0; i < std::min<size_t>(needed / sizeof(HMODULE), 1024); ++i)
    {
        if (modules[i] == self)
            continue;
        const fs::path path(module_path(modules[i]));
        const std::string ext = lower(utf8(path.extension().wstring()));
        if (ext == ".addon64" || ext == ".addon")
            out.push_back(utf8(path.filename().wstring()));
    }
    std::sort(out.begin(), out.end());
    return out;
}

// Community packages known to change the clouds or their look (folder names, lower case).
std::vector<std::string> community_packages(const std::string &installed_packages)
{
    std::vector<std::string> out;
    if (installed_packages.empty())
        return out;
    std::error_code ec;
    const fs::path dir = fs::path(widen(installed_packages)) / L"Community";
    for (fs::directory_iterator it(dir, ec), end; !ec && it != end; it.increment(ec))
    {
        const std::string name = lower(utf8(it->path().filename().wstring()));
        if (name.find("tritri") != std::string::npos || name.find("cloudsurf") != std::string::npos)
            out.push_back(name);
    }
    std::sort(out.begin(), out.end());
    return out;
}

std::string list_json(const std::vector<std::string> &items)
{
    std::string s = "[";
    for (const std::string &item : items)
        s += (s.size() > 1 ? "," : "") + json_quote(item);
    return s + "]";
}
} // namespace

double Result::fps_on() const
{
    const double s = on1.seconds + on2.seconds;
    return s > 0.0 ? (on1.frames + on2.frames) / s : 0.0;
}

double Result::drift() const
{
    const double a = on1.fps(), b = on2.fps();
    return a + b > 0.0 ? std::abs(a - b) / ((a + b) / 2.0) : 0.0;
}

void Measurement::start(Clock::time_point now, bool enabled)
{
    restore_enabled_ = running() ? restore_enabled_ : enabled;
    restore_due_ = false;
    result_ = {};
    enter(Phase::settle_on, now);
}

std::optional<bool> Measurement::stop()
{
    if (!running())
        return std::nullopt;
    phase_ = Phase::stopped;
    restore_due_ = false;
    frame_ms_.clear();
    return restore_enabled_;
}

void Measurement::enter(Phase phase, Clock::time_point now)
{
    phase_ = phase;
    phase_start_ = now;
    have_last_ = false;
    frame_ms_.clear();
}

Part *Measurement::part()
{
    return phase_ == Phase::on ? &result_.on1 : phase_ == Phase::off ? &result_.off
                                              : phase_ == Phase::on2 ? &result_.on2 : nullptr;
}

std::optional<bool> Measurement::frame(Clock::time_point now)
{
    if (!running())
        return std::nullopt;
    if (Part *p = part())
    {
        if (have_last_)
        {
            const double dt = seconds(now - last_frame_);
            p->frames += 1;
            p->seconds += dt;
            frame_ms_.push_back(float(dt * 1000.0));
        }
        last_frame_ = now;
        have_last_ = true;
    }
    const double length = phase_measures(phase_) ? timing_.measure_s : timing_.settle_s;
    if (seconds(now - phase_start_) >= length)
    {
        if (Part *p = part(); p != nullptr && !frame_ms_.empty())
        {
            std::vector<float> sorted = frame_ms_;
            const size_t k = std::min(sorted.size() - 1, size_t(std::ceil(0.99 * double(sorted.size()))) - 1);
            std::nth_element(sorted.begin(), sorted.begin() + k, sorted.end());
            p->ms_p99 = sorted[k];
        }
        const Phase next = phase_ == Phase::settle_on ? Phase::on : phase_ == Phase::on ? Phase::settle_off
                           : phase_ == Phase::settle_off ? Phase::off : phase_ == Phase::off ? Phase::settle_on2
                           : phase_ == Phase::settle_on2 ? Phase::on2 : Phase::done;
        enter(next, now);
        if (next == Phase::done)
        {
            restore_due_ = true;
            return std::nullopt;
        }
    }
    return phase_on(phase_);
}

double Measurement::progress(Clock::time_point now) const
{
    if (phase_ == Phase::done)
        return 1.0;
    if (!running())
        return 0.0;
    const int index = int(phase_) - int(Phase::settle_on);  // 0..5
    const double before = (index / 2) * (timing_.settle_s + timing_.measure_s) + (index % 2) * timing_.settle_s;
    const double length = phase_measures(phase_) ? timing_.measure_s : timing_.settle_s;
    const double total = 3.0 * (timing_.settle_s + timing_.measure_s);
    return std::clamp((before + std::min(seconds(now - phase_start_), length)) / total, 0.0, 1.0);
}

std::optional<bool> Measurement::take_restore()
{
    if (!restore_due_)
        return std::nullopt;
    restore_due_ = false;
    return restore_enabled_;
}

std::map<std::string, std::string> parse_usercfg(const std::string &text)
{
    std::map<std::string, std::string> out;
    std::vector<std::string> sections;
    std::istringstream in(text);
    std::string line;
    while (std::getline(in, line))
    {
        line = trim(line);
        if (line.empty())
            continue;
        if (line[0] == '{')
        {
            sections.push_back(trim(line.substr(1)));
            continue;
        }
        if (line[0] == '}')
        {
            if (!sections.empty())
                sections.pop_back();
            continue;
        }
        const size_t space = line.find_first_of(" \t");
        std::string key = line.substr(0, space);
        std::string value = space == std::string::npos ? "" : trim(line.substr(space));
        if (value.size() >= 2 && value.front() == '"' && value.back() == '"')
            value = value.substr(1, value.size() - 2);
        std::string path;
        for (const std::string &s : sections)
            path += s + ".";
        out[path + key] = value;
    }
    return out;
}

std::string system_json(uint64_t native_device, void *reshade_module, uint32_t output_w, uint32_t output_h,
                        int effects_on, uint32_t register_space)
{
    const Gpu g = gpu(native_device);
    const std::string cpu =
        registry_string(HKEY_LOCAL_MACHINE, L"HARDWARE\\DESCRIPTION\\System\\CentralProcessor\\0", L"ProcessorNameString");
    ULONGLONG ram_kb = 0;
    GetPhysicallyInstalledSystemMemory(&ram_kb);

    // The sim: from where it runs, its settings file.
    const std::string exe = lower(utf8(module_path(nullptr)));
    const bool store = exe.find("\\windowsapps\\") != std::string::npos;
    const bool steam = exe.find("steamapps") != std::string::npos;
    const fs::path store_cfg = fs::path(env(L"LOCALAPPDATA")) / L"Packages" / L"Microsoft.Limitless_8wekyb3d8bbwe" /
                               L"LocalCache" / L"UserCfg.opt";
    const fs::path steam_cfg = fs::path(env(L"APPDATA")) / L"Microsoft Flight Simulator 2024" / L"UserCfg.opt";
    std::error_code ec;
    fs::path cfg_path = store ? store_cfg : steam ? steam_cfg : fs::exists(store_cfg, ec) ? store_cfg : steam_cfg;
    std::map<std::string, std::string> cfg;
    if (std::ifstream file(cfg_path, std::ios::binary); file)
        cfg = parse_usercfg(std::string(std::istreambuf_iterator<char>(file), std::istreambuf_iterator<char>()));
    static const char *const kKeys[] = {
        "Video.Windowed", "Video.FullscreenBorderless", "Video.FullScreenResolution", "Video.AntiAliasing",
        "Video.DLSSMode", "Video.FSRMode", "Video.FrameGeneration", "Video.NBFramesToGenerate",
        "Video.PrimaryScaling", "Video.SecondaryScaling", "Video.DynamicSettings", "Video.TargetFrameRate",
        "Video.VSync", "Video.FrameLimiter", "Video.Reflex", "Video.HDR10",
        "Video.AntiAliasingVR", "Video.DLSSModeVR", "Video.FSRModeVR", "Video.FrameGenerationVR",
        "Video.PrimaryScalingVR", "Video.DynamicSettingsVR",
        "Graphics.Preset", "Graphics.VolumetricClouds.Enabled", "Graphics.VolumetricClouds.Quality",
        "Graphics.RaytracedShadows.Enabled", "Graphics.Terrain.LoDFactor", "Graphics.ObjectsLoD.LoDFactor",
        "GraphicsVR.Preset", "GraphicsVR.VolumetricClouds.Quality", "GraphicsVR.RaytracedShadows.Enabled",
    };
    std::string sim_cfg = "{";
    for (const char *key : kKeys)
        if (const auto it = cfg.find(key); it != cfg.end())
            sim_cfg += (sim_cfg.size() > 1 ? "," : "") + json_quote(key) + ":" + json_quote(it->second);
    sim_cfg += "}";
    const auto packages = cfg.find("InstalledPackagesPath");

    const fs::path reshade(module_path(static_cast<HMODULE>(reshade_module)));
    const std::string loader = utf8(reshade.filename().wstring());

    return std::string("{\"pc\":{\"gpu\":") + json_quote(g.name) + ",\"gpuVendor\":" + json_quote(g.vendor) +
           ",\"vramGB\":" + number(g.vram_gb, 1) + ",\"driver\":" + json_quote(g.driver) + ",\"cpu\":" + json_quote(cpu) +
           ",\"threads\":" + std::to_string(GetActiveProcessorCount(ALL_PROCESSOR_GROUPS)) +
           ",\"ramGB\":" + std::to_string(int(std::lround(double(ram_kb) / (1024.0 * 1024.0)))) +
           ",\"windows\":" + json_quote(windows_version()) + "},\"sim\":{\"store\":" +
           json_quote(store ? "Microsoft Store" : steam ? "Steam" : "") + ",\"output\":" +
           json_quote(std::to_string(output_w) + "x" + std::to_string(output_h)) + ",\"cfg\":" + sim_cfg +
           ",\"packages\":" + list_json(community_packages(packages != cfg.end() ? packages->second : "")) +
           "},\"reshade\":{\"loader\":" + json_quote(loader) + ",\"version\":" + json_quote(file_version(reshade.wstring())) +
           ",\"effectsOn\":" + std::to_string(effects_on) + ",\"addons\":" + list_json(other_addons()) +
           ",\"registerSpace\":" + std::to_string(register_space) + "}}";
}

std::string state_json(const Measurement &m, Clock::time_point now, const std::string &system)
{
    std::string s = std::string("{\"phase\":\"") + phase_name(m.phase()) + "\",\"progress\":" +
                    number(m.progress(now), 3);
    if (m.phase() == Phase::done)
    {
        const Result &r = m.result();
        const double on = r.fps_on(), off = r.fps_off();
        s += ",\"result\":{\"fpsOn\":" + number(on) + ",\"fpsOff\":" + number(off) +
             ",\"msOn\":" + number(on > 0 ? 1000.0 / on : 0.0) + ",\"msOff\":" + number(off > 0 ? 1000.0 / off : 0.0) +
             ",\"p99On\":" + number(std::max(r.on1.ms_p99, r.on2.ms_p99)) + ",\"p99Off\":" + number(r.off.ms_p99) +
             ",\"fpsOn1\":" + number(r.on1.fps()) + ",\"fpsOn2\":" + number(r.on2.fps()) +
             ",\"drift\":" + number(r.drift(), 3) + ",\"framesOn\":" + std::to_string(r.on1.frames + r.on2.frames) +
             ",\"framesOff\":" + std::to_string(r.off.frames) + "}";
    }
    if (!system.empty())
        s += ",\"system\":" + system;
    return s + "}";
}
} // namespace perf
