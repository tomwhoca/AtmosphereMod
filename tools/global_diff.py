"""Diff two captures' Global cbuffer dumps (<id>_global.txt, ch_deferred_shadows).

    python tools/global_diff.py 23 24            # e.g. clear sky vs under an overcast

Lists the registers whose components differ, largest relative change first,
to find what the sim changes between two situations (camera, time and jitter
registers change too; take both captures from a similar view).
"""

import argparse
import re
from pathlib import Path

import numpy as np

CAPTURES = Path(__file__).resolve().parent.parent / "build" / "logs" / "captures"


def load(capture: str) -> np.ndarray:
    rows = {}
    for line in (CAPTURES / f"{capture}_global.txt").read_text().splitlines():
        if m := re.match(r"Global\[\s*(\d+)\]\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)", line):
            rows[int(m[1])] = [float(m[k]) for k in range(2, 6)]
    out = np.zeros((max(rows) + 1, 4))
    for r, v in rows.items():
        out[r] = v
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--min", type=float, default=0.02, help="smallest relative change listed (default 0.02)")
    args = ap.parse_args()
    a, b = load(args.a), load(args.b)
    n = min(len(a), len(b))
    found = []
    for r in range(n):
        for c in range(4):
            x, y = a[r, c], b[r, c]
            if x == y or not (np.isfinite(x) and np.isfinite(y)):
                continue
            rel = abs(y - x) / max(abs(x), abs(y), 1e-30)
            if rel >= args.min:
                found.append((rel, r, c, x, y))
    found.sort(reverse=True)
    print(f"{len(found)} components differ by >= {args.min:.0%} ({args.a} -> {args.b}):")
    for rel, r, c, x, y in found:
        print(f"  Global[{r:3d}].{'xyzw'[c]}  {x:15.7g} -> {y:15.7g}   ({rel:.0%})")


if __name__ == "__main__":
    main()
