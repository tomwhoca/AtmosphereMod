"""Check the add-on's hotkeys (addon/src/hotkeys.cpp): builds and runs
tools/lab/hotkeys_test.cpp (keys as text, which keys can be hotkeys, the file,
learning a key, Glass Cockpit DLSS Fix's keys from its log).

    python tools/test_hotkeys.py
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import build_addon  # noqa: E402


def main():
    cmake = str(build_addon.find_cmake())
    build = ROOT / "build" / "lab-build"
    subprocess.run([cmake, "-S", str(ROOT / "tools" / "lab"), "-B", str(build)], check=True, stdout=subprocess.DEVNULL)
    subprocess.run([cmake, "--build", str(build), "--config", "Release", "--target", "hotkeys_test"],
                   check=True, stdout=subprocess.DEVNULL)
    sys.exit(subprocess.run([str(ROOT / "build" / "lab" / "hotkeys_test.exe")]).returncode)


if __name__ == "__main__":
    main()
