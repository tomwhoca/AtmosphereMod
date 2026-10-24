"""Compile replacement shaders listed in shaders/manifest.json.

Each target is written to build/shaders/<crc32>.cso, named after the stock
shader it replaces; the add-on loads replacements by that name. The private
shaders/replicas.json (the developer's HLSL replicas of patched passes, for
atmolab) goes to build/replicas instead. Also writes
build/shader_names.tsv (crc32 -> entry) so the add-on can name what it logs,
and copies shaders/tunables.ini to build/ for the add-on to read (made from
shaders/tunables.defaults.ini, the shipping defaults, if missing). Then runs
tools/patch_dxil.py for the stock shaders we patch instead of replace.

    python tools/build_shaders.py            # build all
    python tools/build_shaders.py --compare  # also diff the hook-free variant
                                             # against the stock DXIL
"""

import argparse
import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path

from disasm import find_dxc

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "shaders" / "src"
OUT = ROOT / "build" / "shaders"
COMPARE_OUT = ROOT / "build" / "compare"
REPLICAS = ROOT / "build" / "replicas"


def compile_target(dxc: Path, shader: dict, target: dict, out: Path, extra_defines=()) -> Path:
    cmd = [str(dxc), "-nologo", "-T", shader["profile"], "-E", shader["entry"],
           "-O3", "-Qstrip_debug", "-Qstrip_reflect", *shader.get("options", []), "-I", str(SRC),
           "-Fo", str(out), str(SRC / shader["source"])]
    for d in [*target.get("defines", []), *extra_defines]:
        cmd += ["-D", d]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode:
        sys.exit(f"{shader['source']} -> {out.name} failed:\n{res.stderr}")
    if res.stderr.strip():
        print(res.stderr.strip())
    return out


def write_names() -> None:
    rows = csv.DictReader(open(ROOT / "extracted" / "index.csv"))
    lines = sorted({f"{r['crc32']}\t{r['entry']}" for r in rows})
    (ROOT / "build" / "shader_names.tsv").write_text("\n".join(lines) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--compare", action="store_true",
                    help="diff each hook-free variant against the stock shader's DXIL")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    write_names()
    tunables = ROOT / "shaders" / "tunables.ini"  # (your own; private)
    if not tunables.exists():
        shutil.copy2(ROOT / "shaders" / "tunables.defaults.ini", tunables)
    shutil.copy2(tunables, ROOT / "build" / "tunables.ini")
    dxc = find_dxc()
    manifest = json.loads((ROOT / "shaders" / "manifest.json").read_text())
    jobs = [(shader, OUT) for shader in manifest["shaders"]]
    # the developer's HLSL replicas of patched passes, for atmolab (ATMOLAB_OURS=build/replicas)
    replicas = ROOT / "shaders" / "replicas.json"
    if replicas.exists():
        REPLICAS.mkdir(parents=True, exist_ok=True)
        jobs += [(shader, REPLICAS) for shader in json.loads(replicas.read_text())["shaders"]]
    for shader, out_dir in jobs:
        if not (SRC / shader["source"]).exists():  # (private sources aren't in public copies)
            print(f"skipped {shader['source']}: not in this copy, the stock shader stays")
            continue
        for target in shader["targets"]:
            out = compile_target(dxc, shader, target, out_dir / f"{target['crc32']}.cso")
            print(f"built {out.relative_to(ROOT)}  <- {shader['source']}")
            if args.compare:
                from compare_dxil import compare
                COMPARE_OUT.mkdir(parents=True, exist_ok=True)
                plain = compile_target(dxc, shader, target, COMPARE_OUT / f"{target['crc32']}.cso",
                                       ["MOD_HOOKS=0"])
                compare(dxc, plain, target["crc32"], shader["entry"])

    # Stock shaders we patch rather than replace (manifest "patches").
    from patch_dxil import patch_all
    if failures := patch_all():
        sys.exit(f"{failures} patch target(s) failed")


if __name__ == "__main__":
    main()
