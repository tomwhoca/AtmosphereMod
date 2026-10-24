// psotest: can the driver build a pipeline from a (patched) shader against the
// game's global root signature?
//
//   psotest <vs.cso> <ps.cso> [--no-mod]   graphics
//   psotest --cs <cs.cso> [--no-mod]        compute
//
// The root signatures mirror build/logs/rootsigs.log. Graphics: root constants
// b0 space222 x2, a t0 x80 / b0 x16 / s0 x16 table triple per stage, a u0 x8
// table. Compute: the same constants and one t/b/s/u table each. Unless
// --no-mod, plus what the add-on injects into both: 16 root constants at
// b0 space50 and the debug buffer's root UAV at u0 space50; compute also gets
// the cloud tunables, 32 root constants at b1 space50. Exit code 0 when the
// pipeline is created.

#include <d3d12.h>
#include <wrl/client.h>

#include <cstdio>
#include <cstring>
#include <fstream>
#include <vector>

using Microsoft::WRL::ComPtr;

namespace
{
std::vector<char> read_file(const char *path)
{
    std::ifstream file(path, std::ios::binary);
    return { std::istreambuf_iterator<char>(file), std::istreambuf_iterator<char>() };
}

void print_messages(ID3D12Device *dev)
{
    ComPtr<ID3D12InfoQueue> info;
    if (FAILED(dev->QueryInterface(IID_PPV_ARGS(&info))))
        return;
    for (UINT64 i = 0; i < info->GetNumStoredMessages(); ++i)
    {
        SIZE_T size = 0;
        info->GetMessage(i, nullptr, &size);
        std::vector<char> buf(size);
        auto *msg = reinterpret_cast<D3D12_MESSAGE *>(buf.data());
        info->GetMessage(i, msg, &size);
        printf("  debug layer: %.*s\n", static_cast<int>(msg->DescriptionByteLength), msg->pDescription);
    }
}
int test_compute(ID3D12Device *dev, const std::vector<char> &cs, const char *name, bool mod, bool debug_layer)
{
    const auto flags = D3D12_DESCRIPTOR_RANGE_FLAG_DESCRIPTORS_VOLATILE;
    const D3D12_DESCRIPTOR_RANGE1 ranges[4] = {
        { D3D12_DESCRIPTOR_RANGE_TYPE_SRV, 80, 0, 0, flags, 0 },
        { D3D12_DESCRIPTOR_RANGE_TYPE_CBV, 16, 0, 0, flags, 0 },
        { D3D12_DESCRIPTOR_RANGE_TYPE_SAMPLER, 16, 0, 0, flags, 0 },
        { D3D12_DESCRIPTOR_RANGE_TYPE_UAV, 8, 0, 0, flags, 0 },
    };
    std::vector<D3D12_ROOT_PARAMETER1> params;
    D3D12_ROOT_PARAMETER1 p = {};
    p.ParameterType = D3D12_ROOT_PARAMETER_TYPE_32BIT_CONSTANTS;
    p.Constants = { 0, 222, 2 };
    params.push_back(p);
    for (const auto &range : ranges)
    {
        p = {};
        p.ParameterType = D3D12_ROOT_PARAMETER_TYPE_DESCRIPTOR_TABLE;
        p.DescriptorTable = { 1, &range };
        params.push_back(p);
    }
    if (mod)
    {
        p = {};
        p.ParameterType = D3D12_ROOT_PARAMETER_TYPE_32BIT_CONSTANTS;
        p.Constants = { 0, 50, 16 };
        params.push_back(p);
        p = {};
        p.ParameterType = D3D12_ROOT_PARAMETER_TYPE_UAV;
        p.Descriptor = { 0, 50, D3D12_ROOT_DESCRIPTOR_FLAG_NONE };
        params.push_back(p);
        p = {};
        p.ParameterType = D3D12_ROOT_PARAMETER_TYPE_32BIT_CONSTANTS;
        p.Constants = { 1, 50, 32 };  // cloud tunables C0..C31
        params.push_back(p);
        p = {};
        p.ParameterType = D3D12_ROOT_PARAMETER_TYPE_SRV;  // MSFS's TLAS for the aircraft's shadow with RT shadows
        p.Descriptor = { 0, 50, D3D12_ROOT_DESCRIPTOR_FLAG_NONE };
        params.push_back(p);
    }
    D3D12_VERSIONED_ROOT_SIGNATURE_DESC rd = {};
    rd.Version = D3D_ROOT_SIGNATURE_VERSION_1_1;
    rd.Desc_1_1.NumParameters = static_cast<UINT>(params.size());
    rd.Desc_1_1.pParameters = params.data();
    rd.Desc_1_1.Flags = D3D12_ROOT_SIGNATURE_FLAG_CBV_SRV_UAV_HEAP_DIRECTLY_INDEXED |
                        D3D12_ROOT_SIGNATURE_FLAG_SAMPLER_HEAP_DIRECTLY_INDEXED;
    ComPtr<ID3DBlob> blob, error;
    ComPtr<ID3D12RootSignature> root;
    if (FAILED(D3D12SerializeVersionedRootSignature(&rd, &blob, &error)) ||
        FAILED(dev->CreateRootSignature(0, blob->GetBufferPointer(), blob->GetBufferSize(), IID_PPV_ARGS(&root))))
    {
        fprintf(stderr, "root signature: %s\n", error ? static_cast<const char *>(error->GetBufferPointer()) : "?");
        return 2;
    }
    D3D12_COMPUTE_PIPELINE_STATE_DESC pd = {};
    pd.pRootSignature = root.Get();
    pd.CS = { cs.data(), cs.size() };
    ComPtr<ID3D12PipelineState> pso;
    const HRESULT hr = dev->CreateComputePipelineState(&pd, IID_PPV_ARGS(&pso));
    printf("%s with%s our root parameters: %s (0x%08lx)%s\n", name, mod ? "" : "out",
           SUCCEEDED(hr) ? "pipeline created" : "FAILED", static_cast<unsigned long>(hr),
           debug_layer ? "" : " [no debug layer]");
    print_messages(dev);
    return SUCCEEDED(hr) ? 0 : 1;
}
} // namespace

int main(int argc, char **argv)
{
    if (argc < 3)
    {
        fprintf(stderr, "usage: psotest <vs.cso> <ps.cso> [--no-mod] | psotest --cs <cs.cso> [--no-mod]\n");
        return 2;
    }
    const bool compute = strcmp(argv[1], "--cs") == 0;
    const bool mod = strcmp(argv[argc - 1], "--no-mod") != 0;
    const std::vector<char> vs = compute ? std::vector<char>() : read_file(argv[1]), ps = read_file(argv[2]);
    if ((!compute && vs.empty()) || ps.empty())
    {
        fprintf(stderr, "cannot read the shaders\n");
        return 2;
    }

    ComPtr<ID3D12Debug> debug;
    const bool debug_layer = SUCCEEDED(D3D12GetDebugInterface(IID_PPV_ARGS(&debug)));
    if (debug_layer)
        debug->EnableDebugLayer();
    ComPtr<ID3D12Device> dev;
    if (FAILED(D3D12CreateDevice(nullptr, D3D_FEATURE_LEVEL_12_0, IID_PPV_ARGS(&dev))))
    {
        fprintf(stderr, "no D3D12 device\n");
        return 2;
    }
    if (compute)
        return test_compute(dev.Get(), ps, argv[2], mod, debug_layer);

    // Seven t/b/s triples, one per graphics stage, like the game's layout.
    const D3D12_SHADER_VISIBILITY stages[7] = {
        D3D12_SHADER_VISIBILITY_VERTEX, D3D12_SHADER_VISIBILITY_HULL, D3D12_SHADER_VISIBILITY_DOMAIN,
        D3D12_SHADER_VISIBILITY_GEOMETRY, D3D12_SHADER_VISIBILITY_PIXEL, D3D12_SHADER_VISIBILITY_AMPLIFICATION,
        D3D12_SHADER_VISIBILITY_MESH,
    };
    const auto flags = D3D12_DESCRIPTOR_RANGE_FLAG_DESCRIPTORS_VOLATILE;
    const D3D12_DESCRIPTOR_RANGE1 srv = { D3D12_DESCRIPTOR_RANGE_TYPE_SRV, 80, 0, 0, flags, 0 };
    const D3D12_DESCRIPTOR_RANGE1 cbv = { D3D12_DESCRIPTOR_RANGE_TYPE_CBV, 16, 0, 0, flags, 0 };
    const D3D12_DESCRIPTOR_RANGE1 smp = { D3D12_DESCRIPTOR_RANGE_TYPE_SAMPLER, 16, 0, 0,
                                          D3D12_DESCRIPTOR_RANGE_FLAG_DESCRIPTORS_VOLATILE, 0 };
    const D3D12_DESCRIPTOR_RANGE1 uav = { D3D12_DESCRIPTOR_RANGE_TYPE_UAV, 8, 0, 0, flags, 0 };

    std::vector<D3D12_ROOT_PARAMETER1> params;
    D3D12_ROOT_PARAMETER1 p = {};
    p.ParameterType = D3D12_ROOT_PARAMETER_TYPE_32BIT_CONSTANTS;
    p.Constants = { 0, 222, 2 };
    params.push_back(p);
    for (const auto visibility : stages)
        for (const D3D12_DESCRIPTOR_RANGE1 *range : { &srv, &cbv, &smp })
        {
            p = {};
            p.ParameterType = D3D12_ROOT_PARAMETER_TYPE_DESCRIPTOR_TABLE;
            p.DescriptorTable = { 1, range };
            p.ShaderVisibility = visibility;
            params.push_back(p);
        }
    p = {};
    p.ParameterType = D3D12_ROOT_PARAMETER_TYPE_DESCRIPTOR_TABLE;
    p.DescriptorTable = { 1, &uav };
    params.push_back(p);
    if (mod)
    {
        p = {};
        p.ParameterType = D3D12_ROOT_PARAMETER_TYPE_32BIT_CONSTANTS;
        p.Constants = { 0, 50, 16 };
        params.push_back(p);
        p = {};
        p.ParameterType = D3D12_ROOT_PARAMETER_TYPE_UAV;
        p.Descriptor = { 0, 50, D3D12_ROOT_DESCRIPTOR_FLAG_NONE };
        params.push_back(p);
    }

    D3D12_VERSIONED_ROOT_SIGNATURE_DESC rd = {};
    rd.Version = D3D_ROOT_SIGNATURE_VERSION_1_1;
    rd.Desc_1_1.NumParameters = static_cast<UINT>(params.size());
    rd.Desc_1_1.pParameters = params.data();
    rd.Desc_1_1.Flags = D3D12_ROOT_SIGNATURE_FLAG_ALLOW_INPUT_ASSEMBLER_INPUT_LAYOUT;
    ComPtr<ID3DBlob> blob, error;
    if (FAILED(D3D12SerializeVersionedRootSignature(&rd, &blob, &error)))
    {
        fprintf(stderr, "root signature: %s\n", error ? static_cast<const char *>(error->GetBufferPointer()) : "?");
        return 2;
    }
    ComPtr<ID3D12RootSignature> root;
    if (FAILED(dev->CreateRootSignature(0, blob->GetBufferPointer(), blob->GetBufferSize(), IID_PPV_ARGS(&root))))
    {
        fprintf(stderr, "CreateRootSignature failed\n");
        return 2;
    }

    D3D12_GRAPHICS_PIPELINE_STATE_DESC pd = {};
    pd.pRootSignature = root.Get();
    pd.VS = { vs.data(), vs.size() };
    pd.PS = { ps.data(), ps.size() };
    pd.BlendState.RenderTarget[0].RenderTargetWriteMask = D3D12_COLOR_WRITE_ENABLE_ALL;
    pd.SampleMask = UINT_MAX;
    pd.RasterizerState.FillMode = D3D12_FILL_MODE_SOLID;
    pd.RasterizerState.CullMode = D3D12_CULL_MODE_NONE;
    pd.PrimitiveTopologyType = D3D12_PRIMITIVE_TOPOLOGY_TYPE_TRIANGLE;
    pd.NumRenderTargets = 1;
    pd.RTVFormats[0] = DXGI_FORMAT_R16G16B16A16_FLOAT;
    pd.SampleDesc.Count = 1;
    ComPtr<ID3D12PipelineState> pso;
    const HRESULT hr = dev->CreateGraphicsPipelineState(&pd, IID_PPV_ARGS(&pso));
    printf("%s with%s our root parameters: %s (0x%08lx)%s\n", argv[2], mod ? "" : "out",
           SUCCEEDED(hr) ? "pipeline created" : "FAILED", static_cast<unsigned long>(hr),
           debug_layer ? "" : " [no debug layer]");
    print_messages(dev.Get());
    return SUCCEEDED(hr) ? 0 : 1;
}
