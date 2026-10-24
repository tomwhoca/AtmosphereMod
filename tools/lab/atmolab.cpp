// atmolab: run the sky-atmosphere LUT passes offline on the local GPU.
//
//   atmolab compare [--sun DEG] [--alt M]   stock vs replacement, per texel
//   atmolab dump DIR [--sun DEG] [--alt M]  write every LUT as .pfm (stock_*, ours_*)
//
//   --model  run ours with build/tunables.ini and chain our own LUTs (as in
//            game) instead of feeding every pass the stock inputs
//   also: --pitch DEG (camera), --apdist M (aerial-perspective range)
//
// ATMOLAB_PATCHED=DIR: for passes with DIR/<crc>.cso (stock DXIL patched by
// tools/patch_dxil.py), run that in place of stock, with our inputs and
// tunables, so it is compared against "ours" (with ATMOLAB_OURS=build/replicas:
// the developer's HLSL replicas, to check the sky-pass patches).
//
// Stock shaders come from extracted/blobs, replacements from build/shaders.
// Constants are the live SkyAtmosphere capture (build/logs/captures/*.txt,
// newest file), plus a synthetic sun for the light constants. Each pass is
// fed the *stock* outputs of earlier passes, so differences don't compound.

#include <d3d12.h>
#include <wrl/client.h>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <map>
#include <regex>
#include <string>
#include <vector>

using Microsoft::WRL::ComPtr;
namespace fs = std::filesystem;

#define CHECK(x) do { HRESULT hr_ = (x); if (FAILED(hr_)) { fprintf(stderr, "%s failed: 0x%08lx (line %d)\n", #x, hr_, __LINE__); exit(1); } } while (0)

static fs::path g_root;

// ---------------------------------------------------------------- inputs

std::vector<float> load_capture()
{
    // the newest capture's constants file (the folder also holds images and binary dumps)
    fs::path dir = g_root / "build" / "logs" / "captures", newest;
    for (const auto &e : fs::directory_iterator(dir))
        if (e.path().filename().string().ends_with("_sky_atmosphere.txt") &&
            (newest.empty() || fs::last_write_time(e) > fs::last_write_time(newest)))
            newest = e.path();
    std::vector<float> regs(13 * 4, 0.0f);
    std::ifstream f(newest);
    std::string line;
    const std::regex re(R"(^c(\d+)\s*\.([xyzw])\s+(\S+))");
    std::smatch m;
    while (std::getline(f, line))
        if (std::regex_search(line, m, re))
            regs[std::stoi(m[1]) * 4 + std::string("xyzw").find(m[2].str()[0])] = std::stof(m[3]);
    printf("constants from %s\n", newest.filename().string().c_str());
    return regs;
}

std::vector<uint8_t> read_file(const fs::path &p)
{
    std::ifstream f(p, std::ios::binary);
    if (!f)
    {
        fprintf(stderr, "missing %s\n", p.string().c_str());
        exit(1);
    }
    return { std::istreambuf_iterator<char>(f), std::istreambuf_iterator<char>() };
}

std::vector<uint8_t> stock_blob(const char *entry, const char *crc)
{
    return read_file(g_root / "extracted" / "blobs" / entry / (std::string(crc) + ".cs_6_7.cso"));
}

// ATMOLAB_OURS=DIR takes "ours" from DIR/<crc>.cso where present.
std::vector<uint8_t> our_blob(const char *crc)
{
    if (const char *dir = getenv("ATMOLAB_OURS"))
        if (const fs::path p = fs::path(dir) / (std::string(crc) + ".cso"); fs::exists(p))
            return read_file(p);
    return read_file(g_root / "build" / "shaders" / (std::string(crc) + ".cso"));
}

// ---------------------------------------------------------------- D3D12

struct Lab
{
    ComPtr<ID3D12Device> dev;
    ComPtr<ID3D12CommandQueue> queue;
    ComPtr<ID3D12CommandAllocator> alloc;
    ComPtr<ID3D12GraphicsCommandList> list;
    ComPtr<ID3D12Fence> fence;
    UINT64 fence_value = 0;
    ComPtr<ID3D12RootSignature> root;
    ComPtr<ID3D12DescriptorHeap> heap;
    UINT inc = 0;
    UINT next_descriptor = 0;
    ComPtr<ID3D12Resource> mod_uav;  // dummy for the u0 space50 root UAV
};

struct Texture
{
    ComPtr<ID3D12Resource> res;
    UINT w = 0, h = 0, d = 0;  // d > 1: 3D texture, or array layers when 'array'
    DXGI_FORMAT format = DXGI_FORMAT_R32G32B32A32_FLOAT;
    bool array = false;
};

constexpr UINT kSrvSlots = 16, kCbvSlots = 8, kUavSlots = 8;

void init(Lab &lab)
{
    CHECK(D3D12CreateDevice(nullptr, D3D_FEATURE_LEVEL_12_0, IID_PPV_ARGS(&lab.dev)));
    D3D12_FEATURE_DATA_SHADER_MODEL sm = { D3D_SHADER_MODEL_6_7 };
    lab.dev->CheckFeatureSupport(D3D12_FEATURE_SHADER_MODEL, &sm, sizeof(sm));
    if (sm.HighestShaderModel < D3D_SHADER_MODEL_6_7)
    {
        fprintf(stderr, "device supports SM 0x%x only; SM 6.7 needed\n", sm.HighestShaderModel);
        exit(1);
    }
    D3D12_COMMAND_QUEUE_DESC qd = { D3D12_COMMAND_LIST_TYPE_COMPUTE };
    CHECK(lab.dev->CreateCommandQueue(&qd, IID_PPV_ARGS(&lab.queue)));
    CHECK(lab.dev->CreateCommandAllocator(D3D12_COMMAND_LIST_TYPE_COMPUTE, IID_PPV_ARGS(&lab.alloc)));
    CHECK(lab.dev->CreateCommandList(0, D3D12_COMMAND_LIST_TYPE_COMPUTE, lab.alloc.Get(), nullptr, IID_PPV_ARGS(&lab.list)));
    CHECK(lab.list->Close());
    CHECK(lab.dev->CreateFence(0, D3D12_FENCE_FLAG_NONE, IID_PPV_ARGS(&lab.fence)));

    // A superset of every pass's bindings, including our space50 parameters.
    D3D12_DESCRIPTOR_RANGE1 ranges[3] = {
        { D3D12_DESCRIPTOR_RANGE_TYPE_SRV, kSrvSlots, 0, 0, D3D12_DESCRIPTOR_RANGE_FLAG_DESCRIPTORS_VOLATILE, 0 },
        { D3D12_DESCRIPTOR_RANGE_TYPE_CBV, kCbvSlots, 0, 0, D3D12_DESCRIPTOR_RANGE_FLAG_DESCRIPTORS_VOLATILE, kSrvSlots },
        { D3D12_DESCRIPTOR_RANGE_TYPE_UAV, kUavSlots, 0, 0, D3D12_DESCRIPTOR_RANGE_FLAG_DESCRIPTORS_VOLATILE, kSrvSlots + kCbvSlots },
    };
    D3D12_ROOT_PARAMETER1 params[5] = {};
    params[0].ParameterType = D3D12_ROOT_PARAMETER_TYPE_DESCRIPTOR_TABLE;
    params[0].DescriptorTable = { 3, ranges };
    params[1].ParameterType = D3D12_ROOT_PARAMETER_TYPE_32BIT_CONSTANTS;
    params[1].Constants = { 0, 222, 2 };
    params[2].ParameterType = D3D12_ROOT_PARAMETER_TYPE_32BIT_CONSTANTS;
    params[2].Constants = { 0, 50, 16 };
    params[3].ParameterType = D3D12_ROOT_PARAMETER_TYPE_UAV;
    params[3].Descriptor = { 0, 50, D3D12_ROOT_DESCRIPTOR_FLAG_NONE };
    params[4].ParameterType = D3D12_ROOT_PARAMETER_TYPE_32BIT_CONSTANTS;
    params[4].Constants = { 1, 50, 32 };  // cloud tunables C0..C31 (zero, but C22 from ATMOLAB_C22)
    D3D12_STATIC_SAMPLER_DESC samplers[4] = {};
    for (UINT i = 0; i < 4; ++i)
    {
        samplers[i].Filter = D3D12_FILTER_MIN_MAG_MIP_LINEAR;
        samplers[i].AddressU = samplers[i].AddressV = samplers[i].AddressW = D3D12_TEXTURE_ADDRESS_MODE_CLAMP;
        samplers[i].MaxLOD = D3D12_FLOAT32_MAX;
        samplers[i].ShaderRegister = i;
    }
    D3D12_VERSIONED_ROOT_SIGNATURE_DESC rs = { D3D_ROOT_SIGNATURE_VERSION_1_1 };
    rs.Desc_1_1 = { 5, params, 4, samplers, D3D12_ROOT_SIGNATURE_FLAG_NONE };
    ComPtr<ID3DBlob> blob, err;
    if (FAILED(D3D12SerializeVersionedRootSignature(&rs, &blob, &err)))
    {
        fprintf(stderr, "%s\n", static_cast<const char *>(err->GetBufferPointer()));
        exit(1);
    }
    CHECK(lab.dev->CreateRootSignature(0, blob->GetBufferPointer(), blob->GetBufferSize(), IID_PPV_ARGS(&lab.root)));

    D3D12_DESCRIPTOR_HEAP_DESC hd = { D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV, 1024, D3D12_DESCRIPTOR_HEAP_FLAG_SHADER_VISIBLE };
    CHECK(lab.dev->CreateDescriptorHeap(&hd, IID_PPV_ARGS(&lab.heap)));
    lab.inc = lab.dev->GetDescriptorHandleIncrementSize(D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV);

    D3D12_HEAP_PROPERTIES hp = { D3D12_HEAP_TYPE_DEFAULT };
    D3D12_RESOURCE_DESC rd = {};
    rd.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER;
    rd.Width = 4096;
    rd.Height = rd.DepthOrArraySize = rd.MipLevels = 1;
    rd.SampleDesc.Count = 1;
    rd.Layout = D3D12_TEXTURE_LAYOUT_ROW_MAJOR;
    rd.Flags = D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS;
    CHECK(lab.dev->CreateCommittedResource(&hp, D3D12_HEAP_FLAG_NONE, &rd, D3D12_RESOURCE_STATE_COMMON, nullptr,
                                           IID_PPV_ARGS(&lab.mod_uav)));
}

void wait(Lab &lab)
{
    CHECK(lab.queue->Signal(lab.fence.Get(), ++lab.fence_value));
    while (lab.fence->GetCompletedValue() < lab.fence_value)
        Sleep(1);
}

void execute(Lab &lab)
{
    CHECK(lab.list->Close());
    ID3D12CommandList *lists[] = { lab.list.Get() };
    lab.queue->ExecuteCommandLists(1, lists);
    wait(lab);
}

ComPtr<ID3D12Resource> upload_buffer(Lab &lab, const void *data, size_t size)
{
    D3D12_HEAP_PROPERTIES hp = { D3D12_HEAP_TYPE_UPLOAD };
    D3D12_RESOURCE_DESC rd = {};
    rd.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER;
    rd.Width = (size + 255) & ~size_t(255);
    rd.Height = rd.DepthOrArraySize = rd.MipLevels = 1;
    rd.SampleDesc.Count = 1;
    rd.Layout = D3D12_TEXTURE_LAYOUT_ROW_MAJOR;
    ComPtr<ID3D12Resource> buf;
    CHECK(lab.dev->CreateCommittedResource(&hp, D3D12_HEAP_FLAG_NONE, &rd, D3D12_RESOURCE_STATE_GENERIC_READ, nullptr,
                                           IID_PPV_ARGS(&buf)));
    void *p;
    CHECK(buf->Map(0, nullptr, &p));
    memcpy(p, data, size);
    buf->Unmap(0, nullptr);
    return buf;
}

Texture make_texture(Lab &lab, UINT w, UINT h, UINT d,
                     DXGI_FORMAT format = DXGI_FORMAT_R32G32B32A32_FLOAT, bool array = false)
{
    Texture t{ {}, w, h, d, format, array };
    D3D12_HEAP_PROPERTIES hp = { D3D12_HEAP_TYPE_DEFAULT };
    D3D12_RESOURCE_DESC rd = {};
    rd.Dimension = d > 1 && !array ? D3D12_RESOURCE_DIMENSION_TEXTURE3D : D3D12_RESOURCE_DIMENSION_TEXTURE2D;
    rd.Width = w;
    rd.Height = h;
    rd.DepthOrArraySize = static_cast<UINT16>(d);
    rd.MipLevels = 1;
    rd.Format = format;
    rd.SampleDesc.Count = 1;
    rd.Flags = D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS | D3D12_RESOURCE_FLAG_ALLOW_SIMULTANEOUS_ACCESS;
    CHECK(lab.dev->CreateCommittedResource(&hp, D3D12_HEAP_FLAG_NONE, &rd, D3D12_RESOURCE_STATE_COMMON, nullptr,
                                           IID_PPV_ARGS(&t.res)));
    return t;
}

void execute(Lab &lab);

// RGBA32F texture with given texels (x fastest, then y, then z / layer).
Texture make_filled_texture(Lab &lab, UINT w, UINT h, UINT d, const std::vector<float> &texels, bool array = false)
{
    Texture t = make_texture(lab, w, h, d, DXGI_FORMAT_R32G32B32A32_FLOAT, array);
    D3D12_RESOURCE_DESC rd = t.res->GetDesc();
    const UINT subresources = array ? d : 1;
    const UINT slices = array ? 1 : d;
    std::vector<D3D12_PLACED_SUBRESOURCE_FOOTPRINT> fp(subresources);
    UINT64 total;
    lab.dev->GetCopyableFootprints(&rd, 0, subresources, 0, fp.data(), nullptr, nullptr, &total);
    std::vector<uint8_t> staging(total);
    for (UINT sub = 0; sub < subresources; ++sub)
        for (UINT z = 0; z < slices; ++z)
            for (UINT y = 0; y < h; ++y)
                memcpy(staging.data() + fp[sub].Offset +
                           (size_t(z) * fp[sub].Footprint.Height + y) * fp[sub].Footprint.RowPitch,
                       &texels[((size_t(sub) + z) * h + y) * w * 4], size_t(w) * 16);
    auto upload = upload_buffer(lab, staging.data(), staging.size());

    CHECK(lab.alloc->Reset());
    CHECK(lab.list->Reset(lab.alloc.Get(), nullptr));
    for (UINT sub = 0; sub < subresources; ++sub)
    {
        D3D12_TEXTURE_COPY_LOCATION src = { upload.Get(), D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT };
        src.PlacedFootprint = fp[sub];
        D3D12_TEXTURE_COPY_LOCATION dst = { t.res.Get(), D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX };
        dst.SubresourceIndex = sub;
        lab.list->CopyTextureRegion(&dst, 0, 0, 0, &src, nullptr);
    }
    execute(lab);
    return t;
}

D3D12_CPU_DESCRIPTOR_HANDLE cpu(Lab &lab, UINT i) { return { lab.heap->GetCPUDescriptorHandleForHeapStart().ptr + SIZE_T(i) * lab.inc }; }
D3D12_GPU_DESCRIPTOR_HANDLE gpu(Lab &lab, UINT i) { return { lab.heap->GetGPUDescriptorHandleForHeapStart().ptr + UINT64(i) * lab.inc }; }

struct Pass
{
    const char *name;
    std::vector<uint8_t> shader;
    std::vector<ID3D12Resource *> cbs;     // b0, b1, ...
    std::vector<UINT> cb_sizes;
    std::vector<const Texture *> srvs;     // t0, t1, ...
    const Texture *uav;                    // u0
    UINT groups[3];
    const uint32_t *mod_params = nullptr;  // 16 root constants at b0 space50
};

void run(Lab &lab, const Pass &pass)
{
    D3D12_COMPUTE_PIPELINE_STATE_DESC pd = {};
    pd.pRootSignature = lab.root.Get();
    pd.CS = { pass.shader.data(), pass.shader.size() };
    ComPtr<ID3D12PipelineState> pso;
    CHECK(lab.dev->CreateComputePipelineState(&pd, IID_PPV_ARGS(&pso)));

    // Fill this pass's table: null descriptors first, then the real ones.
    const UINT base = lab.next_descriptor;
    lab.next_descriptor += kSrvSlots + kCbvSlots + kUavSlots;
    for (UINT i = 0; i < kSrvSlots; ++i)
    {
        D3D12_SHADER_RESOURCE_VIEW_DESC sd = {};
        sd.Shader4ComponentMapping = D3D12_DEFAULT_SHADER_4_COMPONENT_MAPPING;
        const Texture *t = i < pass.srvs.size() ? pass.srvs[i] : nullptr;
        sd.Format = t ? t->format : DXGI_FORMAT_R32G32B32A32_FLOAT;
        if (t && t->array)
        {
            sd.ViewDimension = D3D12_SRV_DIMENSION_TEXTURE2DARRAY;
            sd.Texture2DArray.MipLevels = 1;
            sd.Texture2DArray.ArraySize = t->d;
        }
        else
        {
            sd.ViewDimension = t && t->d > 1 ? D3D12_SRV_DIMENSION_TEXTURE3D : D3D12_SRV_DIMENSION_TEXTURE2D;
            sd.Texture2D.MipLevels = 1;
        }
        lab.dev->CreateShaderResourceView(t ? t->res.Get() : nullptr, &sd, cpu(lab, base + i));
    }
    for (UINT i = 0; i < kCbvSlots; ++i)
    {
        D3D12_CONSTANT_BUFFER_VIEW_DESC cd = {};
        if (i < pass.cbs.size())
        {
            cd.BufferLocation = pass.cbs[i]->GetGPUVirtualAddress();
            cd.SizeInBytes = (pass.cb_sizes[i] + 255) & ~255u;
        }
        lab.dev->CreateConstantBufferView(&cd, cpu(lab, base + kSrvSlots + i));
    }
    for (UINT i = 0; i < kUavSlots; ++i)
    {
        D3D12_UNORDERED_ACCESS_VIEW_DESC ud = {};
        const Texture *t = i == 0 ? pass.uav : nullptr;
        ud.Format = t ? t->format : DXGI_FORMAT_R32G32B32A32_FLOAT;
        ud.ViewDimension = t && t->d > 1 ? D3D12_UAV_DIMENSION_TEXTURE3D : D3D12_UAV_DIMENSION_TEXTURE2D;
        if (t && t->d > 1)
            ud.Texture3D.WSize = t->d;
        lab.dev->CreateUnorderedAccessView(t ? t->res.Get() : nullptr, nullptr, &ud, cpu(lab, base + kSrvSlots + kCbvSlots + i));
    }

    CHECK(lab.alloc->Reset());
    CHECK(lab.list->Reset(lab.alloc.Get(), pso.Get()));
    ID3D12DescriptorHeap *heaps[] = { lab.heap.Get() };
    lab.list->SetDescriptorHeaps(1, heaps);
    lab.list->SetComputeRootSignature(lab.root.Get());
    lab.list->SetComputeRootDescriptorTable(0, gpu(lab, base));
    const UINT zeros[32] = {};
    lab.list->SetComputeRoot32BitConstants(1, 2, zeros, 0);
    lab.list->SetComputeRoot32BitConstants(2, 16, pass.mod_params ? pass.mod_params : zeros, 0);
    lab.list->SetComputeRootUnorderedAccessView(3, lab.mod_uav->GetGPUVirtualAddress());
    // cloud tunables: zero, except C22 (the daylight white balance) from ATMOLAB_C22
    float cloud[32] = {};
    if (const char *c22 = getenv("ATMOLAB_C22"))
        cloud[22] = static_cast<float>(atof(c22));
    lab.list->SetComputeRoot32BitConstants(4, 32, cloud, 0);
    lab.list->Dispatch(pass.groups[0], pass.groups[1], pass.groups[2]);
    D3D12_RESOURCE_BARRIER b = { D3D12_RESOURCE_BARRIER_TYPE_UAV };
    b.UAV.pResource = pass.uav->res.Get();
    lab.list->ResourceBarrier(1, &b);
    CHECK(lab.list->Close());
    ID3D12CommandList *lists[] = { lab.list.Get() };
    lab.queue->ExecuteCommandLists(1, lists);
    wait(lab);
}

std::vector<float> read_back(Lab &lab, const Texture &t)
{
    D3D12_RESOURCE_DESC rd = t.res->GetDesc();
    D3D12_PLACED_SUBRESOURCE_FOOTPRINT fp;
    UINT64 total;
    lab.dev->GetCopyableFootprints(&rd, 0, 1, 0, &fp, nullptr, nullptr, &total);

    D3D12_HEAP_PROPERTIES hp = { D3D12_HEAP_TYPE_READBACK };
    D3D12_RESOURCE_DESC bd = {};
    bd.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER;
    bd.Width = total;
    bd.Height = bd.DepthOrArraySize = bd.MipLevels = 1;
    bd.SampleDesc.Count = 1;
    bd.Layout = D3D12_TEXTURE_LAYOUT_ROW_MAJOR;
    ComPtr<ID3D12Resource> rb;
    CHECK(lab.dev->CreateCommittedResource(&hp, D3D12_HEAP_FLAG_NONE, &bd, D3D12_RESOURCE_STATE_COPY_DEST, nullptr, IID_PPV_ARGS(&rb)));

    CHECK(lab.alloc->Reset());
    CHECK(lab.list->Reset(lab.alloc.Get(), nullptr));
    D3D12_TEXTURE_COPY_LOCATION dst = { rb.Get(), D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT };
    dst.PlacedFootprint = fp;
    D3D12_TEXTURE_COPY_LOCATION src = { t.res.Get(), D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX };
    lab.list->CopyTextureRegion(&dst, 0, 0, 0, &src, nullptr);
    CHECK(lab.list->Close());
    ID3D12CommandList *lists[] = { lab.list.Get() };
    lab.queue->ExecuteCommandLists(1, lists);
    wait(lab);

    std::vector<float> out(size_t(t.w) * t.h * t.d * 4);
    uint8_t *p;
    CHECK(rb->Map(0, nullptr, reinterpret_cast<void **>(&p)));
    for (UINT z = 0; z < t.d; ++z)
        for (UINT y = 0; y < t.h; ++y)
        {
            const uint8_t *row = p + fp.Offset + (size_t(z) * fp.Footprint.Height + y) * fp.Footprint.RowPitch;
            float *dst = &out[(size_t(z) * t.h + y) * t.w * 4];
            if (t.format == DXGI_FORMAT_R16G16B16A16_UNORM)
                for (UINT i = 0; i < t.w * 4; ++i)
                    dst[i] = reinterpret_cast<const uint16_t *>(row)[i] / 65535.0f;
            else
                memcpy(dst, row, size_t(t.w) * 16);
        }
    rb->Unmap(0, nullptr);
    return out;
}

// Max relative error over rgb, relative to the larger magnitude (with a floor).
void report(const char *name, const std::vector<float> &a, const std::vector<float> &b)
{
    double max_rel = 0, max_abs = 0, peak = 0;
    size_t worst = 0;
    for (size_t i = 0; i < a.size(); ++i)
    {
        if (i % 4 == 3)
            continue;
        const double d = std::fabs(double(a[i]) - b[i]);
        const double m = std::max({ std::fabs(double(a[i])), std::fabs(double(b[i])), 1e-6 });
        peak = std::max(peak, std::fabs(double(a[i])));
        max_abs = std::max(max_abs, d);
        if (d / m > max_rel)
        {
            max_rel = d / m;
            worst = i;
        }
    }
    printf("  %-22s max rel err %.3g, max abs err %.3g (peak %.3g)  worst: stock %.6g ours %.6g  [texel %zu ch %zu]\n",
           name, max_rel, max_abs, peak, a[worst], b[worst], worst / 4, worst % 4);
    double alpha_err = 0;
    for (size_t i = 3; i < a.size(); i += 4)
        alpha_err = std::max(alpha_err, std::fabs(double(a[i]) - b[i]));
    printf("  %-22s alpha max abs err %.3g\n", "", alpha_err);
}

// rgb to <path>.pfm, alpha to <path>_alpha.pfm (greyscale).
void write_pfm(const fs::path &path, const Texture &t, const std::vector<float> &px)
{
    const UINT h = t.h * t.d;  // 3D textures: slices stacked vertically
    std::ofstream rgb(path, std::ios::binary), alpha(fs::path(path).replace_extension().string() + "_alpha.pfm",
                                                     std::ios::binary);
    rgb << "PF\n" << t.w << ' ' << h << "\n-1.0\n";
    alpha << "Pf\n" << t.w << ' ' << h << "\n-1.0\n";
    for (UINT y = h; y-- > 0;)  // PFM rows go bottom-up
        for (UINT x = 0; x < t.w; ++x)
        {
            const float *p = &px[(size_t(y) * t.w + x) * 4];
            rgb.write(reinterpret_cast<const char *>(p), 12);
            alpha.write(reinterpret_cast<const char *>(p + 3), 4);
        }
}

std::vector<float> random_texels(size_t count, float lo, float hi, uint32_t seed)
{
    std::vector<float> out(count * 4);
    for (float &v : out)
    {
        seed = seed * 1664525u + 1013904223u;
        v = lo + (hi - lo) * float(seed >> 8) / float(1u << 24);
    }
    return out;
}

// ---------------------------------------------------------------- main

int main(int argc, char **argv)
{
    g_root = fs::path(argv[0]).parent_path();
    while (!g_root.empty() && !fs::exists(g_root / "shaders" / "manifest.json"))
        g_root = g_root.parent_path();
    if (g_root.empty() || argc < 2)
    {
        fprintf(stderr, "usage: atmolab compare|dump DIR [--sun DEG] [--alt M]  (run from inside the project)\n");
        return 1;
    }
    const std::string mode = argv[1];
    fs::path dump_dir;
    float sun_deg = 20.0f, alt_m = 10000.0f, ap_dist_m = 100000.0f, pitch_deg = -10.0f;
    bool model = false;
    for (int i = 2; i < argc; ++i)
    {
        const std::string a = argv[i];
        if (a == "--sun" && i + 1 < argc) sun_deg = std::stof(argv[++i]);
        else if (a == "--alt" && i + 1 < argc) alt_m = std::stof(argv[++i]);
        else if (a == "--apdist" && i + 1 < argc) ap_dist_m = std::stof(argv[++i]);
        else if (a == "--pitch" && i + 1 < argc) pitch_deg = std::stof(argv[++i]);
        else if (a == "--model") model = true;
        else if (mode == "dump" && dump_dir.empty()) dump_dir = a;
    }

    Lab lab;
    init(lab);

    std::vector<float> atmo = load_capture();
    std::vector<float> global(300 * 4, 0.0f);
    global[117 * 4 + 1] = alt_m;  // camera altitude (m) as the world origin offset, as in game; Global[132] = 0
    global[10 * 4 + 1] = ap_dist_m;  // aerial-perspective range (m)
    // Inverse projection (rows 174-177): ndc (x, y, 0, 1) -> view ray (x*tan, y*tan, 1), 90x60 deg.
    global[174 * 4 + 0] = 1.0f; global[175 * 4 + 1] = 0.577f; global[176 * 4 + 3] = 1.0f; global[177 * 4 + 3] = 1.0f;
    // View rotation (rows 134-136): pitch about x.
    const float pr = pitch_deg * 3.14159265f / 180.0f;
    global[134 * 4 + 0] = 1.0f;
    global[135 * 4 + 1] = std::cos(pr); global[135 * 4 + 2] = std::sin(pr);
    global[136 * 4 + 1] = -std::sin(pr); global[136 * 4 + 2] = std::cos(pr);
    global[92 * 4 + 2] = 1.0f / 200000.0f;        // cloud-layer map: 200 km across, centred on origin
    global[51 * 4 + 3] = 50000.0f;                // cloud-shadow volume radius (m)
    global[56 * 4 + 2] = 0.1f;
    global[94 * 4 + 2] = 1.0f / (2000.0f * 100.0f); // light-pollution depth: 100 km
    std::vector<float> user(658 * 4, 0.0f);
    user[0] = 1.0f / 255; user[1] = 1.0f / 255; user[2] = 2.5f;     // cloud atmospherics: 256 texels, shell at 2.5 km
    user[4] = 1.0f / 32; user[5] = 1.0f / 32; user[6] = 1.0f / 64;  // camera volume size
    const uint32_t noise_layer = 2, noise_enabled = 1;
    memcpy(&global[1 * 4 + 3], &noise_layer, 4);
    memcpy(&global[295 * 4 + 3], &noise_enabled, 4);
    std::vector<float> light(44, 0.0f);
    const float s = sun_deg * 3.14159265f / 180.0f;
    light[0] = std::cos(s); light[1] = std::sin(s); light[2] = 0.0f;  // sun direction
    light[4] = light[5] = light[6] = 1.0f;                             // sun illuminance
    light[8] = 0.6f; light[9] = 0.5f; light[10] = 0.62f;               // moon direction (exercise that path)
    light[12] = light[13] = light[14] = 0.02f;                         // moon illuminance
    for (int c = 0; c < 4; ++c) light[20 + c * 5] = 1.0f;              // cloud-cover matrix = identity
    light[36] = 10000.0f; light[37] = 50000.0f;                        // cloud-cover fade 10-50 km

    auto cb_global = upload_buffer(lab, global.data(), global.size() * 4);
    auto cb_atmo = upload_buffer(lab, atmo.data(), atmo.size() * 4);
    auto cb_light = upload_buffer(lab, light.data(), light.size() * 4);
    auto cb_user = upload_buffer(lab, user.data(), user.size() * 4);

    // Synthetic cloud inputs, so every MSFS-specific path in the AP pass runs.
    std::vector<float> layer = random_texels(64 * 64, 0.0f, 1.0f, 1);
    for (size_t i = 0; i < 64 * 64; ++i)
    {
        layer[i * 4 + 0] = 800.0f + 1200.0f * layer[i * 4 + 0];   // bottom (m)
        layer[i * 4 + 1] = layer[i * 4 + 0] + 300.0f + 2000.0f * layer[i * 4 + 1];  // top (m)
    }
    // ATMOLAB_OFF=layer,shadow,glow neutralises those inputs (for isolating differences).
    const std::string off = getenv("ATMOLAB_OFF") ? getenv("ATMOLAB_OFF") : "";
    if (off.find("layer") != std::string::npos)
        for (size_t i = 0; i < 64 * 64; ++i) { layer[i * 4] = -1e6f; layer[i * 4 + 1] = -1e6f + 1.0f; }  // fully lit
    Texture cloud_layer = make_filled_texture(lab, 64, 64, 1, layer);
    Texture cloud_shadow = make_filled_texture(lab, 32, 32, 16, off.find("shadow") != std::string::npos
        ? random_texels(32 * 32 * 16, 1.0f, 1.0f, 2) : random_texels(32 * 32 * 16, 0.2f, 1.0f, 2));
    Texture light_volume = make_filled_texture(lab, 64, 64, 64, random_texels(64 * 64 * 64, 0.0f,
        off.find("glow") != std::string::npos ? 0.0f : 0.01f, 3));
    Texture blue_noise = make_filled_texture(lab, 64, 64, 4, random_texels(64 * 64 * 4, 0.0f, 1.0f, 4), true);

    struct Stage
    {
        const char *entry, *crc;
        UINT w, h, d, groups[3];
        DXGI_FORMAT format = DXGI_FORMAT_R32G32B32A32_FLOAT;
    };
    const Stage stages[] = {
        { "ch_transmittance_lut", "0xd33dae66", 256, 64, 1, { 32, 8, 1 } },
        { "ch_multi_scatt", "0x81355ad0", 32, 32, 1, { 32, 32, 1 } },
        { "ch_sky_view", "0xdc5e4b84", 128, 128, 1, { 16, 16, 1 } },
        { "ch_camera_volume", "0x7d2ec0ce", 32, 32, 64, { 4, 4, 64 } },
        { "ph_globalCloudAtmospherics", "0x0283ca18", 256, 256, 1, { 32, 32, 1 }, DXGI_FORMAT_R16G16B16A16_UNORM },
    };

    // ModParams: debug_capture, frame, T12, T13, then T0..T11 as floats.
    uint32_t mod_params[16] = {};
    if (model)
    {
        std::ifstream f(g_root / "build" / "tunables.ini");
        std::string line;
        unsigned index;
        float value;
        while (std::getline(f, line))
            if (sscanf_s(line.c_str(), " T%u = %f", &index, &value) == 2 && index < 14)
                memcpy(&mod_params[index < 12 ? 4 + index : index - 10], &value, 4);
        puts("model tunables from build/tunables.ini");
    }
    if (mode == "dump")
    {
        // The constants as raw float4 rows, for offline checks (tools/test_cloud_ap.py).
        fs::create_directories(dump_dir);
        auto write = [&](const char *name, const void *data, size_t bytes) {
            std::ofstream(dump_dir / name, std::ios::binary).write(static_cast<const char *>(data),
                                                                   static_cast<std::streamsize>(bytes));
        };
        write("cb_global.bin", global.data(), global.size() * 4);
        write("cb_atmo.bin", atmo.data(), atmo.size() * 4);
        write("mod_params.bin", mod_params, sizeof(mod_params));
    }

    std::map<std::string, Texture> stock, ours;  // outputs that feed later passes
    // In model mode our passes read our own earlier outputs; otherwise stock's.
    auto input = [&](const char *name, bool for_ours) { return for_ours && model ? &ours[name] : &stock[name]; };
    for (const Stage &st : stages)
    {
        std::vector<ID3D12Resource *> cbs;
        std::vector<UINT> sizes;
        std::vector<const Texture *> srvs, srvs_ours;
        const std::string e = st.entry;
        if (e == "ch_transmittance_lut")
        {
            cbs = { cb_atmo.Get() };
            sizes = { 208 };
        }
        else if (e == "ph_globalCloudAtmospherics")
        {
            cbs = { cb_user.Get(), cb_global.Get(), cb_atmo.Get(), cb_light.Get() };
            sizes = { 10528, 4800, 208, 176 };
            srvs = { &blue_noise, &cloud_shadow, &light_volume, input("ch_transmittance_lut", false), input("ch_multi_scatt", false) };
            srvs_ours = { &blue_noise, &cloud_shadow, &light_volume, input("ch_transmittance_lut", true), input("ch_multi_scatt", true) };
        }
        else if (e == "ch_camera_volume")
        {
            cbs = { cb_user.Get(), cb_global.Get(), cb_atmo.Get(), cb_light.Get() };
            sizes = { 10528, 4800, 208, 176 };
            srvs = { &cloud_layer, &cloud_shadow, &light_volume, input("ch_transmittance_lut", false), input("ch_multi_scatt", false) };
            srvs_ours = { &cloud_layer, &cloud_shadow, &light_volume, input("ch_transmittance_lut", true), input("ch_multi_scatt", true) };
        }
        else
        {
            cbs = { cb_global.Get(), cb_atmo.Get(), cb_light.Get() };
            sizes = { 4800, 208, 176 };
            srvs = { input("ch_transmittance_lut", false) };
            srvs_ours = { input("ch_transmittance_lut", true) };
            if (e == "ch_sky_view")
            {
                srvs.push_back(input("ch_multi_scatt", false));
                srvs_ours.push_back(input("ch_multi_scatt", true));
            }
        }

        Texture out_stock = make_texture(lab, st.w, st.h, st.d, st.format);
        Texture out_ours = make_texture(lab, st.w, st.h, st.d, st.format);
        const char *patched_dir = getenv("ATMOLAB_PATCHED");
        const fs::path patched = patched_dir ? fs::path(patched_dir) / (std::string(st.crc) + ".cso") : fs::path();
        if (!patched.empty() && fs::exists(patched))
        {
            printf("%s: patched stock (%s) vs ours\n", st.entry, patched.string().c_str());
            run(lab, { st.entry, read_file(patched), cbs, sizes, srvs_ours.empty() ? srvs : srvs_ours, &out_stock,
                       { st.groups[0], st.groups[1], st.groups[2] }, mod_params });
        }
        else
            run(lab, { st.entry, stock_blob(st.entry, st.crc), cbs, sizes, srvs, &out_stock,
                       { st.groups[0], st.groups[1], st.groups[2] } });
        run(lab, { st.entry, our_blob(st.crc), cbs, sizes, srvs_ours.empty() ? srvs : srvs_ours, &out_ours,
                   { st.groups[0], st.groups[1], st.groups[2] }, mod_params });

        const auto a = read_back(lab, out_stock);
        const auto b = read_back(lab, out_ours);
        report(st.entry, a, b);
        if (st.d > 1 && getenv("ATMOLAB_SLICES"))
            for (UINT z = 0; z < st.d; ++z)
            {
                double ea = 0, er = 0;
                UINT worst_xy = 0;
                for (UINT i = 0; i < st.w * st.h; ++i)
                {
                    const size_t k = (size_t(z) * st.w * st.h + i) * 4;
                    ea = std::max(ea, std::fabs(double(a[k + 3]) - b[k + 3]));
                    for (int c = 0; c < 3; ++c)
                    {
                        const double r = std::fabs(double(a[k + c]) - b[k + c]) /
                                         std::max({ std::fabs(double(a[k + c])), std::fabs(double(b[k + c])), 1e-6 });
                        if (r > er) { er = r; worst_xy = i; }
                    }
                }
                if (ea > 1e-7 || er > 1e-5)
                    printf("    slice %2u: alpha err %.3g, rgb rel err %.3g at (%u,%u)\n", z, ea, er,
                           worst_xy % st.w, worst_xy / st.w);
            }
        if (mode == "dump")
        {
            fs::create_directories(dump_dir);
            write_pfm(dump_dir / ("stock_" + e + ".pfm"), out_stock, a);
            write_pfm(dump_dir / ("ours_" + e + ".pfm"), out_ours, b);
        }
        stock[e] = out_stock;
        ours[e] = out_ours;
    }
    return 0;
}
