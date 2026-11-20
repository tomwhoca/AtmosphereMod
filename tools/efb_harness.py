"""The browser harness for the redesigned EFB app: serves efb/harness on http://127.0.0.1:8078/ with live reload
(the UI module, efb/PackageSources/AtmoWeather/src/ui, in a tablet of each size the sim gives the app, with a
fake add-on). Runs until stopped (Ctrl+C).

    python tools/efb_harness.py [--port 8078]

It runs modern Chrome's engine, not the sim's (CoherentGT 2.0): check there with tools/efb_reload.py.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NODE = ROOT / "tools" / "node" / "node.exe"
HARNESS = ROOT / "efb" / "harness"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8078)
    args = ap.parse_args()
    if not NODE.exists():
        raise SystemExit("tools/node missing (see tools/build_efb.py)")
    if not (ROOT / "efb" / "PackageSources" / "AtmoWeather" / "node_modules" / "esbuild").exists():
        raise SystemExit("the app's node_modules are missing: run tools/build_efb.py once")
    env = {**os.environ, "ATMO_HARNESS_PORT": str(args.port)}
    sys.exit(subprocess.run([str(NODE), str(HARNESS / "dev.js")], env=env).returncode)


if __name__ == "__main__":
    main()
