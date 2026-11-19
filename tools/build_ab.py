"""Build a set of cloud shaders for the A/B test (tools/ab.py, addon/src/ab_test.h): every
ch_volumetric_clouds_render variant, patched by a patch_dxil.py, as build/ab/<name>/<crc>.cso. The lean set, as
build/shaders has it (what runs while the rare features are off), unless --full.

    python tools/build_ab.py new                   # the working tree's tools/patch_dxil.py
    python tools/build_ab.py old --rev HEAD        # tools/patch_dxil.py as committed (any git revision)
    python tools/build_ab.py apc --patch-dir DIR   # the patch_dxil.py in DIR
    python tools/build_ab.py stock --stock         # the sim's own cloud shaders, unpatched
    python tools/build_ab.py full --full           # the full set (build/shaders/full: C36, the tilt view)
    python tools/build_ab.py no-haze --leave-out haze   # the cost profile: one feature (or several, comma
                                                        # separated) left out (patch_dxil.CLOUD_FEATURES)

The other tools (the assembler, tunables) come from the working tree either way.
"""
import argparse
import csv
import importlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("name", help="the set: build/ab/<name> (letters, digits, - and _)")
    source = ap.add_mutually_exclusive_group()
    source.add_argument("--rev", help="tools/patch_dxil.py at this git revision")
    source.add_argument("--patch-dir", type=Path, help="a folder with the patch_dxil.py to use")
    source.add_argument("--stock", action="store_true", help="the sim's own cloud shaders, unpatched")
    ap.add_argument("--full", action="store_true", help="the full set (patch_dxil.LEAN_LEAVE_OUT kept)")
    ap.add_argument("--leave-out", default="", help="cost profile: features left out, comma separated "
                    "(patch_dxil.CLOUD_FEATURES)")
    args = ap.parse_args()
    if not args.name.replace("-", "").replace("_", "").isalnum():
        raise SystemExit("the set's name: letters, digits, - and _ only")

    with tempfile.TemporaryDirectory() as tmp:
        mod_dir = ROOT / "tools"
        if args.rev:
            text = subprocess.run(["git", "show", f"{args.rev}:tools/patch_dxil.py"], cwd=ROOT, check=True,
                                  capture_output=True).stdout
            mod_dir = Path(tmp)
            (mod_dir / "patch_dxil.py").write_bytes(text)
        elif args.patch_dir:
            mod_dir = args.patch_dir.resolve()
            if not (mod_dir / "patch_dxil.py").exists():
                raise SystemExit(f"no patch_dxil.py in {mod_dir}")
        sys.path.insert(0, str(ROOT / "tools"))
        sys.path.insert(0, str(mod_dir))
        P = importlib.import_module("patch_dxil")
        print("the sim's own shaders" if args.stock else f"patch_dxil.py from {args.rev or P.__file__}")
        leave_out = {f.strip() for f in args.leave_out.split(",") if f.strip()}
        if leave_out:
            if args.stock:
                raise SystemExit("--leave-out with --stock: nothing to leave out")
            unknown = leave_out - set(getattr(P, "CLOUD_FEATURES", ()))
            if unknown:
                raise SystemExit(f"unknown features {sorted(unknown)}; patch_dxil has {getattr(P, 'CLOUD_FEATURES', ())}")
            P.PROFILE_LEAVE_OUT = leave_out
            print("left out:", ", ".join(sorted(leave_out)))

        out = ROOT / "build" / "ab" / args.name
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)
        dxc = P.find_dxc()
        asm = P.Assembler(dxc.parent)
        with open(ROOT / "extracted" / "index.csv", encoding="utf-8") as f:
            rows = [r for r in csv.DictReader(f) if r["entry"] == "ch_volumetric_clouds_render"]
        for row in rows:
            crc = Path(row["path"]).name.split(".")[0]
            if args.stock:
                shutil.copyfile(ROOT / "extracted" / row["path"], out / f"{crc}.cso")
                continue
            source = P.disassemble(ROOT / "extracted" / row["path"], dxc)
            # (a patch_dxil.py from before the lean set has no such option: its one set)
            lean = {} if args.full or not hasattr(P, "LEAN_LEAVE_OUT") else {"lean": True}
            patched, _ = P.patch_clouds(source, **lean)
            (out / f"{crc}.cso").write_bytes(asm.assemble(patched))
        print(len(rows), "shaders in", out)


if __name__ == "__main__":
    main()
