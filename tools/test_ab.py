"""Check the A/B test's request and statistics (addon/src/ab_test.cpp): builds and runs
tools/lab/ab_test_test.cpp (the request file; the medians and B - A under drift,
noise and outlier frames).

    python tools/test_ab.py
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
    subprocess.run([cmake, "--build", str(build), "--config", "Release", "--target", "ab_test_test"],
                   check=True, stdout=subprocess.DEVNULL)
    sys.exit(subprocess.run([str(ROOT / "build" / "lab" / "ab_test_test.exe")]).returncode)


if __name__ == "__main__":
    main()
