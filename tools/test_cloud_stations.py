"""Check the METAR anchoring's add-on side (addon/src/cloud_stations.h,
cloud_anchor.h): builds tools/lab/cloud_stations_test.cpp, runs its station-set
scenario (fades, crossfades, waiting for positions, drops, eviction), and
compares the rows it builds for test_cloud_gen_capture.py's three stations with
that test's reference (anchor_rows), which the generator is checked against.

    python tools/test_cloud_stations.py
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import build_addon  # noqa: E402
import test_cloud_gen_capture as G  # noqa: E402


def main():
    cmake = str(build_addon.find_cmake())
    build = ROOT / "build" / "lab-build"
    subprocess.run([cmake, "-S", str(ROOT / "tools" / "lab"), "-B", str(build)], check=True, stdout=subprocess.DEVNULL)
    subprocess.run([cmake, "--build", str(build), "--config", "Release", "--target", "cloud_stations_test"],
                   check=True, stdout=subprocess.DEVNULL)
    exe = ROOT / "build" / "lab" / "cloud_stations_test.exe"
    bad = 0
    out = subprocess.run([str(exe), "--rows"], check=True, capture_output=True, text=True).stdout
    got = {}
    for line in out.split("\n"):
        if line.strip():
            i, k, *row = line.split()
            got[int(i), int(k)] = [float(x) for x in row]
    for i, (_, _, layers, ceiling) in enumerate(G.STATIONS):
        for k, want in enumerate(G.anchor_rows(layers, ceiling)):
            if max(abs(a - b) for a, b in zip(got[i, k], want)) > 1e-6:
                bad += 1
                print(f"FAIL: station {i} slice {k}: add-on row {got[i, k]}, reference {want}")
    print(f"rows: {len(got)} compared with the generator test's reference")
    run = subprocess.run([str(exe)], capture_output=True, text=True)
    print("\n".join(line for line in run.stdout.split("\n") if not line.startswith("  log:")))
    bad += run.returncode != 0
    print("all checks passed" if bad == 0 else f"{bad} check(s) failed")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
