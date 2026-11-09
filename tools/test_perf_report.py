"""Check the performance report, add-on side (addon/src/perf_report.cpp):
builds and runs tools/lab/perf_report_test.cpp (the on/off measurement with
made-up frame times, reading the sim's UserCfg.opt).

    python tools/test_perf_report.py
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
    subprocess.run([cmake, "--build", str(build), "--config", "Release", "--target", "perf_report_test"],
                   check=True, stdout=subprocess.DEVNULL)
    sys.exit(subprocess.run([str(ROOT / "build" / "lab" / "perf_report_test.exe")]).returncode)


if __name__ == "__main__":
    main()
