"""What a shader does per step of its loops: the loops in its LLVM IR (dxc -dumpbin text), each with its
instructions counted by kind, and in a patched shader, the mod's own (named %mod.<feature>...) by feature.

    python tools/loop_cost.py stock.ll patched.ll    # (patch_dxil.py writes both for a variant: see below)

A loop is a natural loop: a back edge's header and the blocks that reach the back edge without passing it,
so a nested loop shows inside the outer one too. Static counts: a block behind a branch counts even when the branch skips it.
Texture reads (sample, sampleLevel, textureLoad) and buffer reads cost the most per instruction.
"""

import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

LABEL = re.compile(r"^(?:; <label>:(\d+)|([\w.$-]+):)")
BR_TARGETS = re.compile(r"label %([\w.$-]+)")
DXOP = re.compile(r"@dx\.op\.(\w+)(?:\.\w+)?\(i32 (\d+)")
KINDS = {  # dx.op opcode -> kind
    "60": "sample", "61": "sample", "62": "sample", "63": "sample", "64": "sample", "65": "sample",
    "66": "textureLoad", "68": "bufferLoad", "139": "rawBufferLoad", "140": "rawBufferStore",
    "59": "cbufferLoad", "21": "exp", "23": "log", "24": "sqrt", "13": "trig", "12": "trig", "14": "trig",
    "15": "trig", "16": "trig", "17": "trig",
}
FLOAT = re.compile(r"= (fadd|fsub|fmul|fdiv|frem|fcmp|fneg)\b")
MOD = re.compile(r"^\s*%mod\.([a-z]+)")


def blocks(text: str) -> list[tuple[str, list[str]]]:
    """The function's blocks in order: (label, lines)."""
    out, label, lines = [], "entry", []
    in_func = False
    for line in text.splitlines():
        if line.startswith("define "):
            in_func, label, lines = True, "entry", []
            continue
        if not in_func:
            continue
        if line.startswith("}"):
            out.append((label, lines))
            in_func = False
            continue
        m = LABEL.match(line)
        if m:
            out.append((label, lines))
            label, lines = m[1] or m[2], []
            continue
        if line.strip() and not line.strip().startswith(";"):
            lines.append(line)
    return out


def loops(bl: list[tuple[str, list[str]]]) -> list[tuple[int, list[int]]]:
    """Natural loops: for each back edge (a branch to a block earlier in the text, the header), the header and
    every block that reaches the branch's block without passing through the header. Outermost first."""
    where = {label: i for i, (label, _) in enumerate(bl)}
    succ = {i: [where[t] for line in lines if line.strip().startswith(("br ", "switch "))
                for t in BR_TARGETS.findall(line) if t in where] for i, (_, lines) in enumerate(bl)}
    pred = {i: [] for i in range(len(bl))}
    for i, ss in succ.items():
        for j in ss:
            pred[j].append(i)
    bodies = {}
    for latch, ss in succ.items():
        for header in ss:
            if header > latch:
                continue
            body, stack = {header, latch}, [latch]
            while stack:
                b = stack.pop()
                if b == header:
                    continue
                for q in pred[b]:
                    if q not in body:
                        body.add(q)
                        stack.append(q)
            bodies.setdefault(header, set()).update(body)
    return sorted(((h, sorted(b)) for h, b in bodies.items()), key=lambda hb: -len(hb[1]))


def classify(lines: list[str]) -> tuple[Counter, dict]:
    kinds, per_feature = Counter(), defaultdict(Counter)
    for line in lines:
        kind = None
        if m := DXOP.search(line):
            kind = KINDS.get(m[2], "dx.op " + m[1])
        elif FLOAT.search(line):
            kind = "float math"
        elif re.search(r"= (add|sub|mul|and|or|xor|shl|lshr|ashr|icmp|select|zext|sext|trunc|bitcast|"
                       r"fptoui|fptosi|uitofp|sitofp|extractvalue|insertelement|extractelement|phi)\b", line):
            kind = "other"
        else:
            kind = "other"
        kinds[kind] += 1
        if m := MOD.match(line):
            per_feature[m[1]][kind] += 1
    return kinds, per_feature


def report(path: str) -> None:
    text = Path(path).read_text(encoding="utf-8")
    bl = blocks(text)
    total = sum(len(lines) for _, lines in bl)
    print(f"\n{path}: {total} instructions in {len(bl)} blocks")
    for header, body in loops(bl):
        lines = [l for i in body for l in bl[i][1]]
        kinds, per_feature = classify(lines)
        reads = kinds["sample"] + kinds["textureLoad"] + kinds["bufferLoad"] + kinds["rawBufferLoad"]
        print(f"  loop at {bl[header][0]} ({len(body)} blocks): {len(lines)} instructions, "
              f"{reads} reads ({kinds['sample']} samples, {kinds['textureLoad']} texture loads, "
              f"{kinds['bufferLoad'] + kinds['rawBufferLoad']} buffer loads), {kinds['float math']} float ops, "
              f"{kinds['exp'] + kinds['log'] + kinds['sqrt'] + kinds['trig']} exp/log/sqrt/trig")
        if per_feature:
            mod_total = sum(sum(c.values()) for c in per_feature.values())
            print(f"    the mod's (%mod.*): {mod_total} instructions")
            for feat, c in sorted(per_feature.items(), key=lambda kv: -sum(kv[1].values())):
                r = c["sample"] + c["textureLoad"] + c["bufferLoad"] + c["rawBufferLoad"]
                print(f"      {feat:8s} {sum(c.values()):5d}  reads {r:3d}  float {c['float math']:4d}  "
                      f"exp/log/sqrt {c['exp'] + c['log'] + c['sqrt']:3d}")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        report(p)
