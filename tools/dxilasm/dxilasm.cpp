// dxilasm: assemble DXIL from LLVM IR text (as printed by dxc -dumpbin) into
// a validated, signed shader container.
//
//   dxilasm <dxc bin dir> <in.ll> <out.cso>
//
// Uses only the dxcompiler.dll and dxil.dll that ship with the Windows SDK:
// the optimizer parses the IR text (it accepts text as well as bitcode) and,
// with no passes, writes it back as bitcode; the assembler wraps the bitcode
// in a container (signatures, PSV0 and feature info are rebuilt from the
// module metadata); the validator checks it and signs it in place.

#include <windows.h>
#include <objidl.h>  // IUnknown, IStream (WIN32_LEAN_AND_MEAN leaves them out)
#include <dxcapi.h>
#include <wrl/client.h>

#include <cstdio>
#include <fstream>
#include <string>
#include <vector>

using Microsoft::WRL::ComPtr;

namespace
{
std::vector<char> read_file(const wchar_t *path)
{
    std::ifstream file(path, std::ios::binary);
    return { std::istreambuf_iterator<char>(file), std::istreambuf_iterator<char>() };
}

void print_errors(IDxcOperationResult *result, const char *what)
{
    ComPtr<IDxcBlobEncoding> errors;
    if (SUCCEEDED(result->GetErrorBuffer(&errors)) && errors && errors->GetBufferSize() != 0)
        fprintf(stderr, "%s:\n%.*s\n", what, static_cast<int>(errors->GetBufferSize()),
                static_cast<const char *>(errors->GetBufferPointer()));
}

bool succeeded(IDxcOperationResult *result, const char *what)
{
    HRESULT status = E_FAIL;
    result->GetStatus(&status);
    if (FAILED(status))
    {
        fprintf(stderr, "%s failed (0x%08lx)\n", what, static_cast<unsigned long>(status));
        print_errors(result, what);
        return false;
    }
    return true;
}

template <typename T>
ComPtr<T> create(HMODULE dll, REFCLSID clsid)
{
    auto create_instance = reinterpret_cast<DxcCreateInstanceProc>(GetProcAddress(dll, "DxcCreateInstance"));
    ComPtr<T> result;
    if (create_instance == nullptr || FAILED(create_instance(clsid, IID_PPV_ARGS(&result))))
        return nullptr;
    return result;
}
} // namespace

int wmain(int argc, wchar_t **argv)
{
    if (argc != 4)
    {
        fprintf(stderr, "usage: dxilasm <dxc bin dir> <in.ll> <out.cso>\n");
        return 2;
    }
    const std::wstring bin = argv[1];
    // dxcompiler.dll looks for dxil.dll next to itself when validating.
    SetDllDirectoryW(bin.c_str());
    HMODULE compiler = LoadLibraryW((bin + L"\\dxcompiler.dll").c_str());
    HMODULE validator_dll = LoadLibraryW((bin + L"\\dxil.dll").c_str());
    if (compiler == nullptr || validator_dll == nullptr)
    {
        fprintf(stderr, "cannot load dxcompiler.dll / dxil.dll from %ls\n", bin.c_str());
        return 1;
    }

    auto utils = create<IDxcUtils>(compiler, CLSID_DxcUtils);
    auto optimizer = create<IDxcOptimizer>(compiler, CLSID_DxcOptimizer);
    auto assembler = create<IDxcAssembler>(compiler, CLSID_DxcAssembler);
    auto validator = create<IDxcValidator>(validator_dll, CLSID_DxcValidator);
    if (!utils || !optimizer || !assembler || !validator)
    {
        fprintf(stderr, "cannot create the DXC interfaces\n");
        return 1;
    }

    std::vector<char> text = read_file(argv[2]);
    if (text.empty())
    {
        fprintf(stderr, "cannot read %ls\n", argv[2]);
        return 1;
    }
    text.push_back('\0');  // the IR parser wants a terminated buffer
    ComPtr<IDxcBlobEncoding> source;
    utils->CreateBlob(text.data(), static_cast<UINT32>(text.size() - 1), DXC_CP_UTF8, &source);

    // Text -> bitcode (no passes).
    ComPtr<IDxcBlob> bitcode;
    ComPtr<IDxcBlobEncoding> unused_text;
    HRESULT hr = optimizer->RunOptimizer(source.Get(), nullptr, 0, &bitcode, &unused_text);
    if (FAILED(hr) || !bitcode)
    {
        fprintf(stderr, "parsing the IR failed (0x%08lx)\n", static_cast<unsigned long>(hr));
        return 1;
    }

    // Bitcode -> container.
    ComPtr<IDxcOperationResult> assembled;
    if (FAILED(assembler->AssembleToContainer(bitcode.Get(), &assembled)) || !succeeded(assembled.Get(), "assembly"))
        return 1;
    ComPtr<IDxcBlob> container;
    assembled->GetResult(&container);

    // Validate and sign. The validator writes the hash into the blob, so give
    // it a private, writable copy.
    ComPtr<IDxcBlobEncoding> signed_blob;
    utils->CreateBlob(container->GetBufferPointer(), static_cast<UINT32>(container->GetBufferSize()), DXC_CP_ACP,
                      &signed_blob);
    ComPtr<IDxcOperationResult> validated;
    if (FAILED(validator->Validate(signed_blob.Get(), DxcValidatorFlags_InPlaceEdit, &validated)) ||
        !succeeded(validated.Get(), "validation"))
        return 1;

    std::ofstream out(argv[3], std::ios::binary);
    out.write(static_cast<const char *>(signed_blob->GetBufferPointer()),
              static_cast<std::streamsize>(signed_blob->GetBufferSize()));
    if (!out)
    {
        fprintf(stderr, "cannot write %ls\n", argv[3]);
        return 1;
    }
    printf("%ls: %zu bytes, validated and signed\n", argv[3], static_cast<size_t>(signed_blob->GetBufferSize()));
    return 0;
}
