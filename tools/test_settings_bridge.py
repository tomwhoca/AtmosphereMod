"""Check the EFB app's settings, add-on side (addon/src/settings_bridge.cpp):
builds and runs tools/lab/settings_bridge_test.cpp (message parsing, when the
state is sent, saving into tunables.ini in place).

    python tools/test_settings_bridge.py
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
    subprocess.run([cmake, "--build", str(build), "--config", "Release", "--target", "settings_bridge_test"],
                   check=True, stdout=subprocess.DEVNULL)
    sys.exit(subprocess.run([str(ROOT / "build" / "lab" / "settings_bridge_test.exe")]).returncode)


if __name__ == "__main__":
    main()
