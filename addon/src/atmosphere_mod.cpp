// AtmosphereMod: ReShade add-on for MSFS 2024.
//
//  - logs every pipeline the game creates, by CRC32 of its shaders (joined
//    against shader_names.tsv written by tools/build_shaders.py), plus root
//    signature layouts and dispatch sizes of the atmosphere and weather-volume
//    passes, draws of the sim's radar shaders, and every distinct 3D texture
//  - swaps compute pipelines for replacements (<DataPath>/shaders/0x<crc>.cso)
//    at bind time, so they can be toggled and hot-reloaded in game
//  - does the same for graphics pipelines whose pixel shader has a
//    replacement (the patched ps_deferredRendering, tools/patch_dxil.py, and
//    an optional ph_rain_radar_horz replacement). The copy is built from the
//    game's own pipeline description when it is created, so F10 reloads only
//    its tunables, not its shader.
//  - appends our own parameters to the game's global compute root signature:
//    16 root constants at b0 space50 (ModParams: tunables + capture flag), a
//    root UAV at u0 space50 (debug buffer) and 16 root constants at b1 space50
//    (cloud tunables C0..C15). Replacement shaders write the game constants
//    they see into the debug buffer, which is read back on capture. The global
//    graphics root signature gets the first two parameters (the radar replica
//    captures from its pixel shader).
//
// Hotkeys, all with Ctrl+Shift:
//  F9  toggle replacements (A/B against stock)
//  F10 reload replacement shaders and tunables.ini from disk
//  F11 capture the constants our replacements see
//
// Output goes to <DataPath>/logs. DataPath comes from [ATMOSPHEREMOD] DataPath
// in ReShade.ini, defaulting to an AtmosphereMod folder next to this add-on.
// The root-signature change is opt-in: [ATMOSPHEREMOD] InjectRootSignature=1
// (compute), plus InjectGraphicsRootSignature=1 for the graphics one.
// (An early version crashed the sim at startup, so it stays opt-in.)

#include <reshade.hpp>
#include <d3d12.h>

#include "cloud_stations.h"
#include "ozone_climatology.h"
#include "settings_bridge.h"
#include "weather_feed.h"

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cinttypes>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <mutex>
#include <shared_mutex>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

using namespace reshade::api;
namespace fs = std::filesystem;

extern "C" __declspec(dllexport) const char *NAME = "AtmosphereMod";
extern "C" __declspec(dllexport) const char *DESCRIPTION =
    "Experimental atmosphere and clouds for MSFS 2024: layered haze, sky colours, cloud lighting and "
    "METAR-driven clouds. Ctrl+Shift+F9 switches to stock.";

namespace
{
// Passes logged in detail: dispatch and draw sizes (dispatches.tsv) and rates
// (dispatch_rates.log).
const std::unordered_set<std::string> kWatchedEntries = {
    // atmosphere
    "ch_transmittance_lut", "ch_multi_scatt", "ch_sky_view", "ch_camera_volume",
    "ch_sky", "ch_atmosphereLight", "ch_sunMoonExtinctionMap",
    "ph_globalCloudAtmospherics", "cs_compute_scattered_light_and_extinction",
    "cs_integrate_scattering_and_transmittance", "ch_volumetric_clouds_render",
    // the sim's own fog/haze, which may overlap ours
    "ch_raymarched_fog_generate_fogmap", "ch_raymarched_fog_render", "ch_raymarched_fog_reproject",
    // the sun's shadow maps (for the aircraft's shadow on clouds)
    "ch_deferred_shadows",
    // ... and the sim's other shadow passes: which one draws the aircraft's own shadow?
    "ch_raymarched_shadows", "ch_raymarched_shadows_accumulate", "ch_indirect_capsule_shadowing", "ch_shadow",
    "cs_directional_shadow_accumulate", "ch_shadow_map_min_max", "ch_shadow_map_min_max_array",
    "ch_shadow_map_min_max_cube",
    "cs_gather_participating_media_properties", "cs_fill_extinction_clipmap",
    // the weather volume and its derived products
    "ch_cloudDensity", "ch_cloudMips", "ch_cloudLightMap",
    // the sim's weather radar: graphics pipelines, tracked even without a replacement
    "ph_rain_radar_horz", "ph_rain_radar_vert", "ph_rain_top",
};

// Must match the patched shaders' ModParams (tools/patch_dxil.py prologue_lines).
constexpr uint32_t kModSpace = 50;
constexpr uint32_t kTunableCount = 14;  // T0..T13
struct ModParams
{
    uint32_t debug_capture = 0;  // bit 0: capture armed; bit 1: radar draw (R0..R13 below); bit 2: 16 KB debug buffer (compute)
    uint32_t frame = 0;
    float extra[2] = {};      // T12 and the ozone scale (push_mod_args; radar draws: R12, R13)
    float tunables[12] = {};  // T0..T11
};
static_assert(sizeof(ModParams) == 64);
// Cloud tunables C0..C32 (tunables.ini). C0..C31, compute only: 32 root
// constants at b1 space50, read by the patched ch_volumetric_clouds_render
// (tools/patch_dxil.py, cloud_prologue_lines). C32 (thunderstorms) goes to the
// shaders as ModParams' DebugCapture bit 8 (push_mod_args).
constexpr uint32_t kCloudTunableCount = 33;
constexpr uint32_t kCloudRootConstants = 32;
constexpr uint32_t kDebugMagic = 0x4F4D5441;  // 'ATMO'
constexpr uint32_t kDebugBufferSize = 20 * 1024 * 1024;  // bits 6 and 7 below: the weather volume, cloud types
// Regions: uint4(stamp, register count, 0, 0) then float4 registers.
constexpr uint32_t kDebugRegionSkyAtmosphere = 16;  // ch_transmittance_lut, 13 registers
constexpr uint32_t kDebugRegionCamera = 256;        // ch_camera_volume, 16 registers
constexpr uint32_t kDebugRegionRadar = 1024;        // ph_rain_radar_horz, 22 registers
// ch_deferred_shadows (patched, tools/patch_dxil.py patch_shadow_capture):
// StructShadowReceiveConstant_Z (43 registers), then 4 cascades x 16 x 16
// shadow-map depths around the camera, 6 m apart.
constexpr uint32_t kDebugRegionShadow = 2048;
constexpr uint32_t kDebugRegionShadowGrid = 4096;
constexpr uint32_t kShadowGrid = 16;
// With DebugCapture bit 3: each cascade whole as a 192 x 192 image of
// float2(max, smallest non-empty depth) over 8 x 8 texels, and the pass's
// output (sun visibility) at every 8th screen pixel, rows of 512.
constexpr uint32_t kDebugRegionShadowImage = 0x10000;
constexpr uint32_t kShadowImage = 192;
constexpr uint32_t kDebugRegionShadowMask = 0x140000;
constexpr uint32_t kShadowMaskStride = 512;
constexpr uint32_t kShadowMaskRows = 320;
constexpr uint32_t kShadowMaskStep = 8;
// With DebugCapture bit 4 (a 4 MB buffer), passes patched with
// tools/patch_dxil.py patch_output_capture dump their output at every 8th
// pixel (rows of 512, from region + 512) and their pass cbuffer (16
// registers from region + 16); header uint4(stamp, 512, 320, cbuffer bytes).
struct OutputCapture
{
    const char *entry;
    uint32_t region;
};
constexpr OutputCapture kOutputCaptures[] = {
    { "ch_raymarched_shadows", 0x200000 },
    { "ch_raymarched_shadows_accumulate", 0x2C0000 },
};
constexpr uint32_t kOutputMaskOffset = 512;
constexpr uint32_t kOutputRegisters = 16;
// The aircraft's live shadow window (patch_dxil.py SIL_REGION): written by
// ch_deferred_shadows every frame with bit 4, read by the clouds. Header
// (flag, c0, dpm, camera depth; rows U, V, D; cascade, W, A, frame), then a
// coverage pyramid of 7 levels from 256 x 256.
constexpr uint32_t kShadowWindowRegion = 0x380000;
constexpr uint32_t kShadowWindowHeader = 96;  // ... then the direct sunlight at the camera and the header's frame
constexpr uint32_t kShadowWindowN0 = 256;
constexpr uint32_t kShadowWindowBytes = kShadowWindowHeader + 4 * (65536 + 16384 + 4096 + 1024 + 256 + 64 + 16);
// On capture with bit 4: the whole Global cbuffer as ch_deferred_shadows sees it
// (header uint4(stamp, 300, 0, 0) then 300 float4), written to <id>_global.txt.
constexpr uint32_t kDebugRegionGlobal = 0x3E0000;
constexpr uint32_t kGlobalRegisters = 300;
static_assert(kShadowWindowRegion + kShadowWindowBytes <= kDebugRegionGlobal);
static_assert(kDebugRegionGlobal + 16 + 16 * kGlobalRegisters <= kDebugBufferSize);
constexpr uint32_t kRadarRegisters = 22;
// While radar draws carry the capture flag, wait this long for the radar
// region before writing the capture without it.
constexpr uint64_t kCaptureRadarWaitFrames = 300;
// The weather volume generator (ch_cloudDensity, patched with patch_dxil.py
// patch_cloud_gen_capture), on capture with DebugCapture bit 6 (a 16 MB
// buffer): its constants, input textures (weather maps, shape noise, coverage
// curve, precipitation maps) and output, from kCloudGenRegion (layout in
// patch_dxil.py), written raw to <id>_clouds.bin for tools/cloud_capture_view.py.
// Header uint4(stamp, version, flags, threads per axis); getDimensions of
// t4, t5, t6, t7, t2, t3 at +16; User[0..63] at +128; PostProcess[0..51] at +1152.
// It runs only every ~25 frames, so a capture waits for it a while.
constexpr uint32_t kCloudGenRegion = 0x400000;
constexpr uint32_t kCloudGenBytes = 0xAC3000;
// The METAR anchoring's station table (weather_feed.h cloud_anchor_table,
// patch_dxil.py ANCHOR_TABLE), which the generator reads: the add-on copies it
// in whenever it changes. Inside the captured range, so captures carry it.
constexpr uint32_t kCloudGenAnchor = 0xAC1000;
static_assert(kCloudGenAnchor + weather::kAnchorTableBytes <= kCloudGenBytes);
constexpr uint32_t kAnchorUploadSlots = 4;    // a ring, so a copy in flight keeps its source
constexpr uint64_t kAnchorUploadFrames = 8;   // at most this often (the volume is rebuilt every ~25 frames)
constexpr uint32_t kCloudGenCloud = 3072;  // the cloud constants C0..C31 as the generator sees them
constexpr uint32_t kCloudGenDims = 16;
constexpr uint32_t kCloudGenUser = 128;
constexpr uint32_t kCloudGenPost = 1152;
constexpr uint64_t kCaptureCloudWaitFrames = 300;
// ... and the cloud renderer's own User cbuffer (ch_volumetric_clouds_render:
// its thin layer at User[9], User[10]), 64 registers after a stamp, with bit 4.
constexpr uint32_t kCloudUserRegion = 0x3E2000;
constexpr uint32_t kCloudUserRegisters = 64;
// ... then its sky light: SH elements 0 and 1 (144 bytes each: R, G, B, A,
// 9 coefficients each); element 1 lights the clouds.
constexpr uint32_t kCloudShRegion = kCloudUserRegion + 16 + 16 * kCloudUserRegisters;
constexpr uint32_t kCloudShStride = 144;
static_assert(kDebugRegionGlobal + 16 + 16 * kGlobalRegisters <= kCloudUserRegion);
static_assert(kCloudShRegion + 2 * kCloudShStride <= kCloudGenRegion);
static_assert(kCloudGenRegion + kCloudGenBytes <= kDebugBufferSize);
// Cloud types (patch_dxil.py TYPES_REGION): the generator writes per-slice type
// weights on a 128 x 128 grid over the weather volume (header uint4(magic,
// frame, threads per axis, f32 64 / N), then 128 x 128 x 32 RGBA8) with
// DebugCapture bit 7, and the cloud renderer reads them. Captures write it to
// <id>_types.bin.
constexpr uint32_t kCloudTypesRegion = 0x1000000;
constexpr uint32_t kCloudTypesMagic = 0x45505954;  // 'TYPE'
constexpr uint32_t kCloudTypesBytes = 16 + 4 * 128 * 128 * 32;
// ... followed by the storm cells' anvils on the same grid (float2: level, cover), for the
// renderer's cirrus (tools/patch_dxil.py ANVIL_REGION), and their towers' marks (float2: mark,
// base), for its sky light (TOWER_REGION); captures append both to <id>_types.bin.
constexpr uint32_t kCloudAnvilBytes = 2 * 8 * 128 * 128;
static_assert(kCloudGenRegion + kCloudGenBytes <= kCloudTypesRegion);
static_assert(kCloudTypesRegion + kCloudTypesBytes + kCloudAnvilBytes <= kDebugBufferSize);

// Registers the radar replacement dumps into region C, if it is installed.
// Rows 16-19 are uint4 texture sizes.
const char *const kRadarRegisterNames[kRadarRegisters] = {
    "User[0]      beam frame row 0, max-azimuth edge (.w antenna x)",
    "User[1]      beam frame row 1, max-azimuth edge (.w antenna y)",
    "User[2]      beam frame row 2, max-azimuth edge (.w antenna z)",
    "User[3]      (not read by the radar)",
    "User[4]      beam frame row 0, min-azimuth edge",
    "User[5]      beam frame row 1, min-azimuth edge",
    "User[6]      beam frame row 2, min-azimuth edge",
    "User[7]      (not read by the radar)",
    "User[8]      .xy volume centre, .z volume scale (m), .w display range (m)",
    "User[9]      .w world y -> altitude offset (m)",
    "User[10]     .xy sector azimuth limits (rad), .w > 0: black outside",
    "User[11]     .xy rain rate (mm/h) -> colour u: scale, bias",
    "Global[51]   .w volume scale as the cloud renderer sees it (m)",
    "Global[52]   .xy volume centre as the cloud renderer sees it (m)",
    "Global[92]   terrain map: .xy origin (m), .z 1/extent",
    "Global[117]  .y world origin altitude offset (m)",
    "t0 volume    width, height, depth, mips",
    "t1 terrain   width, height, mips",
    "t2 colours   width, height, mips",
    "t3 noise     width, height, layers, mips",
    "antenna      rain here (mm/h), column max (mm/h), altitude (m), terrain (m)",
    "antenna      volume coords u, v, w; display width (px)",
};

// Registers dumped by ch_camera_volume (ModDumpCamera in mod_common.hlsli).
const char *const kCameraRegisterNames[16] = {
    "Global[10]   .y = aerial-perspective range (m)",
    "Global[51]   .w = cloud-shadow volume radius (m)",
    "Global[52]   .xy = cloud-shadow volume centre (m)",
    "Global[56]   .z = cloud-shadow volume altitude offset",
    "Global[92]   cloud-layer map: .xy origin (m), .z 1/extent",
    "Global[94]   .z = light-pollution depth scale",
    "Global[117]  .y = world origin altitude offset (m)",
    "Global[132]  camera position (m)",
    "Global[134]  view rotation row 0",
    "Global[135]  view rotation row 1",
    "Global[136]  view rotation row 2",
    "Global[174]  inverse projection row 0",
    "Global[175]  inverse projection row 1",
    "Global[176]  inverse projection row 2",
    "Global[177]  inverse projection row 3",
    "User[1]      1 / camera-volume size",
};

// Our labels for the StructSkyAtmosphereConstant_Z components; '?' marks a guess.
const char *const kSkyAtmosphereNames[13][4] = {
    { "AbsorptionExtinction.r", "AbsorptionExtinction.g", "AbsorptionExtinction.b", "unused" },
    { "RayleighScattering.r", "RayleighScattering.g", "RayleighScattering.b", "MiePhaseG" },
    { "MieScattering.r", "MieScattering.g", "MieScattering.b", "BottomRadius" },
    { "MieExtinction.r", "MieExtinction.g", "MieExtinction.b", "TopRadius" },
    { "c4.x (MieAbsorption?)", "c4.y", "c4.z", "MieDensityExpScale" },
    { "GroundAlbedo.r?", "GroundAlbedo.g?", "GroundAlbedo.b?", "RayleighDensityExpScale" },
    { "Ozone0.LayerWidth", "Ozone0.Constant", "Ozone0.Linear", "c6.w (MultiScatteringFactor?)" },
    { "Ozone1.Constant", "Ozone1.Linear", "c7.z", "c7.w" },
    { "AtmosphereHeight", "BottomRadius^2", "TopRadius^2", "HorizonDistanceMax" },
    { "SkyViewFrameX.x", "SkyViewFrameX.y", "SkyViewFrameX.z", "unused" },
    { "SkyViewFrameY.x", "SkyViewFrameY.y", "SkyViewFrameY.z", "unused" },
    { "SkyViewFrameZ.x", "SkyViewFrameZ.y", "SkyViewFrameZ.z", "unused" },
    { "CameraVolumeJitter.x", "CameraVolumeJitter.y", "c12.z", "c12.w" },
};

// While a capture is armed, every replaced dispatch gets DebugCapture=1 and
// the capture id as its stamp, so a pass that only runs occasionally is
// caught whenever it next runs. The present thread copies the debug buffer to
// the readback buffer every few frames until the readback carries the stamp.
enum class Capture { idle, armed };
constexpr uint64_t kCaptureLatencyFrames = 4;   // frames between a copy and reading it
constexpr uint64_t kCaptureTimeoutFrames = 1800;
constexpr uint64_t kDispatchRateFrames = 900;   // window for dispatch_rates.log

// Retired replacement pipelines (F10) may still be in flight on the GPU, in
// command lists the game recorded before the swap and the GPU has yet to run:
// kept for both this many presents and seconds (with frame generation each
// rendered frame is presented twice, so presents alone are a short margin).
constexpr uint64_t kGraveyardFrames = 600;
constexpr auto kGraveyardTime = std::chrono::seconds(10);

// A compute pipeline (all are tracked, for logging) or a graphics pipeline
// whose pixel shader has a replacement or is watched (only those are tracked).
struct TrackedPipeline
{
    uint32_t crc = 0;  // compute or pixel shader
    pipeline_layout layout = {};
    std::string entry;
    bool graphics = false;
    bool watched = false;
    bool radar = false;  // the replica dumps the radar capture region
    uint32_t mod_param = UINT32_MAX;  // first injected root parameter, if any
    uint32_t cloud_param = UINT32_MAX;  // injected cloud constants (compute only)
    uint32_t tlas_param = UINT32_MAX;   // injected root SRV for MSFS's TLAS (compute only)
    pipeline replacement = {};
    device *dev = nullptr;  // the game's pipeline and its replacement belong to it
};

struct LayoutParam
{
    pipeline_layout_param_type type = pipeline_layout_param_type::push_descriptors;
    constant_range constants = {};
    std::vector<descriptor_range> ranges;
};

struct __declspec(uuid("7c1f3d52-9a4e-4f0b-8e2d-5b6a1c9d0e47")) CommandListState
{
    pipeline compute = {};
    pipeline graphics = {};  // only when it is a tracked graphics pipeline
    float viewport_width = 0.0f, viewport_height = 0.0f;  // first viewport, for draw logs
};

std::shared_mutex g_mutex;  // guards the containers below
std::unordered_map<uint32_t, std::string> g_names;                  // crc -> entry
std::unordered_map<uint32_t, std::vector<uint8_t>> g_replacements;  // crc -> DXIL
std::unordered_map<uint64_t, TrackedPipeline> g_pipelines;          // pipeline -> info
std::unordered_map<uint64_t, std::vector<LayoutParam>> g_layouts;   // layout -> params
std::unordered_map<uint64_t, uint32_t> g_injected;                  // layout -> first mod param
std::unordered_map<uint64_t, uint32_t> g_cloud_injected;            // layout -> cloud constants param
std::unordered_map<uint64_t, uint32_t> g_tlas_injected;             // layout -> TLAS root SRV param
// MSFS's latest top-level acceleration structure (Raytraced Shadows/Reflections):
// its GPU address and the frame it was built in. Bound for ch_deferred_shadows
// (the aircraft's shadow on clouds) only if built within kTlasFreshFrames, so a
// structure freed after RT is switched off is never bound.
std::atomic<uint64_t> g_tlas_va{0};
std::atomic<uint64_t> g_tlas_frame{0};
constexpr uint64_t kTlasFreshFrames = 2;
struct Retired
{
    device *dev;
    pipeline pipe;
    uint64_t frame;
    std::chrono::steady_clock::time_point time;
};
std::vector<Retired> g_graveyard;
std::array<float, kTunableCount> g_tunables = {};            // tunables.ini
std::array<float, kTunableCount> g_effective_tunables = {};  // with the weather feed blended in (T13)
std::array<float, kTunableCount> g_radar_tunables = {};      // tunables.ini R0..R13
std::array<float, kTunableCount> g_effective_radar_tunables = {};  // pushed on radar draws: R4 = 0 -> METAR freezing level
std::array<float, kCloudTunableCount> g_cloud_tunables = {};       // tunables.ini C0..C32
std::array<float, kCloudTunableCount> g_effective_cloud_tunables = {};  // pushed: C13-C15 = the aircraft (SimConnect)
// C32 <= 0: no thunderstorm cells (DebugCapture bit 8 to the cloud generator)
std::atomic<bool> g_storms_off{true};
constexpr float kFallbackFreezingLevel = 3000.0f;  // m, R4 = 0 before the first METAR
// Ozone for the fitted sky colours (T8): T14 (tunables.ini only; the shaders
// get the scale) > 0 fixes the column in DU, 0 takes the climatology at the
// aircraft. The shaders scale their 300 DU fit by column / 300 (ModParams.extra[1]).
float g_ozone_setting = 0.0f;
std::atomic<float> g_ozone_scale{1.0f};
// The haze field: T15 (tunables.ini only, on unless 0) has the weather feed
// write the stations' haze into the station table, and g_haze_rows says the
// table on the GPU has them: the patched atmosphere passes then read them
// (DebugCapture bit 9, push_mod_args).
constexpr uint32_t kHazeFieldBit = 512;
std::atomic<bool> g_haze_rows{false};

std::mutex g_seen_mutex;  // guards the log-once sets below
std::unordered_set<uint32_t> g_seen_shaders;
std::unordered_set<std::string> g_seen_dispatches;
std::unordered_set<std::string> g_seen_textures;

std::mutex g_log_mutex;
std::ofstream g_pipeline_log;  // pipelines.tsv
std::ofstream g_dispatch_log;  // dispatches.tsv
std::ofstream g_layout_log;    // layouts.txt
std::ofstream g_event_log;     // events.log
std::ofstream g_rootsig_log;   // rootsigs.log: every root signature the game creates
std::ofstream g_create_log;    // compute_creates.tsv: every compute pipeline creation attempt
std::ofstream g_texture_log;   // textures3d.tsv: each distinct 3D texture the game creates

// Debug buffer, its root UAV view, and the CPU readback copy.
resource g_debug_buffer = {};
resource_view g_debug_view = {};
resource g_readback = {};
resource g_anchor_upload = {};  // kAnchorUploadSlots station tables, upload heap
device *g_debug_device = nullptr;  // the debug resources belong to it (the game's device)
std::array<uint8_t, weather::kAnchorTableBytes> g_anchor_uploaded = {};
uint64_t g_anchor_uploaded_at = 0;
uint32_t g_anchor_uploads = 0;

std::atomic<Capture> g_capture{Capture::idle};
std::atomic<uint32_t> g_capture_stamp{0};      // capture id, stamped into ModParams.frame
std::atomic<uint32_t> g_capture_dispatches{0}; // replaced dispatches issued while armed
std::atomic<bool> g_auto_capture{true};        // first replaced dispatch triggers a capture
std::atomic<bool> g_auto_capture_radar{true};  // so does the first replaced radar draw
std::atomic<bool> g_capture_radar_due{false};  // ... and that capture waits for the radar region
std::atomic<bool> g_auto_capture_due{false};
std::atomic<uint32_t> g_capture_radar_draws{0}; // replaced radar draws issued while armed
uint32_t g_capture_id = 0;                     // present thread only, like the fields below
uint64_t g_capture_armed_at = 0;
bool g_capture_wants_radar = false;  // armed by the radar: wait for its region
uint64_t g_capture_copied_at = 0;
bool g_capture_copy_pending = false;

std::unordered_map<std::string, uint64_t> g_dispatch_counts;  // guarded by g_seen_mutex
std::vector<std::string> g_capture_order;  // watched passes in order while armed; guarded by g_seen_mutex
// watched pass -> its last thread-group count, y << 32 | x; guarded by g_seen_mutex
std::unordered_map<std::string, uint64_t> g_dispatch_groups;
std::ofstream g_rate_log;  // dispatch_rates.log

std::atomic<bool> g_enabled{true};
std::atomic<uint64_t> g_frame{0};
bool g_inject = false;
bool g_inject_graphics = false;
fs::path g_data_dir;
thread_local bool t_creating_replacement = false;

uint32_t crc32(const void *data, size_t size)
{
    static const auto table = [] {
        std::array<uint32_t, 256> t = {};
        for (uint32_t i = 0; i < 256; ++i)
        {
            uint32_t c = i;
            for (int k = 0; k < 8; ++k)
                c = (c & 1) ? 0xEDB88320u ^ (c >> 1) : c >> 1;
            t[i] = c;
        }
        return t;
    }();
    uint32_t c = 0xFFFFFFFFu;
    const auto *p = static_cast<const uint8_t *>(data);
    for (size_t i = 0; i < size; ++i)
        c = table[(c ^ p[i]) & 0xFF] ^ (c >> 8);
    return c ^ 0xFFFFFFFFu;
}

std::string hex32(uint32_t v)
{
    char buf[16];
    snprintf(buf, sizeof(buf), "0x%08x", v);
    return buf;
}

std::string hex64(uint64_t v)
{
    char buf[24];
    snprintf(buf, sizeof(buf), "0x%016" PRIx64, v);
    return buf;
}

const char *stage_name(pipeline_subobject_type type)
{
    switch (type)
    {
    case pipeline_subobject_type::vertex_shader: return "vs";
    case pipeline_subobject_type::hull_shader: return "hs";
    case pipeline_subobject_type::domain_shader: return "ds";
    case pipeline_subobject_type::geometry_shader: return "gs";
    case pipeline_subobject_type::pixel_shader: return "ps";
    case pipeline_subobject_type::compute_shader: return "cs";
    case pipeline_subobject_type::amplification_shader: return "as";
    case pipeline_subobject_type::mesh_shader: return "ms";
    default: return nullptr;
    }
}

char register_letter(descriptor_type type)
{
    switch (type)
    {
    case descriptor_type::sampler: return 's';
    case descriptor_type::constant_buffer:
    case descriptor_type::constant_buffer_with_dynamic_offset: return 'b';
    case descriptor_type::unordered_access_view:
    case descriptor_type::buffer_unordered_access_view:
    case descriptor_type::shader_storage_buffer:
    case descriptor_type::shader_storage_buffer_with_dynamic_offset: return 'u';
    default: return 't';
    }
}

void write_line(std::ofstream &file, const std::string &line)
{
    const std::lock_guard lock(g_log_mutex);
    file << line << '\n';
    file.flush();
}

// events.log lines carry the local time, to match them with screenshots.
void event(reshade::log::level level, const std::string &message)
{
    reshade::log::message(level, ("AtmosphereMod: " + message).c_str());
    SYSTEMTIME t;
    GetLocalTime(&t);
    char stamp[16];
    snprintf(stamp, sizeof(stamp), "%02u:%02u:%02u ", t.wHour, t.wMinute, t.wSecond);
    write_line(g_event_log, stamp + message);
}

fs::path resolve_data_dir(HMODULE module)
{
    char value[1024] = "";
    size_t size = sizeof(value);
    if (reshade::get_config_value(nullptr, "ATMOSPHEREMOD", "DataPath", value, &size) && value[0] != '\0')
        return fs::path(std::u8string(reinterpret_cast<const char8_t *>(value)));

    wchar_t module_path[MAX_PATH] = L"";
    GetModuleFileNameW(module, module_path, MAX_PATH);
    return fs::path(module_path).parent_path() / L"AtmosphereMod";
}

void load_names()
{
    std::ifstream file(g_data_dir / "shader_names.tsv");
    std::string crc, entry;
    while (file >> crc >> entry)
        g_names[static_cast<uint32_t>(std::stoul(crc, nullptr, 16))] = entry;
}

std::unordered_map<uint32_t, std::vector<uint8_t>> load_replacements()
{
    std::unordered_map<uint32_t, std::vector<uint8_t>> result;
    std::error_code ec;
    for (const auto &item : fs::directory_iterator(g_data_dir / "shaders", ec))
    {
        if (item.path().extension() != ".cso")
            continue;
        uint32_t crc;
        try
        {
            crc = static_cast<uint32_t>(std::stoul(item.path().stem().string(), nullptr, 16));
        }
        catch (const std::exception &)
        {
            continue;
        }
        std::ifstream file(item.path(), std::ios::binary);
        std::vector<uint8_t> code((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());
        if (!code.empty())
            result[crc] = std::move(code);
    }
    return result;
}

// [ATMOSPHEREMOD] TunablesPath, else <DataPath>/tunables.ini.
fs::path tunables_path()
{
    char value[1024] = "";
    size_t size = sizeof(value);
    if (reshade::get_config_value(nullptr, "ATMOSPHEREMOD", "TunablesPath", value, &size) && value[0] != '\0')
        return fs::path(std::u8string(reinterpret_cast<const char8_t *>(value)));
    return g_data_dir / "tunables.ini";
}

// tunables.ini: lines of "<prefix><index> = <value>", T for the atmosphere,
// R for the weather radar and C for the clouds; anything else is ignored.
template <size_t N = kTunableCount>
std::array<float, N> load_tunables(char prefix = 'T')
{
    std::array<float, N> result = {};
    std::ifstream file(tunables_path());
    std::string line;
    const std::string format = std::string(" ") + prefix + "%u = %f";
    while (std::getline(file, line))
    {
        unsigned index;
        float value;
        if (sscanf_s(line.c_str(), format.c_str(), &index, &value) == 2 && index < N)
            result[index] = value;
    }
    return result;
}

// One tunable that an older tunables.ini may not have yet: `fallback` then.
float load_tunable(char prefix, unsigned want, float fallback)
{
    std::ifstream file(tunables_path());
    std::string line;
    const std::string format = std::string(" ") + prefix + "%u = %f";
    float result = fallback;
    while (std::getline(file, line))
    {
        unsigned index;
        float value;
        if (sscanf_s(line.c_str(), format.c_str(), &index, &value) == 2 && index == want)
            result = value;
    }
    return result;
}

pipeline create_replacement(device *dev, pipeline_layout layout, const std::vector<uint8_t> &code, uint32_t crc)
{
    shader_desc cs = {};
    cs.code = code.data();
    cs.code_size = code.size();
    const pipeline_subobject subobject = { pipeline_subobject_type::compute_shader, 1, &cs };

    pipeline result = {};
    t_creating_replacement = true;
    const bool ok = dev->create_pipeline(layout, 1, &subobject, &result);
    t_creating_replacement = false;
    if (!ok)
    {
        event(reshade::log::level::error, "failed to create replacement pipeline for " + hex32(crc) +
              " (does the shader match the root signature? is InjectRootSignature on?)");
        return {};
    }
    return result;
}

// The game's graphics pipeline description with the pixel shader swapped.
// Only valid while the game's subobjects are (inside init_pipeline).
pipeline create_graphics_replacement(device *dev, pipeline_layout layout, uint32_t count,
                                     const pipeline_subobject *subobjects, uint32_t ps_index,
                                     const std::vector<uint8_t> &code, uint32_t crc)
{
    std::vector<pipeline_subobject> copy(subobjects, subobjects + count);
    shader_desc ps = *static_cast<const shader_desc *>(copy[ps_index].data);
    ps.code = code.data();
    ps.code_size = code.size();
    copy[ps_index].data = &ps;

    pipeline result = {};
    t_creating_replacement = true;
    const bool ok = dev->create_pipeline(layout, count, copy.data(), &result);
    t_creating_replacement = false;
    if (!ok)
    {
        event(reshade::log::level::error, "failed to create replacement graphics pipeline for pixel shader " +
              hex32(crc) + " (is InjectGraphicsRootSignature on?)");
        return {};
    }
    return result;
}

std::string entry_name(uint32_t crc)
{
    const std::shared_lock lock(g_mutex);
    const auto it = g_names.find(crc);
    return it != g_names.end() ? it->second : std::string();
}

void log_shader(uint32_t crc, const char *stage, pipeline_layout layout)
{
    {
        const std::lock_guard lock(g_seen_mutex);
        if (!g_seen_shaders.insert(crc).second)
            return;
    }
    const std::string entry = entry_name(crc);
    write_line(g_pipeline_log, hex32(crc) + '\t' + stage + '\t' + (entry.empty() ? "?" : entry) +
               '\t' + hex64(layout.handle));
}

std::string describe_param(const LayoutParam &param)
{
    std::string out;
    char buf[96];
    switch (param.type)
    {
    case pipeline_layout_param_type::push_constants:
        snprintf(buf, sizeof(buf), "root constants b%u space%u x%u dwords", param.constants.dx_register_index,
                 param.constants.dx_register_space, param.constants.count);
        return buf;
    case pipeline_layout_param_type::push_descriptors:
        out = "root descriptor";
        break;
    default:
        out = "descriptor table";
        break;
    }
    for (const auto &range : param.ranges)
    {
        snprintf(buf, sizeof(buf), " %c%u space%u x%u", register_letter(range.type), range.dx_register_index,
                 range.dx_register_space, range.count);
        out += buf;
    }
    return out;
}

void log_layout(uint32_t crc, const std::string &entry, pipeline_layout layout)
{
    std::vector<LayoutParam> params;
    {
        const std::shared_lock lock(g_mutex);
        if (const auto it = g_layouts.find(layout.handle); it != g_layouts.end())
            params = it->second;
    }
    std::string text = entry + ' ' + hex32(crc) + " layout " + hex64(layout.handle) +
                       (params.empty() ? " (not seen at creation)" : "") + '\n';
    for (size_t i = 0; i < params.size(); ++i)
        text += "  [" + std::to_string(i) + "] " + describe_param(params[i]) + '\n';
    write_line(g_layout_log, text);
}

void log_dispatch(const TrackedPipeline &info, const std::string &size)
{
    const std::string line = hex32(info.crc) + '\t' + info.entry + '\t' + size;
    {
        const std::lock_guard lock(g_seen_mutex);
        if (!g_seen_dispatches.insert(line).second)
            return;
    }
    write_line(g_dispatch_log, line);
}

void retire(device *dev, pipeline pipe)
{
    if (pipe.handle != 0)
        g_graveyard.push_back({ dev, pipe, g_frame.load(), std::chrono::steady_clock::now() });  // caller holds g_mutex
}

void reload(device *dev)
{
    auto fresh = load_replacements();
    const auto tunables = load_tunables();
    const auto radar_tunables = load_tunables('R');
    const auto cloud_tunables = load_tunables<kCloudTunableCount>('C');
    const float ozone_setting = load_tunables<kTunableCount + 1>()[kTunableCount];  // T14
    weather::set_haze_field(load_tunable('T', 15, 1.0f) > 0.0f);
    struct Job
    {
        uint64_t handle;
        uint32_t crc;
        pipeline_layout layout;
        device *dev;
    };
    std::vector<Job> jobs;
    size_t graphics = 0;
    {
        const std::unique_lock lock(g_mutex);
        g_replacements = fresh;
        g_tunables = tunables;
        g_radar_tunables = radar_tunables;
        g_cloud_tunables = cloud_tunables;
        g_ozone_setting = ozone_setting;
        for (const auto &[handle, info] : g_pipelines)
        {
            if (info.graphics)
                graphics += info.replacement.handle != 0;  // rebuilt only at creation
            else if (info.replacement.handle != 0 || fresh.contains(info.crc))
                jobs.push_back({ handle, info.crc, info.layout, info.dev != nullptr ? info.dev : dev });
        }
    }

    size_t replaced = 0, skipped = 0;
    for (const Job &job : jobs)
    {
        pipeline replacement = {};
        if (const auto it = fresh.find(job.crc); it != fresh.end())
        {
            bool layout_alive;
            {
                const std::shared_lock lock(g_mutex);
                layout_alive = g_layouts.contains(job.layout.handle);
            }
            if (layout_alive)
                replacement = create_replacement(job.dev, job.layout, it->second, job.crc);
            else
                ++skipped;
        }

        const std::unique_lock lock(g_mutex);
        const auto info = g_pipelines.find(job.handle);
        if (info == g_pipelines.end())
        {
            retire(job.dev, replacement);
            continue;
        }
        retire(job.dev, info->second.replacement);
        info->second.replacement = replacement;
        replaced += replacement.handle != 0;
    }
    event(reshade::log::level::info, "reloaded " + std::to_string(fresh.size()) + " shader(s) and tunables; " +
          std::to_string(replaced) + " compute pipeline(s) replaced" +
          (skipped ? ", " + std::to_string(skipped) + " skipped (root signature gone)" : "") +
          (graphics ? "; " + std::to_string(graphics) +
                      " graphics pipeline(s) keep their shader (new tunables apply, a shader change needs a restart)"
                    : ""));
}

void collect_garbage(uint64_t frame)
{
    std::vector<Retired> dead;
    {
        const std::unique_lock lock(g_mutex);
        const auto now = std::chrono::steady_clock::now();
        const auto split = std::partition(g_graveyard.begin(), g_graveyard.end(), [&](const Retired &r) {
            return frame - r.frame < kGraveyardFrames || now - r.time < kGraveyardTime;
        });
        dead.assign(split, g_graveyard.end());
        g_graveyard.erase(split, g_graveyard.end());
    }
    for (const Retired &r : dead)
        r.dev->destroy_pipeline(r.pipe);
}

// ---------------------------------------------------------------- root signature injection

const descriptor_range *first_range(const pipeline_layout_param &param)
{
    switch (param.type)
    {
    case pipeline_layout_param_type::descriptor_table:
        return param.descriptor_table.count ? param.descriptor_table.ranges : nullptr;
    case pipeline_layout_param_type::descriptor_table_with_flags:
        return param.descriptor_table_with_flags.count ? param.descriptor_table_with_flags.ranges : nullptr;
    default:
        return nullptr;
    }
}

bool is_table(const pipeline_layout_param &param, char letter, uint32_t count)
{
    const descriptor_range *range = first_range(param);
    return range != nullptr && register_letter(range->type) == letter && range->dx_register_index == 0 &&
           range->dx_register_space == 0 && range->count == count;
}

// The game's global compute root signature (see rootsigs.log).
bool is_global_compute_layout(uint32_t count, const pipeline_layout_param *params)
{
    return count == 5 &&
           params[0].type == pipeline_layout_param_type::push_constants &&
           params[0].push_constants.dx_register_space == 222 && params[0].push_constants.count == 2 &&
           is_table(params[1], 't', 80) && is_table(params[2], 'b', 16) &&
           is_table(params[3], 's', 16) && is_table(params[4], 'u', 8);
}

// The global graphics root signature (rootsigs.log): the same root constants,
// then a t/b/s table triple per shader stage, then the UAV table.
bool is_global_graphics_layout(uint32_t count, const pipeline_layout_param *params)
{
    if (count != 23 || params[0].type != pipeline_layout_param_type::push_constants ||
        params[0].push_constants.dx_register_space != 222 || params[0].push_constants.count != 2 ||
        !is_table(params[22], 'u', 8))
        return false;
    for (uint32_t i = 1; i < 22; i += 3)
        if (!is_table(params[i], 't', 80) || !is_table(params[i + 1], 'b', 16) || !is_table(params[i + 2], 's', 16))
            return false;
    return true;
}

std::vector<LayoutParam> to_layout_params(uint32_t count, const pipeline_layout_param *params)
{
    std::vector<LayoutParam> copy(count);
    for (uint32_t i = 0; i < count; ++i)
    {
        const pipeline_layout_param &p = params[i];
        LayoutParam &c = copy[i];
        c.type = p.type;
        switch (p.type)
        {
        case pipeline_layout_param_type::push_constants:
            c.constants = p.push_constants;
            break;
        case pipeline_layout_param_type::push_descriptors:
            c.ranges = { p.push_descriptors };
            break;
        case pipeline_layout_param_type::descriptor_table:
        case pipeline_layout_param_type::push_descriptors_with_ranges:
            c.ranges.assign(p.descriptor_table.ranges, p.descriptor_table.ranges + p.descriptor_table.count);
            break;
        case pipeline_layout_param_type::descriptor_table_with_flags:
        case pipeline_layout_param_type::push_descriptors_with_ranges_and_flags:
            for (uint32_t r = 0; r < p.descriptor_table_with_flags.count; ++r)
                c.ranges.push_back(p.descriptor_table_with_flags.ranges[r]);
            break;
        }
    }
    return copy;
}

std::string describe_params(const std::vector<LayoutParam> &params)
{
    std::string out;
    for (size_t i = 0; i < params.size(); ++i)
        out += " [" + std::to_string(i) + "] " + describe_param(params[i]) + ';';
    return out;
}

// Logs if the device has been removed; a removed device is what makes the
// game's next D3D12 call fail.
void check_device(device *dev, const char *where)
{
    if (dev->get_api() != device_api::d3d12)
        return;
    const HRESULT reason = reinterpret_cast<ID3D12Device *>(dev->get_native())->GetDeviceRemovedReason();
    if (FAILED(reason))
    {
        char buf[64];
        snprintf(buf, sizeof(buf), "0x%08lx", static_cast<unsigned long>(reason));
        event(reshade::log::level::error, std::string("device removed (") + buf + ") after " + where);
    }
}

bool on_create_pipeline_layout(device *, uint32_t &param_count, pipeline_layout_param *&params)
{
    const bool compute = is_global_compute_layout(param_count, params);
    const bool graphics = is_global_graphics_layout(param_count, params);
    const bool inject = (compute && g_inject) || (graphics && g_inject_graphics);
    write_line(g_rootsig_log, std::string("create") + (compute ? " [global compute]" : "") +
               (graphics ? " [global graphics]" : "") + (inject ? " [injecting]" : "") +
               describe_params(to_layout_params(param_count, params)));
    if (!inject)
        return false;

    // ReShade re-creates the root signature from this array before the event
    // returns, keeping the original root-signature and range flags.
    thread_local std::vector<pipeline_layout_param> extended;
    extended.assign(params, params + param_count);

    constant_range constants = {};
    constants.dx_register_space = kModSpace;
    constants.count = sizeof(ModParams) / 4;
    constants.visibility = shader_stage::all;
    extended.emplace_back(constants);

    // Both signatures also get the debug buffer as a root UAV. Graphics:
    // 23 + 16 + 2 = 41 of the 64 root-signature dwords.
    descriptor_range debug = {};  // single buffer UAV becomes a root UAV
    debug.dx_register_space = kModSpace;
    debug.count = 1;
    debug.visibility = shader_stage::all;
    debug.type = descriptor_type::buffer_unordered_access_view;
    extended.emplace_back(debug);

    // Compute also gets the cloud tunables: 6 + 16 + 2 + 32 = 56 dwords.
    if (compute)
    {
        constant_range cloud = {};
        cloud.dx_register_index = 1;
        cloud.dx_register_space = kModSpace;
        cloud.count = kCloudRootConstants;
        cloud.visibility = shader_stage::all;
        extended.emplace_back(cloud);

        // ... and MSFS's ray-tracing scene (its TLAS) as a root SRV, t0 space50,
        // for the aircraft's shadow with Raytraced Shadows: 58 of the 64 dwords
        descriptor_range tlas = {};
        tlas.dx_register_space = kModSpace;
        tlas.count = 1;
        tlas.visibility = shader_stage::all;
        tlas.type = descriptor_type::acceleration_structure;
        extended.emplace_back(tlas);
    }

    params = extended.data();
    param_count = static_cast<uint32_t>(extended.size());
    return true;
}

// Our root constants: ModParams at b0 space50, the cloud tunables at b1.
bool is_mod_param(const pipeline_layout_param &param, uint32_t reg = 0)
{
    return param.type == pipeline_layout_param_type::push_constants &&
           param.push_constants.dx_register_space == kModSpace && param.push_constants.dx_register_index == reg;
}

// ---------------------------------------------------------------- capture

void push_mod_args(command_list *cmd_list, const TrackedPipeline &info, const std::array<float, kTunableCount> &tunables)
{
    ModParams p;
    std::copy(tunables.begin(), tunables.begin() + 12, p.tunables);
    std::copy(tunables.begin() + 12, tunables.end(), p.extra);
    if (!info.radar)
        p.extra[1] = g_ozone_scale.load(std::memory_order_relaxed);  // (T13 is the add-on's own)
    if (info.graphics)
    {
        // The constants go through ReShade, which makes info.layout the bound
        // graphics signature. The debug buffer is bound on the native command
        // list: ReShade 6.8.0's push_descriptors binds a graphics root UAV with
        // SetGraphicsRootShaderResourceView, the wrong parameter type. A shader
        // only writes to it while DebugCapture is set, i.e. only after this bind.
        const bool can_capture = g_debug_buffer.handle != 0 && cmd_list->get_device()->get_api() == device_api::d3d12;
        p.debug_capture = can_capture && g_capture.load(std::memory_order_acquire) == Capture::armed;
        if (info.radar)
            p.debug_capture |= 2;  // R0..R13 in the tunables (the replica runs its model only then)
        // bit 6: the debug buffer (20 MB) is bound; bit 9: its station table has
        // the haze rows (the patched ps_deferredRendering reads them then)
        if (can_capture)
            p.debug_capture |= 64 | (g_haze_rows.load(std::memory_order_relaxed) ? kHazeFieldBit : 0);
        p.frame = g_capture_stamp.load(std::memory_order_relaxed);
        cmd_list->push_constants(shader_stage::all_graphics, info.layout, info.mod_param, 0, sizeof(p) / 4, &p);
        if (can_capture)
            reinterpret_cast<ID3D12GraphicsCommandList *>(cmd_list->get_native())->SetGraphicsRootUnorderedAccessView(
                info.mod_param + 1, reinterpret_cast<ID3D12Resource *>(g_debug_buffer.handle)->GetGPUVirtualAddress());
        if ((p.debug_capture & 1) && info.radar)
            ++g_capture_radar_draws;
        return;
    }
    p.debug_capture = g_capture.load(std::memory_order_acquire) == Capture::armed;
    p.frame = g_capture_stamp.load(std::memory_order_relaxed);
    if (p.debug_capture)
        ++g_capture_dispatches;
    // bit 2: the debug buffer is at least 16 KB; bit 3: at least 2 MB. The
    // patched ch_deferred_shadows writes beyond 4 KB (16 KB) only when it sees
    // these, so with an older add-on's smaller buffer it can't write past its end.
    static_assert(kDebugBufferSize >= kDebugRegionShadowGrid + 16 + 4 * 4 * kShadowGrid * kShadowGrid);
    static_assert(kDebugRegionShadowImage + 16 + 4 * kShadowImage * kShadowImage * 8 <= kDebugRegionShadowMask);
    static_assert(kDebugBufferSize >= kDebugRegionShadowMask + 16 + 4 * kShadowMaskStride * kShadowMaskRows);
    // bit 4: at least 4 MB, for patch_output_capture's regions and the
    // aircraft's live shadow window (ch_deferred_shadows writes it every frame
    // and ch_volumetric_clouds_render reads it)
    static_assert([] {
        for (const OutputCapture &o : kOutputCaptures)
            if (o.region + kOutputMaskOffset + 4 * kShadowMaskStride * kShadowMaskRows > kDebugBufferSize)
                return false;
        return true;
    }());
    static_assert(kShadowWindowRegion + kShadowWindowBytes <= kDebugBufferSize);
    // Only with the buffer bound below: bit 4 makes ch_deferred_shadows write
    // every frame, not just while a capture is armed.
    // Bit 6: at least 16 MB, for the weather volume capture (patch_cloud_gen_capture).
    // Bit 7: at least 20 MB, for the cloud types (written by the generator each
    // time it runs, read by the cloud renderer).
    static_assert(kCloudGenRegion + kCloudGenBytes <= kDebugBufferSize);
    static_assert(kCloudTypesRegion + kCloudTypesBytes <= kDebugBufferSize);
    if (g_debug_view.handle != 0)
        p.debug_capture |=
            4 | 8 | 16 | 64 | 128 | ((uint32_t(g_frame.load(std::memory_order_relaxed)) & 0xFFFF) << 16);
    // bit 9: the station table has the haze rows (the haze field: patch_dxil.py HAZE_ROWS)
    if (g_debug_view.handle != 0 && g_haze_rows.load(std::memory_order_relaxed))
        p.debug_capture |= kHazeFieldBit;
    // bit 8: no thunderstorm cells (C32 <= 0); the cloud generator's stay off.
    // Inverted so that shaders under an add-on without C32 keep them.
    if (g_storms_off.load(std::memory_order_relaxed))
        p.debug_capture |= 256;
    // bit 5: MSFS's TLAS is bound (t0 space50): ch_deferred_shadows traces the
    // aircraft into the shadow window, for Raytraced Shadows
    const uint64_t tlas = g_tlas_va.load(std::memory_order_relaxed);
    const bool bind_tlas = g_debug_view.handle != 0 && tlas != 0 && info.tlas_param != UINT32_MAX &&
                           info.entry == "ch_deferred_shadows" &&
                           g_frame.load(std::memory_order_relaxed) - g_tlas_frame.load(std::memory_order_relaxed) <=
                               kTlasFreshFrames;
    if (bind_tlas)
    {
        p.debug_capture |= 32;
        const resource_view view = { tlas };
        descriptor_table_update update = {};
        update.count = 1;
        update.type = descriptor_type::acceleration_structure;
        update.descriptors = &view;
        cmd_list->push_descriptors(shader_stage::all_compute, info.layout, info.tlas_param, update);
    }
    cmd_list->push_constants(shader_stage::all_compute, info.layout, info.mod_param, 0, sizeof(p) / 4, &p);

    if (g_debug_view.handle != 0)
    {
        descriptor_table_update update = {};
        update.count = 1;
        update.type = descriptor_type::buffer_unordered_access_view;
        update.descriptors = &g_debug_view;
        cmd_list->push_descriptors(shader_stage::all_compute, info.layout, info.mod_param + 1, update);
    }
}

bool region_present(const uint8_t *data, uint32_t offset)
{
    return reinterpret_cast<const uint32_t *>(data + offset)[0] == g_capture_stamp.load();
}

// The sun's shadow maps as ch_deferred_shadows sees them, for the aircraft
// shadow on clouds: per cascade the view -> shadow-map matrix (rows 5k..5k+3,
// normal offset 5k+4), its scale, where the camera falls, and a depth grid
// around it; then the watched passes in the order they ran while armed.
uint64_t dispatch_groups(const std::string &entry)
{
    const std::lock_guard lock(g_seen_mutex);
    const auto it = g_dispatch_groups.find(entry);
    return it == g_dispatch_groups.end() ? 0 : it->second;
}

// Passes patched with patch_output_capture: their output at every 8th pixel
// as <id>_<entry>.f32 beside the capture, and their pass cbuffer.
// The whole Global cbuffer (from ch_deferred_shadows) as <id>_global.txt, one
// register per line: to diff captures (tools/global_diff.py).
void write_global_dump(std::ofstream &file, const uint8_t *data, const fs::path &dir)
{
    if (!region_present(data, kDebugRegionGlobal))
    {
        file << "\nGlobal cbuffer dump: not captured\n";
        return;
    }
    const fs::path path = dir / (std::to_string(g_capture_id) + "_global.txt");
    std::ofstream out(path);
    const auto *g = reinterpret_cast<const float *>(data + kDebugRegionGlobal + 16);
    const auto *gi = reinterpret_cast<const uint32_t *>(data + kDebugRegionGlobal + 16);
    char line[256];
    for (uint32_t r = 0; r < kGlobalRegisters; ++r)
    {
        snprintf(line, sizeof(line), "Global[%3u] %15.7g %15.7g %15.7g %15.7g   (bits %08x %08x %08x %08x)\n", r,
                 g[r * 4], g[r * 4 + 1], g[r * 4 + 2], g[r * 4 + 3], gi[r * 4], gi[r * 4 + 1], gi[r * 4 + 2],
                 gi[r * 4 + 3]);
        out << line;
    }
    file << "\nGlobal cbuffer dump: " << path.filename().string() << "\n";
}

// The aircraft's live shadow window as the clouds see it: its header and
// level 0 (256 x 256 coverage) as <id>_shadow_window.f32.
void write_shadow_window(std::ofstream &file, const uint8_t *data, const fs::path &dir)
{
    char line[320];
    const uint8_t *w = data + kShadowWindowRegion;
    const auto *f = reinterpret_cast<const float *>(w);
    const auto *u = reinterpret_cast<const uint32_t *>(w);
    {
        const uint64_t tlas = g_tlas_va.load(), built = g_tlas_frame.load(), now = g_frame.load();
        snprintf(line, sizeof(line), "\nMSFS ray-tracing TLAS: %s0x%" PRIx64 ", built %" PRIu64 " frames ago (traced "
                 "into the window when <= %" PRIu64 ")\n", tlas ? "" : "none ", tlas, now - built, kTlasFreshFrames);
        file << line;
    }
    file << "\nAircraft shadow window (ch_deferred_shadows -> clouds, every frame)";
    if ((u[0] & 0x10000) == 0)
    {
        file << ": empty (no caster near the camera in the sun's shadow map: Raytraced Shadows on, or not written)\n";
        return;
    }
    snprintf(line, sizeof(line),
             ": last written in frame %u (& 0xFFFF; now %u)\n  cascade %u, window %.1f m, A %.1f m, cell %.3f m, "
             "depth per m %.6g, camera depth %.6f\n  U %.6g %.6g %.6g %.1f\n  V %.6g %.6g %.6g %.1f\n"
             "  D %.6g %.6g %.6g %.6f\n",
             u[0] & 0xFFFF, uint32_t(g_frame.load()) & 0xFFFF, u[16], f[17], f[18], f[1], f[2], f[3], f[4], f[5],
             f[6], f[7], f[8], f[9], f[10], f[11], f[12], f[13], f[14], f[15]);
    file << line;
    const fs::path path = dir / (std::to_string(g_capture_id) + "_shadow_window.f32");
    std::ofstream bin(path, std::ios::binary);
    bin.write(reinterpret_cast<const char *>(w + kShadowWindowHeader), 4 * kShadowWindowN0 * kShadowWindowN0);
    snprintf(line, sizeof(line), "  sunlight at the camera through clouds (MSFS's cloud-layer shadow): %.3f, "
             "header frame %u\n", f[20], u[21] & 0xFFFF);
    file << line;
    snprintf(line, sizeof(line), "  level 0: %s (%u x %u float32 coverage)\n", path.filename().string().c_str(),
             kShadowWindowN0, kShadowWindowN0);
    file << line;
}

void write_output_captures(std::ofstream &file, const uint8_t *data, const fs::path &dir)
{
    char line[256];
    for (const OutputCapture &o : kOutputCaptures)
    {
        const uint64_t groups = dispatch_groups(o.entry);
        const uint32_t width = std::min(uint32_t(groups) * 8 / kShadowMaskStep, kShadowMaskStride);
        const uint32_t height = std::min(uint32_t(groups >> 32) * 8 / kShadowMaskStep, kShadowMaskRows);
        snprintf(line, sizeof(line), "\n%s output", o.entry);
        file << line;
        if (!region_present(data, o.region) || width == 0 || height == 0)
        {
            file << (groups == 0 ? ": not captured (didn't run while armed)\n" : ": not captured\n");
            continue;
        }
        const fs::path path = dir / (std::to_string(g_capture_id) + "_" + o.entry + ".f32");
        std::ofstream bin(path, std::ios::binary);
        for (uint32_t j = 0; j < height; ++j)
            bin.write(reinterpret_cast<const char *>(data + o.region + kOutputMaskOffset + 4 * j * kShadowMaskStride),
                      4 * width);
        snprintf(line, sizeof(line), ": %s (%u x %u float32, every %uth pixel of %u x %u threads)\n",
                 path.filename().string().c_str(), width, height, kShadowMaskStep, uint32_t(groups) * 8,
                 uint32_t(groups >> 32) * 8);
        file << line;
        const uint32_t cb_bytes = reinterpret_cast<const uint32_t *>(data + o.region)[3];
        const auto *c = reinterpret_cast<const float *>(data + o.region + 16);
        for (uint32_t r = 0; r < std::min(cb_bytes / 16, kOutputRegisters); ++r)
        {
            snprintf(line, sizeof(line), "  cb c%-2u %15.7g %15.7g %15.7g %15.7g\n", r, c[r * 4], c[r * 4 + 1],
                     c[r * 4 + 2], c[r * 4 + 3]);
            file << line;
        }
    }
}

// The weather volume generator's dump as <id>_clouds.bin (raw, for
// tools/cloud_capture_view.py), with its texture sizes and first constants in
// the capture text; then the cloud renderer's User cbuffer.
void write_cloud_capture(std::ofstream &file, const uint8_t *data, const fs::path &dir)
{
    char line[256];
    file << "\nWeather volume generator (ch_cloudDensity)";
    if (!region_present(data, kCloudGenRegion))
    {
        file << ": not captured\n";
    }
    else
    {
        const auto *h = reinterpret_cast<const uint32_t *>(data + kCloudGenRegion);
        const fs::path path = dir / (std::to_string(g_capture_id) + "_clouds.bin");
        std::ofstream bin(path, std::ios::binary);
        bin.write(reinterpret_cast<const char *>(data + kCloudGenRegion), kCloudGenBytes);
        // flags: 1 the live-weather family (weather map), 2 the layer family (presets,
        // injected weather such as Active Sky), 4 its 2D map t4
        const bool map_family = (h[2] & 1) != 0, layer_family = (h[2] & 2) != 0;
        snprintf(line, sizeof(line), ": %s (layout %u, %u x %u threads, %s; view with tools/cloud_capture_view.py)\n",
                 path.filename().string().c_str(), h[1], h[3], h[3],
                 map_family     ? "live weather: the weather-map generator"
                 : layer_family ? "presets or injected weather: the layer generator"
                                : "unknown generator, no textures");
        file << line;
        static const char *const kMapNames[] = { "t4 shape noise", "t5 weather map, keyframe 0",
                                                 "t6 weather map, keyframe 1", "t7 coverage curve",
                                                 "t2 precipitation, keyframe 0", "t3 precipitation, keyframe 1" };
        static const char *const kLayerNames[] = { "t2 shape noise", nullptr, nullptr, "t3 coverage curve",
                                                   "t4 2D map", nullptr };
        if (map_family || layer_family)
            for (uint32_t j = 0; j < 6; ++j)
            {
                const uint32_t *d = h + kCloudGenDims / 4 + 4 * j;
                const char *name = map_family ? kMapNames[j] : kLayerNames[j];
                if (name == nullptr || (d[0] | d[1] | d[2] | d[3]) == 0)
                    continue;
                snprintf(line, sizeof(line), "  %-30s %u x %u x %u, %u mip levels\n", name, d[0], d[1], d[2], d[3]);
                file << line;
            }
        const auto *u = reinterpret_cast<const float *>(data + kCloudGenRegion + kCloudGenUser);
        if (layer_family)
            for (uint32_t j = 0; j < 3; ++j)
            {
                snprintf(line, sizeof(line),
                         "  layer %u: %7.0f - %7.0f m, coverage %.3f, density %.3f, noise mix %.2f %.2f %.2f %.2f\n", j,
                         u[6 * 4 + j], u[7 * 4 + j], u[8 * 4 + j], u[9 * 4 + j], u[(3 + j) * 4], u[(3 + j) * 4 + 1],
                         u[(3 + j) * 4 + 2], u[(3 + j) * 4 + 3]);
                file << line;
            }
        for (uint32_t r = 0; r < 12; ++r)
        {
            snprintf(line, sizeof(line), "  User[%2u]        %15.7g %15.7g %15.7g %15.7g\n", r, u[r * 4], u[r * 4 + 1],
                     u[r * 4 + 2], u[r * 4 + 3]);
            file << line;
        }
        // the METAR anchoring as the generator received it: C20, C21 and the station table
        const auto *cc = reinterpret_cast<const float *>(data + kCloudGenRegion + kCloudGenCloud);
        const uint8_t *table = data + kCloudGenRegion + kCloudGenAnchor;
        const auto *th = reinterpret_cast<const uint32_t *>(table);
        snprintf(line, sizeof(line), "  METAR anchoring as received: strength %.2f, radius %.0f km, %s\n", cc[20],
                 cc[21], th[0] == anchor::kTableMagic ? (std::to_string(th[1]) + " stations").c_str()
                                                      : "no station table");
        file << line;
        for (uint32_t i = 0; th[0] == anchor::kTableMagic && i < anchor::kStations; ++i)
        {
            const auto *st = reinterpret_cast<const float *>(table + 16 + 16 * i);
            if (st[0] == 0.0f && st[1] == 0.0f && st[2] == 0.0f)
                continue;
            snprintf(line, sizeof(line), "    station %u: %.3f %.3f, weight %.2f; slices (C a deck smooth):", i,
                     std::asin(std::clamp(st[2], -1.0f, 1.0f)) * 57.29578f, std::atan2(st[1], st[0]) * 57.29578f,
                     st[3]);
            file << line;
            const auto *rows = reinterpret_cast<const float *>(table + 16 + 16 * anchor::kStations +
                                                               16 * anchor::kSlices * i);
            for (int k = 0; k < anchor::kWeatherSlices; ++k)
            {
                const float *r = rows + 4 * k;
                if (r[1] <= 0.0f)
                    continue;
                snprintf(line, sizeof(line), " %d:%.2f/%.2f/%.1f/%.1f", k, r[0], r[1], r[2], r[3]);
                file << line;
            }
            file << "\n";
        }
        const auto *pp = reinterpret_cast<const float *>(data + kCloudGenRegion + kCloudGenPost);
        for (uint32_t r = 0; r < 8; ++r)
        {
            snprintf(line, sizeof(line), "  PostProcess[%u]  %15.7g %15.7g %15.7g %15.7g\n", r, pp[r * 4],
                     pp[r * 4 + 1], pp[r * 4 + 2], pp[r * 4 + 3]);
            file << line;
        }
    }
    // the cloud types grid as the renderer reads it
    const auto *types = reinterpret_cast<const uint32_t *>(data + kCloudTypesRegion);
    if (types[0] == kCloudTypesMagic)
    {
        const fs::path path = dir / (std::to_string(g_capture_id) + "_types.bin");
        std::ofstream bin(path, std::ios::binary);
        bin.write(reinterpret_cast<const char *>(data + kCloudTypesRegion), kCloudTypesBytes + kCloudAnvilBytes);
        snprintf(line, sizeof(line), "\nCloud types: %s (%u x %u threads, frame %u)\n",
                 path.filename().string().c_str(), types[2], types[2], types[1]);
    }
    else
    {
        snprintf(line, sizeof(line), "\nCloud types: none (%s)\n",
                 types[0] == 0 ? "the layer generator, or not written yet" : "no grid");
    }
    file << line;
    file << "\nCloud renderer User cbuffer (ch_volumetric_clouds_render)";
    if (!region_present(data, kCloudUserRegion))
    {
        file << ": not captured\n";
        return;
    }
    file << "\n";
    const auto *c = reinterpret_cast<const float *>(data + kCloudUserRegion + 16);
    for (uint32_t r = 0; r < 16; ++r)
    {
        snprintf(line, sizeof(line), "  User[%2u] %15.7g %15.7g %15.7g %15.7g\n", r, c[r * 4], c[r * 4 + 1],
                 c[r * 4 + 2], c[r * 4 + 3]);
        file << line;
    }
    // the sky light (SH), raw: per element and channel its 9 coefficients
    file << "Cloud renderer sky light (SH3VecRGBA_Z elements 0 and 1; element 1 lights the clouds)\n";
    for (uint32_t e = 0; e < 2; ++e)
        for (uint32_t ch = 0; ch < 4; ++ch)
        {
            const auto *s = reinterpret_cast<const float *>(data + kCloudShRegion + kCloudShStride * e) + 9 * ch;
            snprintf(line, sizeof(line), "  SH[%u].%c %12.5g %12.5g %12.5g %12.5g %12.5g %12.5g %12.5g %12.5g %12.5g\n",
                     e, "RGBA"[ch], s[0], s[1], s[2], s[3], s[4], s[5], s[6], s[7], s[8]);
            file << line;
        }
}

void write_shadow_capture(std::ofstream &file, const uint8_t *data, const fs::path &dir)
{
    char line[256];
    file << "\nSun shadow maps as seen by ch_deferred_shadows (StructShadowReceiveConstant_Z)";
    if (!region_present(data, kDebugRegionShadow))
    {
        file << ": not captured (pass not patched, or didn't run while armed)\n";
    }
    else
    {
        file << "\n\n";
        const auto *c = reinterpret_cast<const float *>(data + kDebugRegionShadow + 16);
        const auto *ci = reinterpret_cast<const int32_t *>(data + kDebugRegionShadow + 16);
        for (int r = 0; r < 43; ++r)
        {
            const char *what = r < 40 ? (r % 5 == 4 ? "   normal offset" : "")
                             : r == 40 ? "   map size, 1/size"
                             : r == 41 ? "   filter"
                                       : "   .x = cascade count (int)";
            snprintf(line, sizeof(line), "c%-2d %15.7g %15.7g %15.7g %15.7g%s\n", r, c[r * 4], c[r * 4 + 1],
                     c[r * 4 + 2], c[r * 4 + 3], what);
            file << line;
        }
        snprintf(line, sizeof(line), "cascade count (c42.x as int): %d\n", ci[42 * 4]);
        file << line;
        const float size = c[40 * 4];
        const auto *grid = reinterpret_cast<const float *>(data + kDebugRegionShadowGrid + 16);
        const bool have_grid = region_present(data, kDebugRegionShadowGrid);
        for (int k = 0; k < 4; ++k)
        {
            const float *m = c + k * 5 * 4;
            const float u_per_m = std::sqrt(m[0] * m[0] + m[1] * m[1] + m[2] * m[2]);
            const float v_per_m = std::sqrt(m[4] * m[4] + m[5] * m[5] + m[6] * m[6]);
            snprintf(line, sizeof(line),
                     "\ncascade %d: %.4g m per texel (u), %.4g (v); covers %.0f x %.0f m; camera at uv (%.4f, %.4f), "
                     "depth %.6g\n",
                     k, size > 0 && u_per_m > 0 ? 1.0f / (u_per_m * size) : 0.0f,
                     size > 0 && v_per_m > 0 ? 1.0f / (v_per_m * size) : 0.0f, u_per_m > 0 ? 1.0f / u_per_m : 0.0f,
                     v_per_m > 0 ? 1.0f / v_per_m : 0.0f, m[3], m[7], m[11]);
            file << line;
            if (!have_grid)
                continue;
            snprintf(line, sizeof(line), "depth grid, %u x %u, 6 m apart (at least 1 texel), centred on the camera:\n",
                     kShadowGrid, kShadowGrid);
            file << line;
            for (uint32_t j = 0; j < kShadowGrid; ++j)
            {
                std::string row;
                for (uint32_t i = 0; i < kShadowGrid; ++i)
                {
                    snprintf(line, sizeof(line), " %9.6f", grid[k * kShadowGrid * kShadowGrid + j * kShadowGrid + i]);
                    row += line;
                }
                file << row << "\n";
            }
        }
        // whole cascades and the screen's sun visibility, as raw float32 files
        // beside this one (tools/shadow_capture_view.py renders them)
        if (region_present(data, kDebugRegionShadowImage))
        {
            const fs::path path = dir / (std::to_string(g_capture_id) + "_shadow_maps.f32");
            std::ofstream bin(path, std::ios::binary);
            bin.write(reinterpret_cast<const char *>(data + kDebugRegionShadowImage + 16),
                      4 * kShadowImage * kShadowImage * 8);
            snprintf(line, sizeof(line), "\nshadow-map images: %s (4 cascades x %u x %u x float32 (max, smallest "
                     "non-empty depth) over %u x %u texels each; 0 = empty)\n", path.filename().string().c_str(),
                     kShadowImage, kShadowImage, uint32_t(size) / kShadowImage, uint32_t(size) / kShadowImage);
            file << line;
        }
        else
        {
            file << "\nshadow-map images: not captured\n";
        }
        const uint64_t groups = dispatch_groups("ch_deferred_shadows");
        const uint32_t width = std::min(uint32_t(groups) * 8 / kShadowMaskStep, kShadowMaskStride);
        const uint32_t height = std::min(uint32_t(groups >> 32) * 8 / kShadowMaskStep, kShadowMaskRows);
        if (region_present(data, kDebugRegionShadowMask) && width > 0 && height > 0)
        {
            const fs::path path = dir / (std::to_string(g_capture_id) + "_shadow_mask.f32");
            std::ofstream bin(path, std::ios::binary);
            for (uint32_t j = 0; j < height; ++j)
                bin.write(reinterpret_cast<const char *>(data + kDebugRegionShadowMask + 16 + 4 * j * kShadowMaskStride),
                          4 * width);
            snprintf(line, sizeof(line), "screen sun visibility: %s (%u x %u float32, every %uth pixel of %u x %u "
                     "threads)\n", path.filename().string().c_str(), width, height, kShadowMaskStep,
                     uint32_t(groups) * 8, uint32_t(groups >> 32) * 8);
            file << line;
        }
        else
        {
            file << "screen sun visibility: not captured\n";
        }
    }
    const weather::AircraftShadow aircraft = weather::aircraft_shadow();
    snprintf(line, sizeof(line), "\nAircraft (SimConnect): %s, heading - sun azimuth %.1f deg, bank %.1f deg, "
             "span %.1f m\n", !aircraft.valid ? "no data" : aircraft.cockpit ? "cockpit view" : "not in the cockpit view",
             aircraft.heading_from_sun * 57.29578f, aircraft.bank * 57.29578f, aircraft.span_m);
    file << line;
    std::vector<std::string> order;
    {
        const std::lock_guard lock(g_seen_mutex);
        order = g_capture_order;
    }
    file << "\nWatched passes in the order they ran while armed (first 400; repeats collapsed):\n";
    std::string last;
    int repeat = 0;
    for (size_t i = 0; i <= order.size(); ++i)
    {
        if (i < order.size() && order[i] == last)
        {
            ++repeat;
            continue;
        }
        if (!last.empty())
            file << "  " << last << (repeat > 1 ? " x" + std::to_string(repeat) : std::string()) << "\n";
        if (i < order.size())
        {
            last = order[i];
            repeat = 1;
        }
    }
}

void write_capture(const uint8_t *data)
{
    const fs::path dir = g_data_dir / "logs" / "captures";
    std::error_code ec;
    fs::create_directories(dir, ec);
    const fs::path path = dir / (std::to_string(g_capture_id) + "_sky_atmosphere.txt");
    std::ofstream file(path);
    file << "StructSkyAtmosphereConstant_Z as seen by ch_transmittance_lut, capture " << g_capture_id
         << ", frame " << g_frame.load() << "\n\n";
    char line[200];
    const auto *regs = reinterpret_cast<const float *>(data + kDebugRegionSkyAtmosphere + 16);
    for (int r = 0; r < 13; ++r)
        for (int c = 0; c < 4; ++c)
        {
            snprintf(line, sizeof(line), "c%-2d.%c  %16.9g  %s\n", r, "xyzw"[c], regs[r * 4 + c],
                     kSkyAtmosphereNames[r][c]);
            file << line;
        }

    file << "\nCamera-volume constants as seen by ch_camera_volume";
    if (region_present(data, kDebugRegionCamera))
    {
        file << "\n\n";
        const auto *cam = reinterpret_cast<const float *>(data + kDebugRegionCamera + 16);
        for (int r = 0; r < 16; ++r)
        {
            snprintf(line, sizeof(line), "%-12.12s %14.7g %14.7g %14.7g %14.7g   %s\n", kCameraRegisterNames[r],
                     cam[r * 4], cam[r * 4 + 1], cam[r * 4 + 2], cam[r * 4 + 3], kCameraRegisterNames[r] + 13);
            file << line;
        }
    }
    else
    {
        file << ": not captured (pass didn't run while armed)\n";
    }

    file << "\nWeather radar as seen by ph_rain_radar_horz";
    if (region_present(data, kDebugRegionRadar))
    {
        file << "\n\n";
        const auto *f = reinterpret_cast<const float *>(data + kDebugRegionRadar + 16);
        const auto *u = reinterpret_cast<const uint32_t *>(data + kDebugRegionRadar + 16);
        for (uint32_t r = 0; r < kRadarRegisters; ++r)
        {
            if (r >= 16 && r <= 19)  // texture sizes
                snprintf(line, sizeof(line), "%-12.12s %14u %14u %14u %14u   %s\n", kRadarRegisterNames[r],
                         u[r * 4], u[r * 4 + 1], u[r * 4 + 2], u[r * 4 + 3], kRadarRegisterNames[r] + 13);
            else
                snprintf(line, sizeof(line), "%-12.12s %14.7g %14.7g %14.7g %14.7g   %s\n", kRadarRegisterNames[r],
                         f[r * 4], f[r * 4 + 1], f[r * 4 + 2], f[r * 4 + 3], kRadarRegisterNames[r] + 13);
            file << line;
        }
    }
    else
    {
        file << ": not captured (radar not drawn while armed)\n";
    }
    std::array<float, kTunableCount> radar, ini;
    {
        const std::shared_lock lock(g_mutex);
        radar = g_effective_radar_tunables;
        ini = g_radar_tunables;
    }
    file << "\nRadar parameters pushed (R0..R13):";
    for (size_t i = 0; i < radar.size(); ++i)
    {
        snprintf(line, sizeof(line), " R%zu=%g", i, radar[i]);
        file << line;
    }
    file << (ini[4] <= 0.0f ? "\n(R4 = 0 in tunables.ini: freezing level from the METAR temperature, see weather.log)\n"
                            : "\n(R4 fixed in tunables.ini)\n");
    if (const weather::AircraftRadar aircraft = weather::aircraft_radar(); aircraft.valid)
    {
        snprintf(line, sizeof(line), "(R2 = PMDG 737 tilt knob %.1f -> %+.1f deg, plus R2 = %g from tunables.ini)\n",
                 aircraft.tilt_position, aircraft.tilt_deg, ini[2]);
        file << line;
    }
    else
    {
        file << "(R2 from tunables.ini only: not a PMDG 737, or its tilt knob not read yet)\n";
    }
    write_shadow_capture(file, data, dir);
    write_output_captures(file, data, dir);
    write_shadow_window(file, data, dir);
    write_global_dump(file, data, dir);
    write_cloud_capture(file, data, dir);
    event(reshade::log::level::info, "capture " + std::to_string(g_capture_id) + " written: " + path.string());
}

// Runs once per present while armed: read back the previous copy (once it is
// old enough), finish if it carries this capture's stamp, otherwise copy again.
void advance_capture(effect_runtime *runtime, uint64_t frame)
{
    if (g_capture.load() != Capture::armed)
        return;

    if (g_capture_copy_pending)
    {
        if (frame - g_capture_copied_at < kCaptureLatencyFrames)
            return;
        g_capture_copy_pending = false;

        device *dev = runtime->get_device();
        void *mapped = nullptr;
        bool ok = false;
        if (dev->map_buffer_region(g_readback, 0, kDebugBufferSize, map_access::read_only, &mapped) && mapped)
        {
            const auto *header = static_cast<const uint32_t *>(mapped);
            const auto *data = static_cast<const uint8_t *>(mapped);
            // While the radar replica is drawing (or the radar armed this capture),
            // wait a while for its region too. The radar draws intermittently at first.
            const bool radar_done = region_present(data, kDebugRegionRadar) ||
                                    (g_capture_radar_draws.load() == 0 && !g_capture_wants_radar) ||
                                    frame - g_capture_armed_at > kCaptureRadarWaitFrames;
            // The weather volume is rebuilt only every ~25 frames: wait a while for it too.
            const bool cloud_done = region_present(data, kCloudGenRegion) ||
                                    frame - g_capture_armed_at > kCaptureCloudWaitFrames;
            ok = header[0] == kDebugMagic && header[1] == g_capture_stamp.load() &&
                 region_present(data, kDebugRegionSkyAtmosphere) && radar_done && cloud_done;
            if (ok)
                write_capture(static_cast<const uint8_t *>(mapped));
            dev->unmap_buffer_region(g_readback);
        }
        if (ok)
        {
            g_capture = Capture::idle;
            return;
        }
    }

    if (frame - g_capture_armed_at > kCaptureTimeoutFrames)
    {
        const uint32_t dispatches = g_capture_dispatches.load();
        std::string why;
        if (!g_enabled)
            why = "replacements are OFF (Ctrl+Shift+F9)";
        else if (dispatches == 0)
            why = "no replaced pass ran while armed";
        else
            why = "replaced passes ran " + std::to_string(dispatches) + " times but no debug data arrived";
        event(reshade::log::level::warning, "capture " + std::to_string(g_capture_id) + " gave up: " + why);
        g_capture = Capture::idle;
        return;
    }

    command_queue *queue = runtime->get_command_queue();
    command_list *cmd = queue->get_immediate_command_list();
    cmd->barrier(g_debug_buffer, resource_usage::unordered_access, resource_usage::copy_source);
    cmd->copy_buffer_region(g_debug_buffer, 0, g_readback, 0, kDebugBufferSize);
    cmd->barrier(g_debug_buffer, resource_usage::copy_source, resource_usage::unordered_access);
    queue->flush_immediate_command_list();
    g_capture_copied_at = frame;
    g_capture_copy_pending = true;
}

// The METAR anchoring's station table into the debug buffer, when it changed
// (fades and crossfades change it every frame while they run), at most every
// kAnchorUploadFrames frames.
void upload_anchor_table(effect_runtime *runtime, uint64_t frame)
{
    if (g_anchor_upload.handle == 0 || g_debug_buffer.handle == 0)
        return;
    std::array<uint8_t, weather::kAnchorTableBytes> table;
    weather::cloud_anchor_table(table);
    if (g_anchor_uploads != 0 && (table == g_anchor_uploaded || frame - g_anchor_uploaded_at < kAnchorUploadFrames))
        return;
    device *dev = runtime->get_device();
    const uint64_t offset = uint64_t(weather::kAnchorTableBytes) * (g_anchor_uploads % kAnchorUploadSlots);
    void *mapped = nullptr;
    if (!dev->map_buffer_region(g_anchor_upload, offset, weather::kAnchorTableBytes, map_access::write_only, &mapped) ||
        mapped == nullptr)
        return;
    std::memcpy(mapped, table.data(), table.size());
    dev->unmap_buffer_region(g_anchor_upload);
    command_queue *queue = runtime->get_command_queue();
    command_list *cmd = queue->get_immediate_command_list();
    cmd->barrier(g_debug_buffer, resource_usage::unordered_access, resource_usage::copy_dest);
    cmd->copy_buffer_region(g_anchor_upload, offset, g_debug_buffer, kCloudGenRegion + kCloudGenAnchor,
                            weather::kAnchorTableBytes);
    cmd->barrier(g_debug_buffer, resource_usage::copy_dest, resource_usage::unordered_access);
    queue->flush_immediate_command_list();
    g_anchor_uploaded = table;
    g_anchor_uploaded_at = frame;
    ++g_anchor_uploads;
    uint32_t haze_word;
    std::memcpy(&haze_word, table.data() + 12, 4);
    g_haze_rows.store(haze_word == anchor::kHazeMagic, std::memory_order_relaxed);
}

void request_capture(uint64_t frame)
{
    if (g_debug_buffer.handle == 0)
    {
        event(reshade::log::level::warning, "capture unavailable: debug resources were not created");
        return;
    }
    if (g_capture.load() != Capture::idle)
        return;
    if (g_capture_id == 0)
    {
        // continue after the captures already on disk, so a new session doesn't overwrite them
        std::error_code ec;
        for (const auto &f : fs::directory_iterator(g_data_dir / "logs" / "captures", ec))
        {
            const std::string name = f.path().filename().string();
            if (name.size() > 18 && name.ends_with("_sky_atmosphere.txt"))
                g_capture_id = std::max(g_capture_id, uint32_t(std::strtoul(name.c_str(), nullptr, 10)));
        }
    }
    ++g_capture_id;
    g_capture_stamp = g_capture_id;
    g_capture_dispatches = 0;
    g_capture_radar_draws = 0;
    g_capture_wants_radar = g_capture_radar_due.exchange(false);
    g_capture_armed_at = frame;
    g_capture_copy_pending = false;
    {
        const std::lock_guard lock(g_seen_mutex);
        g_capture_order.clear();
    }
    g_capture = Capture::armed;
    event(reshade::log::level::info, "capture " + std::to_string(g_capture_id) + " armed" +
          (g_enabled ? "" : " (replacements are OFF; it completes once they're back on)"));
}

void log_dispatch_rates(uint64_t frame)
{
    std::unordered_map<std::string, uint64_t> counts;
    {
        const std::lock_guard lock(g_seen_mutex);
        counts.swap(g_dispatch_counts);
    }
    std::string line = "frames " + std::to_string(frame - kDispatchRateFrames) + "-" + std::to_string(frame) + ':';
    for (const auto &[entry, count] : counts)
    {
        char buf[160];
        snprintf(buf, sizeof(buf), " %s %.2f/frame;", entry.c_str(), double(count) / kDispatchRateFrames);
        line += buf;
    }
    write_line(g_rate_log, line);
}

// ---------------------------------------------------------------- events

// Created on the first present rather than at device creation, so nothing of
// ours touches the device while the game is still initialising it. Each step
// is logged separately; a failure only disables captures.
void create_debug_resources(device *dev)
{
    if (dev->get_api() != device_api::d3d12)
        return;
    const resource_desc buffer_desc(kDebugBufferSize, memory_heap::default_,
                                    resource_usage::unordered_access | resource_usage::copy_source |
                                        resource_usage::copy_dest);
    const resource_desc readback_desc(kDebugBufferSize, memory_heap::readback, resource_usage::copy_dest);

    const char *failed = nullptr;
    if (!dev->create_resource(buffer_desc, nullptr, resource_usage::unordered_access, &g_debug_buffer))
        failed = "debug buffer";
    // ReShade 6.8's D3D12 backend uses the view "size" as NumElements, not bytes
    // (the header says bytes). Passing bytes made the view overrun the buffer,
    // and D3D12 removed the device for it.
    else if (!dev->create_resource_view(g_debug_buffer, resource_usage::unordered_access,
                                        resource_view_desc(format::r32_uint, 0, kDebugBufferSize / 4), &g_debug_view))
        failed = "debug buffer view";
    else if (!dev->create_resource(readback_desc, nullptr, resource_usage::copy_dest, &g_readback))
        failed = "readback buffer";
    // the station table's upload ring: without it only the anchoring is off
    else if (!dev->create_resource(resource_desc(weather::kAnchorTableBytes * kAnchorUploadSlots, memory_heap::upload,
                                                 resource_usage::copy_source),
                                   nullptr, resource_usage::cpu_access, &g_anchor_upload))
        event(reshade::log::level::warning, "failed to create the station table's upload buffer; METAR anchoring off");
    check_device(dev, "creating debug resources");

    if (failed == nullptr)
    {
        g_debug_device = dev;
        event(reshade::log::level::info, "debug resources created");
        return;
    }
    event(reshade::log::level::error, std::string("failed to create ") + failed + "; captures disabled");
    if (g_debug_view.handle != 0)
        dev->destroy_resource_view(std::exchange(g_debug_view, resource_view{}));
    if (g_debug_buffer.handle != 0)
        dev->destroy_resource(std::exchange(g_debug_buffer, resource{}));
}

// Only what belongs to the device being destroyed: other software in the sim's
// process may create and destroy a D3D12 device of its own (a 640 x 480 one
// at the start of a flight did, 2026-10-25), and tearing down the game's
// replacements and debug buffer then turned the mod off in that flight, and an
// F10 afterwards hung the GPU (the replacements' debug UAV was gone).
void on_destroy_device(device *dev)
{
    std::vector<pipeline> pipes;
    {
        const std::unique_lock lock(g_mutex);
        const auto split = std::partition(g_graveyard.begin(), g_graveyard.end(),
                                          [dev](const Retired &r) { return r.dev != dev; });
        for (auto it = split; it != g_graveyard.end(); ++it)
            pipes.push_back(it->pipe);
        g_graveyard.erase(split, g_graveyard.end());
        for (auto it = g_pipelines.begin(); it != g_pipelines.end();)
        {
            if (it->second.dev != dev)
            {
                ++it;
                continue;
            }
            if (it->second.replacement.handle != 0)
                pipes.push_back(it->second.replacement);
            it = g_pipelines.erase(it);  // (the game's pipeline goes with its device)
        }
    }
    for (const pipeline pipe : pipes)
        dev->destroy_pipeline(pipe);

    if (dev != g_debug_device)
    {
        if (!pipes.empty() || dev->get_api() == device_api::d3d12)
            event(reshade::log::level::info, "a D3D12 device other than the game's was destroyed: " +
                  std::to_string(pipes.size()) + " replacement(s) of its own released, the game's kept");
        return;
    }
    g_debug_device = nullptr;
    if (g_debug_view.handle != 0)
        dev->destroy_resource_view(std::exchange(g_debug_view, resource_view{}));
    if (g_debug_buffer.handle != 0)
        dev->destroy_resource(std::exchange(g_debug_buffer, resource{}));
    if (g_readback.handle != 0)
        dev->destroy_resource(std::exchange(g_readback, resource{}));
    if (g_anchor_upload.handle != 0)
        dev->destroy_resource(std::exchange(g_anchor_upload, resource{}));
}

void on_init_command_list(command_list *cmd_list)
{
    cmd_list->create_private_data<CommandListState>();
}

void on_destroy_command_list(command_list *cmd_list)
{
    cmd_list->destroy_private_data<CommandListState>();
}

void on_reset_command_list(command_list *cmd_list)
{
    if (auto *state = cmd_list->get_private_data<CommandListState>())
        *state = {};
}

void on_init_pipeline_layout(device *dev, uint32_t count, const pipeline_layout_param *params, pipeline_layout layout)
{
    uint32_t mod_param = UINT32_MAX, cloud_param = UINT32_MAX, tlas_param = UINT32_MAX;
    for (uint32_t i = 0; i < count; ++i)
    {
        if (mod_param == UINT32_MAX && is_mod_param(params[i]))
            mod_param = i;
        if (cloud_param == UINT32_MAX && is_mod_param(params[i], 1))
            cloud_param = i;
        if (tlas_param == UINT32_MAX && params[i].type == pipeline_layout_param_type::push_descriptors &&
            params[i].push_descriptors.type == descriptor_type::acceleration_structure &&
            params[i].push_descriptors.dx_register_space == kModSpace)
            tlas_param = i;
    }
    write_line(g_rootsig_log, "created " + hex64(layout.handle) + ", " + std::to_string(count) + " params" +
               (mod_param != UINT32_MAX ? " [injected, mod params at " + std::to_string(mod_param) +
                (cloud_param != UINT32_MAX ? ", cloud params at " + std::to_string(cloud_param) : "") + "]" : ""));
    if (mod_param != UINT32_MAX)
        check_device(dev, "creating the injected root signature");

    auto copy = to_layout_params(count, params);
    const std::unique_lock lock(g_mutex);
    g_layouts[layout.handle] = std::move(copy);
    if (mod_param != UINT32_MAX)
        g_injected[layout.handle] = mod_param;
    if (cloud_param != UINT32_MAX)
        g_cloud_injected[layout.handle] = cloud_param;
    if (tlas_param != UINT32_MAX)
        g_tlas_injected[layout.handle] = tlas_param;
}

// Logged before creation: if the game stops right after one of these lines,
// that creation is what failed.
bool on_create_pipeline(device *, pipeline_layout layout, uint32_t count, const pipeline_subobject *subobjects)
{
    if (t_creating_replacement)
        return false;
    for (uint32_t i = 0; i < count; ++i)
    {
        if (subobjects[i].type != pipeline_subobject_type::compute_shader || subobjects[i].data == nullptr)
            continue;
        const auto &desc = *static_cast<const shader_desc *>(subobjects[i].data);
        if (desc.code != nullptr && desc.code_size != 0)
            write_line(g_create_log, hex32(crc32(desc.code, desc.code_size)) + '\t' + hex64(layout.handle));
    }
    return false;
}

void on_destroy_pipeline_layout(device *, pipeline_layout layout)
{
    const std::unique_lock lock(g_mutex);
    g_layouts.erase(layout.handle);
    g_injected.erase(layout.handle);
    g_cloud_injected.erase(layout.handle);
    g_tlas_injected.erase(layout.handle);
}

void on_init_pipeline(device *dev, pipeline_layout layout, uint32_t count, const pipeline_subobject *subobjects,
                      pipeline pipe)
{
    if (t_creating_replacement)
        return;

    uint32_t compute_crc = 0, pixel_crc = 0, pixel_index = UINT32_MAX;
    bool is_compute = false;
    for (uint32_t i = 0; i < count; ++i)
    {
        const char *stage = stage_name(subobjects[i].type);
        if (stage == nullptr || subobjects[i].data == nullptr)
            continue;
        const auto &desc = *static_cast<const shader_desc *>(subobjects[i].data);
        if (desc.code == nullptr || desc.code_size == 0)
            continue;
        const uint32_t crc = crc32(desc.code, desc.code_size);
        log_shader(crc, stage, layout);
        if (subobjects[i].type == pipeline_subobject_type::compute_shader)
        {
            is_compute = true;
            compute_crc = crc;
        }
        else if (subobjects[i].type == pipeline_subobject_type::pixel_shader)
        {
            pixel_crc = crc;
            pixel_index = i;
        }
    }

    TrackedPipeline info;
    info.crc = is_compute ? compute_crc : pixel_crc;
    info.layout = layout;
    info.graphics = !is_compute;
    info.dev = dev;
    std::vector<uint8_t> code;
    {
        const std::shared_lock lock(g_mutex);
        if (g_pipelines.contains(pipe.handle))  // init_pipeline repeats for the same handle
            return;
        if (const auto it = g_names.find(info.crc); it != g_names.end())
            info.entry = it->second;
        if (const auto it = g_injected.find(layout.handle); it != g_injected.end())
            info.mod_param = it->second;
        if (const auto it = g_cloud_injected.find(layout.handle); it != g_cloud_injected.end())
            info.cloud_param = it->second;
        if (const auto it = g_tlas_injected.find(layout.handle); it != g_tlas_injected.end())
            info.tlas_param = it->second;
        if (const auto it = g_replacements.find(info.crc); it != g_replacements.end())
            code = it->second;
    }
    info.watched = kWatchedEntries.contains(info.entry);
    info.radar = info.entry == "ph_rain_radar_horz";
    if (info.graphics && (pixel_index == UINT32_MAX || (code.empty() && !info.watched)))
        return;  // graphics pipelines are tracked only when replaced or watched

    if (info.watched || info.graphics)
        log_layout(info.crc, info.entry, layout);
    if (!code.empty())
        info.replacement = info.graphics
            ? create_graphics_replacement(dev, layout, count, subobjects, pixel_index, code, info.crc)
            : create_replacement(dev, layout, code, compute_crc);
    if (info.graphics && info.replacement.handle == 0 && !info.watched)
        return;

    const pipeline replacement = info.replacement;
    const std::string entry = info.entry;
    {
        const std::unique_lock lock(g_mutex);
        if (!g_pipelines.try_emplace(pipe.handle, std::move(info)).second)
        {
            retire(dev, replacement);  // another thread registered this pipeline first
            return;
        }
    }
    if (replacement.handle != 0)
        event(reshade::log::level::info, "replacing " + entry + ' ' + hex32(is_compute ? compute_crc : pixel_crc) +
              (is_compute ? "" : " (graphics pipeline " + hex64(pipe.handle) + ")"));
}

void on_destroy_pipeline(device *dev, pipeline pipe)
{
    pipeline replacement = {};
    {
        const std::unique_lock lock(g_mutex);
        const auto it = g_pipelines.find(pipe.handle);
        if (it == g_pipelines.end())
            return;
        replacement = it->second.replacement;
        g_pipelines.erase(it);
    }
    // The game only destroys a pipeline once the GPU is done with it, and the
    // replacement was only ever used in its place.
    if (replacement.handle != 0)
        dev->destroy_pipeline(replacement);
}

void on_bind_pipeline(command_list *cmd_list, pipeline_stage stages, pipeline pipe)
{
    pipeline replacement = {};
    bool graphics = true;  // every compute pipeline is tracked, so an unknown one is graphics
    bool tracked = false;
    {
        const std::shared_lock lock(g_mutex);
        if (const auto it = g_pipelines.find(pipe.handle); it != g_pipelines.end())
        {
            tracked = true;
            graphics = it->second.graphics;
            replacement = it->second.replacement;
        }
    }
    if (auto *state = cmd_list->get_private_data<CommandListState>())
    {
        if (graphics)
            state->graphics = tracked ? pipe : pipeline{};
        else
            state->compute = pipe;
    }
    if (replacement.handle != 0 && g_enabled.load(std::memory_order_relaxed))
        cmd_list->bind_pipeline(stages, replacement);
}

// Common to direct and indirect dispatches: log watched passes and give
// replacements their extra root arguments right before they run.
void before_dispatch(command_list *cmd_list, const std::string &size)
{
    const auto *state = cmd_list->get_private_data<CommandListState>();
    if (state == nullptr || state->compute.handle == 0)
        return;

    TrackedPipeline info;
    std::array<float, kTunableCount> tunables;
    std::array<float, kCloudTunableCount> cloud;
    {
        const std::shared_lock lock(g_mutex);
        const auto it = g_pipelines.find(state->compute.handle);
        if (it == g_pipelines.end() || (!it->second.watched && it->second.replacement.handle == 0))
            return;
        info = it->second;
        tunables = g_effective_tunables;
        cloud = g_effective_cloud_tunables;
    }
    if (info.watched)
    {
        log_dispatch(info, size);
        const std::lock_guard lock(g_seen_mutex);
        ++g_dispatch_counts[info.entry];
        if (g_capture.load(std::memory_order_relaxed) == Capture::armed && g_capture_order.size() < 400)
            g_capture_order.push_back(info.entry);
        // the screen size behind captured outputs (8 x 8 threads per group)
        unsigned gx = 0, gy = 0;
        if (sscanf_s(size.c_str(), "%u\t%u", &gx, &gy) == 2)
            g_dispatch_groups[info.entry] = (uint64_t(gy) << 32) | gx;
    }
    if (info.replacement.handle != 0 && info.mod_param != UINT32_MAX && g_enabled.load(std::memory_order_relaxed))
    {
        // The aircraft's live shadow window: ch_deferred_shadows writes it and
        // ch_volumetric_clouds_render reads it later in the frame, through our
        // buffer, which the game's own barriers don't cover.
        if (g_debug_buffer.handle != 0 && cmd_list->get_device()->get_api() == device_api::d3d12 &&
            (info.entry == "ch_deferred_shadows" || info.entry == "ch_volumetric_clouds_render"))
        {
            D3D12_RESOURCE_BARRIER barrier = {};
            barrier.Type = D3D12_RESOURCE_BARRIER_TYPE_UAV;
            barrier.UAV.pResource = reinterpret_cast<ID3D12Resource *>(g_debug_buffer.handle);
            reinterpret_cast<ID3D12GraphicsCommandList *>(cmd_list->get_native())->ResourceBarrier(1, &barrier);
        }
        push_mod_args(cmd_list, info, tunables);
        if (info.cloud_param != UINT32_MAX)
            cmd_list->push_constants(shader_stage::all_compute, info.layout, info.cloud_param, 0, kCloudRootConstants,
                                     cloud.data());
        if (g_auto_capture.exchange(false))
            g_auto_capture_due = true;  // first replaced dispatch: capture once automatically
    }
}

void on_bind_viewports(command_list *cmd_list, uint32_t first, uint32_t count, const viewport *viewports)
{
    if (first != 0 || count == 0)
        return;
    if (auto *state = cmd_list->get_private_data<CommandListState>())
    {
        state->viewport_width = viewports[0].width;
        state->viewport_height = viewports[0].height;
    }
}

// Volumes are rare, so every distinct 3D texture description is logged: the
// weather volume (N x N x 32, its format), the weather maps, noise, LUTs.
// The format is the DXGI_FORMAT value (ReShade's format enum matches it).
void on_init_resource(device *, const resource_desc &desc, const subresource_data *, resource_usage, resource)
{
    if (desc.type != resource_type::texture_3d)
        return;
    char line[128];
    snprintf(line, sizeof(line), "%u\t%u\t%u\t%u\t%u\t0x%x\t%u", desc.texture.width, desc.texture.height,
             unsigned(desc.texture.depth_or_layers), unsigned(desc.texture.levels), unsigned(desc.texture.format),
             unsigned(desc.usage), unsigned(desc.heap));
    {
        const std::lock_guard lock(g_seen_mutex);
        if (!g_seen_textures.insert(line).second)
            return;
    }
    write_line(g_texture_log, line);
}

// Ray tracing (Raytraced Shadows/Reflections): where MSFS builds its
// acceleration structures and traces, relative to the watched passes, for the
// aircraft's shadow on clouds with RT shadows on.
// While a capture is armed they go into the pass order; each new top-level
// address is logged once (up to 32).
std::unordered_set<uint64_t> g_seen_tlas;  // guarded by g_seen_mutex

bool on_build_acceleration_structure(command_list *cmd_list, acceleration_structure_type type,
                                     acceleration_structure_build_flags flags, uint32_t input_count,
                                     const acceleration_structure_build_input *inputs, resource, uint64_t,
                                     resource_view source, resource_view dest, acceleration_structure_build_mode mode)
{
    const bool top = type == acceleration_structure_type::top_level;
    uint32_t instances = 0;
    for (uint32_t i = 0; top && i < input_count; ++i)
        if (inputs[i].type == acceleration_structure_build_input_type::instances)
            instances += inputs[i].instances.count;
    char line[200];
    snprintf(line, sizeof(line), "%s %s (%u %s) -> 0x%" PRIx64 "%s, cmd %p",
             top ? "TLAS" : "BLAS", mode == acceleration_structure_build_mode::update ? "update" : "build",
             top ? instances : input_count, top ? "instances" : "geometries", dest.handle,
             source.handle != 0 ? " (from a source)" : "", static_cast<void *>(cmd_list));
    bool fresh = false;
    {
        const std::lock_guard lock(g_seen_mutex);
        if (g_capture.load(std::memory_order_relaxed) == Capture::armed && g_capture_order.size() < 400 &&
            (top || g_capture_order.empty() || g_capture_order.back().rfind("BLAS", 0) != 0))
            g_capture_order.push_back(top ? std::string(line) : std::string("BLAS builds"));
        if (top && g_seen_tlas.size() < 32)
            fresh = g_seen_tlas.insert(dest.handle).second;
        if (top)
            ++g_dispatch_counts["TLAS build"];
    }
    if (top && dest.handle != 0)
    {
        g_tlas_va = dest.handle;
        g_tlas_frame = g_frame.load(std::memory_order_relaxed);
    }
    if (fresh)
        event(reshade::log::level::info, std::string("ray tracing: ") + line + ", flags 0x" +
              std::to_string(static_cast<uint32_t>(flags)));
    return false;
}

bool on_dispatch_rays(command_list *cmd_list, resource, uint64_t, uint64_t, resource, uint64_t, uint64_t, uint64_t,
                      resource, uint64_t, uint64_t, uint64_t, resource, uint64_t, uint64_t, uint64_t, uint32_t width,
                      uint32_t height, uint32_t depth)
{
    const std::lock_guard lock(g_seen_mutex);
    ++g_dispatch_counts["dispatch_rays"];
    if (g_capture.load(std::memory_order_relaxed) == Capture::armed && g_capture_order.size() < 400)
        g_capture_order.push_back("dispatch_rays " + std::to_string(width) + "x" + std::to_string(height) + "x" +
                                  std::to_string(depth) + ", cmd " +
                                  std::to_string(reinterpret_cast<uintptr_t>(cmd_list) & 0xFFFFFF));
    return false;
}

bool on_dispatch(command_list *cmd_list, uint32_t x, uint32_t y, uint32_t z)
{
    before_dispatch(cmd_list, std::to_string(x) + '\t' + std::to_string(y) + '\t' + std::to_string(z));
    return false;
}

// Draws with a tracked graphics pipeline bound are logged, and replacements
// get the tunables. The game sets its root signature (and root arguments)
// before drawing, so pushing here, not at bind time, is what survives.
// Cheap check first: this runs for every draw call in the frame.
bool tracked_graphics_bound(command_list *cmd_list)
{
    const auto *state = cmd_list->get_private_data<CommandListState>();
    return state != nullptr && state->graphics.handle != 0;
}

void before_draw(command_list *cmd_list, const std::string &size)
{
    const auto *state = cmd_list->get_private_data<CommandListState>();
    TrackedPipeline info;
    std::array<float, kTunableCount> tunables;
    {
        const std::shared_lock lock(g_mutex);
        const auto it = g_pipelines.find(state->graphics.handle);
        if (it == g_pipelines.end())
            return;
        info = it->second;
        tunables = info.radar ? g_effective_radar_tunables : g_effective_tunables;  // R0..R13 for the radar
    }
    // The viewport gives the target size: for the radar, the display's resolution.
    char viewport[48];
    snprintf(viewport, sizeof(viewport), ", viewport %.0fx%.0f", state->viewport_width, state->viewport_height);
    log_dispatch(info, size + viewport);
    {
        const std::lock_guard lock(g_seen_mutex);
        ++g_dispatch_counts[info.entry];
    }
    if (info.replacement.handle != 0 && info.mod_param != UINT32_MAX && g_enabled.load(std::memory_order_relaxed))
    {
        push_mod_args(cmd_list, info, tunables);
        if (info.radar && g_auto_capture_radar.exchange(false))
        {
            g_capture_radar_due = true;
            g_auto_capture_due = true;  // radar first switched on: capture what it sees
        }
    }
}

bool on_draw(command_list *cmd_list, uint32_t vertices, uint32_t instances, uint32_t, uint32_t)
{
    if (tracked_graphics_bound(cmd_list))
        before_draw(cmd_list, "draw\t" + std::to_string(vertices) + " vertices\t" + std::to_string(instances) +
                    " instances");
    return false;
}

bool on_draw_indexed(command_list *cmd_list, uint32_t indices, uint32_t instances, uint32_t, int32_t, uint32_t)
{
    if (tracked_graphics_bound(cmd_list))
        before_draw(cmd_list, "draw_indexed\t" + std::to_string(indices) + " indices\t" +
                    std::to_string(instances) + " instances");
    return false;
}

bool on_draw_or_dispatch_indirect(command_list *cmd_list, indirect_command type, resource, uint64_t, uint32_t count,
                                  uint32_t)
{
    if (type == indirect_command::dispatch)
        before_dispatch(cmd_list, "indirect\tx" + std::to_string(count) + '\t');
    else if ((type == indirect_command::draw || type == indirect_command::draw_indexed) &&
             tracked_graphics_bound(cmd_list))
        before_draw(cmd_list, "draw_indirect\tx" + std::to_string(count) + '\t');
    return false;
}

// Once per frame: tunables.ini with the weather feed's smoothed values blended
// in by T13 (weather_feed.h). Shaders get these, never g_tunables directly.
void update_effective_tunables()
{
    static auto last = std::chrono::steady_clock::now();
    const auto now = std::chrono::steady_clock::now();
    const float dt = std::chrono::duration<float>(now - last).count();
    last = now;
    std::array<float, kTunableCount> ini, radar;
    std::array<float, kCloudTunableCount> cloud;
    float ozone_setting;
    {
        const std::shared_lock lock(g_mutex);
        ini = g_tunables;
        radar = g_radar_tunables;
        cloud = g_cloud_tunables;
        ozone_setting = g_ozone_setting;
    }
    const auto effective = weather::apply(ini, dt);
    // Ozone: T14's fixed column, else the climatology (300 DU until the feed has the aircraft).
    const float du = ozone_setting > 0.0f ? ozone_setting : weather::ozone_du();
    g_ozone_scale.store(std::clamp((std::isfinite(du) ? du : static_cast<float>(ozone::kReferenceDu)) /
                                       static_cast<float>(ozone::kReferenceDu), 0.25f, 2.0f),
                        std::memory_order_relaxed);
    if (radar[4] <= 0.0f)  // R4 = 0: freezing level from the METAR temperature
    {
        const float level = weather::freezing_level_m();
        radar[4] = std::isfinite(level) ? level : kFallbackFreezingLevel;
    }
    if (const weather::AircraftRadar aircraft = weather::aircraft_radar(); aircraft.valid)
        radar[2] += aircraft.tilt_deg;  // the PMDG 737's tilt knob; R2 is then an offset
    // The aircraft's shadow on clouds: C13-C15 from SimConnect. C15 is the span
    // in the cockpit view (the drawn silhouette may stand in), minus the span
    // outside it (only the sun's shadow map), 0 without the feed. C12
    // (darkness) stays the ini's.
    const weather::AircraftShadow aircraft = weather::aircraft_shadow();
    const bool drawn = aircraft.valid && aircraft.cockpit;
    cloud[13] = drawn ? aircraft.heading_from_sun : 0.0f;
    cloud[14] = drawn ? aircraft.bank : 0.0f;
    cloud[15] = aircraft.valid ? (aircraft.cockpit ? aircraft.span_m : -aircraft.span_m) : 0.0f;
    // METAR anchoring: C20 (strength) and C21 (radius) are the ini's; the
    // stations go to the generator as a table (upload_anchor_table).
    g_storms_off.store(!(cloud[32] > 0.0f), std::memory_order_relaxed);

    const std::unique_lock lock(g_mutex);
    g_effective_tunables = effective;
    g_effective_radar_tunables = radar;
    g_effective_cloud_tunables = cloud;
}

// The state the EFB app's settings show (settings_bridge.h): tunables.ini's T
// and C values, the atmosphere tunables the shaders get (TE, rounded: they
// drift while the METAR's haze is smoothed in), the Ctrl+Shift+F9 switch and
// the METAR's haze in a line.
std::string settings_state()
{
    std::array<float, kTunableCount> ini, effective;
    std::array<float, kCloudTunableCount> cloud;
    {
        const std::shared_lock lock(g_mutex);
        ini = g_tunables;
        effective = g_effective_tunables;
        cloud = g_cloud_tunables;
    }
    const auto list = [](const auto &values, bool rounded) {
        std::string s = "[";
        for (const float v : values)
        {
            char buf[32];
            if (rounded)
                snprintf(buf, sizeof(buf), "%.3g", v);
            s += (s.size() > 1 ? "," : "") + (rounded ? std::string(buf) : settings::format(v));
        }
        return s + "]";
    };
    std::string weather;
    for (const char c : weather::summary())
        weather += c == '"' || c == '\\' ? std::string("\\") + c : std::string(1, c);
    return std::string("{\"enabled\":") + (g_enabled ? "true" : "false") + ",\"T\":" + list(ini, false) +
           ",\"C\":" + list(cloud, false) + ",\"TE\":" + list(effective, true) + ",\"weather\":\"" + weather + "\"}";
}

// The EFB app's settings: applies what it asked for, saves the changes into
// tunables.ini once they have rested for a second (at once before a reload),
// and publishes the state it shows, twice a second. Returns whether to reload
// (Ctrl+Shift+F10 pressed, or the app's Reload button).
bool sync_settings(bool reload_key)
{
    using namespace std::chrono_literals;
    static std::vector<settings::Change> unsaved;
    static auto last_change = std::chrono::steady_clock::now(), last_publish = last_change;
    const auto now = std::chrono::steady_clock::now();
    const settings::Requests requests = settings::take();
    if (requests.reload)
        event(reshade::log::level::info, "reload asked for by the EFB app");
    const bool flush = reload_key || requests.reload;
    if (requests.enabled && *requests.enabled != g_enabled)
    {
        g_enabled = *requests.enabled;
        event(reshade::log::level::info, g_enabled ? "replacements ON (EFB app)" : "replacements OFF (EFB app)");
    }
    if (!requests.set.empty())
    {
        const std::unique_lock lock(g_mutex);
        for (const settings::Change &c : requests.set)
        {
            if (c.prefix == 'T' && c.index < kTunableCount)
                g_tunables[c.index] = c.value;
            else if (c.prefix == 'C' && c.index < kCloudTunableCount && (c.index < 13 || c.index > 15))  // (C13-C15: ours)
                g_cloud_tunables[c.index] = c.value;
            else
                continue;
            unsaved.push_back(c);
        }
        last_change = now;
    }
    if (!unsaved.empty() && (flush || now - last_change >= 1s))
    {
        const fs::path path = tunables_path();
        if (settings::save(path, unsaved))
            event(reshade::log::level::info, "saved " + std::to_string(unsaved.size()) +
                  " setting change(s) from the EFB app to " + path.string());
        else
            event(reshade::log::level::warning, "couldn't save the EFB app's settings to " + path.string());
        unsaved.clear();
    }
    if (!requests.set.empty() || requests.enabled || now - last_publish >= 500ms)
    {
        last_publish = now;
        settings::publish(settings_state());
    }
    return flush;
}

void on_present(effect_runtime *runtime)
{
    const uint64_t frame = ++g_frame;
    // (once per game device: again if the game ever recreates its device)
    static device *debug_tried = nullptr;
    if (g_debug_device == nullptr && runtime->get_device() != debug_tried)
    {
        debug_tried = runtime->get_device();
        create_debug_resources(debug_tried);
    }
    const bool modifiers = runtime->is_key_down(VK_CONTROL) && runtime->is_key_down(VK_SHIFT);
    if (modifiers && runtime->is_key_pressed(VK_F9))
    {
        g_enabled = !g_enabled;
        event(reshade::log::level::info, g_enabled ? "replacements ON" : "replacements OFF (stock shaders)");
    }
    // (the EFB app's unsaved changes go to tunables.ini before the reload reads it)
    if (sync_settings(modifiers && runtime->is_key_pressed(VK_F10)))
        reload(runtime->get_device());
    if ((modifiers && runtime->is_key_pressed(VK_F11)) || g_auto_capture_due.exchange(false))
        request_capture(frame);
    advance_capture(runtime, frame);
    if (frame % kDispatchRateFrames == 0)
        log_dispatch_rates(frame);
    collect_garbage(frame);
    update_effective_tunables();
    upload_anchor_table(runtime, frame);
}

void open_logs()
{
    const fs::path dir = g_data_dir / "logs";
    std::error_code ec;
    fs::create_directories(dir, ec);
    g_pipeline_log.open(dir / "pipelines.tsv", std::ios::trunc);
    g_pipeline_log << "crc32\tstage\tentry\tlayout\n";
    g_dispatch_log.open(dir / "dispatches.tsv", std::ios::trunc);
    g_dispatch_log << "crc32\tentry\tgroups_x\tgroups_y\tgroups_z\n";
    g_layout_log.open(dir / "layouts.txt", std::ios::trunc);
    g_event_log.open(dir / "events.log", std::ios::trunc);
    g_rootsig_log.open(dir / "rootsigs.log", std::ios::trunc);
    g_create_log.open(dir / "compute_creates.tsv", std::ios::trunc);
    g_create_log << "crc32\tlayout\n";
    g_texture_log.open(dir / "textures3d.tsv", std::ios::trunc);
    g_texture_log << "width\theight\tdepth\tlevels\tdxgi_format\tusage\theap\n";
    g_rate_log.open(dir / "dispatch_rates.log", std::ios::trunc);
}
} // namespace

extern "C" __declspec(dllexport) bool AddonInit(HMODULE addon_module, HMODULE reshade_module)
{
    if (!reshade::register_addon(addon_module, reshade_module))
        return false;

    g_data_dir = resolve_data_dir(addon_module);
    reshade::get_config_value(nullptr, "ATMOSPHEREMOD", "InjectRootSignature", g_inject);
    reshade::get_config_value(nullptr, "ATMOSPHEREMOD", "InjectGraphicsRootSignature", g_inject_graphics);
    open_logs();
    load_names();
    g_replacements = load_replacements();
    g_tunables = load_tunables();
    g_ozone_setting = load_tunables<kTunableCount + 1>()[kTunableCount];  // T14
    weather::set_haze_field(load_tunable('T', 15, 1.0f) > 0.0f);
    g_effective_tunables = g_tunables;
    g_radar_tunables = load_tunables('R');
    g_cloud_tunables = load_tunables<kCloudTunableCount>('C');
    bool weather_feed = true;
    reshade::get_config_value(nullptr, "ATMOSPHEREMOD", "WeatherFeed", weather_feed);
    if (weather_feed)
        weather::start(g_data_dir / "logs");

    reshade::register_event<reshade::addon_event::destroy_device>(on_destroy_device);
    reshade::register_event<reshade::addon_event::init_command_list>(on_init_command_list);
    reshade::register_event<reshade::addon_event::destroy_command_list>(on_destroy_command_list);
    reshade::register_event<reshade::addon_event::reset_command_list>(on_reset_command_list);
    reshade::register_event<reshade::addon_event::create_pipeline_layout>(on_create_pipeline_layout);
    reshade::register_event<reshade::addon_event::init_pipeline_layout>(on_init_pipeline_layout);
    reshade::register_event<reshade::addon_event::destroy_pipeline_layout>(on_destroy_pipeline_layout);
    reshade::register_event<reshade::addon_event::create_pipeline>(on_create_pipeline);
    reshade::register_event<reshade::addon_event::init_pipeline>(on_init_pipeline);
    reshade::register_event<reshade::addon_event::destroy_pipeline>(on_destroy_pipeline);
    reshade::register_event<reshade::addon_event::init_resource>(on_init_resource);
    reshade::register_event<reshade::addon_event::bind_pipeline>(on_bind_pipeline);
    reshade::register_event<reshade::addon_event::bind_viewports>(on_bind_viewports);
    reshade::register_event<reshade::addon_event::dispatch>(on_dispatch);
    reshade::register_event<reshade::addon_event::build_acceleration_structure>(on_build_acceleration_structure);
    reshade::register_event<reshade::addon_event::dispatch_rays>(on_dispatch_rays);
    reshade::register_event<reshade::addon_event::draw>(on_draw);
    reshade::register_event<reshade::addon_event::draw_indexed>(on_draw_indexed);
    reshade::register_event<reshade::addon_event::draw_or_dispatch_indirect>(on_draw_or_dispatch_indirect);
    reshade::register_event<reshade::addon_event::reshade_present>(on_present);

    event(reshade::log::level::info, "data dir " + g_data_dir.string() + ", " + std::to_string(g_names.size()) +
          " shader names, " + std::to_string(g_replacements.size()) + " replacement(s), root signature injection " +
          (g_inject ? "on" : "off") + " (graphics " + (g_inject_graphics ? "on" : "off") + ")");
    return true;
}

extern "C" __declspec(dllexport) void AddonUninit(HMODULE addon_module, HMODULE reshade_module)
{
    weather::stop();
    reshade::unregister_addon(addon_module, reshade_module);
}
