// Do a GPU's graphics and compute queues share a timestamp clock? Runs one
// timestamp pair on each, then compares the raw ticks with what each queue's
// clock calibration (GetClockCalibration) says. The performance report merges
// the sim's command lists across queues (perf_report.h busy_ms), so it converts
// each queue's ticks to the CPU's clock first; this shows why that's needed, or
// that it isn't on this GPU.

#include <windows.h>
#include <d3d12.h>

#include <cstdio>
#include <cstdint>

int main()
{
    ID3D12Device *device = nullptr;
    if (FAILED(D3D12CreateDevice(nullptr, D3D_FEATURE_LEVEL_12_0, IID_PPV_ARGS(&device))))
        return std::printf("no D3D12 device\n"), 1;
    const D3D12_COMMAND_LIST_TYPE types[2] = {D3D12_COMMAND_LIST_TYPE_DIRECT, D3D12_COMMAND_LIST_TYPE_COMPUTE};
    const char *names[2] = {"graphics", "compute"};
    ID3D12QueryHeap *heap = nullptr;
    const D3D12_QUERY_HEAP_DESC hd = {D3D12_QUERY_HEAP_TYPE_TIMESTAMP, 4, 0};
    device->CreateQueryHeap(&hd, IID_PPV_ARGS(&heap));
    D3D12_HEAP_PROPERTIES hp = {D3D12_HEAP_TYPE_READBACK};
    D3D12_RESOURCE_DESC rd = {D3D12_RESOURCE_DIMENSION_BUFFER, 0, 32, 1, 1, 1, DXGI_FORMAT_UNKNOWN, {1, 0},
                              D3D12_TEXTURE_LAYOUT_ROW_MAJOR, D3D12_RESOURCE_FLAG_NONE};
    ID3D12Resource *readback = nullptr;
    device->CreateCommittedResource(&hp, D3D12_HEAP_FLAG_NONE, &rd, D3D12_RESOURCE_STATE_COPY_DEST, nullptr,
                                    IID_PPV_ARGS(&readback));
    LARGE_INTEGER qpf;
    QueryPerformanceFrequency(&qpf);
    ID3D12CommandQueue *queues[2] = {};
    UINT64 freq[2] = {}, cal_gpu[2] = {}, cal_cpu[2] = {};
    for (int q = 0; q < 2; ++q)
    {
        const D3D12_COMMAND_QUEUE_DESC qd = {types[q]};
        device->CreateCommandQueue(&qd, IID_PPV_ARGS(&queues[q]));
        ID3D12CommandAllocator *alloc = nullptr;
        ID3D12GraphicsCommandList *list = nullptr;
        device->CreateCommandAllocator(types[q], IID_PPV_ARGS(&alloc));
        device->CreateCommandList(0, types[q], alloc, nullptr, IID_PPV_ARGS(&list));
        list->EndQuery(heap, D3D12_QUERY_TYPE_TIMESTAMP, q * 2);
        list->EndQuery(heap, D3D12_QUERY_TYPE_TIMESTAMP, q * 2 + 1);
        list->ResolveQueryData(heap, D3D12_QUERY_TYPE_TIMESTAMP, q * 2, 2, readback, q * 16);
        list->Close();
        ID3D12CommandList *lists[] = {list};
        queues[q]->ExecuteCommandLists(1, lists);
        ID3D12Fence *fence = nullptr;
        device->CreateFence(0, D3D12_FENCE_FLAG_NONE, IID_PPV_ARGS(&fence));
        HANDLE done = CreateEventW(nullptr, FALSE, FALSE, nullptr);
        queues[q]->Signal(fence, 1);
        fence->SetEventOnCompletion(1, done);
        WaitForSingleObject(done, 5000);
        queues[q]->GetTimestampFrequency(&freq[q]);
        queues[q]->GetClockCalibration(&cal_gpu[q], &cal_cpu[q]);
    }
    UINT64 *ticks = nullptr;
    const D3D12_RANGE range = {0, 32};
    readback->Map(0, &range, reinterpret_cast<void **>(&ticks));
    for (int q = 0; q < 2; ++q)
    {
        const double on_cpu_ms =
            double(cal_cpu[q]) * 1000.0 / double(qpf.QuadPart) +
            double(int64_t(ticks[q * 2] - cal_gpu[q])) * 1000.0 / double(freq[q]);
        std::printf("%-8s queue: %llu Hz, timestamp %llu; calibration gpu %llu cpu %llu; the timestamp on the CPU's "
                    "clock: %.3f ms\n", names[q], (unsigned long long)freq[q], (unsigned long long)ticks[q * 2],
                    (unsigned long long)cal_gpu[q], (unsigned long long)cal_cpu[q], on_cpu_ms);
    }
    const double raw_ms = (double(ticks[2]) / double(freq[1]) - double(ticks[0]) / double(freq[0])) * 1000.0;
    std::printf("raw ticks: the compute timestamp is %.3f ms after the graphics one (the two ran one after the other, "
                "within milliseconds)\n", raw_ms);
    return 0;
}
