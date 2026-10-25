"""Check the add-on's METAR parsing for cloud layers without a base
(FEW///CB): builds and runs tools/lab/metar_parse_test.cpp
(addon/src/haze_mapping.h, cloud_anchor.h).

    python tools/test_metar_parse.py
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
    subprocess.run([cmake, "--build", str(build), "--config", "Release", "--target", "metar_parse_test"],
                   check=True, stdout=subprocess.DEVNULL)
    sys.exit(subprocess.run([str(ROOT / "build" / "lab" / "metar_parse_test.exe")]).returncode)


if __name__ == "__main__":
    main()
