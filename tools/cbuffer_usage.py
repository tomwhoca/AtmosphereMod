"""Report which registers/components of a cbuffer each shader reads.

    python tools/cbuffer_usage.py --size 208 ch_transmittance_lut ch_sky_view ...

Parses dxc -dumpbin output in extracted/disasm/<entry>/*.asm. The cbuffer is
identified by its byte size (from annotateHandle), since reflection names are
stripped. Output is one row per c<reg>.<comp> with the entries reading it.
"""

import argparse
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ANNOTATE = re.compile(r"(%\d+) = call %dx\.types\.Handle @dx\.op\.annotateHandle\("
                      r"i32 216, %dx\.types\.Handle %\d+, %dx\.types\.ResourceProperties "
                      r"\{ i32 13, i32 (\d+) \}\)")
LOAD = re.compile(r"(%\d+) = call %dx\.types\.CBufRet\.f32 @dx\.op\.cbufferLoadLegacy\.f32"
                  r"\(i32 59, %dx\.types\.Handle (%\d+), i32 (\d+)\)")
EXTRACT = re.compile(r"= extractvalue %dx\.types\.CBufRet\.f32 (%\d+), (\d)")


def usage(asm: str, size: int) -> set:
    handles = {m[1] for m in ANNOTATE.finditer(asm) if int(m[2]) == size}
    loads = {m[1]: int(m[3]) for m in LOAD.finditer(asm) if m[2] in handles}
    return {(loads[m[1]], int(m[2])) for m in EXTRACT.finditer(asm) if m[1] in loads}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("entries", nargs="+")
    ap.add_argument("--size", type=int, required=True, help="cbuffer size in bytes")
    ap.add_argument("--disasm", type=Path, default=ROOT / "extracted" / "disasm")
    args = ap.parse_args()

    readers = defaultdict(set)
    for entry in args.entries:
        for f in sorted((args.disasm / entry).glob("*.asm")):
            for reg_comp in usage(f.read_text(), args.size):
                readers[reg_comp].add(entry)

    for (reg, comp) in sorted(readers):
        print(f"c{reg}.{'xyzw'[comp]}  {', '.join(sorted(readers[reg, comp]))}")


if __name__ == "__main__":
    main()
