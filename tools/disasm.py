"""Disassemble extracted shaders whose entry point matches a regex.

    python tools/disasm.py "transmittance|sky_view|atmosphereLight"

Writes extracted/disasm/<entry>/<crc32>.<stage>_<sm>.asm using dxc -dumpbin
from the newest installed Windows SDK (override with --dxc).
"""

import argparse
import csv
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def find_dxc() -> Path:
    kits = Path(r"C:\Program Files (x86)\Windows Kits\10\bin")
    found = sorted(kits.glob("10.*/x64/dxc.exe"))
    if not found:
        raise SystemExit("dxc.exe not found; pass --dxc")
    return found[-1]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pattern", help="regex matched against the entry name")
    ap.add_argument("--extracted", type=Path, default=ROOT / "extracted")
    ap.add_argument("--dxc", type=Path, default=None)
    args = ap.parse_args()

    dxc = args.dxc or find_dxc()
    pat = re.compile(args.pattern)
    rows = [r for r in csv.DictReader(open(args.extracted / "index.csv"))
            if pat.search(r["entry"])]
    for r in rows:
        src = args.extracted / r["path"]
        dst = args.extracted / "disasm" / r["entry"] / (src.stem + ".asm")
        dst.parent.mkdir(parents=True, exist_ok=True)
        res = subprocess.run([str(dxc), "-dumpbin", str(src), "-Fc", str(dst)],
                             capture_output=True, text=True)
        if res.returncode:
            print(f"FAILED {src.name}: {res.stderr.strip()[:200]}")
    print(f"{len(rows)} shaders disassembled with {dxc}")


if __name__ == "__main__":
    main()
