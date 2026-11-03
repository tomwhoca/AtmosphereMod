// The preset import's file dialog (file_dialog.h).

#include "file_dialog.h"

#include <windows.h>
#include <shlobj.h>
#include <shobjidl.h>
#include <wrl/client.h>

#include <mutex>
#include <thread>
#include <utility>

namespace file_dialog
{
namespace
{
std::mutex g_mutex;  // guards the fields below
bool g_open = false;
std::optional<std::filesystem::path> g_chosen;

// The dialog's own memory of the folder last used, apart from the sim's dialogs'.
constexpr GUID kClient = {0x6b2f3c1e, 0x8d4a, 0x4f5e, {0x9a, 0x21, 0x3c, 0x7e, 0x55, 0x10, 0xa4, 0x2d}};

std::filesystem::path choose(HWND owner)
{
    using Microsoft::WRL::ComPtr;
    ComPtr<IFileOpenDialog> dialog;
    if (FAILED(CoCreateInstance(__uuidof(FileOpenDialog), nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&dialog))))
        return {};
    const COMDLG_FILTERSPEC types[] = {{L"AtmosphereMod presets (*.ini)", L"*.ini"}, {L"All files", L"*.*"}};
    dialog->SetFileTypes(2, types);
    dialog->SetTitle(L"Import an AtmosphereMod preset");
    dialog->SetClientGuid(kClient);
    FILEOPENDIALOGOPTIONS options = 0;
    dialog->GetOptions(&options);
    dialog->SetOptions(options | FOS_FORCEFILESYSTEM | FOS_FILEMUSTEXIST);
    // (Downloads, where a shared preset lands, until the dialog remembers a folder)
    ComPtr<IShellItem> downloads;
    if (SUCCEEDED(SHGetKnownFolderItem(FOLDERID_Downloads, KF_FLAG_DEFAULT, nullptr, IID_PPV_ARGS(&downloads))))
        dialog->SetDefaultFolder(downloads.Get());
    ComPtr<IShellItem> item;
    PWSTR path = nullptr;
    if (FAILED(dialog->Show(owner)) || FAILED(dialog->GetResult(&item)) ||
        FAILED(item->GetDisplayName(SIGDN_FILESYSPATH, &path)))
        return {};
    std::filesystem::path chosen(path);
    CoTaskMemFree(path);
    return chosen;
}

void run(HWND owner)
{
    std::filesystem::path chosen;
    if (SUCCEEDED(CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE)))
    {
        chosen = choose(owner);
        CoUninitialize();
    }
    const std::lock_guard lock(g_mutex);
    g_chosen = std::move(chosen);
    g_open = false;
}
} // namespace

bool open(void *owner_window)
{
    {
        const std::lock_guard lock(g_mutex);
        if (g_open)
            return false;
        g_open = true;
        g_chosen.reset();
    }
    // (detached: nothing waits for the user; a dialog still open as the sim exits ends with it)
    std::thread(run, static_cast<HWND>(owner_window)).detach();
    return true;
}

std::optional<std::filesystem::path> take()
{
    const std::lock_guard lock(g_mutex);
    return std::exchange(g_chosen, std::nullopt);
}
} // namespace file_dialog
