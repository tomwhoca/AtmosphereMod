// A Windows file dialog for the EFB app's preset import (settings_bridge.h
// presets::import): the app's pages can't open files, the add-on in the sim's
// process can. The dialog runs on its own thread (the present thread doesn't
// wait for the user), owned by the game's window: it stays in front of the
// sim, which takes no input while it's open and has the focus back after.

#pragma once

#include <filesystem>
#include <optional>

namespace file_dialog
{
// Opens the dialog to choose a preset's file, unless it's open already (false
// then). Any thread.
bool open(void *owner_window);

// Once the dialog has closed: the file chosen, "" if none was (cancelled).
// Nothing while it's open, or after it was taken.
std::optional<std::filesystem::path> take();
} // namespace file_dialog
