"""Compare a compiled replacement against the stock shader it replaces.

Disassembles both, normalises SSA value and label numbering, and diffs the
entry function body. A baseline replacement should be a near-identical match;
interface lines (resource bindings, cbuffer size, numthreads) must match exactly.

    python tools/compare_dxil.py build/shaders/0xd33dae66.cso
"""

import argparse
import csv
import difflib
import re
import subprocess
from collections import Counter
from pathlib import Path

from disasm import find_dxc

ROOT = Path(__file__).resolve().parent.parent
EXTRACTED = ROOT / "extracted"


def dumpbin(dxc: Path, cso: Path) -> str:
    res = subprocess.run([str(dxc), "-dumpbin", str(cso)], capture_output=True, text=True)
    if res.returncode:
        raise RuntimeError(res.stderr)
    return res.stdout


def interface(asm: str) -> list:
    """Signature elements (register packing included: a graphics replacement must
    link with the game's other stages), resource binding table rows, cbuffer
    sizes and numthreads."""
    rows = [l for l in asm.splitlines()
            if re.match(r";\s+\w+\s+\d+\s+[xyzw]+\s+\d+\s+\w+\s+\w+", l)
            or re.match(r";\s+\S.*\b(cbuffer|texture|UAV|sampler)\b", l)]
    sizes = sorted(re.findall(r"ResourceProperties \{ i32 13, i32 (\d+) \}", asm))
    threads = re.findall(r"!\{i32 (\d+), i32 (\d+), i32 (\d+)\}\s*$", asm, re.M)
    return [" ".join(r.split()) for r in rows] + [f"cbuffer sizes {sizes}",
                                                   f"numthreads {threads[-1:]}"]


def body(asm: str) -> list:
    m = re.search(r"^define void @\w+\(\) \{\n(.*?)^\}", asm, re.S | re.M)
    lines = []
    names = {}

    def rename(tok: re.Match) -> str:
        key = tok.group(0)
        names.setdefault(key, f"%v{len(names)}")
        return names[key]

    for line in m.group(1).splitlines():
        line = line.split(";")[0].rstrip()
        if not line:
            continue
        if re.match(r"^\d+:", line):          # label definition
            line = "%" + line.split(":")[0] + ":"
        line = re.sub(r"%\d+", rename, line.strip())
        # a*b and a+b are exact in either order; don't report swapped operands.
        c = re.match(r"(%v\d+ = f(?:mul|add) fast float) ([^,]+), (.+)$", line)
        if c:
            line = f"{c[1]} " + ", ".join(sorted((c[2], c[3])))
        lines.append(line)
    return lines


COMMUTATIVE = re.compile(r"^(f?mul|f?add|and|or|xor)\b|dx\.op\.binary\.f32\(i32 3[56],")


def dataflow(asm: str, rounds: int = 12):
    """Order-independent fingerprint of the entry function.

    Each instruction gets a colour from its opcode/constants and its operands'
    colours, refined over the data-flow graph (Weisfeiler-Lehman). Instruction
    scheduling, value numbering and swapped commutative operands don't change
    the result; any change in what is computed does.
    """
    m = re.search(r"^define void @\w+\(\) \{\n(.*?)^\}", asm, re.S | re.M)
    labels = set(re.findall(r"^; <label>:(\d+)", m.group(1), re.M)) | {"0"}
    insts = []  # (result, shape, operands)
    for raw in m.group(1).splitlines():
        line = raw.split(";")[0].strip()
        if not line:
            continue
        res = None
        if (d := re.match(r"%(\d+) = (.*)$", line)):
            res, line = d.group(1), d.group(2)
        refs = [r for r in re.findall(r"%(\d+)", line) if r not in labels]
        shape = re.sub(r"%(\d+)", lambda t: "L" if t.group(1) in labels else "%", line)
        insts.append((res, shape, refs))

    colour = {res: hash(shape) for res, shape, _ in insts if res}
    for _ in range(rounds):
        new = {}
        for res, shape, refs in insts:
            if res is None:
                continue
            ops = [colour.get(r, 0) for r in refs]
            if COMMUTATIVE.search(shape) and len(ops) >= 2:
                ops = sorted(ops[:2]) + ops[2:]
            new[res] = hash((shape, tuple(ops)))
        colour = new

    fingerprint = Counter()
    pretty = {}
    for res, shape, refs in insts:
        ops = [colour.get(r, 0) for r in refs]
        if COMMUTATIVE.search(shape) and len(ops) >= 2:
            ops = sorted(ops[:2]) + ops[2:]
        key = colour[res] if res else hash((shape, tuple(ops)))
        fingerprint[key] += 1
        pretty.setdefault(key, shape)
    return fingerprint, pretty


def dataflow_compare(stock_asm: str, our_asm: str, show: int = 12) -> float:
    a, pa = dataflow(stock_asm)
    b, pb = dataflow(our_asm)
    common = sum((a & b).values())
    score = 2 * common / (sum(a.values()) + sum(b.values()))
    only_a, only_b = a - b, b - a
    print(f"  dataflow match {score:.3f} ({sum(only_a.values())} stock-only, {sum(only_b.values())} ours-only)")
    for name, only, pretty in (("stock-only", only_a, pa), ("ours-only", only_b, pb)):
        for key, n in list(only.items())[:show]:
            print(f"    {name}: {n}x {pretty[key][:150]}")
    return score


def stock_path(crc: str) -> Path:
    for r in csv.DictReader(open(EXTRACTED / "index.csv")):
        if r["crc32"] == crc:
            return EXTRACTED / r["path"]
    raise SystemExit(f"{crc} not in extracted/index.csv")


def compare(dxc: Path, ours: Path, crc: str, entry: str) -> bool:
    stock_asm = dumpbin(dxc, stock_path(crc))
    our_asm = dumpbin(dxc, ours)

    iface_ok = interface(stock_asm) == interface(our_asm)
    a, b = body(stock_asm), body(our_asm)
    ratio = difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()
    print(f"  {entry} vs stock {crc}: interface {'MATCH' if iface_ok else 'MISMATCH'}, "
          f"body {len(b)}/{len(a)} lines, similarity {ratio:.3f}")
    if not iface_ok:
        for line in difflib.unified_diff(interface(stock_asm), interface(our_asm),
                                         "stock", "ours", lineterm="", n=0):
            print("    " + line)
    if ratio < 1.0:
        diff_path = ours.with_suffix(".diff")
        diff_path.write_text("\n".join(difflib.unified_diff(
            a, b, "stock", "ours", lineterm="")))
        print(f"  body diff -> {diff_path.relative_to(ROOT) if diff_path.is_relative_to(ROOT) else diff_path}")
        dataflow_compare(stock_asm, our_asm)
    return iface_ok


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cso", type=Path, help="our compiled shader, named <crc32>.cso")
    args = ap.parse_args()
    compare(find_dxc(), args.cso, args.cso.stem, args.cso.stem)


if __name__ == "__main__":
    main()
