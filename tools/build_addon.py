"""Build the ReShade add-on with the CMake bundled in Visual Studio.

    python tools/build_addon.py [--config Debug]

Output: build/AtmosphereMod.addon64
"""

import argparse
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VSWHERE = Path(r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe")


def find_cmake() -> Path:
    if found := shutil.which("cmake"):
        return Path(found)
    vs = subprocess.run([str(VSWHERE), "-latest", "-property", "installationPath"],
                        capture_output=True, text=True, check=True).stdout.strip()
    cmake = Path(vs) / "Common7/IDE/CommonExtensions/Microsoft/CMake/CMake/bin/cmake.exe"
    if not cmake.exists():
        raise SystemExit("cmake not found; install the VS 'C++ CMake tools' component")
    return cmake


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default="Release", choices=["Release", "Debug", "RelWithDebInfo"])
    args = ap.parse_args()

    cmake = find_cmake()
    build_dir = ROOT / "build" / "addon"
    subprocess.run([str(cmake), "-S", str(ROOT / "addon"), "-B", str(build_dir),
                    "-G", "Visual Studio 17 2022", "-A", "x64"], check=True)
    subprocess.run([str(cmake), "--build", str(build_dir), "--config", args.config], check=True)

    out = ROOT / "build" / "AtmosphereMod.addon64"
    shutil.copy2(build_dir / args.config / "AtmosphereMod.addon64", out)
    print(f"-> {out}")


if __name__ == "__main__":
    main()
