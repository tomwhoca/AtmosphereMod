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
//    root UAV at u0 space50 (debug buffer), a root CBV at b1 space50 (cloud
//    tunables C0..C35, from an upload ring) and a root SRV at t0 space50 (MSFS's
//    TLAS): 28 of the 64 dwords. Replacement shaders write the game constants
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
#include "file_dialog.h"
#include "ozone_climatology.h"
#include "perf_report.h"
#include "settings_bridge.h"
#include "weather_feed.h"

#include <algorithm>
#include <array>
#include <atomic>
#include <bit>
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
// Drawn after the clouds, fogged by their distance (tools/patch_dxil.py patch_forward_fog).
const std::unordered_set<std::string> kForwardFogEntries = {"ph_base", "ph_particle", "ph_billboard_lights",
                                                             "ph_macro_lights"};

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
// The register space of the mod's root parameters: [ATMOSPHEREMOD] RegisterSpace, which the installer sets along with
// the patched shaders (--renodx, tools/install.py); a replacement in another space than this fails to create and stays stock.
uint32_t g_mod_space = 50;
constexpr uint32_t kTunableCount = 14;  // T0..T13
struct ModParams
{
    uint32_t debug_capture = 0;  // bit 0: capture armed; bit 1: radar draw (R0..R13 below); bit 2: 16 KB debug buffer (compute)
    uint32_t frame = 0;
    float extra[2] = {};      // T12 and the ozone scale (push_mod_args; radar draws: R12, R13)
    float tunables[12] = {};  // T0..T11
};
static_assert(sizeof(ModParams) == 64);
// Cloud tunables C0..C35 (tunables.ini). Compute only: a root CBV at b1
// space50 (CloudParams, C0..C35 by index, the spare values 0), read by the
// patched cloud renderer, cloud generator, ch_deferred_shadows and the
// transmittance LUT (tools/patch_dxil.py, cloud_prologue_lines). It points at
// a slot of an upload buffer the present thread writes when they change
// (write_cloud_settings), each change to the next slot: a command list keeps
// the slot it was recorded with, and the GPU, a few frames behind at most, is
// done with it long before the ring comes round again. (They were 36 root
// constants until 0.7.2: the root signature's 64 dwords were then all but full.)
// C32 (thunderstorms) reaches the shaders as ModParams' DebugCapture bit 8
// (push_mod_args); C35 (developers: > 0 tints what the forward pass fogs, as
// the graphics DebugCapture bit 13; < 0 turns that fog off) likewise.
constexpr uint32_t kCloudTunableCount = 36;
constexpr uint32_t kCloudSettingsSlotBytes = 256;  // D3D12's constant buffer alignment; room for 64 values
constexpr uint32_t kCloudSettingsSlots = 256;
// (patch_dxil.py's CLOUD_PARAMS_SIZE, what the shaders read, is at most a slot; one spare slot at the end keeps
// even a longer read inside the buffer)
constexpr uint64_t kCloudSettingsBytes = uint64_t(kCloudSettingsSlots + 1) * kCloudSettingsSlotBytes;
static_assert(kCloudTunableCount * 4 <= kCloudSettingsSlotBytes);
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
constexpr uint32_t kCloudGenCloud = 3072;  // the cloud constants C0..C35 as the generator sees them
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
// The fog around the camera for see-through surfaces (patch_dxil.py
// FOG_REGION): the cloud renderer records, every frame, its first march
// step's extinction and radiance at 4 screen points, float4 then uint frame
// | 0x10000, 32 bytes each; then the cloud merge its factor from the clouds'
// colour to the scene's (FOG_SCALE: float, uint frame | 0x10000); the patched
// forward pass (ph_base) reads them.
constexpr uint32_t kFogRegion = 0x3E3000;
constexpr uint32_t kFogPoints = 4;
constexpr uint32_t kFogPointBytes = 32;
constexpr uint32_t kFogScale = kFogRegion + kFogPoints * kFogPointBytes;
// The merge's fog colour grid (patch_dxil.py FOG_GRID): per cell float4(colour, alpha).
constexpr uint32_t kFogGrid = kFogRegion + 0x100, kFogGridX = 32, kFogGridY = 18;
static_assert(kFogScale + 8 <= kFogGrid);
static_assert(kCloudShRegion + 2 * kCloudShStride <= kFogRegion);
static_assert(kFogGrid + kFogGridX * kFogGridY * 16 <= kCloudGenRegion);
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
    uint32_t fog_mode = 0;  // kForwardFogEntries: the fog for its blending (forward_fog_mode), 0 = not replaced
    uint32_t mod_param = UINT32_MAX;  // first injected root parameter, if any
    uint32_t cloud_param = UINT32_MAX;  // injected cloud constants (compute only)
    uint32_t tlas_param = UINT32_MAX;   // injected root SRV for MSFS's TLAS (compute only)
    pipeline replacement = {};
    uint32_t shader_index = UINT32_MAX;  // the replacement's shader in g_mod_shaders_ran
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
    pipeline graphics_any = {};  // any graphics pipeline with a pixel shader, for the capture's draw log
    uint32_t draw_index = 0;     // the draw log's draws on this list
    float viewport_width = 0.0f, viewport_height = 0.0f;  // first viewport, for draw logs
    bool timeable = false;          // a direct or compute list of the game's device (kept over resets)
    std::vector<uint32_t> timers;   // its timestamps since the reset, resolved at the close
    // the performance report's timestamps at its start and its close (g_frame_timer_next counts), or none;
    // pending: reset while measuring, the start comes with its first command (start_frame_timing)
    uint64_t frame_begin = UINT64_MAX, frame_end = UINT64_MAX;
    bool frame_pending = false;
};

// Every graphics pipeline's pixel shader and how it writes its targets
// (graphics.tsv), not only the tracked ones: for the capture's draw log.
struct GraphicsShader
{
    uint32_t crc = 0;
    std::string entry;
    std::string blend;  // RT0's and RT1's blending and the depth test (describe_output)
};

std::shared_mutex g_mutex;  // guards the containers below
std::unordered_map<uint32_t, std::string> g_names;                  // crc -> entry
std::unordered_map<uint32_t, std::vector<uint8_t>> g_replacements;  // crc -> DXIL
// The forward pass's replacements (thousands, ~100 MB): read when a pipeline needs one.
std::unordered_map<uint32_t, fs::path> g_lazy_replacements;         // crc -> file
std::unordered_map<uint64_t, TrackedPipeline> g_pipelines;          // pipeline -> info
std::unordered_map<uint64_t, GraphicsShader> g_graphics;            // graphics pipeline -> pixel shader
std::unordered_map<uint64_t, uint32_t> g_compute_shaders;           // compute pipeline -> shader crc (draw log)
std::unordered_map<uint32_t, uint32_t> g_shader_indices;            // a replaced shader's crc -> its index
std::unordered_map<uint64_t, std::vector<LayoutParam>> g_layouts;   // layout -> params
std::unordered_map<uint64_t, uint32_t> g_injected;                  // layout -> first mod param
std::unordered_map<uint64_t, uint32_t> g_cloud_injected;            // layout -> cloud constants param
std::unordered_map<uint64_t, uint32_t> g_tlas_injected;             // layout -> TLAS root SRV param

// The mod's shaders that ran while a performance report measured with the mod on (perf::Measurement
// mod_shaders): whether the mod did anything in that view. Each replaced shader's bit (by its index in
// g_shader_indices) and every bind of a replacement, counted on the threads that record while
// g_counting_mod_shaders; the present thread clears them when a measurement starts and reads them each frame.
constexpr uint32_t kModShaderBits = 16384;
std::array<std::atomic<uint64_t>, kModShaderBits / 64> g_mod_shaders_ran{};
std::atomic<uint64_t> g_mod_shader_runs{0};
std::atomic<bool> g_counting_mod_shaders{false};

// A replaced shader's index for g_mod_shaders_ran (the caller holds g_mutex exclusively).
uint32_t shader_index_locked(uint32_t crc)
{
    return g_shader_indices.try_emplace(crc, uint32_t(g_shader_indices.size())).first->second;
}

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
std::array<float, kCloudTunableCount> g_cloud_tunables = {};       // tunables.ini C0..C35
std::array<float, kCloudTunableCount> g_effective_cloud_tunables = {};  // pushed: C13-C15 = the aircraft (SimConnect)
// C32 <= 0: no thunderstorm cells (DebugCapture bit 8 to the cloud generator)
std::atomic<bool> g_storms_off{true};
std::atomic<bool> g_cloud_tilt_view{false};  // T17 > 0 (developers): DebugCapture bit 14, patch_dxil.py DEBUG_CLOUD_TILT
std::atomic<float> g_cloud_plane_light{0.0f};  // C36 (0..1, the app's Sun-facing cloud sides): pushed as C35's slot, patch_dxil.py PLANE_*
// Varied cloud tops, C37 (tunables.ini only): 0 MSFS's, 1 gentle tops (DebugCapture bit 15), 2 also rising air at full
// strength (C30's slot -1), the default; patch_dxil.py TOP_*. (experiment/cloud-detail: more states, C38, Ctrl+Shift+F7)
std::atomic<int> g_cloud_tops{2};
std::atomic<bool> g_fog_tint{false};  // C35 > 0: DebugCapture bit 13 on graphics draws
std::atomic<bool> g_forward_fog_off{false};  // C35 < 0: no fog on the forward pass (bits 10-12 left 0)
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
std::ofstream g_graphics_log;  // graphics.tsv: every graphics pipeline's pixel shader and output state

// Debug buffer, its root UAV view, and the CPU readback copy.
resource g_debug_buffer = {};
resource_view g_debug_view = {};
resource g_readback = {};
resource g_anchor_upload = {};  // kAnchorUploadSlots station tables, upload heap
device *g_debug_device = nullptr;  // the debug resources belong to it (the game's device)
// The cloud tunables' ring (upload heap, mapped while it exists) and the GPU address of the slot written last:
// 0 until the first write, and then compute replacements stay unbound (their shaders would read an unset CBV).
resource g_cloud_settings = {};
device *g_cloud_settings_device = nullptr;
uint8_t *g_cloud_settings_mapped = nullptr;
uint64_t g_cloud_settings_base = 0;
std::atomic<uint64_t> g_cloud_settings_va{0};
uint64_t g_cloud_settings_writes = 0;  // (present thread)
std::array<float, kCloudTunableCount> g_cloud_settings_written = {};
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
// On capture, the draws of the next whole frame, in recording order per
// command list, and the order the command lists ran in: captures/<id>_draws.tsv
// (what the game draws after its clouds, and how it blends it).
std::mutex g_draw_log_mutex;
std::vector<std::string> g_draw_log;
std::atomic<uint64_t> g_draw_log_frame{UINT64_MAX};  // the frame being logged
uint32_t g_draw_log_id = 0;                          // present thread only
constexpr size_t kDrawLogMax = 60000;
// The draw log's GPU time: a timestamp before every draw and dispatch it logs and one
// at each such list's close, resolved at the close (not after each query: that would
// make the GPU wait at every draw). ReShade's readback doesn't wait for the GPU, so
// the log is written kGpuTimerDelayFrames after its frames.
constexpr uint32_t kGpuTimers = 65536;
constexpr uint64_t kGpuTimerDelayFrames = 10;
ID3D12QueryHeap *g_timer_heap = nullptr;  // the game's device (g_debug_device)
resource g_timer_readback = {};
std::atomic<uint32_t> g_timer_next{0};
std::atomic<uint64_t> g_timer_frequency{0};  // the graphics queue's ticks per second
// The performance report's GPU time per frame (perf_report.h): while a measurement
// runs, a timestamp at the start and at the close of each direct or compute list
// of the game's, each resolved at the close into its slot of a ring; at execute
// the list is filed under the frame (g_frame); kFrameTimerDelay frames later,
// when the GPU has run it, the frame's lists give its working time (busy_ms).
constexpr uint32_t kFrameTimers = 32768;
constexpr uint64_t kFrameTimerDelay = 8;
ID3D12QueryHeap *g_frame_timer_heap = nullptr;  // the game's device (g_debug_device)
resource g_frame_timer_readback = {};
std::atomic<bool> g_frame_timing{false};
std::atomic<uint64_t> g_frame_timer_next{0};  // timestamps taken so far; slot = count % kFrameTimers
std::mutex g_frame_timer_mutex;               // guards the maps and the counts
struct TimedList
{
    uint64_t begin, end;          // g_frame_timer_next counts: slot = count % kFrameTimers
    ID3D12CommandQueue *queue;    // where it ran: its timestamps are on that queue's clock
};
std::map<uint64_t, std::vector<TimedList>> g_frame_lists;  // frame -> the lists executed in it
std::map<uint64_t, perf::Phase> g_frame_phase;  // frame -> the part of the measurement it was rendered in
void start_frame_timing(command_list *cmd_list);
// What became of the frames, for the log: GPU-timed, or why not.
struct GpuTimingCounts
{
    uint64_t lists = 0, timed = 0, unrun = 0, invalid = 0, overwritten = 0, no_clock = 0;
    uint64_t stamps_at_start = 0, frames_ready = 0, frames_outside = 0;  // (outside: not in a measured part)
    std::map<ID3D12CommandQueue *, uint64_t> queues;  // -> its timestamp frequency
    std::string first;  // the first frame read back, in detail
} g_gpu_counts;
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

std::string entry_name(uint32_t crc);

std::vector<uint8_t> read_file(const fs::path &path)
{
    std::ifstream file(path, std::ios::binary);
    return std::vector<uint8_t>((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());
}

// The replacements in <DataPath>/shaders, by crc; the forward pass's go to
// `lazy` (their files only).
std::unordered_map<uint32_t, std::vector<uint8_t>> load_replacements(std::unordered_map<uint32_t, fs::path> &lazy)
{
    std::unordered_map<uint32_t, std::vector<uint8_t>> result;
    lazy.clear();
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
        if (entry_name(crc) == "ph_base")
        {
            lazy[crc] = item.path();
            continue;
        }
        std::vector<uint8_t> code = read_file(item.path());
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

// The EFB app's presets (settings_bridge.h): <DataPath>/presets, Default.ini from the installer.
fs::path presets_dir()
{
    return g_data_dir / "presets";
}

// The tunables the add-on reads: T0..T16 (T14..T16 its own), R0..R13, C0..C37 (C36, C37 its own).
bool known_tunable(char prefix, unsigned index)
{
    return (prefix == 'T' && index < kTunableCount + 3) || (prefix == 'R' && index < kTunableCount) ||
           (prefix == 'C' && index < kCloudTunableCount + 2);
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

const char *factor_name(blend_factor f)
{
    static const char *const kNames[] = { "0", "1", "sc", "1-sc", "dc", "1-dc", "sa", "1-sa", "da", "1-da", "k",
                                          "1-k", "ka", "1-ka", "sa_sat", "s1c", "1-s1c", "s1a", "1-s1a" };
    const auto i = static_cast<uint32_t>(f);
    return i < std::size(kNames) ? kNames[i] : "?";
}

const char *op_name(blend_op op)
{
    static const char *const kNames[] = { "+", "-", "rev-", "min", "max" };
    const auto i = static_cast<uint32_t>(op);
    return i < std::size(kNames) ? kNames[i] : "?";
}

// A graphics pipeline's outputs for graphics.tsv: its render targets' formats
// (DXGI), the depth test and write, and RT0's and RT1's blending as
// "x*src op y*dst, a: x op y" ("s1c" and "s1a" are dual-source factors,
// SV_Target1). `summary` gets the blending and depth, for the draw log.
std::string describe_output(uint32_t count, const pipeline_subobject *subobjects, std::string &summary)
{
    std::string rts = "-", depth = "-", rt[2] = { "-", "-" }, coverage;
    for (uint32_t i = 0; i < count; ++i)
    {
        const pipeline_subobject &s = subobjects[i];
        if (s.data == nullptr)
            continue;
        if (s.type == pipeline_subobject_type::render_target_formats)
        {
            rts = std::to_string(s.count) + " RT";
            for (uint32_t r = 0; r < s.count; ++r)
                rts += (r ? "," : " ") + std::to_string(static_cast<uint32_t>(static_cast<const format *>(s.data)[r]));
        }
        else if (s.type == pipeline_subobject_type::depth_stencil_state)
        {
            const auto &d = *static_cast<const depth_stencil_desc *>(s.data);
            depth = std::string(d.depth_enable ? "depth test" : "no depth test") + (d.depth_write_mask ? "+write" : "");
        }
        else if (s.type == pipeline_subobject_type::blend_state)
        {
            const auto &b = *static_cast<const blend_desc *>(s.data);
            for (int r = 0; r < 2; ++r)
            {
                char buf[128];
                if (b.blend_enable[r])
                    snprintf(buf, sizeof(buf), "%s*src %s %s*dst, a: %s %s %s, mask %X",
                             factor_name(b.source_color_blend_factor[r]), op_name(b.color_blend_op[r]),
                             factor_name(b.dest_color_blend_factor[r]), factor_name(b.source_alpha_blend_factor[r]),
                             op_name(b.alpha_blend_op[r]), factor_name(b.dest_alpha_blend_factor[r]),
                             b.render_target_write_mask[r]);
                else
                    snprintf(buf, sizeof(buf), "off, mask %X", b.render_target_write_mask[r]);
                rt[r] = buf;
            }
            if (b.alpha_to_coverage_enable)
                coverage = ", alpha to coverage";
        }
    }
    summary = "RT0 " + rt[0] + " | RT1 " + rt[1] + " | " + depth + coverage;
    return rts + '\t' + depth + '\t' + rt[0] + '\t' + rt[1] + coverage;
}

// The forward pass's blending, for its fog (tools/patch_dxil.py
// patch_forward_fog, FOG_*), by what it draws into. Into the scene colour
// (R11G11B10_FLOAT): 1 dual source (src0 + src1 dst), 2 alpha, 3
// premultiplied alpha, 4 additive. Into the refraction buffer (one
// RGBA16F target, premultiplied: refraction offsets, blur and coverage, not
// colour): 5, the fog fades them. 0 for anything else, which keeps the stock
// shader (opaque: MSFS draws those before its clouds, whose fog covers them;
// other buffers, which aren't colour).
uint32_t forward_fog_mode(uint32_t count, const pipeline_subobject *subobjects)
{
    format rt0 = format::unknown;
    uint32_t targets = 0;
    for (uint32_t i = 0; i < count; ++i)
        if (subobjects[i].type == pipeline_subobject_type::render_target_formats && subobjects[i].data != nullptr &&
            subobjects[i].count > 0)
        {
            rt0 = static_cast<const format *>(subobjects[i].data)[0];
            targets = subobjects[i].count;
        }
    for (uint32_t i = 0; i < count; ++i)
    {
        if (subobjects[i].type != pipeline_subobject_type::blend_state || subobjects[i].data == nullptr)
            continue;
        const auto &b = *static_cast<const blend_desc *>(subobjects[i].data);
        if (!b.blend_enable[0] || b.color_blend_op[0] != blend_op::add)
            return 0;
        const blend_factor src = b.source_color_blend_factor[0], dst = b.dest_color_blend_factor[0];
        if (rt0 == format::r16g16b16a16_float && targets == 1)
            return src == blend_factor::one && dst == blend_factor::one_minus_source_alpha ? 5 : 0;
        if (rt0 != format::r11g11b10_float)
            return 0;
        if (src == blend_factor::one && dst == blend_factor::source1_color)
            return 1;
        if (src == blend_factor::source_alpha && dst == blend_factor::one_minus_source_alpha)
            return 2;
        if (src == blend_factor::one && dst == blend_factor::one_minus_source_alpha)
            return 3;
        if (dst == blend_factor::one && (src == blend_factor::one || src == blend_factor::source_alpha))
            return 4;
        return 0;
    }
    return 0;
}

// Every graphics pipeline with a pixel shader, once: graphics.tsv, and its
// shader for the capture's draw log.
void record_graphics(pipeline pipe, uint32_t crc, uint32_t count, const pipeline_subobject *subobjects)
{
    GraphicsShader ps;
    ps.crc = crc;
    const std::string fields = describe_output(count, subobjects, ps.blend);
    {
        const std::unique_lock lock(g_mutex);
        if (const auto it = g_names.find(crc); it != g_names.end())
            ps.entry = it->second;
        if (!g_graphics.try_emplace(pipe.handle, ps).second)
            return;  // (init_pipeline repeats for the same handle)
    }
    write_line(g_graphics_log, hex32(crc) + '\t' + (ps.entry.empty() ? "?" : ps.entry) + '\t' + hex64(pipe.handle) +
               '\t' + fields);
}

// Two frames: MSFS records a frame's command lists across presents (and frame
// generation presents twice per rendered frame), so one often caught a fragment.
constexpr uint64_t kDrawLogFrames = 2;

bool draw_log_on()
{
    const uint64_t first = g_draw_log_frame.load(std::memory_order_relaxed);
    const uint64_t frame = g_frame.load(std::memory_order_relaxed);
    return frame >= first && frame < first + kDrawLogFrames;
}

// A timestamp on the list now, for the draw log's GPU time: "#index", or "-" without one.
std::string gpu_timestamp(command_list *cmd_list, CommandListState &state)
{
    if (g_timer_heap == nullptr || !state.timeable || cmd_list->get_device() != g_debug_device)
        return "-";
    const uint32_t index = g_timer_next.fetch_add(1, std::memory_order_relaxed);
    if (index >= kGpuTimers)
        return "-";
    reinterpret_cast<ID3D12GraphicsCommandList *>(cmd_list->get_native())
        ->EndQuery(g_timer_heap, D3D12_QUERY_TYPE_TIMESTAMP, index);
    state.timers.push_back(index);
    return '#' + std::to_string(index);
}

void log_draw(command_list *cmd_list, const std::string &what)
{
    auto *state = cmd_list->get_private_data<CommandListState>();
    if (state == nullptr)
        return;
    GraphicsShader ps;
    {
        const std::shared_lock lock(g_mutex);
        if (const auto it = g_graphics.find(state->graphics_any.handle); it != g_graphics.end())
            ps = it->second;
    }
    std::string line = "draw\t" + hex64(reinterpret_cast<uintptr_t>(cmd_list)) + '\t' +
                       std::to_string(state->draw_index++) + '\t' + what + '\t' + hex32(ps.crc) + '\t' +
                       (ps.entry.empty() ? "?" : ps.entry) + '\t' + ps.blend + '\t' + gpu_timestamp(cmd_list, *state);
    const std::lock_guard lock(g_draw_log_mutex);
    if (g_draw_log.size() < kDrawLogMax)
        g_draw_log.push_back(std::move(line));
}

void write_draw_log()
{
    std::vector<std::string> lines;
    {
        const std::lock_guard lock(g_draw_log_mutex);
        lines.swap(g_draw_log);
    }
    // Each entry's GPU time: from its timestamp to the next one on its list (the next
    // draw or dispatch, or the list's close), so what the list does in between (copies,
    // clears, barriers) counts to the entry before it.
    const uint32_t timers = std::min(g_timer_next.load(), kGpuTimers);
    const uint64_t frequency = g_timer_frequency.load();
    std::vector<uint64_t> ticks;
    if (timers > 0 && frequency > 0 && g_debug_device != nullptr)
    {
        void *mapped = nullptr;
        if (g_debug_device->map_buffer_region(g_timer_readback, 0, uint64_t(timers) * 8, map_access::read_only,
                                              &mapped) && mapped)
        {
            ticks.assign(static_cast<const uint64_t *>(mapped), static_cast<const uint64_t *>(mapped) + timers);
            g_debug_device->unmap_buffer_region(g_timer_readback);
        }
    }
    std::vector<double> micros(lines.size(), -1.0);
    std::vector<uint64_t> at(lines.size(), 0);  // the timestamps of the entries timed (both ends)
    std::unordered_map<std::string, std::pair<size_t, uint64_t>> open;  // list -> its last timed entry
    double total = 0.0;
    for (size_t i = 0; i < lines.size() && !ticks.empty(); ++i)
    {
        const size_t hash = lines[i].rfind("\t#");
        if (hash == std::string::npos)
            continue;
        const uint32_t index = uint32_t(std::stoul(lines[i].substr(hash + 2)));
        const size_t tab = lines[i].find('\t');
        const std::string list = lines[i].substr(tab + 1, lines[i].find('\t', tab + 1) - tab - 1);
        const uint64_t t = index < ticks.size() ? ticks[index] : 0;
        if (const auto it = open.find(list); it != open.end())
        {
            const auto [before, t0] = it->second;
            if (t0 != 0 && t >= t0 && t - t0 < frequency)  // (under a second: else not run yet)
            {
                total += micros[before] = double(t - t0) * 1e6 / double(frequency);
                at[before] = t0;
                at[i] = t;
            }
        }
        if (lines[i].rfind("close\t", 0) == 0)
            open.erase(list);
        else
            open[list] = {i, t};
    }
    // ... and when, from the first: the GPU's timeline, with its idle gaps between the lists
    uint64_t first = UINT64_MAX, last = 0;
    for (const uint64_t t : at)
        if (t != 0)
            first = std::min(first, t), last = std::max(last, t);
    const fs::path dir = g_data_dir / "logs" / "captures";
    std::error_code ec;
    fs::create_directories(dir, ec);
    std::ofstream file(dir / (std::to_string(g_draw_log_id) + "_draws.tsv"));
    file << "kind\tcommand list\tindex\twhat\tcrc32\tentry\tblending and depth\tgpu us\tgpu at us\n";
    for (size_t i = 0; i < lines.size(); ++i)
    {
        const size_t hash = lines[i].rfind("\t#");
        if (hash == std::string::npos)
        {
            file << lines[i] << '\n';
            continue;
        }
        char us[32] = "-", when[32] = "-";
        if (micros[i] >= 0.0)
            snprintf(us, sizeof(us), "%.1f", micros[i]);
        if (at[i] != 0)
            snprintf(when, sizeof(when), "%.1f", double(at[i] - first) * 1e6 / double(frequency));
        file << lines[i].substr(0, hash) << '\t' << us << '\t' << when << '\n';
    }
    char timing[160] = "no GPU times (no timestamps, or not read back)";
    if (!ticks.empty() && last > first)
        snprintf(timing, sizeof(timing), "%u GPU timestamps, %.2f ms of GPU time between them over %.2f ms", timers,
                 total / 1000.0, double(last - first) * 1e3 / double(frequency));
    else if (!ticks.empty())
        snprintf(timing, sizeof(timing), "%u GPU timestamps, %.2f ms of GPU time between them", timers, total / 1000.0);
    event(reshade::log::level::info, "capture " + std::to_string(g_draw_log_id) + ": " +
          std::to_string(lines.size()) + " draws and command list executions logged; " + timing);
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
    std::unordered_map<uint32_t, fs::path> lazy;
    auto fresh = load_replacements(lazy);
    const auto tunables = load_tunables();
    const auto radar_tunables = load_tunables('R');
    const auto cloud_tunables = load_tunables<kCloudTunableCount>('C');
    const float ozone_setting = load_tunables<kTunableCount + 1>()[kTunableCount];  // T14
    weather::set_haze_field(load_tunable('T', 15, 1.0f) > 0.0f);
    weather::set_haze_strength(load_tunable('T', 16, 1.0f));
    g_cloud_tilt_view.store(load_tunable('T', 17, 0.0f) > 0.0f, std::memory_order_relaxed);
    g_cloud_plane_light.store(std::clamp(load_tunable('C', 36, 0.0f), 0.0f, 1.0f), std::memory_order_relaxed);
    g_cloud_tops.store(std::clamp(int(load_tunable('C', 37, 2.0f)), 0, 2), std::memory_order_relaxed);
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
        g_lazy_replacements = std::move(lazy);
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
        if (replacement.handle != 0)
            info->second.shader_index = shader_index_locked(info->second.crc);
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

// (from the first'th on)
std::string describe_params(const std::vector<LayoutParam> &params, size_t first = 0)
{
    std::string out;
    for (size_t i = first; i < params.size(); ++i)
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
    constants.dx_register_space = g_mod_space;
    constants.count = sizeof(ModParams) / 4;
    constants.visibility = shader_stage::all;
    extended.emplace_back(constants);

    // Both signatures also get the debug buffer as a root UAV. Graphics:
    // 23 + 16 + 2 = 41 of the 64 root-signature dwords.
    descriptor_range debug = {};  // single buffer UAV becomes a root UAV
    debug.dx_register_space = g_mod_space;
    debug.count = 1;
    debug.visibility = shader_stage::all;
    debug.type = descriptor_type::buffer_unordered_access_view;
    extended.emplace_back(debug);

    // Compute also gets the cloud tunables as a root CBV, b1 space50 (one
    // buffer: like the debug UAV, a root descriptor): 6 + 16 + 2 + 2 = 26 dwords.
    if (compute)
    {
        descriptor_range cloud = {};
        cloud.dx_register_index = 1;
        cloud.dx_register_space = g_mod_space;
        cloud.count = 1;
        cloud.visibility = shader_stage::all;
        cloud.type = descriptor_type::constant_buffer;
        extended.emplace_back(cloud);

        // ... and MSFS's ray-tracing scene (its TLAS) as a root SRV, t0 space50,
        // for the aircraft's shadow with Raytraced Shadows: 28 of the 64 dwords
        descriptor_range tlas = {};
        tlas.dx_register_space = g_mod_space;
        tlas.count = 1;
        tlas.visibility = shader_stage::all;
        tlas.type = descriptor_type::acceleration_structure;
        extended.emplace_back(tlas);
    }

    params = extended.data();
    param_count = static_cast<uint32_t>(extended.size());
    return true;
}

// Our root constants: ModParams at b0 space50.
bool is_mod_param(const pipeline_layout_param &param)
{
    return param.type == pipeline_layout_param_type::push_constants &&
           param.push_constants.dx_register_space == g_mod_space && param.push_constants.dx_register_index == 0;
}

// The cloud tunables' root CBV, b1 space50. (Only as a root descriptor: a table there would take a descriptor
// heap's slot, not the buffer's address.)
bool is_cloud_param(const pipeline_layout_param &param)
{
    return param.type == pipeline_layout_param_type::push_descriptors &&
           param.push_descriptors.type == descriptor_type::constant_buffer && param.push_descriptors.count == 1 &&
           param.push_descriptors.dx_register_space == g_mod_space && param.push_descriptors.dx_register_index == 1;
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
        // the haze rows (the patched ps_deferredRendering reads them then);
        // bits 10-12: the forward pass's blending, for its fog (forward_fog_mode);
        // bit 13: tint what it fogs (C35 > 0, developers; C35 < 0: the mode left
        // 0, no fog); bits 16-31: the frame, as for compute (the fog record's age)
        if (can_capture)
            p.debug_capture |= 64 | (g_haze_rows.load(std::memory_order_relaxed) ? kHazeFieldBit : 0) |
                               ((g_forward_fog_off.load(std::memory_order_relaxed) ? 0 : info.fog_mode) << 10) |
                               (g_fog_tint.load(std::memory_order_relaxed) ? 1u << 13 : 0) |
                               ((uint32_t(g_frame.load(std::memory_order_relaxed)) & 0xFFFF) << 16);
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
    if (g_cloud_tilt_view.load(std::memory_order_relaxed))
        p.debug_capture |= 1u << 14;
    if (g_cloud_tops.load(std::memory_order_relaxed) > 0)
        p.debug_capture |= 1u << 15;  // gentle tops
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

// The fog record for see-through surfaces (kFogRegion), as the readback has it.
void write_fog_record(std::ofstream &file, const uint8_t *data)
{
    file << "\nFog for see-through surfaces (the cloud renderer's first march step at 4 screen points; frame "
         << (g_frame.load() & 0xFFFF) << " now):\n";
    for (uint32_t p = 0; p < kFogPoints; ++p)
    {
        const auto *f = reinterpret_cast<const float *>(data + kFogRegion + kFogPointBytes * p);
        uint32_t stamp;
        std::memcpy(&stamp, data + kFogRegion + kFogPointBytes * p + 16, sizeof(stamp));
        char line[200];
        if ((stamp & 0x10000) == 0)
            snprintf(line, sizeof(line), "  point %u: not written (no cloud there yet)\n", p);
        else
            snprintf(line, sizeof(line),
                     "  point %u: extinction %.4g /m (visibility %.0f m), radiance %.4g %.4g %.4g, frame %u\n", p,
                     f[0], f[0] > 0.0f ? 3.0f / f[0] : 0.0f, f[1], f[2], f[3], stamp & 0xFFFF);
        file << line;
    }
    // the merge's factor from the clouds' colour to the scene's (patch_dxil.py FOG_SCALE)
    float scale;
    uint32_t stamp;
    std::memcpy(&scale, data + kFogScale, sizeof(scale));
    std::memcpy(&stamp, data + kFogScale + 4, sizeof(stamp));
    char line[200];
    if (stamp & 0x10000)
        snprintf(line, sizeof(line), "  the merge's factor to the scene: %.6g, frame %u\n", scale, stamp & 0xFFFF);
    else
        snprintf(line, sizeof(line), "  the merge's factor to the scene: not written\n");
    file << line;
    // the merge's fog colour grid: its range, and the cells that aren't finite
    const auto *cells = reinterpret_cast<const float *>(data + kFogGrid);
    uint32_t bad = 0, cloudy = 0, finite = 0;
    float lo = 1e30f, hi = -1e30f, alpha_sum = 0.0f;
    for (uint32_t i = 0; i < kFogGridX * kFogGridY; ++i)
    {
        const float *c = cells + 4 * i;
        if (!std::isfinite(c[0]) || !std::isfinite(c[1]) || !std::isfinite(c[2]) || !std::isfinite(c[3]))
        {
            ++bad;
            continue;
        }
        ++finite;
        alpha_sum += c[3];
        if (c[3] < 0.05f)
            continue;
        ++cloudy;
        lo = std::min({lo, c[0], c[1], c[2]});
        hi = std::max({hi, c[0], c[1], c[2]});
    }
    snprintf(line, sizeof(line),
             "  the merge's fog colour grid (%ux%u): %u cell(s) not finite, %u with cloud enough (alpha >= 0.05), "
             "colour %.4g .. %.4g, mean alpha %.3f\n",
             kFogGridX, kFogGridY, bad, cloudy, cloudy ? lo : 0.0f, cloudy ? hi : 0.0f,
             finite ? alpha_sum / float(finite) : 0.0f);
    file << line;
    for (const auto &[x, y] : {std::pair{4u, 3u}, std::pair{27u, 3u}, std::pair{16u, 9u}, std::pair{4u, 14u},
                               std::pair{27u, 14u}})
    {
        const float *c = cells + 4 * (y * kFogGridX + x);
        snprintf(line, sizeof(line), "    cell (%u, %u): colour %.4g %.4g %.4g, alpha %.3f\n", x, y, c[0], c[1],
                 c[2], c[3]);
        file << line;
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
    write_fog_record(file, data);
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

// The next capture's number (captures and GPU times count together).
uint32_t next_capture_id()
{
    if (g_capture_id == 0)
    {
        // continue after the captures already on disk, so a new session doesn't overwrite them
        std::error_code ec;
        for (const auto &f : fs::directory_iterator(g_data_dir / "logs" / "captures", ec))
        {
            const std::string name = f.path().filename().string();
            if (name.ends_with("_sky_atmosphere.txt") || name.ends_with("_draws.tsv"))
                g_capture_id = std::max(g_capture_id, uint32_t(std::strtoul(name.c_str(), nullptr, 10)));
        }
    }
    return ++g_capture_id;
}

// Ctrl+Shift+F12: the draw log alone, for its GPU times. No capture is armed, so no
// replacement writes its capture data in the frames timed; with F9 off they're stock.
void request_gpu_times(uint64_t frame)
{
    if (g_timer_heap == nullptr)
    {
        event(reshade::log::level::warning, "GPU times unavailable: no timestamps (see the start of this log)");
        return;
    }
    if (const uint64_t first = g_draw_log_frame.load();
        first != UINT64_MAX && frame < first + kDrawLogFrames + kGpuTimerDelayFrames)
        return;  // (one draw log at a time)
    {
        const std::lock_guard lock(g_draw_log_mutex);
        g_draw_log.clear();
    }
    g_draw_log_id = next_capture_id();
    g_timer_next = 0;
    g_draw_log_frame = frame + 1;
    event(reshade::log::level::info, "GPU times " + std::to_string(g_draw_log_id) + ": the next frames, " +
          (g_enabled ? "the mod's" : "stock (replacements are OFF)"));
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
    next_capture_id();
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
    {
        const std::lock_guard lock(g_draw_log_mutex);
        g_draw_log.clear();
    }
    g_draw_log_id = g_capture_id;
    g_timer_next = 0;
    g_draw_log_frame = frame + 1;  // the next whole frames (kDrawLogFrames)
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

// The cloud tunables' ring, mapped for as long as it exists. Without it the
// mod's compute shaders stay stock (on_bind_pipeline).
void create_cloud_settings(device *dev)
{
    if (g_cloud_settings.handle != 0)
        return;
    if (!dev->create_resource(resource_desc(kCloudSettingsBytes, memory_heap::upload, resource_usage::constant_buffer),
                              nullptr, resource_usage::cpu_access, &g_cloud_settings))
    {
        event(reshade::log::level::error, "failed to create the cloud tunables' buffer; the mod's compute shaders stay off");
        return;
    }
    void *mapped = nullptr;
    if (!dev->map_buffer_region(g_cloud_settings, 0, kCloudSettingsBytes, map_access::write_only, &mapped) ||
        mapped == nullptr)
    {
        dev->destroy_resource(std::exchange(g_cloud_settings, resource{}));
        event(reshade::log::level::error, "failed to map the cloud tunables' buffer; the mod's compute shaders stay off");
        return;
    }
    std::memset(mapped, 0, kCloudSettingsBytes);
    g_cloud_settings_device = dev;
    g_cloud_settings_mapped = static_cast<uint8_t *>(mapped);
    g_cloud_settings_base = reinterpret_cast<ID3D12Resource *>(g_cloud_settings.handle)->GetGPUVirtualAddress();
    g_cloud_settings_writes = 0;
    check_device(dev, "creating the cloud tunables' buffer");
    event(reshade::log::level::info, "cloud tunables' buffer created: " + std::to_string(kCloudSettingsSlots) +
          " slots of " + std::to_string(kCloudSettingsSlotBytes) + " bytes");
}

void destroy_cloud_settings(device *dev)
{
    g_cloud_settings_va.store(0, std::memory_order_release);
    g_cloud_settings_mapped = nullptr;
    g_cloud_settings_device = nullptr;
    if (g_cloud_settings.handle != 0)
    {
        dev->unmap_buffer_region(g_cloud_settings);
        dev->destroy_resource(std::exchange(g_cloud_settings, resource{}));
    }
}

// Once per frame, after update_effective_tunables: the cloud tunables into the
// ring's next slot if they changed. Command lists recorded from then on get
// that slot (before_dispatch); earlier ones keep theirs.
void write_cloud_settings()
{
    if (g_cloud_settings_mapped == nullptr)
        return;
    std::array<float, kCloudTunableCount> cloud;
    {
        const std::shared_lock lock(g_mutex);
        cloud = g_effective_cloud_tunables;
    }
    if (g_cloud_settings_writes != 0 && std::memcmp(cloud.data(), g_cloud_settings_written.data(), sizeof(cloud)) == 0)
        return;
    const uint64_t offset = (g_cloud_settings_writes % kCloudSettingsSlots) * kCloudSettingsSlotBytes;
    std::array<uint8_t, kCloudSettingsSlotBytes> slot = {};  // (the spare values 0)
    std::memcpy(slot.data(), cloud.data(), sizeof(cloud));
    std::memcpy(g_cloud_settings_mapped + offset, slot.data(), slot.size());  // (write-combined memory: written once)
    g_cloud_settings_written = cloud;
    ++g_cloud_settings_writes;
    g_cloud_settings_va.store(g_cloud_settings_base + offset, std::memory_order_release);
}

// Created on the first present rather than at device creation, so nothing of
// ours touches the device while the game is still initialising it. Each step
// is logged separately; a failure only disables captures.
void create_debug_resources(device *dev)
{
    if (dev->get_api() != device_api::d3d12)
        return;
    create_cloud_settings(dev);  // (on its own: a failure here turns off the compute replacements, not captures)
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
        // the draw log's GPU times: without them the log has none
        const D3D12_QUERY_HEAP_DESC timers = {D3D12_QUERY_HEAP_TYPE_TIMESTAMP, kGpuTimers, 0};
        if (!dev->create_resource(resource_desc(uint64_t(kGpuTimers) * 8, memory_heap::readback,
                                                resource_usage::copy_dest),
                                  nullptr, resource_usage::copy_dest, &g_timer_readback) ||
            FAILED(reinterpret_cast<ID3D12Device *>(dev->get_native())
                       ->CreateQueryHeap(&timers, IID_PPV_ARGS(&g_timer_heap))))
        {
            g_timer_heap = nullptr;
            event(reshade::log::level::warning, "failed to create the GPU timestamps; the draw log has no GPU times");
        }
        // the performance report's: without them it has the frame rate only
        const D3D12_QUERY_HEAP_DESC frame_timers = {D3D12_QUERY_HEAP_TYPE_TIMESTAMP, kFrameTimers, 0};
        if (!dev->create_resource(resource_desc(uint64_t(kFrameTimers) * 8, memory_heap::readback,
                                                resource_usage::copy_dest),
                                  nullptr, resource_usage::copy_dest, &g_frame_timer_readback) ||
            FAILED(reinterpret_cast<ID3D12Device *>(dev->get_native())
                       ->CreateQueryHeap(&frame_timers, IID_PPV_ARGS(&g_frame_timer_heap))))
        {
            g_frame_timer_heap = nullptr;
            event(reshade::log::level::warning,
                  "failed to create the performance report's GPU timestamps; it measures the frame rate only");
        }
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

    if (dev == g_cloud_settings_device)
        destroy_cloud_settings(dev);
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
    if (g_timer_heap != nullptr)
        std::exchange(g_timer_heap, nullptr)->Release();
    if (g_timer_readback.handle != 0)
        dev->destroy_resource(std::exchange(g_timer_readback, resource{}));
    g_frame_timing = false;
    if (g_frame_timer_heap != nullptr)
        std::exchange(g_frame_timer_heap, nullptr)->Release();
    if (g_frame_timer_readback.handle != 0)
        dev->destroy_resource(std::exchange(g_frame_timer_readback, resource{}));
}

void on_init_command_list(command_list *cmd_list)
{
    auto *state = cmd_list->create_private_data<CommandListState>();
    // (timestamps only where D3D12 allows them: not in bundles or copy lists)
    if (cmd_list->get_device()->get_api() == device_api::d3d12)
    {
        const D3D12_COMMAND_LIST_TYPE type = reinterpret_cast<ID3D12CommandList *>(cmd_list->get_native())->GetType();
        state->timeable = type == D3D12_COMMAND_LIST_TYPE_DIRECT || type == D3D12_COMMAND_LIST_TYPE_COMPUTE;
    }
}

void on_destroy_command_list(command_list *cmd_list)
{
    cmd_list->destroy_private_data<CommandListState>();
}

// The performance report's timestamp on a list now: its count (g_frame_timer_next).
uint64_t frame_timestamp(command_list *cmd_list)
{
    const uint64_t n = g_frame_timer_next.fetch_add(1, std::memory_order_relaxed);
    reinterpret_cast<ID3D12GraphicsCommandList *>(cmd_list->get_native())
        ->EndQuery(g_frame_timer_heap, D3D12_QUERY_TYPE_TIMESTAMP, UINT(n % kFrameTimers));
    return n;
}

// ReShade calls reset_command_list before ID3D12GraphicsCommandList::Reset, while
// the list is still closed: a timestamp there would be lost. So the list's
// start is stamped at its first command (barrier, bind, clear, copy, draw,
// dispatch, ...), each of which calls this first.
void on_reset_command_list(command_list *cmd_list)
{
    if (auto *state = cmd_list->get_private_data<CommandListState>())
    {
        const bool timeable = state->timeable;
        *state = {};
        state->timeable = timeable;
        state->frame_pending = timeable && g_frame_timing.load(std::memory_order_relaxed) &&
                               g_frame_timer_heap != nullptr && cmd_list->get_device() == g_debug_device;
    }
}

void start_frame_timing(command_list *cmd_list)
{
    if (!g_frame_timing.load(std::memory_order_relaxed))
        return;
    auto *state = cmd_list->get_private_data<CommandListState>();
    if (state == nullptr || !state->frame_pending)
        return;
    state->frame_pending = false;
    if (g_frame_timer_heap != nullptr)
        state->frame_begin = frame_timestamp(cmd_list);
}

// The commands a list may start with that the add-on doesn't handle otherwise.
template <typename... Args>
void start_frame_timing_void(command_list *cmd_list, Args...)
{
    start_frame_timing(cmd_list);
}
template <typename... Args>
bool start_frame_timing_bool(command_list *cmd_list, Args...)
{
    start_frame_timing(cmd_list);
    return false;
}

// A list with draw log timestamps: one more at its end, then all of them resolved into
// the readback, contiguous runs at a time.
void on_close_command_list(command_list *cmd_list)
{
    auto *state = cmd_list->get_private_data<CommandListState>();
    if (state != nullptr && state->frame_begin != UINT64_MAX && g_frame_timer_heap != nullptr)
    {
        state->frame_end = frame_timestamp(cmd_list);
        auto *native = reinterpret_cast<ID3D12GraphicsCommandList *>(cmd_list->get_native());
        for (const uint64_t n : {state->frame_begin, state->frame_end})
            native->ResolveQueryData(g_frame_timer_heap, D3D12_QUERY_TYPE_TIMESTAMP, UINT(n % kFrameTimers), 1,
                                     reinterpret_cast<ID3D12Resource *>(g_frame_timer_readback.handle),
                                     (n % kFrameTimers) * 8);
    }
    if (state == nullptr || state->timers.empty() || g_timer_heap == nullptr)
        return;
    const std::string stamp = gpu_timestamp(cmd_list, *state);
    const uint64_t first = g_draw_log_frame.load(std::memory_order_relaxed);
    const uint64_t frame = g_frame.load(std::memory_order_relaxed);
    if (stamp != "-" && first != UINT64_MAX && frame < first + kDrawLogFrames + kGpuTimerDelayFrames)
    {
        const std::lock_guard lock(g_draw_log_mutex);
        if (g_draw_log.size() < kDrawLogMax)
            g_draw_log.push_back("close\t" + hex64(reinterpret_cast<uintptr_t>(cmd_list)) + "\t-\t-\t-\t-\t-\t" +
                                 stamp);
    }
    auto *native = reinterpret_cast<ID3D12GraphicsCommandList *>(cmd_list->get_native());
    auto *readback = reinterpret_cast<ID3D12Resource *>(g_timer_readback.handle);
    std::vector<uint32_t> &timers = state->timers;
    std::sort(timers.begin(), timers.end());
    for (size_t i = 0; i < timers.size();)
    {
        size_t j = i + 1;
        while (j < timers.size() && timers[j] == timers[j - 1] + 1)
            ++j;
        native->ResolveQueryData(g_timer_heap, D3D12_QUERY_TYPE_TIMESTAMP, timers[i], UINT(j - i), readback,
                                 uint64_t(timers[i]) * 8);
        i = j;
    }
    timers.clear();
}

void on_init_pipeline_layout(device *dev, uint32_t count, const pipeline_layout_param *params, pipeline_layout layout)
{
    uint32_t mod_param = UINT32_MAX, cloud_param = UINT32_MAX, tlas_param = UINT32_MAX;
    for (uint32_t i = 0; i < count; ++i)
    {
        if (mod_param == UINT32_MAX && is_mod_param(params[i]))
            mod_param = i;
        if (cloud_param == UINT32_MAX && is_cloud_param(params[i]))
            cloud_param = i;
        if (tlas_param == UINT32_MAX && params[i].type == pipeline_layout_param_type::push_descriptors &&
            params[i].push_descriptors.type == descriptor_type::acceleration_structure &&
            params[i].push_descriptors.dx_register_space == g_mod_space)
            tlas_param = i;
    }
    auto copy = to_layout_params(count, params);
    // (injected: what ReShade made of our parameters, as the root signature has them)
    write_line(g_rootsig_log, "created " + hex64(layout.handle) + ", " + std::to_string(count) + " params" +
               (mod_param != UINT32_MAX ? " [injected, mod params at " + std::to_string(mod_param) +
                (cloud_param != UINT32_MAX ? ", cloud params at " + std::to_string(cloud_param) : "") + "]" +
                describe_params(copy, mod_param) : ""));
    if (mod_param != UINT32_MAX)
        check_device(dev, "creating the injected root signature");
    // The compute signature (the one with the TLAS) always gets the cloud tunables; without them as a root CBV,
    // compute replacements can't be given them, and stay stock (on_bind_pipeline).
    if (tlas_param != UINT32_MAX && cloud_param == UINT32_MAX)
        event(reshade::log::level::error, "the injected compute root signature " + hex64(layout.handle) +
              " has no root CBV for the cloud tunables; its replacements stay stock");

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
    if (!is_compute && pixel_index != UINT32_MAX)
        record_graphics(pipe, pixel_crc, count, subobjects);
    if (is_compute)
    {
        const std::unique_lock lock(g_mutex);
        g_compute_shaders[pipe.handle] = compute_crc;
    }

    TrackedPipeline info;
    info.crc = is_compute ? compute_crc : pixel_crc;
    info.layout = layout;
    info.graphics = !is_compute;
    info.dev = dev;
    std::vector<uint8_t> code;
    fs::path lazy_file;
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
        else if (const auto lazy = g_lazy_replacements.find(info.crc); lazy != g_lazy_replacements.end())
            lazy_file = lazy->second;
    }
    info.watched = kWatchedEntries.contains(info.entry);
    info.radar = info.entry == "ph_rain_radar_horz";
    // The forward pass, particles and light points: replaced only where they
    // blend into the scene colour or the refraction buffer (their fog depends
    // on how), ph_base's replacement read from disk the first time a pipeline
    // needs it
    if (info.graphics && kForwardFogEntries.contains(info.entry))
    {
        info.fog_mode = info.mod_param != UINT32_MAX ? forward_fog_mode(count, subobjects) : 0;
        if (info.fog_mode == 0)
        {
            code.clear();
        }
        else if (code.empty() && !lazy_file.empty())
        {
            code = read_file(lazy_file);
            const std::unique_lock lock(g_mutex);
            g_replacements.try_emplace(info.crc, code);
        }
    }
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
        if (replacement.handle != 0)
            info.shader_index = shader_index_locked(info.crc);
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
        g_graphics.erase(pipe.handle);
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
    start_frame_timing(cmd_list);
    pipeline replacement = {};
    uint32_t shader_index = UINT32_MAX;
    bool graphics = true;  // every compute pipeline is tracked, so an unknown one is graphics
    bool tracked = false, shaded = false, cloud = false;
    {
        const std::shared_lock lock(g_mutex);
        if (const auto it = g_pipelines.find(pipe.handle); it != g_pipelines.end())
        {
            tracked = true;
            graphics = it->second.graphics;
            replacement = it->second.replacement;
            shader_index = it->second.shader_index;
            cloud = it->second.cloud_param != UINT32_MAX;
        }
        shaded = g_graphics.contains(pipe.handle);
    }
    if (auto *state = cmd_list->get_private_data<CommandListState>())
    {
        if (graphics)
        {
            state->graphics = tracked ? pipe : pipeline{};
            state->graphics_any = shaded ? pipe : pipeline{};
        }
        else
        {
            state->compute = pipe;
        }
    }
    // A compute replacement only with the cloud tunables' slot to give it (before_dispatch): until the first present
    // has written one, or if its root signature has no root CBV for it, the game's own pipeline runs.
    if (replacement.handle != 0 && g_enabled.load(std::memory_order_relaxed) &&
        (graphics || (cloud && g_cloud_settings_va.load(std::memory_order_acquire) != 0)))
    {
        cmd_list->bind_pipeline(stages, replacement);
        if (g_counting_mod_shaders.load(std::memory_order_relaxed))
        {
            g_mod_shader_runs.fetch_add(1, std::memory_order_relaxed);
            if (shader_index < kModShaderBits)
                g_mod_shaders_ran[shader_index / 64].fetch_or(uint64_t(1) << (shader_index % 64),
                                                              std::memory_order_relaxed);
        }
    }
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
    {
        const std::shared_lock lock(g_mutex);
        const auto it = g_pipelines.find(state->compute.handle);
        if (it == g_pipelines.end() || (!it->second.watched && it->second.replacement.handle == 0))
            return;
        info = it->second;
        tunables = g_effective_tunables;
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
        // The cloud tunables' slot, on the native command list: push_mod_args has made info.layout the bound
        // compute signature. (Without the slot, on_bind_pipeline left the game's pipeline bound.)
        const uint64_t cloud = g_cloud_settings_va.load(std::memory_order_acquire);
        if (info.cloud_param != UINT32_MAX && cloud != 0 && cmd_list->get_device()->get_api() == device_api::d3d12)
            reinterpret_cast<ID3D12GraphicsCommandList *>(cmd_list->get_native())
                ->SetComputeRootConstantBufferView(info.cloud_param, cloud);
        if (g_auto_capture.exchange(false))
            g_auto_capture_due = true;  // first replaced dispatch: capture once automatically
    }
}

void on_bind_viewports(command_list *cmd_list, uint32_t first, uint32_t count, const viewport *viewports)
{
    start_frame_timing(cmd_list);
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
    start_frame_timing(cmd_list);
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
    start_frame_timing(cmd_list);
    const std::lock_guard lock(g_seen_mutex);
    ++g_dispatch_counts["dispatch_rays"];
    if (g_capture.load(std::memory_order_relaxed) == Capture::armed && g_capture_order.size() < 400)
        g_capture_order.push_back("dispatch_rays " + std::to_string(width) + "x" + std::to_string(height) + "x" +
                                  std::to_string(depth) + ", cmd " +
                                  std::to_string(reinterpret_cast<uintptr_t>(cmd_list) & 0xFFFFFF));
    return false;
}

// Every dispatch in the capture's draw log (the frame's order, compute passes included).
void log_dispatch_draw_log(command_list *cmd_list, const std::string &what)
{
    if (!draw_log_on())
        return;
    auto *state = cmd_list->get_private_data<CommandListState>();
    if (state == nullptr)
        return;
    uint32_t crc = 0;
    std::string entry = "?";
    {
        const std::shared_lock lock(g_mutex);
        if (const auto it = g_compute_shaders.find(state->compute.handle); it != g_compute_shaders.end())
            crc = it->second;
        if (const auto it = g_names.find(crc); it != g_names.end())
            entry = it->second;
    }
    std::string line = "dispatch\t" + hex64(reinterpret_cast<uintptr_t>(cmd_list)) + '\t' +
                       std::to_string(state->draw_index++) + '\t' + what + '\t' + hex32(crc) + '\t' + entry + "\t-\t" +
                       gpu_timestamp(cmd_list, *state);
    const std::lock_guard lock(g_draw_log_mutex);
    if (g_draw_log.size() < kDrawLogMax)
        g_draw_log.push_back(std::move(line));
}

bool on_dispatch(command_list *cmd_list, uint32_t x, uint32_t y, uint32_t z)
{
    start_frame_timing(cmd_list);
    log_dispatch_draw_log(cmd_list, std::to_string(x) + " x " + std::to_string(y) + " x " + std::to_string(z) +
                                        " groups");
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
    // (Not the forward pass's draws: every mesh is a new size.)
    if (info.fog_mode == 0)
    {
        char viewport[48];
        snprintf(viewport, sizeof(viewport), ", viewport %.0fx%.0f", state->viewport_width, state->viewport_height);
        log_dispatch(info, size + viewport);
    }
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
    start_frame_timing(cmd_list);
    if (draw_log_on())
        log_draw(cmd_list, std::to_string(vertices) + " vertices x" + std::to_string(instances));
    if (tracked_graphics_bound(cmd_list))
        before_draw(cmd_list, "draw\t" + std::to_string(vertices) + " vertices\t" + std::to_string(instances) +
                    " instances");
    return false;
}

bool on_draw_indexed(command_list *cmd_list, uint32_t indices, uint32_t instances, uint32_t, int32_t, uint32_t)
{
    start_frame_timing(cmd_list);
    if (draw_log_on())
        log_draw(cmd_list, std::to_string(indices) + " indices x" + std::to_string(instances));
    if (tracked_graphics_bound(cmd_list))
        before_draw(cmd_list, "draw_indexed\t" + std::to_string(indices) + " indices\t" +
                    std::to_string(instances) + " instances");
    return false;
}

bool on_draw_or_dispatch_indirect(command_list *cmd_list, indirect_command type, resource, uint64_t, uint32_t count,
                                  uint32_t)
{
    start_frame_timing(cmd_list);
    if (type == indirect_command::dispatch)
    {
        log_dispatch_draw_log(cmd_list, "indirect x" + std::to_string(count));
        before_dispatch(cmd_list, "indirect\tx" + std::to_string(count) + '\t');
    }
    else if (type == indirect_command::draw || type == indirect_command::draw_indexed)
    {
        if (draw_log_on())
            log_draw(cmd_list, "indirect x" + std::to_string(count));
        if (tracked_graphics_bound(cmd_list))
            before_draw(cmd_list, "draw_indirect\tx" + std::to_string(count) + '\t');
    }
    return false;
}

void on_execute_command_list(command_queue *queue, command_list *cmd_list)
{
    if (g_timer_frequency.load(std::memory_order_relaxed) == 0 &&
        (queue->get_type() & command_queue_type::graphics) == command_queue_type::graphics)
        g_timer_frequency = queue->get_timestamp_frequency();
    if (g_frame_timing.load(std::memory_order_relaxed))
        if (const auto *state = cmd_list->get_private_data<CommandListState>();
            state != nullptr && state->frame_end != UINT64_MAX)
        {
            const std::lock_guard lock(g_frame_timer_mutex);
            g_frame_lists[g_frame.load(std::memory_order_relaxed)].push_back(
                {state->frame_begin, state->frame_end, reinterpret_cast<ID3D12CommandQueue *>(queue->get_native())});
            g_gpu_counts.lists += 1;
        }
    if (!draw_log_on())
        return;
    const std::lock_guard lock(g_draw_log_mutex);
    if (g_draw_log.size() < kDrawLogMax)
        g_draw_log.push_back("execute\t" + hex64(reinterpret_cast<uintptr_t>(cmd_list)) + "\t-\tqueue " +
                             hex64(reinterpret_cast<uintptr_t>(queue)));
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
    g_fog_tint.store(cloud[35] > 0.0f, std::memory_order_relaxed);
    g_forward_fog_off.store(cloud[35] < 0.0f, std::memory_order_relaxed);
    cloud[35] = g_cloud_plane_light.load(std::memory_order_relaxed);  // (C35 itself goes no further; no cloud shader read its slot)
    // Rising air goes to the generator as C30's slot < 0: the render shaders read max(C30, 0). C30 > 0 (finer
    // billows, tunables.ini only) wins.
    if (!(cloud[30] > 0.0f) && g_cloud_tops.load(std::memory_order_relaxed) == 2)
        cloud[30] = -1.0f;

    const std::unique_lock lock(g_mutex);
    g_effective_tunables = effective;
    g_effective_radar_tunables = radar;
    g_effective_cloud_tunables = cloud;
}

// The presets the app lists, the one the settings are, as of the files' stamp, and why its last preset
// request failed (present thread only).
std::vector<std::string> g_preset_names;
std::map<std::string, std::vector<std::string>> g_presets_missing;
settings::presets::Current g_preset_current;
std::string g_presets_stamp;
std::string g_preset_note;

// The EFB app's performance report (perf_report.h). Present thread.
perf::Measurement g_measurement;
std::string g_report_system;            // perf::system_json, taken when the last measurement started
bool g_report_asked = false;            // the app asked for one: the state carries the report
std::optional<bool> g_measure_request;  // the app's last start (true) or stop (false), for on_present
HMODULE g_reshade_module = nullptr;

// A JSON string's content (quotes and backslashes escaped).
std::string json_text(const std::string &text)
{
    std::string out;
    for (const char c : text)
        out += c == '"' || c == '\\' ? std::string("\\") + c : std::string(1, c);
    return out;
}

// The state the EFB app's settings show (settings_bridge.h): tunables.ini's T
// and C values, the atmosphere tunables the shaders get (TE, rounded: they
// drift while the METAR's haze is smoothed in), the Ctrl+Shift+F9 switch, the
// METAR's haze in a line, the add-on's version, the presets and the one the
// settings are (listed and matched again when a file changed); not saved while
// changes wait to be saved into tunables.ini (`pending`).
std::string settings_state(bool pending)
{
    const fs::path dir = presets_dir(), tunables = tunables_path();
    if (std::string stamp = settings::presets::stamp(dir, tunables); stamp != g_presets_stamp)
    {
        g_presets_stamp = std::move(stamp);
        g_preset_names = settings::presets::list(dir);
        g_presets_missing = settings::presets::missing(dir, known_tunable);
        g_preset_current = settings::presets::current(dir, tunables, known_tunable);
    }
    std::string presets = "[";
    for (const std::string &name : g_preset_names)
        presets += (presets.size() > 1 ? ",\"" : "\"") + json_text(name) + "\"";
    presets += "]";
    std::string missing = "{";
    for (const auto &[name, keys] : g_presets_missing)
    {
        missing += (missing.size() > 1 ? ",\"" : "\"") + json_text(name) + "\":[";
        for (size_t i = 0; i < keys.size(); ++i)
            missing += (i > 0 ? ",\"" : "\"") + keys[i] + "\"";
        missing += "]";
    }
    missing += "}";
    std::array<float, kTunableCount> ini, effective;
    std::array<float, kCloudTunableCount + 1> cloud;  // (+ C36, the plane-cloud light, for its slider)
    float ozone_setting;
    {
        const std::shared_lock lock(g_mutex);
        ini = g_tunables;
        effective = g_effective_tunables;
        std::copy(g_cloud_tunables.begin(), g_cloud_tunables.end(), cloud.begin());
        ozone_setting = g_ozone_setting;
    }
    cloud[kCloudTunableCount] = g_cloud_plane_light.load(std::memory_order_relaxed);
    // (by index: T14, the ozone column, and T15, the haze field, which the app doesn't set but its performance
    // report lists when they differ from the defaults; T16, the haze strength)
    std::array<float, kTunableCount + 3> shown = {};
    std::copy(ini.begin(), ini.end(), shown.begin());
    shown[14] = ozone_setting;
    shown[15] = weather::haze_field() ? 1.0f : 0.0f;
    shown[16] = weather::haze_strength();
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
    return std::string("{\"enabled\":") + (g_enabled ? "true" : "false") + ",\"T\":" + list(shown, false) +
           ",\"C\":" + list(cloud, false) + ",\"TE\":" + list(effective, true) + ",\"weather\":\"" +
           json_text(weather::summary()) + "\",\"version\":\"" ATMOSPHEREMOD_VERSION "\",\"presets\":" + presets +
           ",\"preset\":\"" + json_text(g_preset_current.name) + "\",\"presetSaved\":" +
           (g_preset_current.saved && !pending ? "true" : "false") + ",\"presetsMissing\":" + missing +
           ",\"presetNote\":\"" + json_text(g_preset_note) + "\"" +
           (g_report_asked ? ",\"report\":" + perf::state_json(g_measurement, perf::Clock::now(), g_report_system)
                           : std::string()) + "}";
}

// The app's preset requests (settings_bridge.h presets): saved, loaded or deleted, in the event log;
// why one failed in g_preset_note. Returns whether a preset was loaded (tunables.ini changed: reload).
bool apply_preset_requests(const settings::Requests &requests)
{
    namespace presets = settings::presets;
    const fs::path dir = presets_dir(), tunables = tunables_path();
    bool loaded = false;
    std::string error, done;
    g_preset_note.clear();
    if (requests.save_preset)
    {
        if (presets::save(dir, *requests.save_preset, tunables, error))
            done += " saved \"" + presets::valid_name(*requests.save_preset) + "\"";
        else
            g_preset_note = error;
    }
    if (requests.load_preset)
    {
        if (const size_t n = presets::load(dir, *requests.load_preset, tunables, known_tunable, requests.discard,
                                           requests.fill, error))
        {
            loaded = true;
            done += " loaded \"" + presets::valid_name(*requests.load_preset) + "\" (" + std::to_string(n) +
                    " settings" +
                    (requests.fill == settings::Fill::defaults ? ", the ones it lacked added from Default"
                     : requests.fill == settings::Fill::current ? ", the ones it lacked added from the settings before"
                                                                : "") +
                    (requests.discard ? ", the settings before not saved" : "") + ")";
        }
        else
            g_preset_note = error;
    }
    if (requests.delete_preset)
    {
        if (presets::remove(dir, *requests.delete_preset, error))
            done += " deleted \"" + presets::valid_name(*requests.delete_preset) + "\"";
        else
            g_preset_note = error;
    }
    event(reshade::log::level::info, "presets (" + dir.string() + "):" + done +
          (g_preset_note.empty() ? "" : " " + g_preset_note));
    return loaded;
}

// The app's preset import (settings_bridge.h presets::import): a file dialog over the sim's window
// (file_dialog.h), then the file chosen added as a preset; what happened in g_preset_note and the event
// log. Returns whether the note changed (publish).
bool sync_import(bool asked, void *window)
{
    if (asked)
    {
        if (!file_dialog::open(window))
            return false;  // (it's open: its note stands)
        g_preset_note = "Choose the file in the window that opened";
        return true;
    }
    const std::optional<fs::path> chosen = file_dialog::take();
    if (!chosen)
        return false;
    const std::u8string from = chosen->u8string();
    std::string error, name;
    if (!chosen->empty())
        name = settings::presets::import(presets_dir(), *chosen, known_tunable, error);
    g_preset_note = chosen->empty() ? "" : !name.empty() ? "Imported \"" + name + "\"" : error;
    event(reshade::log::level::info, "presets: import " +
          (chosen->empty() ? std::string("cancelled") : std::string(from.begin(), from.end()) + ": " + g_preset_note));
    return true;
}

// The EFB app's settings: applies what it asked for, saves the changes into
// tunables.ini once they have rested for a second (at once before a reload or
// a preset request: a preset saves what the app shows, a load starts from it),
// handles its presets (an import's dialog owned by `window`, the game's), and
// publishes the state it shows, twice a second. Returns whether to reload
// (Ctrl+Shift+F10 pressed, the app's Reload button, or a preset loaded).
bool sync_settings(bool reload_key, void *window)
{
    using namespace std::chrono_literals;
    static std::vector<settings::Change> unsaved;
    static auto last_change = std::chrono::steady_clock::now(), last_publish = last_change;
    const auto now = std::chrono::steady_clock::now();
    const settings::Requests requests = settings::take();
    if (requests.reload)
        event(reshade::log::level::info, "reload asked for by the EFB app");
    const bool flush = reload_key || requests.reload;
    if (requests.measure)
        g_measure_request = requests.measure;
    // (the user's own switching ends a measurement, and the switch stays as they set it)
    if (requests.enabled && g_measurement.running())
    {
        g_measurement.stop();
        event(reshade::log::level::info, "performance report: stopped (the mod switched in the EFB app)");
    }
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
            else if (c.prefix == 'T' && c.index == 16)
                weather::set_haze_strength(c.value);
            else if (c.prefix == 'C' && c.index < kCloudTunableCount && (c.index < 13 || c.index > 15))  // (C13-C15: ours)
                g_cloud_tunables[c.index] = c.value;
            else if (c.prefix == 'C' && c.index == 36)  // (the plane-cloud light, Sun-facing cloud sides)
                g_cloud_plane_light.store(std::clamp(c.value, 0.0f, 1.0f), std::memory_order_relaxed);
            else
                continue;
            unsaved.push_back(c);
        }
        last_change = now;
    }
    const bool preset = requests.save_preset || requests.load_preset || requests.delete_preset;
    if (!unsaved.empty() && (flush || preset || now - last_change >= 1s))
    {
        const fs::path path = tunables_path();
        if (settings::save(path, unsaved))
            event(reshade::log::level::info, "saved " + std::to_string(unsaved.size()) +
                  " setting change(s) from the EFB app to " + path.string());
        else
            event(reshade::log::level::warning, "couldn't save the EFB app's settings to " + path.string());
        unsaved.clear();
    }
    const bool loaded = preset && apply_preset_requests(requests);
    const bool imported = sync_import(requests.import_preset, window);
    if (!requests.set.empty() || requests.enabled || requests.measure || preset || imported ||
        now - last_publish >= 500ms)
    {
        last_publish = now;
        settings::publish(settings_state(!unsaved.empty()));
    }
    return flush || loaded;
}

// The EFB app's performance report (perf_report.h): starts or stops a
// measurement when the app asks, and switches the mod as it goes; afterwards
// the switch is as it was, and the result is in the event log.
// The performance report's GPU times: the frame now presented is filed under the
// part it's measured in (the lists executed from now on are rendered with the
// switch frame() just set), and the frames kFrameTimerDelay ago, whose lists the
// GPU has run, go to the measurement with their GPU time.
void time_gpu_frames(uint64_t frame)
{
    const bool on = g_measurement.running() && g_frame_timer_heap != nullptr && g_debug_device != nullptr;
    if (on != g_frame_timing.exchange(on))
    {
        const std::lock_guard lock(g_frame_timer_mutex);
        g_frame_lists.clear();
        g_frame_phase.clear();
        if (on)
        {
            g_gpu_counts = {};
            g_gpu_counts.stamps_at_start = g_frame_timer_next.load();
        }
    }
    if (!on)
        return;
    const perf::Phase phase = g_measurement.phase();
    std::vector<std::pair<perf::Phase, std::vector<TimedList>>> ready;
    {
        const std::lock_guard lock(g_frame_timer_mutex);
        if (phase == perf::Phase::on || phase == perf::Phase::off || phase == perf::Phase::on2)
            g_frame_phase[frame] = phase;
        while (!g_frame_lists.empty() && g_frame_lists.begin()->first + kFrameTimerDelay <= frame)
        {
            auto node = g_frame_lists.extract(g_frame_lists.begin());
            g_gpu_counts.frames_ready += 1;
            if (const auto it = g_frame_phase.find(node.key()); it != g_frame_phase.end())
                ready.emplace_back(it->second, std::move(node.mapped()));
            else
                g_gpu_counts.frames_outside += 1;
        }
        g_frame_phase.erase(g_frame_phase.begin(),
                            g_frame_phase.lower_bound(frame > kFrameTimerDelay ? frame - kFrameTimerDelay : 0));
    }
    if (ready.empty())
        return;
    void *mapped = nullptr;
    if (!g_debug_device->map_buffer_region(g_frame_timer_readback, 0, uint64_t(kFrameTimers) * 8,
                                           map_access::read_only, &mapped) ||
        mapped == nullptr)
        return;
    const auto *ticks = static_cast<const uint64_t *>(mapped);
    const uint64_t taken = g_frame_timer_next.load();
    // Each queue's clock against the CPU's, now (the frames are ~8 frames old: the
    // clocks don't drift apart in that time): a timestamp t is at
    // cpu + (t - gpu) / frequency on the CPU's clock.
    struct QueueClock
    {
        uint64_t frequency = 0, gpu = 0, cpu = 0;
    };
    std::unordered_map<ID3D12CommandQueue *, QueueClock> clocks;
    LARGE_INTEGER qpc_frequency = {};
    QueryPerformanceFrequency(&qpc_frequency);
    const auto clock_of = [&](ID3D12CommandQueue *queue) -> const QueueClock * {
        auto [it, added] = clocks.try_emplace(queue);
        if (added && queue != nullptr &&
            (FAILED(queue->GetTimestampFrequency(&it->second.frequency)) ||
             FAILED(queue->GetClockCalibration(&it->second.gpu, &it->second.cpu))))
            it->second.frequency = 0;
        return it->second.frequency > 0 ? &it->second : nullptr;
    };
    const double qpc_ms = 1000.0 / double(qpc_frequency.QuadPart);
    std::vector<std::pair<perf::Phase, double>> timed;
    {
        const std::lock_guard lock(g_frame_timer_mutex);
        for (auto &[measured_in, lists] : ready)
        {
            std::vector<std::pair<double, double>> spans;
            bool fresh = true, run = true, valid = true, clocked = true;
            for (const TimedList &l : lists)
            {
                fresh = fresh && taken - l.begin < kFrameTimers - 1024;  // (its slots not taken again since)
                const uint64_t b = ticks[l.begin % kFrameTimers], e = ticks[l.end % kFrameTimers];
                run = run && b != 0 && e != 0;
                valid = valid && e >= b;
                const QueueClock *c = clock_of(l.queue);
                clocked = clocked && c != nullptr;
                if (c == nullptr)
                    continue;
                g_gpu_counts.queues[l.queue] = c->frequency;
                const double at = double(c->cpu) * qpc_ms;
                const double per_tick = 1000.0 / double(c->frequency);
                spans.emplace_back(at + double(int64_t(b - c->gpu)) * per_tick,
                                   at + double(int64_t(e - c->gpu)) * per_tick);
            }
            if (g_gpu_counts.first.empty() && !lists.empty())
            {
                double lo = 0.0, hi = 0.0;
                for (size_t i = 0; i < spans.size(); ++i)
                    lo = i == 0 ? spans[i].first : std::min(lo, spans[i].first),
                    hi = i == 0 ? spans[i].second : std::max(hi, spans[i].second);
                const uint64_t b0 = ticks[lists.front().begin % kFrameTimers];
                const uint64_t e0 = ticks[lists.front().end % kFrameTimers];
                char line[320];
                snprintf(line, sizeof(line), "first frame read back: %zu lists, slots %llu..%llu of %llu taken, the first "
                         "list's ticks %llu..%llu; %s%s%s%sfrom first start to last end %.3f ms, busy %.3f ms",
                         lists.size(), (unsigned long long)lists.front().begin, (unsigned long long)lists.back().end,
                         (unsigned long long)taken, (unsigned long long)b0, (unsigned long long)e0,
                         fresh ? "" : "overwritten, ", run ? "" : "not run yet, ", valid ? "" : "an end before its start, ",
                         clocked ? "" : "no clock, ", hi - lo, perf::busy_ms(spans));
                g_gpu_counts.first = line;
            }
            const double ms = fresh && run && valid && clocked ? perf::busy_ms(std::move(spans)) : -1.0;
            uint64_t &count = !fresh ? g_gpu_counts.overwritten : !run ? g_gpu_counts.unrun
                              : !clocked ? g_gpu_counts.no_clock : ms < 0.0 || !valid ? g_gpu_counts.invalid
                              : g_gpu_counts.timed;
            count += 1;
            if (ms > 0.0)
                timed.emplace_back(measured_in, ms);
        }
    }
    g_debug_device->unmap_buffer_region(g_frame_timer_readback);
    for (const auto &[measured_in, ms] : timed)
        g_measurement.gpu_frame(measured_in, ms);
}

// The GPU timing's counts, for the log line at the end of a measurement.
std::string gpu_timing_counts()
{
    const std::lock_guard lock(g_frame_timer_mutex);
    const GpuTimingCounts &c = g_gpu_counts;
    std::string queues;
    for (const auto &[queue, frequency] : c.queues)
        queues += (queues.empty() ? "" : ", ") + std::to_string(frequency) + " Hz";
    return "GPU timing: " + std::to_string(g_frame_timer_next.load() - c.stamps_at_start) + " timestamps, " +
           std::to_string(c.lists) + " lists executed, " + std::to_string(c.frames_ready) + " frames read back (" +
           std::to_string(c.frames_outside) + " outside the measured parts), " + std::to_string(c.timed) + " timed, " +
           std::to_string(c.unrun) + " not run yet, " + std::to_string(c.invalid) + " invalid, " +
           std::to_string(c.overwritten) + " overwritten, " + std::to_string(c.no_clock) + " without a clock; " +
           std::to_string(c.queues.size()) + " queue(s)" + (queues.empty() ? "" : " (" + queues + ")") +
           (c.first.empty() ? "; no frame read back" : "; " + c.first);
}

void measure_performance(effect_runtime *runtime)
{
    const auto now = perf::Clock::now();
    if (const std::optional<bool> want = std::exchange(g_measure_request, std::nullopt))
    {
        g_report_asked = true;
        if (*want)
        {
            uint32_t width = 0, height = 0;
            runtime->get_screenshot_width_and_height(&width, &height);
            int effects = 0;
            if (runtime->get_effects_state())
                runtime->enumerate_techniques(nullptr, [&effects](effect_runtime *rt, effect_technique technique) {
                    effects += rt->get_technique_state(technique) ? 1 : 0;
                });
            device *const dev = runtime->get_device();
            g_report_system = perf::system_json(dev->get_api() == device_api::d3d12 ? dev->get_native() : 0,
                                                g_reshade_module, width, height, effects, g_mod_space);
            g_counting_mod_shaders = false;
            for (auto &word : g_mod_shaders_ran)
                word.store(0, std::memory_order_relaxed);
            g_mod_shader_runs = 0;
            g_measurement.start(now, g_enabled);
            event(reshade::log::level::info, "performance report: measuring (the mod on, off, on again)");
        }
        else if (const std::optional<bool> restore = g_measurement.stop())
        {
            g_enabled = *restore;
            event(reshade::log::level::info, "performance report: stopped in the EFB app");
        }
    }
    // (the mod's shaders that ran in the frame just presented go to the part it belongs to, before it ends)
    if (g_counting_mod_shaders.load(std::memory_order_relaxed))
    {
        uint32_t shaders = 0;
        for (const auto &word : g_mod_shaders_ran)
            shaders += uint32_t(std::popcount(word.load(std::memory_order_relaxed)));
        g_measurement.mod_shaders(shaders, g_mod_shader_runs.load(std::memory_order_relaxed));
    }
    if (const std::optional<bool> want = g_measurement.frame(now))
        g_enabled = *want;
    g_counting_mod_shaders = g_measurement.counts_mod_shaders();
    time_gpu_frames(g_frame.load());
    if (const std::optional<bool> restore = g_measurement.take_restore())
    {
        g_enabled = *restore;
        const perf::Result &r = g_measurement.result();
        char buf[384];
        const int n = snprintf(buf, sizeof(buf), "performance report: %.1f fps with the mod (%.1f and %.1f), %.1f "
                               "without; 99 %% of frames under %.1f ms with, %.1f ms without", r.fps_on(), r.on1.fps(),
                               r.on2.fps(), r.fps_off(), std::max(r.on1.ms_p99, r.on2.ms_p99), r.off.ms_p99);
        if (n > 0 && size_t(n) < sizeof(buf))
        {
            if (r.has_gpu())
                snprintf(buf + n, sizeof(buf) - n, "; GPU %.2f ms per frame with (%.2f and %.2f), %.2f ms without "
                         "(%u and %u frames)", r.gpu_on(), r.on1.gpu_ms(), r.on2.gpu_ms(), r.gpu_off(),
                         r.on1.gpu_frames + r.on2.gpu_frames, r.off.gpu_frames);
            else
                snprintf(buf + n, sizeof(buf) - n, "; no GPU times");
        }
        event(reshade::log::level::info, buf);
        event(reshade::log::level::info, "performance report: " + std::to_string(r.mod_shaders) +
              " of the mod's shaders ran with the mod on, " + std::to_string(r.mod_shader_runs) + " times");
        event(reshade::log::level::info, "performance report: " + gpu_timing_counts());
    }
}

void on_present(effect_runtime *runtime)
{
    const uint64_t frame = ++g_frame;
    // (once the GPU has run the logged frames: their GPU times)
    if (const uint64_t first = g_draw_log_frame.load();
        first != UINT64_MAX && frame == first + kDrawLogFrames + kGpuTimerDelayFrames)
        write_draw_log();
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
        if (g_measurement.running())
        {
            g_measurement.stop();
            event(reshade::log::level::info, "performance report: stopped (Ctrl+Shift+F9)");
        }
        g_enabled = !g_enabled;
        event(reshade::log::level::info, g_enabled ? "replacements ON" : "replacements OFF (stock shaders)");
    }
    // (the EFB app's unsaved changes go to tunables.ini before the reload reads it)
    if (sync_settings(modifiers && runtime->is_key_pressed(VK_F10), runtime->get_hwnd()))
        reload(runtime->get_device());
    measure_performance(runtime);
    if ((modifiers && runtime->is_key_pressed(VK_F11)) || g_auto_capture_due.exchange(false))
        request_capture(frame);
    if (modifiers && runtime->is_key_pressed(VK_F12))
        request_gpu_times(frame);
    advance_capture(runtime, frame);
    if (frame % kDispatchRateFrames == 0)
        log_dispatch_rates(frame);
    collect_garbage(frame);
    update_effective_tunables();
    write_cloud_settings();
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
    g_graphics_log.open(dir / "graphics.tsv", std::ios::trunc);
    g_graphics_log << "pixel shader crc32\tentry\tpipeline\trender targets (DXGI formats)\tdepth\tRT0 blending\tRT1 blending\n";
    g_rate_log.open(dir / "dispatch_rates.log", std::ios::trunc);
}
} // namespace

extern "C" __declspec(dllexport) bool AddonInit(HMODULE addon_module, HMODULE reshade_module)
{
    if (!reshade::register_addon(addon_module, reshade_module))
        return false;

    g_data_dir = resolve_data_dir(addon_module);
    g_reshade_module = reshade_module;
    reshade::get_config_value(nullptr, "ATMOSPHEREMOD", "InjectRootSignature", g_inject);
    reshade::get_config_value(nullptr, "ATMOSPHEREMOD", "InjectGraphicsRootSignature", g_inject_graphics);
    reshade::get_config_value(nullptr, "ATMOSPHEREMOD", "RegisterSpace", g_mod_space);
    open_logs();
    if (g_mod_space != 50)
        event(reshade::log::level::info, "root parameters in register space " + std::to_string(g_mod_space) + " (installed with --renodx)");
    load_names();
    g_replacements = load_replacements(g_lazy_replacements);
    g_tunables = load_tunables();
    g_ozone_setting = load_tunables<kTunableCount + 1>()[kTunableCount];  // T14
    weather::set_haze_field(load_tunable('T', 15, 1.0f) > 0.0f);
    weather::set_haze_strength(load_tunable('T', 16, 1.0f));
    g_cloud_tilt_view.store(load_tunable('T', 17, 0.0f) > 0.0f, std::memory_order_relaxed);
    g_cloud_plane_light.store(std::clamp(load_tunable('C', 36, 0.0f), 0.0f, 1.0f), std::memory_order_relaxed);
    g_cloud_tops.store(std::clamp(int(load_tunable('C', 37, 2.0f)), 0, 2), std::memory_order_relaxed);
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
    reshade::register_event<reshade::addon_event::close_command_list>(on_close_command_list);
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
    reshade::register_event<reshade::addon_event::execute_command_list>(on_execute_command_list);
    // (the performance report's GPU time: a list's start, at its first command; see on_reset_command_list)
    reshade::register_event<reshade::addon_event::barrier>(start_frame_timing_void);
    reshade::register_event<reshade::addon_event::bind_render_targets_and_depth_stencil>(start_frame_timing_void);
    reshade::register_event<reshade::addon_event::begin_render_pass>(start_frame_timing_bool);
    reshade::register_event<reshade::addon_event::clear_depth_stencil_view>(start_frame_timing_bool);
    reshade::register_event<reshade::addon_event::clear_render_target_view>(start_frame_timing_bool);
    reshade::register_event<reshade::addon_event::clear_unordered_access_view_uint>(start_frame_timing_bool);
    reshade::register_event<reshade::addon_event::clear_unordered_access_view_float>(start_frame_timing_bool);
    reshade::register_event<reshade::addon_event::copy_resource>(start_frame_timing_bool);
    reshade::register_event<reshade::addon_event::copy_buffer_region>(start_frame_timing_bool);
    reshade::register_event<reshade::addon_event::copy_buffer_to_texture>(start_frame_timing_bool);
    reshade::register_event<reshade::addon_event::copy_texture_region>(start_frame_timing_bool);
    reshade::register_event<reshade::addon_event::copy_texture_to_buffer>(start_frame_timing_bool);
    reshade::register_event<reshade::addon_event::resolve_texture_region>(start_frame_timing_bool);
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
